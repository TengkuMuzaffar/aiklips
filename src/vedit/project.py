"""Project model: project.json is the single source of truth for an edit.

Original media is never modified. Every edit changes this file, and every change
is snapshotted by `vedit.history`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROJECT_FILE = "project.json"
STATE_DIR = ".vedit"
ACTIVE_FILE = ".vedit-active"
SCHEMA_VERSION = 1


class VeditError(Exception):
    """User-facing error with a clear message."""


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class Canvas(_Model):
    width: int = 1080
    height: int = 1920
    fps: float = 30
    background: str = "#000000"


class Source(_Model):
    path: str
    duration: float
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_audio: bool = True
    has_video: bool = True


class Effect(_Model):
    """A per-clip effect computed on the clip's source (cached in .vedit/cache)."""

    type: Literal["bg", "face_mesh", "face_sticker", "face_prop", "face_blur"]
    params: dict[str, Any] = Field(default_factory=dict)


class Clip(_Model):
    id: str
    source: str
    in_: float = Field(alias="in")
    out: float
    speed: float = 1.0
    volume: float = 1.0
    fit: Literal["cover", "contain", "blur"] = "cover"
    focus_x: float = 0.5
    focus_y: float = 0.5
    zoom: float = 1.0
    zoom_to: float | None = None  # animated zoom (Ken Burns) from `zoom` to `zoom_to`
    effects: list[Effect] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        return (self.out - self.in_) / self.speed


class Keyframe(_Model):
    t: float  # seconds, relative to the layer start
    x: float | str | None = None
    y: float | str | None = None
    scale: float | None = None
    rotation: float | None = None
    opacity: float | None = None
    reveal: float | None = None  # 0..1 fraction of text shown (typewriter)
    ease: str = "ease_in_out"  # easing used from this keyframe to the next


class Preset(_Model):
    name: str
    duration: float | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class TextStyle(_Model):
    font: str | None = None
    size: int = 80
    color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: int = 6
    bg_color: str | None = None
    bg_padding: int = 24
    bg_radius: int = 24
    shadow: bool = True
    max_width: int = 920
    align: Literal["left", "center", "right"] = "center"
    uppercase: bool = False
    line_spacing: float = 1.15
    highlight_color: str = "#FFE600"


class Word(_Model):
    text: str
    start: float
    end: float


class Layer(_Model):
    """An overlay drawn on top of the timeline: text, image or captions."""

    id: str
    type: Literal["text", "image", "captions"]
    start: float = 0.0  # timeline seconds
    end: float | None = None  # None = until the end of the timeline
    x: float | str = "50%"  # centre position, canvas px or "NN%"
    y: float | str = "50%"
    scale: float = 1.0
    rotation: float = 0.0
    opacity: float = 1.0
    keyframes: list[Keyframe] = Field(default_factory=list)
    presets: list[Preset] = Field(default_factory=list)
    text: str | None = None
    style: TextStyle = Field(default_factory=TextStyle)
    image: str | None = None
    width: int | None = None  # image width in canvas px
    words: list[Word] = Field(default_factory=list)
    words_per_group: int = 3


class MusicTrack(_Model):
    id: str
    source: str
    start: float = 0.0  # timeline seconds
    in_: float = Field(default=0.0, alias="in")
    volume: float = 0.25
    loop: bool = True
    duck: bool = False
    fade_in: float = 0.5
    fade_out: float = 1.0


class Project(_Model):
    version: int = SCHEMA_VERSION
    name: str = "untitled"
    canvas: Canvas = Field(default_factory=Canvas)
    sources: dict[str, Source] = Field(default_factory=dict)
    clips: list[Clip] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)
    music: list[MusicTrack] = Field(default_factory=list)

    # ---- timeline helpers -------------------------------------------------
    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)

    def clip_starts(self) -> list[float]:
        starts, t = [], 0.0
        for c in self.clips:
            starts.append(t)
            t += c.duration
        return starts

    def clip(self, clip_id: str) -> Clip:
        for c in self.clips:
            if c.id == clip_id:
                return c
        raise VeditError(f"No clip '{clip_id}'. Clips: {', '.join(c.id for c in self.clips) or 'none'}")

    def layer(self, layer_id: str) -> Layer:
        for l in self.layers:
            if l.id == layer_id:
                return l
        raise VeditError(f"No layer '{layer_id}'. Layers: {', '.join(l.id for l in self.layers) or 'none'}")

    def new_id(self, prefix: str) -> str:
        used = (
            [c.id for c in self.clips]
            + [l.id for l in self.layers]
            + [m.id for m in self.music]
            + list(self.sources)
        )
        n = 0
        for u in used:
            m = re.fullmatch(rf"{re.escape(prefix)}(\d+)", u)
            if m:
                n = max(n, int(m.group(1)))
        return f"{prefix}{n + 1}"

    def to_dict(self) -> dict:
        return self.model_dump(mode="json", by_alias=True)


# ---- locating and loading projects -----------------------------------------


def find_project_dir(explicit: str | None = None) -> Path:
    """Resolve the project: --project, $VEDIT_PROJECT, ./.vedit-active, then search upward."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    elif os.environ.get("VEDIT_PROJECT"):
        candidates.append(Path(os.environ["VEDIT_PROJECT"]))
    else:
        active = Path.cwd() / ACTIVE_FILE
        if active.exists():
            candidates.append(Path(active.read_text().strip()))
        cur = Path.cwd()
        for d in [cur, *cur.parents]:
            if (d / PROJECT_FILE).exists():
                candidates.append(d)
                break
    for c in candidates:
        c = c.expanduser().resolve()
        if (c / PROJECT_FILE).exists():
            return c
    hint = f" (looked at {candidates[0]})" if candidates else ""
    raise VeditError(
        f"No project found{hint}. Create one with `vedit init <folder>` "
        "or select one with `vedit use <folder>`."
    )


def load(pdir: Path) -> Project:
    try:
        data = json.loads((pdir / PROJECT_FILE).read_text())
    except json.JSONDecodeError as e:
        raise VeditError(f"project.json is not valid JSON: {e}") from e
    try:
        return Project.model_validate(data)
    except Exception as e:  # pydantic ValidationError
        raise VeditError(f"project.json is invalid: {e}") from e


def dump_json(project: Project) -> str:
    return json.dumps(project.to_dict(), indent=2) + "\n"


def save(pdir: Path, project: Project) -> None:
    validate(project)
    tmp = pdir / (PROJECT_FILE + ".tmp")
    tmp.write_text(dump_json(project))
    tmp.replace(pdir / PROJECT_FILE)


def validate(project: Project) -> None:
    for c in project.clips:
        if c.source not in project.sources:
            raise VeditError(f"Clip {c.id} references unknown source '{c.source}'")
        src = project.sources[c.source]
        if not (0 <= c.in_ < c.out <= src.duration + 1e-3):
            raise VeditError(
                f"Clip {c.id} range {c.in_:.3f}-{c.out:.3f}s is outside source {c.source} (0-{src.duration:.3f}s)"
            )
        if not (0.1 <= c.speed <= 16):
            raise VeditError(f"Clip {c.id} speed {c.speed} must be between 0.1 and 16")
    for m in project.music:
        if m.source not in project.sources:
            raise VeditError(f"Music {m.id} references unknown source '{m.source}'")


def state_dir(pdir: Path) -> Path:
    d = pdir / STATE_DIR
    d.mkdir(exist_ok=True)
    return d


def resolve_media(pdir: Path, path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (pdir / p).resolve()


def store_media_path(pdir: Path, path: Path) -> str:
    """Store paths relative to the project when the media lives inside it."""
    path = path.resolve()
    try:
        return path.relative_to(pdir.resolve()).as_posix()
    except ValueError:
        return str(path)


def parse_pos(value: float | str, total: float) -> float:
    """'50%' -> half of total, numbers are pixels."""
    if isinstance(value, str):
        v = value.strip()
        if v.endswith("%"):
            return float(v[:-1]) / 100.0 * total
        return float(v)
    return float(value)

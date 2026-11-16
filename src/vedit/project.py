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
    loudness: float | None = -14.0  # final mix normalised to this LUFS (social apps play at about -14); None = off


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

    # "fx" = a filter from ops/fx_filters.py (blur, vignette, grain...): params {"id", "key", "on", settings...}
    type: Literal["bg", "face_mesh", "face_sticker", "face_prop", "face_blur", "mask", "focus", "fx"]
    params: dict[str, Any] = Field(default_factory=dict)


TRANSITIONS = (
    "fade", "fadeblack", "fadewhite", "dissolve", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown", "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circleopen", "circleclose", "circlecrop", "rectcrop", "radial", "zoomin", "pixelize", "hblur", "distance",
    "squeezeh", "squeezev", "diagtl", "diagbr", "hlslice", "hrslice", "coverleft", "coverright", "revealleft", "revealright",
)
TRANSITION_ALIASES = {
    "crossfade": "fade", "cross": "fade", "mix": "fade", "dip": "fadeblack", "dip-black": "fadeblack", "black": "fadeblack",
    "dip-white": "fadewhite", "flash": "fadewhite", "white": "fadewhite", "zoom": "zoomin", "push": "slideleft",
    "slide": "slideleft", "wipe": "wipeleft", "whip": "hblur", "blur": "hblur", "circle": "circleopen", "iris": "circleopen",
    "pixel": "pixelize", "glitch": "pixelize", "clock": "radial", "squeeze": "squeezeh",
}


def transition_name(name: str) -> str:
    n = name.strip().lower().replace("_", "-")
    n = TRANSITION_ALIASES.get(n, n).replace("-", "")
    if n not in TRANSITIONS:
        raise VeditError(f"Unknown transition '{name}'. Use one of: {', '.join(TRANSITIONS)} "
                         f"(aliases: {', '.join(TRANSITION_ALIASES)})")
    return n


class Transition(_Model):
    """How this clip comes in from the previous one. The cut point stays where it is:
    the blend is centred on it, using source handles (or held frames) on both sides,
    so the timeline length and every overlay time are unchanged."""

    type: str = "fade"
    duration: float = 0.5


class Grade(_Model):
    """Colour grade of a clip: a correction (from `color match` or set by hand), then a named look.

    The correction numbers are what `vedit color match` measured to make this clip match its
    target; the look is a creative grade applied on top at `intensity` (0..1).
    """

    brightness: float = 0.0  # added to luma, -0.3..0.3
    contrast: float = 1.0  # 0.5..1.6, around mid grey
    saturation: float = 1.0  # 0 = black & white .. 2
    temperature: float = 0.0  # -1 cool .. +1 warm
    tint: float = 0.0  # -1 green .. +1 magenta
    look: str | None = None
    intensity: float = 1.0
    lut: str | None = None  # .cube file applied last (before the look's vignette)
    matched_to: str | None = None  # informational: "clips", "c2", "ref 3"


ROLES = ("hook", "problem", "setup", "demo", "proof", "benefit", "payoff", "cta", "other")


class ClipMeta(_Model):
    """Why a clip is in the edit. Written by the generator or `vedit why`; read by the auditor."""

    why: str | None = None
    role: str | None = None  # one of ROLES
    beat: str | None = None  # beat id in Project.notes.beats
    segment: str | None = None  # footage-log segment id it came from (generation)
    quality: int | None = None  # that segment's logged quality 1-5
    audio: str | None = None  # voice (keep, music ducks) | ambient (keep, lower) | mute (music carries it)


class Beat(_Model):
    id: str
    role: str = "other"
    purpose: str = ""


class EditNotes(_Model):
    """The edit's intent: what it is for and how it is structured."""

    rationale: str = ""
    brief: str = ""
    beats: list[Beat] = Field(default_factory=list)
    target_shot_s: float | None = None  # pacing the edit aims for (from the reference)
    style: str = ""  # one-line summary of the target style (Style DNA), for reviewers


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
    transition: Transition | None = None  # transition *into* this clip from the previous one
    grade: Grade | None = None  # colour correction + look (`vedit color ...`)
    meta: ClipMeta | None = None  # why this clip is here (`vedit why`)

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
    letter_spacing: float = 0.0  # extra px between letters (negative = tighter)
    glow_color: str | None = None  # soft coloured glow behind the letters (neon looks)
    glow_size: int = 18
    gradient: list[str] | None = None  # fill top -> bottom, e.g. ["#FFE600", "#FF6A00"]
    look: str | None = None  # name of the text look this style came from (informational)


class Word(_Model):
    text: str
    start: float
    end: float


BLEND_MODES = ("normal", "screen", "multiply", "overlay", "add", "lighten", "darken")
SHAPE_KINDS = ("rect", "ellipse", "line", "arrow")


class Layer(_Model):
    """An overlay drawn on top of the timeline: text, image, captions, video (picture-in-picture, b-roll, light leaks)
    or a shape. The list order of project.layers is the stacking order (later = on top)."""

    id: str
    type: Literal["text", "image", "captions", "video", "shape"]
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
    why: str | None = None  # why this overlay is here (generation / `vedit why`)
    # how it mixes with what is under it (screen for light leaks / dust, multiply for paper / shadows ...)
    blend: Literal["normal", "screen", "multiply", "overlay", "add", "lighten", "darken"] = "normal"
    # video layers: a project source played from src_in for the layer's duration
    source: str | None = None
    src_in: float = 0.0
    loop: bool = False  # repeat the clip when the layer is longer than it
    volume: float = 0.0  # overlay sound (0 = muted, the usual for b-roll / textures)
    # frame of image / video / shape layers (canvas px): rounded corners, an inside border, circle cut-out, green screen
    radius: int = 0
    border_width: int = 0
    border_color: str = "#FFFFFF"
    mask: Literal["rect", "circle"] = "rect"
    chroma: str | None = None  # key colour made transparent, e.g. "#00FF00"
    chroma_similarity: float = 0.25
    # shape layers
    shape: Literal["rect", "ellipse", "line", "arrow"] = "rect"
    height: int | None = None  # shape height in canvas px (line / arrow: thickness)
    fill: str | None = "#FFFFFF"  # shape fill (None = outline only, with border_width)


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
    notes: EditNotes | None = None

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
    for layer in project.layers:
        if layer.type == "video":
            if not layer.source or layer.source not in project.sources:
                raise VeditError(f"Video layer {layer.id} references unknown source '{layer.source}'")
            src = project.sources[layer.source]
            if not src.has_video:
                raise VeditError(f"Video layer {layer.id}: source {layer.source} has no picture")
            if not (0 <= layer.src_in < src.duration):
                raise VeditError(f"Video layer {layer.id} starts at {layer.src_in:.2f}s, outside its source (0-{src.duration:.2f}s)")


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

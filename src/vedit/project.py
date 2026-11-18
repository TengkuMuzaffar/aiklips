"""Project model: project.json is the single source of truth for an edit.

Original media is never modified. Every edit changes this file, and every change
is snapshotted by `vedit.history`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

PROJECT_FILE = "project.json"
STATE_DIR = ".vedit"
ACTIVE_FILE = ".vedit-active"
SCHEMA_VERSION = 2


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
    fade_in: float = 0.0  # sound fade in / out, seconds (the picture is untouched: use a transition for that)
    fade_out: float = 0.0
    voice: Literal["off", "clean", "studio"] = "off"  # speech clean-up: rumble, noise, level (render/graph.py VOICE_CHAINS)
    # empty space on the main track (a clip dragged later leaves one): plays a generated background-colour file with no
    # sound (ops/cut.py `gap_source`), so cuts, render and export treat it like any clip. Audits and sound skip it.
    gap: bool = False

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
    reveal: float | None = None  # 0..1 fraction shown (text: typewriter; other layers: wipe from the left)
    w: float | None = None  # shape / image / video width in canvas px (stretch; shapes are redrawn crisp)
    h: float | None = None  # shape height in canvas px
    sx: float | None = None  # non-uniform stretch (multiplies the drawn width / height, any layer)
    sy: float | None = None
    fill: str | None = None  # shape fill colour, blended in RGB between keyframes
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
    bold: bool = False  # heavier letters (a fill-colour stroke on top of the font's own weight)
    italic: bool = False  # slanted (sheared) letters
    underline: bool = False
    strike: bool = False  # strikethrough
    list_style: Literal["none", "bullet", "number"] = "none"  # each line (paragraph) as a list item


class Word(_Model):
    text: str
    start: float
    end: float


BLEND_MODES = ("normal", "screen", "multiply", "overlay", "add", "lighten", "darken")
SHAPE_KINDS = ("rect", "ellipse", "triangle", "diamond", "polygon", "star", "ring", "heart", "bubble", "blob", "line",
               "arrow")
SCENE_TRANSITIONS = ("cut", "fade", "slide", "zoom", "pop", "wipe")


class Layer(_Model):
    """An overlay drawn on top of the timeline: text, image, captions, video (picture-in-picture, b-roll, light leaks)
    a shape, or an adjustment layer (filters + colour over a time range). The list order of project.layers is the stacking
    order (later = on top), kept sorted by `track` (see `normalize_tracks`)."""

    id: str
    type: Literal["text", "image", "captions", "video", "shape", "adjust"]
    # timeline row, like CapCut: 0 = the bottom overlay track, higher = drawn on top. Several layers share a track when
    # their times don't overlap. None = not placed yet (new layer): `normalize_tracks` puts it on top.
    track: int | None = None
    start: float = 0.0  # timeline seconds
    end: float | None = None  # None = until the end of the timeline
    x: float | str = "50%"  # centre position, canvas px or "NN%"
    y: float | str = "50%"
    scale: float = 1.0
    rotation: float = 0.0
    opacity: float = 1.0
    sx: float = 1.0  # non-uniform stretch of the drawn layer (1 = as drawn); shapes stretch with width / height instead
    sy: float = 1.0
    scene: str | None = None  # design projects: the scene (Project.scenes) this layer belongs to; None = whole video
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
    # framing, like a main-track clip: free = `width` wide (picture-in-picture), cover = fill the canvas (crop),
    # contain = the whole picture as big as fits. flip_h / flip_v mirror it (orientation; rotation is `rotation`)
    fit: Literal["free", "cover", "contain"] = "free"
    hidden: bool = False  # track hidden on the timeline (eye): not drawn in the preview or the export; sound still plays
    flip_h: bool = False
    flip_v: bool = False
    volume: float = 0.0  # overlay sound (0 = muted, the usual for b-roll / textures)
    # frame of image / video / shape layers (canvas px): rounded corners, an inside border, circle cut-out, green screen
    radius: int = 0
    border_width: int = 0
    border_color: str = "#FFFFFF"
    mask: Literal["rect", "circle"] = "rect"
    chroma: str | None = None  # key colour made transparent, e.g. "#00FF00"
    chroma_similarity: float = 0.25
    # shape layers
    shape: Literal["rect", "ellipse", "triangle", "diamond", "polygon", "star", "ring", "heart", "bubble", "blob", "line",
                   "arrow"] = "rect"
    height: int | None = None  # shape height in canvas px (line / arrow: thickness)
    fill: str | None = "#FFFFFF"  # shape fill (None = outline only, with border_width)
    fill_gradient: list[str] | None = None  # shape fill as a linear gradient (2+ colours), replaces `fill`
    gradient_angle: float = 90.0  # degrees: 0 = left -> right, 90 = top -> bottom
    sides: int = 6  # polygon
    points: int = 5  # star
    inner: float = 0.5  # star: inner radius / outer; ring: hole size / outer
    # adjustment layers: filters and a colour grade applied to everything under them (clips and the layers below)
    effects: list[Effect] = Field(default_factory=list)
    grade: Grade | None = None


class MusicTrack(_Model):
    id: str
    source: str
    start: float = 0.0  # timeline seconds
    in_: float = Field(default=0.0, alias="in")
    end: float | None = None  # timeline seconds where it stops; None = the end of the timeline (or of the file)
    volume: float = 0.25
    loop: bool = True
    duck: bool = False
    fade_in: float = 0.5
    fade_out: float = 1.0


class Marker(_Model):
    """A named moment on the timeline (notes, beats to hit, YouTube chapters)."""

    id: str
    t: float  # timeline seconds
    label: str = ""
    color: str = "#FBBF24"


class Scene(_Model):
    """A design project's scene ("page"): a stretch of the timeline. Scenes play back to back in list order; a scene's
    start is the sum of the durations before it (`Project.scene_starts`). Layers point at their scene with
    `Layer.scene`, and scene ops (`ops/scenes.py`) move those layers' absolute times along when scenes change."""

    id: str
    name: str = ""
    dur: float = 5.0
    background: str | None = None  # hex; drawn as the scene's bottom full-canvas layer (`bg_layer`)
    bg_layer: str | None = None
    transition: Literal["cut", "fade", "slide", "zoom", "pop", "wipe"] = "cut"  # how this scene's layers enter
    notes: str = ""  # storyboard text: what happens / is said in this scene
    why: str | None = None


class Project(_Model):
    version: int = SCHEMA_VERSION
    kind: Literal["edit", "design"] = "edit"  # design = made in the Design studio: scenes of animated elements
    name: str = "untitled"
    canvas: Canvas = Field(default_factory=Canvas)
    sources: dict[str, Source] = Field(default_factory=dict)
    clips: list[Clip] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)
    music: list[MusicTrack] = Field(default_factory=list)
    notes: EditNotes | None = None
    markers: list[Marker] = Field(default_factory=list)
    main_hidden: bool = False  # main video track hidden (eye): its picture becomes the background; sound still plays
    scenes: list[Scene] = Field(default_factory=list)  # design projects only

    def scene_starts(self) -> list[float]:
        starts, t = [], 0.0
        for sc in self.scenes:
            starts.append(round(t, 4))
            t += sc.dur
        return starts

    def scene(self, scene_id: str) -> Scene:
        for sc in self.scenes:
            if sc.id == scene_id:
                return sc
        raise VeditError(f"No scene '{scene_id}'. Scenes: {', '.join(s.id for s in self.scenes) or 'none'}")

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
            + [k.id for k in self.markers]
            + list(self.sources)
            + [s.id for s in self.scenes]
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


# project.json format migrations: MIGRATIONS[n] turns a version-n dict into version n+1. When the format changes,
# bump SCHEMA_VERSION and add a step here, so projects saved by older versions keep opening.
MIGRATIONS: dict[int, Callable[[dict], dict]] = {
    1: lambda d: d,  # 2: design projects (kind, scenes), stretch (sx / sy), more shapes, keyframed size / fill
}


def migrate(data: dict) -> dict:
    """Bring a project dict up to SCHEMA_VERSION. A project saved by a *newer* aiklips is refused rather than
    loaded and re-saved without the fields this version doesn't know (that would silently lose work after a
    rollback to an older version)."""
    v = int(data.get("version", 1))
    if v > SCHEMA_VERSION:
        raise VeditError(f"This project was saved by a newer version of aiklips (format {v}; this version reads up to "
                         f"{SCHEMA_VERSION}). Update aiklips to open it.")
    while v < SCHEMA_VERSION:
        data = MIGRATIONS[v](data)
        v += 1
        data["version"] = v
    return data


def load(pdir: Path) -> Project:
    try:
        data = json.loads((pdir / PROJECT_FILE).read_text())
    except json.JSONDecodeError as e:
        raise VeditError(f"project.json is not valid JSON: {e}") from e
    data = migrate(data)
    try:
        p = Project.model_validate(data)
    except Exception as e:  # pydantic ValidationError
        raise VeditError(f"project.json is invalid: {e}") from e
    normalize_tracks(p)  # projects from before tracks existed
    return p


def dump_json(project: Project) -> str:
    return json.dumps(project.to_dict(), indent=2) + "\n"


def _span(layer: Layer) -> tuple[float, float]:
    return layer.start, float("inf") if layer.end is None else layer.end


def spans_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] - 1e-3 and b[0] < a[1] - 1e-3


def normalize_tracks(project: Project) -> None:
    """Keep overlay tracks consistent (CapCut-style rows), in place:

    - a new layer (track None) goes on the top track if it is free at that time, else on a new track above it, so the
      layer added last is drawn on top, as before tracks existed;
    - two layers on one track never overlap in time: the later one in the list moves to a new track just above;
    - tracks are numbered 0..n-1 with no gaps, and project.layers is sorted by track (stable), so the list order the
      renderers use (later = on top) always matches the timeline rows.
    """
    layers = project.layers
    top = max((l.track for l in layers if l.track is not None), default=-1)
    for l in layers:
        if l.track is None:
            if top >= 0 and not any(o.track == top and spans_overlap(_span(l), _span(o)) for o in layers if o is not l):
                l.track = top
            else:
                top += 1
                l.track = top
    # resolve overlaps on a track: bump the later layer to a new track right above (fractional, renumbered below)
    rows: dict[float, list[Layer]] = {}
    for l in layers:
        rows.setdefault(l.track, []).append(l)
    pos: dict[str, float] = {}
    for t, row in rows.items():
        placed: list[list[Layer]] = []
        for l in row:
            for k, sub in enumerate(placed):
                if not any(spans_overlap(_span(l), _span(o)) for o in sub):
                    sub.append(l)
                    break
            else:
                placed.append([l])
        for k, sub in enumerate(placed):
            for l in sub:
                pos[l.id] = t + k / (len(placed) + 1)
    order = sorted(set(pos.values()))
    rank = {v: i for i, v in enumerate(order)}
    for l in layers:
        l.track = rank[pos[l.id]]
    layers.sort(key=lambda l: l.track)


def normalize_gaps(project: Project) -> None:
    """Main-track gaps: merge neighbours, drop empty ones and any at the very end (nothing comes after them)."""
    out: list[Clip] = []
    for c in project.clips:
        if c.gap and c.duration < 0.01:
            continue
        if c.gap and out and out[-1].gap and out[-1].source == c.source:
            prev = out[-1]
            prev.out = min(project.sources[c.source].duration, prev.out + (c.out - c.in_))
            continue
        out.append(c)
    while out and out[-1].gap:
        out.pop()
    project.clips = out


def save(pdir: Path, project: Project) -> None:
    if project.kind == "design" and project.scenes:
        from .ops.scenes import reconcile

        reconcile(project)
    normalize_tracks(project)
    normalize_gaps(project)
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

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
SCHEMA_VERSION = 6  # 4 also has Clip.zoom_ease (additive, default linear); 5: 3D object layers (obj3d, yaw / pitch / roll)


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
    refs: list[int] = Field(default_factory=list)  # reference videos this edit should look like: the audit compares them


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
    zoom_ease: str = "linear"  # how zoom -> zoom_to moves: linear | ease_out | ease_in | ease_in_out | snap (ZOOM_EASES)
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


# easing of a clip's animated zoom: (python fn of p 0..1, the same as an FFmpeg expression of P)
ZOOM_EASES: dict[str, tuple[Callable[[float], float], str]] = {
    "linear": (lambda p: p, "P"),
    "ease_out": (lambda p: 1 - (1 - p) ** 3, "(1-pow(1-P,3))"),
    "ease_in": (lambda p: p**3, "pow(P,3)"),
    "ease_in_out": (lambda p: 4 * p**3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2,
                    "if(lt(P,0.5),4*pow(P,3),1-pow(-2*P+2,3)/2)"),
    "snap": (lambda p: 1 - (1 - p) ** 5, "(1-pow(1-P,5))"),  # gets there almost at once, then settles
}


def zoom_at(clip: "Clip", u: float) -> float:
    """A clip's zoom at fraction u (0..1) through it."""
    z = max(1.0, clip.zoom)
    if clip.zoom_to:
        fn = ZOOM_EASES.get(clip.zoom_ease, ZOOM_EASES["linear"])[0]
        z = z + (max(1.0, clip.zoom_to) - z) * fn(min(max(u, 0.0), 1.0))
    return z


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
    # build-up progress, 0..1 (ops/motion.py): draw = how much of a shape's outline / a chart is drawn (1 = all),
    # morph = how far a shape has turned into its `morph_to` kind, count = how far a counter has counted (1 = `to`)
    draw: float | None = None
    morph: float | None = None
    count: float | None = None
    # 3D objects (obj3d layers): turn in degrees around the object's own up (yaw), side (pitch) and front (roll) axes
    yaw: float | None = None
    pitch: float | None = None
    roll: float | None = None
    # easing used from this keyframe to the next: a name (ease_out, snap, expo_out ...) or "cubic-bezier(a,b,c,d)"
    ease: str = "ease_in_out"


class Counter(_Model):
    """A text layer that counts: its text is `from` -> `to` as the `count` progress goes 0 -> 1 (`count_up` preset)."""
    from_: float = Field(default=0.0, alias="from")
    to: float = 100.0
    decimals: int = 0
    prefix: str = ""
    suffix: str = ""
    separator: str = ","  # thousands separator ("" = none)


class ChartItem(_Model):
    label: str = ""
    value: float = 0.0
    color: str | None = None  # None = the chart's palette


class Chart(_Model):
    """A chart layer (ops/chart.py), grown by the `draw` progress (`draw_in` preset): bars rise one after another,
    a line draws itself, a donut sweeps round, and the values roll up with them."""
    kind: Literal["bar", "hbar", "line", "donut"] = "bar"
    items: list[ChartItem] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=lambda: ["#22D3EE", "#A78BFA", "#F472B6", "#FBBF24", "#34D399", "#60A5FA"])
    max: float | None = None  # top of the value axis (None = the largest value)
    show_values: bool = True
    show_labels: bool = True
    prefix: str = ""
    suffix: str = ""
    decimals: int = 0
    font: str | None = None
    text_color: str = "#FFFFFF"
    text_size: int = 40
    radius: int = 12  # bar corners
    gap: float = 0.35  # space between bars, as a fraction of each bar's slot
    line_width: int = 10  # line chart stroke / donut ring thickness (0 = a pie)
    stagger: float = 0.5  # how much each item waits for the previous one (0 = all together, 1 = one after another)


class IsoBlock(_Model):
    """A box in an isometric scene, in grid units: x / y on the floor, z up."""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0  # along x
    d: float = 1.0  # along y
    h: float = 1.0  # up
    color: str = "#7C9CFF"


class IsoItem(_Model):
    """A placed object from the iso item library (ops/iso_items.py: trees, buildings, cars, desks, people, signs ...),
    in grid units. `color` / `color2` override its theme colours; `params` are its own settings (floors, text ...)."""
    kind: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rot: int = 0  # quarter turns (0-3)
    size: float = 1.0  # scale
    color: str | None = None
    color2: str | None = None
    label: str | None = None  # floating label above it
    params: dict[str, Any] = Field(default_factory=dict)


class IsoScene(_Model):
    """An isometric miniature world (ops/iso.py): its items (and raw blocks) build up one after another as `draw`
    goes 0 -> 1."""
    items: list[IsoItem] = Field(default_factory=list)
    blocks: list[IsoBlock] = Field(default_factory=list)
    theme: str = "pastel"  # colour palette for every item (ops/iso_items.py THEMES)
    light: float = 0.25  # how much brighter the tops / darker the sides are (0..0.6)
    outline: str | None = "#00000030"  # thin edge lines (None = none)
    build: Literal["drop", "grow", "pop"] = "drop"  # how each item arrives


OBJ3D_KINDS = ("text", "car", "truck", "bus", "plane", "rocket", "dog", "cat", "bird", "fish", "cube", "sphere", "gem", "coin",
               "heart", "star", "model")


class Obj3D(_Model):
    """A 3D object (ops/render3d.py): generated from a recipe (ops/items3d.py), never stored as a file, except
    `kind="model"` (a .glb / .obj / .stl the user brings). Drawn low-poly, flat-shaded, in perspective. Its turn is
    yaw / pitch / roll here plus the layer's keyframes / presets (spin3d, travel). Front = +x (cars drive, animals walk
    that way at yaw 0), up = +y."""
    kind: str = "cube"
    text: str | None = None  # kind text
    font: str | None = None  # kind text (a font key from `vedit fonts`)
    src: str | None = None  # kind model: a .glb / .gltf / .obj / .stl path
    color: str | None = None  # main colour (None = the recipe's own)
    color2: str | None = None  # second colour (windows, belly, sides of 3D text ...)
    depth: float = 0.35  # text: how deep the letters are, as a fraction of their height
    params: dict[str, Any] = Field(default_factory=dict)  # the recipe's own settings (`vedit 3d kinds`)
    yaw: float = -25.0  # resting turn in degrees (keyframes / presets add to it)
    pitch: float = 18.0
    roll: float = 0.0
    fov: float = 30.0  # camera lens: small = flat / telephoto, large = strong perspective
    light: float = 0.45  # how much the light shapes it (0 = flat colour, 1 = deep shadows)
    outline: str | None = "#00000038"  # thin edge lines (None = none)
    gait: bool = True  # animals / vehicles move their legs / wheels / wings / propellers over time


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
               "arrow", "path")
SCENE_TRANSITIONS = ("cut", "fade", "slide", "zoom", "pop", "wipe")


class Layer(_Model):
    """An overlay drawn on top of the timeline: text (captions are text phrases in a captions group), image, video (picture-in-picture, b-roll, light leaks)
    a shape, or an adjustment layer (filters + colour over a time range). The list order of project.layers is the stacking
    order (later = on top), kept sorted by `track` (see `normalize_tracks`)."""

    id: str
    type: Literal["text", "image", "video", "shape", "adjust", "chart", "glass", "iso", "obj3d"]
    # timeline row, like CapCut: 0 = the bottom overlay track, higher = drawn on top. Several layers share a track when
    # their times don't overlap. None = not placed yet (new layer): `normalize_tracks` puts it on top.
    track: int | None = None
    # on the video track itself (equal tracks): in a gap or past the clips, never over a clip; drawn at the video
    # track's height. `track` then just mirrors main_z (normalize_tracks)
    on_video: bool = False
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
    # a caption phrase (text layer): each word's time, in seconds from the layer's start (so moving the layer keeps
    # them in sync); with `highlight_words` the word being spoken is drawn in style.highlight_color (ops/captions.py)
    words: list[Word] = Field(default_factory=list)
    highlight_words: bool = False
    # kinetic text (ops/kinetic.py): animate the text by word / letter / line, each piece `split_anim`-ing in
    # `stagger` seconds after the previous one, over `split_dur` seconds with `split_ease`
    split: Literal["none", "word", "letter", "line"] = "none"
    split_anim: Literal["rise", "drop", "fade", "pop", "mask_up", "slide", "type"] = "rise"
    stagger: float = 0.06
    split_dur: float = 0.45
    split_ease: str = "ease_out"
    counter: Counter | None = None  # a counting number (text layers)
    group: str | None = None  # the LayerGroup it belongs to (Project.groups)
    step_fps: int | None = None  # motion in steps ("on twos": 12) like stop motion / paper cut-out; None = smooth
    chart: Chart | None = None  # chart layers
    glass_blur: float = 22.0  # glass layers: how frosted the picture behind is (px; `fill` = its tint, `radius` = corners)
    iso: IsoScene | None = None  # iso layers: blocks of a little isometric world (ops/iso.py)
    obj3d: Obj3D | None = None  # obj3d layers: a 3D object (ops/render3d.py); `width` = the size of its turning box
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
                   "arrow", "path"] = "rect"
    # path shapes: points in the box as 0..1 fractions [[x, y], ...]; open paths are strokes (lines, brackets,
    # underlines, a drawing), closed ones are filled outlines
    path: list[list[float]] | None = None
    closed: bool = False
    morph_to: str | None = None  # shape kind this shape turns into as `morph` goes 0 -> 1 (keyframes)
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


class LayerGroup(_Model):
    """Layers that belong together (a signature effect's parts). The timeline shows them as one row with one block
    while `collapsed`; moving the block moves them all (ops/groups.py)."""
    id: str
    name: str = "Group"
    collapsed: bool = True
    kind: Literal["captions"] | None = None  # "captions": the phrases of one set of subtitles (ops/captions.py)
    per_group: int = 3  # captions: words per phrase


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
    # where the clip track (the video track: `clips`, played back to back) sits among the layer tracks: the number of
    # layer tracks drawn UNDER it. 0 = the video is the bottom track (the old fixed "main track"); layers on tracks
    # < main_z show through wherever the video is transparent (gaps, letterbox bars, keyed green screen). All tracks
    # are equal otherwise: the timeline is as long as whatever ends last, and something must start at 0s
    main_z: int = 0
    # the social app this video is for (tiktok | reels | shorts | all): its safe zone is where text, stickers and
    # buttons go (ops/safezone.py; the editor's guides, `vedit safe`, the audit). None = "all apps" on a portrait canvas
    platform: str | None = None
    # layer groups (one per effect: a chat, a checklist ...): shown as one collapsible row on the timeline
    groups: list[LayerGroup] = Field(default_factory=list)
    # motion style (design/styles.py): a look as numbers (easing, timing, palette, fonts) the AI follows and the
    # audit checks; None = no style rules
    motion_style: str | None = None
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
    def clips_end(self) -> float:
        """Where the video track's clips end (its gaps included)."""
        return sum(c.duration for c in self.clips)

    @property
    def duration(self) -> float:
        """The timeline's length: whatever ends last, clips or a layer with an end time (open-ended layers, end=None,
        run to this length rather than setting it)."""
        ends = [l.end for l in self.layers if l.end is not None and not l.hidden]
        return max([self.clips_end, *ends]) if ends else self.clips_end

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
    2: lambda d: d,  # 3: equal tracks: `main_z` (video track position; default 0 = bottom), timeline = longest track
    3: lambda d: d,  # 4: motion: draw-on / morph / counters / kinetic text / charts (`draw`, `morph`, `count` keyframes)
    4: lambda d: d,  # 5: 3D objects (`Layer.obj3d`, type "obj3d") and `yaw` / `pitch` / `roll` keyframes
    5: lambda d: _captions_to_text(d),  # 6: captions are text phrases in a captions group (no "captions" layer type)
}


def _captions_to_text(d: dict) -> dict:
    from .ops.captions import migrate_v5

    return migrate_v5(d)


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
    gids = {g.id for g in project.groups}
    for l in project.layers:
        if l.group and l.group not in gids:
            l.group = None
    used = {l.group for l in project.layers if l.group}
    project.groups = [g for g in project.groups if g.id in used]  # a group whose layers are all gone goes too
    vids = [l for l in project.layers if l.on_video]  # on the video track: not in the rows
    layers = [l for l in project.layers if not l.on_video]
    top = max((l.track for l in layers if l.track is not None), default=-1)
    for l in layers:
        if l.track is None:
            # (drawn on top of everything: never onto a track under the video track)
            # a grouped layer only shares a track with its own group (so the group's tracks can collapse into one row)
            if top >= max(0, project.main_z) and not any(o.track == top and (spans_overlap(_span(l), _span(o)) or o.group != l.group)
                                                         for o in layers if o is not l):
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
    # the video track keeps its place: as many tracks under it as there were before renumbering
    project.main_z = sum(1 for v in order if v < project.main_z - 1e-9) if order else 0
    for l in layers:
        l.track = rank[pos[l.id]]
    for l in vids:
        l.track = project.main_z
    # list order = drawing order: tracks under the video, the video track's own layers, then the tracks over it
    project.layers.sort(key=lambda l: l.track - 0.5 if l.on_video else l.track)


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

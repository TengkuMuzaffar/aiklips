"""Data shapes for Design: the wizard's request and the AI's MotionPlan (flat on purpose: LLMs fill flat objects
more reliably than unions)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..library import FORMATS

MAX_LENGTH = 60.0
MIN_LENGTH = 5.0

STYLES: dict[str, str] = {
    "clean": "Clean and modern: lots of space, one accent colour, calm fades and slides, sans-serif titles.",
    "bold-kinetic": "Bold kinetic type: big uppercase words that punch and pop on the beat, fast scenes, high contrast.",
    "playful": "Playful: bright palette, bouncy drop-ins and wiggles, rounded cards, stickers and shapes.",
    "corporate": "Corporate explainer: tidy cards, lower-third boxes, steady slides, readable text, brand colours.",
    "retro": "Retro: warm film look, grain or dust texture, serif or condensed titles, slow zooms.",
    "neon": "Neon night: dark background, glowing text, cool light leaks, pulses.",
}

FRAMES = ("full", "card", "circle", "pip", "half")
TRANSITIONS = ("cut", "fade", "slide", "zoom", "pop")


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class DesignSpec(_M):
    clips: list[str] = Field(min_length=1, description="Upload ids of the user's videos / images")
    sheet_text: str = Field("", max_length=4000, description="Script or scene list")
    sheet_images: list[str] = Field(default_factory=list, max_length=6, description="Upload ids of image sheets / logos")
    split_sheets: bool = Field(True, description="Cut image sheets into their panels")
    cutout: bool = Field(False, description="Remove the background of sheet panels (needs rembg)")
    sound: list[str] = Field(default_factory=list, max_length=3)
    refs: list[int] = Field(default_factory=list, description="Optional References whose style to follow")
    format: Literal[tuple(FORMATS)] = "reels"  # type: ignore[valid-type]
    length_s: float = Field(30, ge=MIN_LENGTH, le=MAX_LENGTH)
    style: Literal[tuple(STYLES)] = "clean"  # type: ignore[valid-type]
    count: int = Field(1, ge=1, le=3)
    quality: Literal["best", "balanced", "economy"] = "balanced"
    review: bool = True
    models: dict[str, str] = Field(default_factory=dict)


class KF(_M):
    t: float = Field(description="Seconds from the element's start")
    x: str | None = Field(None, description="'NN%' of canvas width")
    y: str | None = Field(None, description="'NN%' of canvas height")
    scale: float | None = None
    rotation: float | None = None
    opacity: float | None = None
    w: float | None = Field(None, description="shapes: width px (stays crisp)")
    h: float | None = Field(None, description="shapes: height px")
    sx: float | None = Field(None, description="stretch width (1 = normal)")
    sy: float | None = Field(None, description="stretch height")
    fill: str | None = Field(None, description="shapes: fill hex colour")
    ease: str = "ease_in_out"


class Element(_M):
    kind: Literal["clip", "image", "text", "shape"]
    file: str | None = Field(None, description="clip / image: file id (f1.., p1.. for sheet pieces)")
    src_in: float | None = Field(None, description="clip: where to start inside the file, seconds")
    at: float = Field(0.0, description="Seconds after the scene start")
    dur: float | None = Field(None, description="Seconds on screen (empty = until the scene ends)")
    text: str | None = None
    look: str | None = Field(None, description="text: a text look name")
    size: int | None = None
    color: str | None = None
    shape: Literal["rect", "ellipse", "triangle", "diamond", "polygon", "star", "ring", "heart", "bubble", "blob", "line",
                   "arrow"] | None = None
    fill: str | None = None
    gradient: list[str] | None = Field(None, description="shapes: 2-4 hex colours, a top-to-bottom gradient fill")
    sides: int | None = Field(None, description="polygon: number of sides")
    points: int | None = Field(None, description="star: number of points")
    inner: float | None = Field(None, description="star: inner radius 0..1; ring: hole size 0..1")
    width: int | None = Field(None, description="px; shapes / images / clips with frame pip|card")
    height: int | None = None
    frame: Literal[FRAMES] | None = Field(None, description="clip / image framing")  # type: ignore[valid-type]
    radius: int | None = None
    border: str | None = Field(None, description="Border colour (adds a 8px border)")
    x: str | None = None
    y: str | None = None
    scale: float | None = None
    rotation: float | None = None
    opacity: float | None = None
    sx: float | None = Field(None, description="stretch width (1 = normal)")
    sy: float | None = Field(None, description="stretch height")
    enter: str | None = Field(None, description="Entrance preset")
    exit: str | None = Field(None, description="Exit preset")
    loop: str | None = Field(None, description="Loop preset while on screen")
    path: list[KF] = Field(default_factory=list, description="Optional keyframes (custom motion)")
    volume: float | None = Field(None, description="clip: its own sound 0..1 (default muted)")
    why: str | None = None


class Scene(_M):
    id: str = Field(description="S1, S2, ...")
    dur: float = Field(description="Seconds")
    purpose: str = ""
    background: str | None = Field(None, description="Hex colour behind this scene (empty = the global one)")
    transition: Literal[TRANSITIONS] = "fade"  # type: ignore[valid-type]
    elements: list[Element] = Field(default_factory=list, description="Bottom to top")


class Music(_M):
    file: str
    offset: float = 0.0
    volume: float = 0.6


class MotionPlan(_M):
    title: str = ""
    background: str = Field("#101014", description="Global background hex colour")
    texture: str | None = Field(None, description="Optional overlay library key over everything (grain, dust, bokeh...)")
    palette: list[str] = Field(default_factory=list, description="3-4 hex colours used throughout")
    font: str | None = None
    scenes: list[Scene] = Field(min_length=1)
    music: Music | None = None
    rationale: str = ""

    @property
    def length(self) -> float:
        return round(sum(max(0.0, s.dur) for s in self.scenes), 3)


class PatchOp(_M):
    """One targeted change. Elements are addressed as S2.E3 (scene 2, element 3, 1-based)."""
    op: Literal["set", "remove", "add", "scene_dur"]
    target: str = Field(description="S2.E3 for set / remove, S2 for add / scene_dur")
    element: Element | None = Field(None, description="add: the new element; set: only the fields to change")
    dur: float | None = None


class MotionPatch(_M):
    fixes: list[str] = Field(default_factory=list)
    ops: list[PatchOp] = Field(default_factory=list, max_length=8)
    note: str = ""


class Piece(_M):
    id: str
    label: str = Field(description="What this panel shows, 2-6 words")


class PieceLabels(_M):
    pieces: list[Piece]

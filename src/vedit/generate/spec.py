"""Data shapes for generation: the wizard's request, per-variation specs, and edit plans."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..library import FORMATS

AXES: dict[str, list[str]] = {
    "hook": ["reference", "question", "bold_claim", "stat", "pain_point", "pov", "before_after"],
    "order": ["best_first", "story", "reverse", "mixed"],
    "pacing": ["reference", "faster", "slower"],
    "caption_style": ["reference", "bold", "boxed", "neon", "karaoke", "minimal"],
    "text_animation": ["reference", "pop_in", "slide_in", "typewriter", "drop_in", "zoom_in", "punch"],
    "transitions": ["reference", "none", "accent"],
    "music_section": ["start", "middle", "late"],
}
AXIS_LABELS = {
    "hook": "Hook", "order": "Clip order", "pacing": "Pacing", "caption_style": "Caption style",
    "text_animation": "Text animation", "transitions": "Transitions", "music_section": "Music section",
}


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Brief(_M):
    goal: Literal["promo", "tutorial", "story", "ugc", "announcement", "other"] = "promo"
    product: str = Field("", max_length=300, description="What the video is about")
    hook: str = Field("", max_length=200, description="Hook line, or empty to let AI write it")
    cta: str = Field("", max_length=120)
    length_s: float = Field(20, ge=5, le=90)
    format: Literal[tuple(FORMATS)] = "reels"  # type: ignore[valid-type]
    language: str = Field("en", max_length=20)
    notes: str = Field("", max_length=1000)


class BatchSpec(_M):
    refs: list[int] = Field(min_length=1)
    lead: int | None = None
    footage: list[str] = Field(min_length=1, description="Upload ids")
    brief: Brief = Field(default_factory=Brief)
    count: int = Field(3, ge=1, le=10)
    axes: list[str] = Field(default_factory=lambda: list(AXES))
    quality: Literal["best", "balanced", "economy"] = "balanced"
    review: bool = Field(True, description="Run the AI review pass on each variation")


class Creative(_M):
    """The planner's creative content for one variation."""
    index: int = Field(description="Variation number, 1-based")
    angle: str = Field(description="One line: the idea of this variation")
    hook_text: str = Field(description="On-screen hook line (short)")
    cta_text: str = ""
    structure: list[str] = Field(default_factory=list, description="Sections in order")
    notes: str = ""


class CreativePlan(_M):
    variations: list[Creative]


class Op(_M):
    """One edit operation. Flat on purpose (LLMs fill flat objects more reliably than unions)."""
    op: Literal["clip", "text", "image", "transition", "jumpcut_zoom", "music", "captions", "fit"]
    file: str | None = None
    start: float | None = None
    end: float | None = None
    speed: float | None = None
    zoom: float | None = None
    zoom_to: float | None = None
    text: str | None = None
    x: str | None = None
    y: str | None = None
    size: int | None = None
    color: str | None = None
    uppercase: bool | None = None
    bg: str | None = None
    width: int | None = None
    presets: list[str] = Field(default_factory=list)
    into: int | None = None
    type: str | None = None
    duration: float | None = None
    level: float | None = None
    offset: float | None = None
    volume: float | None = None
    duck: bool | None = None
    words: int | None = None


class EditPlan(_M):
    title: str = Field("", description="Short name for this variation")
    beat_sync: bool = Field(False, description="Snap cuts to the music's beats")
    ops: list[Op]
    rationale: str = ""


class Fix(_M):
    layer: str
    x: str | None = None
    y: str | None = None
    size: int | None = None
    start: float | None = None
    end: float | None = None
    text: str | None = None
    remove: bool = False


class Review(_M):
    ok: bool
    issues: list[str] = Field(default_factory=list)
    fixes: list[Fix] = Field(default_factory=list)


class SegmentNote(_M):
    id: str
    description: str
    quality: int = Field(3, ge=1, le=5)
    talking: bool = False
    tags: list[str] = Field(default_factory=list)


class FootageNotes(_M):
    segments: list[SegmentNote]

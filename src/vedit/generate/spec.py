"""Data shapes for generation: the wizard's request, per-variation specs, and edit plans."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from ..library import FORMATS
from ..ops.moves import MOVE_NAMES
from ..project import ROLES

Role = Literal[ROLES]  # type: ignore[valid-type]
MoveName = Literal[MOVE_NAMES]  # type: ignore[valid-type]

AXES: dict[str, list[str]] = {
    "hook": ["reference", "question", "bold_claim", "stat", "pain_point", "pov", "before_after"],
    "order": ["best_first", "story", "reverse", "mixed"],
    "pacing": ["reference", "faster", "slower"],
    "caption_style": ["reference", "bold", "boxed", "neon", "karaoke", "minimal"],
    "text_animation": ["reference", "pop_in", "slide_in", "typewriter", "drop_in", "zoom_in", "punch"],
    "transitions": ["reference", "none", "accent"],
    "music_section": ["start", "middle", "late"],
    "color_look": ["reference", "clean-bright", "warm-film", "teal-orange", "moody", "vibrant"],
    "font": ["reference", "condensed", "bold_sans", "serif", "rounded", "handwritten"],
    "structure": ["reference", "rhythmic_montage", "text_led", "one_take", "before_after", "problem_solution"],
}
AXIS_LABELS = {
    "hook": "Hook", "order": "Clip order", "pacing": "Pacing", "caption_style": "Caption style",
    "text_animation": "Text animation", "transitions": "Transitions", "music_section": "Music section",
    "color_look": "Colour look", "font": "Font", "structure": "Structure",
}
STRUCTURE_HINT = {
    "rhythmic_montage": "Rhythmic montage: many short shots cut to the music, little text, energy builds to the payoff.",
    "text_led": "Text-led story: a sequence of short on-screen lines carries the story; footage illustrates each line.",
    "one_take": "One-take feel: few, longer shots with continuous motion; minimal cutting, the camera tells it.",
    "before_after": "Before / after: show the 'before' state, a clear turning point, then the 'after' result.",
    "problem_solution": "Problem / solution: open on a relatable problem, then show the product solving it, then proof.",
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
    footage: list[str] = Field(min_length=1, description="Upload ids used for their pictures")
    sound: list[str] = Field(default_factory=list, max_length=10,
                             description="Upload ids used for their audio only (audio or video); the videos take turns using them")
    brief: Brief = Field(default_factory=Brief)
    count: int = Field(3, ge=1, le=10)
    axes: list[str] = Field(default_factory=lambda: list(AXES))
    quality: Literal["best", "balanced", "economy"] = "balanced"
    review: bool = Field(True, description="Run the AI auditor (review + fix rounds) on each variation")
    explore: bool = Field(True, description="Write 3 concepts per variation and keep the best (AI ranks them across siblings)")
    audit_threshold: int | None = Field(None, ge=0, le=100, description="Pass mark for the audit score (default: audit settings)")
    audit_rounds: int | None = Field(None, ge=0, le=8, description="Max editor revisions per video (default: audit settings)")
    models: dict[str, str] = Field(default_factory=dict,
                                   description="Model per role for this generation: {vision|planner|editor|auditor: '<provider>/<model>'}")


class Concept(_M):
    title: str = Field(description="3-6 words naming the idea")
    arc: str = Field(description="The emotional arc in one line, e.g. 'frustration -> surprise -> relief'")
    motif: str = Field("", description="A visual motif that recurs, e.g. 'hands', 'the colour yellow', 'clock close-ups'")
    signature_move: MoveName | None = Field(None, description="The one memorable editing move this concept is built around")
    opening: str = Field(description="Exactly what the first 1.5 seconds show and say")
    why: str = Field("", description="Why this would work for the brief and audience")


class ConceptSet(_M):
    concepts: list[Concept] = Field(description="Exactly 3 clearly different concepts")


class Pick(_M):
    variation: int
    concept: int = Field(description="1-based index of the chosen concept for that variation")
    reason: str = ""


class Ranking(_M):
    picks: list[Pick]


class Creative(_M):
    """The planner's creative content for one variation."""
    index: int = Field(description="Variation number, 1-based")
    angle: str = Field(description="One line: the idea of this variation")
    hook_text: str = Field(description="On-screen hook line (short)")
    cta_text: str = ""
    structure: list[str] = Field(default_factory=list, description="Sections in order")
    notes: str = ""
    concept: SkipJsonSchema[Concept | None] = None  # chosen by rank() when exploring
    concept_reason: SkipJsonSchema[str] = ""


class CreativePlan(_M):
    variations: list[Creative]


class Op(_M):
    """One edit operation. Flat on purpose (LLMs fill flat objects more reliably than unions)."""
    op: Literal["clip", "text", "image", "transition", "jumpcut_zoom", "music", "captions", "fit", "move"]
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
    font: str | None = None
    letter_spacing: float | None = None
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
    beat: str | None = Field(None, description="clip/text: id of the beat it belongs to")
    segment: str | None = Field(None, description="clip: the footage segment id it is cut from, e.g. f1.s3")
    why: str | None = Field(None, description="clip/text: why it is here, max ~15 words, specific to what is on screen")
    audio: Literal["voice", "ambient", "mute"] | None = Field(
        None, description="clip: its own sound. voice = keep (music ducks under it); ambient = keep quietly; mute = music only")


class PlanBeat(_M):
    id: str = Field(description="b1, b2, ...")
    role: Role = "other"
    purpose: str = Field("", description="One line: what this beat must make the viewer feel or understand")


class EditPlan(_M):
    title: str = Field("", description="Short name for this variation")
    beats: list[PlanBeat] = Field(default_factory=list, description="The structure, in order; the first is the hook")
    beat_sync: bool = Field(False, description="Snap cuts to the music's beats")
    ops: list[Op]
    rationale: str = Field("", description="1-3 sentences: why this opening shot and this order")
    grade: SkipJsonSchema[dict | None] = None  # set by enforce(), never asked of the model
    problems: SkipJsonSchema[list[str]] = Field(default_factory=list)  # validate_plan() errors left after the retry


class PatchOp(_M):
    """One targeted change to the current plan. Clips are C1..Cn and titles T1..Tn as listed in the prompt."""
    op: Literal["replace_clip", "trim_clip", "move_clip", "remove_clip", "insert_clip", "set_audio",
                "edit_text", "add_text", "remove_text", "set_music", "set_move"]
    target: str | None = Field(None, description="C3 / T2: the clip or title it changes (insert_clip: insert BEFORE this clip; empty = at the end)")
    segment: str | None = Field(None, description="replace_clip / insert_clip: footage segment id, e.g. f1.s4")
    start: float | None = Field(None, description="clips: seconds inside the file; titles: timeline seconds")
    end: float | None = None
    to: int | None = Field(None, description="move_clip: new position, 1 = first")
    beat: str | None = None
    why: str | None = Field(None, description="replace_clip / insert_clip / add_text: why this is here now")
    audio: Literal["voice", "ambient", "mute"] | None = None
    text: str | None = None
    y: str | None = None
    size: int | None = None
    offset: float | None = Field(None, description="set_music: where in the song to start")
    volume: float | None = None
    type: str | None = Field(None, description="set_move: move name (or 'none' to remove the move)")
    into: int | None = Field(None, description="set_move: clip number it applies to (1 = first clip)")


class PlanPatch(_M):
    fixes: list[str] = Field(default_factory=list, description="Ids of the issues this patch fixes (I1, I3...)")
    ops: list[PatchOp] = Field(default_factory=list, description="At most 6 targeted changes; everything else stays exactly as it is")
    note: str = Field("", description="One line: what you changed and why it fixes those issues")


class SegmentNote(_M):
    id: str
    description: str
    quality: int = Field(3, ge=1, le=5)
    talking: bool = False
    tags: list[str] = Field(default_factory=list)
    shot: Literal["wide", "medium", "close", "detail", "screen", "other"] = "medium"
    subject: str = Field("", description="Main subject in 1-4 words: 'woman's hands', 'serum bottle', 'phone screen'")
    good_for: list[Role] = Field(default_factory=list, description="1-3 roles this segment could play in an edit")


class FootageNotes(_M):
    segments: list[SegmentNote]


class MediaRole(_M):
    id: str
    role: Literal["footage", "sound"]
    reason: str = ""


class MediaRoles(_M):
    files: list[MediaRole]

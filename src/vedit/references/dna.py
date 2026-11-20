"""Style DNA: what makes a reference look and sound the way it does, in vedit's own vocabulary.

Every enum here is something vedit can actually do (animation presets, caption styles,
transitions), so any trait can be turned into an edit. The AI fills the visual traits;
code overwrites the measured ones (tempo, pacing, cuts on beat) with exact numbers.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from ..ops.animate import PRESETS
from ..ops.captions import CAPTION_STYLES
from ..ops.color import LOOKS as COLOR_LOOKS
from ..ops.fonts import FONTS
from ..ops.moves import MOVE_NAMES
from ..project import TRANSITIONS

IN_PRESETS = tuple(k for k, v in PRESETS.items() if v[0] == "in")
OUT_PRESETS = tuple(k for k, v in PRESETS.items() if v[0] == "out")
LOOP_PRESETS = tuple(k for k, v in PRESETS.items() if v[0] == "loop")
HOOK_PATTERNS = ("question", "bold_claim", "stat", "pain_point", "pov", "before_after", "list", "story", "visual_only", "other")

InPreset = Literal[IN_PRESETS]  # type: ignore[valid-type]
OutPreset = Literal[OUT_PRESETS]  # type: ignore[valid-type]
LoopPreset = Literal[LOOP_PRESETS]  # type: ignore[valid-type]
TransitionName = Literal[TRANSITIONS]  # type: ignore[valid-type]
CaptionStyle = Literal[tuple(CAPTION_STYLES)]  # type: ignore[valid-type]
HookPattern = Literal[HOOK_PATTERNS]  # type: ignore[valid-type]
FontKey = Literal[tuple(FONTS)]  # type: ignore[valid-type]
ColorLook = Literal[tuple(COLOR_LOOKS)]  # type: ignore[valid-type]
MoveName = Literal[MOVE_NAMES]  # type: ignore[valid-type]


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Pacing(_M):
    avg_shot_s: float = Field(2.0, description="Average shot length in seconds")
    cuts_per_10s: float = 5.0
    rhythm: Literal["steady", "accelerating", "decelerating", "varied"] = "steady"
    speed_ramps: bool = Field(False, description="Speed-up / slow-mo moments")


class Hook(_M):
    duration_s: float = Field(3.0, description="How long the hook lasts before the main content")
    pattern: HookPattern = "bold_claim"
    first_cut_s: float | None = None
    text_on_screen: bool = True
    example: str = Field("", description="What the hook says/shows, in a few words (inspiration only, never copied)")


class TextDNA(_M):
    present: bool = True
    position_y: str = Field("25%", description="Vertical centre of titles, % of canvas height")
    size: Literal["small", "medium", "large", "huge"] = "large"
    uppercase: bool = False
    color: str = "#FFFFFF"
    stroke: bool = True
    bg_box: bool = False
    bg_color: str | None = None
    font_feel: str = Field("bold sans", description="e.g. bold sans, condensed, serif, handwritten, rounded")
    font: FontKey | None = Field(None, description="The bundled font closest to the titles' typography")
    letter_spacing: float = Field(0.0, description="Extra px between letters: 0 normal, 6-16 for wide-tracked cinematic caps")
    animation_in: list[InPreset] = Field(default_factory=lambda: ["pop_in"])
    animation_out: list[OutPreset] = Field(default_factory=lambda: ["fade_out"])
    loop: list[LoopPreset] = Field(default_factory=list)
    on_beat: bool = Field(False, description="Text appears exactly on beats")
    words_per_title: int = Field(5, ge=1, le=20)


class CaptionsDNA(_M):
    present: bool = False
    style: CaptionStyle = "bold"
    words_at_once: int = Field(3, ge=1, le=8)
    y: str = "72%"
    highlight: str = "#FFE600"


class MotionDNA(_M):
    jumpcut_zoom: float | None = Field(None, description="Punch-in level on alternating shots, e.g. 1.12; null if none")
    ken_burns: bool = False
    zoom_punch_on_beat: bool = False
    shake: bool = False
    camera: Literal["static", "handheld", "moving", "mixed"] = "mixed"


class TransitionsDNA(_M):
    default: Literal[("none",) + TRANSITIONS] = "none"  # type: ignore[valid-type]
    accents: list[TransitionName] = Field(default_factory=list)
    frequency: Literal["never", "rare", "often", "every_cut"] = "rare"
    duration_s: float = 0.3


class SoundDNA(_M):
    bpm: float | None = None
    beat_sync_score: float | None = None
    cut_on_beat: bool = False
    voiceover: bool = False
    music: bool = True
    music_energy: Literal["low", "medium", "high"] = "medium"
    sfx_on_text: bool = False
    ducking: bool = False


class ColorDNA(_M):
    look: ColorLook = Field("natural", description="The colour look closest to the reference's grade")
    intensity: float = Field(0.8, ge=0, le=1, description="How strong that look is in the reference")
    measured: SkipJsonSchema[dict[str, float]] = Field(default_factory=dict)  # filled by apply_measured(), not the model
    palette: SkipJsonSchema[dict] = Field(default_factory=dict)  # analysis.palette() of the lead reference, set by Generate


class SignatureMove(_M):
    move: MoveName = Field(description="The library move closest to what the reference does")
    at: float | None = Field(None, description="Timeline seconds where you saw it in the reference, if you can tell")
    note: str = Field("", description="What exactly the reference does, in a few words")
    confirmed: SkipJsonSchema[bool] = False  # set by confirm_moves() when the measurements back it up


class Gap(_M):
    what: str = Field(description="Something the reference does that the allowed values can't reproduce exactly, in a few words")
    closest: str = Field("", description="The closest vedit command or trait to use instead, e.g. 'vedit transition hblur' or 'text.animation_in = typewriter'")


class StyleDNA(_M):
    version: int = 1
    summary: str = Field("", description="One sentence a video editor would use to describe this style")
    pacing: Pacing = Field(default_factory=Pacing)
    hook: Hook = Field(default_factory=Hook)
    structure: list[str] = Field(default_factory=lambda: ["hook", "content", "cta"],
                                 description="Sections in order, e.g. hook, problem, demo, demo, proof, cta")
    text: TextDNA = Field(default_factory=TextDNA)
    captions: CaptionsDNA = Field(default_factory=CaptionsDNA)
    motion: MotionDNA = Field(default_factory=MotionDNA)
    transitions: TransitionsDNA = Field(default_factory=TransitionsDNA)
    sound: SoundDNA = Field(default_factory=SoundDNA)
    color_feel: str = Field("", description="One phrase on the colour grade, e.g. 'warm faded film', 'clean bright daylight'")
    color: ColorDNA = Field(default_factory=ColorDNA)
    signature_moves: list[SignatureMove] = Field(default_factory=list, description="0-4 memorable moves the reference uses")
    gaps: list[Gap] = Field(default_factory=list, description="0-5 things in the reference vedit can't do exactly, each with the closest option")
    confidence: dict[str, float] = Field(default_factory=dict, description="0..1 per area: hook, text, captions, motion, transitions, sound")
    notes: str = ""
    tags: list[str] = Field(default_factory=list, description="3-6 short tags, e.g. fast-cut, beat-sync, bold-captions")


# ---- measured traits ------------------------------------------------------------------

def apply_measured(dna: StyleDNA, m: dict) -> StyleDNA:
    """Overwrite traits that code measures exactly."""
    pace, audio = m.get("pacing") or {}, m.get("audio") or {}
    dna.pacing.avg_shot_s = pace.get("avg_shot_s", dna.pacing.avg_shot_s)
    dna.pacing.cuts_per_10s = pace.get("cuts_per_10s", dna.pacing.cuts_per_10s)
    dna.pacing.rhythm = pace.get("rhythm", dna.pacing.rhythm)
    dna.hook.first_cut_s = m.get("first_cut_s")
    dna.sound.bpm = audio.get("bpm")
    dna.sound.beat_sync_score = m.get("cuts_on_beat")
    score, chance = m.get("cuts_on_beat"), m.get("cuts_on_beat_chance") or 0.35
    dna.sound.cut_on_beat = bool(score is not None and len(m.get("cuts") or []) >= 4 and score >= max(0.6, chance + 0.25))
    if not m.get("has_audio"):
        dna.sound.music = dna.sound.voiceover = False
    if m.get("color"):
        dna.color.measured = {k: float(v) for k, v in m["color"].items()}
    confirm_moves(dna, m)
    dna.confidence["pacing"] = 1.0
    if audio:
        dna.confidence["tempo"] = float(audio.get("tempo_confidence") or 0)
    return dna


def confirm_moves(dna: StyleDNA, m: dict) -> None:
    """Mark the moves the measurements back up. Unconfirmable moves are kept, just not marked."""
    cuts = m.get("cuts") or []
    flashes = (m.get("motion") or {}).get("flashes") or []
    energy = m.get("motion_energy") or []
    fps = float((m.get("motion") or {}).get("fps") or 10)
    mean = (sum(energy) / len(energy)) if energy else 0.0

    def near(ts, t, tol):
        return any(abs(x - t) <= tol for x in ts)

    def window(t, half):
        a, b = max(0, int((t - half) * fps)), min(len(energy), int((t + half) * fps) + 1)
        return energy[a:b]

    for sm in dna.signature_moves:
        t = sm.at
        if sm.move == "flash_cut":
            sm.confirmed = bool(flashes) and (t is None or near(flashes, t, 0.6))
        elif sm.move == "speed_ramp":
            sm.confirmed = bool(dna.pacing.speed_ramps)
        elif sm.move == "whip" and t is not None and energy:
            sm.confirmed = near(cuts, t, 0.6) and max(window(t, 0.3) or [0]) > 2.5 * mean
        elif sm.move == "punch_in" and t is not None and energy:
            sm.confirmed = not near(cuts, t, 0.2) and max(window(t, 0.15) or [0]) > 3 * mean
        elif sm.move == "freeze_title" and t is not None and energy:
            w = window(t + 0.4, 0.4)
            sm.confirmed = bool(w) and max(w) < 0.2 * mean
        else:
            sm.confirmed = False


def from_metrics(m: dict) -> StyleDNA:
    """A Style DNA with only the measured traits (used without AI, or when the AI fails)."""
    dna = apply_measured(StyleDNA(), m)
    fast = dna.pacing.avg_shot_s < 1.5
    dna.summary = f"{'Fast' if fast else 'Relaxed'} cutting (~{dna.pacing.avg_shot_s:g}s shots)" + (
        f", cuts on the beat at {dna.sound.bpm:g} BPM" if dna.sound.cut_on_beat else "")
    dna.tags = (["fast-cut"] if fast else ["slow-cut"]) + (["beat-sync"] if dna.sound.cut_on_beat else [])
    dna.motion.jumpcut_zoom = 1.12 if fast else None
    dna.captions.present = bool(m.get("transcript"))
    dna.sound.voiceover = bool(m.get("transcript"))
    for k in ("hook", "text", "captions", "motion", "transitions"):
        dna.confidence[k] = 0.0
    dna.notes = "Measured only (no AI): visual traits are defaults."
    return dna


# ---- overrides ------------------------------------------------------------------------

def leaves(d: dict, prefix: str = "") -> dict[str, Any]:
    """Flatten to dotted paths: {"text.color": "#fff", ...}."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        p = f"{prefix}{k}"
        if isinstance(v, dict) and k != "confidence":
            out.update(leaves(v, p + "."))
        else:
            out[p] = v
    return out


def apply_overrides(dna: dict, overrides: dict[str, Any]) -> StyleDNA:
    data = StyleDNA.model_validate(dna).model_dump()
    for path, value in (overrides or {}).items():
        cur = data
        parts = path.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value
    return StyleDNA.model_validate(data)


# ---- merging several references -----------------------------------------------------------

def _merge(values: list[Any], lead: int | None) -> Any:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    first = vals[0]
    lead_v = values[lead] if lead is not None and lead < len(values) and values[lead] is not None else first
    if isinstance(first, bool):
        n = sum(1 for v in vals if v)
        return lead_v if n * 2 == len(vals) else n * 2 > len(vals)
    if isinstance(first, (int, float)):
        avg = sum(float(v) for v in vals) / len(vals)
        return int(round(avg)) if all(isinstance(v, int) and not isinstance(v, bool) for v in vals) else round(avg, 2)
    if isinstance(first, list):
        if all(isinstance(x, str) for v in vals for x in v):
            cnt = Counter(x for v in vals for x in v)
            size = max(len(v) for v in vals)
            order = {x: i for i, x in enumerate(lead_v or [])}
            return [x for x, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], order.get(kv[0], 99)))][:size]
        return lead_v
    if isinstance(first, dict):
        keys = dict.fromkeys(k for v in vals for k in v)
        return {k: _merge([v.get(k) for v in values if isinstance(v, dict)], None if lead is None else
                          sum(1 for v in values[:lead] if isinstance(v, dict))) for k in keys}
    cnt = Counter(vals)
    top = max(cnt.values())
    winners = [v for v, c in cnt.items() if c == top]
    return lead_v if lead_v in winners else winners[0]


def merge(dnas: list[StyleDNA], lead: int | None = None) -> StyleDNA:
    """Combine references: numbers average, enums take the majority, ties go to the lead reference."""
    if not dnas:
        return StyleDNA()
    if len(dnas) == 1:
        return dnas[0].model_copy(deep=True)
    dicts = [d.model_dump() for d in dnas]
    merged = _merge(dicts, lead)
    li = lead if lead is not None else 0
    merged["summary"] = dicts[li]["summary"]
    merged["structure"] = dicts[li]["structure"]
    merged["notes"] = " | ".join(d["notes"] for d in dicts if d["notes"])[:600]
    return StyleDNA.model_validate(merged)


EDITABLE: list[tuple[str, str, Any]] = [
    # (dotted path, label, options: list = enum, "list:<enum>" handled as one choice, bool/float/str = type)
    ("hook.pattern", "Hook type", list(HOOK_PATTERNS)),
    ("hook.duration_s", "Hook length (s)", float),
    ("pacing.avg_shot_s", "Average shot (s)", float),
    ("text.present", "Titles on screen", bool),
    ("text.position_y", "Title position (y)", str),
    ("text.size", "Title size", ["small", "medium", "large", "huge"]),
    ("text.color", "Title colour", "color"),
    ("text.uppercase", "Uppercase titles", bool),
    ("text.font", "Title font", list(FONTS)),
    ("text.bg_box", "Title on a box", bool),
    ("text.animation_in", "Title animates in", list(IN_PRESETS)),
    ("text.animation_out", "Title animates out", list(OUT_PRESETS)),
    ("text.on_beat", "Titles on the beat", bool),
    ("captions.present", "Captions", bool),
    ("captions.style", "Caption style", list(CAPTION_STYLES)),
    ("captions.words_at_once", "Caption words at once", int),
    ("motion.jumpcut_zoom", "Jump-cut zoom (blank = none)", float),
    ("motion.ken_burns", "Ken Burns zoom", bool),
    ("transitions.default", "Default transition", ["none", *TRANSITIONS]),
    ("transitions.frequency", "Transition frequency", ["never", "rare", "often", "every_cut"]),
    ("color.look", "Colour look", list(COLOR_LOOKS)),
    ("color.intensity", "Colour look strength (0-1)", float),
    ("sound.cut_on_beat", "Cut on the beat", bool),
    ("sound.voiceover", "Voice-over", bool),
]


def editable() -> list[dict]:
    out = []
    for path, label, kind in EDITABLE:
        list_field = path in ("text.animation_in", "text.animation_out")
        if isinstance(kind, list):
            out.append({"path": path, "label": label, "type": "list" if list_field else "enum", "options": kind})
        else:
            out.append({"path": path, "label": label, "type": kind if isinstance(kind, str) else kind.__name__})
    return out


def card(dna: StyleDNA) -> list[dict]:
    """Human-readable trait rows for the Style card."""
    d = dna
    t = d.transitions
    rows = [
        ("Pacing", f"{d.pacing.avg_shot_s:g}s shots · {d.pacing.cuts_per_10s:g} cuts / 10s · {d.pacing.rhythm}"
                   + (" · speed ramps" if d.pacing.speed_ramps else "")),
        ("Hook", f"{d.hook.pattern.replace('_', ' ')} · {d.hook.duration_s:g}s" + (f" · first cut {d.hook.first_cut_s:g}s" if d.hook.first_cut_s else "")
                 + (f" · “{d.hook.example}”" if d.hook.example else "")),
        ("Structure", " → ".join(d.structure)),
        ("Text", "none" if not d.text.present else f"{d.text.size}, {d.text.font or d.text.font_feel}, {d.text.color}"
                 + (" uppercase" if d.text.uppercase else "") + (" with outline" if d.text.stroke else "")
                 + (" on a box" if d.text.bg_box else "") + f" at y {d.text.position_y} · in: {', '.join(d.text.animation_in) or 'cut'}"
                 + f" · out: {', '.join(d.text.animation_out) or 'cut'}" + (" · on the beat" if d.text.on_beat else "")),
        ("Captions", "none" if not d.captions.present else f"{d.captions.style}, {d.captions.words_at_once} words at once, y {d.captions.y}"),
        ("Motion", ", ".join(x for x in [f"jump-cut zoom {d.motion.jumpcut_zoom:g}" if d.motion.jumpcut_zoom else "",
                                         "Ken Burns" if d.motion.ken_burns else "", "zoom punch on beat" if d.motion.zoom_punch_on_beat else "",
                                         "shake" if d.motion.shake else "", f"{d.motion.camera} camera"] if x)),
        ("Transitions", f"{t.default} by default" + (f", accents: {', '.join(t.accents)}" if t.accents else "") + f" · {t.frequency.replace('_', ' ')}"),
        ("Sound", ", ".join(x for x in [f"{d.sound.bpm:g} BPM" if d.sound.bpm else "no clear tempo",
                                        f"{round((d.sound.beat_sync_score or 0) * 100)}% of cuts on the beat" if d.sound.beat_sync_score is not None else "",
                                        "voice-over" if d.sound.voiceover else "", f"{d.sound.music_energy}-energy music" if d.sound.music else "no music",
                                        "SFX on text" if d.sound.sfx_on_text else "", "ducking" if d.sound.ducking else ""] if x)),
        ("Moves", ", ".join(f"{x.move.replace('_', ' ')}" + (f" @{x.at:g}s" if x.at is not None else "") + (" ✓" if x.confirmed else "")
                            for x in d.signature_moves) or "—"),
        ("Colour", ", ".join(x for x in [
            f"{d.color.look} look" + (f" at {d.color.intensity:g}" if d.color.look != "natural" else ""),
            d.color_feel,
            (f"brightness {d.color.measured['luma']:.2f}, saturation {d.color.measured['sat']:.2f}, "
             + ("warm" if d.color.measured['warmth'] > 0.03 else "cool" if d.color.measured['warmth'] < -0.03 else "neutral"))
            if d.color.measured.get("luma") is not None else ""] if x)),
    ]
    return [{"label": a, "value": b, "confidence": d.confidence.get({"colour": "color"}.get(a.lower(), a.lower()))} for a, b in rows]

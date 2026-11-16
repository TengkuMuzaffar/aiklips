"""Planning variations: code decides WHAT differs, the AI writes the content, code checks the result.

1. assign_axes()   – deterministic spread over the creative axes the user ticked. Variation 1
                      stays closest to the reference; the others each take a different value
                      on every ticked axis.
2. creative()      – ONE AI call writes angle / hook line / CTA for all N variations together,
                      so it can keep them apart.
3. directives()    – turns DNA + brief + axes into concrete numbers and styles per variation.
4. plan_edit()     – one AI call per variation picks the clips and text timing (EditPlan);
                      enforce() then guarantees the style directives regardless of the model.
5. fingerprint()   – near-duplicate check between finished siblings.
"""

from __future__ import annotations

import json
import random

from ..ai import client as ai
from ..ai import prompts
from ..ops import fonts
from ..ops.animate import PRESETS
from ..ops.color import LOOKS as COLOR_LOOKS
from ..references.dna import StyleDNA
from .spec import AXES, Brief, BatchSpec, Creative, CreativePlan, EditPlan, Op, Review

TEXT_SIZE = {"small": 56, "medium": 72, "large": 92, "huge": 118}
PACE = {"reference": 1.0, "faster": 0.7, "slower": 1.45}
MUSIC_AT = {"start": 0.0, "middle": 0.35, "late": 0.6}
ORDER_HINT = {
    "best_first": "Open with the single most striking shot, then the rest by impact.",
    "story": "Tell it in a natural order: setup, then detail, then payoff.",
    "reverse": "Start from the result / payoff, then show how you get there.",
    "mixed": "Alternate close-ups and wide shots for rhythm.",
}


def assign_axes(count: int, enabled: list[str], seed: str) -> list[dict[str, str]]:
    rng = random.Random(seed)
    orders = {}
    for axis, values in AXES.items():
        rest = values[1:]
        rng.shuffle(rest)
        orders[axis] = [values[0]] + rest
    out = []
    for i in range(count):
        out.append({axis: (orders[axis][i % len(orders[axis])] if axis in enabled else AXES[axis][0]) for axis in AXES})
    return out


def assign_sounds(axes: list[dict[str, str]], log: dict) -> None:
    """With several sounds, the variations take turns: video 1 gets the first, video 2 the second, ...
    Stored on each variation's axes as 'sound' (a file id), so a regenerate keeps its sound."""
    sounds = [fid for fid, f in log.items() if f["kind"] == "audio"]
    sounds.sort(key=lambda fid: (not fid.startswith("m"), fid))  # Sound-step files first, then legacy music in footage
    for i, a in enumerate(axes):
        if sounds:
            a["sound"] = sounds[i % len(sounds)]


def _style_brief(dna: StyleDNA) -> dict:
    d = dna.model_dump(exclude={"confidence", "version"})
    return d


def _footage_brief(log: dict) -> dict:
    out = {}
    for fid, f in log.items():
        if f["kind"] == "video":
            out[fid] = {"name": f["name"], "duration": f["duration"],
                        "shots": [f"{s['id']} ({s['from']:.1f}-{s['to']:.1f}s, q{s.get('quality', 3)}): {s.get('description', '')}"
                                  for s in f["segments"]][:60]}
        else:
            out[fid] = {k: v for k, v in f.items() if k != "segments"}
    return out


def creative(spec: BatchSpec, dna: StyleDNA, log: dict, axes: list[dict]) -> tuple[list[Creative], dict]:
    b = spec.brief
    prompt = (
        f"STYLE DNA (merged references):\n{json.dumps(_style_brief(dna), indent=1)}\n\n"
        f"BRIEF:\n{b.model_dump_json(indent=1)}\n"
        + (f"The user wrote this hook line; variation 1 must use it verbatim, others may rephrase it for their hook type: {b.hook!r}\n" if b.hook else "")
        + f"\nFOOTAGE:\n{json.dumps(_footage_brief(log), indent=1)}\n\n"
        f"VARIATIONS ({spec.count}), with their fixed axes:\n"
        + "\n".join(f"{i + 1}. " + ", ".join(f"{k}={v}" for k, v in a.items()) for i, a in enumerate(axes))
        + f"\n\nWrite in language: {b.language}. Return exactly {spec.count} variations, index 1..{spec.count}."
    )
    res, usage = ai.call("planner", prompt, CreativePlan, system=prompts.PLANNER, quality=spec.quality)
    by_idx = {c.index: c for c in res.variations}
    out = []
    for i in range(spec.count):
        c = by_idx.get(i + 1) or (res.variations[i] if i < len(res.variations) else None)
        if c is None:
            c = Creative(index=i + 1, angle=b.product or "Variation", hook_text=b.hook or (b.product[:40] or "Watch this"), cta_text=b.cta)
        c.index = i + 1
        if i == 0 and b.hook:
            c.hook_text = b.hook
        if b.cta and not c.cta_text:
            c.cta_text = b.cta
        out.append(c)
    return out, usage


# ---- per-variation directives ------------------------------------------------------------

def _font(dna: StyleDNA, axes: dict[str, str]) -> tuple[str, str]:
    """(title font, accent font). The axis picks a feel; 'reference' uses the studied font."""
    axis = axes.get("font", "reference")
    if axis == "reference":
        main = dna.text.font if dna.text.font in fonts.FONTS else fonts.for_feel(dna.text.font_feel)
    else:
        main = fonts.for_feel(axis)
    feel = fonts.FONTS[main][2]
    accent = "dm-serif-italic" if feel != "serif" else "poppins-black"  # one contrasting font for a single emphasis line
    return main, accent


def _grade(dna: StyleDNA, axes: dict[str, str]) -> dict:
    axis = axes.get("color_look", "reference")
    look = dna.color.look if axis == "reference" else axis
    return {"look": look if look in COLOR_LOOKS else "natural", "intensity": dna.color.intensity if axis == "reference" else 0.8,
            "target": dict(dna.color.measured) if dna.color.measured.get("luma") is not None else None,
            "strength": 0.7}


def directives(dna: StyleDNA, brief: Brief, axes: dict[str, str], log: dict, captions_available: bool) -> dict:
    avg = max(0.4, min(6.0, dna.pacing.avg_shot_s * PACE[axes["pacing"]]))
    text_in = list(dna.text.animation_in) if axes.get("text_animation", "reference") == "reference" else [axes["text_animation"]]
    text_out = list(dna.text.animation_out)
    if axes["transitions"] == "reference":
        trans = {"default": dna.transitions.default, "accents": list(dna.transitions.accents), "frequency": dna.transitions.frequency}
    elif axes["transitions"] == "none":
        trans = {"default": "none", "accents": [], "frequency": "never"}
    else:
        trans = {"default": "none", "accents": list(dna.transitions.accents) or ["zoomin", "hblur"], "frequency": "rare"}
    music = axes.get("sound") if log.get(axes.get("sound") or "", {}).get("kind") == "audio" else None
    music = music or next((fid for fid, f in log.items() if f["kind"] == "audio"), None)
    talking = any(s.get("talking") for f in log.values() if f["kind"] == "video" for s in f["segments"])
    want_caps = captions_available and (dna.captions.present or talking) and any(f.get("has_audio") for f in log.values() if f["kind"] == "video")
    music_offset = 0.0
    if music:
        mdur = float(log[music].get("duration") or 0)
        music_offset = round(max(0.0, (mdur - brief.length_s) * MUSIC_AT[axes["music_section"]]), 2)
    font, accent = _font(dna, axes)
    return {
        "length_s": brief.length_s,
        "avg_shot_s": round(avg, 2),
        "clip_count": max(1, round(brief.length_s / avg)),
        "order": ORDER_HINT[axes["order"]],
        "hook_type": dna.hook.pattern if axes["hook"] == "reference" else axes["hook"],
        "hook_duration_s": dna.hook.duration_s,
        "text": {"y": dna.text.position_y, "size": TEXT_SIZE[dna.text.size], "color": dna.text.color, "uppercase": dna.text.uppercase,
                 "stroke": dna.text.stroke, "bg": (dna.text.bg_color or "#000000B0") if dna.text.bg_box else None,
                 "presets": [p for p in text_in + text_out if p in PRESETS], "on_beat": dna.text.on_beat,
                 "font": font, "accent_font": accent, "letter_spacing": dna.text.letter_spacing},
        "grade": _grade(dna, axes),
        "captions": None if not want_caps else {
            "style": dna.captions.style if axes["caption_style"] == "reference" else axes["caption_style"],
            "words": dna.captions.words_at_once, "y": dna.captions.y},
        "transitions": {**trans, "duration": dna.transitions.duration_s},
        "jumpcut_zoom": dna.motion.jumpcut_zoom,
        "ken_burns": dna.motion.ken_burns,
        "music": None if not music else {"file": music, "offset": music_offset, "duck": dna.sound.voiceover or talking,
                                         "volume": 0.18 if (dna.sound.voiceover or talking) else 0.8},
        "beat_sync": bool(music and dna.sound.cut_on_beat),
    }


def plan_edit(spec: BatchSpec, dna: StyleDNA, log: dict, c: Creative, axes: dict, d: dict, avoid: list[str] | None = None) -> tuple[EditPlan, dict]:
    images = [fid for fid, f in log.items() if f["kind"] == "image"]
    prompt = (
        f"STYLE DNA:\n{json.dumps(_style_brief(dna), indent=1)}\n\n"
        f"BRIEF:\n{spec.brief.model_dump_json(indent=1)}\n\n"
        f"FOOTAGE (use these file ids; segment ids show good ranges):\n{json.dumps(_footage_brief(log), indent=1)}\n\n"
        f"THIS VARIATION (#{c.index}): angle: {c.angle}\nhook line: {c.hook_text}\nCTA: {c.cta_text or '(none)'}\n"
        f"structure: {' → '.join(c.structure) or 'hook → content → cta'}\nnotes: {c.notes}\n"
        f"axes: {json.dumps(axes)}\n\n"
        f"DIRECTIVES (follow exactly):\n{json.dumps(d, indent=1)}\n"
        f"- About {d['clip_count']} clips of ~{d['avg_shot_s']}s each, total ≈ {d['length_s']}s. Order: {d['order']}\n"
        f"- Hook text '{c.hook_text}' from 0s to ~{d['hook_duration_s']}s; CTA text near the end if there is one.\n"
        + (f"- Images you may overlay: {', '.join(images)}\n" if images else "")
        + (f"- Add a music op with file {d['music']['file']} and the given offset (other sound files belong to sibling videos).\n" if d["music"] else "")
        + ("- Add one captions op.\n" if d["captions"] else "- Do not add captions.\n")
        + (f"\nIt must look different from sibling variations: {'; '.join(avoid)}\n" if avoid else "")
    )
    plan, usage = ai.call("editor", prompt, EditPlan, system=prompts.EDITOR, quality=spec.quality)
    return enforce(plan, d, c), usage


def enforce(plan: EditPlan, d: dict, c: Creative) -> EditPlan:
    """Guarantee the style directives whatever the model did (the model chooses clips and words)."""
    ops = [o for o in plan.ops if o.op not in ("music", "captions", "jumpcut_zoom")]
    if not any(o.op == "text" for o in ops) and c.hook_text:
        ops.append(Op(op="text", text=c.hook_text, start=0.0, end=d["hook_duration_s"]))
    t = d["text"]
    accent_used = False
    for o in ops:
        if o.op == "text":
            # one title font for the whole edit, plus at most one line in the accent font
            if o.font == t.get("accent_font") and not accent_used and o.font:
                accent_used = True
            else:
                o.font = t.get("font")
            o.letter_spacing = t.get("letter_spacing") or None
            o.size = o.size or t["size"]
            o.color = o.color or t["color"]
            o.uppercase = t["uppercase"] if o.uppercase is None else o.uppercase
            o.bg = o.bg or t["bg"]
            o.y = o.y or t["y"]
            o.presets = [p for p in (o.presets or t["presets"]) if p in PRESETS] or t["presets"]
    tr = d["transitions"]
    if tr["frequency"] == "never":
        ops = [o for o in ops if o.op != "transition"]
    elif tr["default"] != "none" and not any(o.op == "transition" for o in ops):
        ops.append(Op(op="transition", type=tr["default"], duration=tr["duration"]))
    if d["jumpcut_zoom"]:
        ops.append(Op(op="jumpcut_zoom", level=d["jumpcut_zoom"]))
    if d["music"]:
        m = next((o for o in plan.ops if o.op == "music" and o.file == d["music"]["file"]), None)
        ops.append(Op(op="music", file=d["music"]["file"], offset=d["music"]["offset"] if m is None or m.offset is None else m.offset,
                      volume=d["music"]["volume"], duck=d["music"]["duck"]))
    if d["captions"]:
        ops.append(Op(op="captions", type=d["captions"]["style"], words=d["captions"]["words"], y=d["captions"]["y"]))
    plan.ops = ops
    plan.beat_sync = d["beat_sync"]
    plan.grade = d.get("grade")
    plan.title = plan.title or c.angle[:60]
    return plan


def review(sheet, layers: list[dict], dna: StyleDNA, quality: str) -> tuple[Review, dict]:
    prompt = (f"LAYERS in the edit:\n{json.dumps(layers, indent=1)}\n\nSTYLE DNA summary: {dna.summary}\n"
              f"Text style expected: {dna.text.model_dump_json()}\nCanvas safe zone: x 8-88%, y 12-75%.")
    return ai.call("critic", prompt, Review, system=prompts.CRITIC, images=[sheet], quality=quality)


# ---- near-duplicate check -------------------------------------------------------------------

def fingerprint(plan: EditPlan, axes: dict) -> list:
    clips = [o for o in plan.ops if o.op == "clip"]
    first = [(o.file, round(o.start or 0)) for o in clips[:3]]
    text = next((o.text for o in plan.ops if o.op == "text"), "")
    trans = sorted({o.type or "" for o in plan.ops if o.op == "transition"})
    music = next((round((o.offset or 0) / 5) for o in plan.ops if o.op == "music"), None)
    caps = next((o.type for o in plan.ops if o.op == "captions"), None)
    return [axes.get("hook"), axes.get("sound"), str(first), (text or "").lower().strip(), str(trans), music, caps, len(clips)]


def similarity(a: list, b: list) -> float:
    return sum(1 for x, y in zip(a, b) if x == y) / max(1, len(a))

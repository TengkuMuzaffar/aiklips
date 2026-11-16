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
from ..ops import moves as moves_lib
from . import taste
from .spec import AXES, STRUCTURE_HINT, Brief, BatchSpec, ConceptSet, Creative, CreativePlan, EditPlan, Op, Ranking

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
                        "shots": [_shot_line(s) for s in f["segments"]][:60]}
        else:
            out[fid] = {k: v for k, v in f.items() if k != "segments"}
    return out


def _shot_line(s: dict) -> str:
    """One footage segment for the prompt: id, range, quality, what it is and what it is good for."""
    extra = []
    if s.get("shot"):
        extra.append(s["shot"])
    if s.get("good_for"):
        extra.append("good for " + "/".join(s["good_for"]))
    m = s.get("measured") or {}
    if m:
        extra.append("static" if m.get("motion", 0) < 0.01 else "fast motion" if m.get("motion", 0) > 0.06 else "moving")
        if "voice" in m:
            from ..generate.footage import _audio_text

            extra.append(_audio_text(m))
    tail = f" [{', '.join(extra)}]" if extra else ""
    return f"{s['id']} ({s['from']:.1f}-{s['to']:.1f}s, q{s.get('quality', 3)}): {s.get('description', '')}{tail}"


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
        + (f"\n\n{taste.examples()}" if taste.examples() else "")
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


# ---- concepts: explore, then pick ----------------------------------------------------------

def _moves_text(dna: StyleDNA) -> str:
    seen = [f"{m.move}{' (confirmed in the reference)' if m.confirmed else ''}: {m.note}" for m in dna.signature_moves]
    lib = "\n".join(f"- {k} [{v['needs']}]: {v['about']}" for k, v in ((m["name"], m) for m in moves_lib.catalog()))
    return ("REFERENCE'S SIGNATURE MOVES:\n" + "\n".join(f"- {x}" for x in seen) + "\n" if seen else "") + f"MOVE LIBRARY:\n{lib}"


def concepts(spec: BatchSpec, dna: StyleDNA, log: dict, c: Creative, axes: dict) -> tuple[ConceptSet, dict]:
    """Three clearly different concepts for one variation."""
    hint = STRUCTURE_HINT.get(axes.get("structure", "reference"), "")
    prompt = (f"STYLE: {dna.summary}\nBRIEF:\n{spec.brief.model_dump_json(indent=1)}\n\n"
              f"FOOTAGE:\n{json.dumps(_footage_brief(log), indent=1)}\n\n"
              f"THIS VARIATION (#{c.index}): angle: {c.angle}\nhook line: {c.hook_text}\nCTA: {c.cta_text or '(none)'}\n"
              f"axes: {json.dumps({k: v for k, v in axes.items() if k != 'sound'})}\n" + (f"structure: {hint}\n" if hint else "")
              + f"\n{_moves_text(dna)}\n" + (f"\n{taste.examples()}\n" if taste.examples() else "")
              + "\nWrite 3 concepts for this variation.")
    return ai.call("concepts", prompt, ConceptSet, system=prompts.CONCEPTS, quality=spec.quality)


def rank(spec: BatchSpec, creatives: list[Creative], sets: list[ConceptSet]) -> tuple[Ranking, dict]:
    """Pick one concept per variation, judging all siblings together so the picks stay distinct."""
    lines = []
    for c, cs in zip(creatives, sets):
        lines.append(f"VARIATION {c.index} (angle: {c.angle}; hook: {c.hook_text})")
        for j, k in enumerate(cs.concepts, 1):
            lines.append(f"  {j}. {k.title} | arc: {k.arc} | motif: {k.motif} | move: {k.signature_move} | opening: {k.opening} | {k.why}")
    prompt = (f"BRIEF:\n{spec.brief.model_dump_json(indent=1)}\n\n" + "\n".join(lines)
              + (f"\n\n{taste.examples()}" if taste.examples() else "") + "\n\nPick one concept per variation.")
    return ai.call("rank", prompt, Ranking, system=prompts.RANK, quality=spec.quality)


def explore(spec: BatchSpec, dna: StyleDNA, log: dict, creatives: list[Creative], axes: list[dict],
            usage_cb=None) -> list[Creative]:
    """Concepts for every variation, then one ranking call. Failures fall back to no concept."""
    sets = []
    for c, a in zip(creatives, axes):
        try:
            cs, u = concepts(spec, dna, log, c, a)
            if usage_cb:
                usage_cb("concepts", u)
        except ai.RateLimited:
            raise
        except ai.AIError:
            cs = ConceptSet(concepts=[])
        sets.append(cs)
    if not any(cs.concepts for cs in sets):
        return creatives
    try:
        r, u = rank(spec, creatives, sets)
        if usage_cb:
            usage_cb("rank", u)
        picks = {pk.variation: pk for pk in r.picks}
    except ai.RateLimited:
        raise
    except ai.AIError:
        picks = {}
    for c, cs in zip(creatives, sets):
        if not cs.concepts:
            continue
        pk = picks.get(c.index)
        i = (pk.concept - 1) if pk and 1 <= pk.concept <= len(cs.concepts) else 0
        c.concept = cs.concepts[i]
        c.concept_reason = pk.reason if pk else ""
    return creatives


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
    structure = axes.get("structure", "reference")
    return {
        "structure": STRUCTURE_HINT.get(structure, "follow the reference's structure"),
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
                                         "volume": 0.45 if (dna.sound.voiceover or talking) else 0.8},  # ducked under speech by sidechain
        "beat_sync": bool(music and dna.sound.cut_on_beat),
    }


def _edit_prompt(spec: BatchSpec, dna: StyleDNA, log: dict, c: Creative, axes: dict, d: dict, avoid: list[str] | None = None) -> str:
    images = [fid for fid, f in log.items() if f["kind"] == "image"]
    return (
        f"STYLE DNA:\n{json.dumps(_style_brief(dna), indent=1)}\n\n"
        f"BRIEF:\n{spec.brief.model_dump_json(indent=1)}\n\n"
        f"FOOTAGE (use these file ids; segment ids show good ranges):\n{json.dumps(_footage_brief(log), indent=1)}\n\n"
        f"THIS VARIATION (#{c.index}): angle: {c.angle}\nhook line: {c.hook_text}\nCTA: {c.cta_text or '(none)'}\n"
        f"structure: {' → '.join(c.structure) or 'hook → content → cta'}\nnotes: {c.notes}\n"
        f"axes: {json.dumps(axes)}\n"
        + (f"\nCONCEPT TO BUILD: {c.concept.title}\n  arc: {c.concept.arc}\n  motif: {c.concept.motif}\n  opening: {c.concept.opening}\n"
           f"  signature move: {c.concept.signature_move or 'none'}\n  why: {c.concept.why}\n" if c.concept else "")
        + f"\n{_moves_text(dna)}\n"
        + f"\nDIRECTIVES (follow exactly):\n{json.dumps(d, indent=1)}\n"
        f"- About {d['clip_count']} clips of ~{d['avg_shot_s']}s each, total ≈ {d['length_s']}s. Order: {d['order']}\n"
        f"- Hook text '{c.hook_text}' from 0s to ~{d['hook_duration_s']}s; CTA text near the end if there is one.\n"
        + (f"- Images you may overlay: {', '.join(images)}\n" if images else "")
        + (f"- Add a music op with file {d['music']['file']} and the given offset (other sound files belong to sibling videos).\n" if d["music"] else "")
        + ("- Add one captions op.\n" if d["captions"] else "- Do not add captions.\n")
        + (f"\nIt must look different from sibling variations: {'; '.join(avoid)}\n" if avoid else "")
        + (f"\n{taste.examples()}\n" if taste.examples() else "")
    )


def plan_edit(spec: BatchSpec, dna: StyleDNA, log: dict, c: Creative, axes: dict, d: dict, avoid: list[str] | None = None) -> tuple[EditPlan, dict]:
    return _call_editor(_edit_prompt(spec, dna, log, c, axes, d, avoid), spec, log, d, c)


def revise_plan(spec: BatchSpec, dna: StyleDNA, log: dict, c: Creative, axes: dict, d: dict, plan: EditPlan, review: dict,
                threshold: int, attempts: list[dict] | None = None, best_round: int | None = None) -> tuple[EditPlan, dict]:
    """The editor gets the independent review of its best rendered edit so far, plus what earlier attempts scored,
    and returns a complete revised plan."""
    current = plan.model_dump(exclude={"grade", "problems"}, exclude_none=True, exclude_defaults=True)
    history = ""
    if attempts and len(attempts) > 1:
        lines = []
        for a in attempts:
            tag = "  <- the version below (best so far)" if a.get("round") == best_round else ""
            gist = (a.get("summary") or "; ".join(a.get("must_fix") or []))[:240]
            lines.append(f"- round {a.get('round')}: {a.get('score')}/100{tag}" + (f": {gist}" if gist else ""))
        history = ("\n\nEARLIER ATTEMPTS (every revision so far, with its audit). Revisions that scored lower than the best "
                   "made it worse: don't repeat what they changed; keep what the best version does well:\n" + "\n".join(lines))
    findings = [f for f in review.get("findings", []) if f["severity"] != "note"] or review.get("findings", [])
    prompt = (_edit_prompt(spec, dna, log, c, axes, d)
              + history
              + "\n\nYOUR BEST PLAN SO FAR (already built and rendered):\n" + json.dumps(current, separators=(",", ":"))[:12000]
              + f"\n\nINDEPENDENT REVIEW OF THE RENDERED VIDEO: score {review.get('score')}/100, needs {threshold}."
              + (f"\nReviewer: {review['summary']}" if review.get("summary") else "")
              + ("\nScores: " + ", ".join(f"{k} {v}/5" for k, v in review["scores"].items()) if review.get("scores") else "")
              + "\nFindings (times are timeline seconds of the rendered video):\n"
              + "\n".join(f"- {f['id']} [{f['severity']}] {f['category']}" + (f" @{f['at']}s" if f.get("at") is not None else "")
                          + f": {f['message']}" + (f" -> {f['fix']}" if f.get("fix") else "") for f in findings)
              + "\n\nRevise the plan to fix every must-fix and as many should-fix findings as you can, and raise the weakest "
                "scores. Keep what works: change only what the findings point at, so you don't lose what already scores. "
                "You may swap segments, reorder, retime, rewrite text, change clip audio modes, music offset/volume, "
                "transitions and moves. Return the COMPLETE revised plan.")
    return _call_editor(prompt, spec, log, d, c)


def _call_editor(prompt: str, spec: BatchSpec, log: dict, d: dict, c: Creative) -> tuple[EditPlan, dict]:
    plan, usage = ai.call("editor", prompt, EditPlan, system=prompts.EDITOR, quality=spec.quality)
    fill_ranges(plan, log)
    errors = validate_plan(plan, log)
    if errors:  # one corrective round, with the exact problems
        retry = prompt + "\n\nYOUR PREVIOUS PLAN HAD THESE PROBLEMS, fix all of them:\n- " + "\n- ".join(errors[:20])
        plan2, u2 = ai.call("editor", retry, EditPlan, system=prompts.EDITOR, quality=spec.quality)
        usage = _sum_usage(usage, u2)
        fill_ranges(plan2, log)
        errors2 = validate_plan(plan2, log)
        if len(errors2) <= len(errors):
            plan, errors = plan2, errors2
    plan.problems = errors
    plan = enforce(plan, d, c)
    audio_defaults(plan, log)
    return plan, usage


def _sum_usage(a: dict, b: dict) -> dict:
    out = dict(a or {})
    for k, v in (b or {}).items():
        out[k] = out.get(k, 0) + v if isinstance(v, (int, float)) and isinstance(out.get(k, 0), (int, float)) else v
    return out


SEG_TOLERANCE = 0.25


def fill_ranges(plan: EditPlan, log: dict) -> None:
    """A clip that names a segment but no range gets the segment's range (and its file)."""
    segs = {s["id"]: (fid, s) for fid, f in log.items() if f.get("kind") == "video" for s in f.get("segments", [])}
    for o in plan.ops:
        if o.op == "clip" and o.segment in segs:
            fid, sg = segs[o.segment]
            o.file = o.file or fid
            if o.start is None:
                o.start = sg["from"]
            if o.end is None:
                o.end = sg["to"]


def audio_defaults(plan: EditPlan, log: dict) -> None:
    """Clips the editor gave no audio mode: speech (measured, or the log's talking flag) keeps its sound;
    anything else is muted when there is music (so the music carries it) and kept quietly when there isn't."""
    from ..sound import SPEECH

    segs = {s["id"]: s for f in log.values() if f.get("kind") == "video" for s in f.get("segments", [])}
    music = any(o.op == "music" for o in plan.ops)
    for o in plan.ops:
        if o.op != "clip" or o.audio:
            continue
        sg = segs.get(o.segment or "") or {}
        m = sg.get("measured") or {}
        if sg.get("talking") or m.get("voice", 0) >= SPEECH:
            o.audio = "voice"
        else:
            o.audio = "mute" if music else "ambient"
    if any(o.op == "clip" and o.audio == "voice" for o in plan.ops):
        for o in plan.ops:  # someone talks: the music must duck under them, and can sit higher between lines
            if o.op == "music":
                o.duck = True
                o.volume = min(o.volume if o.volume is not None else 0.45, 0.45)


def validate_plan(plan: EditPlan, log: dict) -> list[str]:
    """Concrete problems with a plan's structure and justification (code, not AI)."""
    errors: list[str] = []
    segs = {s["id"]: (fid, s) for fid, f in log.items() if f.get("kind") == "video" for s in f.get("segments", [])}
    beat_ids = [b.id for b in plan.beats]
    if not plan.beats:
        errors.append("no beats: give the edit a beat sheet (first beat = hook)")
    elif plan.beats[0].role != "hook":
        errors.append(f"the first beat ({plan.beats[0].id}) must have role hook")
    used_beats = set()
    clips = [o for o in plan.ops if o.op == "clip"]
    if not clips:
        errors.append("no clip ops")
    for i, o in enumerate(clips, 1):
        label = f"clip {i} ({o.file} {o.start}-{o.end}s)"
        if not (o.why or "").strip():
            errors.append(f"{label}: missing why")
        if beat_ids:
            if o.beat not in beat_ids:
                errors.append(f"{label}: beat {o.beat!r} is not one of {', '.join(beat_ids)}")
            else:
                used_beats.add(o.beat)
        if not o.segment:
            errors.append(f"{label}: missing segment id")
            continue
        if o.segment not in segs:
            errors.append(f"{label}: unknown segment {o.segment}")
            continue
        fid, sg = segs[o.segment]
        if o.file and o.file != fid:
            errors.append(f"{label}: segment {o.segment} belongs to file {fid}, not {o.file}")
        a, b = o.start if o.start is not None else sg["from"], o.end if o.end is not None else sg["to"]
        if a < sg["from"] - SEG_TOLERANCE or b > sg["to"] + SEG_TOLERANCE:
            errors.append(f"{label}: {a}-{b}s is outside segment {o.segment} ({sg['from']}-{sg['to']}s)")
    for b in plan.beats:
        if b.id not in used_beats and clips and beat_ids:
            errors.append(f"beat {b.id} ({b.role}) has no clips")
    mv = [o for o in plan.ops if o.op == "move"]
    if len(mv) > 2:
        errors.append(f"{len(mv)} moves: use at most 2")
    for o in mv:
        needs = moves_lib.MOVES.get(o.type or "", (None,))[0]
        if needs is None:
            errors.append(f"move {o.type!r} is not in the move library")
        elif needs == "clip" and o.into is None and o.start is None:
            errors.append(f"move {o.type} needs `into` (clip index)")
        elif needs.startswith("at") and o.start is None and o.into is None:
            errors.append(f"move {o.type} needs `start` (timeline seconds)")
        if needs and "+text" in needs and not (o.text or "").strip():
            errors.append(f"move {o.type} needs `text`")
    return errors


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

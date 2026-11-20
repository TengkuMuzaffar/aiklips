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
from ..ops.motion import SPLIT_ANIMS
from ..ops.color import LOOKS as COLOR_LOOKS
from ..references.dna import StyleDNA
from ..ops import moves as moves_lib
from . import commands, context, executor, taste
from .spec import AXES, STRUCTURE_HINT, Brief, BatchSpec, ConceptSet, Creative, CreativePlan, EditPlan, Op, PlanPatch, Ranking

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
    if s.get("marks"):
        from .footage import marks_text

        extra.append(marks_text(s["marks"]))
    tail = f" [{', '.join(x for x in extra if x)}]" if extra else ""
    return f"{s['id']} ({s['from']:.1f}-{s['to']:.1f}s, q{s.get('quality', 3)}): {s.get('description', '')}{tail}"


def _kind_note(dna: StyleDNA) -> str:
    if not is_motion(dna):
        return ""
    return ("\n\nTHE REFERENCE IS MOTION DESIGN (graphics and kinetic type on flat colour, not a cut of filmed footage). "
            "Plan each variation as motion design built around the user's footage: graphic scenes (a canvas in the "
            "reference's colours with a short line in kinetic type), colour wipes and an end card, with the footage "
            "between them as the proof. Name in the structure which parts are scenes and which are footage.\n")


def creative(spec: BatchSpec, dna: StyleDNA, log: dict, axes: list[dict]) -> tuple[list[Creative], dict]:
    b = spec.brief
    prompt = (
        f"STYLE DNA (merged references):\n{json.dumps(_style_brief(dna), indent=1)}\n\n"
        f"BRIEF:\n{b.model_dump_json(indent=1)}\n"
        + (f"The user wrote this hook line; variation 1 must use it verbatim, others may rephrase it for their hook type: {b.hook!r}\n" if b.hook else "")
        + f"\nFOOTAGE:\n{context.footage_block(log, b.length_s, context.CREATIVE_LINES)}\n\n"
        f"VARIATIONS ({spec.count}), with their fixed axes:\n"
        + "\n".join(f"{i + 1}. " + ", ".join(f"{k}={v}" for k, v in a.items()) for i, a in enumerate(axes))
        + _kind_note(dna)
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
              f"FOOTAGE:\n{context.footage_block(log, spec.brief.length_s, 60, c.structure)}\n\n"
              f"THIS VARIATION (#{c.index}): angle: {c.angle}\nhook line: {c.hook_text}\nCTA: {c.cta_text or '(none)'}\n"
              f"axes: {json.dumps({k: v for k, v in axes.items() if k != 'sound'})}\n" + (f"structure: {hint}\n" if hint else "")
              + f"\n{_moves_text(dna)}\n" + _kind_note(dna) + (f"\n{taste.examples()}\n" if taste.examples() else "")
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


MOTION_WORDS = ("motion-graphic", "motion graphic", "motion-design", "motion design", "kinetic", "explainer", "animated graphic")


def is_motion(dna: StyleDNA) -> bool:
    """The reference is motion design (graphics, kinetic type, flat canvases), not a cut of filmed footage."""
    if dna.design is not None:  # studied with the design pass: it says so directly
        return dna.design.kind in ("motion_graphics", "hybrid")
    text = " ".join([dna.summary, dna.notes, dna.color_feel, *dna.tags]).lower()
    return any(w in text for w in MOTION_WORDS) or float((dna.color.palette or {}).get("bg_share") or 0) >= 0.45


def _grade(dna: StyleDNA, axes: dict[str, str]) -> dict:
    axis = axes.get("color_look", "reference")
    if is_motion(dna):
        # the reference's colour is its canvas (white, mint...), not a grade: matching footage to it washes the
        # footage out. Match the clips to each other, keep them natural; the palette goes to the graphics instead.
        look = "natural" if axis == "reference" else axis
        return {"look": look if look in COLOR_LOOKS else "natural", "intensity": 0.6, "target": None, "strength": 0.6}
    look = dna.color.look if axis == "reference" else axis
    return {"look": look if look in COLOR_LOOKS else "natural", "intensity": dna.color.intensity if axis == "reference" else 0.8,
            "target": dict(dna.color.measured) if dna.color.measured.get("luma") is not None else None,
            "strength": 0.7}


def _light(hexc: str | None) -> bool:
    h = (hexc or "#000000").lstrip("#")[:6].ljust(6, "0")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.55


SPLIT_FROM_PRESET = {"pop_in": "pop", "slide_in": "slide", "drop_in": "drop", "typewriter": "type", "fade_in": "fade",
                     "rise_in": "rise", "zoom_in": "pop", "punch": "pop", "wipe_in": "mask_up", "grow_in": "pop"}


def motion_design(dna: StyleDNA, text_in: list[str]) -> dict:
    """The graphic language of a motion-design reference, as numbers: its palette and its kinetic type."""
    pal = dna.color.palette or {}
    canvas = pal.get("background") or ("#FFFFFF" if "white" in dna.color_feel.lower() else "#111111")
    ink = pal.get("ink") or (dna.text.color if _light(canvas) != _light(dna.text.color) else ("#111111" if _light(canvas) else "#FFFFFF"))
    accents = list(pal.get("accents") or [])
    if not accents and dna.text.color and _light(dna.text.color) == _light(canvas):
        accents = [dna.text.color]
    anim = next((SPLIT_FROM_PRESET[p] for p in text_in if p in SPLIT_FROM_PRESET), "pop")
    return {"canvas": canvas, "surface": pal.get("surface"), "ink": ink, "accents": accents or ["#FF3D57"],
            "kinetic": anim, "scenes": "2-4 scenes, about a third of the running time"}


def music_style(dna: StyleDNA) -> dict:
    """A built-in music loop that fits the reference, for when the user gave no song."""
    text = " ".join([dna.summary, dna.notes, dna.color_feel, *dna.tags]).lower()
    energy = dna.sound.music_energy
    rules = [(("cinematic", "epic", "trailer"), "cinematic"), (("lofi", "lo-fi", "chill", "cozy", "calm"), "lofi"),
             (("synthwave", "retro", "80s", "neon"), "synthwave"), (("pixel", "8-bit", "chiptune", "game"), "chiptune"),
             (("trap",), "trap"), (("hip hop", "hiphop", "street"), "hiphop"), (("ambient", "meditat"), "ambient")]
    style = next((st for words, st in rules if any(w in text for w in words)), None)
    bpm = dna.sound.bpm if float(dna.confidence.get("tempo") or 0) >= 0.4 else None
    if style is None:
        style = "house" if energy == "high" or (bpm or 0) >= 118 else "pop"
    mood = {"high": "energetic", "low": "chill"}.get(energy, "bright")
    out = {"style": style, "mood": mood}
    if bpm:
        out["bpm"] = int(min(160, max(70, round(bpm))))
    return out


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
    md = motion_design(dna, text_in) if is_motion(dna) else None
    text_color, text_bg = dna.text.color, (dna.text.bg_color or "#000000B0") if dna.text.bg_box else None
    if md and not text_bg:  # the reference's titles sit on its canvas; over footage they get that canvas as a card
        text_bg, text_color = md["canvas"] + "E6", md["ink"]
    make = None if music else music_style(dna)
    talking_vol = 0.22 if (dna.sound.voiceover or talking) else 0.5
    return {
        "structure": STRUCTURE_HINT.get(structure, "follow the reference's structure"),
        "length_s": brief.length_s,
        "avg_shot_s": round(avg, 2),
        "clip_count": max(1, round(brief.length_s * (0.65 if md else 1.0) / avg)),  # scenes take about a third
        "order": ORDER_HINT[axes["order"]],
        "hook_type": dna.hook.pattern if axes["hook"] == "reference" else axes["hook"],
        "hook_duration_s": dna.hook.duration_s,
        "text": {"y": dna.text.position_y, "size": TEXT_SIZE[dna.text.size], "color": text_color, "uppercase": dna.text.uppercase,
                 "stroke": dna.text.stroke, "bg": text_bg,
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
        "motion_design": md,
        "music_make": None if make is None else {**make, "volume": talking_vol},
    }


def _edit_prompt(spec: BatchSpec, dna: StyleDNA, log: dict, c: Creative, axes: dict, d: dict, avoid: list[str] | None = None) -> str:
    images = [fid for fid, f in log.items() if f["kind"] == "image"]
    return (
        f"STYLE DNA:\n{json.dumps(_style_brief(dna), indent=1)}\n\n"
        f"BRIEF:\n{spec.brief.model_dump_json(indent=1)}\n\n"
        f"FOOTAGE (use these file ids; segment ids show good ranges):\n"
        f"{context.footage_block(log, spec.brief.length_s, context.PLAN_LINES, c.structure)}\n\n"
        f"THIS VARIATION (#{c.index}): angle: {c.angle}\nhook line: {c.hook_text}\nCTA: {c.cta_text or '(none)'}\n"
        f"structure: {' → '.join(c.structure) or 'hook → content → cta'}\nnotes: {c.notes}\n"
        f"axes: {json.dumps(axes)}\n"
        + (f"\nCONCEPT TO BUILD: {c.concept.title}\n  arc: {c.concept.arc}\n  motif: {c.concept.motif}\n  opening: {c.concept.opening}\n"
           f"  signature move: {c.concept.signature_move or 'none'}\n  why: {c.concept.why}\n" if c.concept else "")
        + f"\n{_moves_text(dna)}\n"
        + f"\nVEDIT COMMANDS (for `vedit` ops; the same commands the editor's chat AI uses):\n{commands.reference()}\n"
        + f"\nDIRECTIVES (follow exactly):\n{json.dumps(d, indent=1)}\n"
        f"- About {d['clip_count']} clips of ~{d['avg_shot_s']}s each, total ≈ {d['length_s']}s. Order: {d['order']}\n"
        f"- Hook text '{c.hook_text}' from 0s to ~{d['hook_duration_s']}s; CTA text near the end if there is one.\n"
        + (f"- Images you may overlay: {', '.join(images)}\n" if images else "")
        + (f"- Add a music op with file {d['music']['file']} and the given offset (other sound files belong to sibling videos).\n" if d["music"] else "")
        + (_music_make_line(d["music_make"]) if d.get("music_make") else "")
        + (_motion_block(d["motion_design"], c) if d.get("motion_design") else "")
        + ("- Add one captions op.\n" if d["captions"] else "- Do not add captions.\n")
        + (f"\nIt must look different from sibling variations: {'; '.join(avoid)}\n" if avoid else "")
        + (f"\n{taste.examples()}\n" if taste.examples() else "")
    )


def _music_make_line(m: dict) -> str:
    return (f"- No song was given: add ONE vedit op `music make {m['style']} --mood {m['mood']}"
            + (f" --bpm {m['bpm']}" if m.get("bpm") else "") + f" --volume {m['volume']}` (or another style / mood that "
            "fits the concept better: lofi, hiphop, trap, house, synthwave, chiptune, pop, ambient, cinematic). "
            "Never leave the video silent.\n")


def _motion_block(md: dict, c: Creative) -> str:
    acc = ", ".join(md["accents"])
    return (
        "\nMOTION DESIGN: the reference is a motion-design video (graphics and kinetic type on flat colour), not a cut "
        "of filmed footage. Rebuild ITS graphic language around the user's footage; don't just cut the footage:\n"
        f"- Palette: canvas {md['canvas']}" + (f", panels {md['surface']}" if md.get("surface") else "")
        + f", ink (text) {md['ink']}, accents {acc}. Use these for scenes, cards, shapes and highlight words. "
        "Never colour-grade the footage toward the canvas: the footage keeps its own colour.\n"
        f"- Use {md['scenes']}: `scene` ops (a full-screen canvas with a short line in kinetic type, word by word, "
        f"anim {md['kinetic']}). Typical: open on a scene that asks the hook, cut to footage that answers it, a "
        "colour wipe scene (accent fill, no text, 0.6-0.9 s) between parts, and an end-card scene with the CTA.\n"
        "- Scene lines are short (2-6 words). Put footage between scenes so the product is the hero.\n"
        "- Make the graphics move like the reference: vedit commands for an accent shape that slides or grows in "
        "behind a word (shape add ... --start @S1.start), highlight one word in an accent colour, a counter or "
        "chart if the brief has a number, a whoosh / pop on scene changes. @S1 = the first scene's canvas, "
        "@S1_text = its kinetic line.\n"
        "- Titles over footage sit on a card in the canvas colour (the directives already set it).\n"
        + (f"- End card: '{c.cta_text}' on the canvas or an accent.\n" if c.cta_text else ""))


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
                "transitions, moves and vedit commands. Return the COMPLETE revised plan.")
    return _call_editor(prompt, spec, log, d, c)


# ---- targeted revisions: a small patch for the top open issues, everything else locked --------------------

MAX_PATCH_OPS = 6


def plan_listing(plan: EditPlan) -> str:
    """The plan as numbered lines the editor can point at: C1..Cn clips, T1..Tn titles."""
    beats = {b.id: b for b in plan.beats}
    lines = ["BEATS: " + " | ".join(f"{b.id} {b.role}: {b.purpose}" for b in plan.beats)] if plan.beats else []
    clips = [o for o in plan.ops if o.op == "clip"]
    for i, o in enumerate(clips, 1):
        b = beats.get(o.beat or "")
        lines.append(f"C{i}: {o.segment or o.file} {o.start}-{o.end}s" + (f" speed {o.speed}" if o.speed and o.speed != 1 else "")
                     + f", beat {o.beat}" + (f" ({b.role})" if b else "") + f", audio {o.audio or '?'}" + f'  WHY: "{o.why or ""}"')
    for i, o in enumerate([o for o in plan.ops if o.op == "text"], 1):
        lines.append(f"T{i}: \"{o.text}\" {o.start}-{o.end}s at y {o.y}" + (f", size {o.size}" if o.size else "") + f'  WHY: "{o.why or ""}"')
    for o in plan.ops:
        if o.op == "music":
            lines.append(f"MUSIC: {o.file} offset {o.offset}s, volume {o.volume}, ducked {o.duck}")
        elif o.op == "move":
            lines.append(f"MOVE: {o.type}" + (f" on C{o.into + 1}" if o.into is not None else f" at {o.start}s"))
    for i, o in enumerate([o for o in plan.ops if o.op == "scene"], 1):
        pos = [x for x in plan.ops if x.op in ("clip", "scene")].index(o) + 1
        lines.append(f"S{i}: scene #{pos} of the sequence, {o.duration}s on {o.fill}: \"{o.text or ''}\"" + f'  WHY: "{o.why or ""}"')
    for i, o in enumerate([o for o in plan.ops if o.op == "vedit"], 1):
        lines.append(f"V{i}: vedit {o.command}" + (f"  (@{o.name})" if o.name else ""))
    return "\n".join(lines)


def apply_patch(plan: EditPlan, patch: PlanPatch, log: dict) -> tuple[EditPlan, list[str]]:
    """Apply a patch to a copy of the plan. Returns (new plan, problems). Unknown targets are problems, not crashes."""
    new = plan.model_copy(deep=True)
    segs = {s["id"]: (fid, s) for fid, f in log.items() if f.get("kind") == "video" for s in f.get("segments", [])}
    problems: list[str] = []

    def clips():
        return [o for o in new.ops if o.op == "clip"]

    def texts():
        return [o for o in new.ops if o.op == "text"]

    def cmds():
        return [o for o in new.ops if o.op == "vedit"]

    def scenes():
        return [o for o in new.ops if o.op == "scene"]

    def seq():
        return [o for o in new.ops if o.op in ("clip", "scene")]

    def pick(items, target, prefix):
        t = (target or "").strip().upper()
        if not t.startswith(prefix) or not t[1:].isdigit() or not 1 <= int(t[1:]) <= len(items):
            raise ValueError(f"{target!r} is not one of {prefix}1..{prefix}{len(items)}")
        return items[int(t[1:]) - 1]

    def seg_range(o: Op, segment: str, start, end):
        if segment not in segs:
            raise ValueError(f"unknown segment {segment}")
        fid, sg = segs[segment]
        o.file, o.segment = fid, segment
        o.start = start if start is not None and sg["from"] - 0.25 <= start < sg["to"] else sg["from"]
        o.end = end if end is not None and o.start < end <= sg["to"] + 0.25 else sg["to"]

    for x in patch.ops[:MAX_PATCH_OPS]:
        try:
            if x.op == "replace_clip":
                o = pick(clips(), x.target, "C")
                seg_range(o, x.segment or "", x.start, x.end)
                o.why = x.why or o.why
                o.audio = x.audio  # the new footage's own sound decides (audio_defaults) unless set here
                o.beat = x.beat or o.beat
            elif x.op == "trim_clip":
                o = pick(clips(), x.target, "C")
                o.start = x.start if x.start is not None else o.start
                o.end = x.end if x.end is not None else o.end
            elif x.op == "move_clip":
                o = pick(clips(), x.target, "C")
                sq = seq()
                sq.remove(o)
                cl = [y for y in sq if y.op == "clip"]
                to = max(1, x.to or 1)
                sq.insert(sq.index(cl[to - 1]) if to <= len(cl) else (sq.index(cl[-1]) + 1 if cl else len(sq)), o)
                new.ops = sq + [y for y in new.ops if y.op not in ("clip", "scene")]
            elif x.op == "remove_clip":
                new.ops.remove(pick(clips(), x.target, "C"))
            elif x.op == "insert_clip":
                o = Op(op="clip", beat=x.beat, why=x.why, audio=x.audio)
                seg_range(o, x.segment or "", x.start, x.end)
                sq = seq()
                cl = clips()
                at = sq.index(pick(cl, x.target, "C")) if x.target else (sq.index(cl[-1]) + 1 if cl else len(sq))
                sq.insert(at, o)
                new.ops = sq + [y for y in new.ops if y.op not in ("clip", "scene")]
            elif x.op == "set_audio":
                pick(clips(), x.target, "C").audio = x.audio
            elif x.op == "edit_text":
                o = pick(texts(), x.target, "T")
                for k in ("text", "start", "end", "y", "size"):
                    if getattr(x, k) is not None:
                        setattr(o, k, getattr(x, k))
            elif x.op == "add_text":
                new.ops.append(Op(op="text", text=x.text or "", start=x.start or 0.0, end=x.end, y=x.y, size=x.size, beat=x.beat, why=x.why))
            elif x.op == "remove_text":
                new.ops.remove(pick(texts(), x.target, "T"))
            elif x.op == "set_music":
                for o in new.ops:
                    if o.op == "music":
                        o.offset = x.offset if x.offset is not None else o.offset
                        o.volume = x.volume if x.volume is not None else o.volume
            elif x.op == "edit_scene":
                o = pick(scenes(), x.target, "S")
                for k in ("text", "duration", "fill"):
                    if getattr(x, k) is not None:
                        setattr(o, k, getattr(x, k))
            elif x.op == "remove_scene":
                new.ops.remove(pick(scenes(), x.target, "S"))
            elif x.op in ("add_command", "replace_command"):
                commands.parse(x.command or "", log)
                if x.op == "add_command":
                    new.ops.append(Op(op="vedit", command=x.command, name=x.name))
                else:
                    o = pick(cmds(), x.target, "V")
                    o.command, o.name = x.command, x.name or o.name
            elif x.op == "remove_command":
                new.ops.remove(pick(cmds(), x.target, "V"))
            elif x.op == "set_move":
                new.ops = [o for o in new.ops if o.op != "move"]
                if x.type and x.type != "none":
                    new.ops.append(Op(op="move", type=x.type, into=(x.into - 1) if x.into else None, start=x.start, text=x.text))
        except ValueError as e:
            problems.append(f"{x.op} {x.target or ''}: {e}")
    if len(patch.ops) > MAX_PATCH_OPS:
        problems.append(f"only the first {MAX_PATCH_OPS} changes were used")
    return new, problems


def patch_plan(spec: BatchSpec, dna: StyleDNA, log: dict, c: Creative, axes: dict, d: dict, plan: EditPlan,
               issues: list[dict], review: dict, threshold: int, attempts: list[dict] | None = None) -> tuple[EditPlan | None, dict, dict]:
    """Ask for a small patch that fixes the top open issues. Returns (patched plan or None, usage, info)."""
    targets = issues[:3]
    hist = ""
    if attempts and len(attempts) > 1:
        hist = "\nEARLIER ROUNDS: " + "; ".join(f"r{a.get('round')} {a.get('score')}" + (f" ({a['verdict']})" if a.get("verdict") else "")
                                             + (f" fixed {', '.join(a['fixed'])}" if a.get("fixed") else "") for a in attempts)
    prompt = (f"BRIEF:\n{spec.brief.model_dump_json(indent=1)}\nSTYLE: {dna.summary}\n"
              f"\nFOOTAGE SEGMENTS (id, range in file, quality, description, reviewer marks):\n"
              + "\n".join(_shot_line(sg) for f in log.values() if f.get("kind") == "video" for sg in f.get("segments", []))[:9000]
              + f"\n\nTHE CURRENT EDIT (the best version so far, audit {review.get('score')}/100, pass mark {threshold}):\n{plan_listing(plan)}"
              + hist
              + "\n\nFIX THESE ISSUES THIS ROUND (and only these):\n"
              + "\n".join(f"- {i['id']} [{i['severity']}] {i['category']}" + (f" @{i['at']}s" if i.get("at") is not None else "")
                          + f": {i['message']}" + (f" -> {i['fix']}" if i.get("fix") else "") for i in targets)
              + (f"\n(Other open issues, for context only: " + "; ".join(f"{i['id']} {i['message'][:80]}" for i in issues[3:8]) + ")" if len(issues) > 3 else "")
              + "\n\nReturn a PATCH: the smallest set of changes (at most 6) that fixes those issues. Everything you don't "
                "mention stays exactly as it is, so don't touch what already works. Prefer segments the reviewer marked good "
                "for that role; never use a segment marked weak for that role.")
    patch, usage = ai.call("editor", prompt, PlanPatch, system=prompts.PATCHER, quality=spec.quality)
    if not patch.ops:
        return None, usage, {"note": "empty patch"}
    new, problems = apply_patch(plan, patch, log)
    fill_ranges(new, log)
    errors = [e for e in validate_plan(new, log) if e not in validate_plan(plan, log)]  # only problems this patch created
    if errors or len(problems) == len(patch.ops):
        return None, usage, {"note": "patch rejected", "problems": problems + errors}
    new = enforce(new, d, c)
    audio_defaults(new, log)
    new.problems = problems
    return new, usage, {"fixes": patch.fixes, "note": patch.note, "ops": [x.model_dump(exclude_none=True) for x in patch.ops[:MAX_PATCH_OPS]],
                        "problems": problems}


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
    music = any(o.op == "music" or (o.op == "vedit" and _is_music_make(o.command)) for o in plan.ops)
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
    for o in plan.ops:
        if o.op == "scene":
            if o.beat in beat_ids:
                used_beats.add(o.beat)
            if o.duration is not None and not 0.4 <= o.duration <= 8:
                errors.append(f"scene '{(o.text or '')[:30]}': duration {o.duration}s, use 0.6-6 s")
    for b in plan.beats:
        if b.id not in used_beats and clips and beat_ids:
            errors.append(f"beat {b.id} ({b.role}) has no clips")
    mv = [o for o in plan.ops if o.op == "move"]
    cap = executor.max_moves(plan)
    if len(mv) > cap:
        errors.append(f"{len(mv)} moves: use at most {cap} (more signature effects go in as `fx apply` vedit commands)")
    for i, o in enumerate([o for o in plan.ops if o.op == "vedit"], 1):
        try:
            commands.parse(o.command or "", log)
        except ValueError as e:
            errors.append(f"vedit op {i} ({(o.command or '')[:60]!r}): {e}")
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


def _is_music_make(command: str | None) -> bool:
    words = (command or "").split()
    words = words[1:] if words[:1] in (["vedit"], ["aiklips"]) else words
    return words[:2] == ["music", "make"]


def enforce(plan: EditPlan, d: dict, c: Creative) -> EditPlan:
    """Fill in the style directives where the model left a choice open, and keep the variation's axes (font, music,
    captions, transitions). The model's own explicit choices (a title's colour, size, position, animation, spacing,
    a second font for emphasis, jump-cut zoom level, music level) stand: directives are defaults, not overrides."""
    ops = [o for o in plan.ops if o.op not in ("music", "captions", "jumpcut_zoom")]
    if not any(o.op == "text" for o in ops) and c.hook_text:
        ops.append(Op(op="text", text=c.hook_text, start=0.0, end=d["hook_duration_s"]))
    t = d["text"]
    second: str | None = None  # at most two fonts: the variation's, plus one the model picks for emphasis
    for o in ops:
        if o.op == "text":
            if not o.font or o.font not in fonts.FONTS or o.font == t.get("font"):
                o.font = t.get("font")
            elif second in (None, o.font):
                second = o.font
            else:
                o.font = t.get("font")
            if o.letter_spacing is None:
                o.letter_spacing = t.get("letter_spacing") or None
            o.size = o.size or t["size"]
            o.color = o.color or t["color"]
            o.uppercase = t["uppercase"] if o.uppercase is None else o.uppercase
            o.bg = o.bg or t["bg"]
            o.y = o.y or t["y"]
            o.presets = [p for p in (o.presets or t["presets"]) if p in PRESETS] or t["presets"]
    md = d.get("motion_design")
    seq = [o for o in ops if o.op in ("clip", "scene")]
    if md and not any(o.op == "scene" for o in ops) and c.cta_text:  # the model ignored the scenes: at least the end card
        end_card = Op(op="scene", duration=2.0, text=c.cta_text, why="end card: the offer on the brand canvas",
                      beat=next((b.id for b in reversed(plan.beats) if b.role == "cta"), plan.beats[-1].id if plan.beats else None))
        at = ops.index(seq[-1]) + 1 if seq else len(ops)
        ops.insert(at, end_card)
    for o in ops:
        if o.op == "scene":
            canvas = (md or {}).get("canvas") or "#111111"
            o.fill = o.fill or canvas
            light = _light(o.fill.split(",")[0])
            o.color = o.color or ((md or {}).get("ink") if md and _light((md or {}).get("ink")) != light else ("#111111" if light else "#FFFFFF"))
            if not o.font or o.font not in fonts.FONTS or (o.font != t.get("font") and second not in (None, o.font)):
                o.font = t.get("font")
            elif o.font != t.get("font"):
                second = o.font
            o.size = o.size or round(t["size"] * 1.3)
            o.type = o.type if o.type in SPLIT_ANIMS else (md or {}).get("kinetic", "pop")
    tr = d["transitions"]
    if tr["frequency"] == "never":
        ops = [o for o in ops if o.op != "transition"]
    elif tr["default"] != "none" and not any(o.op == "transition" for o in ops):
        ops.append(Op(op="transition", type=tr["default"], duration=tr["duration"]))
    jz = next((o for o in plan.ops if o.op == "jumpcut_zoom" and o.level), None)
    if jz or d["jumpcut_zoom"]:
        ops.append(Op(op="jumpcut_zoom", level=jz.level if jz else d["jumpcut_zoom"]))
    if d["music"]:
        m = next((o for o in plan.ops if o.op == "music" and o.file == d["music"]["file"]), None)
        ops.append(Op(op="music", file=d["music"]["file"], offset=d["music"]["offset"] if m is None or m.offset is None else m.offset,
                      volume=d["music"]["volume"] if m is None or m.volume is None else max(0.05, min(1.0, m.volume)),
                      duck=d["music"]["duck"]))
    if d.get("music_make") and not any(o.op == "vedit" and _is_music_make(o.command) for o in ops):
        m = d["music_make"]
        cmd = f"music make {m['style']} --mood {m['mood']} --volume {m['volume']}" + (f" --bpm {m['bpm']}" if m.get("bpm") else "")
        first = next((i for i, o in enumerate(ops) if o.op == "vedit"), len(ops))
        ops.insert(first, Op(op="vedit", command=cmd))
    if d["captions"]:
        cp = next((o for o in plan.ops if o.op == "captions"), None)
        ops.append(Op(op="captions", type=d["captions"]["style"], words=(cp and cp.words) or d["captions"]["words"],
                      y=(cp and cp.y) or d["captions"]["y"]))
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

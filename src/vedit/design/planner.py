"""The AI side of Design: one planner call writes the whole MotionPlan; code validates and fits it; fix rounds patch it.

Code measures and times (length, beats, catalogs, ids); the AI judges and writes (scenes, motion, copy)."""

from __future__ import annotations

import json
import re

from ..ai import client as ai
from ..ai import prompts
from ..ops import animate as anim
from ..ops import fonts, overlays, text_looks
from .spec import FRAMES, MAX_LENGTH, STYLES, TRANSITIONS, DesignSpec, Element, MotionPatch, MotionPlan

_PCT = re.compile(r"^-?\d+(\.\d+)?%$")


def catalog() -> dict:
    return {
        "presets": {k: v[0] + ": " + v[3] for k, v in anim.PRESETS.items()},
        "looks": {x["name"]: x["about"] for x in text_looks.catalog()},
        "textures": {k: it.about for k, it in overlays.LIBRARY.items() if it.kind == "video"},
        "fonts": list(fonts.FONTS),
        "frames": list(FRAMES),
        "transitions": list(TRANSITIONS),
        "eases": list(anim.EASINGS),
    }


def _media_brief(log: dict, pieces: list[dict]) -> dict:
    from ..generate.planner import _shot_line

    out: dict = {}
    for fid, f in log.items():
        if f["kind"] == "video":
            out[fid] = {"kind": "video", "name": f["name"], "duration": f["duration"],
                        "shots": [_shot_line(s) for s in f.get("segments", [])][:40]}
        else:
            out[fid] = {k: v for k, v in f.items() if k != "segments"}
    for pc in pieces:
        out[pc["id"]] = {"kind": "image", "piece_of_sheet": pc.get("sheet"), "label": pc.get("label", ""),
                         "size": f"{pc['width']}x{pc['height']}", "cutout": pc.get("cutout", False)}
    return out


def _prompt(spec: DesignSpec, log: dict, pieces: list[dict], style_note: str, beats: list[float]) -> str:
    from ..library import FORMATS

    W, H = FORMATS.get(spec.format, FORMATS["reels"])
    parts = [
        f"Canvas {W}x{H}, target length {spec.length_s:g}s (max {MAX_LENGTH:g}s).",
        f"Style preset '{spec.style}': {STYLES[spec.style]}",
    ]
    if style_note:
        parts.append(f"Reference style to follow (measured from the user's reference videos):\n{style_note}")
    parts.append("The sheet (script / scene list) from the user:\n" + (spec.sheet_text.strip() or "(no script: write the scenes from the clips and images)"))
    parts.append("Files you may use (ids):\n" + json.dumps(_media_brief(log, pieces), indent=1))
    if beats:
        parts.append(f"Music beats (s): {', '.join(f'{b:.2f}' for b in beats[:120])}. Prefer scene changes on beats.")
    parts.append("Catalogs:\n" + json.dumps(catalog(), indent=1))
    return "\n\n".join(parts)


def validate(plan: MotionPlan, files: dict[str, dict], target: float | None = None) -> list[str]:
    """Problems a rebuild can't silently fix. Empty = good."""
    errs: list[str] = []
    if plan.length > MAX_LENGTH + 0.01:
        errs.append(f"total length {plan.length:g}s is above {MAX_LENGTH:g}s")
    if target and abs(plan.length - target) > max(1.5, target * 0.1):
        errs.append(f"total length {plan.length:g}s should be about {target:g}s")
    if plan.texture and plan.texture not in overlays.LIBRARY:
        errs.append(f"unknown texture {plan.texture}")
    looks = set(text_looks.all_looks())
    for si, sc in enumerate(plan.scenes, 1):
        if sc.dur < 0.8:
            errs.append(f"{sc.id}: scene shorter than 0.8s")
        if not sc.elements:
            errs.append(f"{sc.id}: no elements")
        moving = False
        for ei, e in enumerate(sc.elements, 1):
            at = f"S{si}.E{ei}"
            for k in ("enter", "exit", "loop"):
                n = getattr(e, k)
                if n and n not in anim.PRESETS:
                    errs.append(f"{at}: unknown preset {n}")
                elif n:
                    phase = anim.PRESETS[n][0]
                    want = {"enter": "in", "exit": "out", "loop": "loop"}[k]
                    if phase != want:
                        errs.append(f"{at}: {n} is an '{phase}' preset, not '{want}' ({k})")
            moving = moving or bool(e.enter or e.loop or e.path) or sc.transition != "cut"
            if e.kind in ("clip", "image"):
                f = files.get(e.file or "")
                if not f:
                    errs.append(f"{at}: unknown file {e.file}")
                elif (e.kind == "clip") != (f.get("kind") == "video"):
                    errs.append(f"{at}: {e.file} is a {f.get('kind')}, not a {e.kind}")
            if e.kind == "text":
                if not (e.text or "").strip():
                    errs.append(f"{at}: empty text")
                elif e.look and e.look not in looks:
                    errs.append(f"{at}: unknown look {e.look}")
                words = len((e.text or "").split())
                shown = (e.dur or (sc.dur - e.at))
                if words > 12:
                    errs.append(f"{at}: text too long ({words} words)")
                elif shown < 1.2 * words / 3 - 0.05:
                    errs.append(f"{at}: {words} words shown only {shown:.1f}s")
            for v, name in ((e.x, "x"), (e.y, "y")):
                if v and not _PCT.match(str(v)):
                    errs.append(f"{at}: {name} must be like '40%'")
            for k in e.path:
                if k.ease not in anim.EASINGS:
                    errs.append(f"{at}: unknown ease {k.ease}")
                    break
            if e.at >= sc.dur:
                errs.append(f"{at}: starts after its scene ends")
        if not moving:
            errs.append(f"{sc.id}: nothing moves")
    return errs


def fit(plan: MotionPlan, target: float, beats: list[float] | None = None) -> MotionPlan:
    """Scale scene lengths to the target (code times, not the AI), then snap scene changes to nearby beats."""
    plan = plan.model_copy(deep=True)
    target = min(MAX_LENGTH, target)
    total = plan.length or 1.0
    k = target / total
    for sc in plan.scenes:
        sc.dur = max(0.8, round(sc.dur * k, 3))
    if beats:
        t, cuts = 0.0, []
        for sc in plan.scenes[:-1]:
            t += sc.dur
            near = min(beats, key=lambda b: abs(b - t))
            cuts.append(near if abs(near - t) <= 0.25 else t)
        prev = 0.0
        for sc, c in zip(plan.scenes, cuts):
            sc.dur = max(0.8, round(c - prev, 3))
            prev += sc.dur
        plan.scenes[-1].dur = max(0.8, round(target - prev, 3))
    over = plan.length - MAX_LENGTH
    if over > 0:
        plan.scenes[-1].dur = max(0.8, round(plan.scenes[-1].dur - over, 3))
    return plan


def plan_motion(spec: DesignSpec, log: dict, pieces: list[dict], files: dict[str, dict], style_note: str = "",
                beats: list[float] | None = None, avoid: str = "") -> tuple[MotionPlan, dict]:
    prompt = _prompt(spec, log, pieces, style_note, beats or [])
    if avoid:
        prompt += f"\n\nMake this one clearly different from the other versions: {avoid}"
    plan, usage = ai.call("design_plan", prompt, MotionPlan, system=prompts.DESIGN_PLAN, quality=spec.quality)
    errs = validate(plan, files, spec.length_s)
    if errs:
        plan2, u2 = ai.call("design_plan", prompt + "\n\nYour plan had these problems; fix them:\n- " + "\n- ".join(errs[:20])
                            + "\n\nThe plan was:\n" + plan.model_dump_json(), MotionPlan, system=prompts.DESIGN_PLAN, quality=spec.quality)
        usage = _sum(usage, u2)
        if len(validate(plan2, files, spec.length_s)) <= len(errs):
            plan = plan2
    return fit(plan, spec.length_s, beats), usage


def listing(plan: MotionPlan) -> str:
    rows = []
    t = 0.0
    for si, sc in enumerate(plan.scenes, 1):
        rows.append(f"S{si} {t:.1f}-{t + sc.dur:.1f}s {sc.purpose} (transition {sc.transition}{', bg ' + sc.background if sc.background else ''})")
        for ei, e in enumerate(sc.elements, 1):
            d = e.model_dump(exclude_none=True, exclude_defaults=True)
            rows.append(f"  S{si}.E{ei} " + json.dumps(d))
        t += sc.dur
    return "\n".join(rows)


def apply_patch(plan: MotionPlan, patch: MotionPatch) -> tuple[MotionPlan, list[str]]:
    plan = plan.model_copy(deep=True)
    notes: list[str] = []
    removed: list[tuple[int, int]] = []
    for op in patch.ops:
        m = re.fullmatch(r"S(\d+)(?:\.E(\d+))?", op.target.strip())
        if not m:
            notes.append(f"bad target {op.target}")
            continue
        si = int(m.group(1)) - 1
        if not 0 <= si < len(plan.scenes):
            notes.append(f"no scene {op.target}")
            continue
        sc = plan.scenes[si]
        if op.op == "scene_dur" and op.dur:
            sc.dur = max(0.8, op.dur)
        elif op.op == "add" and op.element:
            sc.elements.append(op.element)
        elif op.op in ("set", "remove") and m.group(2):
            ei = int(m.group(2)) - 1
            if not 0 <= ei < len(sc.elements):
                notes.append(f"no element {op.target}")
                continue
            if op.op == "remove":
                removed.append((si, ei))
            elif op.element:
                cur = sc.elements[ei].model_dump()
                cur.update(op.element.model_dump(exclude_unset=True))
                sc.elements[ei] = Element.model_validate(cur)
        else:
            notes.append(f"ignored {op.op} {op.target}")
    for si, ei in sorted(removed, reverse=True):  # remove last, so earlier ids stay right
        del plan.scenes[si].elements[ei]
    return plan, notes


def patch_motion(spec: DesignSpec, plan: MotionPlan, issues: list[dict], files: dict[str, dict]) -> tuple[MotionPlan, dict, str]:
    lines = [f"{i.get('id', '?')} [{i.get('severity')}] {('at %.1fs ' % i['at']) if i.get('at') is not None else ''}{i.get('message')}"
             + (f" -> {i['fix']}" if i.get("fix") else "") for i in issues[:12]]
    prompt = (f"Target length {spec.length_s:g}s. Style: {STYLES[spec.style]}\n\nCurrent plan:\n{listing(plan)}\n\n"
              f"Audit issues:\n" + "\n".join(lines) + "\n\nCatalogs:\n" + json.dumps(catalog()))
    patch, usage = ai.call("design_fix", prompt, MotionPatch, system=prompts.DESIGN_FIX, quality=spec.quality)
    new, notes = apply_patch(plan, patch)
    if validate(new, files) and not validate(plan, files):
        return plan, usage, "patch made the plan invalid; kept the previous one"
    return fit(new, spec.length_s), usage, (patch.note or "") + (f" ({'; '.join(notes)})" if notes else "")


def _sum(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = round(out.get(k, 0) + v, 4)
    return out

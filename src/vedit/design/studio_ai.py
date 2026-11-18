"""AI actions of the Design studio, with whichever model the user connected (any provider in the model registry: the
Claude login, OpenAI, DeepSeek, OpenRouter, Gemini, Ollama ...). Each is ONE structured call through `ai.call`
(JSON schema, validated, one repair round), so any model that can return JSON works; no tool use is needed.

- storyboard: a brief / script -> scenes (name, length, notes, background, entrance). Shown to the user first,
  applied with `apply_storyboard` (one undo step).
- scene: one scene's notes + the design's look -> animated elements for that scene (one undo step).
- animate: the selected elements + an intent ("bouncy", "slow reveal") -> presets / keyframes (one undo step).

The vocabulary in the prompts (shapes, presets, easings, looks, fonts) is generated from the code, so it never
drifts from what the renderer can do. Rule of thumb as everywhere in aiklips: the AI writes and judges, code places
and times (scene ranges, clamping, validation).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .. import history
from ..ai import client as aic
from ..ops import animate as anim
from ..ops import fonts, scenes, shapes, text_looks
from ..project import SCENE_TRANSITIONS, Layer, Preset, Project, VeditError, load
from . import builder
from .spec import KF, STYLES, Element, MotionPlan

MAX_SCENES = 12


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SceneIdea(_M):
    name: str = Field(description="2-4 words")
    dur: float = Field(description="Seconds, 1.5-12")
    notes: str = Field(description="What the viewer sees and reads in this scene, 1-2 sentences, with the on-screen words in quotes")
    background: str | None = Field(None, description="Hex colour, or two joined with a comma for a gradient")
    transition: str = Field("fade", description="How its elements enter: " + " | ".join(SCENE_TRANSITIONS))


class Storyboard(_M):
    title: str = ""
    palette: list[str] = Field(default_factory=list, description="3-4 hex colours used across all scenes")
    scenes: list[SceneIdea] = Field(min_length=1, max_length=MAX_SCENES)
    note: str = Field("", description="One sentence: the idea behind this storyboard")


class SceneDesign(_M):
    elements: list[Element] = Field(min_length=1, max_length=10, description="Bottom to top")
    note: str = ""


class LayerMotion(_M):
    id: str
    enter: str | None = None
    exit: str | None = None
    loop: str | None = None
    path: list[KF] = Field(default_factory=list, description="Keyframes, t = seconds from the element's start")
    why: str = ""


class MotionAnswer(_M):
    layers: list[LayerMotion]
    note: str = ""


def _vocab() -> str:
    pres = {ph: [f"{k} ({v[3]})" for k, v in anim.PRESETS.items() if v[0] == ph] for ph in ("in", "out", "loop")}
    return "\n".join([
        "SHAPES (kind: shape): " + ", ".join(shapes.LABELS) + ". Fill: `fill` hex or `gradient` [2-4 hex]; polygon `sides`, "
        "star `points` + `inner` (0..1), ring `inner` = hole size. `width`/`height` in canvas px; any element can be "
        "stretched with sx / sy.",
        "ENTER presets: " + "; ".join(pres["in"]),
        "EXIT presets: " + "; ".join(pres["out"]),
        "LOOP presets: " + "; ".join(pres["loop"]),
        "EASINGS for keyframes: " + ", ".join(anim.EASINGS),
        "KEYFRAME props: x, y ('NN%'), scale, rotation, opacity, w, h (shape size px), sx, sy (stretch), fill (hex).",
        "TEXT looks: " + ", ".join(x["name"] for x in text_looks.catalog()),
        "FONTS: " + ", ".join(f"{k} ({v[2]})" for k, v in fonts.FONTS.items()),
    ])


SYSTEM = """You are the motion designer inside aiklips' Design studio. You design short vertical / square / landscape
motion-graphics videos built from animated shapes, text and the user's media, scene by scene (like Canva pages).
Taste: one idea per scene, few elements that each move with purpose, generous space, big readable words (at most
8 words on screen at once), a consistent palette and type across scenes, motion that has rhythm (things enter in
sequence, not all at once; stagger with `at`). Keep text inside x 8-92% and y 10-80%. Return only JSON that matches
the schema."""


def _models(model: str | None, task: str) -> dict[str, str]:
    return {aic.TIERS.get(task, "editor"): model} if model else {}


def _canvas_line(p: Project) -> str:
    return f"canvas {p.canvas.width}x{p.canvas.height}px, background {p.canvas.background}"


# ---- storyboard ----------------------------------------------------------------------------------------------

def storyboard(pdir: Path, brief: str, length: float = 20.0, style: str | None = None, model: str | None = None) -> dict:
    brief = (brief or "").strip()
    if not brief:
        raise VeditError("Write a brief or paste a script first")
    p = load(pdir)
    length = max(5.0, min(120.0, float(length or 20)))
    prompt = "\n".join([
        f"Write the storyboard for a {length:g}-second motion-design video ({_canvas_line(p)}).",
        f"Style: {STYLES[style]}" if style in STYLES else "Style: pick what suits the brief.",
        "Brief / script from the user:", '"""', brief[:6000], '"""',
        f"Return 2-{MAX_SCENES} scenes whose durations add up to about {length:g}s: a hook first, a clear ending "
        "(call to action or payoff) last. Scene notes say what is on screen and the exact words shown, in quotes.",
        "Scene entrances: " + ", ".join(SCENE_TRANSITIONS) + " (cut = elements just appear).",
    ])
    with aic.use_models(_models(model, "design_storyboard")):
        sb, usage = aic.call("design_storyboard", prompt, Storyboard, system=SYSTEM)
    # code fixes the timing: scale to the asked length, clamp each scene
    total = sum(max(0.5, s.dur) for s in sb.scenes) or 1.0
    out = []
    for s in sb.scenes:
        d = round(max(1.0, min(scenes.MAX_DUR, s.dur * length / total)) * 2) / 2
        out.append({"name": s.name.strip()[:40] or "Scene", "dur": d, "notes": s.notes.strip()[:600],
                    "background": scenes._background(s.background) if _ok_bg(s.background) else None,
                    "transition": s.transition if s.transition in SCENE_TRANSITIONS else "fade"})
    return {"title": sb.title[:80], "palette": [c for c in sb.palette if _ok_bg(c)][:4], "scenes": out, "note": sb.note[:300],
            "model": aic.model_for("design_storyboard") if not model else model, "usage": usage}


def _ok_bg(v) -> bool:
    try:
        return bool(v) and bool(scenes._background(v))
    except VeditError:
        return False


def apply_storyboard(pdir: Path, items: list[dict], mode: str = "replace") -> dict:
    """Make the proposed scenes real, as one undo step. replace: the design's scenes (and their elements) are
    replaced; append: added after the last scene."""
    if not items:
        raise VeditError("No scenes to add")
    with history.transaction(pdir, f"AI storyboard: {len(items)} scenes") as tx:
        p = tx.project
        scenes.require_design(p)
        start = round(sum(s.dur for s in p.scenes), 3) if mode == "append" else 0.0
        if mode != "append":
            old = {s.id for s in p.scenes}
            p.layers = [l for l in p.layers if l.scene not in old]
            first = p.scenes[0]
            p.scenes = [first]
            p.layers = [l for l in p.layers if l.scene != first.id]
            first.bg_layer = None
        made = []
        for i, it in enumerate(items[:MAX_SCENES]):
            kw = dict(name=str(it.get("name") or ""), background=it.get("background"), transition=it.get("transition") or "cut",
                      notes=str(it.get("notes") or ""))
            if mode != "append" and i == 0:
                sc = scenes.set_scene(p, pdir, p.scenes[0].id, float(it.get("dur") or 4), kw["name"], kw["background"],
                                      kw["transition"], kw["notes"])
            else:
                sc = scenes.add(p, pdir, float(it.get("dur") or 4), None, **kw)
            made.append(sc.id)
    return {"added": len(made), "scenes": made, "start": start}


# ---- one scene -----------------------------------------------------------------------------------------------

def design_scene(pdir: Path, scene_id: str, model: str | None = None, extra: str = "") -> dict:
    p = load(pdir)
    scenes.require_design(p)
    sc = p.scene(scene_id)
    s0, s1 = scenes.scene_range(p, sc.id)
    idx = [s.id for s in p.scenes].index(sc.id)
    others = []
    for o in p.scenes:
        if o.id == sc.id:
            continue
        ls = [l for l in p.layers if l.scene == o.id and l.id != o.bg_layer]
        looks = sorted({l.style.look for l in ls if l.type == "text" and l.style.look})
        fills = sorted({l.fill for l in ls if l.type == "shape" and l.fill})[:4]
        others.append(f"- {o.name or o.id}: {o.notes[:120]!r}; {len(ls)} elements" + (f", text looks {looks}" if looks else "")
                      + (f", colours {fills}" if fills else ""))
    existing = [f"{l.id} {l.type} {l.shape if l.type == 'shape' else (l.text or '')[:30]}" for l in p.layers
                if l.scene == sc.id and l.id != sc.bg_layer]
    media = [f"{sid}: {Path(s.path).name}" for sid, s in p.sources.items()
             if "/.gap-" not in s.path.replace("\\", "/") and (s.has_video or Path(s.path).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))]
    prompt = "\n".join([
        f"Design scene {idx + 1} of {len(p.scenes)} ({sc.dur:g}s) of a motion-design video ({_canvas_line(p)}).",
        f"Scene name: {sc.name!r}. Notes: {sc.notes!r}." + (f" Background {sc.background}." if sc.background else ""),
        f"The scene's entrance is '{sc.transition}': elements without their own `enter` get it.",
        "Other scenes (stay consistent with them):", *(others or ["(none yet)"]),
        "Already in this scene (keep them; add what is missing): " + ("; ".join(existing) or "nothing"),
        ("User media you may use as `image` elements with file = the id: " + "; ".join(media)) if media else "No user media: use shapes and text.",
        f"`at` / `dur` are seconds within the scene (0-{sc.dur:g}). Positions are 'NN%'. Give each element a short `why`.",
        extra and f"The user adds: {extra}", "", _vocab(),
    ])
    with aic.use_models(_models(model, "design_scene")):
        res, usage = aic.call("design_scene", prompt, SceneDesign, system=SYSTEM)
    files = {sid: {"path": s.path, "kind": "image" if Path(s.path).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") else "video"}
             for sid, s in p.sources.items()}
    report = {"added": 0, "skipped": [], "note": res.note[:300], "usage": usage}
    with history.transaction(pdir, f"AI design: {sc.name or sc.id}") as tx:
        q = tx.project
        plan = MotionPlan(scenes=[{"id": "S1", "dur": sc.dur}], palette=[], font=None)
        sid_map = {k: k for k, v in files.items() if v["kind"] == "video" and k in q.sources}
        for i, e in enumerate(res.elements, 1):
            try:
                if e.kind == "clip" and e.file not in sid_map:
                    raise VeditError(f"unknown video {e.file}")
                l = builder._element(q, e, s0, s1 - s0, plan, files, sid_map)
                _motion(l, e.enter, e.exit, e.loop, e.path)
                l.why = (e.why or sc.notes)[:200] or None
                scenes.place_in_scene(q, l, sc.id)
                l.start, l.end = max(l.start, s0), min(l.end or s1, s1)
                report["added"] += 1
            except (VeditError, ValueError, KeyError) as ex:
                report["skipped"].append(f"element {i} {e.kind}: {ex}")
        if not report["added"]:
            raise VeditError("The AI's elements could not be used: " + "; ".join(report["skipped"])[:300])
    report["skipped"] = len(report["skipped"])
    return report


def _motion(l: Layer, enter: str | None, exit: str | None, loop: str | None, path: list[KF] | None) -> None:
    pres = []
    for name, phase in ((enter, "in"), (exit, "out"), (loop, "loop")):
        if name and name in anim.PRESETS and anim.PRESETS[name][0] == phase:
            pres.append(Preset(name=name))
    if pres:
        l.presets = [x for x in l.presets if anim.PRESETS.get(x.name, ("",))[0] not in {anim.PRESETS[p.name][0] for p in pres}] + pres
    if path:
        l.keyframes = builder.keyframes(path)
        anim.validate_keyframes(l.keyframes)
        l.presets = [x for x in l.presets if not x.params.get("auto")]


# ---- animate the selection -------------------------------------------------------------------------------------

def animate(pdir: Path, layer_ids: list[str], intent: str = "", model: str | None = None) -> dict:
    p = load(pdir)
    ls = [p.layer(i) for i in layer_ids][:12]
    if not ls:
        raise VeditError("Select the elements to animate first")
    desc = []
    for l in ls:
        end = l.end if l.end is not None else p.duration
        what = l.text if l.type == "text" else l.shape if l.type == "shape" else Path(l.image or l.source or "").name
        desc.append(json.dumps({"id": l.id, "type": l.type, "what": (what or "")[:40], "x": l.x, "y": l.y,
                                "width": l.width, "height": l.height, "seconds": round(end - l.start, 2),
                                "starts_at": round(l.start, 2), "presets": [x.name for x in l.presets],
                                "keyframes": len(l.keyframes)}))
    prompt = "\n".join([
        f"Animate these elements of a motion-design video ({_canvas_line(p)}). The user wants: {intent or 'motion that fits them, with rhythm'}.",
        "Elements:", *desc,
        "For each element give an enter, exit and/or loop preset, and/or a keyframe path for custom motion (t in "
        "seconds from the element's start, within its seconds). Stagger elements that enter together. Leave a field "
        "empty to keep what it has.", "", _vocab(),
    ])
    with aic.use_models(_models(model, "design_animate")):
        res, usage = aic.call("design_animate", prompt, MotionAnswer, system=SYSTEM)
    changed = 0
    with history.transaction(pdir, f"AI animate: {', '.join(layer_ids)[:60]}") as tx:
        for m in res.layers:
            if m.id not in layer_ids:
                continue
            l = tx.project.layer(m.id)
            end = (l.end if l.end is not None else tx.project.duration) - l.start
            path = [k for k in m.path if 0 <= k.t <= end + 0.01]
            _motion(l, m.enter, m.exit, m.loop, path)
            if m.why:
                l.why = m.why[:200]
            changed += 1
    return {"changed": changed, "note": res.note[:300], "usage": usage}

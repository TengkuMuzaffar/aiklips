"""Design projects: scenes ("pages") of animated elements, back to back on the timeline.

A design is an ordinary project (`kind == "design"`), so undo, the renderer, export and the audit all work as usual:
- `Project.scenes` lists the scenes in play order; a scene's start is the sum of the durations before it.
- every layer made in a scene points at it (`Layer.scene`) and keeps absolute timeline times; when a scene grows,
  shrinks, moves or goes, `_relayout` moves its layers (and those of the scenes after it) along.
- the main track is owned by the scenes: background clips of the canvas colour as long as all scenes together
  (`_sync_main`); media goes in as overlay layers. A scene's own background colour is a full-canvas rect layer on
  the bottom track (`Scene.bg_layer`).
- a scene's `transition` is how its layers enter: each layer gets that enter preset, marked `auto` in its params,
  unless the user gave it an enter animation of its own.
"""

from __future__ import annotations

from pathlib import Path

from ..project import (SCENE_TRANSITIONS, ClipMeta, Layer, Preset, Project, Scene, VeditError, normalize_tracks)
from . import cut

MIN_DUR, MAX_DUR = 0.5, 60.0
MAX_TOTAL = 300.0
FORMATS = {"reels": (1080, 1920), "square": (1080, 1080), "landscape": (1920, 1080), "portrait45": (1080, 1350)}
# a scene's transition -> the enter preset its layers get (None = they just appear)
ENTER = {"cut": None, "fade": "fade_in", "slide": "slide_in", "zoom": "zoom_in", "pop": "pop_in", "wipe": "wipe_in"}


def require_design(p: Project) -> None:
    if p.kind != "design":
        raise VeditError("Scenes are for design projects (made with `vedit design new` or the Design studio)")


def _round(v: float) -> float:
    return round(v, 3)


def scene_range(p: Project, scene_id: str) -> tuple[float, float]:
    starts = p.scene_starts()
    for sc, st in zip(p.scenes, starts):
        if sc.id == scene_id:
            return st, _round(st + sc.dur)
    raise VeditError(f"No scene '{scene_id}'")


def scene_at(p: Project, t: float) -> Scene | None:
    for sc, st in zip(p.scenes, p.scene_starts()):
        if st <= t < st + sc.dur:
            return sc
    return p.scenes[-1] if p.scenes else None


def _check_dur(d: float) -> float:
    d = float(d)
    if not MIN_DUR <= d <= MAX_DUR:
        raise VeditError(f"A scene lasts {MIN_DUR:g}-{MAX_DUR:g} s")
    return _round(d)


# ---- keeping layers, background and main track in step ------------------------------------------------------------

def _relayout(p: Project, before: dict[str, tuple[float, float]], pdir: Path | None) -> None:
    """Scenes changed (order / durations): move each scene's layers by how far its start moved, keep them inside
    the scene, then fix backgrounds and the main track. `before`: scene id -> (old start, old end)."""
    after = {sc.id: (st, _round(st + sc.dur)) for sc, st in zip(p.scenes, p.scene_starts())}
    for l in p.layers:
        if l.scene not in after or l.scene not in before:
            continue
        (o0, o1), (n0, n1) = before[l.scene], after[l.scene]
        shift = n0 - o0
        to_end = l.end is None or abs(l.end - o1) < 1e-3  # lasted to the scene's end: keeps doing so
        l.start = _round(l.start + shift)
        l.end = n1 if to_end else _round(l.end + shift)
        # a shorter scene trims what now runs past its end
        if l.start > n1 - 0.1:
            l.start = _round(max(n0, n1 - 0.5))
        if l.end is None or l.end > n1:
            l.end = n1
    _sync_backgrounds(p)
    if pdir is not None:
        _sync_main(p, pdir)
    normalize_tracks(p)


def _sync_backgrounds(p: Project) -> None:
    ranges = {sc.id: (st, _round(st + sc.dur)) for sc, st in zip(p.scenes, p.scene_starts())}
    W, H = p.canvas.width, p.canvas.height
    ids = {sc.bg_layer for sc in p.scenes if sc.bg_layer}
    for sc in p.scenes:
        lay = next((l for l in p.layers if l.id == sc.bg_layer), None) if sc.bg_layer else None
        if not sc.background:
            if lay is not None:
                p.layers.remove(lay)
            sc.bg_layer = None
            continue
        if lay is None:
            lay = Layer(id=p.new_id("bg"), type="shape", shape="rect", radius=0, scene=sc.id,
                        why=f"{sc.name or sc.id} background")
            # the bottom row: shift every other layer up one track, unless a background row already exists
            row = next((l.track for l in p.layers if l.id in ids and l.track is not None), None)
            if row is None:
                for o in p.layers:
                    if o.track is not None:
                        o.track += 1
                row = 0
            lay.track = row
            p.layers.insert(0, lay)
            sc.bg_layer = lay.id
            ids.add(lay.id)
        lay.start, lay.end = ranges[sc.id]
        cols = [c.strip() for c in sc.background.split(",") if c.strip()]  # "#a,#b" = a gradient, top to bottom
        lay.width, lay.height, lay.x, lay.y, lay.fill = W, H, "50%", "50%", cols[0]
        lay.fill_gradient = cols if len(cols) > 1 else None
        lay.gradient_angle = 90.0


def _sync_main(p: Project, pdir: Path) -> None:
    """The main track: canvas-colour background clips, exactly as long as the scenes together."""
    total = _round(sum(sc.dur for sc in p.scenes))
    gid = cut.gap_source(p, pdir)
    p.clips = []
    for c in cut._gap_clips(p, gid, total):
        c.gap, c.volume = False, 0.0  # real clips: a trailing gap would be dropped
        c.meta = ClipMeta(why="Design background (the scenes' elements are layers on top)", role="other")
        p.clips.append(c)


def _snapshot(p: Project) -> dict[str, tuple[float, float]]:
    return {sc.id: (st, _round(st + sc.dur)) for sc, st in zip(p.scenes, p.scene_starts())}


def _apply_transition(p: Project, sc: Scene) -> None:
    """Give the scene's layers its enter preset (replacing an earlier automatic one, never one the user chose)."""
    want = ENTER.get(sc.transition)
    for l in p.layers:
        if l.scene != sc.id or l.id == sc.bg_layer:
            continue
        l.presets = [x for x in l.presets if not x.params.get("auto")]
        own_enter = any(_phase(x.name) == "in" for x in l.presets)
        if want and not own_enter and not l.keyframes:
            l.presets.insert(0, Preset(name=want, params={"auto": True}))


def _phase(name: str) -> str | None:
    from .animate import PRESETS

    return PRESETS[name][0] if name in PRESETS else None


# ---- the ops ------------------------------------------------------------------------------------------------------

def new_design(p: Project, pdir: Path, fmt: str = "reels", name: str | None = None, dur: float = 5.0,
               background: str | None = None) -> Scene:
    """Turn an empty project into a design: canvas of the format, one scene, the background main track."""
    if p.clips or p.layers:
        raise VeditError("A design starts from an empty project")
    if fmt not in FORMATS:
        raise VeditError(f"Format must be one of: {', '.join(FORMATS)}")
    p.kind = "design"
    p.canvas.width, p.canvas.height = FORMATS[fmt]
    if background:
        p.canvas.background = background
    if name:
        p.name = name
    sc = Scene(id=p.new_id("sc"), name="Scene 1", dur=_check_dur(dur))
    p.scenes = [sc]
    _sync_main(p, pdir)
    return sc


def add(p: Project, pdir: Path, dur: float = 5.0, after: str | None = None, name: str = "", background: str | None = None,
        transition: str = "cut", notes: str = "") -> Scene:
    require_design(p)
    if transition not in SCENE_TRANSITIONS:
        raise VeditError(f"Transition must be one of: {', '.join(SCENE_TRANSITIONS)}")
    before = _snapshot(p)
    sc = Scene(id=p.new_id("sc"), name=name or f"Scene {len(p.scenes) + 1}", dur=_check_dur(dur),
               background=_background(background), transition=transition, notes=notes)  # type: ignore[arg-type]
    idx = len(p.scenes) if after is None else [s.id for s in p.scenes].index(p.scene(after).id) + 1
    p.scenes.insert(idx, sc)
    _check_total(p)
    _relayout(p, before, pdir)
    return sc


def _background(v) -> str | None:
    """A scene background: one hex colour, or 2-4 joined with commas for a top-to-bottom gradient."""
    if not v or str(v).lower() == "none":
        return None
    from .animate import mix_hex

    cols = [mix_hex(c.strip(), c.strip(), 0) for c in str(v).split(",") if c.strip()][:4]
    return ",".join(cols) or None


def _check_total(p: Project) -> None:
    if sum(sc.dur for sc in p.scenes) > MAX_TOTAL:
        raise VeditError(f"A design is at most {MAX_TOTAL:g} s long")


def set_scene(p: Project, pdir: Path, scene_id: str, dur: float | None = None, name: str | None = None,
              background: str | None | bool = False, transition: str | None = None, notes: str | None = None,
              why: str | None = None) -> Scene:
    """Change a scene. background=False leaves it; None or "" removes it."""
    require_design(p)
    sc = p.scene(scene_id)
    before = _snapshot(p)
    if dur is not None:
        sc.dur = _check_dur(dur)
        _check_total(p)
    if name is not None:
        sc.name = name.strip()[:60]
    if background is not False:
        sc.background = _background(background)
    if notes is not None:
        sc.notes = notes[:2000]
    if why is not None:
        sc.why = why[:300] or None
    if transition is not None:
        if transition not in SCENE_TRANSITIONS:
            raise VeditError(f"Transition must be one of: {', '.join(SCENE_TRANSITIONS)}")
        sc.transition = transition  # type: ignore[assignment]
        _apply_transition(p, sc)
    _relayout(p, before, pdir)
    return sc


def move(p: Project, pdir: Path, scene_id: str, index: int) -> None:
    require_design(p)
    sc = p.scene(scene_id)
    before = _snapshot(p)
    p.scenes.remove(sc)
    p.scenes.insert(max(0, min(len(p.scenes), int(index))), sc)
    _relayout(p, before, pdir)


def duplicate(p: Project, pdir: Path, scene_id: str) -> Scene:
    require_design(p)
    sc = p.scene(scene_id)
    before = _snapshot(p)
    copy = sc.model_copy(deep=True, update={"id": p.new_id("sc"), "name": f"{sc.name or sc.id} copy", "bg_layer": None})
    p.scenes.insert(p.scenes.index(sc) + 1, copy)
    _check_total(p)
    o0 = before[sc.id][0]
    new_layers = []
    for l in [l for l in p.layers if l.scene == sc.id and l.id != sc.bg_layer]:
        d = l.model_copy(deep=True, update={"id": "", "scene": copy.id, "track": None})
        d.id = _fresh_id(p, new_layers, l.id)
        new_layers.append(d)
    p.layers.extend(new_layers)
    # the copies sit at the original's times; make relayout move them into the new scene (which starts at o0 + dur)
    before[copy.id] = (o0, _round(o0 + sc.dur))
    _relayout(p, before, pdir)
    return copy


def _fresh_id(p: Project, pending: list[Layer], like: str) -> str:
    import re

    prefix = re.sub(r"\d+$", "", like) or "l"
    used = {l.id for l in p.layers} | {l.id for l in pending}
    n = 1
    while f"{prefix}{n}" in used or f"{prefix}{n}" in {c.id for c in p.clips}:
        n += 1
    return f"{prefix}{n}"


def remove(p: Project, pdir: Path, scene_id: str) -> None:
    require_design(p)
    sc = p.scene(scene_id)
    if len(p.scenes) == 1:
        raise VeditError("A design needs at least one scene")
    before = _snapshot(p)
    p.layers = [l for l in p.layers if l.scene != sc.id]
    p.scenes.remove(sc)
    _relayout(p, before, pdir)


def place_in_scene(p: Project, layer: Layer, scene_id: str | None = None, at: float | None = None) -> Layer:
    """Attach a new layer to a scene (the one at `at`, or scene_id) and fit it inside it; the scene's transition
    becomes its enter animation."""
    if p.kind != "design" or not p.scenes:
        return layer
    sc = p.scene(scene_id) if scene_id else scene_at(p, layer.start if at is None else at)
    if sc is None:
        return layer
    s0, s1 = scene_range(p, sc.id)
    layer.scene = sc.id
    if not s0 <= layer.start < s1 - 0.1:
        layer.start = s0
    if layer.end is None or layer.end > s1 or layer.end <= layer.start:
        layer.end = s1
    want = ENTER.get(sc.transition)
    if want and not layer.keyframes and not any(_phase(x.name) == "in" for x in layer.presets):
        layer.presets.insert(0, Preset(name=want, params={"auto": True}))
    return layer


def adopt_legacy(pdir: Path) -> bool:
    """A project made by the old Design (beta) wizard (a saved MotionPlan, a main track of background clips) becomes a
    design with scenes, so it opens in the Design studio. One history step; returns True when it changed."""
    from .. import history
    from ..design.builder import plan_path, saved_plan
    from ..project import load

    if not plan_path(pdir).exists():
        return False
    p = load(pdir)
    if p.kind == "design" or any(c.gap is False and "/.gap-" not in p.sources[c.source].path.replace("\\", "/")
                                 for c in p.clips if c.source in p.sources):
        return False
    plan = saved_plan(pdir)
    if plan is None:
        return False
    with history.transaction(pdir, "open as a design (scenes)") as tx:
        q = tx.project
        q.kind = "design"
        total, t = q.duration, 0.0
        for sc in plan.scenes:
            if t >= total - 0.05:
                break
            d = _round(min(sc.dur, total - t))
            q.scenes.append(Scene(id=q.new_id("sc"), name=(sc.purpose or sc.id)[:40], dur=max(MIN_DUR, d),
                                  transition=sc.transition if sc.transition in SCENE_TRANSITIONS else "cut",  # type: ignore[arg-type]
                                  notes=sc.purpose[:600]))
            t = _round(t + d)
        if not q.scenes:
            q.scenes.append(Scene(id=q.new_id("sc"), name="Scene 1", dur=max(MIN_DUR, _round(total))))
    return True


def reconcile(p: Project) -> None:
    """Keep `Layer.scene` true to the layer's times (run on every save of a design): a layer that lies inside one
    scene belongs to it; one spanning several scenes (or the whole video) belongs to none. A layer that just joined
    a scene gets that scene's enter animation."""
    ranges = [(sc, st, _round(st + sc.dur)) for sc, st in zip(p.scenes, p.scene_starts())]
    total = ranges[-1][2] if ranges else 0.0
    bgs = {sc.bg_layer for sc in p.scenes if sc.bg_layer}
    for l in p.layers:
        if l.id in bgs or l.type == "captions":
            continue
        end = total if l.end is None else l.end
        home = next((sc for sc, a, b in ranges if a - 1e-3 <= l.start and end <= b + 1e-3), None)
        if home is None:
            l.scene = None
            continue
        if l.scene != home.id:
            l.scene = home.id
            want = ENTER.get(home.transition)
            l.presets = [x for x in l.presets if not x.params.get("auto")]
            if want and not l.keyframes and not any(_phase(x.name) == "in" for x in l.presets):
                l.presets.insert(0, Preset(name=want, params={"auto": True}))


def summary(p: Project) -> list[dict]:
    out = []
    for sc, st in zip(p.scenes, p.scene_starts()):
        layers = [l.id for l in p.layers if l.scene == sc.id and l.id != sc.bg_layer]
        out.append({"id": sc.id, "name": sc.name, "start": st, "end": _round(st + sc.dur), "dur": sc.dur,
                    "background": sc.background, "transition": sc.transition, "notes": sc.notes, "layers": layers})
    return out

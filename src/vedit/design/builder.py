"""Turn a MotionPlan into a normal aiklips project.

The main track is a background (canvas colour gap clips) as long as the plan; the user's clips, images, text and
shapes are animated overlay layers on top. One undoable history step per scene, so the user can step back through
the AI's work in the editor. Elements that fail are skipped and reported; the rest of the video is still built.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import history
from ..ops import animate as anim
from ..ops import cut, fonts, motion, overlays, text_looks
from ..ops import scenes as scenes_ops
from ..project import ClipMeta, Keyframe, Layer, MusicTrack, Preset, Project, Source, TextStyle, VeditError
from ..project import Scene as ProjScene
from .spec import MAX_LENGTH, Element, MotionPlan, Scene

PLAN_FILE = "design_plan.json"  # inside .vedit/: the plan this project was built from (fix rounds patch it)

# scene transition -> default entrance of the scene's elements (an element's own `enter` wins)
_ENTER = {"cut": None, "fade": "fade_in", "slide": "slide_in", "zoom": "zoom_in", "pop": "pop_in"}


def _hex(c: str | None, default: str) -> str:
    c = (c or "").strip()
    if len(c) in (4, 7, 9) and c.startswith("#"):
        try:
            int(c[1:], 16)
            return c
        except ValueError:
            pass
    return default


def _preset(name: str | None) -> Preset | None:
    if not name or name not in anim.PRESETS:
        return None
    return Preset(name=name)


def plan_path(pdir: Path) -> Path:
    return pdir / ".vedit" / PLAN_FILE


def saved_plan(pdir: Path) -> MotionPlan | None:
    f = plan_path(pdir)
    return MotionPlan.model_validate_json(f.read_text()) if f.exists() else None


def build(plan: MotionPlan, pdir: Path, files: dict[str, dict]) -> dict:
    """files: id -> {path, kind: video|image|audio, duration, width, height, fps, has_audio}. The project at `pdir`
    must exist (executor.create_project) and is rebuilt from scratch: earlier layers / clips / music are replaced."""
    report: dict = {"applied": [], "skipped": [], "length": 0.0}
    length = min(MAX_LENGTH, plan.length)
    if length < 1:
        raise VeditError("The plan is shorter than a second")
    report["length"] = length
    sid: dict[str, str] = {}

    with history.transaction(pdir, f"AI design: background ({length:g}s)") as tx:
        p = tx.project
        p.layers, p.clips, p.music, p.scenes = [], [], [], []
        # a design project: the plan's scenes become studio scenes (pages), so it opens in the Design studio
        p.kind = "design"
        if plan.style:
            from . import styles

            try:
                p.motion_style = styles.get(plan.style).key
            except VeditError as ex:
                report["skipped"].append(f"style: {ex}")
        t = 0.0
        for sc in plan.scenes:
            if t >= length - 0.05:
                break
            d = round(min(sc.dur, length - t), 3)
            p.scenes.append(ProjScene(id=p.new_id("sc"), name=(sc.purpose or sc.id)[:40], dur=d,
                                      background=_hex(sc.background, "") or None, transition=sc.transition,
                                      notes=sc.purpose[:600]))
            t = round(t + d, 3)
        p.canvas.background = _hex(plan.background, "#101014")
        if plan.title:
            p.name = plan.title[:80]
        # the background: the canvas-colour file gap clips use, but as real clips (trailing gaps are dropped)
        gid = cut.gap_source(p, pdir)
        for c in cut._gap_clips(p, gid, length):
            c.gap, c.volume = False, 0.0
            c.meta = ClipMeta(why="Design background (the motion graphics are layers on top)", role="other")
            p.clips.append(c)
        for fid in sorted({e.file for s in plan.scenes for e in s.elements if e.kind == "clip" and e.file}
                          | ({plan.music.file} if plan.music else set())):
            f = files.get(fid)
            if not f or f.get("kind") == "image":
                continue
            s = p.new_id("s")
            p.sources[s] = Source(path=f["path"], duration=f["duration"], width=f.get("width"), height=f.get("height"),
                                  fps=f.get("fps"), has_audio=f.get("has_audio", True), has_video=f.get("kind") == "video")
            sid[fid] = s
        if plan.texture:
            try:
                l = overlays.add_from_library(p, pdir, plan.texture, 0.0)
                l.end = None
                report["applied"].append(f"texture {plan.texture}")
            except (VeditError, KeyError, ValueError) as e:
                report["skipped"].append(f"texture {plan.texture}: {e}")
        scenes_ops._sync_backgrounds(p)

    t = 0.0
    for si, sc in enumerate(plan.scenes, 1):
        if t >= length - 0.05:
            report["skipped"].append(f"{sc.id}: past {MAX_LENGTH:g}s")
            break
        dur = round(min(sc.dur, length - t), 3)
        with history.transaction(pdir, f"AI design: scene {si} {sc.purpose[:40]}".rstrip()) as tx:
            _scene(tx.project, sc, si, t, dur, plan, files, sid, report)
        t = round(t + dur, 3)

    if plan.music and plan.music.file in sid:
        with history.transaction(pdir, "AI design: music") as tx:
            m = plan.music
            tx.project.music.append(MusicTrack(id=tx.project.new_id("m"), source=sid[m.file], in_=max(0.0, m.offset),
                                               volume=min(1.0, max(0.0, m.volume)), loop=True, fade_in=0.2, fade_out=1.0,
                                               duck=any(e.volume for s in plan.scenes for e in s.elements)))
        report["applied"].append("music")
    elif plan.music:
        report["skipped"].append(f"music {plan.music.file}: not a sound file of this design")

    f = plan_path(pdir)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(plan.model_dump_json(indent=1))
    return report


def _scene(p: Project, sc: Scene, si: int, t0: float, dur: float, plan: MotionPlan, files: dict[str, dict],
           sid: dict[str, str], report: dict) -> None:
    default_enter = _ENTER.get(sc.transition)
    last = si == len(plan.scenes)
    for ei, e in enumerate(sc.elements, 1):
        name = f"{sc.id}.E{ei} {e.kind}"
        try:
            l = _element(p, e, t0, dur, plan, files, sid)
            ins = [x for x in (_preset(e.enter) or _preset(default_enter if not e.path else None),
                               _preset(e.exit) or (None if last or sc.transition == "cut" else _preset("fade_out")),
                               _preset(e.loop)) if x]
            for pr in ins:
                anim.validate_preset(pr)
            own = [x for x in l.presets if x.name in ("count_up", "draw_in") and x.name not in {i.name for i in ins}]
            l.presets = own + ins  # a counter's count / a chart's growth stay
            if e.path:
                l.keyframes = keyframes(e.path)
                anim.validate_keyframes(l.keyframes)
            l.why = (e.why or f"{sc.id}: {sc.purpose}")[:200] or None
            report["applied"].append(name)
        except (VeditError, ValueError, KeyError) as ex:
            report["skipped"].append(f"{name}: {ex}")


def _times(e: Element, t0: float, dur: float) -> tuple[float, float]:
    at = min(max(0.0, e.at), max(0.0, dur - 0.3))
    d = e.dur if e.dur and e.dur > 0 else dur - at
    return round(t0 + at, 3), round(t0 + min(dur, at + d), 3)


def _common(l: Layer, e: Element) -> None:
    if e.x:
        l.x = e.x
    if e.y:
        l.y = e.y
    if e.scale is not None:
        l.scale = max(0.05, min(5.0, e.scale))
    if e.rotation is not None:
        l.rotation = e.rotation
    if e.opacity is not None:
        l.opacity = max(0.0, min(1.0, e.opacity))
    if e.sx is not None:
        l.sx = max(0.05, min(10.0, e.sx))
    if e.sy is not None:
        l.sy = max(0.05, min(10.0, e.sy))


def keyframes(path) -> list[Keyframe]:
    """A plan's path (KF list) as layer keyframes (bad colours are dropped, not fatal)."""
    out = []
    for k in path:
        fill = _hex(k.fill, "") or None if k.fill else None
        out.append(Keyframe(t=max(0.0, k.t), x=k.x, y=k.y, scale=k.scale, rotation=k.rotation, opacity=k.opacity,
                            w=k.w, h=k.h, sx=k.sx, sy=k.sy, fill=fill, draw=k.draw, morph=k.morph, count=k.count,
                            ease=_ease(k.ease)))
    return out


def _ease(name: str | None, default: str = "ease_in_out") -> str:
    try:
        anim.ease(name or default, 0.5)
        return name or default
    except VeditError:
        return default


def _frame(l: Layer, e: Element, W: int) -> None:
    fr = e.frame or "card"
    if fr == "full":
        l.width, l.x, l.y = W, "50%", "50%"
        if l.type == "video":
            l.fit = "cover"
    elif fr == "card":
        l.width, l.radius = e.width or int(W * 0.8), 36 if e.radius is None else e.radius
    elif fr == "circle":
        l.width, l.mask = e.width or int(W * 0.62), "circle"
    elif fr == "half":
        l.width, l.radius = e.width or int(W * 0.5), 28 if e.radius is None else e.radius
    else:  # pip
        l.width, l.radius = e.width or int(W * 0.38), 24 if e.radius is None else e.radius
        l.x, l.y = "72%", "70%"
    if e.border:
        l.border_width, l.border_color = 8, _hex(e.border, "#FFFFFF")


def _element(p: Project, e: Element, t0: float, dur: float, plan: MotionPlan, files: dict[str, dict],
             sid: dict[str, str]) -> Layer:
    W = p.canvas.width
    start, end = _times(e, t0, dur)
    if e.kind == "clip":
        if e.file not in sid:
            raise VeditError(f"unknown video file {e.file}")
        src = p.sources[sid[e.file]]
        src_in = max(0.0, min(e.src_in or 0.0, max(0.0, src.duration - 0.2)))
        l = overlays.add_video(p, sid[e.file], start, end, x="50%", y="50%", src_in=src_in,
                               loop=src.duration - src_in < end - start, volume=min(1.0, max(0.0, e.volume or 0.0)))
        _frame(l, e, W)
    elif e.kind == "image":
        f = files.get(e.file or "")
        if not f or f.get("kind") != "image":
            raise VeditError(f"unknown image {e.file}")
        l = Layer(id=p.new_id("img"), type="image", image=f["path"], start=start, end=end)
        p.layers.append(l)
        if e.frame:
            _frame(l, e, W)
        else:
            l.width = e.width or int(W * 0.6)
    elif e.kind == "text":
        txt = (e.text or "").strip()[:160]
        if not txt:
            raise VeditError("empty text")
        l = Layer(id=p.new_id("t"), type="text", text=txt, start=start, end=end, y="30%")
        if e.look:
            text_looks.apply(l, e.look, animation=False)
        else:
            l.style = TextStyle(size=88, color=_hex(e.color or (plan.palette[0] if plan.palette else None), "#FFFFFF"),
                                max_width=int(W * 0.84), stroke_width=0, shadow=True)
        if e.size:
            l.style.size = max(24, min(260, e.size))
        if e.color:
            l.style.color = _hex(e.color, l.style.color)
        if plan.font and plan.font in fonts.FONTS:
            l.style.font = plan.font
        p.layers.append(l)
        if e.split and e.split != "none":
            motion.set_fields(l, {"split": e.split, "split_anim": e.split_anim, "stagger": e.stagger,
                                  "split_ease": _ease(e.split_ease, "ease_out")})
    elif e.kind == "counter":
        style = {"size": max(24, min(320, e.size or 150)), "color": _hex(e.color or (plan.palette[0] if plan.palette else None), "#FFFFFF"),
                 "stroke_width": 0}
        if plan.font and plan.font in fonts.FONTS:
            style["font"] = plan.font
        l = motion.add_counter(p, float(e.count_to if e.count_to is not None else 100), start, end, e.x or "50%", e.y or "40%",
                               min(1.5, max(0.3, (end - start) * 0.6)), "expo_out", style,
                               **{"from": e.count_from, "decimals": e.decimals, "prefix": e.prefix, "suffix": e.suffix})
    elif e.kind == "chart":
        from ..ops import chart as chart_ops

        opts = {k: v for k, v in (("prefix", e.prefix), ("suffix", e.suffix), ("decimals", e.decimals),
                                  ("colors", [_hex(c, "#22D3EE") for c in e.colors] if e.colors else (plan.palette or None)),
                                  ("font", plan.font if plan.font in fonts.FONTS else None)) if v is not None}
        l = motion.add_chart(p, e.chart or "bar", chart_ops.parse_items(e.data or "A=3, B=7, C=5"), start, end,
                             e.x or "50%", e.y or "50%", e.width, e.height, min(1.6, max(0.4, (end - start) * 0.5)), **opts)
    else:  # shape
        kw: dict = {}
        if e.width:
            kw["width"] = max(4, e.width)
        if e.height:
            kw["height"] = max(2, e.height)
        if e.fill:
            kw["fill"] = _hex(e.fill, "#FFFFFF")
        if e.radius is not None:
            kw["radius"] = max(0, e.radius)
        if e.border:
            kw["border_width"], kw["border_color"] = 8, _hex(e.border, "#FFFFFF")
        l = overlays.add_shape(p, e.shape or "rect", start, end, **kw)
        extra = {k: v for k, v in (("fill_gradient", ",".join(e.gradient) if e.gradient else None), ("sides", e.sides),
                                   ("points", e.points), ("inner", e.inner)) if v is not None}
        overlays.set_fields(l, extra)
        if e.shape == "path" or e.morph_to:
            motion.set_fields(l, {"path": e.points_xy, "closed": e.closed, **({"morph_to": e.morph_to} if e.morph_to else {})})
    _common(l, e)
    return l


def files_from_json(data: dict) -> dict[str, dict]:
    """`vedit design build` input: {"files": {"f1": "/path/clip.mp4", ...}} -> probed file metas."""
    from .. import media
    from ..ops.tracks import IMAGE_EXT

    out: dict[str, dict] = {}
    for fid, path in (data or {}).items():
        pth = Path(path).expanduser().resolve()
        if not pth.exists():
            raise VeditError(f"{fid}: no file {pth}")
        if pth.suffix.lower() in IMAGE_EXT:
            out[fid] = {"path": str(pth), "kind": "image"}
            continue
        info = media.probe(pth)
        out[fid] = {"path": str(pth), "kind": "video" if info.get("has_video") else "audio", **info}
    return out


def load_plan_file(path: Path) -> tuple[MotionPlan, dict]:
    """A plan file for the CLI: the MotionPlan plus an optional "files" map."""
    data = json.loads(Path(path).read_text())
    return MotionPlan.model_validate(data), data.get("files") or {}

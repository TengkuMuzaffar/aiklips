"""Turn an EditPlan into a normal aiklips project, one undoable history step per operation.

Only files from the batch's footage list can be used (by file id), so a reference video's
own frames or music can never end up in an output.

`vedit` ops run last, as real CLI commands on the built project (generate/commands.py): the same command set the
editor chat uses, so a generated edit can have everything a chat edit can.
"""

from __future__ import annotations

from pathlib import Path

from .. import history, library
from ..ops import animate as anim
from ..ops import captions as caps
from ..ops import color, cut, fonts, motion, overlays
from ..project import (
    Beat, ClipMeta, EditNotes, Layer, MusicTrack, Preset, Project, Source, TextStyle, VeditError, save, transition_name,
)
from . import commands
from .spec import EditPlan, Op


def captions_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def create_project(pdir: Path, name: str, fmt: str) -> Project:
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "media").mkdir(exist_ok=True)
    (pdir / "exports").mkdir(exist_ok=True)
    p = Project(name=name)
    p.canvas.width, p.canvas.height = library.FORMATS.get(fmt, library.FORMATS["reels"])
    save(pdir, p)
    history.commit(pdir, p, "create project (AI generation)")
    return p


def _presets(names: list[str]) -> list[Preset]:
    out, seen = [], set()
    for n in names:
        if n in anim.PRESETS and n not in seen:
            out.append(Preset(name=n))
            seen.add(n)
    return out


def _describe(o: Op) -> str:
    if o.op == "clip":
        return f"clip {o.file} {o.start:.2f}-{o.end:.2f}s"
    if o.op == "text":
        return f"text '{(o.text or '')[:30]}'"
    if o.op == "scene":
        return f"scene {o.duration or 0:.1f}s {o.fill or ''} '{(o.text or '')[:30]}'".replace("  ", " ")
    if o.op == "transition":
        return f"transition {o.type}" + (f" into clip {o.into + 1}" if o.into is not None else " on all cuts")
    if o.op == "vedit":
        return f"vedit {o.command}"[:120]
    if o.op == "move":
        return f"move {o.type}" + (f" on clip {o.into + 1}" if o.into is not None else f" at {o.start}s" if o.start is not None else "")
    return o.op + (f" {o.file}" if o.file else "")


def execute(plan: EditPlan, pdir: Path, files: dict[str, dict], name: str, fmt: str,
            log: dict | None = None, brief: str = "", target_shot_s: float | None = None, style: str = "",
            refs: list[int] | None = None) -> dict:
    """files: fid -> upload meta (needs 'path', 'kind', probe info). log: the shot log (for segment quality).
    Returns a report."""
    create_project(pdir, name, fmt)
    quality = {sg["id"]: sg.get("quality") for f in (log or {}).values() for sg in f.get("segments", [])}
    roles = {b.id: b.role for b in plan.beats}
    used = sorted({o.file for o in plan.ops if o.file})
    sid: dict[str, str] = {}
    with history.transaction(pdir, "AI: import footage") as tx:
        p = tx.project
        for fid in used:
            f = files[fid]
            if f["kind"] == "image":
                continue
            s = p.new_id("s")
            p.sources[s] = Source(path=f["path"], duration=f["duration"], width=f.get("width"), height=f.get("height"),
                                  fps=f.get("fps"), has_audio=f.get("has_audio", True), has_video=f["kind"] == "video")
            sid[fid] = s
    with history.transaction(pdir, "AI: edit notes (beats, rationale)") as tx:
        tx.project.notes = EditNotes(rationale=plan.rationale or "", brief=brief[:500], target_shot_s=target_shot_s, style=style[:300],
                                     refs=list(refs or []), beats=[Beat(id=b.id, role=b.role, purpose=b.purpose) for b in plan.beats])
    report: dict = {"applied": [], "skipped": [], "plan_problems": list(plan.problems)}
    clip_ids: list[str] = []   # footage clips, in order (@C1..)
    seq_ids: list[str] = []    # every element on the video track, footage and scenes (transition `into`)
    text_ids: list[str] = []
    scene_ids: list[tuple[str, str | None]] = []  # (canvas layer, text layer) per scene (@S1, @S1_text)
    # the video track (footage + scenes) in one step: a scene is a gap, and a gap left at the end of the track would
    # be dropped between steps, so whatever follows it must land in the same save
    seq = [o for o in plan.ops if o.op in ("clip", "scene")]
    with history.transaction(pdir, f"AI: {sum(o.op == 'clip' for o in seq)} clips"
                             + (f", {sum(o.op == 'scene' for o in seq)} scenes" if any(o.op == "scene" for o in seq) else "")) as tx:
        for o in seq:
            try:
                if o.op == "scene":
                    scene_ids.append(_scene(tx.project, pdir, o, roles, seq_ids))
                else:
                    _apply(tx.project, pdir, o, sid, files, clip_ids, quality, roles, text_ids)
                    seq_ids.append(clip_ids[-1])
                report["applied"].append(_describe(o))
            except (VeditError, ValueError, KeyError) as e:
                report["skipped"].append(f"{_describe(o)}: {e}")
    # then overlays / transitions, then signature moves (they split clips), then captions;
    # vedit commands after the colour grade, so they can build on (or change) everything the plan made
    ordered = [o for o in plan.ops if o.op not in ("clip", "scene", "captions", "move", "vedit")] + \
              [o for o in plan.ops if o.op == "move"][:max_moves(plan)] + [o for o in plan.ops if o.op == "captions"]
    for o in ordered:
        try:
            if o.op == "captions":
                if not captions_available():
                    raise VeditError("auto-captions need the captions extra (uv sync --extra captions)")
                _captions(pdir, o)
            else:
                with history.transaction(pdir, "AI: " + _describe(o)) as tx:
                    _apply(tx.project, pdir, o, sid, files, clip_ids, quality, roles, text_ids, seq_ids)
            report["applied"].append(_describe(o))
        except (VeditError, ValueError, KeyError) as e:
            report["skipped"].append(f"{_describe(o)}: {e}")
    if plan.grade:
        try:
            report["applied"].append(grade(pdir, plan.grade, clip_ids))
        except (VeditError, ValueError, KeyError) as e:
            report["skipped"].append(f"colour grade: {e}")
    cmds = [o for o in plan.ops if o.op == "vedit"]
    if cmds:
        refs = {f"C{i}": c for i, c in enumerate(clip_ids, 1)} | {f"T{i}": t for i, t in enumerate(text_ids, 1)}
        for i, (bg, txt) in enumerate(scene_ids, 1):
            refs[f"S{i}"] = bg
            if txt:
                refs[f"S{i}_text"] = txt
        from ..project import load

        cap = next((l.id for l in load(pdir).layers if l.type == "captions"), None)
        if cap:
            refs["captions"] = cap
        commands.execute(pdir, cmds, files, refs, report)
    return report


def max_moves(plan: EditPlan, length_s: float | None = None) -> int:
    """Signature moves per video: 2-4 per 30 s (CLAUDE.md), at least 2."""
    if length_s is None:
        length_s = sum(((o.end or 0) - (o.start or 0)) / (o.speed or 1.0) for o in plan.ops if o.op == "clip")
    return max(2, round(length_s * 4 / 30))


def grade(pdir: Path, g: dict, clip_ids: list[str] | None = None) -> str:
    """Match the footage clips to the reference's measured colour (or to each other), then apply the look.
    Scenes (gap clips under a canvas) are left alone."""
    with history.transaction(pdir, f"AI: colour grade ({g.get('look') or 'natural'})") as tx:
        p = tx.project
        have = {c.id for c in p.clips}
        ids = [c for c in (clip_ids or []) if c in have] or None
        r = color.match_clips(p, pdir, target=g.get("target"), strength=float(g.get("strength", 0.7)),
                              label="reference" if g.get("target") else None, clip_ids=ids)
        color.set_look(p, g.get("look") or "natural", float(g.get("intensity", 0.8)), clip_ids=ids)
    return f"colour: matched {len(r['clips'])} clips to {r['matched_to']}, look {g.get('look') or 'natural'}"


def _apply(p: Project, pdir: Path, o: Op, sid: dict[str, str], files: dict[str, dict], clip_ids: list[str],
           quality: dict | None = None, roles: dict | None = None, text_ids: list[str] | None = None,
           seq_ids: list[str] | None = None) -> None:
    if o.op == "clip":
        c = cut.append_clip(p, sid[o.file], o.start or 0.0, o.end)
        c.speed = o.speed or 1.0
        if o.audio:
            c.volume = {"voice": 1.0, "ambient": 0.35, "mute": 0.0}[o.audio]
        if o.why or o.beat or o.segment or o.audio:
            c.meta = ClipMeta(why=(o.why or "").strip()[:200] or None, beat=o.beat, role=(roles or {}).get(o.beat),
                              segment=o.segment, quality=(quality or {}).get(o.segment), audio=o.audio)
        if o.zoom or o.zoom_to:
            c.zoom, c.zoom_to = o.zoom or 1.0, o.zoom_to
        clip_ids.append(c.id)
    elif o.op == "text":
        st = TextStyle(size=o.size or 88, color=o.color or "#FFFFFF", uppercase=bool(o.uppercase), bg_color=o.bg,
                       stroke_width=0 if o.bg else 6, font=o.font if o.font in fonts.FONTS else None, max_width=820,
                       letter_spacing=o.letter_spacing or 0.0)
        tid = p.new_id("t")
        if text_ids is not None:
            text_ids.append(tid)
        p.layers.append(Layer(id=tid, type="text", text=(o.text or "").strip()[:200] or "…", start=o.start or 0.0, end=o.end,
                              x=o.x or "48%", y=o.y or "25%", style=st, presets=_presets(o.presets),
                              why=(o.why or "").strip()[:200] or None))
    elif o.op == "image":
        p.layers.append(Layer(id=p.new_id("img"), type="image", image=files[o.file]["path"], start=o.start or 0.0, end=o.end,
                              x=o.x or "50%", y=o.y or "50%", width=o.width or int(p.canvas.width * 0.5), presets=_presets(o.presets)))
    elif o.op == "transition":
        kind = transition_name(o.type or "fade")
        order = seq_ids or [c.id for c in p.clips]
        if o.into is not None and not 0 < o.into < len(order):
            raise ValueError(f"no cut before element {o.into + 1}")
        target = None if o.into is None else order[o.into]
        cut.set_transition(p, kind, o.duration or 0.3, target)
    elif o.op == "jumpcut_zoom":
        cut.jumpcut_zoom(p, min(1.3, max(1.02, o.level or 1.12)))
    elif o.op == "fit":
        if o.type not in ("cover", "contain", "blur"):
            raise ValueError(f"fit must be cover, contain or blur, not {o.type}")
        for c in p.clips:
            c.fit = o.type  # type: ignore[assignment]
    elif o.op == "move":
        from ..ops import moves

        clip = clip_ids[o.into] if o.into is not None and 0 <= o.into < len(clip_ids) else None
        moves.apply(p, o.type or "", clip=clip, at=o.start, text=o.text)
    elif o.op == "music":
        s = sid[o.file]
        p.music.append(MusicTrack(id=p.new_id("m"), source=s, in_=o.offset or 0.0, volume=o.volume if o.volume is not None else 0.6,
                                  duck=bool(o.duck), loop=True, fade_in=0.2, fade_out=1.0))
    else:
        raise ValueError(f"unknown op {o.op}")


def _scene(p: Project, pdir: Path, o: Op, roles: dict | None, seq_ids: list[str]) -> tuple[str, str | None]:
    """A graphic scene on the video track: a gap of `duration`, a full-screen canvas over it and, if it has text,
    kinetic type (word by word). Returns (canvas layer id, text layer id)."""
    dur = float(o.duration or 1.8)
    t0 = p.clips_end
    gaps = cut._gap_clips(p, cut.gap_source(p, pdir), dur)
    gaps[0].meta = ClipMeta(why=(o.why or "").strip()[:200] or None, beat=o.beat, role=(roles or {}).get(o.beat))
    p.clips.extend(gaps)
    seq_ids.append(gaps[0].id)
    W, H = p.canvas.width, p.canvas.height
    bg = overlays.add_shape(p, "rect", t0, t0 + dur, width=W, height=H, radius=0, x="50%", y="50%")
    fill = (o.fill or "#FFFFFF").strip()
    overlays.set_fields(bg, {"fill_gradient": fill} if "," in fill else {"fill": fill})
    bg.why = (o.why or "").strip()[:200] or None
    tid = None
    if (o.text or "").strip():
        st = TextStyle(size=o.size or 96, color=o.color or "#111111", uppercase=bool(o.uppercase), stroke_width=0, shadow=False,
                       font=o.font if o.font in fonts.FONTS else None, max_width=int(W * 0.8),
                       letter_spacing=o.letter_spacing or 0.0)
        tid = p.new_id("t")
        layer = Layer(id=tid, type="text", text=o.text.strip()[:200], start=round(t0 + 0.1, 3), end=round(t0 + dur, 3),
                      x=o.x or "50%", y=o.y or "46%", style=st, presets=_presets(o.presets), why=bg.why)
        motion.set_fields(layer, {"split": "word", "split_anim": o.type if o.type in motion.SPLIT_ANIMS else "pop",
                                  "stagger": 0.09, "split_dur": 0.35, "split_ease": "snap"})
        p.layers.append(layer)
    return bg.id, tid


def _captions(pdir: Path, o: Op) -> None:
    from ..project import load
    from ..render.runner import render_preview
    from .. import jobs

    with jobs.render_lock:
        res = render_preview(load(pdir), pdir, progress=False)
    words = caps.transcribe(Path(res["output"]), "small", None)
    if not words:
        raise VeditError("no speech found for captions")
    style = o.type if o.type in caps.CAPTION_STYLES else "bold"
    with history.transaction(pdir, f"AI: captions ({style})") as tx:
        caps.add_captions(tx.project, words, style, o.y or "72%", max(1, min(6, o.words or 3)))

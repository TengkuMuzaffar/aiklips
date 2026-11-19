"""`vedit` command line: every edit is one command, every command is undoable.

Use --json for machine-readable output (Claude Code should always pass it).
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Optional

import typer

from . import history, media
from .ops import animate as anim
from .ops import captions as caps
from .ops import cut
from .ops.silence import find_silences, silence_cut
from .project import (
    ACTIVE_FILE, PROJECT_FILE, Effect, Keyframe, Layer, MusicTrack, Preset, Project, Source, TextStyle, VeditError,
    find_project_dir, load, save, store_media_path,
)

app = typer.Typer(add_completion=False, no_args_is_help=True, pretty_exceptions_enable=False,
                  help="aiklips: a video editor you drive with Claude Code. All edits are non-destructive and undoable.")
text_app = typer.Typer(help="Text overlays")
image_app = typer.Typer(help="Image overlays (logos, stickers)")
layer_app = typer.Typer(help="List / change / remove overlay layers")
anim_app = typer.Typer(help="Animate layers with presets or custom keyframes")
cap_app = typer.Typer(help="Captions (SRT import or offline auto-captions)")
bg_app = typer.Typer(help="Background removal / replacement")
face_app = typer.Typer(help="Face mesh / AR effects (needs: uv sync --extra ai)")
music_app = typer.Typer(help="Background music")
mask_app = typer.Typer(help="Hide a region (text, logo, label, plate): remove it (fill with the background) or blur / pixelate / fill, tracked")
focus_app = typer.Typer(help="Object focus: spotlight an object or make the camera follow it")
color_app = typer.Typer(help="Colour: match clips to each other or a reference, grade with looks, LUTs")
sound_app = typer.Typer(help="Footage sound vs music: what each clip's audio is (speech / background / silent), and sensible defaults")
fx_app = typer.Typer(help="Signature moves (punch-in, speed ramp, freeze + title, flash, whip, keyword pop, before/after, "
                          "zoom-out reveal) and clip filters (blur, sharpen, glow, vignette, grain, RGB split, film, VHS, shake...)")
overlay_app = typer.Typer(help="Video overlays (picture-in-picture, b-roll on top, green screen) and the overlay library "
                               "(light leaks, dust, grain, bokeh, VHS lines, frame, stickers)")
version_app = typer.Typer(help="Named versions: bookmark the edit as it is now, jump back to it later")
template_app = typer.Typer(help="Templates: save a group of overlay layers (lower third, end card...) and reuse it in any project")
adjust_app = typer.Typer(help="Adjustment layers: filters + colour over a time range, on everything under them")
proxy_app = typer.Typer(help="Proxy media: small copies of big (4K) sources so previews are fast; exports use the originals")
track_app = typer.Typer(help="Timeline tracks: list them, hide / show the main track or an overlay track")
marker_app = typer.Typer(help="Markers on the timeline (notes, beats) and YouTube chapters from them")
shape_app = typer.Typer(help="Shapes: boxes, circles, triangles, stars, hearts, bubbles, lines, arrows (callouts, "
                             "backgrounds, motion-design elements)")
scene_app = typer.Typer(help="Design projects: scenes (pages) played back to back; their elements move along with them")
iso_app = typer.Typer(help="Isometric worlds: ready-made scenes or your own, from a library of 60+ items (`iso items`)")
iso_item_app = typer.Typer(help="The items of an iso world: add / set (move, turn, recolour, label, settings) / remove")
iso_app.add_typer(iso_item_app, name="item")
group_app = typer.Typer(help="Layer groups: an effect's layers as one row on the timeline (move / rename / ungroup / delete together)")
style_app = typer.Typer(help="Motion styles: a look as numbers (easing, timing, palette, fonts) the AI follows and the audit checks")
motion_app = typer.Typer(help="Motion graphics on the tracks: kinetic text (by word / letter / line), draw-on, morph, paths")
chart_app = typer.Typer(help="Charts that grow in: bar, hbar, line, donut (values roll up with the bars)")
counter_app = typer.Typer(help="Numbers that count up: 0 -> 1,250,000 with prefix / suffix / decimals")
safe_app = typer.Typer(help="Safe zone of the project's platform (`vedit platform`): check where every layer is, fit "
                             "the ones under the app's buttons / caption bar back inside")
look_app = typer.Typer(help="Reusable text looks (font + colours + outline + animation): list / apply / save / delete")
for name, sub in [("text", text_app), ("image", image_app), ("layer", layer_app), ("animate", anim_app),
                  ("captions", cap_app), ("bg", bg_app), ("face", face_app), ("music", music_app),
                  ("mask", mask_app), ("focus", focus_app), ("color", color_app), ("fx", fx_app), ("sound", sound_app),
                  ("overlay", overlay_app), ("shape", shape_app), ("marker", marker_app), ("proxy", proxy_app), ("adjust", adjust_app),
                  ("version", version_app), ("template", template_app), ("track", track_app), ("scene", scene_app),
                  ("safe", safe_app), ("motion", motion_app), ("chart", chart_app), ("counter", counter_app),
                  ("style", style_app), ("group", group_app), ("iso", iso_app)]:
    app.add_typer(sub, name=name)
text_app.add_typer(look_app, name="look")

STATE: dict[str, Any] = {"json": False, "project": None}


def _print_version() -> None:
    from . import __version__

    print(__version__)
    raise typer.Exit()


@app.callback()
def _global(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Project folder (default: active project)"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
    show_version: bool = typer.Option(False, "--version", help="Print the version and exit", is_eager=True,
                                      callback=lambda v: _print_version() if v else None),
):
    STATE["project"] = project
    STATE["json"] = as_json


def pdir() -> Path:
    return find_project_dir(STATE["project"])


def emit(data: dict, human: str | None = None) -> None:
    if STATE["json"]:
        print(json.dumps({"ok": True, **data}, default=str))
    else:
        print(human if human is not None else json.dumps(data, indent=2, default=str))


def edit(message: str):
    return history.transaction(pdir(), message)


def fmt_t(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:06.3f}"


def _summary(p: Project) -> str:
    return f"timeline {fmt_t(p.duration)} | {len(p.clips)} clips | {len(p.layers)} layers"


# ---- project -------------------------------------------------------------------

@app.command()
def init(
    folder: str,
    name: Optional[str] = None,
    width: int = 1080,
    height: int = 1920,
    fps: float = 30,
):
    """Create a new project folder (default canvas 1080x1920 @30fps for Reels/TikTok)."""
    d = Path(folder).expanduser().resolve()
    if (d / PROJECT_FILE).exists():
        raise VeditError(f"{d} already has a project.json")
    d.mkdir(parents=True, exist_ok=True)
    (d / "media").mkdir(exist_ok=True)
    (d / "exports").mkdir(exist_ok=True)
    proj = Project(name=name or d.name)
    proj.canvas.width, proj.canvas.height, proj.canvas.fps = width, height, fps
    save(d, proj)
    history.commit(d, proj, "create project")
    from . import library

    library.register(d)
    (Path.cwd() / ACTIVE_FILE).write_text(str(d))
    emit({"project": str(d)}, f"Created project {d} (now active)")


@app.command()
def use(folder: str):
    """Set the active project for commands run from this folder."""
    d = Path(folder).expanduser().resolve()
    if not (d / PROJECT_FILE).exists():
        raise VeditError(f"No project.json in {d}")
    (Path.cwd() / ACTIVE_FILE).write_text(str(d))
    emit({"project": str(d)}, f"Active project: {d}")


@app.command("import")
def import_(
    files: list[str],
    append: bool = typer.Option(True, help="Append to the end of the timeline"),
    at: Optional[int] = typer.Option(None, help="Insert at clip index instead of appending"),
    start: float = typer.Option(0.0, help="Source in-point (seconds)"),
    end: Optional[float] = typer.Option(None, help="Source out-point (seconds)"),
    copy: bool = typer.Option(False, help="Copy the file into the project's media/ folder"),
):
    """Import video/audio/image files as sources (and add them to the timeline)."""
    d = pdir()
    with edit("import " + ", ".join(Path(f).name for f in files)) as tx:
        p = tx.project
        added = []
        for i, f in enumerate(files):
            path = Path(f).expanduser().resolve()
            info = media.probe(path)
            if copy:
                dest = d / "media" / path.name
                if not dest.exists():
                    shutil.copy2(path, dest)
                path = dest
            sid = p.new_id("s")
            p.sources[sid] = Source(path=store_media_path(d, path), **info)
            clip = None
            if append and info["has_video"]:
                clip = cut.append_clip(p, sid, start, end, None if at is None else at + i)
            added.append({"source": sid, "clip": clip.id if clip else None, **info})
    emit({"added": added, "duration": p.duration},
         "\n".join(f"+ {a['source']} {Path(files[i]).name} ({a['duration']}s) -> clip {a['clip']}" for i, a in enumerate(added)) + f"\n{_summary(p)}")


@app.command()
def info():
    """Show sources, the timeline (clip ranges), layers and music."""
    d = pdir()
    p = load(d)
    starts = p.clip_starts()
    clips = []
    for c, s in zip(p.clips, starts):
        clips.append({
            "id": c.id, "source": c.source, "timeline": [round(s, 3), round(s + c.duration, 3)],
            "source_range": [c.in_, c.out], "speed": c.speed, "fit": c.fit, "zoom": c.zoom, "zoom_to": c.zoom_to,
            "effects": [_fx_label(e) for e in c.effects],
            **({"gap": True} if c.gap else {}),
            **({"transition_in": c.transition.model_dump()} if c.transition else {}),
        })
    layers = []
    for l in p.layers:
        row = {"id": l.id, "type": l.type, "track": l.track, "start": l.start, "end": l.end,
               "text": l.text or (" ".join(w.text for w in l.words[:8]) if l.words else l.image),
               "presets": [x.name for x in l.presets], "keyframes": len(l.keyframes)}
        if l.hidden:
            row["hidden"] = True
        if l.on_video:
            row["on_video_track"] = True
        if l.group:
            row["group"] = l.group
        if l.type == "video":
            row.update(source=l.source, src_in=l.src_in, fit=l.fit, flip_h=l.flip_h, flip_v=l.flip_v)
        if l.type in ("video", "adjust"):
            row["effects"] = [_fx_label(e) for e in l.effects]
            row["grade"] = l.grade.model_dump(exclude_none=True) if l.grade else None
        layers.append(row)
    data = {"project": str(d), "name": p.name, "canvas": p.canvas.model_dump(), "duration": round(p.duration, 3),
            **({"main_hidden": True} if p.main_hidden else {}),
            **({"video_track_height": p.main_z} if p.main_z else {}),
            "sources": {k: v.model_dump() for k, v in p.sources.items()}, "clips": clips, "layers": layers,
            **({"groups": __import__("vedit.ops.groups", fromlist=["summary"]).summary(p)} if p.groups else {}),
            "music": [m.model_dump(by_alias=True) for m in p.music], "history_step": history.head(d)}
    if STATE["json"]:
        return emit(data)
    out = [f"Project {p.name}  ({d})", f"Canvas {p.canvas.width}x{p.canvas.height} @{p.canvas.fps:g}fps   Duration {fmt_t(p.duration)}", "", "Sources:"]
    out += [f"  {k}: {v.path}  {v.duration}s" + (f" {v.width}x{v.height}" if v.has_video else " audio") for k, v in p.sources.items()] or ["  (none)"]
    out += ["", "Timeline:"]
    out += [f"  {c['id']:>4}  {fmt_t(c['timeline'][0])} - {fmt_t(c['timeline'][1])}   " + ("(gap)" if c.get("gap") else f"src {c['source']} {c['source_range'][0]:.2f}-{c['source_range'][1]:.2f}s") +
            f"{'  x' + str(c['speed']) if c['speed'] != 1 else ''}{'  zoom ' + str(c['zoom']) if c['zoom'] != 1 or c['zoom_to'] else ''}{' -> ' + str(c['zoom_to']) if c['zoom_to'] else ''}"
            f"{'  fx:' + ','.join(c['effects']) if c['effects'] else ''}"
            f"{'  <- ' + c['transition_in']['type'] + ' ' + str(c['transition_in']['duration']) + 's' if c.get('transition_in') else ''}" for c in clips] or ["  (empty)"]
    out += ["", "Layers:"]
    out += [f"  {l['id']:>5}  T{l['track']} {l['type']:<8}{' (hidden)' if l.get('hidden') else ''} {l['start']:.2f}-{(l['end'] if l['end'] is not None else p.duration):.2f}s  {str(l['text'] if l['text'] is not None else l.get('source') or '')[:40]!r}"
            f"{'  presets:' + ','.join(l['presets']) if l['presets'] else ''}{'  [' + l['group'] + ']' if l.get('group') else ''}" for l in layers] or ["  (none)"]
    if data.get("groups"):
        out += ["", "Groups (one row each on the timeline; `vedit group move|set|ungroup|remove`):"]
        out += [f"  {g['id']}: {g['name']} {g['start']:.2f}-{g['end']:.2f}s, {len(g['layers'])} layers" for g in data["groups"]]
    if p.music:
        out += ["", "Music:"] + [f"  {m.id}: {m.source} vol {m.volume}{' duck' if m.duck else ''}" for m in p.music]
    print("\n".join(out))


def _fx_label(e) -> str:
    """`blur (fx3)`, with its window when it covers only part of its clip / layer: `blur (fx3) 1.0-2.5s fade 0.3/0.3`."""
    if e.type != "fx":
        return e.type
    s = f"{e.params.get('key')} ({e.params.get('id')})"
    pr = e.params
    if pr.get("start") or pr.get("end") is not None or pr.get("fade_in") or pr.get("fade_out"):
        s += f" {float(pr.get('start') or 0):g}-{'end' if pr.get('end') is None else f'{float(pr['end']):g}'}s"
        if pr.get("fade_in") or pr.get("fade_out"):
            s += f" fade {float(pr.get('fade_in') or 0):g}/{float(pr.get('fade_out') or 0):g}"
    if pr.get("on") is False:
        s += " off"
    return s


@app.command()
def canvas(
    width: Optional[int] = None, height: Optional[int] = None, fps: Optional[float] = None,
    background: Optional[str] = None,
    preset: Optional[str] = typer.Option(None, help="reels | square | landscape"),
):
    """Change the output canvas (default 1080x1920 vertical)."""
    with edit("change canvas") as tx:
        c = tx.project.canvas
        if preset:
            sizes = {"reels": (1080, 1920), "square": (1080, 1080), "landscape": (1920, 1080), "portrait45": (1080, 1350)}
            if preset not in sizes:
                raise VeditError(f"preset must be one of {', '.join(sizes)}")
            c.width, c.height = sizes[preset]
        c.width = width or c.width
        c.height = height or c.height
        c.fps = fps or c.fps
        c.background = background or c.background
    emit({"canvas": c.model_dump()}, f"Canvas {c.width}x{c.height} @{c.fps:g}fps bg {c.background}")


# ---- cutting ---------------------------------------------------------------------

@app.command()
def trim(head: float = typer.Option(0.0, help="Seconds to remove from the start"),
         tail: float = typer.Option(0.0, help="Seconds to remove from the end")):
    """Remove seconds from the start and/or end of the timeline."""
    with edit(f"trim head {head}s tail {tail}s") as tx:
        r = cut.trim(tx.project, head, tail)
    emit(r, f"Trimmed. {_summary(tx.project)}")


@app.command()
def keep(start: float = typer.Option(..., "--from"), end: float = typer.Option(..., "--to")):
    """Keep only the timeline range FROM..TO."""
    with edit(f"keep {start}-{end}s") as tx:
        r = cut.keep_range(tx.project, start, end)
    emit(r, f"Kept {start}-{end}s. {_summary(tx.project)}")


@app.command()
def delete(start: float = typer.Option(..., "--from"), end: float = typer.Option(..., "--to")):
    """Delete the timeline range FROM..TO (everything after moves up)."""
    with edit(f"delete {start}-{end}s") as tx:
        r = cut.delete_range(tx.project, start, end)
    emit(r, f"Deleted {r['removed_seconds']}s. {_summary(tx.project)}")


@app.command()
def split(at: float = typer.Option(..., help="Timeline seconds")):
    """Split the clip under the playhead into two clips."""
    with edit(f"split at {at}s") as tx:
        i = cut.split_at(tx.project, at)
    emit({"index": i, "clips": [c.id for c in tx.project.clips]}, f"Split at {at}s. Clips: {', '.join(c.id for c in tx.project.clips)}")


@app.command()
def move(clip: str, to: int = typer.Option(..., help="New position index (0 = first)")):
    """Reorder a clip on the timeline."""
    with edit(f"move {clip} to {to}") as tx:
        cut.move_clip(tx.project, clip, to)
    emit({"clips": [c.id for c in tx.project.clips]}, "Order: " + ", ".join(c.id for c in tx.project.clips))


@app.command("remove-clip")
def remove_clip(clip: str):
    """Remove one clip from the timeline."""
    with edit(f"remove clip {clip}") as tx:
        r = cut.remove_clip(tx.project, clip)
    emit(r, f"Removed {clip}. {_summary(tx.project)}")


@app.command()
def place(
    item: str = typer.Argument(..., help="A clip id (video track) or a layer id (any other track)"),
    at: Optional[float] = typer.Option(None, help="New start, timeline seconds"),
    end: Optional[float] = typer.Option(None, help="Layers: new end (timeline seconds)"),
    track: Optional[int] = typer.Option(None, help="Layers: track (0 = bottom, higher = drawn on top; tracks below the video track's height are under the video)"),
    new_track: bool = typer.Option(False, "--new-track", help="Layers: open a new track at --track instead of sharing it"),
    src_in: Optional[float] = typer.Option(None, help="Video layers: start this many seconds into the file (trim the head)"),
    under_video: bool = typer.Option(False, "--under-video", help="Layers: with --new-track at the video track's height, "
                                                                  "open it under the video instead of over it"),
    video_track: bool = typer.Option(False, "--video-track", help="Layers: onto the video track itself (in a gap or "
                                                                  "after the clips; not over a clip)"),
):
    """Move something on the timeline, like dragging it. A clip placed past the end of the clip before it leaves a gap
    (background colour, or the tracks under the video) before it; placed over another clip it goes before / after that
    clip. A layer gets new times and / or a track (a busy track bumps it to a new track just above). Something must
    stay at 0s: a move that leaves the start empty is refused."""
    from .ops import tracks

    with edit(f"place {item}") as tx, tracks.zero_guard(tx.project):
        p = tx.project
        if any(c.id == item for c in p.clips):
            if at is None:
                raise VeditError("Give --at for a clip")
            t = cut.place_clip(p, pdir(), item, at)
            r = {"clip": item, "start": round(t, 3)}
            msg = f"{item} at {t:.2f}s"
        else:
            if video_track:
                l = tracks.put_on_video_track(p, item, at, tracks._KEEP if end is None else end)
            else:
                l = tracks.place_layer(p, item, at, tracks._KEEP if end is None else end, track, new_track, src_in, under_video)
            r = {"layer": l.id, "track": l.track, "start": l.start, "end": l.end}
            msg = f"{l.id}: track {l.track}, {l.start:.2f}-{'end' if l.end is None else f'{l.end:.2f}'}s"
    emit(r, msg + f". {_summary(tx.project)}")


@app.command("to-overlay")
def to_overlay(clip: str, at: float = typer.Option(0.0, help="Start on the timeline after the clip leaves the video track"),
               track: int = typer.Option(0, help="Track (0 = bottom)"),
               share_track: bool = typer.Option(False, "--share-track", help="Join --track instead of opening a new one"),
               close_gap: bool = typer.Option(False, "--close-gap", help="Close up the video track (ripple) instead of "
                                                                         "leaving a gap where the clip was")):
    """Move a clip from the video track onto another track as a full-frame video layer (keeps sound, colour, filters;
    text on it moves with it). The video track keeps its timing (a gap where the clip was) unless --close-gap. Use for
    b-roll over a talking head, or cutaways."""
    from .ops import tracks

    with edit(f"{clip} to overlay") as tx, tracks.zero_guard(tx.project):
        l = tracks.clip_to_overlay(tx.project, clip, at, track, not share_track, None if close_gap else pdir())
    emit({"layer": l.id, "track": l.track, "start": l.start, "end": l.end}, f"{clip} -> overlay {l.id} on track {l.track}. {_summary(tx.project)}")


@app.command("to-clip")
def to_clip(layer: str, index: Optional[int] = typer.Option(None, help="Position among the clips (default: the end)"),
            at: Optional[float] = typer.Option(None, help="Timeline time instead of --index (a gap before it on empty space)")):
    """Move a video layer onto the video track as a clip (keeps its trim, sound, colour and filters)."""
    from .ops import tracks

    with edit(f"{layer} to video track") as tx, tracks.zero_guard(tx.project):
        c = tracks.overlay_to_clip(tx.project, layer, index, at, pdir())
    emit({"clip": c.id}, f"overlay {layer} -> clip {c.id}. {_summary(tx.project)}")


@app.command("trim-to")
def trim_to(at: float = typer.Option(..., help="Timeline seconds"), side: str = typer.Option("start", help="start | end")):
    """Trim the clip under --at so it starts (side=start) or ends (side=end) there; later clips move up."""
    from .ops import editing

    with edit(f"trim {side} to {at:.2f}s") as tx:
        c = editing.trim_to(tx.project, at, side)
    emit({"clip": c.id}, f"{c.id}: trimmed {side} to {at:.2f}s. {_summary(tx.project)}")


@app.command()
def paste(src: str = typer.Option(..., "--from", help="Clip or layer to copy from"),
          to: list[str] = typer.Option(..., help="Target clip / layer (repeatable)"),
          group: list[str] = typer.Option(..., help="Clips: colour|effects|framing|sound|speed. Layers: style|frame|transform|animation (repeatable)")):
    """Paste attributes from one clip / layer onto others (like Cmd+C / Cmd+V in the editor)."""
    from .ops import editing

    with edit(f"paste {','.join(group)} from {src}") as tx:
        kind = "clip" if any(c.id == src for c in tx.project.clips) else "layer"
        n = editing.paste_attributes(tx.project, kind, src, list(to), list(group))
    emit({"changed": n, "kind": kind}, f"Pasted {', '.join(group)} from {src} onto {n} {kind}(s)")


@track_app.command("video")
def track_video(to: str = typer.Argument(..., help="up | down | top | bottom | a height (0 = bottom)")):
    """Move the video track (the clips) up or down among the other tracks. Tracks under it show through where the
    video is transparent: gaps, letterbox bars (fit contain), a keyed green screen (`bg remove --mode chroma --replace
    none`). E.g. a colour screen behind green-screen footage: `shape add rect --full --fill #hex --track 0 --new-track`,
    then `track video up`."""
    from .ops import tracks

    with edit(f"video track {to}") as tx:
        z = tracks.set_video_track(tx.project, to)
    emit({"height": z}, f"Video track at height {z}: {z} track(s) under it")


@track_app.command("list")
def track_list():
    """All tracks bottom to top (the video track at its height) with what is on each."""
    from .ops import tracks

    rows = tracks.track_rows(load(pdir()))
    emit({"tracks": rows}, "\n".join(f"  {str(r['track']):>4}{' (hidden)' if r['hidden'] else ''}: {', '.join(r['items']) or '-'}" for r in rows))


def _track_vis(track: str, visible: bool) -> None:
    from .ops import tracks

    with edit(f"{'show' if visible else 'hide'} track {track}") as tx:
        msg = tracks.set_track_visible(tx.project, track, visible)
    emit({"track": track, "visible": visible}, msg)


@track_app.command("hide")
def track_hide(track: str = typer.Argument(..., help="'video' (or 'main') or a track number (0 = bottom)")):
    """Hide a track (not drawn in the preview or export; its sound still plays)."""
    _track_vis(track, False)


@track_app.command("show")
def track_show(track: str = typer.Argument(..., help="'video' (or 'main') or a track number (0 = bottom)")):
    """Show a hidden track again."""
    _track_vis(track, True)


@app.command()
def speed(
    factor: float,
    clip: Optional[str] = None,
    start: Optional[float] = typer.Option(None, "--from"),
    end: Optional[float] = typer.Option(None, "--to"),
):
    """Change speed (2 = 2x faster, 0.5 = slow-mo) for a clip, a range, or everything."""
    with edit(f"speed x{factor}" + (f" {clip}" if clip else "")) as tx:
        p = tx.project
        if clip:
            r = cut.set_clip_speed(p, clip, factor)
        elif start is not None or end is not None:
            r = cut.set_speed_range(p, start or 0.0, end if end is not None else p.duration, factor)
        else:
            r = cut.set_speed_range(p, 0, p.duration, factor)
    emit(r, f"Speed x{factor}. {_summary(p)}")


@app.command()
def volume(level: float, clip: Optional[str] = None):
    """Clip audio volume (1 = original, 0 = mute)."""
    with edit(f"volume {level}" + (f" {clip}" if clip else "")) as tx:
        targets = [tx.project.clip(clip)] if clip else tx.project.clips
        for c in targets:
            c.volume = level
    emit({"clips": [c.id for c in targets]}, f"Volume {level} on {len(targets)} clip(s)")


@app.command()
def fit(
    mode: str = typer.Argument(..., help="cover (fill+crop) | contain (letterbox) | blur (blurred background)"),
    clip: Optional[str] = None,
    focus_x: Optional[float] = typer.Option(None, help="0=left .. 1=right crop focus"),
    focus_y: Optional[float] = typer.Option(None, help="0=top .. 1=bottom crop focus"),
):
    """How clips fill the vertical canvas (reframing)."""
    if mode not in ("cover", "contain", "blur"):
        raise VeditError("mode must be cover, contain or blur")
    with edit(f"fit {mode}" + (f" {clip}" if clip else "")) as tx:
        targets = [tx.project.clip(clip)] if clip else tx.project.clips
        for c in targets:
            c.fit = mode  # type: ignore[assignment]
            if focus_x is not None:
                c.focus_x = focus_x
            if focus_y is not None:
                c.focus_y = focus_y
    emit({"clips": [c.id for c in targets]}, f"Fit {mode} on {len(targets)} clip(s)")


@app.command()
def zoom(level: float, clip: Optional[str] = None, to: Optional[float] = typer.Option(None, help="Animate zoom to this level (Ken Burns)")):
    """Zoom into a clip (1.2 = 20% punch-in). Use --to for a slow animated zoom."""
    with edit(f"zoom {level}" + (f" {clip}" if clip else "")) as tx:
        targets = [tx.project.clip(clip)] if clip else tx.project.clips
        for c in targets:
            c.zoom, c.zoom_to = level, to
    emit({"clips": [c.id for c in targets]}, f"Zoom {level}{' -> ' + str(to) if to else ''} on {len(targets)} clip(s)")


@app.command("jumpcut-zoom")
def jumpcut_zoom(level: float = typer.Option(1.12, "--zoom")):
    """Punch in on every other clip so jump cuts look intentional (use after silence-cut)."""
    with edit(f"jump-cut zoom {level}") as tx:
        changed = cut.jumpcut_zoom(tx.project, level)
    emit({"zoomed": changed}, f"Zoomed {len(changed)} clips to {level}")


@app.command()
def transition(
    kind: str = typer.Argument(..., help="fade | fadeblack | fadewhite | slideleft | wipeleft | zoomin | hblur (whip) | circleopen | "
                                         "pixelize | ... or 'none' to remove. `vedit transition list` shows all"),
    clip: Optional[str] = typer.Option(None, help="Clip the transition leads INTO (default: every cut)"),
    duration: float = typer.Option(0.5, help="Seconds (blend is centred on the cut)"),
):
    """Animated transition between clips. Timeline length and overlay times don't change."""
    from .project import TRANSITION_ALIASES, TRANSITIONS

    if kind == "list":
        return emit({"transitions": list(TRANSITIONS), "aliases": TRANSITION_ALIASES},
                    "Transitions: " + ", ".join(TRANSITIONS) + "\nAliases: " + ", ".join(f"{k}->{v}" for k, v in TRANSITION_ALIASES.items()))
    with edit(f"transition {kind}" + (f" into {clip}" if clip else " on all cuts")) as tx:
        if kind in ("none", "cut", "clear", "off"):
            ids = cut.clear_transition(tx.project, clip)
        else:
            ids = cut.set_transition(tx.project, kind, duration, clip)
    emit({"clips": ids, "transition": kind, "duration": duration}, f"Transition {kind} into {', '.join(ids)}")


@app.command("silence-cut")
def silence_cut_cmd(
    threshold: float = typer.Option(-35.0, help="Silence level in dB (higher = more aggressive, e.g. -30)"),
    min_silence: float = typer.Option(0.5, "--min", help="Only cut pauses longer than this (seconds)"),
    pad: float = typer.Option(0.12, help="Keep this much breathing room around speech (seconds)"),
    dry_run: bool = typer.Option(False, help="Only list the silent ranges"),
):
    """Remove pauses automatically (waveform-based, no AI)."""
    d = pdir()
    if dry_run:
        p = load(d)
        r = find_silences(p, d, threshold, min_silence, pad)
        return emit({"ranges": r, "total": round(sum(b - a for a, b in r), 3)},
                    "\n".join(f"  {a:.2f}-{b:.2f}s ({b - a:.2f}s)" for a, b in r) or "No silences found")
    with edit(f"silence cut ({threshold}dB, >{min_silence}s)") as tx:
        r = silence_cut(tx.project, d, threshold, min_silence, pad)
        if not r["cuts"]:
            tx.cancelled = True
    emit(r, f"Removed {r['cuts']} pauses ({r['removed_seconds']}s). {r['before']}s -> {r['after']}s")


# ---- layers: text / image ----------------------------------------------------------

def _style_from(style: TextStyle, **kw) -> TextStyle:
    data = style.model_dump()
    data.update({k: v for k, v in kw.items() if v is not None})
    return TextStyle(**data)


def _presets(names: list[str] | None) -> list[Preset]:
    out = []
    for n in names or []:
        name, _, dur = n.partition(":")
        p = Preset(name=name, duration=float(dur) if dur else None)
        anim.validate_preset(p)
        out.append(p)
    return out


def _gradient(value: str | None) -> list[str] | None:
    if value is None:
        return None
    cols = [c.strip() for c in value.split(",") if c.strip()]
    return cols or []


@text_app.command("add")
def text_add(
    text: str,
    start: float = 0.0, end: Optional[float] = None,
    x: str = "50%", y: str = "30%",
    look: Optional[str] = typer.Option(None, help="Text look (font, colours, animation): `vedit text look list`"),
    size: Optional[int] = None, color: Optional[str] = None,
    font: Optional[str] = typer.Option(None, help="Bundled font key (`vedit fonts`), a font name, or a .ttf path"),
    stroke: Optional[str] = typer.Option(None, help="Outline colour"), stroke_width: Optional[int] = None,
    bg: Optional[str] = typer.Option(None, help="Background box colour, e.g. '#000000B0'"),
    max_width: Optional[int] = None, uppercase: Optional[bool] = None,
    letter_spacing: Optional[float] = typer.Option(None, help="Extra px between letters (negative = tighter)"),
    glow: Optional[str] = typer.Option(None, help="Glow colour behind the letters, e.g. '#FF2BD6'"),
    gradient: Optional[str] = typer.Option(None, help="Fill gradient top->bottom, e.g. '#FFE600,#FF6A00'"),
    preset: Optional[list[str]] = typer.Option(None, help="Animation preset, repeatable: pop_in, fade_out:0.6 .. (overrides the look's)"),
    bold: Optional[bool] = None, italic: Optional[bool] = None, underline: Optional[bool] = None, strike: Optional[bool] = None,
    list_style: Optional[str] = typer.Option(None, "--list", help="none | bullet | number (each line an item)"),
    align: Optional[str] = typer.Option(None, help="left | center | right"),
    line_spacing: Optional[float] = None,
):
    """Add a text overlay (titles, hooks, CTAs)."""
    from .ops import text_looks

    kw = dict(size=size, color=color, font=font, stroke_color=stroke, stroke_width=stroke_width, bg_color=bg, max_width=max_width,
              uppercase=uppercase, letter_spacing=letter_spacing, glow_color=glow, gradient=_gradient(gradient),
              bold=bold, italic=italic, underline=underline, strike=strike, list_style=list_style, align=align,
              line_spacing=line_spacing)
    with edit(f"add text '{text[:30]}'" + (f" ({look})" if look else "")) as tx:
        p = tx.project
        layer = Layer(id=p.new_id("t"), type="text", text=text, start=start, end=end, x=x, y=y)
        if look:
            text_looks.apply(layer, look)
            layer.style = text_looks.style_for(look, **kw)
        else:
            layer.style = _style_from(TextStyle(), **kw)
        if preset:
            layer.presets = _presets(preset)
        p.layers.append(layer)
    emit({"layer": layer.id, "look": look}, f"Added text layer {layer.id}" + (f" with look {look}" if look else ""))


@text_app.command("edit")
def text_edit(
    layer_id: str, text: Optional[str] = None,
    size: Optional[int] = None, color: Optional[str] = None, font: Optional[str] = None,
    stroke: Optional[str] = None, stroke_width: Optional[int] = None, bg: Optional[str] = None,
    max_width: Optional[int] = None, uppercase: Optional[bool] = None, highlight: Optional[str] = None,
    letter_spacing: Optional[float] = None,
    glow: Optional[str] = typer.Option(None, help="Glow colour, or 'none' to remove"),
    gradient: Optional[str] = typer.Option(None, help="'#top,#bottom' colours, or 'none' to remove"),
    bold: Optional[bool] = None, italic: Optional[bool] = None, underline: Optional[bool] = None, strike: Optional[bool] = None,
    list_style: Optional[str] = typer.Option(None, "--list", help="none | bullet | number (each line an item)"),
    align: Optional[str] = typer.Option(None, help="left | center | right"),
    line_spacing: Optional[float] = None,
):
    """Change the text or style of a text/captions layer."""
    with edit(f"edit text {layer_id}") as tx:
        l = tx.project.layer(layer_id)
        if text is not None:
            l.text = text
        l.style = _style_from(l.style, size=size, color=color, font=font, stroke_color=stroke, stroke_width=stroke_width,
                              bg_color=bg, max_width=max_width, uppercase=uppercase, highlight_color=highlight,
                              letter_spacing=letter_spacing, bold=bold, italic=italic, underline=underline, strike=strike,
                              list=list_style, align=align, line_spacing=line_spacing,
                              glow_color=None if glow in (None, "none") else glow,
                              gradient=None if gradient in (None, "none") else _gradient(gradient))
        if glow == "none":
            l.style.glow_color = None
        if gradient == "none":
            l.style.gradient = None
        if font is not None:
            from .ops.text import find_font
            find_font(font)  # fail now with the list of fonts, not at render time
    emit({"layer": layer_id}, f"Updated {layer_id}")


@look_app.command("list")
def look_list():
    """Built-in and saved text looks."""
    from .ops import text_looks

    rows = text_looks.catalog()
    emit({"looks": rows}, "\n".join(f"  {r['name']:<16} {r['font']:<16} {'+'.join(r['animation']):<28} {r['about']}"
                                    + ("  [saved]" if r["saved"] else "") for r in rows))


@look_app.command("apply")
def look_apply(
    name: str,
    layer: Optional[list[str]] = typer.Option(None, "--layer", "-l", help="Layer id, repeatable (default: every text layer)"),
    captions: bool = typer.Option(False, help="Also restyle captions layers (keeps their size)"),
    animation: bool = typer.Option(True, help="Also replace the layers' animation with the look's"),
):
    """Give layers one look, so every title in the edit matches."""
    from .ops import text_looks

    text_looks.get(name)
    with edit(f"text look {name}") as tx:
        p = tx.project
        targets = [p.layer(i) for i in layer] if layer else [l for l in p.layers if l.type == "text" or (captions and l.type == "captions")]
        if not targets:
            raise VeditError("No text layers to restyle")
        for l in targets:
            if l.type not in ("text", "captions"):
                raise VeditError(f"Layer {l.id} is {l.type}, not text")
            text_looks.apply(l, name, animation=animation)
    emit({"layers": [l.id for l in targets], "look": name}, f"Look {name} on {', '.join(l.id for l in targets)}")


@look_app.command("save")
def look_save(name: str, from_: str = typer.Option(..., "--from", help="Layer to copy the look from"),
              about: str = typer.Option("", help="When to use it (shown in `text look list`)")):
    """Save a layer's style + animation as a reusable look (stored in ~/.aiklips, works in every project)."""
    from .ops import text_looks

    look = text_looks.save(name, load(pdir()).layer(from_), about)
    emit({"look": name, "saved": look}, f"Saved look {name} from {from_}")


@look_app.command("delete")
def look_delete(name: str):
    """Delete a saved look."""
    from .ops import text_looks

    text_looks.delete(name)
    emit({"deleted": name}, f"Deleted look {name}")


@app.command()
def fonts(feel: Optional[str] = typer.Option(None, help="Only this feel: condensed, bold_sans, clean, serif, handwritten, rounded, display, retro, mono"),
          sheet: bool = typer.Option(False, help="Render a sample sheet image (Read it to see the fonts)")):
    """Bundled fonts with their feel and when to use them."""
    from .ops import fonts as font_lib

    rows = [r for r in font_lib.catalog() if not feel or r["feel"] == font_lib.feel_of(feel)]
    data: dict = {"fonts": rows}
    if sheet:
        data["sheet"] = str(_font_sheet(rows))
    emit(data, "\n".join(f"  {r['key']:<18} {r['feel']:<12} {r['use']}" for r in rows) + (f"\nSheet: {data['sheet']}" if sheet else ""))


def _font_sheet(rows: list[dict]) -> Path:
    from PIL import Image, ImageDraw

    from . import library
    from .ops.text import load_font

    W, row_h = 1400, 140
    img = Image.new("RGB", (W, row_h * len(rows) + 20), "#15171c")
    d = ImageDraw.Draw(img)
    label = load_font("poppins", 22)
    for i, r in enumerate(rows):
        y = 10 + i * row_h
        d.text((20, y + 8), f"{r['key']}  ·  {r['feel']}", font=label, fill="#5eead4")
        d.text((20, y + 42), "Stop scrolling: 50% OFF", font=load_font(r["key"], 56), fill="#ffffff")
    out = library.home_dir() / "fonts-sheet.png"
    img.save(out)
    return out


@image_app.command("add")
def image_add(
    path: str, start: float = 0.0, end: Optional[float] = None,
    x: str = "50%", y: str = "50%", width: Optional[int] = typer.Option(None, help="Width in canvas px"),
    preset: Optional[list[str]] = typer.Option(None),
):
    """Add an image overlay (logo, sticker, product shot)."""
    d = pdir()
    src = Path(path).expanduser().resolve()
    if not src.exists():
        raise VeditError(f"Image not found: {src}")
    with edit(f"add image {src.name}") as tx:
        p = tx.project
        layer = Layer(id=p.new_id("img"), type="image", image=store_media_path(d, src), start=start, end=end, x=x, y=y,
                      width=width, presets=_presets(preset))
        p.layers.append(layer)
    emit({"layer": layer.id}, f"Added image layer {layer.id}")


def _frame_opts(blend, radius, border, border_color, circle, chroma, similarity) -> dict:
    out: dict[str, Any] = {}
    for k, v in (("blend", blend), ("radius", radius), ("border_width", border), ("border_color", border_color),
                 ("chroma", chroma), ("chroma_similarity", similarity)):
        if v is not None:
            out[k] = v
    if circle is not None:
        out["mask"] = "circle" if circle else "rect"
    return out


@overlay_app.command("library")
def overlay_library():
    """The built-in overlays (made on first use, then cached)."""
    from .ops import overlays

    rows = overlays.catalog()
    emit({"library": rows}, "\n".join(f"  {r['key']:<16} {r['kind']:<6} {r['blend']:<8} {r['about']}" for r in rows))


@overlay_app.command("add")
def overlay_add(
    what: str = typer.Argument(..., help="A video file, an imported source id (s2), or a library key (see `overlay library`)"),
    start: float = 0.0, end: Optional[float] = None,
    x: Optional[str] = None, y: Optional[str] = None, width: Optional[int] = typer.Option(None, help="Width in canvas px"),
    src_in: float = typer.Option(0.0, help="Start this many seconds into the overlay video"),
    volume: Optional[float] = typer.Option(None, help="Overlay sound 0..2 (default: muted)"),
    loop: bool = typer.Option(False, help="Repeat the video if the layer is longer"),
    blend: Optional[str] = typer.Option(None, help="normal|screen|multiply|overlay|add|lighten|darken"),
    radius: Optional[int] = None, border: Optional[int] = typer.Option(None, help="Border width px"),
    border_color: Optional[str] = None, circle: Optional[bool] = typer.Option(None, "--circle/--rect", help="Circle cut-out"),
    chroma: Optional[str] = typer.Option(None, help="Key colour to remove (green screen), e.g. #00FF00"),
    similarity: Optional[float] = typer.Option(None, help="Green-screen tolerance 0.01..0.9 (default 0.25)"),
    preset: Optional[list[str]] = typer.Option(None),
):
    """Put a video (or a library overlay) on top of the edit."""
    from .ops import overlays

    d = pdir()
    with edit(f"overlay add {Path(what).name}") as tx:
        p = tx.project
        if what in overlays.LIBRARY:
            layer = overlays.add_from_library(p, d, what, start)
        else:
            if what in p.sources:
                sid = what
            else:
                path = Path(what).expanduser().resolve()
                if not path.exists():
                    raise VeditError(f"Not a file, source id or library overlay: {what}. Library: {', '.join(overlays.LIBRARY)}")
                sid = overlays.add_source(p, d, path)
            kw: dict[str, Any] = {"src_in": src_in, "loop": loop}
            for k, v in (("x", x), ("y", y), ("width", width), ("volume", volume)):
                if v is not None:
                    kw[k] = v
            layer = overlays.add_video(p, sid, start, end, **kw)
        if end is not None:
            layer.end = end
        for k, v in (("x", x), ("y", y), ("width", width)):
            if v is not None:
                setattr(layer, k, v)
        overlays.set_fields(layer, _frame_opts(blend, radius, border, border_color, circle, chroma, similarity))
        if preset:
            layer.presets = _presets(preset)
    emit({"layer": layer.id, "type": layer.type}, f"Added {layer.type} overlay {layer.id}")


@shape_app.command("add")
def shape_add(
    shape: str = typer.Argument(..., help="rect | ellipse | triangle | diamond | polygon | star | ring | heart | bubble | "
                                            "blob | line | arrow | path (--path)"),
    start: Optional[float] = typer.Option(None, help="Timeline seconds (design: default = start of --scene)"),
    end: Optional[float] = None, x: str = "50%", y: str = "50%",
    width: Optional[int] = None, height: Optional[int] = None, fill: Optional[str] = typer.Option(None, help="Fill colour, or 'none'"),
    gradient: Optional[str] = typer.Option(None, help="Gradient fill instead: '#FF3B6B,#7C5CFF' (2-6 colours)"),
    gradient_angle: Optional[float] = typer.Option(None, help="Gradient direction in degrees (0 = left->right, 90 = top->bottom)"),
    sides: Optional[int] = typer.Option(None, help="polygon: number of sides"),
    points: Optional[int] = typer.Option(None, help="star: number of points"),
    inner: Optional[float] = typer.Option(None, help="star: inner radius 0..1; ring: hole size 0..1"),
    border: Optional[int] = None, border_color: Optional[str] = None, radius: Optional[int] = None,
    rotation: float = 0.0, blend: Optional[str] = None, preset: Optional[list[str]] = typer.Option(None),
    scene: Optional[str] = typer.Option(None, help="Design: the scene to put it in (default: the scene at --start)"),
    full: bool = typer.Option(False, "--full", help="Cover the whole canvas: a colour screen (rect + --fill) or a "
                                                    "gradient screen (rect + --gradient)"),
    track: Optional[int] = typer.Option(None, help="Timeline track to put it on (0 = bottom overlay track)"),
    new_track: bool = typer.Option(False, "--new-track", help="Open a new track at --track for it (a track of its own)"),
    path: Optional[str] = typer.Option(None, help="path: points as x,y fractions of the box, '0,1 0.3,0.2 1,0' (a line, "
                                                  "bracket, underline, scribble; draw it on with --preset draw_in)"),
    closed: bool = typer.Option(False, "--closed", help="path: close it into a filled outline"),
    morph_to: Optional[str] = typer.Option(None, help="Shape kind it turns into (animate `morph` 0->1 with keyframes, "
                                                      "or --preset morph_loop)"),
):
    """Add a shape (box behind a title, a ring around a detail, an arrow pointing at it, a star that pops in,
    or with --full a colour / gradient screen covering the canvas)."""
    from .ops import overlays, scenes

    with edit(f"shape add {shape}") as tx:
        p = tx.project
        t0 = start if start is not None else (scenes.scene_range(p, scene)[0] if scene and p.scenes else 0.0)
        kw: dict[str, Any] = {"x": x, "y": y, "rotation": rotation}
        for k, v in (("width", width), ("height", height), ("radius", radius), ("border_width", border), ("border_color", border_color)):
            if v is not None:
                kw[k] = v
        if full:
            kw.update(width=p.canvas.width, height=p.canvas.height, radius=0, x="50%", y="50%")
        layer = overlays.add_shape(p, shape, t0, end, **kw)
        vals: dict[str, Any] = {} if fill is None else {"fill": fill}
        for k, v in (("blend", blend), ("fill_gradient", gradient), ("gradient_angle", gradient_angle), ("sides", sides),
                     ("points", points), ("inner", inner)):
            if v is not None:
                vals[k] = v
        overlays.set_fields(layer, vals)
        from .ops import motion as motion_ops

        motion_ops.set_fields(layer, {"path": path, "closed": closed if shape == "path" else None, "morph_to": morph_to}
                              if (path or morph_to or closed) else {})
        layer.presets = _presets(preset)
        scenes.place_in_scene(p, layer, scene)
        if track is not None or new_track:
            overlays.place_layer(p, layer.id, track if track is not None else 1 + max((o.track or 0 for o in p.layers if o is not layer), default=-1),
                                 new_track=new_track)
    emit({"layer": layer.id, "scene": layer.scene}, f"Added {shape} {layer.id}" + (f" in {layer.scene}" if layer.scene else ""))


def _iso(p: Project, lid: str):
    l = p.layer(lid)
    if l.type != "iso" or l.iso is None:
        raise VeditError(f"{lid} is not an isometric world (`vedit iso add`)")
    return l


@iso_app.command("items")
def iso_items_list(category: Optional[str] = typer.Option(None, help="Only this category")):
    """The item library: name, category, size on the grid, its settings."""
    from .ops import iso_items

    rows = [r for r in iso_items.catalog() if not category or r["category"].lower().startswith(category.lower())]
    emit({"items": rows, "themes": list(iso_items.THEMES), "worlds": list(iso_items.WORLDS)},
         "\n".join(f"  {r['kind']:<14} {r['category']:<15} {r['footprint'][0]:g}x{r['footprint'][1]:g}"
                   + (f"  settings: {', '.join(f'{k}={v!r}' for k, v in r['params'].items())}" if r["params"] else "") for r in rows)
         + f"\nThemes: {', '.join(iso_items.THEMES)}   Worlds: {', '.join(iso_items.WORLDS)}, empty")


@iso_app.command("add")
def iso_add(world_name: str = typer.Argument("city", metavar="WORLD", help="city | island | desk | cafe | office | farm | data | stack | empty"),
            theme: str = typer.Option("pastel", help="pastel | bright | corporate | night"),
            start: float = 0.0, end: Optional[float] = None, x: str = "50%", y: str = "55%",
            width: Optional[int] = typer.Option(None, help="Width on the canvas (px)"),
            scene: Optional[str] = typer.Option(None, help="Design: the scene to put it in")):
    """Add an isometric world that builds itself block by block (edit it with `iso item ...`)."""
    from .editor import actions

    with edit(f"iso add {world_name}") as tx:
        msg = actions._iso_add(tx.project, {"world": world_name, "theme": theme, "start": start, "end": end, "x": x, "y": y,
                                            "width": width, "scene": scene})
        lid = tx.project.layers[-1].id if tx.project.layers[-1].type == "iso" else next(l.id for l in reversed(tx.project.layers) if l.type == "iso")
    emit({"layer": lid}, msg)


@iso_app.command("list")
def iso_list(layer: str):
    """An iso world's items with their index (for `iso item set|remove`)."""
    p = load(pdir())
    l = _iso(p, layer)
    rows = [{"index": i, **it.model_dump(exclude_defaults=True)} for i, it in enumerate(l.iso.items)]
    emit({"layer": layer, "theme": l.iso.theme, "build": l.iso.build, "items": rows},
         f"{layer}: theme {l.iso.theme}, build {l.iso.build}\n" + "\n".join(
             f"  #{r['index']:<3} {r['kind']:<14} at {r.get('x', 0):g},{r.get('y', 0):g}" + (f" z{r['z']:g}" if r.get("z") else "")
             + (f" rot{r['rot']}" if r.get("rot") else "") + (f" x{r['size']:g}" if r.get("size") else "")
             + (f" '{r['label']}'" if r.get("label") else "") + (f" {r['params']}" if r.get("params") else "") for r in rows))


def _xy(at: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    if not at:
        return None, None
    try:
        a, b = (float(v) for v in at.split(","))
        return a, b
    except ValueError:
        raise VeditError("--at is x,y on the grid, e.g. 2,1.5") from None


@iso_item_app.command("add")
def iso_item_add(layer: str, kind: str = typer.Argument(..., help="An item from `vedit iso items`"),
                 at: Optional[str] = typer.Option(None, help="x,y on the grid (default: a free spot on the floor)"),
                 z: Optional[float] = typer.Option(None, help="Height it stands at (on a desk: 0.78)"),
                 rot: Optional[int] = typer.Option(None, help="Quarter turns 0-3"), size: Optional[float] = None,
                 color: Optional[str] = None, color2: Optional[str] = None,
                 label: Optional[str] = typer.Option(None, help="A floating label above it"),
                 param: Optional[list[str]] = typer.Option(None, "--param", help="Its own settings: floors=6, text=OPEN, value=3 ...")):
    """Add an item to an iso world."""
    from .ops import iso_items

    x, y = _xy(at)
    with edit(f"iso item add {layer} {kind}") as tx:
        i = iso_items.add_item(_iso(tx.project, layer).iso, kind, x, y, z=z, rot=rot, size=size, color=color, color2=color2,
                               label=label, params=iso_items.parse_params(param))
        it = _iso(tx.project, layer).iso.items[i]
    emit({"layer": layer, "index": i, "x": it.x, "y": it.y}, f"{layer}: #{i} {kind} at {it.x:g},{it.y:g}")


@iso_item_app.command("set")
def iso_item_set(layer: str, index: int, kind: Optional[str] = None, at: Optional[str] = None, z: Optional[float] = None,
                 rot: Optional[int] = None, size: Optional[float] = None, color: Optional[str] = None, color2: Optional[str] = None,
                 label: Optional[str] = None, param: Optional[list[str]] = typer.Option(None, "--param")):
    """Move / turn / resize / recolour / relabel one item, or change its settings."""
    from .ops import iso_items

    x, y = _xy(at)
    with edit(f"iso item set {layer} #{index}") as tx:
        it = iso_items.set_item(_iso(tx.project, layer).iso, index, iso_items.parse_params(param) or None, kind=kind, x=x, y=y, z=z,
                                rot=rot, size=size, color=color, color2=color2, label=label)
    emit({"layer": layer, "index": index, "item": it.model_dump(exclude_defaults=True)}, f"{layer}: #{index} {it.kind}")


@iso_item_app.command("remove")
def iso_item_remove(layer: str, index: int):
    """Delete one item from an iso world."""
    from .ops import iso_items

    with edit(f"iso item remove {layer} #{index}") as tx:
        it = iso_items.remove_item(_iso(tx.project, layer).iso, index)
    emit({"layer": layer, "removed": it.kind}, f"{layer}: removed #{index} {it.kind}")


@iso_app.command("set")
def iso_set(layer: str, theme: Optional[str] = None, world_name: Optional[str] = typer.Option(None, "--world", help="Swap in a ready-made world"),
            light: Optional[float] = typer.Option(None, help="0-0.6: how strongly the faces are lit"),
            outline: Optional[bool] = typer.Option(None, "--outline/--no-outline"),
            build: Optional[str] = typer.Option(None, help="drop | grow | pop: how each item arrives")):
    """An iso world's theme, light, outlines and build style (or swap the whole world)."""
    from .editor import actions

    with edit(f"iso set {layer}") as tx:
        actions._iso_scene_set(tx.project, {"layer": layer, "theme": theme, "world": world_name, "light": light, "outline": outline, "build": build})
    emit({"layer": layer}, f"{layer}: updated")


@iso_app.command("scene")
def iso_scene(layer: str, scene_file: Path = typer.Argument(..., help="JSON: {theme, build, items: [{kind, x, y, z, rot, size, color, label, params}]}")):
    """Replace an iso world's whole scene from a JSON file (build a custom world in one step)."""
    import json as _json

    from .ops import iso_items
    from .project import IsoScene

    sc = IsoScene.model_validate(_json.loads(scene_file.read_text()))
    for it in sc.items:
        iso_items._check(it)
    with edit(f"iso scene {layer}") as tx:
        _iso(tx.project, layer).iso = sc
    emit({"layer": layer, "items": len(sc.items)}, f"{layer}: {len(sc.items)} items")


@group_app.command("list")
def group_list():
    """Groups and their layers (every signature effect with several layers is one group)."""
    from .ops import groups

    rows = groups.summary(load(pdir()))
    emit({"groups": rows}, "\n".join(f"  {g['id']}: {g['name']} {g['start']:.2f}-{g['end']:.2f}s ({', '.join(g['layers'])})"
                                      + ("" if g["collapsed"] else "  [expanded]") for g in rows) or "No groups.")


@group_app.command("make")
def group_make(layers: list[str] = typer.Argument(..., help="Layer ids (2+)"), name: str = typer.Option("Group", help="Its name on the timeline")):
    """Group layers so they show as one row and move together."""
    from .ops import groups

    with edit(f"group {' '.join(layers)}") as tx:
        g = groups.make(tx.project, layers, name)
    emit({"group": g.id}, f"Group {g.id} ({g.name}): {', '.join(layers)}")


@group_app.command("set")
def group_set(group: str, name: Optional[str] = None,
              collapsed: Optional[bool] = typer.Option(None, "--collapsed/--expanded", help="One row (collapsed) or every track")):
    """Rename a group, or collapse / expand it on the timeline."""
    from .ops import groups

    with edit(f"group set {group}") as tx:
        g = groups.set_group(tx.project, group, name, collapsed)
    emit({"group": g.id, "name": g.name, "collapsed": g.collapsed}, f"{g.id}: {g.name}{'' if g.collapsed else ' (expanded)'}")


@group_app.command("move")
def group_move(group: str, to: Optional[float] = typer.Option(None, help="Timeline seconds where the group starts"),
               by: Optional[float] = typer.Option(None, help="Shift by this many seconds (negative = earlier)")):
    """Move every layer of a group in time together."""
    from .ops import groups

    if to is None and by is None:
        raise VeditError("Give --to T or --by S")
    with edit(f"group move {group}") as tx:
        d = groups.move(tx.project, group, by, to)
    emit({"group": group, "shift": round(d, 3)}, f"{group} moved {d:+.2f}s")


@group_app.command("remove")
def group_remove(group: str):
    """Delete a group and all its layers."""
    from .ops import groups

    with edit(f"group remove {group}") as tx:
        gone = groups.remove(tx.project, group)
    emit({"group": group, "removed": gone}, f"Removed {group}: {', '.join(gone)}")


@group_app.command("ungroup")
def group_ungroup(group: str):
    """Keep the layers but drop the group."""
    from .ops import groups

    with edit(f"ungroup {group}") as tx:
        ids = groups.ungroup(tx.project, group)
    emit({"group": group, "layers": ids}, f"Ungrouped {group}: {', '.join(ids)}")


@style_app.command("list")
def style_list():
    """The motion styles (and the ones still to come)."""
    from .design import styles

    rows = [{"key": s.key, "name": s.name, "use_for": s.use_for} for s in styles.STYLES.values()]
    emit({"styles": rows, "planned": styles.PLANNED},
         "\n".join(f"  {r['key']:<18} {r['name']}: {r['use_for']}" for r in rows)
         + "\nComing later: " + "; ".join(styles.PLANNED.values()))


@style_app.command("show")
def style_show(name: str):
    """A style's numbers: easing, entrances, what it never does, timing, palette, fonts, filters, rules."""
    from .design import styles

    s = styles.get(name)
    emit(s.card(), styles.brief(s))


@style_app.command("set")
def style_set(name: str = typer.Argument(..., help="A style key (`vedit style list`), or 'none'")):
    """Give the project a motion style: its rules go to the AI and the audit checks them."""
    from .design import styles

    with edit(f"style {name}") as tx:
        tx.project.motion_style = None if name == "none" else styles.get(name).key
    emit({"style": tx.project.motion_style}, f"Motion style: {tx.project.motion_style or 'none'}")


@motion_app.command("set")
def motion_set(
    layer_id: str,
    split: Optional[str] = typer.Option(None, help="Kinetic text: none | word | letter | line (each piece animates in)"),
    anim: Optional[str] = typer.Option(None, help="How each piece comes in: rise | drop | fade | pop | mask_up | slide | type"),
    stagger: Optional[float] = typer.Option(None, help="Seconds between pieces (0.03 letters, 0.08 words, 0.15 lines)"),
    dur: Optional[float] = typer.Option(None, help="Seconds each piece takes"),
    ease: Optional[str] = typer.Option(None, help="Each piece's easing: ease_out, snap, expo_out, overshoot, "
                                                  "cubic-bezier(.7,0,.2,1) ..."),
    morph_to: Optional[str] = typer.Option(None, help="Shapes: the kind it morphs into ('none' to clear)"),
    path: Optional[str] = typer.Option(None, help="Path shapes: points '0,1 0.3,0.2 1,0' (0..1 of the box)"),
    closed: Optional[bool] = typer.Option(None, help="Path shapes: closed (filled) or open (a stroke)"),
    step_fps: Optional[int] = typer.Option(None, help="Move in steps like stop motion (12 = on twos, paper cut-out); 0 = smooth"),
):
    """Set a layer's motion-graphics fields (kinetic text, morph target, path points)."""
    from .ops import motion as motion_ops

    with edit(f"motion set {layer_id}") as tx:
        l = tx.project.layer(layer_id)
        if (split or anim) and l.type != "text":
            raise VeditError("Kinetic text (--split / --anim) is for text layers")
        if path is not None and l.type == "shape" and l.shape != "path":
            l.shape = "path"
        changed = motion_ops.set_fields(l, {"split": split, "split_anim": anim, "stagger": stagger, "split_dur": dur, "split_ease": ease,
                                            **({"morph_to": None if morph_to == "none" else morph_to} if morph_to is not None else {}),
                                            "path": path, "closed": closed, "step_fps": step_fps})
    emit({"layer": layer_id, "changed": changed}, f"{layer_id}: " + (", ".join(changed) or "nothing changed"))


@motion_app.command("draw")
def motion_draw(layer_id: str, dur: float = typer.Option(1.0, help="Seconds to draw on"),
                ease: str = typer.Option("ease_in_out", help="Easing (a name or cubic-bezier(...))"),
                out: bool = typer.Option(False, "--out", help="Also undraw at the end (draw_out)")):
    """Draw a shape's outline / a path / a chart on (preset draw_in), optionally off again at its end."""
    with edit(f"motion draw {layer_id}") as tx:
        l = tx.project.layer(layer_id)
        if l.type not in ("shape", "chart"):
            raise VeditError("Draw-on is for shapes, paths and charts")
        anim.ease(ease, 0.5)
        l.presets = [x for x in l.presets if x.name not in ("draw_in", "draw_out")]
        l.presets.append(Preset(name="draw_in", duration=dur, params={"ease": ease}))
        if out:
            l.presets.append(Preset(name="draw_out", duration=min(dur, 0.6), params={"ease": ease}))
    emit({"layer": layer_id}, f"{layer_id} draws on over {dur:g}s" + (" and off at the end" if out else ""))


@motion_app.command("morph")
def motion_morph(layer_id: str, to: str = typer.Option(..., help="Shape kind to morph into"),
                 at: float = typer.Option(0.0, help="Seconds after the layer starts"),
                 dur: float = typer.Option(0.8, help="Seconds the morph takes"),
                 ease: str = typer.Option("smooth", help="Easing"),
                 loop: bool = typer.Option(False, "--loop", help="Morph there and back for ever (morph_loop) instead"),
                 period: float = typer.Option(2.4, help="--loop: seconds per there-and-back")):
    """Morph a shape into another kind (circle -> star, square -> heart ...)."""
    from .ops import motion as motion_ops

    with edit(f"motion morph {layer_id} {to}") as tx:
        l = tx.project.layer(layer_id)
        if l.type != "shape":
            raise VeditError("Only shapes morph")
        motion_ops.set_fields(l, {"morph_to": to})
        l.keyframes = [k for k in l.keyframes if k.morph is None]
        l.presets = [x for x in l.presets if x.name != "morph_loop"]
        if loop:
            l.presets.append(Preset(name="morph_loop", params={"period": period}))
        else:
            anim.ease(ease, 0.5)
            l.keyframes += [Keyframe(t=at, morph=0, ease=ease), Keyframe(t=at + dur, morph=1)]
            l.keyframes.sort(key=lambda k: k.t)
    emit({"layer": layer_id, "to": to}, f"{layer_id} morphs into a {to}" + (" and back" if loop else f" at +{at:g}s over {dur:g}s"))


def _chart_opts(colors, prefix, suffix, decimals, max_, values, labels, text_color, text_size, font, stagger, line_width, radius, gap) -> dict:
    return {k: v for k, v in dict(colors=[c.strip() for c in colors.split(",")] if colors else None, prefix=prefix, suffix=suffix,
                                     decimals=decimals, max=max_, show_values=values, show_labels=labels, text_color=text_color,
                                     text_size=text_size, font=font, stagger=stagger, line_width=line_width, radius=radius,
                                     gap=gap).items() if v is not None}


@chart_app.command("add")
def chart_add(
    kind: str = typer.Argument(..., help="bar | hbar | line | donut"),
    data: str = typer.Option(..., help="'Mon=12, Tue=30, Wed=18' (label=value, optional :#colour)"),
    start: float = 0.0, end: Optional[float] = None, x: str = "50%", y: str = "50%",
    width: Optional[int] = None, height: Optional[int] = None,
    dur: float = typer.Option(1.2, help="Seconds it grows in (0 = already there)"),
    colors: Optional[str] = typer.Option(None, help="Palette '#22D3EE,#A78BFA' (items cycle through it)"),
    prefix: Optional[str] = None, suffix: Optional[str] = None, decimals: Optional[int] = None,
    max_: Optional[float] = typer.Option(None, "--max", help="Top of the value axis (default: the largest value)"),
    values: Optional[bool] = typer.Option(None, help="Show the values"), labels: Optional[bool] = typer.Option(None, help="Show the labels"),
    text_color: Optional[str] = None, text_size: Optional[int] = None, font: Optional[str] = None,
    stagger: Optional[float] = typer.Option(None, help="0 = all bars together, 1 = one after another"),
    line_width: Optional[int] = None, radius: Optional[int] = None, gap: Optional[float] = None,
    scene: Optional[str] = typer.Option(None, help="Design: the scene to put it in"),
):
    """Add a chart that grows in (data stories: bars rise one by one, values roll up)."""
    from .ops import chart as chart_ops, motion as motion_ops, scenes

    with edit(f"chart add {kind}") as tx:
        p = tx.project
        l = motion_ops.add_chart(p, kind, chart_ops.parse_items(data), start, end, x, y, width, height, dur,
                                 **_chart_opts(colors, prefix, suffix, decimals, max_, values, labels, text_color, text_size, font,
                                               stagger, line_width, radius, gap))
        scenes.place_in_scene(p, l, scene)
    emit({"layer": l.id}, f"Added {kind} chart {l.id} ({len(l.chart.items)} values)")


@chart_app.command("set")
def chart_set(
    layer_id: str, kind: Optional[str] = None, data: Optional[str] = None,
    colors: Optional[str] = None, prefix: Optional[str] = None, suffix: Optional[str] = None, decimals: Optional[int] = None,
    max_: Optional[float] = typer.Option(None, "--max"), values: Optional[bool] = None, labels: Optional[bool] = None,
    text_color: Optional[str] = None, text_size: Optional[int] = None, font: Optional[str] = None,
    stagger: Optional[float] = None, line_width: Optional[int] = None, radius: Optional[int] = None, gap: Optional[float] = None,
):
    """Change a chart's data or look."""
    from .ops import chart as chart_ops, motion as motion_ops

    vals = _chart_opts(colors, prefix, suffix, decimals, max_, values, labels, text_color, text_size, font, stagger, line_width, radius, gap)
    if kind:
        vals["kind"] = kind
    if data:
        vals["items"] = chart_ops.parse_items(data)
    with edit(f"chart set {layer_id}") as tx:
        motion_ops.set_fields(tx.project.layer(layer_id), {"chart": vals})
    emit({"layer": layer_id, "changed": sorted(vals)}, f"{layer_id}: " + (", ".join(sorted(vals)) or "nothing changed"))


@counter_app.command("add")
def counter_add(
    to: float = typer.Argument(..., help="The number it counts to"),
    from_: float = typer.Option(0.0, "--from", help="The number it starts at"),
    decimals: int = 0, prefix: str = typer.Option("", help="e.g. '$'"), suffix: str = typer.Option("", help="e.g. '+', '%', 'k'"),
    sep: str = typer.Option(",", help="Thousands separator ('' = none)"),
    start: float = 0.0, end: Optional[float] = None, x: str = "50%", y: str = "40%",
    dur: float = typer.Option(1.2, help="Seconds it counts for"),
    ease: str = typer.Option("expo_out", help="Easing of the count (expo_out: fast, then settles)"),
    size: Optional[int] = None, color: Optional[str] = None, font: Optional[str] = None,
    stroke: Optional[str] = None, stroke_width: Optional[int] = None, glow: Optional[str] = None,
    scene: Optional[str] = typer.Option(None, help="Design: the scene to put it in"),
):
    """Add a number that counts up (stats, prices, followers)."""
    from .ops import motion as motion_ops, scenes

    style = {k: v for k, v in dict(size=size, color=color, font=font, stroke_color=stroke, stroke_width=stroke_width,
                                   glow_color=glow).items() if v is not None}
    with edit(f"counter add {to:g}") as tx:
        p = tx.project
        l = motion_ops.add_counter(p, to, start, end, x, y, dur, ease, style, **{"from": from_, "decimals": decimals,
                                                                                 "prefix": prefix, "suffix": suffix, "separator": sep})
        scenes.place_in_scene(p, l, scene)
    emit({"layer": l.id, "text": l.text}, f"Added counter {l.id}: counts to {l.text} over {dur:g}s")


@counter_app.command("set")
def counter_set(layer_id: str, to: Optional[float] = None, from_: Optional[float] = typer.Option(None, "--from"),
                decimals: Optional[int] = None, prefix: Optional[str] = None, suffix: Optional[str] = None,
                sep: Optional[str] = None, off: bool = typer.Option(False, "--off", help="Stop counting (plain text)")):
    """Change a counter (or turn a counting text back into plain text with --off)."""
    from .ops import motion as motion_ops

    with edit(f"counter set {layer_id}") as tx:
        l = tx.project.layer(layer_id)
        if off:
            motion_ops.set_fields(l, {"counter": None})
            l.presets = [x for x in l.presets if x.name != "count_up"]
        else:
            motion_ops.set_fields(l, {"counter": {"to": to, "from": from_, "decimals": decimals, "prefix": prefix,
                                                  "suffix": suffix, "separator": sep}})
            l.text = motion_ops.counter_text(l.counter, 1.0)
            if not any(x.name == "count_up" for x in l.presets):
                l.presets.append(Preset(name="count_up", duration=1.2))
    emit({"layer": layer_id}, f"{layer_id}: " + ("plain text" if off else f"counts to {l.text}"))


@layer_app.command("order")
def layer_order(layer_id: str, to: str = typer.Argument(..., help="up | down | front | back | index (0 = bottom)")):
    """Change which overlay is on top."""
    from .ops import overlays

    with edit(f"layer order {layer_id} {to}") as tx:
        i = overlays.move_layer(tx.project, layer_id, to)
        order = [l.id for l in tx.project.layers]
    emit({"layer": layer_id, "index": i, "order": order}, f"{layer_id} is now #{i} (bottom to top: {', '.join(order)})")


@layer_app.command("style")
def layer_style(
    layer_id: str, blend: Optional[str] = None, radius: Optional[int] = None,
    border: Optional[int] = typer.Option(None, help="Border width px"), border_color: Optional[str] = None,
    circle: Optional[bool] = typer.Option(None, "--circle/--rect"), chroma: Optional[str] = typer.Option(None, help="Key colour, or 'none'"),
    similarity: Optional[float] = None, volume: Optional[float] = None, loop: Optional[bool] = typer.Option(None, "--loop/--no-loop"),
    src_in: Optional[float] = None, fill: Optional[str] = None, height: Optional[int] = None,
    fit: Optional[str] = typer.Option(None, help="Video overlays: free (width-sized, picture-in-picture) | cover (fill the screen) | contain (fit the screen)"),
    flip_h: Optional[bool] = typer.Option(None, "--flip-h/--no-flip-h", help="Mirror left-right"),
    flip_v: Optional[bool] = typer.Option(None, "--flip-v/--no-flip-v", help="Flip upside down"),
):
    """Blend mode and frame of an overlay: rounded corners, border, circle, green screen; video sound / loop / fit / flip; shape fill."""
    from .ops import overlays

    vals = _frame_opts(blend, radius, border, border_color, circle, chroma, similarity)
    for k, v in (("volume", volume), ("loop", loop), ("src_in", src_in), ("fill", fill), ("height", height),
                 ("fit", fit), ("flip_h", flip_h), ("flip_v", flip_v)):
        if v is not None:
            vals[k] = v
    with edit(f"layer style {layer_id}") as tx:
        layer = tx.project.layer(layer_id)
        changed = overlays.set_fields(layer, vals)
    emit({"layer": layer_id, "changed": changed}, f"{layer_id}: " + (", ".join(changed) or "nothing changed"))


@layer_app.command("list")
def layer_list():
    """List overlay layers."""
    p = load(pdir())
    data = [l.model_dump(mode="json", exclude={"words"}) | {"words": len(l.words)} for l in p.layers]
    emit({"layers": data}, "\n".join(f"{l.id}: {l.type} {l.start}-{l.end} {l.text or l.image or ''}" for l in p.layers) or "No layers")


@layer_app.command("set")
def layer_set(
    layer_id: str,
    start: Optional[float] = None, end: Optional[float] = None,
    x: Optional[str] = None, y: Optional[str] = None,
    scale: Optional[float] = None, rotation: Optional[float] = None, opacity: Optional[float] = None,
    width: Optional[int] = None, height: Optional[int] = typer.Option(None, help="Shape height (px)"),
    sx: Optional[float] = typer.Option(None, help="Stretch: width multiplier of the drawn layer (1 = as drawn)"),
    sy: Optional[float] = typer.Option(None, help="Stretch: height multiplier"),
):
    """Change timing / position / transform / size / stretch of a layer."""
    with edit(f"set {layer_id}") as tx:
        l = tx.project.layer(layer_id)
        for k, v in dict(start=start, end=end, x=x, y=y, scale=scale, rotation=rotation, opacity=opacity, width=width,
                         height=height).items():
            if v is not None:
                setattr(l, k, v)
        for k, v in (("sx", sx), ("sy", sy)):
            if v is not None:
                setattr(l, k, max(0.01, v))
    emit({"layer": l.model_dump(mode="json", exclude={"words"})}, f"Updated {layer_id}")


@layer_app.command("dup")
def layer_dup(layer_id: str, dx: float = typer.Option(40, help="Offset of the copy (canvas px)"), dy: float = 40):
    """Duplicate a layer (on top, nudged by dx / dy; 0 0 = same place)."""
    from .ops import overlays

    with edit(f"duplicate {layer_id}") as tx:
        l = overlays.duplicate_layer(tx.project, layer_id, dx, dy)
    emit({"layer": l.id}, f"Duplicated {layer_id} as {l.id}")


@layer_app.command("remove")
def layer_remove(layer_id: str):
    """Delete a layer."""
    with edit(f"remove layer {layer_id}") as tx:
        tx.project.layers.remove(tx.project.layer(layer_id))
    emit({"removed": layer_id}, f"Removed {layer_id}")


# ---- animation -----------------------------------------------------------------------

@anim_app.command("list")
def anim_list():
    """Show animation presets and easing curves."""
    data = {"presets": {k: {"phase": v[0], "default_duration": v[1], "help": v[3]} for k, v in anim.PRESETS.items()},
            "easings": list(anim.EASINGS)}
    emit(data, "Presets:\n" + "\n".join(f"  {k:<11} [{v[0]}] {v[3]}" for k, v in anim.PRESETS.items())
         + "\nEasings: " + ", ".join(anim.EASINGS))


@anim_app.command("preset")
def anim_preset(layer_id: str, name: str, duration: Optional[float] = None,
                params: Optional[str] = typer.Option(None, help='JSON, e.g. \'{"direction":"left"}\'')):
    """Add an animation preset to a layer (stackable)."""
    pr = Preset(name=name, duration=duration, params=json.loads(params) if params else {})
    anim.validate_preset(pr)
    with edit(f"animate {layer_id} {name}") as tx:
        l = tx.project.layer(layer_id)
        l.presets = [x for x in l.presets if x.name != name] + [pr]
    emit({"layer": layer_id, "presets": [x.name for x in l.presets]}, f"{layer_id}: {', '.join(x.name for x in l.presets)}")


@anim_app.command("keyframes")
def anim_keyframes(layer_id: str, keyframes: str = typer.Argument(..., help='JSON list: [{"t":0,"x":"50%","scale":0,"ease":"back_out"},{"t":0.5,"scale":1}]'),
                   append: bool = typer.Option(False, help="Add to existing keyframes instead of replacing")):
    """Set custom keyframes (t = seconds from layer start; x, y, scale, rotation, opacity, reveal, w / h (shape size,
    stays crisp), sx / sy (stretch), fill (hex, shapes); ease)."""
    from .project import Keyframe

    try:
        kfs = [Keyframe(**k) for k in json.loads(keyframes)]
    except Exception as e:
        raise VeditError(f"Invalid keyframes JSON: {e}") from e
    anim.validate_keyframes(kfs)
    with edit(f"keyframes {layer_id} ({len(kfs)})") as tx:
        l = tx.project.layer(layer_id)
        l.keyframes = (l.keyframes + kfs) if append else kfs
    emit({"layer": layer_id, "keyframes": len(l.keyframes)}, f"{layer_id}: {len(l.keyframes)} keyframes")


@anim_app.command("clear")
def anim_clear(layer_id: str):
    """Remove all presets and keyframes from a layer."""
    with edit(f"clear animation {layer_id}") as tx:
        l = tx.project.layer(layer_id)
        l.presets, l.keyframes = [], []
    emit({"layer": layer_id}, f"Cleared animation on {layer_id}")


# ---- captions -----------------------------------------------------------------------

@cap_app.command("srt")
def captions_srt(file: str, style: str = "bold", y: str = "72%", words: int = typer.Option(3, help="Words shown at once")):
    """Import captions from an .srt file (word-by-word highlight)."""
    ws = caps.parse_srt(Path(file).expanduser().read_text(encoding="utf-8-sig"))
    with edit(f"captions from {Path(file).name}") as tx:
        layer = caps.add_captions(tx.project, ws, style, y, words)
    emit({"layer": layer.id, "words": len(layer.words)}, f"Added captions {layer.id} ({len(layer.words)} words)")


@cap_app.command("auto")
def captions_auto(style: str = "bold", y: str = "72%", words: int = 3,
                  model: str = typer.Option("small", help="whisper model: tiny, base, small, medium, large-v3"),
                  language: Optional[str] = typer.Option(None, help="e.g. en, ms, id (auto-detect if empty)")):
    """Transcribe the current timeline offline (faster-whisper) and add captions. Do this AFTER cutting."""
    from .render.runner import render_preview

    d = pdir()
    p = load(d)
    res = render_preview(p, d, progress=not STATE["json"])
    ws = caps.transcribe(Path(res["output"]), model, language)
    with edit(f"auto captions ({len(ws)} words)") as tx:
        layer = caps.add_captions(tx.project, ws, style, y, words)
    emit({"layer": layer.id, "words": len(ws), "text": " ".join(w.text for w in ws)[:500]},
         f"Added captions {layer.id}: {' '.join(w.text for w in ws)[:200]}")


@cap_app.command("style")
def captions_style(layer_id: str, style: str):
    """Switch a captions layer to another style: bold, boxed, minimal, neon, karaoke."""
    with edit(f"caption style {style}") as tx:
        tx.project.layer(layer_id).style = caps.style_for(style)
    emit({"layer": layer_id, "style": style}, f"{layer_id} -> {style}")


# ---- effects --------------------------------------------------------------------------

def _set_effect(p: Project, clip: str | None, eff: Effect, replace_types: tuple[str, ...]) -> list[str]:
    targets = [p.clip(clip)] if clip else [c for c in p.clips if p.sources[c.source].has_video]
    for c in targets:
        c.effects = [e for e in c.effects if e.type not in replace_types] + [eff.model_copy(deep=True)]
    return [c.id for c in targets]


def _clear_effect(p: Project, clip: str | None, types: tuple[str, ...]) -> list[str]:
    targets = [p.clip(clip)] if clip else p.clips
    for c in targets:
        c.effects = [e for e in c.effects if e.type not in types]
    return [c.id for c in targets]


@bg_app.command("remove")
def bg_remove(
    clip: Optional[str] = typer.Option(None, help="Only this clip (default: all clips)"),
    mode: str = typer.Option("fast", help="fast (MediaPipe) | quality (BiRefNet, slow) | chroma (green/blue screen, instant)"),
    replace: Optional[str] = typer.Option(None, help="color:#RRGGBB | blur[:sigma] | image:path | video:path | none (transparent: the tracks under the video show) (default: chroma = canvas colour, AI = #00B140)"),
    smooth: float = typer.Option(0.5, help="fast/quality: temporal smoothing 0..0.9 (less flicker)"),
    key: str = typer.Option("auto", help="chroma: screen colour #RRGGBB, or auto (sampled from each clip)"),
    similarity: Optional[float] = typer.Option(None, help="chroma: how far from the key colour still counts as screen (0.01-0.6; default fitted to the key, at most 0.12)"),
    blend: Optional[float] = typer.Option(None, help="chroma: edge softness (0-0.5; default fitted to the key, at most 0.08)"),
    spill: float = typer.Option(0.6, help="chroma: remove green/blue cast on the person's edges (0-1)"),
    shrink: int = typer.Option(1, help="chroma: pull the matte edge in by N px (0-4) to hide fringes"),
):
    """Remove the background behind people and replace it."""
    from .ops import chroma

    if mode not in ("fast", "quality", "chroma"):
        raise VeditError("--mode must be fast, quality or chroma")
    d = pdir()
    with edit(f"remove background ({mode}, {replace or 'default'})") as tx:
        p = tx.project
        if replace is None:
            replace = f"color:{p.canvas.background}" if mode == "chroma" else "color:#00B140"
        if mode != "chroma":
            ids = _set_effect(p, clip, Effect(type="bg", params={"mode": mode, "replace": replace, "smooth": smooth}), ("bg",))
        else:
            base = chroma.clean({"key": key, "spill": spill, "shrink": shrink,
                                 **{k: v for k, v in (("similarity", similarity), ("blend", blend)) if v is not None}})
            ids, keys = [], {}
            for c in ([p.clip(clip)] if clip else p.clips):
                if c.gap:
                    continue
                k = base["key"]
                if k == "auto":
                    k = chroma.clip_key(d, p, c)
                keys[c.id] = k
                tol = chroma.fit_tolerance(k, base.get("similarity"), base.get("blend"))  # pale screens: keep whites solid
                c.effects = [e for e in c.effects if e.type != "bg"]
                c.effects.append(Effect(type="bg", params={**base, **tol, "mode": "chroma", "key": k, "replace": replace}))
                ids.append(c.id)
    if mode == "chroma":
        emit({"clips": ids, "keys": keys},
             f"Green screen keyed on {', '.join(ids)} ({', '.join(sorted(set(keys.values())))}). Check edges with `vedit frame`; "
             "tune with `vedit bg tune --similarity/--spill/--shrink`.")
    else:
        emit({"clips": ids}, f"Background removal on {', '.join(ids)}. Run `vedit preview` to compute it (one-time, may take a while).")


@bg_app.command("tune")
def bg_tune(
    clip: Optional[str] = typer.Option(None, help="Only this clip (default: every clip with a green-screen key)"),
    key: Optional[str] = typer.Option(None, help="Screen colour #RRGGBB, or auto"),
    similarity: Optional[float] = typer.Option(None, help="Higher = more is keyed out (try +0.03 if screen shows through)"),
    blend: Optional[float] = typer.Option(None, help="Edge softness"),
    spill: Optional[float] = typer.Option(None, help="Green/blue cast removal 0-1"),
    shrink: Optional[int] = typer.Option(None, help="Pull the edge in by N px (0-4)"),
):
    """Adjust a green-screen key (instant: nothing is recomputed)."""
    from .ops import chroma

    vals = chroma.clean({k: v for k, v in {"key": key, "similarity": similarity, "blend": blend, "spill": spill,
                                            "shrink": shrink}.items() if v is not None})
    if not vals:
        raise VeditError("Give at least one of --key --similarity --blend --spill --shrink")
    d = pdir()
    with edit("tune green screen") as tx:
        p = tx.project
        n = 0
        for c in ([p.clip(clip)] if clip else p.clips):
            for e in c.effects:
                if e.type == "bg" and e.params.get("mode") == "chroma":
                    upd = dict(vals)
                    if upd.get("key") == "auto":
                        upd["key"] = chroma.clip_key(d, p, c)
                    if "key" in upd:
                        upd.update(chroma.fit_tolerance(upd["key"], upd.get("similarity"), upd.get("blend")))
                    e.params.update(upd)
                    n += 1
        if not n:
            raise VeditError("No green-screen key on these clips. Use `vedit bg remove --mode chroma` first.")
    emit({"updated": n, **vals}, f"Green screen tuned on {n} clip(s)")


@bg_app.command("replace")
def bg_replace(replace: str, clip: Optional[str] = None):
    """Change the replacement background (no recompute of the mask)."""
    with edit(f"background -> {replace}") as tx:
        targets = [tx.project.clip(clip)] if clip else tx.project.clips
        n = 0
        for c in targets:
            for e in c.effects:
                if e.type == "bg":
                    e.params["replace"] = replace
                    n += 1
        if not n:
            raise VeditError("No background removal on these clips. Use `vedit bg remove` first.")
    emit({"updated": n}, f"Background set to {replace} on {n} clip(s)")


@bg_app.command("clear")
def bg_clear(clip: Optional[str] = None):
    """Turn background removal off."""
    with edit("clear background removal") as tx:
        ids = _clear_effect(tx.project, clip, ("bg",))
    emit({"clips": ids}, "Background removal cleared")


@face_app.command("mesh")
def face_mesh(clip: Optional[str] = None, color: str = "#00E5FF", width: Optional[int] = None,
              glow: float = 6.0, dots: bool = False, faces: int = 1, opacity: float = 0.85):
    """Draw a tracked 3D face-mesh wireframe."""
    params = {"color": color, "glow": glow, "dots": dots, "faces": faces, "opacity": opacity}
    if width:
        params["width"] = width
    with edit("face mesh") as tx:
        ids = _set_effect(tx.project, clip, Effect(type="face_mesh", params=params), ("face_mesh",))
    emit({"clips": ids}, f"Face mesh on {', '.join(ids)}")


@face_app.command("sticker")
def face_sticker(image: str, anchor: str = typer.Option("eyes", help="eyes | head | nose | mouth | face"),
                 clip: Optional[str] = None, size: float = 1.0, offset_x: float = 0.0, offset_y: float = 0.0, faces: int = 1):
    """Attach a PNG to the face (glasses on eyes, hat on head...). It follows position, rotation and scale."""
    d = pdir()
    src = Path(image).expanduser().resolve()
    if not src.exists():
        raise VeditError(f"Image not found: {src}")
    params = {"image": store_media_path(d, src), "anchor": anchor, "size": size, "offset_x": offset_x, "offset_y": offset_y, "faces": faces}
    with edit(f"face sticker {src.name} on {anchor}") as tx:
        ids = _set_effect(tx.project, clip, Effect(type="face_sticker", params=params), ())
    emit({"clips": ids}, f"Sticker on {', '.join(ids)}")


@face_app.command("prop")
def face_prop(model: str, clip: Optional[str] = None, size_cm: float = 12.0,
              x_cm: float = 0.0, y_cm: float = 11.0, z_cm: float = 0.0,
              rot_x: float = 0.0, rot_y: float = 0.0, rot_z: float = 0.0, spin: float = 0.0,
              color: str = "#FF4FA3", faces: int = 1):
    """Attach a 3D model (.obj/.glb/.stl) to the head. Units are cm in face space (y up)."""
    d = pdir()
    src = Path(model).expanduser().resolve()
    if not src.exists():
        raise VeditError(f"Model not found: {src}")
    params = {"model": store_media_path(d, src), "size_cm": size_cm, "x_cm": x_cm, "y_cm": y_cm, "z_cm": z_cm,
              "rot_x": rot_x, "rot_y": rot_y, "rot_z": rot_z, "spin": spin, "color": color, "faces": faces}
    with edit(f"3D prop {src.name}") as tx:
        ids = _set_effect(tx.project, clip, Effect(type="face_prop", params=params), ())
    emit({"clips": ids}, f"3D prop on {', '.join(ids)}")


@face_app.command("blur")
def face_blur(clip: Optional[str] = None, style: str = typer.Option("blur", help="blur | pixelate"),
              strength: float = 30, faces: int = 3, expand: float = 1.15):
    """Blur or pixelate faces (privacy)."""
    params = {"style": style, "strength": strength, "faces": faces, "expand": expand}
    with edit(f"face {style}") as tx:
        ids = _set_effect(tx.project, clip, Effect(type="face_blur", params=params), ("face_blur",))
    emit({"clips": ids}, f"Face {style} on {', '.join(ids)}")


@face_app.command("clear")
def face_clear(clip: Optional[str] = None):
    """Remove all face effects."""
    with edit("clear face effects") as tx:
        ids = _clear_effect(tx.project, clip, ("face_mesh", "face_sticker", "face_prop", "face_blur"))
    emit({"clips": ids}, "Face effects cleared")


# ---- masks & object focus ---------------------------------------------------------------

def _region(p: Project, d: Path, clip: str | None, at: float | None, box: str | None, obj: str | None, space: str,
            paint: list | None = None, keep: str | None = None) -> tuple:
    from .ops import track

    return track.region(p, d, clip, at, box, obj, space, paint, keep)


def _log(msg: str) -> None:
    """Progress of slow one-time work (tracking, SAM) on stderr, so --json output stays clean."""
    print(msg, file=sys.stderr, flush=True)


def _check_tracker(tracker: str | None, sam_mode: str | None) -> None:
    from .ops import outline, sam

    if tracker is not None and tracker not in outline.TRACKERS:
        raise VeditError(f"--tracker must be one of {', '.join(outline.TRACKERS)}")
    if sam_mode is not None and sam_mode not in sam.MODES:
        raise VeditError(f"--sam must be one of {', '.join(sam.MODES)}")


@mask_app.command("add")
def mask_add(
    box: Optional[str] = typer.Option(None, help="x,y,w,h of the region on the canvas (px or %), e.g. 20%,55%,60%,10%; "
                                      "several boxes joined with ';' make one region of any shape"),
    obj: Optional[str] = typer.Option(None, "--object", help="Detect it instead: person, car, bottle, cell phone, laptop, any..."),
    paint: Optional[str] = typer.Option(None, help='Painted region as JSON in canvas px: [{"pts":[[x,y],...],"r":20}, {"rect":[x,y,w,h]}, '
                                        '{"rect":[...],"erase":true}, {"pts":[...],"r":30,"keep":true}]'),
    keep_box: Optional[str] = typer.Option(None, help="Region that must stay ON TOP of the mask (the product in front of the label), "
                                           "x,y,w,h[;x,y,w,h...] on the canvas; tracked on its own"),
    clip: Optional[str] = typer.Option(None, help="Clip (default: the clip under --at)"),
    at: Optional[float] = typer.Option(None, help="Timeline seconds where the box is right (tracking starts here)"),
    style: str = typer.Option("remove", help="remove (fill with the background around it) | blur | pixelate | fill | dim"),
    strength: Optional[float] = typer.Option(None, help="blur sigma (40) / pixel block size (28) / dim amount 0-1 (0.6)"),
    color: str = typer.Option("#000000", help="Fill colour for --style fill"),
    shape: str = typer.Option("rect", help="rect (rounded) | ellipse"),
    feather: float = typer.Option(0.04, help="Soft edge, fraction of the frame's short side (0 = hard edge)"),
    expand: Optional[float] = typer.Option(None, help="Grow the region (default 1.1 for a box = 10% margin; 1.0 for painted)"),
    track_: bool = typer.Option(True, "--track/--no-track", help="Follow the region as the camera / object moves"),
    smooth: float = typer.Option(0.6, help="Tracking smoothing 0-0.95"),
    invert: bool = typer.Option(False, help="Apply to everything EXCEPT the region"),
    space: str = typer.Option("canvas", help="canvas (what you see in frames) | source (original video pixels)"),
    tracker: str = typer.Option("auto", help="auto: segment when SAM is installed, else outline | segment: SAM 2 finds the "
                                "object again in every frame (best for turning, resizing, shape changes, entering the "
                                "frame) | outline: follow the outline point by point (fast) | box: the older box tracker"),
    sam_mode: str = typer.Option("auto", "--sam", help="SAM 2.1 outlines the object from your box: auto (when the "
                                 "mask-ai extra is installed) | on (install it if needed) | off"),
):
    """Hide a region (labels, logos, plates, screens) with a soft-edged mask that follows it."""
    from .ops.track import STYLES

    _check_tracker(tracker, sam_mode)
    if style not in STYLES:
        raise VeditError(f"--style must be one of {', '.join(STYLES)}")
    if shape not in ("rect", "ellipse"):
        raise VeditError("--shape must be rect or ellipse")
    d = pdir()
    with edit(f"mask ({style})") as tx:
        p = tx.project
        try:
            paint_items = json.loads(paint) if paint else None
        except json.JSONDecodeError as e:
            raise VeditError(f"--paint must be JSON: {e}") from None
        c, params, note = _region(p, d, clip, at, box, obj, space, paint_items, keep_box)
        from .ops.track import new_mask_id

        painted = params.get("shape") == "paint"
        params.update({"id": new_mask_id(p), "style": style, "color": color, "shape": "paint" if painted else shape,
                       "feather": feather, "expand": expand if expand is not None else (1.0 if painted else 1.1), "track": track_, "smooth": smooth, "invert": invert,
                       "tracker": tracker, "sam": sam_mode})
        if strength is not None:
            params["strength"] = strength
        c.effects.append(Effect(type="mask", params=params))
    emit({"mask": params["id"], "clip": c.id, "box_source": params["box"], "note": note},
         f"Mask {params['id']} on {c.id}{' (' + note + ')' if note else ''}. Tracking runs on the next preview (one-time).")


def _masks(p: Project, mask_id: str | None):
    hits = [(c, e) for c in p.clips for e in c.effects if e.type == "mask" and (mask_id is None or e.params.get("id") == mask_id)]
    if mask_id and not hits:
        raise VeditError(f"No mask '{mask_id}'. Masks: {', '.join(sorted({e.params.get('id') for c in p.clips for e in c.effects if e.type == 'mask'})) or 'none'}")
    return hits


@mask_app.command("list")
def mask_list():
    """List masks (id, clip, style, box in source fractions)."""
    p = load(pdir())
    rows = [{"id": e.params.get("id"), "clip": c.id, "style": e.params.get("style"), "box_source": e.params.get("box"),
             "track": e.params.get("track", True)} for c, e in _masks(p, None)]
    emit({"masks": rows}, "\n".join(f"{r['id']}  {r['clip']}  {r['style']}  {r['box_source']}" for r in rows) or "(no masks)")


@mask_app.command("set")
def mask_set(mask_id: str, style: Optional[str] = None, strength: Optional[float] = None, color: Optional[str] = None,
             feather: Optional[float] = None, expand: Optional[float] = None, shape: Optional[str] = None,
             tracker: Optional[str] = typer.Option(None, help="auto | segment | outline | box (switching re-tracks, one-time)"),
             sam_mode: Optional[str] = typer.Option(None, "--sam", help="auto | on | off (re-tracks)"),
             invert: Optional[bool] = typer.Option(None, "--invert/--no-invert"),
             keep_box: Optional[str] = typer.Option(None, help="Region that must stay on top (the product in front), "
                                                    "x,y,w,h[;...] on the canvas at --at; 'none' clears it"),
             at: Optional[float] = typer.Option(None, help="Timeline seconds the --keep-box is drawn at")):
    """Change how a mask looks (style changes don't re-track; --tracker / --sam do). MASK_ID 'all' = every mask."""
    from .ops import track

    _check_tracker(tracker, sam_mode)
    if mask_id == "all" and keep_box is not None:
        raise VeditError("--keep-box belongs to one mask: give its id")
    with edit(f"mask {mask_id}") as tx:
        hits = _masks(tx.project, None if mask_id == "all" else mask_id)
        if not hits:
            raise VeditError("No masks in this project")
        for c, e in hits:
            for k, v in (("style", style), ("strength", strength), ("color", color), ("feather", feather),
                         ("expand", expand), ("shape", shape), ("invert", invert), ("tracker", tracker), ("sam", sam_mode)):
                if v is not None:
                    e.params[k] = v
            if keep_box is not None:
                if keep_box.strip().lower() == "none":
                    e.params.pop("keep", None)
                    continue
                if at is None:
                    raise VeditError("Give --at <timeline seconds> where the --keep-box is right")
                hit, t_src, u = track.locate(tx.project, at)
                if hit is not c:
                    raise VeditError(f"--at {at} is not inside {c.id}, the clip of {mask_id}")
                e.params["keep"] = track.keep_region(tx.project, c, u, keep_box)
                e.params["keep"]["at"] = round(t_src, 3)
    emit({"mask": mask_id}, f"Updated {mask_id}")


@mask_app.command("find")
def mask_find(at: float = typer.Option(..., help="Timeline seconds of a frame where the thing to hide is clearly visible"),
              near: Optional[str] = typer.Option(None, help="Look closer around this area: x,y,w,h on the canvas (px or %)")):
    """Step 1 of a precise mask: outline every object on one frame and number them (needs SAM). Read the image,
    pick the number that is exactly the thing to hide, and add the mask with that candidate's box (`add` in the output)."""
    from .ops import maskfind, track
    from .render.runner import preview_dir

    d = pdir()
    p = load(d)
    nb = None
    if near:
        c, _, u = track.locate(p, at)
        nb = track.box_to_source(p, c, track.parse_box(near, p.canvas.width, p.canvas.height), u)
    res = maskfind.find(d, p, at, nb, preview_dir(d) / f"find-{at:.2f}.jpg", _log)
    lines = [f"Candidates at {at}s (picture: {res['image']}):"] + [
        f"  {r['n']:>2}  box {r['box_source']:<28} area {r['area']:<7}  -> {r['add']}" for r in res["candidates"]]
    masks = [e.params.get("id") for c, e in _masks(p, None) if c.id == res["clip"]]
    if masks and res["candidates"]:
        res["fix_hint"] = f"To correct an existing mask here instead: vedit mask fix {masks[0]} --at {at:g} --space source --box <candidate box>"
        lines.append(res["fix_hint"])
    emit(res, "\n".join(lines) if res["candidates"] else f"No objects found at {at}s ({res['image']})")


@mask_app.command("check")
def mask_check(mask_id: str = typer.Argument(..., help="Mask id, or 'all'"),
               verify: bool = typer.Option(True, "--verify/--no-verify", help="Re-outline sample frames with SAM to catch drift")):
    """Step 3: check a tracked mask (tracks it first if needed). Prints how the object moves (size, turning, speed,
    edge, hidden), frames that look wrong, advice with exact fix commands, and a check-sheet image to Read."""
    from .ops import maskcheck
    from .render.runner import preview_dir

    d = pdir()
    p = load(d)
    hits = _masks(p, None if mask_id == "all" else mask_id)
    if not hits:
        raise VeditError("No masks in this project")
    out = []
    for c, e in hits:
        out.append(maskcheck.check(d, p, c, e, preview_dir(d) / f"maskcheck-{e.params.get('id')}.jpg", _log, verify))
    txt = []
    for r in out:
        mo = r["motion"]
        txt.append(f"{r['mask']} on {r['clip']} ({r['tracker']}): {r['verdict'].upper()}  {r['range'][0]:.2f}-{r['range'][1]:.2f}s")
        if mo:
            txt.append(f"  motion: size x{mo['scale_min']}-x{mo['scale_max']}, turns {mo['rotation_deg']}deg, shape change "
                       f"{mo['shape_change']:.0%}, speed up to {mo['max_speed']:.0%} of the frame/s, at the frame edge "
                       f"{mo['edge_share']:.0%}, visible {mo['visible_share']:.0%}")
        if r["agreement"]["checked"]:
            ag = r["agreement"]
            txt.append(f"  SAM re-check on {ag['checked']} frames: covers the object {ag['cover_mean']:.0%} on average, "
                       f"{ag['cover_min']:.0%} at worst (outline overlap {ag['mean']:.0%})")
        for hd in r["hidden"]:
            txt.append(f"  no mask {hd['from']:.2f}-{hd['to']:.2f}s: {hd['why']}")
        for it in r["issues"]:
            txt.append(f"  ! {it['t']:.2f}s {it['kind']}: {it['detail']}")
        txt += [f"  -> {a}" for a in r["advice"]]
        if r["sheet"]:
            txt.append(f"  Check sheet: {r['sheet']}")
    emit({"masks": out} if mask_id == "all" else out[0], "\n".join(txt))


@mask_app.command("fix")
def mask_fix(mask_id: str,
             at: Optional[float] = typer.Option(None, help="Timeline seconds where the mask is wrong"),
             box: Optional[str] = typer.Option(None, help="Where the object really is at --at: x,y,w,h (px or %); several "
                                               "joined with ';' for an object in parts"),
             space: str = typer.Option("canvas", help="canvas (what you see in frames) | source (the check sheet's grid)"),
             clear: bool = typer.Option(False, "--clear", help="Remove all fixes from this mask"),
             undo_at: Optional[float] = typer.Option(None, "--remove-at", help="Remove the fix at these timeline seconds")):
    """Step 4: correct a mask where it's wrong. The fix is a reference for the tracker from that moment on, so one
    fix repairs a whole stretch. Switches the mask to the segment tracker (SAM 2 video), which re-tracks (one-time)."""
    from .ops import sam, track

    with edit(f"fix mask {mask_id}") as tx:
        p = tx.project
        hits = _masks(p, mask_id)
        c, e = hits[0]
        fixes = list(e.params.get("fixes") or [])
        if clear:
            fixes = []
        elif undo_at is not None:
            hit, t_src, _ = track.locate(p, undo_at)
            fixes = [f for f in fixes if abs(float(f["at"]) - t_src) > 0.5 / max(1.0, p.canvas.fps)]
        else:
            if at is None or not box:
                raise VeditError("Give --at <timeline seconds> and --box x,y,w,h (where the object really is then)")
            hit, t_src, u = track.locate(p, at)
            if hit is not c:
                raise VeditError(f"--at {at} is not inside {c.id}, the clip of {mask_id}")
            src = p.sources[c.source]
            boxes = []
            for part in (b for b in box.split(";") if b.strip()):
                if space == "source":
                    sw, sh = src.width or 1080, src.height or 1920
                    x, y, w, h = track.parse_box(part, sw, sh)
                    boxes.append([round(x / sw, 5), round(y / sh, 5), round(w / sw, 5), round(h / sh, 5)])
                else:
                    boxes.append(track.box_to_source(p, c, track.parse_box(part, p.canvas.width, p.canvas.height), u))
            fixes = [f for f in fixes if abs(float(f["at"]) - t_src) > 1e-3] + [{"at": round(t_src, 3), "boxes": boxes}]
            fixes.sort(key=lambda f: f["at"])
            if e.params.get("tracker") != "segment":
                e.params["tracker"] = "segment"
            if e.params.get("sam") == "off" or not sam.installed():
                e.params["sam"] = "on"  # fixes need SAM 2 (installed on the next render when missing)
        if fixes:
            e.params["fixes"] = fixes
        else:
            e.params.pop("fixes", None)
    emit({"mask": mask_id, "fixes": fixes, "tracker": e.params.get("tracker")},
         f"{mask_id}: {len(fixes)} fix(es); re-tracks on the next preview (one-time). Then run: vedit mask check {mask_id}")


@mask_app.command("remove")
def mask_remove(mask_id: str = typer.Argument(..., help="Mask id, or 'all'")):
    """Remove a mask (or all masks)."""
    with edit(f"remove mask {mask_id}") as tx:
        hits = _masks(tx.project, None if mask_id == "all" else mask_id)
        for c, e in hits:
            c.effects.remove(e)
    emit({"removed": len(hits)}, f"Removed {len(hits)} mask(s)")


@focus_app.command("add")
def focus_add(
    box: Optional[str] = typer.Option(None, help="x,y,w,h of the object on the canvas (px or %)"),
    obj: Optional[str] = typer.Option(None, "--object", help="Detect it: person, car, dog, bottle, cell phone, any..."),
    paint: Optional[str] = typer.Option(None, help='Object painted with a brush, JSON in canvas px: [{"pts":[[x,y],...],"r":20}, '
                                        '{"rect":[x,y,w,h]}, {"rect":[...],"erase":true}] (hugs its shape)'),
    clip: Optional[str] = typer.Option(None, help="Clip (default: the clip under --at)"),
    at: Optional[float] = typer.Option(None, help="Timeline seconds where the box is right"),
    mode: str = typer.Option("spotlight", help="spotlight (highlight it in place) | follow (camera pans with it) | both"),
    style: str = typer.Option("spotlight", help="How to highlight: spotlight (blur + dim around) | colorpop (rest black & white) | "
                              "brighten (lift the object) | glow (coloured halo) | outline (coloured stroke)"),
    color: Optional[str] = typer.Option(None, help="glow / outline colour (default #FFD400)"),
    width: Optional[float] = typer.Option(None, help="glow / outline width in px at 1080p (default 30 glow, 6 outline)"),
    strength: Optional[float] = typer.Option(None, help="colorpop / brighten / glow strength 0-2 (default 1)"),
    zoom: Optional[float] = typer.Option(None, help="follow: how tight the shot is (default: 1.0 when reframing wide video, else 1.4)"),
    blur: float = typer.Option(14, help="spotlight: background blur sigma (0 = none)"),
    dim: float = typer.Option(0.35, help="spotlight: background darkening 0-1"),
    feather: Optional[float] = typer.Option(None, help="Edge softness (default 0.12 for a box, 0.03 painted)"),
    expand: Optional[float] = typer.Option(None, help="Room around the object (default 1.35 for a box, 1.0 painted)"),
    smooth: float = typer.Option(0.6, help="Tracking smoothing 0-0.95"),
    track_: bool = typer.Option(True, "--track/--no-track"),
    space: str = typer.Option("canvas", help="canvas | source"),
    tracker: str = typer.Option("auto", help="auto | segment (SAM 2 video, every frame) | outline (follow the outline) | box"),
    sam_mode: str = typer.Option("auto", "--sam", help="SAM 2.1 outline from your box: auto | on | off"),
):
    """Put the focus on one object: highlight it (spotlight, colour pop, glow, outline...) or reframe to follow it."""
    from .ops import track

    try:
        paint_items = json.loads(paint) if paint else None
    except json.JSONDecodeError as e:
        raise VeditError(f"--paint must be JSON: {e}") from None
    d = pdir()
    with edit(f"focus ({mode}, {style})") as tx:
        p = tx.project
        if clip is None and at is None and len(p.clips) > 1:
            raise VeditError("Say which clip with --clip, or give --at <timeline seconds>")
        c, params, note = _region(p, d, clip, at, box, obj, space, paint_items)
        _check_tracker(tracker, sam_mode)
        params = track.focus_params(params, mode, style, color=color, width=width, strength=strength, blur=blur, dim=dim,
                                    feather=feather, expand=expand, smooth=smooth, track=track_, zoom=zoom)
        params.update({"tracker": tracker, "sam": sam_mode})
        c.effects = [e for e in c.effects if e.type != "focus"] + [Effect(type="focus", params=params)]
    emit({"clip": c.id, "mode": mode, "style": style, "shape": params["shape"], "box_source": params["box"], "note": note},
         f"Focus ({mode}, {style}) on {c.id}{' (' + note + ')' if note else ''}. Tracking runs on the next preview (one-time).")


@focus_app.command("set")
def focus_set(
    clip: Optional[str] = typer.Option(None, help="Only this clip (default: every clip with a focus)"),
    style: Optional[str] = typer.Option(None, help="spotlight | colorpop | brighten | glow | outline"),
    color: Optional[str] = typer.Option(None, help="glow / outline colour"),
    width: Optional[float] = typer.Option(None, help="glow / outline width (px at 1080p)"),
    strength: Optional[float] = typer.Option(None, help="0-2"),
    blur: Optional[float] = typer.Option(None, help="spotlight: background blur"),
    dim: Optional[float] = typer.Option(None, help="spotlight: background darkening 0-1"),
):
    """Change how a focused object is highlighted (instant: the tracking is reused)."""
    from .ops import track

    vals = {k: v for k, v in {"style": style, "color": color, "width": width, "strength": strength, "blur": blur, "dim": dim}.items()
            if v is not None}
    if not vals:
        raise VeditError("Give at least one of --style --color --width --strength --blur --dim")
    if style is not None and style not in track.FOCUS_STYLES:
        raise VeditError(f"--style must be one of {', '.join(track.FOCUS_STYLES)}")
    with edit("focus look") as tx:
        hits = [e for c in ([tx.project.clip(clip)] if clip else tx.project.clips) for e in c.effects if e.type == "focus"]
        if not hits:
            raise VeditError("No focus on these clips. Use `vedit focus add` first.")
        for e in hits:
            e.params.update(vals)
    emit({"updated": len(hits), **vals}, f"Focus updated on {len(hits)} clip(s)")


@focus_app.command("clear")
def focus_clear(clip: Optional[str] = None):
    """Remove object focus."""
    with edit("clear focus") as tx:
        ids = _clear_effect(tx.project, clip, ("focus",))
    emit({"clips": ids}, "Focus cleared")


# ---- music ------------------------------------------------------------------------------

# ---- audit & justification ------------------------------------------------------------

@app.command("audit")
def audit_cmd(render: bool = typer.Option(True, help="Render the preview to measure audio loudness (cached if unchanged)"),
              ai_review: bool = typer.Option(False, "--ai", help="Also run the independent AI reviewer on the rendered frames (one vision call)"),
              quality: str = typer.Option("balanced", help="AI quality tier for --ai: best | balanced | economy")):
    """Check the edit before calling it done: hook, readability, safe zones, overlaps, fonts, colour, pacing,
    footage reuse, loudness and stated reasons. Fix every must-fix finding."""
    from .audit import audit

    d = pdir()
    if ai_review:
        from .review import full_audit

        r = full_audit(d, use_ai=True, quality=quality)
    else:
        r = audit(load(d), d, render=render)
    lines = ([f"AI: {r['summary']}  scores: " + ", ".join(f"{k} {v}/5" for k, v in r["scores"].items())] if r.get("scores") else []) + [
        f"Score {r['score']}/100 · {'PASSED' if r['passed'] else 'NOT PASSED'} · "
             f"{r['counts']['must-fix']} must-fix, {r['counts']['should-fix']} should-fix, {r['counts']['note']} notes"]
    for f in r["findings"]:
        at = f" @{f['at']:g}s" if f["at"] is not None else ""
        lines.append(f"  [{f['severity']}] {f['category']}{at}: {f['message']}" + (f"\n      fix: {f['fix']}" if f["fix"] else ""))
    emit(r, "\n".join(lines))


@fx_app.command("list")
def fx_list():
    """The signature move library."""
    from .ops import moves

    rows = moves.catalog()
    emit({"moves": rows}, "\n".join(f"  {r['name']:<16} [{r['needs']}]  {r['about']}" for r in rows))


@fx_app.command("apply")
def fx_apply(name: str, clip: Optional[str] = typer.Option(None, help="Clip the move applies to (clip moves)"),
             at: Optional[float] = typer.Option(None, help="Timeline seconds (moment moves)"),
             text: Optional[str] = typer.Option(None, help="Word / line (freeze_title, keyword_pop, growth_curve label "
                                                           "'+340%'); items separated by ';' (checklist, headline_storm)"),
             source: Optional[str] = typer.Option(None, help="broll_overlay: the imported video to show (s2)"),
             dur: Optional[float] = typer.Option(None, help="How long it lasts (zooms: hold, then cut back)"),
             amount: Optional[float] = typer.Option(None, help="Zooms: how close (1.35); growth_curve: how steep (4); zoom_portal: dive (2.6)"),
             box: Optional[str] = typer.Option(None, help="hud_lock: the target as x,y,w,h in % of the frame, e.g. 30,35,40,25")):
    """Apply one signature move / effect as one undoable step (`vedit fx list` shows them)."""
    from .ops import moves

    with edit(f"move {name}") as tx:
        msg = moves.apply(tx.project, name, clip=clip, at=at, text=text, source=source, dur=dur, amount=amount, box=box)
    emit({"move": name, "done": msg}, msg)


def _kv(pairs: list[str] | None) -> dict:
    out = {}
    for p in pairs or []:
        k, sep, v = p.partition("=")
        if not sep:
            raise VeditError(f"Use name=value, got '{p}'")
        out[k.strip()] = v.strip()
    return out


@fx_app.command("filters")
def fx_filters_list():
    """The clip filter library (blur, vignette, grain, film, VHS, shake...) with their settings."""
    from .ops import fx_filters

    rows = fx_filters.catalog()
    lines = []
    for r in rows:
        ps = ", ".join(f"{p['name']}={p['default']}" + (f" ({'|'.join(p['choices'])})" if p["choices"] else f" [{p['min']:g}..{p['max']:g}]")
                       for p in r["params"])
        lines.append(f"  {r['key']:<11} {r['category']:<15} {r['about']}" + (f"\n              {ps}" if ps else ""))
    emit({"filters": rows}, "\n".join(lines))


@fx_app.command("add")
def fx_add(key: str, clip: Optional[str] = typer.Option(None, help="Clip id (default: every clip)"),
           layer: Optional[str] = typer.Option(None, help="An adjustment layer or video overlay instead of clips"),
           set_: Optional[list[str]] = typer.Option(None, "--set", help="Setting name=value (repeatable), e.g. --set amount=20"),
           start: Optional[float] = typer.Option(None, help="Only from this many seconds into the clip / layer"),
           end: Optional[float] = typer.Option(None, help="Only until this many seconds into the clip / layer"),
           fade_in: Optional[float] = typer.Option(None, help="Fade the filter in over S seconds"),
           fade_out: Optional[float] = typer.Option(None, help="Fade the filter out over S seconds")):
    """Add a filter to a clip (or all clips), a video overlay or an adjustment layer. It stacks after the other filters.
    --start / --end / --fade-in / --fade-out limit it to part of the clip (seconds from the clip's own start)."""
    from .ops import fx_filters

    timing = {k: v for k, v in dict(start=start, end=end, fade_in=fade_in, fade_out=fade_out).items() if v is not None}
    with edit(f"fx add {key}") as tx:
        targets = [tx.project.layer(layer)] if layer else [tx.project.clip(clip)] if clip else tx.project.clips
        effs = [fx_filters.add(tx.project, c, key, _kv(set_)) for c in targets]
        if timing:
            for e in effs:
                fx_filters.set_params(tx.project, e.params["id"], timing)
    ids = [e.params["id"] for e in effs]
    emit({"added": ids, "clips": [c.id for c in targets]}, f"{key} added: " + ", ".join(f"{i} on {c.id}" for i, c in zip(ids, targets)))


@fx_app.command("set")
def fx_set(fx_id: str, values: list[str] = typer.Argument(..., help="name=value pairs; on=false switches it off; "
                                                                     "start= end= fade_in= fade_out= time it (seconds from its clip's start, end=none: to the end)")):
    """Change a filter's settings or timing, e.g. `vedit fx set fx1 amount=30 on=true` or `vedit fx set fx1 start=1 end=2.5 fade_in=0.3`."""
    from .ops import fx_filters

    with edit(f"fx set {fx_id}") as tx:
        eff = fx_filters.set_params(tx.project, fx_id, _kv(values))
    emit({"id": fx_id, "params": eff.params}, f"{fx_id}: {eff.params}")


@fx_app.command("remove")
def fx_remove(fx_id: str = typer.Argument(..., help="Filter id, or 'all'"),
              clip: Optional[str] = typer.Option(None, help="With 'all': only this clip")):
    """Remove a filter (or all filters)."""
    from .ops import fx_filters

    with edit(f"fx remove {fx_id}") as tx:
        if fx_id == "all":
            n = 0
            for c in ([tx.project.clip(clip)] if clip else tx.project.clips):
                before = len(c.effects)
                c.effects = [e for e in c.effects if e.type != "fx"]
                n += before - len(c.effects)
        else:
            fx_filters.remove(tx.project, fx_id)
            n = 1
    emit({"removed": n}, f"Removed {n} filter(s)")


@fx_app.command("replace")
def fx_replace(fx_id: str, key: str = typer.Argument(..., help="The filter to swap in (see `vedit fx filters`)")):
    """Swap an applied filter for another, keeping its place in the stack, its timing and on/off (settings reset)."""
    from .ops import fx_filters

    with edit(f"fx replace {fx_id} -> {key}") as tx:
        e = fx_filters.replace(tx.project, fx_id, key)
    emit({"id": fx_id, "params": e.params}, f"{fx_id} is now {key}")


@fx_app.command("move")
def fx_move(fx_id: str, index: int = typer.Argument(..., help="New position among the clip's filters (0 = first)")):
    """Reorder a clip's filters (they run top to bottom)."""
    from .ops import fx_filters

    with edit(f"fx move {fx_id}") as tx:
        fx_filters.move(tx.project, fx_id, index)
    emit({"id": fx_id, "index": index}, f"{fx_id} -> position {index}")


@fx_app.command("copy")
def fx_copy(src: str = typer.Option(..., "--from", help="Clip whose filters to copy"),
            to: Optional[list[str]] = typer.Option(None, help="Target clip (repeatable; default: all clips)")):
    """Copy one clip's filters onto other clips (replacing their filters)."""
    from .ops import fx_filters

    with edit(f"fx copy {src}") as tx:
        targets = [tx.project.clip(t) for t in to] if to else tx.project.clips
        n = fx_filters.copy_to(tx.project, tx.project.clip(src), targets)
    emit({"copied_to": n}, f"Copied {src}'s filters to {n} clip(s)")


@version_app.command("save")
def version_save(name: str = typer.Argument("", help="e.g. 'client v1' (default: the date and time)")):
    """Name the edit as it is now. `vedit checkout <step>` (or the History tab) goes back to it."""
    r = history.save_version(pdir(), name)
    emit(r, f"Saved version '{r['name']}' (step {r['step']})")


@version_app.command("list")
def version_list():
    """Named versions, oldest first."""
    rows = history.versions(pdir())
    emit({"versions": rows}, "\n".join(f"  step {r['step']:<4} {r['name']}" for r in rows) or "No named versions yet")


@template_app.command("save")
def template_save(name: str, layer: list[str] = typer.Option(..., "--layer", "-l", help="Layer id (repeatable)"), about: str = ""):
    """Save overlay layers as a template (timing relative to the first one)."""
    from .ops import templates

    d = pdir()
    data = templates.save(load(d), d, name, layer, about)
    emit({"template": name, "layers": len(data["layers"])}, f"Saved template '{name}' ({len(data['layers'])} layer(s))")


@template_app.command("list")
def template_list():
    """Saved templates."""
    from .ops import templates

    rows = templates.catalog()
    emit({"templates": rows}, "\n".join(f"  {r['key']:<20} {r['layers']} layer(s)  {', '.join(r['types'])}  {r['about']}" for r in rows) or "No templates yet")


@template_app.command("apply")
def template_apply(name: str, at: float = typer.Option(0.0, help="Timeline seconds where the template starts")):
    """Add a template's layers at a moment."""
    from .ops import templates

    d = pdir()
    with edit(f"template {name}") as tx:
        ids = templates.apply(tx.project, d, name, at)
    emit({"layers": ids}, f"Added {', '.join(ids)}")


@template_app.command("delete")
def template_delete(name: str):
    """Delete a saved template."""
    from .ops import templates

    templates.delete(name)
    emit({"deleted": name}, f"Deleted template {name}")


@adjust_app.command("add")
def adjust_add(start: float = 0.0, end: Optional[float] = typer.Option(None, help="Default: the end of the timeline"),
               look: Optional[str] = typer.Option(None, help="Colour look for this stretch (see `color looks`)"),
               intensity: float = 1.0, saturation: Optional[float] = None, brightness: Optional[float] = None,
               contrast: Optional[float] = None, temperature: Optional[float] = None,
               fx: Optional[list[str]] = typer.Option(None, help="Filter to add (repeatable), e.g. --fx vignette --fx grain")):
    """Grade or filter a whole stretch of the edit at once (e.g. a black & white flashback, a warm ending)."""
    from .ops import color, fx_filters
    from .project import Grade

    with edit("adjustment layer") as tx:
        p = tx.project
        layer = Layer(id=p.new_id("adj"), type="adjust", start=start, end=end)
        vals = {k: v for k, v in (("saturation", saturation), ("brightness", brightness), ("contrast", contrast),
                                  ("temperature", temperature)) if v is not None}
        if look:
            vals["look"], vals["intensity"] = color.look_name(look), intensity
        if vals:
            layer.grade = Grade(**vals)
        for key in fx or []:
            fx_filters.add(p, layer, key)
        p.layers.append(layer)
    emit({"layer": layer.id}, f"Added adjustment layer {layer.id} ({start:g}s-{'end' if end is None else f'{end:g}s'})")


@proxy_app.command("make")
def proxy_make():
    """Build proxies for every source larger than 1280 px (the editor does this by itself in the background)."""
    from . import proxies

    d = pdir()
    p = load(d)
    made = []
    for sid, src in p.sources.items():
        if proxies.needs_proxy(src) and not proxies.available(d, src):
            proxies.make(d, src)
            made.append(sid)
    emit({"made": made, "status": proxies.status(d, p)}, f"Made {len(made)} proxy(ies)" if made else "All proxies ready")


@proxy_app.command("status")
def proxy_status():
    """Which sources have a proxy."""
    from . import proxies

    d = pdir()
    rows = proxies.status(d, load(d))
    emit({"sources": rows}, "\n".join(f"  {r['source']:<5} {r['size']:<11} " + ("ready" if r["ready"] else "needed" if r["needed"] else "not needed (small)")
                                     for r in rows) or "No video sources")


@proxy_app.command("clean")
def proxy_clean():
    """Delete proxies of sources that are no longer in the project."""
    from . import proxies

    d = pdir()
    n = proxies.clean(d, load(d))
    emit({"removed": n}, f"Removed {n} unused proxy(ies)")


@marker_app.command("add")
def marker_add(at: float = typer.Option(..., help="Timeline seconds"), label: str = "", color: Optional[str] = None):
    """Mark a moment (a beat to hit, a chapter start, a note to self)."""
    from .ops import editing

    with edit(f"marker {label or at}") as tx:
        m = editing.add_marker(tx.project, at, label, color)
    emit({"marker": m.id, "t": m.t}, f"Marker {m.id} at {m.t:g}s {m.label}")


@marker_app.command("list")
def marker_list():
    """All markers, in time order."""
    p = load(pdir())
    emit({"markers": [m.model_dump() for m in p.markers]},
         "\n".join(f"  {m.id:<4} {m.t:7.2f}s  {m.label}" for m in p.markers) or "No markers")


@marker_app.command("set")
def marker_set(marker_id: str, at: Optional[float] = None, label: Optional[str] = None, color: Optional[str] = None):
    """Move or rename a marker."""
    from .ops import editing

    with edit(f"marker {marker_id}") as tx:
        m = editing.set_marker(tx.project, marker_id, at, label, color)
    emit({"marker": m.id, "t": m.t, "label": m.label}, f"{m.id}: {m.t:g}s {m.label}")


@marker_app.command("remove")
def marker_remove(marker_id: str = typer.Argument(..., help="Marker id, or 'all'")):
    """Remove a marker (or all)."""
    from .ops import editing

    with edit(f"remove marker {marker_id}") as tx:
        if marker_id == "all":
            tx.project.markers = []
        else:
            editing.remove_marker(tx.project, marker_id)
    emit({"removed": marker_id}, f"Removed {marker_id}")


@marker_app.command("chapters")
def marker_chapters():
    """YouTube chapter lines from the labelled markers (paste into the description)."""
    from .ops import editing

    lines, issues = editing.chapters(load(pdir()))
    emit({"chapters": lines, "issues": issues}, "\n".join(lines + [f"! {i}" for i in issues]))


@sound_app.command("fade")
def sound_fade(clip: Optional[str] = typer.Option(None, help="Clip id (default: every clip)"),
               in_: Optional[float] = typer.Option(None, "--in", help="Fade-in seconds"),
               out: Optional[float] = typer.Option(None, help="Fade-out seconds")):
    """Fade a clip's sound in / out (no click at hard cuts). The picture is unchanged."""
    with edit("sound fade") as tx:
        targets = [tx.project.clip(clip)] if clip else tx.project.clips
        for c in targets:
            if in_ is not None:
                c.fade_in = max(0.0, min(in_, c.duration / 2))
            if out is not None:
                c.fade_out = max(0.0, min(out, c.duration / 2))
    emit({"clips": [c.id for c in targets]}, f"Sound fades on {len(targets)} clip(s)")


@sound_app.command("voice")
def sound_voice(mode: str = typer.Argument(..., help="clean | studio | off"),
                clip: Optional[str] = typer.Option(None, help="Clip id (default: every clip)")):
    """Clean up speech: clean = remove rumble / hiss and even the level; studio = also warmer, clearer, de-essed."""
    if mode not in ("clean", "studio", "off"):
        raise VeditError("mode must be clean, studio or off")
    with edit(f"voice {mode}") as tx:
        targets = [tx.project.clip(clip)] if clip else tx.project.clips
        for c in targets:
            c.voice = mode  # type: ignore[assignment]
    emit({"clips": [c.id for c in targets], "voice": mode}, f"Voice {mode} on {len(targets)} clip(s)")


@sound_app.command("report")
def sound_report():
    """Each clip's own sound (level, speech score, volume) and the sound findings of the audit."""
    from . import sound

    d = pdir()
    p = load(d)
    rows, findings = sound.report(p, d), sound.check_sound(p, d)
    lines = [f"  {r['clip']} @{r['at']:g}s: " + (f"{'speech' if r['speech'] else 'no speech'}, {r['db']} dB (speech score {r['voice']}), volume {r['volume']:g}"
                                               if r["db"] is not None else "no audio") + (f" [{r['mode']}]" if r["mode"] else "") for r in rows]
    lines += [f"  [{f['severity']}] {f['message']}" + (f"  fix: {f['fix']}" if f["fix"] else "") for f in findings]
    emit({"clips": rows, "findings": findings}, "\n".join(lines) or "No clips")


@sound_app.command("auto")
def sound_auto():
    """Keep speech, mute background-only footage under the music, duck the music under speech."""
    from . import sound

    d = pdir()
    with edit("sound auto") as tx:
        changes = sound.auto(tx.project, d)
        if not changes:
            tx.cancelled = True
    emit({"changes": changes}, "\n".join(changes) or "Nothing to change")


@app.command("audit-settings")
def audit_settings_cmd(threshold: Optional[int] = typer.Option(None, help="Pass mark 0-100: edits below it go back to the editor"),
                       max_rounds: Optional[int] = typer.Option(None, help="Max revision rounds (0-8) before the user decides")):
    """Show or change when an edit is good enough (used by generation and the desktop editor's auditor)."""
    from .review import save_settings, settings

    cur = save_settings(threshold, max_rounds) if threshold is not None or max_rounds is not None else settings()
    emit(cur, f"Pass mark {cur['threshold']}/100, at most {cur['max_rounds']} revision round(s)")


@app.command("taste")
def taste_cmd(clear: bool = typer.Option(False, help="Forget everything learned from Keep / Discard"),
              clear_marks: bool = typer.Option(False, help="Forget what the auditor learned about your footage segments")):
    """What generation has learned: from the videos you kept and discarded, and about your footage."""
    from .generate import taste

    if clear_marks:
        from .generate import footage

        n = footage.clear_marks()
        return emit({"cleared_marks": n}, f"Cleared reviewer marks on {n} footage file(s)")
    if clear:
        taste.clear()
        return emit({"cleared": True}, "Taste memory cleared")
    rows = taste.load()
    emit({"rows": rows[-40:], "prompt": taste.examples()}, taste.examples() or "Nothing learned yet: keep or discard some generated videos.")


@app.command("ai-models")
def ai_models_cmd(vision: Optional[str] = None, planner: Optional[str] = None, editor: Optional[str] = None,
                  auditor: Optional[str] = typer.Option(None, help="Model for the independent reviewer (default: the editor's)"),
                  clear: bool = typer.Option(False, help="Remove all overrides")):
    """Show or override which model each AI task tier uses ('<provider>/<model>', see Manage models)."""
    from .ai import client as ai

    cur = {} if clear else ai.settings()
    for k, v in dict(vision=vision, planner=planner, editor=editor, auditor=auditor).items():
        if v is not None:
            cur[k] = v
    if clear or any(v is not None for v in (vision, planner, editor, auditor)):
        cur = ai.save_settings(cur)
    roles = ai.roles()
    emit({"overrides": cur, "effective": {r["tier"]: r["effective"] for r in roles}, "roles": roles},
         "\n".join(f"  {r['label']:<18} ({r['tier']:<7}) {r['effective']}" + ("  (your default)" if r["saved"] else "  (quality default)")
                   + f"\n      {r['about']}" for r in roles))


@app.command("why")
def why_cmd(
    item: str = typer.Argument(..., help="Clip id (c3) or layer id (t1)"),
    reason: str = typer.Argument(..., help="Why it is here: what it shows and what it does for the viewer (max ~15 words)"),
    role: Optional[str] = typer.Option(None, help="hook | problem | setup | demo | proof | benefit | payoff | cta | other"),
    beat: Optional[str] = typer.Option(None, help="Beat id from the edit's beat sheet (b1, b2...)"),
):
    """Record why a clip or overlay is in the edit (shown in the inspector, checked by the audit)."""
    from .project import ROLES, ClipMeta

    if role is not None and role not in ROLES:
        raise VeditError(f"role must be one of {', '.join(ROLES)}")
    reason = reason.strip()
    if not reason:
        raise VeditError("Give a reason")
    with edit(f"why {item}") as tx:
        p = tx.project
        if any(c.id == item for c in p.clips):
            c = p.clip(item)
            c.meta = (c.meta or ClipMeta()).model_copy(update={k: v for k, v in dict(why=reason[:200], role=role, beat=beat).items()
                                                                if v is not None})
        else:
            p.layer(item).why = reason[:200]
    emit({"item": item, "why": reason, "role": role, "beat": beat}, f"{item}: {reason}")


# ---- colour -------------------------------------------------------------------------

def _clip_list(clip: Optional[list[str]]) -> Optional[list[str]]:
    return list(clip) if clip else None


@color_app.command("looks")
def color_looks():
    """Named colour looks."""
    from .ops import color

    emit({"looks": {k: v["about"] for k, v in color.LOOKS.items()}},
         "\n".join(f"  {k:<14} {v['about']}" for k, v in color.LOOKS.items()))


@color_app.command("stats")
def color_stats():
    """Measured colour of every clip, and how consistent the edit is (spread: lower = more uniform)."""
    from .ops import color

    d = pdir()
    p = load(d)
    r = color.consistency(p, d)
    r["grades"] = {c.id: color.describe(c.grade) for c in p.clips}
    human = "\n".join(f"  {cid}: luma {s['luma']:.2f}  sat {s['sat']:.2f}  warmth {s['warmth']:+.3f}  tint {s['tint']:+.3f}  | {r['grades'][cid]}"
                      for cid, s in r.get("per_clip", {}).items())
    emit(r, human + f"\nSpread: {r.get('spread_before', 0)} measured -> {r.get('spread_after', 0)} after grading")


@color_app.command("match")
def color_match(
    to: Optional[str] = typer.Option(None, "--to", help="Hero clip id: match every other clip to this one"),
    ref: Optional[int] = typer.Option(None, "--ref", help="Reference id (`vedit ref list`): match to that video's colour"),
    video: Optional[str] = typer.Option(None, "--video", help="Any video file: match to its colour"),
    strength: float = typer.Option(0.8, help="0..1, how far to move each clip toward the target"),
    clip: Optional[list[str]] = typer.Option(None, help="Only these clips (repeatable)"),
):
    """Make clips look like one shoot. Default target: the average of all clips."""
    from .ops import color

    target, label = None, None
    if ref is not None:
        from .references import store

        target = store.color_of(ref)
        if not target:
            raise VeditError(f"Could not measure the colour of reference {ref}")
        label = f"ref {ref}"
    elif video:
        path = Path(video).expanduser()
        if not path.exists():
            raise VeditError(f"Video not found: {path}")
        target = color.stats(path, 0.0, media.probe(path)["duration"], samples=12)
        label = path.name
    d = pdir()
    with edit("colour match" + (f" to {label or to}" if (label or to) else "")) as tx:
        r = color.match_clips(tx.project, d, target=target, hero=to, strength=strength, clip_ids=_clip_list(clip), label=label)
    after = color.consistency(load(d), d)
    r.update({k: after.get(k) for k in ("spread_before", "spread_after")})
    emit(r, f"Matched {len(r['clips'])} clips to {r['matched_to']}. Colour spread {r['spread_before']} -> {r['spread_after']}")


@color_app.command("look")
def color_look(name: str, intensity: float = typer.Option(1.0, help="0..1"),
               clip: Optional[list[str]] = typer.Option(None, help="Only these clips (default: all, for a consistent grade)"),
               layer: Optional[str] = typer.Option(None, help="A video overlay or adjustment layer instead of clips")):
    """Apply a creative colour look on top of the correction. `natural` removes it."""
    from .ops import color

    if layer:
        from .ops import tracks

        with edit(f"colour look {name} on {layer}") as tx:
            tracks.grade_layer(tx.project, layer, {"intensity": intensity}, name)
        return emit({"layer": layer, "look": name, "intensity": intensity}, f"Look {name} on {layer}")
    with edit(f"colour look {name}") as tx:
        ids = color.set_look(tx.project, name, intensity, _clip_list(clip))
    emit({"clips": ids, "look": name, "intensity": intensity}, f"Look {name} on {len(ids)} clip(s)")


@color_app.command("set")
def color_set(
    clip: Optional[list[str]] = typer.Option(None, help="Clip id (repeatable; default all)"),
    brightness: Optional[float] = typer.Option(None, help="-0.3..0.3"), contrast: Optional[float] = typer.Option(None, help="0.5..1.6"),
    saturation: Optional[float] = typer.Option(None, help="0 (B&W)..2"), temperature: Optional[float] = typer.Option(None, help="-1 cool..1 warm"),
    tint: Optional[float] = typer.Option(None, help="-1 green..1 magenta"),
    layer: Optional[str] = typer.Option(None, help="A video overlay or adjustment layer instead of clips"),
):
    """Set correction values by hand (replaces what `match` measured for those values)."""
    from .project import Grade

    vals = {k: v for k, v in dict(brightness=brightness, contrast=contrast, saturation=saturation, temperature=temperature, tint=tint).items() if v is not None}
    if not vals:
        raise VeditError("Give at least one of --brightness --contrast --saturation --temperature --tint")
    if layer:
        from .ops import tracks

        with edit(f"colour set on {layer}") as tx:
            tracks.grade_layer(tx.project, layer, vals)
        return emit({"layer": layer, **vals}, f"Colour set on {layer}")
    with edit("colour set " + " ".join(f"{k}={v}" for k, v in vals.items())) as tx:
        targets = [tx.project.clip(i) for i in clip] if clip else tx.project.clips
        for c in targets:
            c.grade = (c.grade or Grade()).model_copy(update=vals)
    emit({"clips": [c.id for c in targets], **vals}, f"Colour set on {len(targets)} clip(s)")


@color_app.command("lut")
def color_lut(file: str, clip: Optional[list[str]] = typer.Option(None, help="Clip id (repeatable; default all)")):
    """Apply a .cube LUT (e.g. a camera's log-to-rec709 LUT, or a purchased film look)."""
    from .project import Grade

    path = Path(file).expanduser()
    if path.suffix.lower() != ".cube" or not path.exists():
        raise VeditError(f"Need an existing .cube file, got {file}")
    d = pdir()
    with edit(f"colour LUT {path.name}") as tx:
        targets = [tx.project.clip(i) for i in clip] if clip else tx.project.clips
        for c in targets:
            c.grade = (c.grade or Grade()).model_copy(update={"lut": store_media_path(d, path)})
    emit({"clips": [c.id for c in targets], "lut": str(path)}, f"LUT {path.name} on {len(targets)} clip(s)")


@color_app.command("clear")
def color_clear(clip: Optional[list[str]] = typer.Option(None, help="Clip id (repeatable; default all)"),
                layer: Optional[str] = typer.Option(None, help="A video overlay or adjustment layer instead of clips")):
    """Remove grading (correction, look and LUT)."""
    if layer:
        from .ops import tracks

        with edit(f"colour clear {layer}") as tx:
            tracks.grade_layer(tx.project, layer, clear=True)
        return emit({"layer": layer}, f"Cleared colour on {layer}")
    with edit("colour clear") as tx:
        targets = [tx.project.clip(i) for i in clip] if clip else tx.project.clips
        for c in targets:
            c.grade = None
    emit({"clips": [c.id for c in targets]}, f"Cleared colour on {len(targets)} clip(s)")


@music_app.command("add")
def music_add(file: str, volume: float = 0.2, start: float = 0.0, offset: float = typer.Option(0.0, help="Start position inside the song"),
              duck: bool = typer.Option(True, help="Lower music automatically while someone talks"),
              loop: bool = True, fade_in: float = 0.5, fade_out: float = 1.5):
    """Add background music."""
    d = pdir()
    path = Path(file).expanduser().resolve()
    info = media.probe(path)
    if not info["has_audio"]:
        raise VeditError(f"{path.name} has no audio")
    with edit(f"add music {path.name}") as tx:
        p = tx.project
        sid = p.new_id("s")
        p.sources[sid] = Source(path=store_media_path(d, path), **info)
        m = MusicTrack(id=p.new_id("m"), source=sid, start=start, in_=offset, volume=volume, duck=duck, loop=loop,
                       fade_in=fade_in, fade_out=fade_out)
        p.music.append(m)
    emit({"music": m.id}, f"Added music {m.id} ({path.name}) at volume {volume}{', ducking under voice' if duck else ''}")


@music_app.command("remove")
def music_remove(music_id: str):
    """Remove a music track."""
    with edit(f"remove music {music_id}") as tx:
        p = tx.project
        before = len(p.music)
        p.music = [m for m in p.music if m.id != music_id]
        if len(p.music) == before:
            raise VeditError(f"No music track {music_id}")
    emit({"removed": music_id}, f"Removed {music_id}")


@music_app.command("set")
def music_set(music_id: str, volume: Optional[float] = None, duck: Optional[bool] = None, start: Optional[float] = None,
              offset: Optional[float] = typer.Option(None, help="Where in the song it starts (trim the song's head)"),
              end: Optional[str] = typer.Option(None, help="Timeline second the music stops, or 'none' to play to the end"),
              loop: Optional[bool] = typer.Option(None, "--loop/--no-loop")):
    """Change music volume / ducking / start, or trim it (--offset into the song, --end on the timeline)."""
    from .ops import tracks

    with edit(f"music {music_id}") as tx:
        e: Any = tracks._KEEP if end is None else None if end.lower() in ("none", "") else float(end)
        m = tracks.set_music(tx.project, music_id, volume, start, offset, e, duck, loop)
    emit({"music": m.model_dump(by_alias=True)}, f"Updated {music_id}")


# ---- history ------------------------------------------------------------------------------

@app.command()
def undo(steps: int = 1):
    """Undo the last edit(s)."""
    r = history.undo(pdir(), steps)
    emit(r, f"Undid: {r['undone']}  (now at step {r['head']}: {r['now_at']})")


@app.command()
def redo(steps: int = 1):
    """Redo an undone edit."""
    r = history.redo(pdir(), steps)
    emit(r, f"Redid: {r['redone']}  (step {r['head']})")


@app.command("history")
def history_cmd(limit: int = 30):
    """List edit history (newest last). Use `vedit checkout N` to jump to any step."""
    log = history.log(pdir(), limit)
    emit({"history": log}, "\n".join(f"{'>' if h['current'] else ' '} {h['step']:>4}  {h['message']}" for h in log))


@app.command()
def checkout(step: int):
    """Jump to any step in the history (later steps stay available for redo)."""
    r = history.checkout(pdir(), step)
    emit(r, f"Now at step {step}: {r['now_at']}")


# ---- rendering ------------------------------------------------------------------------------

@app.command()
def preview(open_: bool = typer.Option(False, "--open", help="Open in the system video player"),
            force: bool = False):
    """Render a fast half-resolution preview (cached until the project changes)."""
    from .render.runner import render_preview

    d = pdir()
    res = render_preview(load(d), d, force, progress=not STATE["json"])
    if open_:
        media.open_file(Path(res["output"]))
    emit(res, f"Preview: {res['output']}{' (cached)' if res.get('cached') else ''}")


@app.command()
def platform(name: Optional[str] = typer.Argument(None, help="tiktok | reels | shorts | all | none (no argument: show it)")):
    """The social app this video is for. Its safe zone (what its buttons, top bar and caption bar leave visible) is
    where text, stickers and buttons go: `vedit safe check|fix`, the audit and the editor's guides use it."""
    from .ops import safezone

    if name is None:
        p = load(pdir())
        z = safezone.zone(p)
        return emit({"platform": p.platform, "zone": z.key, "describe": z.describe(p.canvas.width, p.canvas.height)},
                     f"Platform: {p.platform or 'not set'}. {z.describe(p.canvas.width, p.canvas.height)}")
    with edit(f"platform {name}") as tx:
        key = safezone.set_platform(tx.project, name)
        z = safezone.zone(tx.project)
    emit({"platform": key, "describe": z.describe(tx.project.canvas.width, tx.project.canvas.height)},
         f"Platform: {key or 'none'}. {z.describe(tx.project.canvas.width, tx.project.canvas.height)}")


@safe_app.command("check")
def safe_check(platform_: Optional[str] = typer.Option(None, "--platform", help="Check for another app than the project's")):
    """Where every visible layer is (its real on-screen box, measured) and what pokes out of the safe zone."""
    from .ops import safezone

    p = load(pdir())
    r = safezone.check(p, pdir(), platform_)
    bad = [x for x in r["layers"] if not x["ok"]]
    lines = [r["describe"]] + [f"  {'OK ' if x['ok'] else 'OUT'} {x['layer']} ({x['type']}) box x {x['box'][0]}-{x['box'][2]}, "
                               f"y {x['box'][1]}-{x['box'][3]}" + (": " + "; ".join(x["problems"]) if x["problems"] else "")
                               for x in r["layers"]]
    lines.append("All inside the safe zone." if not bad else f"{len(bad)} outside: `vedit safe fix {' '.join(x['layer'] for x in bad)}`")
    emit(r, "\n".join(lines))


@safe_app.command("fix")
def safe_fix(layers: Optional[list[str]] = typer.Argument(None, help="Layer ids (default: every layer outside the zone)"),
             margin: float = typer.Option(12.0, help="Distance from the zone's edges (px)")):
    """Move layers back inside the safe zone (shrinking only those too big for it); animated paths move along."""
    from .ops import safezone

    with edit("safe fix " + (" ".join(layers) if layers else "all")) as tx:
        p = tx.project
        todo = [p.layer(i) for i in layers] if layers else safezone.checked_layers(p)
        changed = {l.id: c for l in todo if (c := safezone.fit(p, pdir(), l, margin=margin))}
    emit({"changed": changed}, "\n".join(f"  {k}: " + ", ".join(f"{a} {b}" for a, b in v.items()) for k, v in changed.items())
         or "Nothing to fix: everything is inside the safe zone.")


@app.command()
def frame(at: str = typer.Option(..., help="Timeline seconds; several as a list: 2,5.5,9"),
          out: Optional[str] = typer.Option(None, help="Output image (single time only)"),
          width: int = 480,
          guides: bool = typer.Option(False, "--guides", help="Draw the platform's safe zone on the stills (covered parts in red)")):
    """Save stills of the edit (JPG). Claude looks at these to check its work."""
    from .render.runner import preview_dir, render_preview

    d = pdir()
    p = load(d)
    try:
        times = [float(x) for x in at.replace(" ", ",").split(",") if x.strip()]
    except ValueError:
        raise VeditError("--at must be seconds, e.g. 3.5 or 2,5.5,9") from None
    if not times or len(times) > 12:
        raise VeditError("Give between 1 and 12 times (use `vedit sheet` for an overview)")
    for t in times:
        if not 0 <= t <= p.duration:
            raise VeditError(f"--at {t} is outside the timeline (0-{p.duration:.2f}s)")
    res = render_preview(p, d, progress=not STATE["json"])
    images = []
    for t in times:
        dest = Path(out).resolve() if (out and len(times) == 1) else preview_dir(d) / f"frame-{t:.2f}.jpg"
        media.extract_frame(Path(res["output"]), min(t, max(0, p.duration - 0.05)), dest, width)
        if guides:
            from .ops import safezone

            safezone.guides_image(dest, p)
        images.append(str(dest))
    data = {"image": images[0], "images": images} if len(images) == 1 else {"images": images}
    emit(data, "\n".join(f"Frame: {i}" for i in images))


@app.command()
def sheet(count: int = 12, cols: int = 4, width: int = 240):
    """Contact sheet of evenly spaced frames (one image to review the whole edit)."""
    from .render.runner import preview_dir, render_preview

    d = pdir()
    p = load(d)
    res = render_preview(p, d, progress=not STATE["json"])
    dest = preview_dir(d) / "sheet.jpg"
    media.contact_sheet(Path(res["output"]), p.duration, dest, count, cols, width)
    emit({"image": str(dest)}, f"Contact sheet: {dest}")


@app.command()
def export(out: Optional[str] = typer.Argument(None, help="Output file (the extension picks the type: .mp4 .mov .webm .gif). "
                                                          "Default: <folder>/<project name>.<ext>"),
           fmt: Optional[str] = typer.Option(None, "--format", help="mp4 (H.264) | hevc | prores | webm | gif"),
           fps: Optional[float] = typer.Option(None, help="Frames per second (default: the project's)"),
           scale: Optional[float] = typer.Option(None, help="Resolution: 0.5 half, 1 full, 2 double"),
           quality: Optional[str] = typer.Option(None, help="high | standard | small"),
           folder: Optional[str] = typer.Option(None, help="Folder to save into (default: the project's exports/)"),
           name: Optional[str] = typer.Option(None, help="File name without extension (default: the project name)"),
           audio: Optional[bool] = typer.Option(None, "--audio/--no-audio", help="Include sound"),
           open_: bool = typer.Option(False, "--open")):
    """Render the final video. Unset options reuse this project's last export settings; never overwrites a file."""
    from .render import export as ex

    d = pdir()
    p = load(d)
    st = ex.load_settings(d).model_dump()
    if out:
        target = Path(out).expanduser().resolve()
        st["folder"], st["filename"] = str(target.parent), target.stem
        by_ext = {".gif": "gif", ".mov": "prores", ".webm": "webm"}
        if not fmt and target.suffix.lower() in by_ext:
            fmt = by_ext[target.suffix.lower()]
        elif not fmt and target.suffix.lower() == ".mp4":
            fmt = "mp4"
    for k, v in dict(format=fmt, fps=fps, scale=scale, quality=quality, folder=folder, filename=name, audio=audio).items():
        if v is not None:
            st[k] = v
    try:
        settings = ex.ExportSettings.model_validate(st)
    except ValueError as e:
        raise VeditError(f"Bad export option: {e}") from None
    ex.save_settings(d, settings)
    bar = None if STATE["json"] else (lambda f, stage: sys.stderr.write(f"\r{stage}: {f * 100:5.1f}%"))
    res = ex.run(p, d, settings, on_progress=bar)
    if bar:
        sys.stderr.write("\n")
    if open_:
        media.open_file(Path(res["output"]))
    emit(res, f"Exported {res['output']}  {res['width']}x{res['height']} {res['fps']}fps {res['label']}  "
              f"{res['bytes'] / 1048576:.1f} MB  (took {res['seconds']}s)")


@app.command("app")
def app_cmd(
    folder: Optional[str] = typer.Argument(None, help="Open this project directly (created if it doesn't exist). Default: the home screen"),
    browser: bool = typer.Option(False, "--browser", help="Open in the web browser instead of a native window"),
    port: Optional[int] = None,
):
    """Open aiklips: home screen with your projects, then the 4-pane editor."""
    from .editor.window import launch

    d = None
    if folder:
        d = Path(folder).expanduser().resolve()
        if not (d / PROJECT_FILE).exists():
            init(str(d), None, 1080, 1920, 30)
    launch(d, browser=browser, port=port)


@app.command()
def projects():
    """List projects in the library (newest first)."""
    from . import library

    rows = library.list_projects()

    def line(r: dict) -> str:
        info = "missing" if r.get("missing") else f"{r.get('duration', 0):.1f}s, {r.get('clips', 0)} clips"
        return f"{r['id']:>3}  {r['name']:<28} {info:<18} {r['path']}"

    emit({"projects": rows, "root": str(library.projects_root())},
         "\n".join(line(r) for r in rows) or "No projects yet. Create one with `vedit app` or `vedit init projects/<name>`.")


@app.command()
def serve(
    port: int = 5173,
    host: str = "127.0.0.1",
    browser: bool = True,
    dev: bool = typer.Option(False, "--dev", help="Auto-reload: restart on Python changes, refresh the page on HTML/JS changes"),
):
    """Open aiklips in your browser on a fixed port (same as `vedit app --browser`)."""
    from .preview_server.app import run

    try:
        d = pdir()
    except VeditError:
        d = None  # no active project: start on the home screen
    run(d, host, port, browser, dev)


# ---- references + generation ------------------------------------------------------------

ref_app = typer.Typer(help="Reference library: videos the AI studies for style (pacing, text, animation, sound)")
gen_app = typer.Typer(help="Generate N different edits of your footage in the style of references")
app.add_typer(ref_app, name="ref")
app.add_typer(gen_app, name="gen")


@ref_app.command("add")
def ref_add(files: list[str], wait: bool = typer.Option(True, help="Study now and wait (otherwise the app studies them)")):
    """Add reference videos and study them (measure + AI)."""
    from . import jobs
    from .references import store

    jobs.AUTOSTART = False
    rows = [store.add(Path(f)) for f in files]
    if wait:
        jobs.drain(timeout=3600, wait=True)
        rows = [store.get(r["id"]) for r in rows]
    emit({"references": [{k: r.get(k) for k in ("id", "name", "status", "summary", "error")} for r in rows]},
         "\n".join(f"#{r['id']} {r['name']}: {r['status']}  {r.get('summary') or r.get('error') or ''}" for r in rows))


@ref_app.command("list")
def ref_list():
    """List references."""
    from .references import store

    rows = store.list_refs()
    emit({"references": [{k: r.get(k) for k in ("id", "name", "status", "summary", "auto_tags", "duration")} for r in rows]},
         "\n".join(f"#{r['id']:<3} {r['name']:<30} {r['status']:<9} {r.get('summary') or ''}" for r in rows) or "No references yet: vedit ref add <video>")


@ref_app.command("show")
def ref_show(ref_id: int):
    """Show a reference's Style DNA."""
    from .references import store

    r = store.get(ref_id, full=True)
    emit({"reference": {k: r.get(k) for k in ("id", "name", "status", "summary", "dna", "overrides", "error", "recipe", "gaps")}},
         f"#{r['id']} {r['name']} ({r['status']})\n" + "\n".join(f"  {t['label']:<12} {t['value']}" for t in r.get("card", []))
         + (f"\n  Rebuild it: vedit ref recipe {r['id']}" if r.get("recipe") else ""))


@ref_app.command("recipe")
def ref_recipe(ref_id: int):
    """How to rebuild this reference's edit with vedit: ordered commands, plus what vedit can't do exactly."""
    from .references import recipe, store

    r = store.get(ref_id, full=True)
    if not r.get("has_dna"):
        raise VeditError(f"Reference #{ref_id} isn't studied yet ({r['status']})")
    emit({"reference": ref_id, "recipe": r["recipe"], "gaps": r["gaps"]},
         f"#{r['id']} {r['name']}: rebuild in vedit (<...> = fill in)\n\n" + recipe.as_text(r["recipe"], r["gaps"]))


@ref_app.command("set")
def ref_set(ref_id: int, trait: str = typer.Argument(..., help="Dotted path, e.g. text.color"),
            value: str = typer.Argument(..., help="JSON value, e.g. '\"#FFE600\"', true, 1.2, '[\"pop_in\"]'")):
    """Correct one Style DNA trait (kept on top of the AI result)."""
    from .references import store

    try:
        v = json.loads(value)
    except json.JSONDecodeError:
        v = value
    r = store.get(ref_id)
    r = store.update(ref_id, overrides={**r["overrides"], trait: v})
    emit({"overrides": r["overrides"]}, f"#{ref_id} {trait} = {v!r}")


@ref_app.command("remove")
def ref_remove(ref_id: int):
    """Delete a reference."""
    from .references import store

    store.delete(ref_id)
    emit({"removed": ref_id}, f"Removed reference #{ref_id}")


def _role_models(items: list[str] | None) -> dict[str, str]:
    alias = {"watcher": "vision", "vision": "vision", "director": "planner", "planner": "planner", "creative": "planner",
             "editor": "editor", "auditor": "auditor", "reviewer": "auditor"}
    out = {}
    for it in items or []:
        role, sep, key = it.partition("=")
        if not sep or role.strip().lower() not in alias:
            raise VeditError(f"--model takes role=<provider>/<model> with role one of watcher, director, editor, auditor (got {it!r})")
        out[alias[role.strip().lower()]] = key.strip()
    return out


@gen_app.command("run")
def gen_run(
    ref: list[int] = typer.Option(..., "--ref", help="Reference id (repeatable)"),
    footage: list[str] = typer.Option(..., "--footage", "-f", help="Your video / music / image file (repeatable)"),
    sound: Optional[list[str]] = typer.Option(None, "--sound", "-s", help="Audio or video used ONLY for its sound (repeatable; the videos take turns)"),
    count: int = typer.Option(3, help="How many different videos (1-10)"),
    about: str = typer.Option("", help="What the video is about"),
    hook: str = typer.Option("", help="Hook line (empty: AI writes one per video)"),
    cta: str = typer.Option(""),
    length: float = typer.Option(20, help="Target length in seconds"),
    fmt: str = typer.Option("reels", "--format", help="reels | portrait45 | square | landscape"),
    language: str = typer.Option("en"),
    quality: str = typer.Option("balanced", help="best | balanced | economy"),
    vary: Optional[list[str]] = typer.Option(None, help="Axes that may change (default all): hook, order, pacing, caption_style, text_animation, transitions, music_section, color_look, font, structure"),
    review: bool = typer.Option(True, help="AI auditor reviews each result and fixes what it finds (up to 2 rounds)"),
    explore: bool = typer.Option(True, help="Write 3 concepts per video and keep the best (+1 call per video, +1 per batch)"),
    threshold: Optional[int] = typer.Option(None, help="Audit pass mark; below it the editor revises (default: audit-settings)"),
    max_rounds: Optional[int] = typer.Option(None, help="Max editor revisions per video (default: audit-settings)"),
    model: Optional[list[str]] = typer.Option(None, "--model", help="Model for a role in this generation: watcher|vision, director|planner, editor, auditor = '<provider>/<model>', e.g. --model auditor=anthropic/claude-opus-5-5 (repeatable)"),
    lead: Optional[int] = typer.Option(None, help="Reference that wins style ties"),
):
    """Generate COUNT videos from your footage in the style of the references (waits until done)."""
    from . import jobs
    from .generate import batch, footage as foot
    from .generate.spec import AXES, BatchSpec

    jobs.AUTOSTART = False
    ups = [foot.add(Path(f))["id"] for f in footage]
    snd = [foot.add(Path(s))["id"] for s in sound or []]
    spec = BatchSpec(refs=ref, lead=lead, footage=ups, sound=snd, count=count, axes=vary or list(AXES), quality=quality, review=review, explore=explore,
                     audit_threshold=threshold, audit_rounds=max_rounds, models=_role_models(model),
                     brief={"product": about, "hook": hook, "cta": cta, "length_s": length, "format": fmt, "language": language})
    est = batch.estimate(spec)
    if not STATE["json"]:
        print(f"About {est['calls']} AI calls, ~{est['minutes']} min…", file=sys.stderr)
    b = batch.create(spec)
    jobs.drain(timeout=3600 * 3, wait=True)
    b = batch.get(b["id"])
    emit({"batch": b}, f"Batch {b['id']}: {b['status']}" + (f" ({b['error']})" if b["error"] else "") + "\n" + "\n".join(
        f"  v{v['idx']} {v['status']:<7} “{v['hook_text']}”  {v['output'] or v['error'] or ''}" for v in b["variations"]))


@gen_app.command("improve")
def gen_improve(variation: str = typer.Argument(..., help="Variation id, e.g. b20261116-185100-ab12-v01 (see `gen list`)"),
                rounds: Optional[int] = typer.Option(None, help="At most this many more revisions (default: audit-settings)"),
                wait: bool = typer.Option(True, help="Wait for it to finish")):
    """Keep improving one generated video: the editor revises its best version from the last review until it passes."""
    from . import jobs
    from .generate import batch

    if wait:
        jobs.AUTOSTART = False  # run it here: a background thread would die with this process
    v = batch.improve(variation, rounds)
    if wait:
        jobs.drain(timeout=3600, wait=True)
        bid = batch._var(variation)["batch_id"]
        v = next(x for x in batch.get(bid)["variations"] if x["id"] == variation)
    a = v.get("audit") or {}
    emit({"variation": v}, f"{variation}: {v['status']}" + (f" · audit {a.get('score')}/100 (pass {a.get('threshold')})"
                                                           + (" · passed" if a.get("reached") else " · still below") if a else ""))


@gen_app.command("list")
def gen_list():
    """List generation batches."""
    from .generate import batch

    rows = batch.list_batches()
    emit({"batches": rows}, "\n".join(f"{b['id']}  {b['status']:<9} {b['done']}/{b['count']}  {b['name']}" for b in rows) or "No batches yet")


design_app = typer.Typer(help="Design: motion-design videos made of scenes of animated shapes, text and media")
app.add_typer(design_app, name="design")


@design_app.command("new")
def design_new(
    folder: Path = typer.Argument(..., help="New project folder, e.g. projects/my-promo"),
    fmt: str = typer.Option("reels", "--format", help="reels | square | landscape | portrait45"),
    name: Optional[str] = None,
    dur: float = typer.Option(5.0, "--dur", help="Length of the first scene (s)"),
    background: Optional[str] = typer.Option(None, help="Canvas colour, e.g. #101014"),
):
    """Start a blank design (one scene) and make it the active project. Open it in the Design studio."""
    from . import features, library
    from .ops import scenes

    features.require_design()
    d = folder.expanduser().resolve()
    if (d / PROJECT_FILE).exists():
        raise VeditError(f"{d} already has a project.json")
    d.mkdir(parents=True, exist_ok=True)
    (d / "media").mkdir(exist_ok=True)
    (d / "exports").mkdir(exist_ok=True)
    proj = Project(name=name or d.name)
    sc = scenes.new_design(proj, d, fmt, name, dur, background)
    save(d, proj)
    history.commit(d, proj, f"new design ({fmt})")
    library.register(d)
    (Path.cwd() / ACTIVE_FILE).write_text(str(d))
    emit({"project": str(d), "scene": sc.id}, f"Created design {d} with scene {sc.id} (now active)")


@scene_app.command("list")
def scene_list():
    """The design's scenes: times, background, transition, notes and their layers."""
    from .ops import scenes

    p = load(pdir())
    scenes.require_design(p)
    rows = scenes.summary(p)
    emit({"scenes": rows}, "\n".join(f"{r['id']}  {r['start']:6.2f}-{r['end']:6.2f}s  {r['name']}  [{r['transition']}]"
                                      f"  {len(r['layers'])} layers" + (f"  bg {r['background']}" if r['background'] else "")
                                      for r in rows))


@scene_app.command("add")
def scene_add(
    dur: float = typer.Option(5.0, "--dur"), after: Optional[str] = typer.Option(None, help="Insert after this scene (default: at the end)"),
    name: str = "", background: Optional[str] = typer.Option(None, help="Scene background colour (hex)"),
    transition: str = typer.Option("cut", help="How its elements enter: cut | fade | slide | zoom | pop | wipe"),
    notes: str = typer.Option("", help="Storyboard notes: what happens in this scene"),
):
    """Add a scene."""
    from .ops import scenes

    d = pdir()
    with edit("scene add") as tx:
        sc = scenes.add(tx.project, d, dur, after, name, background, transition, notes)
        st = scenes.scene_range(tx.project, sc.id)
    emit({"scene": sc.id, "start": st[0], "end": st[1]}, f"Added {sc.id} at {st[0]:g}-{st[1]:g}s")


@scene_app.command("set")
def scene_set(
    scene_id: str, dur: Optional[float] = typer.Option(None, "--dur"), name: Optional[str] = None,
    background: Optional[str] = typer.Option(None, help="Hex colour, or 'none'"),
    transition: Optional[str] = None, notes: Optional[str] = None, why: Optional[str] = None,
):
    """Change a scene: length (later scenes and their elements move along), name, background, transition, notes."""
    from .ops import scenes

    d = pdir()
    with edit(f"scene {scene_id}") as tx:
        bg: Any = False if background is None else (None if background.lower() == "none" else background)
        scenes.set_scene(tx.project, d, scene_id, dur, name, bg, transition, notes, why)
        st = scenes.scene_range(tx.project, scene_id)
    emit({"scene": scene_id, "start": st[0], "end": st[1]}, f"Updated {scene_id} ({st[0]:g}-{st[1]:g}s)")


@scene_app.command("move")
def scene_move(scene_id: str, index: int = typer.Argument(..., help="New position (0 = first)")):
    """Reorder a scene; its elements move with it."""
    from .ops import scenes

    d = pdir()
    with edit(f"move scene {scene_id}") as tx:
        scenes.move(tx.project, d, scene_id, index)
    emit({"scene": scene_id, "index": index}, f"Moved {scene_id} to position {index}")


@scene_app.command("dup")
def scene_dup(scene_id: str):
    """Duplicate a scene with all its elements, right after it."""
    from .ops import scenes

    d = pdir()
    with edit(f"duplicate scene {scene_id}") as tx:
        sc = scenes.duplicate(tx.project, d, scene_id)
    emit({"scene": sc.id}, f"Duplicated {scene_id} as {sc.id}")


@scene_app.command("remove")
def scene_remove(scene_id: str):
    """Delete a scene and its elements; later scenes move up."""
    from .ops import scenes

    d = pdir()
    with edit(f"remove scene {scene_id}") as tx:
        scenes.remove(tx.project, d, scene_id)
    emit({"removed": scene_id}, f"Removed {scene_id}")


@design_app.command("build")
def design_build(
    plan_file: Path = typer.Argument(..., help="MotionPlan JSON (optionally with a \"files\": {id: path} map)"),
    new: Path | None = typer.Option(None, "--new", help="Create this new project folder for it (default: the active project)"),
    fmt: str = typer.Option("reels", "--format", help="reels|square|landscape|portrait45 (with --new)"),
    replace: bool = typer.Option(False, "--replace", help="Rebuild a video-edit project as a design anyway (its clips, "
                                                          "layers and music are replaced; only when the user asked)"),
):
    """Build a whole motion-graphics video from one plan, in one step per scene (undoable). Rebuilds the project's
    timeline: its layers, clips and music are replaced by the plan's. A video edit is never rebuilt by accident:
    give --new DIR for a separate design project (or --replace when the user asked to turn this one into a design)."""
    from . import features
    from .design import builder
    from .generate.executor import create_project

    features.require_design()
    plan, raw_files = builder.load_plan_file(plan_file)
    files = builder.files_from_json(raw_files)
    if new:
        if (new / PROJECT_FILE).exists():
            raise VeditError(f"{new} already has a project.json")
        create_project(new, plan.title or new.name, fmt)
        d = new
    else:
        d = pdir()
        cur = load(d)
        if cur.kind != "design" and (cur.clips or cur.layers or cur.music) and not replace:
            raise VeditError("This project is a video edit: `design build` would replace its clips, layers and music "
                             "with a motion-graphics design. To edit this video (social media layout, text, captions) "
                             "use the normal commands; for a separate motion-graphics video use `--new DIR`.")
    rep = builder.build(plan, d, files)
    emit({"project": str(d), **rep},
         f"built {rep['length']:g}s in {d}: {len(rep['applied'])} parts" + (f", skipped {len(rep['skipped'])}:\n  " + "\n  ".join(rep["skipped"]) if rep["skipped"] else ""))


@design_app.command("split")
def design_split(
    image: Path = typer.Argument(..., exists=True, help="Image sheet: character sheet, storyboard grid, stickers"),
    out: Path | None = typer.Option(None, "--out", help="Folder for the pieces (default: <project>/media/pieces)"),
    grid: str | None = typer.Option(None, "--grid", help="Force an even grid, e.g. 2x3 (rows x columns)"),
    cutout: bool = typer.Option(False, "--cutout", help="Make the sheet's background colour transparent"),
):
    """Cut an image sheet into its panels (PNG pieces to animate)."""
    from .design import sheet

    dest = out or (pdir() / "media" / "pieces")
    pieces = sheet.split(image, dest, grid, cutout)
    for i, pc in enumerate(pieces, 1):
        pc["id"] = f"p{i}"
    over = sheet.contact(pieces, dest / f"{image.stem}-pieces.png")
    emit({"pieces": pieces, "overview": str(over)},
         "\n".join(f"{pc['id']}  {pc['width']}x{pc['height']}  {pc['path']}" for pc in pieces) + f"\noverview: {over}")


@design_app.command("catalog")
def design_catalog():
    """What a MotionPlan may use: frames, transitions, styles, animation presets, text looks, textures, fonts."""
    from .design import spec
    from .ops import animate as anim
    from .ops import fonts, overlays, text_looks

    data = {"frames": list(spec.FRAMES), "transitions": list(spec.TRANSITIONS), "styles": spec.STYLES,
            "presets": {k: v[3] for k, v in anim.PRESETS.items()},
            "looks": [x["name"] for x in text_looks.catalog()],
            "textures": [k for k, it in overlays.LIBRARY.items() if it.kind == "video"],
            "fonts": list(fonts.FONTS), "max_length": spec.MAX_LENGTH}
    from .design import planner

    cat = planner.catalog()
    data.update(eases=cat["eases"], motion_styles=cat["motion_styles"], motion_elements=cat["motion_elements"])
    emit(data)


update_app = typer.Typer(help="App updates (installed desktop app; in a git checkout use `git pull`)")
app.add_typer(update_app, name="update")


def _update_line(st: dict) -> str:
    if not st["enabled"]:
        return f"aiklips {st['current']} (source checkout: update with `git pull`)"
    if st.get("staged"):
        return f"aiklips {st['current']}; {st['staged']} is downloaded and installs when you restart the app"
    if st["available"]:
        return f"aiklips {st['current']}; {st['latest']} is available: `vedit update apply`"
    return f"aiklips {st['current']} is up to date" + (f" (checked {time.strftime('%Y-%m-%d %H:%M', time.localtime(st['checked_at']))})"
                                                         if st.get("checked_at") else "")


@update_app.command("status")
def update_status():
    """Current version, and whether an update is available or downloaded."""
    from . import updater

    st = updater.status()
    emit(st, _update_line(st))


@update_app.command("check")
def update_check():
    """Ask GitHub for the newest release now."""
    from . import updater

    st = updater.check(force=True)
    emit(st, _update_line(st))


@update_app.command("apply")
def update_apply():
    """Download the newest release; it is verified and installed when the app restarts."""
    from . import updater

    st = updater.stage()
    emit(st, f"Downloaded {st['staged']}. Restart aiklips to install it (your projects are not touched).")


@update_app.command("rollback")
def update_rollback():
    """Go back to the previously installed version (on the next restart)."""
    from . import updater

    st = updater.rollback()
    emit(st, f"Restart aiklips to go back to {st['current_after_restart']}.")


@update_app.command("auto")
def update_auto(on: bool = typer.Argument(..., help="true / false")):
    """Turn the automatic update check (every 6 hours) on or off."""
    from . import updater

    updater.set_auto(on)
    emit({"auto": on}, f"Automatic update check {'on' if on else 'off'}")


def main() -> None:
    try:
        app()
    except VeditError as e:
        if STATE.get("json"):
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

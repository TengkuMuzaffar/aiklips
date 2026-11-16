"""`vedit` command line: every edit is one command, every command is undoable.

Use --json for machine-readable output (Claude Code should always pass it).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

import typer

from . import history, media
from .ops import animate as anim
from .ops import captions as caps
from .ops import cut
from .ops.silence import find_silences, silence_cut
from .project import (
    ACTIVE_FILE, PROJECT_FILE, Effect, Layer, MusicTrack, Preset, Project, Source, TextStyle, VeditError,
    find_project_dir, load, save, store_media_path,
)

app = typer.Typer(add_completion=False, no_args_is_help=True, pretty_exceptions_enable=False,
                  help="claude-cut: a video editor you drive with Claude Code. All edits are non-destructive and undoable.")
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
for name, sub in [("text", text_app), ("image", image_app), ("layer", layer_app), ("animate", anim_app),
                  ("captions", cap_app), ("bg", bg_app), ("face", face_app), ("music", music_app),
                  ("mask", mask_app), ("focus", focus_app)]:
    app.add_typer(sub, name=name)

STATE: dict[str, Any] = {"json": False, "project": None}


@app.callback()
def _global(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Project folder (default: active project)"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
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
            "effects": [e.type for e in c.effects],
            **({"transition_in": c.transition.model_dump()} if c.transition else {}),
        })
    layers = [{"id": l.id, "type": l.type, "start": l.start, "end": l.end, "text": l.text or (" ".join(w.text for w in l.words[:8]) if l.words else l.image),
               "presets": [x.name for x in l.presets], "keyframes": len(l.keyframes)} for l in p.layers]
    data = {"project": str(d), "name": p.name, "canvas": p.canvas.model_dump(), "duration": round(p.duration, 3),
            "sources": {k: v.model_dump() for k, v in p.sources.items()}, "clips": clips, "layers": layers,
            "music": [m.model_dump(by_alias=True) for m in p.music], "history_step": history.head(d)}
    if STATE["json"]:
        return emit(data)
    out = [f"Project {p.name}  ({d})", f"Canvas {p.canvas.width}x{p.canvas.height} @{p.canvas.fps:g}fps   Duration {fmt_t(p.duration)}", "", "Sources:"]
    out += [f"  {k}: {v.path}  {v.duration}s" + (f" {v.width}x{v.height}" if v.has_video else " audio") for k, v in p.sources.items()] or ["  (none)"]
    out += ["", "Timeline:"]
    out += [f"  {c['id']:>4}  {fmt_t(c['timeline'][0])} - {fmt_t(c['timeline'][1])}   src {c['source']} {c['source_range'][0]:.2f}-{c['source_range'][1]:.2f}s"
            f"{'  x' + str(c['speed']) if c['speed'] != 1 else ''}{'  zoom ' + str(c['zoom']) if c['zoom'] != 1 or c['zoom_to'] else ''}{' -> ' + str(c['zoom_to']) if c['zoom_to'] else ''}"
            f"{'  fx:' + ','.join(c['effects']) if c['effects'] else ''}"
            f"{'  <- ' + c['transition_in']['type'] + ' ' + str(c['transition_in']['duration']) + 's' if c.get('transition_in') else ''}" for c in clips] or ["  (empty)"]
    out += ["", "Layers:"]
    out += [f"  {l['id']:>5}  {l['type']:<8} {l['start']:.2f}-{(l['end'] if l['end'] is not None else p.duration):.2f}s  {str(l['text'])[:40]!r}"
            f"{'  presets:' + ','.join(l['presets']) if l['presets'] else ''}" for l in layers] or ["  (none)"]
    if p.music:
        out += ["", "Music:"] + [f"  {m.id}: {m.source} vol {m.volume}{' duck' if m.duck else ''}" for m in p.music]
    print("\n".join(out))


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


@text_app.command("add")
def text_add(
    text: str,
    start: float = 0.0, end: Optional[float] = None,
    x: str = "50%", y: str = "30%",
    size: Optional[int] = None, color: Optional[str] = None, font: Optional[str] = None,
    stroke: Optional[str] = typer.Option(None, help="Outline colour"), stroke_width: Optional[int] = None,
    bg: Optional[str] = typer.Option(None, help="Background box colour, e.g. '#000000B0'"),
    max_width: Optional[int] = None, uppercase: Optional[bool] = None,
    preset: Optional[list[str]] = typer.Option(None, help="Animation preset, repeatable: pop_in, fade_out:0.6 ..."),
):
    """Add a text overlay (titles, hooks, CTAs)."""
    with edit(f"add text '{text[:30]}'") as tx:
        p = tx.project
        layer = Layer(id=p.new_id("t"), type="text", text=text, start=start, end=end, x=x, y=y,
                      style=_style_from(TextStyle(), size=size, color=color, font=font, stroke_color=stroke,
                                        stroke_width=stroke_width, bg_color=bg, max_width=max_width, uppercase=uppercase),
                      presets=_presets(preset))
        p.layers.append(layer)
    emit({"layer": layer.id}, f"Added text layer {layer.id}")


@text_app.command("edit")
def text_edit(
    layer_id: str, text: Optional[str] = None,
    size: Optional[int] = None, color: Optional[str] = None, font: Optional[str] = None,
    stroke: Optional[str] = None, stroke_width: Optional[int] = None, bg: Optional[str] = None,
    max_width: Optional[int] = None, uppercase: Optional[bool] = None, highlight: Optional[str] = None,
):
    """Change the text or style of a text/captions layer."""
    with edit(f"edit text {layer_id}") as tx:
        l = tx.project.layer(layer_id)
        if text is not None:
            l.text = text
        l.style = _style_from(l.style, size=size, color=color, font=font, stroke_color=stroke, stroke_width=stroke_width,
                              bg_color=bg, max_width=max_width, uppercase=uppercase, highlight_color=highlight)
    emit({"layer": layer_id}, f"Updated {layer_id}")


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
    width: Optional[int] = None,
):
    """Change timing / position / transform of a layer."""
    with edit(f"set {layer_id}") as tx:
        l = tx.project.layer(layer_id)
        for k, v in dict(start=start, end=end, x=x, y=y, scale=scale, rotation=rotation, opacity=opacity, width=width).items():
            if v is not None:
                setattr(l, k, v)
    emit({"layer": l.model_dump(mode="json", exclude={"words"})}, f"Updated {layer_id}")


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
    """Set custom keyframes (t = seconds from layer start; x,y,scale,rotation,opacity,reveal; ease)."""
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
    mode: str = typer.Option("fast", help="fast (MediaPipe) | quality (BiRefNet, slow)"),
    replace: str = typer.Option("color:#00B140", help="color:#RRGGBB | blur[:sigma] | image:path | video:path"),
    smooth: float = typer.Option(0.5, help="Temporal smoothing 0..0.9 (less flicker)"),
):
    """Remove the background behind people and replace it."""
    with edit(f"remove background ({mode}, {replace})") as tx:
        ids = _set_effect(tx.project, clip, Effect(type="bg", params={"mode": mode, "replace": replace, "smooth": smooth}), ("bg",))
    emit({"clips": ids}, f"Background removal on {', '.join(ids)}. Run `vedit preview` to compute it (one-time, may take a while).")


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
):
    """Hide a region (labels, logos, plates, screens) with a soft-edged mask that follows it."""
    from .ops.track import STYLES

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
                       "feather": feather, "expand": expand if expand is not None else (1.0 if painted else 1.1), "track": track_, "smooth": smooth, "invert": invert})
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
             invert: Optional[bool] = typer.Option(None, "--invert/--no-invert"),
             keep_box: Optional[str] = typer.Option(None, help="Region that must stay on top (the product in front), "
                                                    "x,y,w,h[;...] on the canvas at --at; 'none' clears it"),
             at: Optional[float] = typer.Option(None, help="Timeline seconds the --keep-box is drawn at")):
    """Change how a mask looks (style changes don't re-track)."""
    from .ops import track

    with edit(f"mask {mask_id}") as tx:
        for c, e in _masks(tx.project, mask_id):
            for k, v in (("style", style), ("strength", strength), ("color", color), ("feather", feather),
                         ("expand", expand), ("shape", shape), ("invert", invert)):
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
    clip: Optional[str] = typer.Option(None, help="Clip (default: the clip under --at)"),
    at: Optional[float] = typer.Option(None, help="Timeline seconds where the box is right"),
    mode: str = typer.Option("spotlight", help="spotlight (blur + dim around it) | follow (camera pans with it) | both"),
    zoom: Optional[float] = typer.Option(None, help="follow: how tight the shot is (default: 1.0 when reframing wide video, else 1.4)"),
    blur: float = typer.Option(14, help="spotlight: background blur sigma (0 = none)"),
    dim: float = typer.Option(0.35, help="spotlight: background darkening 0-1"),
    feather: float = typer.Option(0.12, help="spotlight: edge softness"),
    expand: float = typer.Option(1.35, help="spotlight: room around the object"),
    smooth: float = typer.Option(0.6, help="Tracking smoothing 0-0.95"),
    track_: bool = typer.Option(True, "--track/--no-track"),
    space: str = typer.Option("canvas", help="canvas | source"),
):
    """Put the focus on one object: spotlight it, or reframe the shot to follow it."""
    if mode not in ("spotlight", "follow", "both"):
        raise VeditError("--mode must be spotlight, follow or both")
    d = pdir()
    with edit(f"focus ({mode})") as tx:
        p = tx.project
        if clip is None and at is None and len(p.clips) > 1:
            raise VeditError("Say which clip with --clip, or give --at <timeline seconds>")
        c, params, note = _region(p, d, clip, at, box, obj, space)
        params.update({"mode": mode, "blur": blur, "dim": dim, "feather": feather, "expand": expand,
                       "smooth": smooth, "track": track_, "shape": "ellipse", "invert": True})
        if zoom is not None:
            params["zoom"] = zoom
        c.effects = [e for e in c.effects if e.type != "focus"] + [Effect(type="focus", params=params)]
    emit({"clip": c.id, "mode": mode, "box_source": params["box"], "note": note},
         f"Focus ({mode}) on {c.id}{' (' + note + ')' if note else ''}. Tracking runs on the next preview (one-time).")


@focus_app.command("clear")
def focus_clear(clip: Optional[str] = None):
    """Remove object focus."""
    with edit("clear focus") as tx:
        ids = _clear_effect(tx.project, clip, ("focus",))
    emit({"clips": ids}, "Focus cleared")


# ---- music ------------------------------------------------------------------------------

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
def music_set(music_id: str, volume: Optional[float] = None, duck: Optional[bool] = None, start: Optional[float] = None):
    """Change music volume / ducking / start."""
    with edit(f"music {music_id}") as tx:
        m = next((m for m in tx.project.music if m.id == music_id), None)
        if not m:
            raise VeditError(f"No music track {music_id}")
        if volume is not None:
            m.volume = volume
        if duck is not None:
            m.duck = duck
        if start is not None:
            m.start = start
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
def frame(at: str = typer.Option(..., help="Timeline seconds; several as a list: 2,5.5,9"),
          out: Optional[str] = typer.Option(None, help="Output image (single time only)"),
          width: int = 480):
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
def export(out: Optional[str] = typer.Argument(None, help="Output .mp4 (default exports/<name>.mp4)"),
           scale: float = typer.Option(1.0, help="Resolution scale (0.5 = half)"),
           open_: bool = typer.Option(False, "--open")):
    """Render the final high-quality MP4."""
    from .render.runner import render

    d = pdir()
    p = load(d)
    dest = Path(out).expanduser().resolve() if out else d / "exports" / f"{p.name}.mp4"
    res = render(p, d, dest, scale, "export", progress=not STATE["json"])
    if open_:
        media.open_file(dest)
    emit(res, f"Exported {res['output']}  {res['width']}x{res['height']}  {res['duration']}s  (took {res['seconds']}s)")


@app.command("app")
def app_cmd(
    folder: Optional[str] = typer.Argument(None, help="Open this project directly (created if it doesn't exist). Default: the home screen"),
    browser: bool = typer.Option(False, "--browser", help="Open in the web browser instead of a native window"),
    port: Optional[int] = None,
):
    """Open claude-cut: home screen with your projects, then the 4-pane editor."""
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
def serve(port: int = 5173, host: str = "127.0.0.1", browser: bool = True):
    """Open claude-cut in your browser on a fixed port (same as `vedit app --browser`)."""
    from .preview_server.app import run

    try:
        d = pdir()
    except VeditError:
        d = None  # no active project: start on the home screen
    run(d, host, port, browser)


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
    emit({"reference": {k: r.get(k) for k in ("id", "name", "status", "summary", "dna", "overrides", "error")}},
         f"#{r['id']} {r['name']} ({r['status']})\n" + "\n".join(f"  {t['label']:<12} {t['value']}" for t in r.get("card", [])))


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


@gen_app.command("run")
def gen_run(
    ref: list[int] = typer.Option(..., "--ref", help="Reference id (repeatable)"),
    footage: list[str] = typer.Option(..., "--footage", "-f", help="Your video / music / image file (repeatable)"),
    count: int = typer.Option(3, help="How many different videos (1-10)"),
    about: str = typer.Option("", help="What the video is about"),
    hook: str = typer.Option("", help="Hook line (empty: AI writes one per video)"),
    cta: str = typer.Option(""),
    length: float = typer.Option(20, help="Target length in seconds"),
    fmt: str = typer.Option("reels", "--format", help="reels | portrait45 | square | landscape"),
    language: str = typer.Option("en"),
    quality: str = typer.Option("balanced", help="best | balanced | economy"),
    vary: Optional[list[str]] = typer.Option(None, help="Axes that may change (default all): hook, order, pacing, caption_style, text_animation, transitions, music_section"),
    review: bool = typer.Option(True, help="AI reviews each result once"),
    lead: Optional[int] = typer.Option(None, help="Reference that wins style ties"),
):
    """Generate COUNT videos from your footage in the style of the references (waits until done)."""
    from . import jobs
    from .generate import batch, footage as foot
    from .generate.spec import AXES, BatchSpec

    jobs.AUTOSTART = False
    ups = [foot.add(Path(f))["id"] for f in footage]
    spec = BatchSpec(refs=ref, lead=lead, footage=ups, count=count, axes=vary or list(AXES), quality=quality, review=review,
                     brief={"product": about, "hook": hook, "cta": cta, "length_s": length, "format": fmt, "language": language})
    est = batch.estimate(spec)
    if not STATE["json"]:
        print(f"About {est['calls']} AI calls, ~{est['minutes']} min…", file=sys.stderr)
    b = batch.create(spec)
    jobs.drain(timeout=3600 * 3, wait=True)
    b = batch.get(b["id"])
    emit({"batch": b}, f"Batch {b['id']}: {b['status']}" + (f" ({b['error']})" if b["error"] else "") + "\n" + "\n".join(
        f"  v{v['idx']} {v['status']:<7} “{v['hook_text']}”  {v['output'] or v['error'] or ''}" for v in b["variations"]))


@gen_app.command("list")
def gen_list():
    """List generation batches."""
    from .generate import batch

    rows = batch.list_batches()
    emit({"batches": rows}, "\n".join(f"{b['id']}  {b['status']:<9} {b['done']}/{b['count']}  {b['name']}" for b in rows) or "No batches yet")


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

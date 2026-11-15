"""Edits triggered from the GUI (inspector fields, timeline drag & drop).

Every action goes through the same undoable history transaction as the CLI,
so GUI edits, CLI edits and AI edits all share one history.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from .. import history, media
from ..ops import animate as anim
from ..ops import cut
from ..project import Layer, Project, Source, TextStyle, VeditError, store_media_path

Handler = Callable[[Project, dict], str]  # returns the history message
_OPS: dict[str, Handler] = {}


def op(name: str):
    def deco(fn: Handler) -> Handler:
        _OPS[name] = fn
        return fn
    return deco


def _num(a: dict, key: str) -> float | None:
    v = a.get(key)
    return None if v is None or v == "" else float(v)


@op("move_clip")
def _move_clip(p: Project, a: dict) -> str:
    cut.move_clip(p, a["clip"], int(a["index"]))
    return f"move {a['clip']} to position {a['index']}"


@op("retime_clip")
def _retime_clip(p: Project, a: dict) -> str:
    cut.retime_clip(p, a["clip"], float(a["in"]), float(a["out"]))
    return f"trim {a['clip']} to {float(a['in']):.2f}-{float(a['out']):.2f}s"


@op("clip_set")
def _clip_set(p: Project, a: dict) -> str:
    c = p.clip(a["clip"])
    changed = []
    if _num(a, "speed") is not None and abs(float(a["speed"]) - c.speed) > 1e-6:
        cut.set_clip_speed(p, c.id, float(a["speed"]))
        changed.append(f"speed {a['speed']}")
    for key in ("volume", "zoom", "focus_x", "focus_y"):
        v = _num(a, key)
        if v is not None:
            setattr(c, key, v)
            changed.append(f"{key} {v:g}")
    if "zoom_to" in a:
        c.zoom_to = _num(a, "zoom_to")
        changed.append("zoom animation")
    if a.get("fit"):
        if a["fit"] not in ("cover", "contain", "blur"):
            raise VeditError("fit must be cover, contain or blur")
        c.fit = a["fit"]
        changed.append(f"fit {a['fit']}")
    return f"{c.id}: " + (", ".join(changed) or "no change")


@op("remove_clip")
def _remove_clip(p: Project, a: dict) -> str:
    cut.remove_clip(p, a["clip"])
    return f"remove clip {a['clip']}"


@op("remove_effect")
def _remove_effect(p: Project, a: dict) -> str:
    c = p.clip(a["clip"])
    c.effects = [e for e in c.effects if e.type != a["type"]]
    return f"{c.id}: remove {a['type']}"


@op("split")
def _split(p: Project, a: dict) -> str:
    cut.split_at(p, float(a["at"]))
    return f"split at {float(a['at']):.2f}s"


@op("delete_range")
def _delete_range(p: Project, a: dict) -> str:
    cut.delete_range(p, float(a["from"]), float(a["to"]))
    return f"delete {float(a['from']):.2f}-{float(a['to']):.2f}s"


@op("insert_source")
def _insert_source(p: Project, a: dict) -> str:
    sid = a["source"]
    if sid not in p.sources:
        raise VeditError(f"No source {sid}")
    if not p.sources[sid].has_video:
        raise VeditError("Audio files go on the music track (drop them there or use the inspector)")
    clip = cut.append_clip(p, sid, float(a.get("in") or 0), _num(a, "out"), None if a.get("index") is None else int(a["index"]))
    return f"insert {sid} as {clip.id}"


@op("add_music")
def _add_music(p: Project, a: dict) -> str:
    from ..project import MusicTrack

    sid = a["source"]
    if not p.sources[sid].has_audio:
        raise VeditError(f"{sid} has no audio")
    m = MusicTrack(id=p.new_id("m"), source=sid, start=float(a.get("start") or 0), volume=float(a.get("volume") or 0.2), duck=True)
    p.music.append(m)
    return f"add music {m.id}"


_LAYER_FIELDS = ("start", "end", "x", "y", "scale", "rotation", "opacity", "text", "width", "words_per_group")


@op("layer_set")
def _layer_set(p: Project, a: dict) -> str:
    l = p.layer(a["layer"])
    changed = []
    for k in _LAYER_FIELDS:
        if k not in a:
            continue
        v = a[k]
        if k in ("start", "end", "scale", "rotation", "opacity"):
            v = None if (v is None or v == "") and k == "end" else float(v)
        elif k in ("width", "words_per_group"):
            v = None if v in (None, "") else int(v)
        setattr(l, k, v)
        changed.append(k)
    if l.end is not None and l.end - l.start < 0.05:
        raise VeditError("Layer end must be after its start")
    if isinstance(a.get("style"), dict):
        data = l.style.model_dump()
        data.update({k: v for k, v in a["style"].items() if k in data})
        for k in ("size", "stroke_width", "max_width", "bg_padding", "bg_radius"):
            if data.get(k) not in (None, ""):
                data[k] = int(float(data[k]))
        if data.get("bg_color") == "":
            data["bg_color"] = None
        l.style = TextStyle(**data)
        changed.append("style")
    return f"{l.id}: " + ", ".join(changed)


@op("layer_add_text")
def _layer_add_text(p: Project, a: dict) -> str:
    start = float(a.get("start") or 0)
    l = Layer(id=p.new_id("t"), type="text", text=a.get("text") or "New text", start=start,
              end=_num(a, "end") or min(p.duration, start + 3), y=a.get("y") or "30%")
    p.layers.append(l)
    return f"add text {l.id}"


@op("layer_remove")
def _layer_remove(p: Project, a: dict) -> str:
    p.layers.remove(p.layer(a["layer"]))
    return f"remove layer {a['layer']}"


@op("preset_add")
def _preset_add(p: Project, a: dict) -> str:
    from ..project import Preset

    pr = Preset(name=a["name"])
    anim.validate_preset(pr)
    l = p.layer(a["layer"])
    l.presets = [x for x in l.presets if x.name != pr.name] + [pr]
    return f"{l.id}: animate {pr.name}"


@op("preset_remove")
def _preset_remove(p: Project, a: dict) -> str:
    l = p.layer(a["layer"])
    l.presets = [x for x in l.presets if x.name != a["name"]]
    return f"{l.id}: remove {a['name']}"


@op("keyframes_clear")
def _kf_clear(p: Project, a: dict) -> str:
    p.layer(a["layer"]).keyframes = []
    return f"{a['layer']}: clear keyframes"


@op("canvas_set")
def _canvas_set(p: Project, a: dict) -> str:
    c = p.canvas
    presets = {"reels": (1080, 1920), "square": (1080, 1080), "landscape": (1920, 1080), "portrait45": (1080, 1350)}
    if a.get("preset"):
        c.width, c.height = presets[a["preset"]]
    if _num(a, "fps"):
        c.fps = float(a["fps"])
    if a.get("background"):
        c.background = a["background"]
    return f"canvas {c.width}x{c.height} @{c.fps:g}"


@op("rename")
def _rename(p: Project, a: dict) -> str:
    p.name = str(a["name"]).strip() or p.name
    return f"rename project to {p.name}"


@op("music_set")
def _music_set(p: Project, a: dict) -> str:
    m = next((m for m in p.music if m.id == a["music"]), None)
    if not m:
        raise VeditError(f"No music {a['music']}")
    for k in ("volume", "start"):
        if _num(a, k) is not None:
            setattr(m, k, float(a[k]))
    if "duck" in a:
        m.duck = bool(a["duck"])
    if "loop" in a:
        m.loop = bool(a["loop"])
    return f"{m.id}: update"


@op("music_remove")
def _music_remove(p: Project, a: dict) -> str:
    p.music = [m for m in p.music if m.id != a["music"]]
    return f"remove music {a['music']}"


def apply(pdir: Path, name: str, args: dict[str, Any]) -> dict:
    fn = _OPS.get(name)
    if not fn:
        raise VeditError(f"Unknown action '{name}'")
    with history.transaction(pdir, name) as tx:
        tx.message = fn(tx.project, args)
    return {"ok": True, "message": tx.message}


def import_file(pdir: Path, filename: str, data_path: Path, append: bool, index: int | None) -> dict:
    """Store an uploaded / dropped file in media/ and add it as a source (and clip)."""
    media_dir = pdir / "media"
    media_dir.mkdir(exist_ok=True)
    name = Path(filename).name or "upload.mp4"
    dest = media_dir / name
    n = 1
    while dest.exists():
        dest = media_dir / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    shutil.move(str(data_path), dest)
    info = media.probe(dest)
    with history.transaction(pdir, f"import {dest.name}") as tx:
        p = tx.project
        sid = p.new_id("s")
        p.sources[sid] = Source(path=store_media_path(pdir, dest), **info)
        clip_id = None
        if append and info["has_video"]:
            clip_id = cut.append_clip(p, sid, 0, None, index).id
    return {"ok": True, "source": sid, "clip": clip_id, "has_video": info["has_video"]}

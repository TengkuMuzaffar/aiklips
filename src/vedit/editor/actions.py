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
from ..project import Effect, Layer, Project, Source, TextStyle, VeditError, store_media_path

Handler = Callable[[Project, dict], str]  # returns the history message
_PDIR: list[Path] = [Path(".")]  # project folder of the action being applied (for media lookups)
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


# ---- colour (clip inspector) ------------------------------------------------------------

def _color_targets(p: Project, a: dict) -> list[str] | None:
    """all=true -> every clip (None), else the one clip given."""
    return None if a.get("all") else [p.clip(a["clip"]).id]


@op("color_look")
def _color_look(p: Project, a: dict) -> str:
    from ..ops import color

    name = a.get("name") or "natural"
    ids = color.set_look(p, name, float(a.get("intensity", 1.0)), _color_targets(p, a))
    return f"colour look {name} on {'all clips' if a.get('all') else ', '.join(ids)}"


@op("color_match")
def _color_match(p: Project, a: dict) -> str:
    """Match every clip to their average, or to a hero clip (hero=<clip id>)."""
    from ..ops import color

    r = color.match_clips(p, _PDIR[0], hero=a.get("hero") or None, strength=float(a.get("strength", 0.8)))
    return f"colour match {len(r['clips'])} clips to {r['matched_to']}"


@op("color_set")
def _color_set(p: Project, a: dict) -> str:
    from ..project import Grade

    keys = ("brightness", "contrast", "saturation", "temperature", "tint")
    vals = {k: float(a[k]) for k in keys if a.get(k) not in (None, "")}
    if not vals:
        raise VeditError("Nothing to change")
    ids = _color_targets(p, a) or [c.id for c in p.clips]
    for cid in ids:
        c = p.clip(cid)
        c.grade = (c.grade or Grade()).model_copy(update=vals)
    return f"colour {', '.join(f'{k} {v:g}' for k, v in vals.items())} on {', '.join(ids)}"


@op("color_clear")
def _color_clear(p: Project, a: dict) -> str:
    ids = _color_targets(p, a) or [c.id for c in p.clips]
    for cid in ids:
        p.clip(cid).grade = None
    return f"clear colour on {', '.join(ids)}"


@op("transition_set")
def _transition_set(p: Project, a: dict) -> str:
    kind = str(a.get("type") or "none")
    if kind in ("none", "cut", ""):
        cut.clear_transition(p, a.get("clip"))
        return f"{a.get('clip') or 'all cuts'}: hard cut"
    ids = cut.set_transition(p, kind, float(a.get("duration") or 0.5), a.get("clip"))
    return f"transition {kind} into {', '.join(ids)}"


@op("mask_add")
def _mask_add(p: Project, a: dict) -> str:
    from ..ops import track

    style = a.get("style") or "remove"
    if style not in track.STYLES:
        raise VeditError(f"style must be one of {', '.join(track.STYLES)}")
    c, params, _ = track.region(p, _PDIR[0], a.get("clip"), _num(a, "at"), a.get("box"), a.get("object"),
                                paint=a.get("paint") or None, keep=a.get("keep") or None)
    painted = params.get("shape") == "paint"
    params.update({"id": track.new_mask_id(p), "style": style, "color": a.get("color") or "#000000",
                   "shape": "paint" if painted else (a.get("shape") or "rect"), "feather": float(a.get("feather") or 0.04),
                   "expand": float(a.get("expand") or (1.0 if painted else 1.1)), "track": bool(a.get("track", True)), "smooth": 0.6,
                   "invert": bool(a.get("invert", False))})
    c.effects.append(Effect(type="mask", params=params))
    return f"{c.id}: mask {params['id']} ({style})"


@op("mask_set")
def _mask_set(p: Project, a: dict) -> str:
    hits = [e for c in p.clips for e in c.effects if e.type == "mask" and e.params.get("id") == a["id"]]
    if not hits:
        raise VeditError(f"No mask {a['id']}")
    for e in hits:
        for k in ("style", "strength", "color", "feather", "expand", "shape", "invert"):
            if k in a and a[k] not in (None, ""):
                e.params[k] = a[k]
    return f"mask {a['id']}: update"


@op("focus_add")
def _focus_add(p: Project, a: dict) -> str:
    from ..ops import track

    mode = a.get("mode") or "spotlight"
    if mode not in ("spotlight", "follow", "both"):
        raise VeditError("mode must be spotlight, follow or both")
    c, params, _ = track.region(p, _PDIR[0], a.get("clip"), _num(a, "at"), a.get("box"), a.get("object"))
    params.update({"mode": mode, "blur": 14.0, "dim": 0.35, "feather": 0.12, "expand": 1.35, "smooth": 0.6,
                   "track": bool(a.get("track", True)), "shape": "ellipse", "invert": True})
    if _num(a, "zoom"):
        params["zoom"] = float(a["zoom"])
    c.effects = [e for e in c.effects if e.type != "focus"] + [Effect(type="focus", params=params)]
    return f"{c.id}: focus ({mode})"


@op("remove_clip")
def _remove_clip(p: Project, a: dict) -> str:
    cut.remove_clip(p, a["clip"])
    return f"remove clip {a['clip']}"


@op("remove_effect")
def _remove_effect(p: Project, a: dict) -> str:
    c = p.clip(a["clip"])
    if a.get("id"):
        c.effects = [e for e in c.effects if e.params.get("id") != a["id"]]
        return f"{c.id}: remove {a['id']}"
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
        for k in ("size", "stroke_width", "max_width", "bg_padding", "bg_radius", "glow_size"):
            if data.get(k) not in (None, ""):
                data[k] = int(float(data[k]))
        if data.get("letter_spacing") not in (None, ""):
            data["letter_spacing"] = float(data["letter_spacing"])
        for k in ("bg_color", "glow_color", "font"):
            if data.get(k) == "":
                data[k] = None
        if isinstance(data.get("gradient"), str):  # "#top,#bottom" from the GUI; "" = off
            data["gradient"] = [c.strip() for c in data["gradient"].split(",") if c.strip()] or None
        if data.get("font"):
            from ..ops.text import find_font
            find_font(data["font"])  # unknown font: clear error now, not a failed render later
        if "look" not in a["style"]:
            data["look"] = None  # hand-edited: no longer exactly that look
        l.style = TextStyle(**data)
        changed.append("style")
    return f"{l.id}: " + ", ".join(changed)


@op("text_look")
def _text_look(p: Project, a: dict) -> str:
    """Apply a text look to one layer, or to every text layer with all=true (one look = consistent titles)."""
    from ..ops import text_looks

    name = a.get("name") or ""
    text_looks.get(name)
    targets = [l for l in p.layers if l.type == "text"] if a.get("all") else [p.layer(a["layer"])]
    for l in targets:
        if l.type not in ("text", "captions"):
            raise VeditError(f"Layer {l.id} is not text")
        text_looks.apply(l, name, animation=a.get("animation", True))
    return f"look {name} on {', '.join(l.id for l in targets)}"


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
    _PDIR[0] = pdir
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

"""Edits triggered from the GUI (inspector fields, timeline drag & drop).

Every action goes through the same undoable history transaction as the CLI,
so GUI edits, CLI edits and AI edits all share one history.
"""

from __future__ import annotations

import json
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
    for key in ("fade_in", "fade_out"):  # sound fades, seconds
        v = _num(a, key)
        if v is not None:
            setattr(c, key, max(0.0, min(v, c.duration / 2)))
            changed.append(f"{key} {v:g}s")
    if a.get("voice"):
        if a["voice"] not in ("off", "clean", "studio"):
            raise VeditError("voice must be off, clean or studio")
        c.voice = a["voice"]
        changed.append(f"voice {a['voice']}")
    return f"{c.id}: " + (", ".join(changed) or "no change")


@op("clip_why")
def _clip_why(p: Project, a: dict) -> str:
    from ..project import ROLES, ClipMeta

    c = p.clip(a["clip"])
    upd = {}
    if "why" in a:
        upd["why"] = (a.get("why") or "").strip()[:200] or None
    if "role" in a:
        role = a.get("role") or None
        if role is not None and role not in ROLES:
            raise VeditError(f"role must be one of {', '.join(ROLES)}")
        upd["role"] = role
    c.meta = (c.meta or ClipMeta()).model_copy(update=upd)
    return f"{c.id}: why"


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
                   "invert": bool(a.get("invert", False)), "tracker": a.get("tracker") or "auto", "sam": a.get("sam") or "auto"})
    c.effects.append(Effect(type="mask", params=params))
    return f"{c.id}: mask {params['id']} ({style})"


@op("mask_set")
def _mask_set(p: Project, a: dict) -> str:
    hits = [e for c in p.clips for e in c.effects if e.type == "mask" and e.params.get("id") == a["id"]]
    if not hits:
        raise VeditError(f"No mask {a['id']}")
    for e in hits:
        for k in ("style", "strength", "color", "feather", "expand", "shape", "invert", "tracker", "sam"):
            if k in a and a[k] not in (None, ""):
                e.params[k] = a[k]
    return f"mask {a['id']}: update"


@op("bg_remove")
def _bg_remove(p: Project, a: dict) -> str:
    """Background removal on one clip (or every clip): mode chroma (green screen) | fast | quality | off."""
    from ..ops import chroma

    mode = a.get("mode") or "chroma"
    targets = [p.clip(a["clip"])] if a.get("clip") else [c for c in p.clips if not c.gap]
    if mode == "off":
        for c in targets:
            c.effects = [e for e in c.effects if e.type != "bg"]
        return "background removal off"
    if mode not in ("chroma", "fast", "quality"):
        raise VeditError("mode must be chroma, fast, quality or off")
    # keying a green screen and filling the hole with green looks like nothing happened: use the canvas colour
    replace = a.get("replace") or (f"color:{p.canvas.background}" if mode == "chroma" else "color:#00B140")
    for c in targets:
        if mode == "chroma":
            given = chroma.clean({k: a[k] for k in chroma.PARAMS if a.get(k) not in (None, "")})
            prm = {**chroma.DEFAULTS, **given}
            if prm["key"] in ("", "auto") or "key" not in a:
                prm["key"] = chroma.clip_key(_PDIR[0], p, c)
            prm.update(chroma.fit_tolerance(prm["key"], given.get("similarity"), given.get("blend")))
        else:
            prm = {"smooth": float(a.get("smooth") or 0.5)}
        c.effects = [e for e in c.effects if e.type != "bg"] + [Effect(type="bg", params={**prm, "mode": mode, "replace": replace})]
    return f"{', '.join(c.id for c in targets)}: background removal ({mode})"


@op("bg_set")
def _bg_set(p: Project, a: dict) -> str:
    """Change a clip's background settings (key colour, tolerance, spill, replacement): compositing only."""
    from ..ops import chroma

    vals = chroma.clean({k: a[k] for k in (*chroma.PARAMS, "replace") if a.get(k) not in (None, "")})
    targets = [p.clip(a["clip"])] if a.get("clip") else p.clips
    hits = [(c, e) for c in targets for e in c.effects if e.type == "bg"]
    if not hits:
        raise VeditError("No background removal on this clip")
    for c, e in hits:
        upd = {k: v for k, v in vals.items() if k == "replace" or e.params.get("mode") == "chroma"}
        if upd.get("key") == "auto":
            upd["key"] = chroma.clip_key(_PDIR[0], p, c)
        if "key" in upd:  # a new key colour: re-fit the tolerance unless this same call sets it
            upd.update(chroma.fit_tolerance(upd["key"], upd.get("similarity"), upd.get("blend")))
        e.params.update(upd)
    return f"background: {', '.join(f'{k}={v}' for k, v in vals.items())}"


@op("focus_add")
def _focus_add(p: Project, a: dict) -> str:
    from ..ops import track

    c, params, _ = track.region(p, _PDIR[0], a.get("clip"), _num(a, "at"), a.get("box"), a.get("object"),
                                paint=a.get("paint") or None, keep=a.get("keep") or None)
    params = track.focus_params(params, a.get("mode") or "spotlight", a.get("style") or "spotlight",
                                color=a.get("color") or None, width=_num(a, "width"), strength=_num(a, "strength"),
                                track=bool(a.get("track", True)), zoom=_num(a, "zoom") or None)
    params.update({"tracker": a.get("tracker") or "auto", "sam": a.get("sam") or "auto"})
    c.effects = [e for e in c.effects if e.type != "focus"] + [Effect(type="focus", params=params)]
    return f"{c.id}: focus ({params['mode']}, {params['style']})"


@op("focus_set")
def _focus_set(p: Project, a: dict) -> str:
    from ..ops import track

    if a.get("style") and a["style"] not in track.FOCUS_STYLES:
        raise VeditError(f"style must be one of {', '.join(track.FOCUS_STYLES)}")
    hits = [e for c in ([p.clip(a["clip"])] if a.get("clip") else p.clips) for e in c.effects if e.type == "focus"]
    if not hits:
        raise VeditError("No focus on this clip")
    vals = {k: (a[k] if k in ("style", "color") else float(a[k])) for k in track.FOCUS_LOOK if a.get(k) not in (None, "")}
    for e in hits:
        e.params.update(vals)
    return f"focus: {', '.join(f'{k}={v}' for k, v in vals.items())}"


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


# ---- filters (Effects tab): ops/fx_filters.py ----------------------------------------------------------------
@op("fx_add")
def _fx_add(p: Project, a: dict) -> str:
    from ..ops import fx_filters

    if a.get("layer"):  # an adjustment layer or a video overlay
        l = p.layer(a["layer"])
        if l.type not in ("adjust", "video"):
            raise VeditError(f"{l.id} is a {l.type} layer: effects go on clips, video overlays or adjustment layers")
        fx_filters.add(p, l, a["key"], a.get("params") or {})
        return f"{fx_filters.get(a['key']).label} on {l.id}"
    targets = p.clips if a.get("clip") in (None, "", "all") else [p.clip(a["clip"])]
    added = [fx_filters.add(p, c, a["key"], a.get("params") or {}) for c in targets]
    label = fx_filters.get(a["key"]).label
    return f"{label} on {targets[0].id}" if len(targets) == 1 else f"{label} on {len(added)} clips"


@op("fx_set")
def _fx_set(p: Project, a: dict) -> str:
    from ..ops import fx_filters

    vals = {k: v for k, v in a.items() if k != "id"}
    eff = fx_filters.set_params(p, a["id"], vals)
    return f"{a['id']} ({eff.params['key']}): " + ", ".join(f"{k}={v}" for k, v in vals.items())


@op("fx_reset")
def _fx_reset(p: Project, a: dict) -> str:
    from ..ops import fx_filters

    fx_filters.reset(p, a["id"])
    return f"reset {a['id']}"


@op("fx_remove")
def _fx_remove(p: Project, a: dict) -> str:
    from ..ops import fx_filters

    c = fx_filters.remove(p, a["id"])
    return f"{c.id}: remove {a['id']}"


@op("fx_replace")
def _fx_replace(p: Project, a: dict) -> str:
    from ..ops import fx_filters

    e = fx_filters.replace(p, a["id"], a["key"])
    return f"{a['id']} is now {fx_filters.get(e.params['key']).label}"


@op("track_visible")
def _track_visible(p: Project, a: dict) -> str:
    """The eye on a track's header: show / hide everything on an overlay track, or the main video track."""
    from ..ops import tracks

    return tracks.set_track_visible(p, a.get("track"), bool(a.get("visible", True)))


@op("fx_move")
def _fx_move(p: Project, a: dict) -> str:
    from ..ops import fx_filters

    fx_filters.move(p, a["id"], int(a["index"]))
    return f"move {a['id']} to {a['index']}"


@op("fx_copy")
def _fx_copy(p: Project, a: dict) -> str:
    from ..ops import fx_filters

    src = p.clip(a["clip"])
    to = a.get("to", "all")
    targets = p.clips if to == "all" else [p.clip(x) for x in to]
    n = fx_filters.copy_to(p, src, targets)
    return f"copy {src.id} effects to {n} clip(s)"


@op("move_apply")
def _move_apply(p: Project, a: dict) -> str:
    from ..ops import moves

    at = a.get("at")
    return moves.apply(p, a["name"], clip=a.get("clip") or None, at=None if at in (None, "") else float(at),
                       text=a.get("text") or None)


# ---- markers, keyframes, trims, paste attributes (ops/editing.py) ------------------------------------------------
@op("marker_add")
def _marker_add(p: Project, a: dict) -> str:
    from ..ops import editing

    m = editing.add_marker(p, float(a.get("t") or 0), a.get("label") or "", a.get("color"))
    return f"marker {m.id} at {m.t:g}s"


@op("marker_set")
def _marker_set(p: Project, a: dict) -> str:
    from ..ops import editing

    m = editing.set_marker(p, a["id"], _num(a, "t"), a.get("label"), a.get("color"))
    return f"marker {m.id}: {m.label or m.t}"


@op("marker_remove")
def _marker_remove(p: Project, a: dict) -> str:
    from ..ops import editing

    editing.remove_marker(p, a["id"])
    return f"remove marker {a['id']}"


@op("keyframe_set")
def _keyframe_set(p: Project, a: dict) -> str:
    from ..ops import editing

    l = p.layer(a["layer"])
    editing.set_keyframe(l, float(a["t"]) - l.start, a["prop"], a["value"])
    return f"{l.id}: keyframe {a['prop']} at {float(a['t']):.2f}s"


@op("keyframe_remove")
def _keyframe_remove(p: Project, a: dict) -> str:
    from ..ops import editing

    l = p.layer(a["layer"])
    n = editing.remove_keyframe(l, float(a["t"]) - l.start, a.get("prop"))
    return f"{l.id}: removed {n} keyframe value(s)"


@op("trim_to")
def _trim_to(p: Project, a: dict) -> str:
    from ..ops import editing

    c = editing.trim_to(p, float(a["at"]), a.get("side", "start"))
    return f"{c.id}: trim {a.get('side', 'start')} to {float(a['at']):.2f}s"


@op("paste_attrs")
def _paste_attrs(p: Project, a: dict) -> str:
    from ..ops import editing

    n = editing.paste_attributes(p, a["kind"], a["from"], list(a["to"]), list(a["groups"]))
    return f"paste {', '.join(a['groups'])} onto {n} {a['kind']}(s)"


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
            v = None if v in (None, "") else int(float(v))
        setattr(l, k, v)
        changed.append(k)
    if "height" in a:
        l.height = None if a["height"] in (None, "") else max(1, int(float(a["height"])))
        changed.append("height")
    from ..ops import overlays

    changed += overlays.set_fields(l, a)
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
    from ..ops import scenes

    start = float(a.get("start") or 0)
    l = Layer(id=p.new_id("t"), type="text", text=a.get("text") or "New text", start=start,
              end=_num(a, "end") or min(p.duration, start + 3), y=a.get("y") or "30%", x=a.get("x") or "50%")
    if isinstance(a.get("style"), dict):
        data = l.style.model_dump()
        data.update({k: v for k, v in a["style"].items() if k in data})
        l.style = TextStyle(**data)
    p.layers.append(l)
    if a.get("look"):
        from ..ops import text_looks

        text_looks.apply(l, a["look"], animation=a.get("animation", True))
    scenes.place_in_scene(p, l, a.get("scene"))
    return f"add text {l.id}"


@op("layer_add_video")
def _layer_add_video(p: Project, a: dict) -> str:
    from ..ops import overlays

    start = float(a.get("start") or 0)
    extra = {k: a[k] for k in ("x", "y", "width", "volume", "src_in", "loop", "blend", "mask") if a.get(k) not in (None, "")}
    l = overlays.add_video(p, a["source"], start, _num(a, "end"), **extra)
    return f"add video overlay {l.id}"


@op("layer_add_adjust")
def _layer_add_adjust(p: Project, a: dict) -> str:
    start = float(a.get("start") or 0)
    end = _num(a, "end")
    if end is None:
        end = min(p.duration, start + 3) if p.duration > start else None
    l = Layer(id=p.new_id("adj"), type="adjust", start=start, end=end)
    p.layers.append(l)
    return f"add adjustment layer {l.id}"


@op("layer_grade")
def _layer_grade(p: Project, a: dict) -> str:
    """Colour of a video overlay or adjustment layer: correction values, look + intensity, clear."""
    from ..ops import tracks

    l = tracks.grade_layer(p, a["layer"], {k: a.get(k) for k in tracks.GRADE_KEYS}, a.get("look"), bool(a.get("clear")))
    return f"{l.id}: " + ("clear colour" if a.get("clear") else "colour " + ", ".join(
        f"{k} {a[k]}" for k in (*tracks.GRADE_KEYS, "look") if a.get(k) not in (None, "")))


@op("template_apply")
def _template_apply(p: Project, a: dict) -> str:
    from ..ops import templates

    ids = templates.apply(p, _PDIR[0], a["key"], float(a.get("start") or 0))
    return f"template {a['key']}: {', '.join(ids)}"


@op("layer_add_shape")
def _layer_add_shape(p: Project, a: dict) -> str:
    from ..ops import overlays, scenes

    kw = {k: a[k] for k in ("x", "y") if a.get(k) not in (None, "")}
    l = overlays.add_shape(p, a.get("shape") or "rect", float(a.get("start") or 0), _num(a, "end"), **kw)
    overlays.set_fields(l, {k: v for k, v in a.items() if k in overlays.FRAME_FIELDS})
    scenes.place_in_scene(p, l, a.get("scene"))
    return f"add {l.shape} {l.id}"


# ---- design scenes (Design studio) -----------------------------------------------------------------------------

@op("scene_add")
def _scene_add(p: Project, a: dict) -> str:
    from ..ops import scenes

    sc = scenes.add(p, _PDIR[0], float(a.get("dur") or 5), a.get("after"), a.get("name") or "",
                    a.get("background") or None, a.get("transition") or "cut", a.get("notes") or "")
    return f"add scene {sc.id}"


@op("scene_set")
def _scene_set(p: Project, a: dict) -> str:
    from ..ops import scenes

    bg = (a["background"] or None) if "background" in a else False
    scenes.set_scene(p, _PDIR[0], a["scene"], _num(a, "dur"), a.get("name"), bg, a.get("transition"), a.get("notes"),
                     a.get("why"))
    return f"{a['scene']}: " + ", ".join(k for k in a if k != "scene")


@op("scene_move")
def _scene_move(p: Project, a: dict) -> str:
    from ..ops import scenes

    scenes.move(p, _PDIR[0], a["scene"], int(a["index"]))
    return f"move scene {a['scene']} to {a['index']}"


@op("scene_dup")
def _scene_dup(p: Project, a: dict) -> str:
    from ..ops import scenes

    sc = scenes.duplicate(p, _PDIR[0], a["scene"])
    return f"duplicate scene {a['scene']} as {sc.id}"


@op("scene_remove")
def _scene_remove(p: Project, a: dict) -> str:
    from ..ops import scenes

    scenes.remove(p, _PDIR[0], a["scene"])
    return f"remove scene {a['scene']}"


@op("overlay_add")
def _overlay_add(p: Project, a: dict) -> str:
    from ..ops import overlays

    l = overlays.add_from_library(p, _PDIR[0], a["key"], float(a.get("start") or 0))
    return f"add {overlays.LIBRARY[a['key']].label} ({l.id})"


@op("layer_place")
def _layer_place(p: Project, a: dict) -> str:
    """Timeline drag of an overlay: new times and / or track (ops/tracks.py, shared with `vedit place`)."""
    from ..ops import tracks

    end: Any = tracks._KEEP if "end" not in a else _num(a, "end")
    track = int(a["track"]) if a.get("track") not in (None, "") else None
    l = tracks.place_layer(p, a["layer"], _num(a, "start"), end, track, bool(a.get("new_track")), _num(a, "src_in"))
    return f"{l.id}: track {l.track}, {l.start:.2f}-{'end' if l.end is None else f'{l.end:.2f}'}s"


@op("layer_add_media")
def _layer_add_media(p: Project, a: dict) -> str:
    """A video or image from the Media bin dropped on an overlay track: picture-in-picture / b-roll / sticker."""
    from ..ops import tracks

    from ..ops import scenes

    track = int(a["track"]) if a.get("track") not in (None, "") else None
    l = tracks.add_media_layer(p, a["source"], float(a.get("start") or 0), track, bool(a.get("new_track")))
    return f"add {l.type} overlay {l.id}"


@op("clip_place")
def _clip_place(p: Project, a: dict) -> str:
    """Main-track drag: the clip goes to that time (a gap fills the space before it) or between the clips it lands on."""
    t = cut.place_clip(p, _PDIR[0], a["clip"], float(a.get("start") or 0))
    return f"{a['clip']} to {t:.2f}s"


@op("clip_to_overlay")
def _clip_to_overlay(p: Project, a: dict) -> str:
    """A main-track clip dragged up onto the overlay tracks (ops/tracks.py, shared with `vedit to-overlay`)."""
    from ..ops import tracks

    cid = a["clip"]
    l = tracks.clip_to_overlay(p, cid, float(a.get("start") or 0), int(a.get("track") or 0), bool(a.get("new_track", True)))
    return f"{cid} -> overlay {l.id} on track {l.track}"


@op("overlay_to_clip")
def _overlay_to_clip(p: Project, a: dict) -> str:
    """A video overlay dragged down onto the main track (ops/tracks.py, shared with `vedit to-clip`)."""
    from ..ops import tracks

    clip = tracks.overlay_to_clip(p, a["layer"], None if a.get("index") in (None, "") else int(a["index"]))
    return f"overlay {a['layer']} -> clip {clip.id}"


@op("layer_move")
def _layer_move(p: Project, a: dict) -> str:
    from ..ops import overlays

    i = overlays.move_layer(p, a["layer"], a.get("to", "up"))
    return f"{a['layer']} to position {i}"


@op("layer_remove")
def _layer_remove(p: Project, a: dict) -> str:
    p.layers.remove(p.layer(a["layer"]))
    return f"remove layer {a['layer']}"


@op("preset_add")
def _preset_add(p: Project, a: dict) -> str:
    """Add an animation preset. replace_phase: it replaces the layer's other preset of the same phase (in / out /
    loop), as the Design studio's Animate panel does (one entrance, one exit, one loop)."""
    from ..project import Preset

    pr = Preset(name=a["name"], duration=_num(a, "duration"), params=a.get("params") or {})
    anim.validate_preset(pr)
    l = p.layer(a["layer"])
    phase = anim.PRESETS[pr.name][0]
    keep = [x for x in l.presets if x.name != pr.name
            and not (a.get("replace_phase") and anim.PRESETS.get(x.name, ("",))[0] == phase)]
    l.presets = keep + [pr]
    return f"{l.id}: animate {pr.name}"


@op("layer_dup")
def _layer_dup(p: Project, a: dict) -> str:
    from ..ops import overlays

    l = overlays.duplicate_layer(p, a["layer"], a.get("dx", 40), a.get("dy", 40))
    return f"duplicate {a['layer']} as {l.id}"


@op("batch")
def _batch(p: Project, a: dict) -> str:
    """Several actions as one undo step (a drag that moves three layers, a resize that sets size and position)."""
    msgs = []
    for item in a.get("ops") or []:
        name = item.get("op")
        if name == "batch" or name not in _OPS:
            raise VeditError(f"Unknown action '{name}'")
        msgs.append(_OPS[name](p, item.get("args") or {}))
    if not msgs:
        raise VeditError("Nothing to do")
    return msgs[0] if len(msgs) == 1 else f"{len(msgs)} changes: " + "; ".join(msgs)[:200]


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
    from ..ops import tracks

    m = tracks.set_music(p, a["music"], _num(a, "volume"), _num(a, "start"), _num(a, "in"),
                         _num(a, "end") if "end" in a else tracks._KEEP,
                         bool(a["duck"]) if "duck" in a else None, bool(a["loop"]) if "loop" in a else None)
    return f"{m.id}: update"


@op("music_remove")
def _music_remove(p: Project, a: dict) -> str:
    p.music = [m for m in p.music if m.id != a["music"]]
    return f"remove music {a['music']}"


_ID_KEYS = {"clip", "layer", "music", "source", "id", "effect", "index", "scene"}


def _shape(v: Any, key: str = "") -> Any:
    """The args with every value blanked except the ids: the same field on the same object gives the same shape."""
    if isinstance(v, dict):
        return {k: _shape(x, k) for k, x in sorted(v.items())}
    return v if key in _ID_KEYS else None


def coalesce_key(name: str, args: dict[str, Any]) -> str | None:
    """Property edits (`*_set`) of the same field on the same object can merge into one undo step."""
    if not name.endswith("_set"):
        return None
    return f"{name}:{json.dumps(_shape(args), sort_keys=True)}"


def apply(pdir: Path, name: str, args: dict[str, Any]) -> dict:
    fn = _OPS.get(name)
    if not fn:
        raise VeditError(f"Unknown action '{name}'")
    _PDIR[0] = pdir
    with history.transaction(pdir, name) as tx:
        tx.coalesce = coalesce_key(name, args)
        tx.message = fn(tx.project, args)
    return {"ok": True, "message": tx.message}


FORMAT_SIZES = {"reels": (1080, 1920), "portrait45": (1080, 1350), "square": (1080, 1080), "landscape": (1920, 1080)}


def closest_format(w: int, h: int) -> tuple[int, int]:
    """The preset canvas whose aspect ratio is nearest to w:h."""
    import math

    return min(FORMAT_SIZES.values(), key=lambda s: abs(math.log((w / h) / (s[0] / s[1]))))


def import_file(pdir: Path, filename: str, data_path: Path, append: bool, index: int | None, fit_canvas: bool = False) -> dict:
    """Store an uploaded / dropped file in media/ and add it as a source (and clip).

    `fit_canvas`: when this is the project's first clip, switch the canvas to the format closest to the video's shape
    (a project made by dropping a landscape video becomes 16:9)."""
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
            if fit_canvas and len(p.clips) == 1 and info.get("width") and info.get("height"):
                p.canvas.width, p.canvas.height = closest_format(info["width"], info["height"])
    return {"ok": True, "source": sid, "clip": clip_id, "has_video": info["has_video"]}

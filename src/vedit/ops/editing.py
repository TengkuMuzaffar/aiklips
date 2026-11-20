"""Everyday editor operations: markers / chapters, keyframes on any layer property, trimming a clip to the
playhead, and copying attributes (colour, effects, framing, sound, text style, animation...) between items."""

from __future__ import annotations

import copy

from ..project import Clip, Keyframe, Layer, Marker, Project, VeditError
from . import cut

# ---- markers -----------------------------------------------------------------------------------------------------


def add_marker(p: Project, t: float, label: str = "", color: str | None = None) -> Marker:
    t = round(max(0.0, min(t, p.duration)), 3)
    m = Marker(id=p.new_id("m"), t=t, label=label.strip(), **({"color": color} if color else {}))
    p.markers.append(m)
    p.markers.sort(key=lambda k: k.t)
    return m


def marker(p: Project, marker_id: str) -> Marker:
    for m in p.markers:
        if m.id == marker_id:
            return m
    raise VeditError(f"No marker '{marker_id}'. Markers: {', '.join(m.id for m in p.markers) or 'none'}")


def set_marker(p: Project, marker_id: str, t: float | None = None, label: str | None = None, color: str | None = None) -> Marker:
    m = marker(p, marker_id)
    if t is not None:
        m.t = round(max(0.0, min(float(t), p.duration)), 3)
    if label is not None:
        m.label = label.strip()
    if color:
        m.color = color
    p.markers.sort(key=lambda k: k.t)
    return m


def remove_marker(p: Project, marker_id: str) -> None:
    p.markers.remove(marker(p, marker_id))


def _ts(t: float) -> str:
    t = int(t)
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def chapters(p: Project) -> tuple[list[str], list[str]]:
    """YouTube chapter lines from the labelled markers (first one forced to 0:00), plus what YouTube would reject."""
    ms = [m for m in sorted(p.markers, key=lambda k: k.t) if m.label]
    if not ms:
        return [], ["Add labelled markers first (`vedit marker add --at T --label Intro`)."]
    pts = [(0.0 if i == 0 else m.t, m.label) for i, m in enumerate(ms)]
    lines = [f"{_ts(t)} {label}" for t, label in pts]
    issues = []
    if len(pts) < 3:
        issues.append("YouTube needs at least 3 chapters.")
    ends = [t for t, _ in pts[1:]] + [p.duration]
    short = [label for (t, label), e in zip(pts, ends) if e - t < 10]
    if short:
        issues.append(f"Chapters must be at least 10 s long: {', '.join(short)}.")
    return lines, issues


# ---- keyframes ---------------------------------------------------------------------------------------------------

KF_PROPS = ("x", "y", "scale", "rotation", "opacity", "w", "h", "sx", "sy", "fill")
KF_EPS = 0.02  # keyframes closer than this (seconds) are the same keyframe


def set_keyframe(layer: Layer, t_local: float, prop: str, value) -> Keyframe:
    """Set `prop` at local time t (seconds since the layer starts), creating the keyframe if needed."""
    t_local = round(max(0.0, t_local), 3)
    if prop == "ease":  # the curve from this keyframe to the next (the keyframe must exist)
        from .animate import ease

        ease(str(value), 0.5)
        for k in layer.keyframes:
            if abs(k.t - t_local) <= KF_EPS:
                k.ease = str(value)
                return k
        raise VeditError(f"No keyframe at {t_local:g}s on {layer.id}")
    if prop not in KF_PROPS:
        raise VeditError(f"Keyframe property must be one of: {', '.join(KF_PROPS)}, ease")
    if prop in ("x", "y"):
        v = value if isinstance(value, str) and value.strip().endswith("%") else float(value)
    elif prop == "fill":
        from .animate import mix_hex

        v = mix_hex(str(value), str(value), 0)  # checks + normalises the colour
    else:
        v = float(value)
    for k in layer.keyframes:
        if abs(k.t - t_local) <= KF_EPS:
            setattr(k, prop, v)
            return k
    k = Keyframe(t=t_local, **{prop: v})
    layer.keyframes.append(k)
    layer.keyframes.sort(key=lambda x: x.t)
    return k


def remove_keyframe(layer: Layer, t_local: float, prop: str | None = None) -> int:
    """Remove `prop` (or every property) from the keyframe at t; empty keyframes are dropped. Returns how many."""
    n = 0
    for k in list(layer.keyframes):
        if abs(k.t - t_local) > KF_EPS:
            continue
        for pr in ([prop] if prop else list(KF_PROPS) + ["reveal"]):
            if getattr(k, pr, None) is not None:
                setattr(k, pr, None)
                n += 1
        if all(getattr(k, pr) is None for pr in (*KF_PROPS, "reveal")):
            layer.keyframes.remove(k)
    return n


# ---- trim to the playhead ----------------------------------------------------------------------------------------


def trim_to(p: Project, at: float, side: str) -> Clip:
    """Trim the clip under `at` so it starts (side="start") or ends ("end") there. Later clips move up."""
    from ..render.frame import clip_at

    i, local = clip_at(p, at)
    c = p.clips[i]
    src_t = c.in_ + local * c.speed
    if side == "start":
        cut.retime_clip(p, c.id, src_t, c.out)
    elif side == "end":
        cut.retime_clip(p, c.id, c.in_, src_t)
    else:
        raise VeditError("side must be start or end")
    return p.clip(c.id)


# ---- copy / paste attributes ---------------------------------------------------------------------------------------

CLIP_GROUPS: dict[str, tuple[str, ...]] = {
    "colour": ("grade",),
    "effects": ("effects",),
    "framing": ("fit", "zoom", "zoom_to", "focus_x", "focus_y"),
    "sound": ("volume", "fade_in", "fade_out", "voice"),
    "speed": ("speed",),
}
LAYER_GROUPS: dict[str, tuple[str, ...]] = {
    "style": ("style",),
    "frame": ("blend", "radius", "border_width", "border_color", "mask", "chroma", "chroma_similarity"),
    "transform": ("scale", "rotation", "opacity"),
    "animation": ("presets", "keyframes"),
}


def paste_attributes(p: Project, kind: str, src_id: str, targets: list[str], groups: list[str]) -> int:
    """Copy attribute groups from one clip / layer to others. Returns how many items changed."""
    table = CLIP_GROUPS if kind == "clip" else LAYER_GROUPS if kind == "layer" else None
    if table is None:
        raise VeditError("kind must be clip or layer")
    unknown = [g for g in groups if g not in table]
    if unknown or not groups:
        raise VeditError(f"Groups for a {kind}: {', '.join(table)}")
    get = p.clip if kind == "clip" else p.layer
    src = get(src_id)
    n = 0
    for tid in targets:
        if tid == src_id:
            continue
        dst = get(tid)
        for g in groups:
            if kind == "layer" and g == "style" and (dst.type not in ("text", "captions") or src.type not in ("text", "captions")):
                continue  # text style only means something on text
            for f in table[g]:
                if f == "speed":  # retimes the clip and moves the overlays after it, like the Speed field
                    if abs(dst.speed - src.speed) > 1e-6:
                        cut.set_clip_speed(p, dst.id, src.speed)
                    continue
                val = copy.deepcopy(getattr(src, f))
                if f == "effects":  # keep the target's own masks / AI effects, replace its filters
                    from .fx_filters import _new_id

                    keep = [e for e in dst.effects if e.type != "fx"]
                    dst.effects = keep
                    for e in [e for e in val if e.type == "fx"]:
                        e.params["id"] = _new_id(p)
                        dst.effects.append(e)
                    continue
                setattr(dst, f, val)
        n += 1
    if kind == "clip":
        from ..project import validate

        validate(p)  # a pasted speed can push a clip past its source
    return n

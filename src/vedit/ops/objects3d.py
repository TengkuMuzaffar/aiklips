"""3D object layers: add, change, move (A -> B) and spin. Shared by the CLI (`vedit 3d ...`) and the editor's
3D tab (editor/actions.py). Drawing: ops/render3d.py; recipes: ops/items3d.py."""

from __future__ import annotations

from typing import Any

from ..project import OBJ3D_KINDS, Layer, Obj3D, Preset, Project, VeditError
from .animate import _AXES, ease
from .text import rgba

OBJ_FIELDS = ("text", "font", "src", "color", "color2", "depth", "yaw", "pitch", "roll", "fov", "light", "outline", "gait")
LOOPS = ("once", "repeat", "pingpong", "continue")


def obj_layer(p: Project, lid: str) -> Layer:
    l = p.layer(lid)
    if l.type != "obj3d" or l.obj3d is None:
        raise VeditError(f"{lid} is not a 3D object (`vedit 3d add`)")
    return l


def _check(ob: Obj3D, pdir=None) -> None:
    if ob.kind not in OBJ3D_KINDS:
        raise VeditError(f"Unknown 3D kind '{ob.kind}'. Options: {', '.join(OBJ3D_KINDS)}")
    for c in (ob.color, ob.color2, ob.outline):
        if c:
            rgba(c)
    if ob.kind == "text" and not (ob.text or "").strip():
        raise VeditError("3D text needs --text")
    if ob.kind == "model" and not ob.src:
        raise VeditError("A 3D model needs --src model.glb")
    if not 5 <= ob.fov <= 100:
        raise VeditError("fov must be 5-100 degrees")
    if not 0.02 <= ob.depth <= 3:
        raise VeditError("depth must be 0.02-3 (fraction of the letter height)")
    if ob.kind != "model" or pdir is not None:
        from .render3d import mesh_at

        mesh_at(ob, 0.0, pdir)  # unknown settings, missing glyphs / files fail here, not in the export


def set_params(ob: Obj3D, params: dict | None) -> None:
    """Merge recipe settings; a value of None (or "default") removes one."""
    for k, v in (params or {}).items():
        if v is None or v == "default":
            ob.params.pop(k, None)
        else:
            ob.params[k] = v


def add_object(p: Project, kind: str, *, text: str | None = None, font: str | None = None, src: str | None = None,
               color: str | None = None, color2: str | None = None, params: dict | None = None, start: float = 0.0,
               end: float | None = None, x: Any = "50%", y: Any = "50%", width: int | None = None, pdir=None,
               spin: float | None = None, intro: bool = True, **fields) -> Layer:
    ob = Obj3D(kind=kind, text=text, font=font, src=src, color=color, color2=color2, params=dict(params or {}),
               **{k: v for k, v in fields.items() if k in OBJ_FIELDS and v is not None})
    _check(ob, pdir)
    presets = []
    if intro:
        presets.append(Preset(name="spin3d_in", duration=0.8))
    if spin:
        presets.append(Preset(name="spin3d", params={"axis": "y", "period": float(spin)}))
    w = width or round(p.canvas.width * (0.8 if kind in ("text", "bus", "truck", "plane") else 0.55))
    l = Layer(id=p.new_id("o3d"), type="obj3d", obj3d=ob, start=float(start), end=end if end is not None else round(float(start) + 4, 3),
              x=x, y=y, width=int(w), presets=presets)
    p.layers.append(l)
    return l


def set_object(l: Layer, params: dict | None = None, kind: str | None = None, pdir=None, **fields) -> Obj3D:
    ob = l.obj3d.model_copy(deep=True)
    if kind:
        ob.kind = kind
    for k, v in fields.items():
        if k in OBJ_FIELDS and v is not None:
            setattr(ob, k, None if v in ("none", "") and k in ("outline", "color", "color2", "font") else v)
    set_params(ob, params)
    _check(ob, pdir)
    l.obj3d = ob
    return ob


def _pos(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return ",".join(str(x) for x in v)
    s = str(v).replace(" ", "")
    if len(s.split(",")) != 2:
        raise VeditError(f"Positions are x,y in px or NN% (e.g. 10%,60%), got {v!r}")
    return s


def move_object(l: Layer, frm: Any, to: Any, dur: float | None = None, loop: str = "once", face: bool = True, hop: float = 0.0,
                scale_from: float = 1.0, scale_to: float = 1.0, ease_name: str | None = None, off: bool = False) -> Preset | None:
    """Travel from A to B (replaces an earlier travel). dur = seconds from A to B (default: the layer's length for
    `once`, else 3)."""
    l.presets = [pr for pr in l.presets if pr.name != "travel"]
    if off:
        return None
    if loop not in LOOPS:
        raise VeditError(f"loop must be one of {', '.join(LOOPS)}")
    if dur is None:
        length = (l.end - l.start) if l.end is not None else 4.0
        dur = max(0.3, length) if loop == "once" else 3.0
    if dur <= 0:
        raise VeditError("dur must be > 0 seconds")
    k: dict = {"from": _pos(frm), "to": _pos(to), "dur": round(float(dur), 3), "loop": loop}
    if not face:
        k["face"] = False
    if hop:
        k["hop"] = float(hop)
    if scale_from != 1 or scale_to != 1:
        k["scale_from"], k["scale_to"] = float(scale_from), float(scale_to)
    if ease_name:
        ease(ease_name, 0.5)
        k["ease"] = ease_name
    pr = Preset(name="travel", params=k)
    l.presets.append(pr)
    return pr


def spin_object(l: Layer, axis: str = "y", period: float | None = 3.0, off: bool = False, sway: float | None = None) -> None:
    """Turn round and round (period = seconds per full 360 turn, negative = the other way), or sway back and forth
    `sway` degrees. Replaces an earlier spin / sway."""
    if axis not in _AXES:
        raise VeditError(f"axis must be one of {', '.join(_AXES)}")
    l.presets = [pr for pr in l.presets if pr.name not in ("spin3d", "sway3d")]
    if off:
        return
    if sway:
        l.presets.append(Preset(name="sway3d", params={"axis": axis, "degrees": float(sway), "period": abs(float(period or 3.0))}))
        return
    if not period:
        raise VeditError("period must not be 0")
    l.presets.append(Preset(name="spin3d", params={"axis": axis, "period": float(period)}))


def describe(l: Layer) -> dict:
    ob = l.obj3d
    return {"layer": l.id, **ob.model_dump(exclude_defaults=True), "kind": ob.kind, "width": l.width,
            "motion": [pr.model_dump(exclude_defaults=True) for pr in l.presets if pr.name in ("travel", "spin3d", "sway3d", "spin3d_in")]}

"""3D objects: a small perspective renderer in Python (no browser, no GPU), like ops/iso.py but turning freely.

The object (ops/items3d.py recipe, 3D text from the font, or the user's own model) is scaled to fit a sphere, turned
by yaw (round its up axis) -> pitch -> roll, seen through a camera with `fov`, and drawn low-poly: faces facing away are
skipped, the rest are lit flat by one light from the upper left and filled back to front (painter's algorithm), at
2x then scaled down for smooth edges.

The sprite is always the same size (the layer's width, square) whatever the turn, so a spinning object never jitters;
where it is on the canvas (A -> B) is the layer's x / y. Used by the export (render/layers.py) and the live preview
(preview_server/sprites.py), so both match.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..project import Layer, Obj3D, VeditError
from . import items3d
from .mesh3d import Mesh, newell, rx, ry, rz, text_mesh
from .text import rgba

VERSION = "1"  # bump when the drawing changes: cached layer videos are made again
SS = 2
LIGHT = np.array([-0.45, 0.75, 0.55])
LIGHT = LIGHT / np.linalg.norm(LIGHT)
GAIT_FPS = 30  # legs / wheels / wings change at most this often (the export reuses frames in between)
_MESHES: OrderedDict[str, tuple[Mesh, np.ndarray, float]] = OrderedDict()


def animated(ob: Obj3D) -> bool:
    return ob.gait and ob.kind in items3d.GAITED


def _model_mesh(ob: Obj3D, pdir: Path | None) -> Mesh:
    from ..project import resolve_media
    from .effects import require

    if not ob.src:
        raise VeditError("A 3D model needs a file: --src model.glb (.gltf / .obj / .stl)")
    path = resolve_media(pdir, ob.src) if pdir else Path(ob.src).expanduser()
    if not Path(path).exists():
        raise VeditError(f"3D model not found: {ob.src}")
    trimesh = require("trimesh", "ai")
    mesh = trimesh.load(str(path), force="mesh")
    max_faces = int(ob.params.get("max_faces", 4000))
    if len(mesh.faces) > max_faces:
        try:
            mesh = mesh.simplify_quadric_decimation(face_count=max_faces)
        except Exception:  # noqa: BLE001 - no decimation backend: draw every face (slower)
            pass
    cols = None
    try:
        fc = np.asarray(mesh.visual.to_color().face_colors)
        if len(fc) == len(mesh.faces) and not (fc[:, :3] == fc[0, :3]).all():
            cols = fc
    except Exception:  # noqa: BLE001
        cols = None
    base = rgba(ob.color or "#B7BED0")
    m = Mesh([tuple(v) for v in np.asarray(mesh.vertices, float).tolist()])
    from .mesh3d import Face

    for i, f in enumerate(np.asarray(mesh.faces).tolist()):
        c = tuple(int(x) for x in cols[i][:4]) if cols is not None and not ob.color else base
        m.faces.append(Face(f, c, outline=False))
    return m


def _build(ob: Obj3D, t: float, pdir: Path | None) -> Mesh:
    if ob.kind == "text":
        c1, c2 = items3d.TEXT_DEFAULTS
        return text_mesh(ob.text or "3D", ob.font, ob.depth, ob.color or c1, ob.color2 or c2, int(ob.params.get("smooth", 4)),
                         float(ob.params.get("spacing", 0.02)))
    if ob.kind == "model":
        return _model_mesh(ob, pdir)
    return items3d.build(ob.kind, ob.params, ob.color, ob.color2, t)


def mesh_at(ob: Obj3D, t: float = 0.0, pdir: Path | None = None) -> tuple[Mesh, np.ndarray, float]:
    """The object's mesh at t, plus the centre and radius that fit it (always taken at rest, t = 0, so moving legs
    never rescale it)."""
    t = round(t * GAIT_FPS) / GAIT_FPS if animated(ob) else 0.0
    base = json.dumps(ob.model_dump(include={"kind", "text", "font", "src", "color", "color2", "depth", "params"}), sort_keys=True, default=str)
    key = hashlib.sha1(f"{base}|{pdir}|{t}".encode()).hexdigest()
    if key in _MESHES:
        _MESHES.move_to_end(key)
        return _MESHES[key]
    rest_key = hashlib.sha1(f"{base}|{pdir}|0.0".encode()).hexdigest()
    if t and rest_key in _MESHES:
        centre, radius = _MESHES[rest_key][1], _MESHES[rest_key][2]
    else:
        rest = _build(ob, 0.0, pdir)
        v = rest.array()
        if not len(v):
            raise VeditError(f"3D {ob.kind} has nothing to draw")
        centre = (v.max(0) + v.min(0)) / 2
        radius = float(np.linalg.norm(v - centre, axis=1).max()) or 1.0
        _MESHES[rest_key] = (rest, centre, radius)
    m = _MESHES[rest_key][0] if not t else _build(ob, t, pdir)
    _MESHES[key] = (m, centre, radius)
    while len(_MESHES) > 160:
        _MESHES.popitem(last=False)
    return m, centre, radius


def _shade(c, k: float):
    if k >= 0:
        return tuple(round(v + (255 - v) * k) for v in c[:3]) + (c[3],)
    return tuple(round(v * (1 + k)) for v in c[:3]) + (c[3],)


def rotation(yaw: float, pitch: float, roll: float) -> np.ndarray:
    """Turn round the object's own up axis (yaw), then tip it towards the camera (pitch), then roll it."""
    return rz(roll) @ rx(pitch) @ ry(yaw)


def render_obj3d(layer: Layer, scale: float = 1.0, yaw: float | None = None, pitch: float | None = None, roll: float | None = None,
                 t: float = 0.0, pdir: Path | None = None, size: int | None = None) -> Image.Image:
    """The object as a transparent square sprite `layer.width * scale` wide. yaw / pitch / roll default to the object's
    resting turn (the layer's props_at values include it); t = seconds into the layer (legs, wheels, wings)."""
    ob = layer.obj3d or Obj3D()
    yaw = ob.yaw if yaw is None else yaw
    pitch = ob.pitch if pitch is None else pitch
    roll = ob.roll if roll is None else roll
    S = size or max(16, round((layer.width or 600) * scale))
    mesh, centre, radius = mesh_at(ob, t, pdir)
    v = (mesh.array() - centre) / radius
    R = rotation(yaw, pitch, roll)
    v = v @ R.T
    half = math.radians(max(5.0, min(100.0, ob.fov)) / 2)
    D = 1.0 / math.sin(half)  # camera distance: the unit sphere just fits the view
    f = 0.96 * (S * SS / 2) / math.tan(half)
    depth = D - v[:, 2]
    px = S * SS / 2 + f * v[:, 0] / depth
    py = S * SS / 2 - f * v[:, 1] / depth
    cam = np.array([0.0, 0.0, D])
    k_light = max(0.0, min(1.0, ob.light))
    line = rgba(ob.outline) if ob.outline else None
    lw = max(1, round(S * SS / 420))
    todo = []
    for fc in mesh.faces:
        pts = v[fc.loop]
        n = newell(pts)
        nl = np.linalg.norm(n)
        if nl < 1e-12:
            continue
        n = n / nl
        c = pts.mean(0)
        if np.dot(n, cam - c) <= 1e-9:
            continue  # facing away
        lam = max(0.0, float(np.dot(n, LIGHT)))
        todo.append((float(c[2]), fc, k_light * (lam * 1.05 - 0.5)))
    todo.sort(key=lambda x: x[0])  # far first
    img = Image.new("RGBA", (S * SS, S * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for _, fc, sh in todo:
        col = _shade(fc.color, sh)
        loop = [(float(px[i]), float(py[i])) for i in fc.loop]
        if not fc.holes:
            d.polygon(loop, fill=col, outline=line if (fc.outline and line) else col, width=lw if fc.outline and line else 1)
            continue
        loops = [loop] + [[(float(px[i]), float(py[i])) for i in h] for h in fc.holes]
        xs = [p[0] for lp in loops for p in lp]
        ys = [p[1] for lp in loops for p in lp]
        x0, y0 = max(0, int(min(xs)) - 2), max(0, int(min(ys)) - 2)
        x1, y1 = min(img.width, int(max(xs)) + 3), min(img.height, int(max(ys)) + 3)
        if x1 <= x0 or y1 <= y0:
            continue
        mask = Image.new("L", (x1 - x0, y1 - y0), 0)
        md = ImageDraw.Draw(mask)
        md.polygon([(x - x0, y - y0) for x, y in loops[0]], fill=255, outline=255)
        for h in loops[1:]:
            md.polygon([(x - x0, y - y0) for x, y in h], fill=0)
        img.paste(col, (x0, y0, x1, y1), mask)
        if fc.outline and line:
            for lp in loops:
                d.line(lp + [lp[0]], fill=line, width=lw)
    return img.resize((S, S), Image.LANCZOS)


def thumbnail(kind: str, size: int = 240) -> Image.Image:
    """A catalog picture of one kind (the editor's 3D tab)."""
    ob = Obj3D(kind=kind, text="3D" if kind == "text" else None)
    if kind == "model":
        ob = Obj3D(kind="gem", color="#B7BED0", color2="#8E95A8")
    return render_obj3d(Layer(id="thumb", type="obj3d", obj3d=ob, width=size), 1.0, t=0.2)

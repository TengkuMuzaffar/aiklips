"""Isometric miniature worlds: a small 3D renderer in Python (no browser, no GPU).

A scene is items from the library (ops/iso_items.py: trees, buildings, cars, desks, people, signs ...) and raw
blocks. Each item is a recipe of primitives: box, wedge (a slope), cylinder, cone, pyramid and sphere, in grid units
(x / y on the floor, z up). Every primitive is projected isometrically (30 degrees); its visible faces are filled and
lit by their direction (tops light, the left side darkest), drawn back to front. Text and images can sit on a face
(signs, screens, billboards), warped onto it.

The `draw` progress (0..1, preset draw_in) builds the world: each item arrives in turn (drop / grow / pop). Used by iso
layers in the export and the live preview (sprites), so both match.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from ..project import IsoScene, Layer, VeditError
from .animate import ease
from .text import rgba

SS = 2
DROP = 1.2
COS, SIN = math.cos(math.radians(30)), math.sin(math.radians(30))
VIEW = (1.0, 1.0, 1.2)  # towards the camera: faces whose normal points this way are seen
SEG = 20  # cylinder / cone sides


@dataclass
class Prim:
    """A primitive in grid units. box / wedge / pyramid fill x..x+w, y..y+d, z..z+h; cylinder / cone / sphere are
    centred on (x + w/2, y + d/2) with radii w/2, d/2. wedge: rises towards `dir` (+x, +y, -x, -y)."""
    kind: str
    x: float
    y: float
    z: float
    w: float
    d: float
    h: float
    color: str
    dir: str = "+x"
    face_img: Image.Image | None = None  # pasted on the +y face (or `img_face`)
    img_face: str = "+y"
    outline: bool = True
    tags: dict = field(default_factory=dict)


def _proj(x: float, y: float, z: float) -> tuple[float, float]:
    return (x - y) * COS, (x + y) * SIN - z


def _shade(c: tuple[int, int, int, int], k: float) -> tuple[int, int, int, int]:
    if k >= 0:
        return tuple(round(v + (255 - v) * k) for v in c[:3]) + (c[3],)  # type: ignore[return-value]
    return tuple(round(v * (1 + k)) for v in c[:3]) + (c[3],)  # type: ignore[return-value]


def _light(n: tuple[float, float, float], light: float) -> float:
    """Brightness change of a face with normal n: tops lighter, the +y (left) side darkest, +x (right) a little dark."""
    nx, ny, nz = n
    return light * (nz - 1.1 * max(0.0, ny) - 0.45 * max(0.0, nx))


def _norm(v):
    m = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / m for c in v)


def _seen(n) -> bool:
    return n[0] * VIEW[0] + n[1] * VIEW[1] + n[2] * VIEW[2] > 1e-6


def faces(p: Prim) -> list[tuple[list[tuple[float, float, float]], tuple[float, float, float], str]]:
    """The visible faces of a primitive, back to front: (3D points, normal, which: '+x' / '+y' / '+z' / 'side')."""
    x0, y0, z0, x1, y1, z1 = p.x, p.y, p.z, p.x + p.w, p.y + p.d, p.z + p.h
    out = []
    if p.kind == "box":
        out.append(([(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)], (1, 0, 0), "+x"))
        out.append(([(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)], (0, 1, 0), "+y"))
        out.append(([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], (0, 0, 1), "+z"))
        return out
    if p.kind == "wedge":
        # a slope rising towards dir: the high edge is a vertical face, the sides triangles
        hi = {"+x": [(x1, y0), (x1, y1)], "-x": [(x0, y0), (x0, y1)], "+y": [(x0, y1), (x1, y1)], "-y": [(x0, y0), (x1, y0)]}[p.dir]
        lo = {"+x": [(x0, y0), (x0, y1)], "-x": [(x1, y0), (x1, y1)], "+y": [(x0, y0), (x1, y0)], "-y": [(x0, y1), (x1, y1)]}[p.dir]
        run = p.w if p.dir in ("+x", "-x") else p.d
        sx, sy = {"+x": (-1, 0), "-x": (1, 0), "+y": (0, -1), "-y": (0, 1)}[p.dir]
        slope_n = _norm((sx * p.h, sy * p.h, run))
        cand = [
            ([(*lo[0], z0), (*lo[1], z0), (*hi[1], z1), (*hi[0], z1)], slope_n, "slope"),
            ([(*hi[0], z0), (*hi[1], z0), (*hi[1], z1), (*hi[0], z1)], ({"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0)}[p.dir]), "back"),
        ]
        # the two triangular sides
        if p.dir in ("+x", "-x"):
            for yy, n in ((y0, (0, -1, 0)), (y1, (0, 1, 0))):
                cand.append(([(lo[0][0], yy, z0), (hi[0][0], yy, z0), (hi[0][0], yy, z1)], n, "side"))
        else:
            for xx, n in ((x0, (-1, 0, 0)), (x1, (1, 0, 0))):
                cand.append(([(xx, lo[0][1], z0), (xx, hi[0][1], z0), (xx, hi[0][1], z1)], n, "side"))
        out = [f for f in cand if _seen(f[1])]
        out.sort(key=lambda f: sum(a + b + c for a, b, c in f[0]) / len(f[0]))
        return out
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, p.w / 2, p.d / 2
    ring = [(cx + rx * math.cos(2 * math.pi * i / SEG), cy + ry * math.sin(2 * math.pi * i / SEG)) for i in range(SEG)]
    if p.kind == "cylinder":
        sides = []
        for i in range(SEG):
            (ax, ay), (bx, by) = ring[i], ring[(i + 1) % SEG]
            a = 2 * math.pi * (i + 0.5) / SEG
            n = (math.cos(a), math.sin(a), 0.0)
            if _seen(n):
                sides.append(([(ax, ay, z0), (bx, by, z0), (bx, by, z1), (ax, ay, z1)], n, "side"))
        sides.sort(key=lambda f: f[1][0] + f[1][1])
        return sides + [([(px, py, z1) for px, py in ring], (0, 0, 1), "+z")]
    if p.kind in ("cone", "pyramid"):
        if p.kind == "pyramid":
            base = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        else:
            base = ring
        tip = (cx, cy, z1)
        tri = []
        for i in range(len(base)):
            (ax, ay), (bx, by) = base[i], base[(i + 1) % len(base)]
            e1 = (bx - ax, by - ay, 0.0)
            e2 = (tip[0] - ax, tip[1] - ay, tip[2] - z0)
            n = _norm((e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]))
            if n[2] < 0:
                n = tuple(-c for c in n)
            if _seen(n):
                tri.append(([(ax, ay, z0), (bx, by, z0), tip], n, "side"))
        tri.sort(key=lambda f: f[1][0] + f[1][1])
        return tri
    raise VeditError(f"Unknown iso primitive {p.kind}")


def _affine_paste(canvas: Image.Image, img: Image.Image, O, A, B) -> None:
    """Paste `img` onto the parallelogram O + u*A + v*B (u, v in 0..1), in canvas pixels."""
    wi, hi = img.size
    m00, m01, m10, m11 = A[0] / wi, B[0] / hi, A[1] / wi, B[1] / hi
    det = m00 * m11 - m01 * m10
    if abs(det) < 1e-9:
        return
    i00, i01, i10, i11 = m11 / det, -m01 / det, -m10 / det, m00 / det
    coeffs = (i00, i01, -(i00 * O[0] + i01 * O[1]), i10, i11, -(i10 * O[0] + i11 * O[1]))
    warped = img.transform(canvas.size, Image.AFFINE, coeffs, resample=Image.BICUBIC)
    canvas.alpha_composite(warped)


def _face_frame(p: Prim, which: str):
    """(origin, u edge, v edge) of a box face in grid units, so images read left to right, top to bottom."""
    x0, y0, z0, x1, y1, z1 = p.x, p.y, p.z, p.x + p.w, p.y + p.d, p.z + p.h
    if which == "+y":
        return (x0, y1, z1), (p.w, 0, 0), (0, 0, -p.h)
    if which == "+x":
        return (x1, y1, z1), (0, -p.d, 0), (0, 0, -p.h)
    return (x0, y0, z1), (p.w, 0, 0), (0, p.d, 0)


def prim_bounds(prims: list[Prim]) -> tuple[float, float, float, float]:
    pts = []
    for p in prims:
        for x in (p.x, p.x + p.w):
            for y in (p.y, p.y + p.d):
                for z in (p.z, p.z + p.h):
                    pts.append(_proj(x, y, z))
    if not pts:
        return 0.0, 0.0, 1.0, 1.0
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    return min(xs), min(ys), max(xs), max(ys)


def scene_groups(sc: IsoScene) -> list[tuple[list[Prim], str | None, dict]]:
    """The scene as (primitives, label, item info) per item / raw block, in build order."""
    from .iso_items import expand

    out = [(p, it.label, {"kind": it.kind}) for it in sc.items for p in [expand(it, sc.theme)]]
    for b in sc.blocks:
        out.append(([Prim("box", b.x, b.y, b.z, b.w, b.d, b.h, b.color)], None, {"kind": "block"}))
    return out


def bounds(sc: IsoScene) -> tuple[float, float, float, float]:
    return prim_bounds([p for ps, _, _ in scene_groups(sc) for p in ps])


def block_progress(i: int, n: int, draw: float) -> float:
    """How far item i of n has arrived (0..1) when the world is `draw` of the way built."""
    if n <= 1:
        return max(0.0, min(1.0, draw))
    s, window = 0.75 / n, 1 - 0.75 / n * (n - 1)
    return max(0.0, min(1.0, (draw - i * s) / max(window, 1e-6)))


def _label_img(text: str, u: float) -> Image.Image:
    from ..project import TextStyle
    from .text import render_text

    return render_text(text, TextStyle(size=max(8, round(u * 0.42)), color="#1F2937", stroke_width=0, bg_color="#FFFFFFE6",
                                       bg_padding=max(3, round(u * 0.12)), bg_radius=max(3, round(u * 0.14)), font="poppins-bold"))


def render_iso(layer: Layer, scale: float = 1.0, draw: float = 1.0, width: float | None = None) -> Image.Image:
    sc = layer.iso or IsoScene()
    groups = scene_groups(sc)
    allp = [p for ps, _, _ in groups for p in ps]
    bx0, by0, bx1, by1 = prim_bounds(allp)
    labels = any(lbl for _, lbl, _ in groups)
    W_px = max(40, round((width or layer.width or 700) * scale))
    unit = W_px / max(1e-6, bx1 - bx0)
    top_room = DROP + (0.9 if labels else 0.0)
    by0 -= top_room
    H_px = max(40, round((by1 - by0) * unit))
    W, H = W_px * SS, H_px * SS
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = unit * SS
    line = rgba(sc.outline) if sc.outline else None
    lw = max(1, round(u * 0.018))
    n = len(groups)
    # build order: lower and further back first; draw order: back to front (painter's), per primitive
    rank = {gi: r for r, gi in enumerate(sorted(range(n), key=lambda k: (min((p.z for p in groups[k][0]), default=0),
                                                                          min((p.x + p.y for p in groups[k][0]), default=0))))}
    prog = [ease("back_out" if sc.build != "drop" else "ease_out", block_progress(rank[gi], n, draw)) if draw < 0.9995 else 1.0
            for gi in range(n)]
    items = []
    for gi, (ps, lbl, _) in enumerate(groups):
        if prog[gi] <= 0.001:
            continue
        base = min((p.z for p in ps), default=0.0)
        cx = sum(p.x + p.w / 2 for p in ps) / max(1, len(ps))
        cy = sum(p.y + p.d / 2 for p in ps) / max(1, len(ps))
        for p in ps:
            items.append((p, gi, base, cx, cy))
    items.sort(key=lambda t: (t[0].x + t[0].y + t[0].z, t[0].z, t[1]))

    def tf(gi: int, base: float, cx: float, cy: float):
        p = min(prog[gi], 1.2)
        if sc.build == "grow":
            return lambda x, y, z: (x, y, base + (z - base) * max(0.02, p))
        if sc.build == "pop":
            k = max(0.02, p)
            return lambda x, y, z: (cx + (x - cx) * k, cy + (y - cy) * k, base + (z - base) * k)
        lift = (1 - min(p, 1.0)) * DROP
        return lambda x, y, z: (x, y, z + lift)

    def S(pt):
        sx, sy = _proj(*pt)
        return (sx - bx0) * u, (sy - by0) * u

    for p, gi, base, cx, cy in items:
        T = tf(gi, base, cx, cy)
        alpha = min(1.0, prog[gi] * 2.5)
        col = rgba(p.color)
        col = col[:3] + (round(col[3] * alpha),)
        if p.kind == "sphere":
            c3 = T(p.x + p.w / 2, p.y + p.d / 2, p.z + p.h / 2)
            sx, sy = S(c3)
            rx = (p.w + p.d) / 4 * COS * 1.15 * u
            ry = p.h / 2 * u * 1.0
            for k, (dx, dy, f) in enumerate(((0, 0, 1.0), (-0.12, -0.14, 0.8), (-0.22, -0.28, 0.42))):
                shade = (-sc.light * 1.1, 0.0, sc.light * 1.6)[k]
                ex, ey = sx + dx * rx * 2, sy + dy * ry * 2
                d.ellipse((ex - rx * f, ey - ry * f, ex + rx * f, ey + ry * f), fill=_shade(col, shade),
                          outline=line if (k == 0 and line and p.outline) else None, width=lw if k == 0 else 0)
            continue
        for pts, nrm, which in faces(p):
            poly = [S(T(*q)) for q in pts]
            d.polygon(poly, fill=_shade(col, _light(nrm, sc.light)),
                      outline=line if (line and p.outline and which != "side") or (line and p.outline and p.kind in ("box", "wedge", "pyramid")) else None,
                      width=lw)
            if p.face_img is not None and which == p.img_face and p.kind == "box":
                O, A, B = _face_frame(p, which)
                Os = S(T(*O))
                A_s = (S(T(O[0] + A[0], O[1] + A[1], O[2] + A[2]))[0] - Os[0], S(T(O[0] + A[0], O[1] + A[1], O[2] + A[2]))[1] - Os[1])
                B_s = (S(T(O[0] + B[0], O[1] + B[1], O[2] + B[2]))[0] - Os[0], S(T(O[0] + B[0], O[1] + B[1], O[2] + B[2]))[1] - Os[1])
                fimg = p.face_img
                if alpha < 0.999:
                    fimg = fimg.copy()
                    fimg.putalpha(fimg.getchannel("A").point(lambda v: int(v * alpha)))
                _affine_paste(img, fimg, Os, A_s, B_s)
                d = ImageDraw.Draw(img)
    # floating labels over their items
    for gi, (ps, lbl, _) in enumerate(groups):
        if not lbl or prog[gi] < 0.6:
            continue
        top = max(p.z + p.h for p in ps)
        cx = sum(p.x + p.w / 2 for p in ps) / len(ps)
        cy = sum(p.y + p.d / 2 for p in ps) / len(ps)
        sx, sy = S((cx, cy, top + 0.45))
        li = _label_img(lbl, u)
        img.alpha_composite(li, (round(sx - li.width / 2), max(0, round(sy - li.height / 2))))
    return img.resize((W_px, H_px), Image.LANCZOS)


# ---- ready-made worlds ------------------------------------------------------------------------------------------

def world(kind: str = "city", theme: str = "pastel") -> IsoScene:
    """A ready-made scene: city | island | desk | stack | cafe | office | farm | data."""
    from .iso_items import WORLDS

    k = (kind or "city").strip().lower()
    if k not in WORLDS:
        raise VeditError(f"iso worlds: {', '.join(WORLDS)}")
    return WORLDS[k](theme)

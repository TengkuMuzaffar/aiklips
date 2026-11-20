"""Low-poly meshes for 3D objects (ops/items3d.py recipes, ops/render3d.py draws them).

A Mesh is vertices + faces. A face is a planar polygon (its outer loop counter-clockwise seen from outside, so its
normal points out) with an optional list of holes (the inside of an O on 3D text), one colour, and whether its edges
get the thin outline. Coordinates: x right / front, y up, z towards the viewer (right-handed).

Primitives are built around the origin and placed with a rotation + offset (`Mesh.add`), so a leg can swing round
its hip or a wheel round its axle. Convex primitives fix their own face winding from their centre.

Text: glyph outlines from the font file (fontTools), curves flattened to a few segments, extruded front to back.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from ..project import VeditError
from .text import rgba

Color = tuple[int, int, int, int]


@dataclass
class Face:
    loop: list[int]
    color: Color
    holes: list[list[int]] = field(default_factory=list)
    outline: bool = True


@dataclass
class Mesh:
    verts: list[tuple[float, float, float]] = field(default_factory=list)
    faces: list[Face] = field(default_factory=list)

    def add(self, other: "Mesh", R: np.ndarray | None = None, at=(0.0, 0.0, 0.0)) -> "Mesh":
        """Put `other` into this mesh, turned by R (3x3) round its own origin, then moved to `at`."""
        base = len(self.verts)
        v = np.asarray(other.verts, float).reshape(-1, 3)
        if R is not None:
            v = v @ np.asarray(R, float).T
        v = v + np.asarray(at, float)
        self.verts.extend(map(tuple, v.tolist()))
        for f in other.faces:
            self.faces.append(Face([i + base for i in f.loop], f.color, [[i + base for i in h] for h in f.holes], f.outline))
        return self

    def array(self) -> np.ndarray:
        return np.asarray(self.verts, float).reshape(-1, 3)


# ---- rotations ----------------------------------------------------------------

def rx(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def ry(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rz(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def newell(pts: np.ndarray) -> np.ndarray:
    """Normal of a (planar) polygon, pointing to the side its loop turns counter-clockwise; not normalised."""
    n = np.zeros(3)
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        n += ((a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]), (a[0] - b[0]) * (a[1] + b[1]))
    return n


def _convex(verts: list, loops: list[list[int]], color, outline: bool = True) -> Mesh:
    """A convex solid: every face is turned to face away from the centre."""
    v = np.asarray(verts, float)
    c = v.mean(0)
    col = rgba(color) if isinstance(color, str) else color
    faces = []
    for lp in loops:
        p = v[lp]
        if np.dot(newell(p), p.mean(0) - c) < 0:
            lp = lp[::-1]
        faces.append(Face(list(lp), col, outline=outline))
    return Mesh([tuple(x) for x in v.tolist()], faces)


# ---- primitives (centred on the origin unless said) -------------------------

def box(w: float, h: float, d: float, color, outline: bool = True) -> Mesh:
    """w along x, h along y, d along z, centred."""
    x, y, z = w / 2, h / 2, d / 2
    v = [(sx * x, sy * y, sz * z) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    loops = [[0, 1, 3, 2], [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6], [0, 2, 6, 4], [1, 5, 7, 3]]
    return _convex(v, loops, color, outline)


def frustum(r0: float, r1: float, h: float, color, seg: int = 10, outline: bool = False, cap_outline: bool = True,
            rz0: float | None = None, rz1: float | None = None) -> Mesh:
    """Along y from 0 to h: radius r0 at the bottom, r1 at the top (r1 = 0: a cone; r0 = r1: a cylinder).
    rz0 / rz1 = a different radius along z (oval)."""
    rz0 = r0 if rz0 is None else rz0
    rz1 = r1 if rz1 is None else rz1
    v, loops = [], []
    angs = [2 * math.pi * i / seg for i in range(seg)]
    for a in angs:
        v.append((r0 * math.cos(a), 0.0, rz0 * math.sin(a)))
    top_point = r1 <= 1e-9
    if top_point:
        v.append((0.0, h, 0.0))
        for i in range(seg):
            loops.append([i, (i + 1) % seg, seg])
    else:
        for a in angs:
            v.append((r1 * math.cos(a), h, rz1 * math.sin(a)))
        for i in range(seg):
            j = (i + 1) % seg
            loops.append([i, j, seg + j, seg + i])
    m = _convex(v, loops, color, outline)
    caps = [list(range(seg))] + ([] if top_point else [list(range(seg, 2 * seg))])
    capm = _convex(v, caps, color, cap_outline)
    m.faces.extend(capm.faces)
    return m


def cylinder(r: float, h: float, color, seg: int = 10, **kw) -> Mesh:
    return frustum(r, r, h, color, seg, **kw)


def cone(r: float, h: float, color, seg: int = 10, **kw) -> Mesh:
    return frustum(r, 0.0, h, color, seg, **kw)


def ellipsoid(rx_: float, ry_: float, rz_: float, color, seg: int = 10, rings: int = 6, outline: bool = False) -> Mesh:
    v = [(0.0, -ry_, 0.0)]
    for k in range(1, rings):
        phi = -math.pi / 2 + math.pi * k / rings
        for i in range(seg):
            a = 2 * math.pi * i / seg
            v.append((rx_ * math.cos(phi) * math.cos(a), ry_ * math.sin(phi), rz_ * math.cos(phi) * math.sin(a)))
    v.append((0.0, ry_, 0.0))
    top = len(v) - 1
    loops = []
    ring = lambda k, i: 1 + (k - 1) * seg + (i % seg)  # noqa: E731
    for i in range(seg):
        loops.append([0, ring(1, i), ring(1, i + 1)])
        loops.append([top, ring(rings - 1, i + 1), ring(rings - 1, i)])
    for k in range(1, rings - 1):
        for i in range(seg):
            loops.append([ring(k, i), ring(k + 1, i), ring(k + 1, i + 1), ring(k, i + 1)])
    return _convex(v, loops, color, outline)


def sphere(r: float, color, seg: int = 10, rings: int = 6, outline: bool = False) -> Mesh:
    return ellipsoid(r, r, r, color, seg, rings, outline)


def _area(poly) -> float:
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))


def extrude(outer: list[tuple[float, float]], depth: float, color, side=None, holes: list | None = None,
            outline: bool = True, side_outline: bool = False) -> Mesh:
    """A flat shape (in x / y) made `depth` deep along z, centred on z = 0. `side` colours the walls."""
    holes = holes or []
    outer = list(outer) if _area(outer) > 0 else list(outer)[::-1]  # outer counter-clockwise, holes clockwise
    holes = [list(h) if _area(h) < 0 else list(h)[::-1] for h in holes]
    col = rgba(color) if isinstance(color, str) else color
    scol = col if side is None else (rgba(side) if isinstance(side, str) else side)
    z0, z1 = -depth / 2, depth / 2
    m = Mesh()
    loops_front, loops_back = [], []
    for lp in [outer, *holes]:
        b = len(m.verts)
        n = len(lp)
        m.verts.extend((x, y, z1) for x, y in lp)
        m.verts.extend((x, y, z0) for x, y in lp)
        loops_front.append(list(range(b, b + n)))
        loops_back.append(list(range(b + n, b + 2 * n))[::-1])
        for i in range(n):
            j = (i + 1) % n
            m.faces.append(Face([b + n + i, b + n + j, b + j, b + i], scol, outline=side_outline))
    m.faces.append(Face(loops_front[0], col, loops_front[1:], outline))
    m.faces.append(Face(loops_back[0], scol, loops_back[1:], outline))
    return m


def star_points(points: int = 5, inner: float = 0.45, r: float = 1.0) -> list[tuple[float, float]]:
    out = []
    for i in range(points * 2):
        a = math.pi / 2 + math.pi * i / points
        rr = r if i % 2 == 0 else r * inner
        out.append((rr * math.cos(a), rr * math.sin(a)))
    return out


def heart_points(n: int = 28) -> list[tuple[float, float]]:
    out = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        out.append((x / 16, y / 16))
    # drop near-duplicate points (the dip at the top)
    clean = [out[0]]
    for p in out[1:]:
        if math.dist(p, clean[-1]) > 1e-3:
            clean.append(p)
    return clean


# ---- 3D text --------------------------------------------------------------------

def _pen_class():
    from fontTools.pens.basePen import BasePen

    class FlatPen(BasePen):
        """Glyph outlines as polygons; each curve becomes `steps` straight pieces (low-poly)."""

        def __init__(self, glyphset, steps: int, dx: float):
            super().__init__(glyphset)
            self.steps, self.dx = steps, dx
            self.contours: list[list[tuple[float, float]]] = []
            self.cur: list[tuple[float, float]] = []

        def _pt(self, p):
            return (p[0] + self.dx, p[1])

        def _moveTo(self, p):
            self.cur = [self._pt(p)]

        def _lineTo(self, p):
            self.cur.append(self._pt(p))

        def _curveToOne(self, p1, p2, p3):
            x0, y0 = self.cur[-1][0] - self.dx, self.cur[-1][1]
            for i in range(1, self.steps + 1):
                t = i / self.steps
                mt = 1 - t
                x = mt ** 3 * x0 + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t ** 3 * p3[0]
                y = mt ** 3 * y0 + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t ** 3 * p3[1]
                self.cur.append((x + self.dx, y))

        def _qCurveToOne(self, p1, p2):
            x0, y0 = self.cur[-1][0] - self.dx, self.cur[-1][1]
            for i in range(1, self.steps + 1):
                t = i / self.steps
                mt = 1 - t
                self.cur.append((mt * mt * x0 + 2 * mt * t * p1[0] + t * t * p2[0] + self.dx,
                                 mt * mt * y0 + 2 * mt * t * p1[1] + t * t * p2[1]))

        def _closePath(self):
            pts = []
            for p in self.cur:
                if not pts or math.dist(p, pts[-1]) > 1e-6:
                    pts.append(p)
            if len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-6:
                pts.pop()
            if len(pts) >= 3 and abs(_area(pts)) > 1e-6:
                self.contours.append(pts)
            self.cur = []

        _endPath = _closePath

    return FlatPen


def _inside(pt, poly) -> bool:
    x, y = pt
    inside = False
    for i in range(len(poly)):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % len(poly)]
        if (y0 > y) != (y1 > y) and x < x0 + (x1 - x0) * (y - y0) / (y1 - y0):
            inside = not inside
    return inside


def _shapes(contours: list) -> list[tuple[list, list[list]]]:
    """Contours -> (outer, holes) pairs by nesting: inside an odd number of others = a hole of the smallest one."""
    info = []
    for i, c in enumerate(contours):
        parents = [j for j, o in enumerate(contours) if j != i and _inside(c[0], o)]
        info.append(parents)
    outers = [i for i, ps in enumerate(info) if len(ps) % 2 == 0]
    shapes = {i: [] for i in outers}
    for i, ps in enumerate(info):
        if len(ps) % 2 == 1:
            owners = [j for j in ps if j in shapes]
            if owners:
                shapes[min(owners, key=lambda j: abs(_area(contours[j])))].append(contours[i])
    return [(contours[i], hs) for i, hs in shapes.items()]


@lru_cache(maxsize=64)
def _text_mesh_cached(text: str, font_path: str, depth: float, color: Color, side: Color, steps: int, spacing: float) -> Mesh:
    from fontTools.ttLib import TTFont

    font = TTFont(font_path, fontNumber=0, lazy=True)
    cmap = font.getBestCmap() or {}
    gs = font.getGlyphSet()
    upm = float(font["head"].unitsPerEm)
    Pen = _pen_class()
    lines = text.split("\n")
    m = Mesh()
    line_h = upm * 1.15
    for li, line in enumerate(lines):
        x = 0.0
        pen_shapes = []
        for ch in line:
            name = cmap.get(ord(ch), ".notdef")
            if name not in gs:
                name = ".notdef"
            pen = Pen(gs, steps, x)
            gs[name].draw(pen)
            pen_shapes.extend(_shapes(pen.contours))
            x += gs[name].width + spacing * upm
        x -= spacing * upm
        y_off = -li * line_h
        for outer, holes in pen_shapes:
            sh = lambda pts: [((px - x / 2) / upm, (py + y_off) / upm) for px, py in pts]  # noqa: E731  centred line
            m.add(extrude(sh(outer), depth, color, side, [sh(h) for h in holes], outline=True))
    if not m.faces:
        raise VeditError("3D text needs at least one visible letter")
    return m


def text_mesh(text: str, font: str | None, depth: float, color, side, smooth: int = 4, spacing: float = 0.02) -> Mesh:
    from . import fonts as font_lib
    from .text import find_font

    path = find_font(font) if font else None
    if not path:
        path = str(font_lib.path(font_lib.DEFAULT_FONT))
    if not path.lower().endswith((".ttf", ".otf", ".ttc")):
        raise VeditError(f"3D text needs a .ttf / .otf font, got {path}")
    try:
        import fontTools  # noqa: F401
    except ImportError as e:  # pragma: no cover - core dependency
        raise VeditError("3D text needs fontTools (uv sync)") from e
    col = rgba(color) if isinstance(color, str) else color
    sid = rgba(side) if isinstance(side, str) else side
    return _text_mesh_cached(text.strip("\n") or "?", path, float(depth), tuple(col), tuple(sid), max(1, min(12, int(smooth))),
                             float(spacing))

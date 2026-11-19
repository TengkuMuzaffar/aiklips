"""Shape geometry: the one definition of every shape kind, shared by the export (PIL, `overlay_style.render_shape`)
and the live preview (SVG, `static/shapes.js`, a port of this file: change both together; tests/test_shapes.py
checks they give the same points).

A shape fills its w x h box. Kinds drawn with primitives (rect with rounded corners, ellipse, line, arrow) keep
their PIL drawing; the rest are polygons from `polygon(kind, w, h, ...)` in box pixels. `ring` is an ellipse with an
elliptical hole (`inner` = hole size / outer size).
"""

from __future__ import annotations

import math

PRIMITIVE = ("rect", "ellipse", "line", "arrow", "ring")
SAMPLES = 96  # points on curved outlines (heart, blob)

# default size (canvas px) and look of each kind when added without one; line / arrow height = thickness
DEFAULTS: dict[str, dict] = {
    "rect": {"width": 600, "height": 220, "radius": 24, "fill": "#000000B0"},
    "ellipse": {"width": 360, "height": 360, "fill": None, "border_width": 10, "border_color": "#FFE600"},
    "triangle": {"width": 360, "height": 320, "fill": "#FFE600"},
    "diamond": {"width": 340, "height": 340, "fill": "#7C5CFF"},
    "polygon": {"width": 360, "height": 360, "fill": "#22D3EE", "sides": 6},
    "star": {"width": 380, "height": 380, "fill": "#FFC23B", "points": 5, "inner": 0.48},
    "ring": {"width": 380, "height": 380, "fill": "#FF3B6B", "inner": 0.72},
    "heart": {"width": 360, "height": 330, "fill": "#FF3B6B"},
    "bubble": {"width": 560, "height": 300, "fill": "#FFFFFF", "radius": 48},
    "blob": {"width": 420, "height": 400, "fill": "#34D399"},
    "line": {"width": 600, "height": 14, "fill": "#FFFFFF"},
    "arrow": {"width": 320, "height": 110, "fill": "#FFE600"},
    "path": {"width": 600, "height": 240, "fill": "#FFFFFF", "path": [[0.0, 0.8], [0.25, 0.2], [0.5, 0.7], [0.75, 0.15], [1.0, 0.5]]},
}

LABELS: dict[str, str] = {
    "rect": "Rectangle", "ellipse": "Circle", "triangle": "Triangle", "diamond": "Diamond", "polygon": "Hexagon",
    "star": "Star", "ring": "Ring", "heart": "Heart", "bubble": "Speech bubble", "blob": "Blob", "line": "Line",
    "arrow": "Arrow", "path": "Path",
}


def _fit(pts: list[tuple[float, float]], w: float, h: float) -> list[tuple[float, float]]:
    """Stretch an outline so its bounding box is exactly the w x h box (what you see is what the handles hold)."""
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    return [((x - x0) / ((x1 - x0) or 1) * w, (y - y0) / ((y1 - y0) or 1) * h) for x, y in pts]


def polygon(kind: str, w: float, h: float, sides: int = 6, points: int = 5, inner: float = 0.5) -> list[tuple[float, float]]:
    """Outline of a polygon shape kind, in box pixels (0..w, 0..h), clockwise from the top; it fills the box."""
    cx, cy, rx, ry = w / 2, h / 2, w / 2, h / 2
    if kind == "triangle":
        return [(cx, 0.0), (w, h), (0.0, h)]
    if kind == "diamond":
        return [(cx, 0.0), (w, cy), (cx, h), (0.0, cy)]
    if kind == "polygon":
        n = max(3, min(24, int(sides)))
        return _fit([(cx + rx * math.sin(2 * math.pi * i / n), cy - ry * math.cos(2 * math.pi * i / n)) for i in range(n)], w, h)
    if kind == "star":
        n = max(3, min(24, int(points)))
        k = max(0.05, min(0.95, float(inner)))
        out = []
        for i in range(2 * n):
            a, r = math.pi * i / n, (1.0 if i % 2 == 0 else k)
            out.append((cx + rx * r * math.sin(a), cy - ry * r * math.cos(a)))
        return _fit(out, w, h)
    if kind == "heart":  # the classic heart curve, stretched to fill the box
        return [(x * w, y * h) for x, y in _HEART]
    if kind == "blob":
        out = []
        for i in range(SAMPLES):
            a = 2 * math.pi * i / SAMPLES
            r = 0.82 + 0.1 * math.sin(3 * a + 1.0) + 0.08 * math.cos(5 * a + 2.0)
            out.append((cx + rx * r * math.sin(a), cy - ry * r * math.cos(a)))
        return _fit(out, w, h)
    raise ValueError(f"not a polygon shape: {kind}")


def _unit_heart() -> list[tuple[float, float]]:
    raw = []
    for i in range(SAMPLES):
        a = 2 * math.pi * i / SAMPLES
        raw.append((16 * math.sin(a) ** 3, -(13 * math.cos(a) - 5 * math.cos(2 * a) - 2 * math.cos(3 * a) - math.cos(4 * a))))
    x0, x1 = min(p[0] for p in raw), max(p[0] for p in raw)
    y0, y1 = min(p[1] for p in raw), max(p[1] for p in raw)
    return [((x - x0) / (x1 - x0), (y - y0) / (y1 - y0)) for x, y in raw]


_HEART = _unit_heart()
CORNER_STEPS = 8  # points per rounded corner of the speech bubble


def bubble(w: float, h: float, radius: float) -> list[tuple[float, float]]:
    """Speech bubble: a rounded body in the top 78 % with a tail at the lower left, as one clockwise outline."""
    bh = h * 0.78
    r = max(0.0, min(radius, w / 2, bh / 2))
    out: list[tuple[float, float]] = []

    def corner(cx: float, cy: float, a0: float) -> None:
        for i in range(CORNER_STEPS + 1):
            a = math.radians(a0 + 90 * i / CORNER_STEPS)
            out.append((cx + r * math.cos(a), cy + r * math.sin(a)))

    corner(r, r, 180)  # top left
    corner(w - r, r, 270)  # top right
    corner(w - r, bh - r, 0)  # bottom right
    out += [(w * 0.36, bh), (w * 0.12, h), (w * 0.18, bh)]  # the tail
    corner(r, bh - r, 90)  # bottom left
    return out


def outline(kind: str, w: float, h: float, sides: int = 6, points: int = 5, inner: float = 0.5,
            radius: float = 0.0) -> list[tuple[float, float]]:
    """The outline of any polygon kind (not rect / ellipse / line / arrow / ring), in box pixels."""
    if kind == "bubble":
        return bubble(w, h, radius)
    return polygon(kind, w, h, sides, points, inner)


# ---- motion: paths, draw-on and morphing (ops/motion.py) -------------------------------------------------------

def path_points(path: list[list[float]] | None, w: float, h: float) -> list[tuple[float, float]]:
    """A path shape's points (0..1 fractions of its box) in box pixels."""
    return [(float(x) * w, float(y) * h) for x, y in (path or [[0, 0.5], [1, 0.5]])]


def _ellipse(w: float, h: float, n: int = SAMPLES) -> list[tuple[float, float]]:
    """Clockwise from the top, like the polygon kinds."""
    return [(w / 2 + w / 2 * math.sin(2 * math.pi * i / n), h / 2 - h / 2 * math.cos(2 * math.pi * i / n)) for i in range(n)]


def _rounded_rect(w: float, h: float, r: float) -> list[tuple[float, float]]:
    r = max(0.0, min(r, w / 2, h / 2))
    out: list[tuple[float, float]] = [(w / 2, 0.0)]
    for cx, cy, a0 in ((w - r, r, 270), (w - r, h - r, 0), (r, h - r, 90), (r, r, 180)):
        for i in range(CORNER_STEPS + 1):
            a = math.radians(a0 + 90 * i / CORNER_STEPS)
            out.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return out


def any_outline(kind: str, w: float, h: float, sides: int = 6, points: int = 5, inner: float = 0.5, radius: float = 0.0,
                path: list[list[float]] | None = None) -> tuple[list[tuple[float, float]], bool]:
    """The outline of ANY shape kind as (points, closed), in box pixels, clockwise from the top centre where the
    kind has one. Lines and open paths are open; a ring is its outer edge; an arrow its silhouette."""
    if kind == "path":
        return path_points(path, w, h), False
    if kind == "line":
        return [(0.0, h / 2), (w, h / 2)], False
    if kind in ("ellipse", "ring"):
        return _ellipse(w, h), True
    if kind == "rect":
        return _rounded_rect(w, h, radius), True
    if kind == "arrow":
        head, shaft, mid = min(w * 0.45, h), h * 0.38, h / 2
        return [(0.0, mid - shaft / 2), (w - head, mid - shaft / 2), (w - head, 0.0), (w, mid), (w - head, h),
                (w - head, mid + shaft / 2), (0.0, mid + shaft / 2)], True
    return outline(kind, w, h, sides, points, inner, radius), True


def _length(pts: list[tuple[float, float]], closed: bool) -> list[float]:
    """Cumulative length at each point (closed: one more entry, back to the start)."""
    seq = pts + [pts[0]] if closed else pts
    acc = [0.0]
    for (x0, y0), (x1, y1) in zip(seq, seq[1:]):
        acc.append(acc[-1] + math.hypot(x1 - x0, y1 - y0))
    return acc


def trim(pts: list[tuple[float, float]], closed: bool, frac: float) -> list[tuple[float, float]]:
    """The first `frac` (0..1) of an outline by length, as an open polyline: what a draw-on shows."""
    if not pts:
        return []
    seq = pts + [pts[0]] if closed else list(pts)
    acc = _length(pts, closed)
    want = acc[-1] * max(0.0, min(1.0, frac))
    out = [seq[0]]
    for i in range(1, len(seq)):
        if acc[i] <= want:
            out.append(seq[i])
            if acc[i] == want:
                break
            continue
        seg = acc[i] - acc[i - 1]
        k = (want - acc[i - 1]) / seg if seg else 0.0
        (x0, y0), (x1, y1) = seq[i - 1], seq[i]
        out.append((x0 + (x1 - x0) * k, y0 + (y1 - y0) * k))
        break
    return out


def resample(pts: list[tuple[float, float]], closed: bool, n: int) -> list[tuple[float, float]]:
    """n points equally spaced by length along an outline, starting at its first point."""
    acc = _length(pts, closed)
    seq = pts + [pts[0]] if closed else pts
    total = acc[-1] or 1.0
    out, j = [], 1
    for i in range(n):
        d = total * i / (n if closed else max(1, n - 1))
        while j < len(acc) - 1 and acc[j] < d:
            j += 1
        seg = acc[j] - acc[j - 1]
        k = (d - acc[j - 1]) / seg if seg else 0.0
        (x0, y0), (x1, y1) = seq[j - 1], seq[j]
        out.append((x0 + (x1 - x0) * k, y0 + (y1 - y0) * k))
    return out


MORPH_POINTS = 120


def _start_at_top(pts: list[tuple[float, float]], w: float) -> list[tuple[float, float]]:
    """Rotate a closed outline to start at its point nearest the top centre, so two morphing shapes line up."""
    i = min(range(len(pts)), key=lambda k: (pts[k][1], abs(pts[k][0] - w / 2)))
    return pts[i:] + pts[:i]


def morph(a: tuple[list[tuple[float, float]], bool], b: tuple[list[tuple[float, float]], bool], p: float, w: float
          ) -> tuple[list[tuple[float, float]], bool]:
    """Outline between shape a (p = 0) and shape b (p = 1): both resampled to the same number of points from the top
    centre, then blended point by point. Closed if either end is closed."""
    pa = resample(_start_at_top(a[0], w) if a[1] else a[0], a[1], MORPH_POINTS)
    pb = resample(_start_at_top(b[0], w) if b[1] else b[0], b[1], MORPH_POINTS)
    p = max(0.0, min(1.0, p))
    return [(x0 + (x1 - x0) * p, y0 + (y1 - y0) * p) for (x0, y0), (x1, y1) in zip(pa, pb)], (a[1] or b[1])

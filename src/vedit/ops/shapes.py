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
}

LABELS: dict[str, str] = {
    "rect": "Rectangle", "ellipse": "Circle", "triangle": "Triangle", "diamond": "Diamond", "polygon": "Hexagon",
    "star": "Star", "ring": "Ring", "heart": "Heart", "bubble": "Speech bubble", "blob": "Blob", "line": "Line",
    "arrow": "Arrow",
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

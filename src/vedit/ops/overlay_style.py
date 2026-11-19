"""Pixels of image / video / shape overlays: the frame styling (green-screen key, circle or rounded cut-out, inside
border) and shape drawing. Used by the export renderer (render/layers.py) and the editor's live sprites, so both
look the same.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw

from ..project import Canvas, Layer
from . import shapes
from .text import rgba

SS = 3  # supersampling for smooth mask / border edges


def video_width(layer: Layer, canvas: Canvas) -> int:
    """Width of a video layer on the canvas (px): its `width`, else 45% of the canvas (a picture-in-picture)."""
    return int(layer.width or round(canvas.width * 0.45))


def chroma_alpha(rgb: np.ndarray, key: str, similarity: float) -> np.ndarray:
    """0..255 alpha that removes `key` (distance-based, with a soft 0.08 edge). rgb: HxWx3 uint8."""
    k = np.array(rgba(key)[:3], np.float32)
    d = np.sqrt(((rgb.astype(np.float32) - k) ** 2).sum(-1)) / (255 * np.sqrt(3))
    return (np.clip((d - similarity) / 0.08, 0, 1) * 255).astype(np.uint8)


@lru_cache(maxsize=64)
def _mask(w: int, h: int, circle: bool, radius: int) -> Image.Image:
    m = Image.new("L", (w * SS, h * SS), 0)
    d = ImageDraw.Draw(m)
    if circle:
        d.ellipse((0, 0, w * SS - 1, h * SS - 1), fill=255)
    else:
        d.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=radius * SS, fill=255)
    return m.resize((w, h), Image.LANCZOS)


@lru_cache(maxsize=64)
def _border(w: int, h: int, circle: bool, radius: int, width: int, color: str) -> Image.Image:
    b = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(b)
    box, col, bw = (0, 0, w * SS - 1, h * SS - 1), rgba(color), width * SS
    if circle:
        d.ellipse(box, outline=col, width=bw)
    else:
        d.rounded_rectangle(box, radius=radius * SS, outline=col, width=bw)
    return b.resize((w, h), Image.LANCZOS)


def needs_styling(layer: Layer) -> bool:
    return bool(layer.chroma or layer.mask == "circle" or layer.radius > 0 or layer.border_width > 0)


def square_crop(img: Image.Image) -> Image.Image:
    s = min(img.width, img.height)
    x, y = (img.width - s) // 2, (img.height - s) // 2
    return img.crop((x, y, x + s, y + s))


def style_frame(img: Image.Image, layer: Layer, scale: float) -> Image.Image:
    """Apply the layer's frame styling to one RGBA (or RGB) picture already at its on-canvas size * scale."""
    if not needs_styling(layer):
        return img.convert("RGBA") if img.mode != "RGBA" else img
    circle = layer.mask == "circle"
    if circle:
        img = square_crop(img)
    img = img.convert("RGBA")
    w, h = img.size
    if layer.chroma:
        arr = np.asarray(img)
        a = np.minimum(arr[..., 3], chroma_alpha(arr[..., :3], layer.chroma, layer.chroma_similarity))
        img = Image.fromarray(np.dstack([arr[..., :3], a]), "RGBA")
    r = max(0, round(layer.radius * scale))
    if circle or r > 0:
        a = np.asarray(img.getchannel("A"), np.uint16) * np.asarray(_mask(w, h, circle, r), np.uint16) // 255
        img.putalpha(Image.fromarray(a.astype(np.uint8), "L"))
    bw = max(0, round(layer.border_width * scale))
    if bw:
        img = Image.alpha_composite(img, _border(w, h, circle, r, bw, layer.border_color))
    return img


def _gradient(w: int, h: int, colors: list[str], angle: float) -> Image.Image:
    """A linear gradient filling w x h: colours spread evenly along the direction `angle` (0 = left -> right, 90 =
    top -> bottom), the line through the box centre spanning its full projection (like CSS linear-gradient)."""
    cols = np.array([rgba(c) for c in colors], np.float32)
    a = np.radians(angle)
    dx, dy = np.cos(a), np.sin(a)
    half = (abs(w * dx) + abs(h * dy)) / 2 or 1.0
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    p = np.clip(((xs + 0.5 - w / 2) * dx + (ys + 0.5 - h / 2) * dy) / (2 * half) + 0.5, 0, 1) * (len(cols) - 1)
    i = np.minimum(p.astype(np.int32), len(cols) - 2) if len(cols) > 1 else np.zeros_like(p, np.int32)
    f = (p - i)[..., None] if len(cols) > 1 else np.zeros((h, w, 1), np.float32)
    nxt = np.minimum(i + 1, len(cols) - 1)
    out = cols[i] * (1 - f) + cols[nxt] * f
    return Image.fromarray(np.round(out).astype(np.uint8), "RGBA")


def _draw(d: ImageDraw.ImageDraw, layer: Layer, W: int, H: int, fill, out, bw: int, scale: float) -> None:
    """Draw the shape filling a W x H (supersampled) box. Polygon kinds' borders are centred lines here; the
    caller clips them to the inside with `_inside`."""
    box = (0, 0, W - 1, H - 1)
    kind = layer.shape
    if kind == "rect":
        d.rounded_rectangle(box, radius=round(layer.radius * scale) * SS, fill=fill, outline=out, width=bw)
    elif kind == "ellipse":
        d.ellipse(box, fill=fill, outline=out, width=bw)
    elif kind == "line":
        d.rounded_rectangle(box, radius=H // 2, fill=fill or out)
    elif kind == "arrow":  # shaft + head, the full width
        col = fill or out or (255, 255, 255, 255)
        head = min(W * 0.45, H * 1.0)
        shaft_h = H * 0.38
        mid = H / 2
        d.rectangle((0, mid - shaft_h / 2, W - head + 2, mid + shaft_h / 2), fill=col)
        d.polygon([(W - head, 0), (W - 1, mid), (W - head, H - 1)], fill=col)
    elif kind == "ring":
        k = max(0.05, min(0.95, layer.inner))
        hole = ((W - W * k) / 2, (H - H * k) / 2, (W + W * k) / 2 - 1, (H + H * k) / 2 - 1)
        if fill:
            d.ellipse(box, fill=fill)
            d.ellipse(hole, fill=(0, 0, 0, 0))
        if out:
            d.ellipse(box, outline=out, width=bw)
            d.ellipse((hole[0] - bw, hole[1] - bw, hole[2] + bw, hole[3] + bw), outline=out, width=bw)
    else:
        pts = outline_points(layer, W, H, scale)
        if fill:
            d.polygon(pts, fill=fill)
        if out:
            d.line(pts + [pts[0], pts[1]], fill=out, width=bw * 2, joint="curve")


def _motion_shape(layer: Layer, scale: float, lw, lh, w: int, h: int, col, gradient, bw: int, out, draw: float,
                  morph: float) -> Image.Image:
    """Paths, shapes part-way through a draw-on (only the first `draw` of the outline, as a stroke) and shapes
    morphing into `morph_to`. Strokes are `border_width` thick (or a default), in the border colour (or the fill);
    a line keeps its own thickness."""
    W, H = w * SS, h * SS
    if layer.shape == "line":
        sw = H
    elif bw:
        sw = bw
    else:
        sw = round(max(6.0, min(lw or 400, lh or 400) * 0.035) * scale) * SS
    sw = max(SS, min(sw, W, H))
    stroke_col = out or (rgba(col) if col else rgba(gradient[0]) if gradient else (255, 255, 255, 255))
    iw, ih = max(1.0, W - sw), max(1.0, H - sw)  # the outline sits inside the box by half a stroke
    kw = dict(sides=layer.sides, points=layer.points, inner=layer.inner, radius=layer.radius * scale * SS)
    a = shapes.any_outline(layer.shape, iw, ih, path=layer.path, **kw)
    if layer.shape == "path" and layer.closed:
        a = (a[0], True)
    pts, closed = a
    if layer.morph_to and morph > 0.0005:
        pts, closed = shapes.morph(a, shapes.any_outline(layer.morph_to, iw, ih, path=None, **kw), morph, iw)
    pts = [(x + sw / 2, y + sw / 2) for x, y in pts]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def stroke(line: list[tuple[float, float]], color, width: int) -> None:
        if len(line) < 2:
            return
        d.line(line, fill=color, width=width, joint="curve")
        r = width / 2
        for x, y in (line[0], line[-1]):  # round caps
            d.ellipse((x - r, y - r, x + r, y + r), fill=color)

    if draw < 0.9995:
        stroke(shapes.trim(pts, closed, draw), stroke_col, sw)
    elif closed and layer.shape != "line":
        if gradient:
            m = Image.new("L", (W, H), 0)
            ImageDraw.Draw(m).polygon(pts, fill=255)
            g = _gradient(W, H, gradient, layer.gradient_angle)
            g.putalpha(Image.fromarray((np.asarray(g.getchannel("A"), np.uint16) * np.asarray(m, np.uint16) // 255).astype(np.uint8), "L"))
            img = g
            d = ImageDraw.Draw(img)
        elif col:
            d.polygon(pts, fill=rgba(col))
        if out:
            stroke(pts + [pts[0], pts[1]], out, bw)
    else:
        stroke(pts, stroke_col, sw)
    return img.resize((w, h), Image.LANCZOS)


def outline_points(layer: Layer, W: float, H: float, scale: float = 1.0) -> list[tuple[float, float]]:
    """A polygon kind's outline in a W x H supersampled box."""
    return shapes.outline(layer.shape, W, H, layer.sides, layer.points, layer.inner, layer.radius * scale * SS)


def _inside(layer: Layer, W: int, H: int, scale: float) -> Image.Image:
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).polygon(outline_points(layer, W, H, scale), fill=255)
    return m


def render_shape(layer: Layer, scale: float = 1.0, width: float | None = None, height: float | None = None,
                 fill: str | None | bool = False, draw: float = 1.0, morph: float = 0.0) -> Image.Image:
    """A shape layer's sprite: rect (rounded with radius), ellipse, line, arrow (pointing right; rotate the layer),
    ring and the polygon kinds of ops/shapes.py, filled with `fill` or the `fill_gradient`. width / height / fill
    override the layer's (animated values at one moment); fill=False = the layer's own."""
    lw = width if width is not None else layer.width
    lh = height if height is not None else layer.height
    w = max(2, round((lw or 400) * scale))
    h = max(2, round((lh or {"line": 16, "arrow": 90}.get(layer.shape, 400)) * scale))
    W, H = w * SS, h * SS
    col = layer.fill if fill is False else fill
    bw = round(layer.border_width * scale) * SS
    out = rgba(layer.border_color) if bw else None
    polygonal = layer.shape not in shapes.PRIMITIVE
    gradient = layer.fill_gradient if layer.fill_gradient and len(layer.fill_gradient) >= 2 else None
    if layer.shape == "path" or (morph > 0.0005 and layer.morph_to) or draw < 0.9995:
        return _motion_shape(layer, scale, lw, lh, w, h, col, gradient, bw, out, draw, morph)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if gradient:  # the shape's own outline as an alpha mask over the gradient, then the border on top
        m = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        _draw(ImageDraw.Draw(m), layer, W, H, (255, 255, 255, 255), None, 0, scale)
        g = _gradient(W, H, gradient, layer.gradient_angle)
        g.putalpha(Image.fromarray((np.asarray(g.getchannel("A"), np.uint16)
                                    * np.asarray(m.getchannel("A"), np.uint16) // 255).astype(np.uint8), "L"))
        img = g
        if out:
            b = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            _draw(ImageDraw.Draw(b), layer.model_copy(update={"shape": layer.shape if layer.shape != "line" else "rect"}),
                  W, H, None, out, bw, scale)
            if polygonal:
                b.putalpha(Image.fromarray((np.asarray(b.getchannel("A"), np.uint16)
                                            * np.asarray(_inside(layer, W, H, scale), np.uint16) // 255).astype(np.uint8), "L"))
            img = Image.alpha_composite(img, b)
    else:
        _draw(ImageDraw.Draw(img), layer, W, H, rgba(col) if col else None, out, bw, scale)
        if polygonal and out:  # keep the border inside the outline (as the SVG preview does)
            fill_only = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            if col:
                _draw(ImageDraw.Draw(fill_only), layer, W, H, rgba(col), None, 0, scale)
            b = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            _draw(ImageDraw.Draw(b), layer, W, H, None, out, bw, scale)
            b.putalpha(Image.fromarray((np.asarray(b.getchannel("A"), np.uint16)
                                        * np.asarray(_inside(layer, W, H, scale), np.uint16) // 255).astype(np.uint8), "L"))
            img = Image.alpha_composite(fill_only, b)
    return img.resize((w, h), Image.LANCZOS)

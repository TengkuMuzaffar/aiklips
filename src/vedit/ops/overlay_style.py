"""Pixels of image / video / shape overlays: the frame styling (green-screen key, circle or rounded cut-out, inside
border) and shape drawing. Used by the export renderer (render/layers.py) and the editor's live sprites, so both
look the same.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw

from ..project import Canvas, Layer
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


def render_shape(layer: Layer, scale: float = 1.0) -> Image.Image:
    """A shape layer's sprite: rect (rounded with radius), ellipse, line or arrow (pointing right; rotate the layer)."""
    w = max(2, round((layer.width or 400) * scale))
    h = max(2, round((layer.height or {"line": 16, "arrow": 90}.get(layer.shape, 400)) * scale))
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill = rgba(layer.fill) if layer.fill else None
    bw = round(layer.border_width * scale) * SS
    out = rgba(layer.border_color) if bw else None
    box = (0, 0, w * SS - 1, h * SS - 1)
    if layer.shape == "rect":
        d.rounded_rectangle(box, radius=round(layer.radius * scale) * SS, fill=fill, outline=out, width=bw)
    elif layer.shape == "ellipse":
        d.ellipse(box, fill=fill, outline=out, width=bw)
    elif layer.shape == "line":
        d.rounded_rectangle(box, radius=h * SS // 2, fill=fill or out)
    else:  # arrow: shaft + head, the full width
        col = fill or out or (255, 255, 255, 255)
        head = min(w * SS * 0.45, h * SS * 1.0)
        shaft_h = h * SS * 0.38
        mid = h * SS / 2
        d.rectangle((0, mid - shaft_h / 2, w * SS - head + 2, mid + shaft_h / 2), fill=col)
        d.polygon([(w * SS - head, 0), (w * SS - 1, mid), (w * SS - head, h * SS - 1)], fill=col)
    return img.resize((w, h), Image.LANCZOS)

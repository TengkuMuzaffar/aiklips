"""Chart layers: bar / horizontal bar / line / donut, grown by the `draw` progress (0..1, preset `draw_in`).

Bars rise one after another (`stagger`), a line draws itself point by point, a donut sweeps round, and every value
rolls up with its bar. Drawn by Pillow for the export and the live preview alike (sprites.py).
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from ..project import Chart, Counter, Layer, VeditError
from .animate import ease
from .motion import counter_text
from .text import load_font, rgba

SS = 2  # supersampling
DEFAULT_SIZE = (900, 640)


def item_progress(c: Chart, i: int, n: int, draw: float) -> float:
    """How far item i of n has grown (0..1) when the chart as a whole is `draw` of the way."""
    if n <= 1:
        return max(0.0, min(1.0, draw))
    s = max(0.0, min(1.0, c.stagger)) / n  # stagger 1: each starts when the one before is done; 0: all together
    window = max(1e-6, 1 - s * (n - 1))
    return max(0.0, min(1.0, (draw - i * s) / window))


def _value_text(c: Chart, v: float, p: float) -> str:
    return counter_text(Counter(**{"from": 0.0}, to=v, decimals=c.decimals, prefix=c.prefix, suffix=c.suffix), p)


def parse_items(spec: str) -> list[dict]:
    """"Mon=12, Tue=30, Wed=18" (or "12,30,18", or "Mon=12:#FF0000") -> chart items."""
    out = []
    for part in [x.strip() for x in spec.replace(";", ",").split(",") if x.strip()]:
        color = None
        if ":" in part:
            part, color = part.rsplit(":", 1)
        label, _, val = part.rpartition("=")
        try:
            out.append({"label": label.strip(), "value": float(val), "color": color})
        except ValueError:
            raise VeditError(f"Chart data looks like 'Mon=12, Tue=30' (label=value), not {part!r}") from None
    if not out:
        raise VeditError("A chart needs at least one value")
    return out


def render_chart(layer: Layer, scale: float = 1.0, draw: float = 1.0, width: float | None = None,
                 height: float | None = None) -> Image.Image:
    c = layer.chart or Chart()
    w = max(40, round((width or layer.width or DEFAULT_SIZE[0]) * scale))
    h = max(40, round((height or layer.height or DEFAULT_SIZE[1]) * scale))
    W, H = w * SS, h * SS
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    items = c.items
    n = len(items)
    if not n:
        return img.resize((w, h), Image.LANCZOS)
    fs = max(8, round(c.text_size * scale * SS))
    font = load_font(c.font, fs)
    tc = rgba(c.text_color)
    vmax = c.max if c.max else max(max(abs(it.value) for it in items), 1e-9)
    col = lambda i: rgba(items[i].color or c.colors[i % len(c.colors)] if c.colors else "#22D3EE")  # noqa: E731
    prog = [ease("ease_out", item_progress(c, i, n, draw)) for i in range(n)]

    def text(xy, s, anchor):
        d.text(xy, s, font=font, fill=tc, anchor=anchor)

    if c.kind in ("bar", "hbar"):
        horiz = c.kind == "hbar"
        lab = fs * 1.6 if c.show_labels else 0
        val = fs * 1.5 if c.show_values else 0
        if horiz:
            lab_w = max((d.textlength(it.label, font=font) for it in items), default=0) + fs * 0.6 if c.show_labels else 0
            val_w = max((d.textlength(_value_text(c, it.value, 1), font=font) for it in items), default=0) + fs * 0.6 if c.show_values else 0
            x0, x1, slot = lab_w, W - val_w, H / n
            for i, it in enumerate(items):
                bar = slot * (1 - c.gap)
                y = i * slot + (slot - bar) / 2
                length = (x1 - x0) * max(0.0, it.value) / vmax * prog[i]
                if length > 1:
                    d.rounded_rectangle((x0, y, x0 + length, y + bar), radius=min(c.radius * scale * SS, bar / 2, length / 2), fill=col(i))
                if c.show_labels:
                    text((x0 - fs * 0.3, y + bar / 2), it.label, "rm")
                if c.show_values and prog[i] > 0:
                    text((x0 + length + fs * 0.3, y + bar / 2), _value_text(c, it.value, prog[i]), "lm")
        else:
            y_base, top, slot = H - lab, val, W / n
            for i, it in enumerate(items):
                bar = slot * (1 - c.gap)
                x = i * slot + (slot - bar) / 2
                hgt = (y_base - top) * max(0.0, it.value) / vmax * prog[i]
                if hgt > 1:
                    d.rounded_rectangle((x, y_base - hgt, x + bar, y_base), radius=min(c.radius * scale * SS, bar / 2, hgt / 2), fill=col(i))
                if c.show_labels:
                    text((x + bar / 2, y_base + fs * 0.25), it.label, "mt")
                if c.show_values and prog[i] > 0:
                    text((x + bar / 2, y_base - hgt - fs * 0.2), _value_text(c, it.value, prog[i]), "mb")
    elif c.kind == "line":
        from . import shapes

        lab = fs * 1.6 if c.show_labels else 0
        top = fs * 1.5 if c.show_values else fs * 0.5
        lw = max(SS, round(c.line_width * scale * SS))
        edge = [d.textlength(it.label, font=font) for it in (items[0], items[-1])] if c.show_labels else [0, 0]
        if c.show_values:
            edge = [max(e, d.textlength(_value_text(c, it.value, 1), font=font)) for e, it in zip(edge, (items[0], items[-1]))]
        pad = max(lw * 1.2, max(edge) / 2 + fs * 0.2)  # room for the first / last label
        y_base = H - lab - lw
        xs = [pad + (W - 2 * pad) * (i / max(1, n - 1)) for i in range(n)] if n > 1 else [W / 2]
        pts = [(xs[i], y_base - (y_base - top) * max(0.0, it.value) / vmax) for i, it in enumerate(items)]
        p = ease("ease_in_out", max(0.0, min(1.0, draw)))
        shown = shapes.trim(pts, False, p) if n > 1 else pts
        color = col(0)
        if len(shown) >= 2:
            d.line(shown, fill=color, width=lw, joint="curve")
        acc = shapes._length(pts, False)
        for i, (x, y) in enumerate(pts):
            reached = acc[i] <= acc[-1] * p + 1e-6 if n > 1 else p > 0
            if c.show_labels:
                text((x, H - lab + fs * 0.25), items[i].label, "mt")
            if reached and (c.show_values or c.show_labels):  # a bare curve (growth curve) has no beads on it
                r = lw * 1.1
                d.ellipse((x - r, y - r, x + r, y + r), fill=color)
                if c.show_values:
                    text((x, y - r - fs * 0.25), _value_text(c, items[i].value, 1.0), "mb")
        if len(shown) >= 1:
            x, y = shown[-1]
            r = lw * 0.5
            d.ellipse((x - r, y - r, x + r, y + r), fill=color)
    elif c.kind == "donut":
        total = sum(max(0.0, it.value) for it in items) or 1.0
        size = min(W, H) - SS * 4
        box = ((W - size) / 2, (H - size) / 2, (W + size) / 2, (H + size) / 2)
        sweep = 360 * ease("ease_in_out", max(0.0, min(1.0, draw)))
        a = -90.0
        for i, it in enumerate(items):
            span = 360 * max(0.0, it.value) / total
            b = min(a + span, -90 + sweep)
            if b > a:
                d.pieslice(box, a, b, fill=col(i))
            a += span
        ring = c.line_width * scale * SS * 6  # line_width 10 -> a ring 60 px thick at scale 1
        if c.line_width > 0 and ring < size / 2:
            hole = (box[0] + ring, box[1] + ring, box[2] - ring, box[3] - ring)
            d.ellipse(hole, fill=(0, 0, 0, 0))
        if c.show_values:
            text((W / 2, H / 2), _value_text(c, total, ease("ease_out", max(0.0, min(1.0, draw)))), "mm")
    return img.resize((w, h), Image.LANCZOS)

"""Motion-graphics building blocks on our own tracks (no browser): counters, kinetic text, draw-on / morph fields.

Everything here is drawn by Python for both the export (render/layers.py) and the editor's live preview
(preview_server/sprites.py), so they always match. The animated values are ordinary keyframe props
(ops/animate.py): `draw`, `morph`, `count` (0..1), plus each kinetic text piece's own timing.

- counter: a text layer whose text counts `from` -> `to` as `count` goes 0 -> 1 (preset `count_up`)
- kinetic text: `split` word / letter / line; each piece `split_anim`s in `stagger` s after the previous one
- draw-on: `draw` < 1 shows only that much of a shape's outline as a stroke (preset `draw_in`); `path` shapes
- morph: `morph` 0 -> 1 turns a shape into its `morph_to` kind (keyframes, or preset `morph_loop`)
- charts: ops/chart.py
"""

from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image

from ..project import Chart, Counter, Layer, SHAPE_KINDS, TextStyle, VeditError
from .animate import ease

SPLITS = ("none", "word", "letter", "line")
SPLIT_ANIMS = ("rise", "drop", "fade", "pop", "mask_up", "slide", "type")


# ---- counters ------------------------------------------------------------------------------------------------

def counter_text(c: Counter, prog: float) -> str:
    """The counter's text at progress `prog` (0..1). Same digits as animate.js counterText."""
    d = max(0, min(6, int(c.decimals)))
    v = c.from_ + (c.to - c.from_) * prog
    f = 10**d
    n = math.floor(abs(v) * f + 0.5)
    ip = str(n // f)
    if c.separator:
        ip = f"{int(ip):,}".replace(",", c.separator)
    frac = "." + str(n % f).zfill(d) if d else ""
    return f"{c.prefix}{'-' if v < 0 and n > 0 else ''}{ip}{frac}{c.suffix}"


def shown_text(layer: Layer, count: float = 1.0) -> str:
    """What a text layer shows: its counter's value, else its text."""
    return counter_text(layer.counter, count) if layer.counter else (layer.text or "")


# ---- kinetic text -------------------------------------------------------------------------------------------

class Kinetic:
    """A text layer's full sprite cut into pieces (word / letter / line) that tile it, and how each piece moves in.
    The pieces partition the sprite (boundaries halfway between neighbours), so every pixel belongs to one piece."""

    def __init__(self, text: str, style: TextStyle, scale: float, layer: Layer):
        from .text import render_text

        spans: list = []
        self.base = render_text(text, style, scale, spans=spans)
        meta = next(s for s in spans if s.get("meta"))
        words = [s for s in spans if not s.get("meta")]
        self.layer = layer
        W, H, shear = self.base.width, self.base.height, meta["shear"]
        lines: dict[int, list] = {}
        for w in words:
            lines.setdefault(w["li"], []).append(w)
        lis = sorted(lines)
        bands = {}
        for k, li in enumerate(lis):  # rows: halfway between this line's ink box and its neighbours'
            top = min(w["top"] for w in lines[li])
            bot = max(w["bottom"] for w in lines[li])
            bands[li] = [top, bot]
        for k, li in enumerate(lis):
            bands[li][0] = 0 if k == 0 else (bands[lis[k - 1]][1] + bands[li][0]) / 2
        for k, li in enumerate(lis):
            bands[li][1] = H if k == len(lis) - 1 else bands[lis[k + 1]][0]
        pieces: list[tuple[float, float, float, float]] = []
        for li in lis:
            y0, y1 = bands[li]
            if layer.split == "line":
                cols = [(0.0, float(W))]
            else:
                xs = (sorted(lines[li], key=lambda w: w["x0"]) if layer.split == "word" else
                      sorted([{"x0": a, "x1": b} for w in lines[li] for a, b in w["letters"]], key=lambda u: u["x0"]))
                cols = []
                for i, u in enumerate(xs):
                    a = 0.0 if i == 0 else (xs[i - 1]["x1"] + u["x0"]) / 2
                    b = float(W) if i == len(xs) - 1 else (u["x1"] + xs[i + 1]["x0"]) / 2
                    cols.append((a, b))
            for a, b in cols:
                if shear:  # italic: the slant shifts rows right by shear * (H - y)
                    a, b = a + shear * (H - y1), b + shear * (H - y0)
                pieces.append((a, y0, b, y1))
        self.pieces = [tuple(round(v) for v in r) for r in pieces]
        self.bands_h = max([r[3] - r[1] for r in self.pieces] or [H])
        moves = layer.split_anim in ("rise", "drop", "slide")
        self.pad = round(self.bands_h * 0.7) if moves else round(self.bands_h * 0.15) if layer.split_anim == "pop" else 0
        self.end = entry_end(layer, len(self.pieces))
        padded = Image.new("RGBA", (W + 2 * self.pad, H + 2 * self.pad), (0, 0, 0, 0))
        padded.paste(self.base, (self.pad, self.pad))
        self.full = padded

    def frame(self, t: float) -> Image.Image:
        """The sprite at `t` seconds after the layer starts (same size at every t, centred the same)."""
        if t >= self.end:
            return self.full
        L, P = self.layer, self.pad
        out = Image.new("RGBA", self.full.size, (0, 0, 0, 0))
        for i, (x0, y0, x1, y1) in enumerate(self.pieces):
            if x1 <= x0 or y1 <= y0:
                continue
            p = (t - i * L.stagger) / max(0.01, L.split_dur)
            if p <= 0:
                continue
            p = min(1.0, p)
            e = ease(L.split_ease, p)
            piece = self.base.crop((x0, y0, x1, y1))
            dx = dy = 0.0
            alpha = max(0.0, min(1.0, e))
            bh = y1 - y0
            kind = L.split_anim
            if kind == "rise":
                dy = (1 - e) * bh * 0.6
            elif kind == "drop":
                dy = -(1 - e) * bh * 0.6
            elif kind == "slide":
                dx = (1 - e) * bh * 0.6
            elif kind == "type":
                alpha = 1.0
            elif kind == "pop":
                s = max(0.0, e)
                alpha = min(1.0, p * 3)
                if s <= 0.001:
                    continue
                pw, ph = max(1, round(piece.width * s)), max(1, round(piece.height * s))
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                piece = piece.resize((pw, ph), Image.BICUBIC)
                x0, y0 = round(cx - pw / 2), round(cy - ph / 2)
            elif kind == "mask_up":  # rises out of its own row, cut off at the row's bottom
                alpha = 1.0
                off = round((1 - max(0.0, min(1.0, e))) * bh)
                if off >= bh:
                    continue
                piece = piece.crop((0, 0, piece.width, bh - off))
                y0 += off
            if alpha <= 0.001:
                continue
            if alpha < 0.999:
                a = piece.getchannel("A").point(lambda v, o=alpha: int(v * o))
                piece.putalpha(a)
            out.alpha_composite(piece, (round(x0 + dx) + P, round(y0 + dy) + P))
        return out


def entry_end(layer: Layer, n: int) -> float:
    """Seconds after the layer's start when its last piece has finished moving in."""
    return max(0, n - 1) * max(0.0, layer.stagger) + max(0.01, layer.split_dur)


# ---- setting the fields (CLI and editor share this) ------------------------------------------------------------

def set_fields(layer: Layer, a: dict) -> list[str]:
    """Kinetic text / counter / draw-on / morph / path / chart fields from a dict of raw values. Returns the names
    changed; raises VeditError on a bad value."""
    changed = []
    if "split" in a and a["split"] is not None:
        if a["split"] not in SPLITS:
            raise VeditError(f"split must be one of: {', '.join(SPLITS)}")
        layer.split = a["split"]
        changed.append("split")
    if a.get("split_anim") is not None:
        if a["split_anim"] not in SPLIT_ANIMS:
            raise VeditError(f"split_anim must be one of: {', '.join(SPLIT_ANIMS)}")
        layer.split_anim = a["split_anim"]
        changed.append("split_anim")
    for k in ("stagger", "split_dur"):
        if a.get(k) not in (None, ""):
            v = float(a[k])
            if v < 0 or v > 10:
                raise VeditError(f"{k} must be between 0 and 10 seconds")
            setattr(layer, k, v)
            changed.append(k)
    if a.get("split_ease") not in (None, ""):
        ease(str(a["split_ease"]), 0.5)  # raises on an unknown easing
        layer.split_ease = str(a["split_ease"])
        changed.append("split_ease")
    if "counter" in a:
        c = a["counter"]
        if c in (None, False, ""):
            layer.counter = None
        else:
            if layer.type != "text":
                raise VeditError("Only text layers can count")
            base = layer.counter.model_dump(by_alias=True) if layer.counter else {}
            base.update({k: v for k, v in dict(c).items() if v is not None})
            layer.counter = Counter.model_validate(base)
        changed.append("counter")
    if a.get("step_fps") not in (None, ""):
        v = int(float(a["step_fps"]))
        if v and not 4 <= v <= 30:
            raise VeditError("step_fps is 4-30 (12 = 'on twos' stop motion), or 0 for smooth motion")
        layer.step_fps = v or None
        changed.append("step_fps")
    if "morph_to" in a:
        m = a["morph_to"] or None
        if m is not None and (m not in SHAPE_KINDS or m == "path"):
            raise VeditError(f"morph_to must be a shape kind: {', '.join(k for k in SHAPE_KINDS if k != 'path')}")
        layer.morph_to = m
        changed.append("morph_to")
    if "path" in a and a["path"] is not None:
        layer.path = parse_path(a["path"])
        changed.append("path")
    if a.get("closed") is not None:
        layer.closed = bool(a["closed"])
        changed.append("closed")
    if "chart" in a and a["chart"] is not None:
        if layer.type != "chart":
            raise VeditError("Only chart layers have chart data")
        base = layer.chart.model_dump() if layer.chart else {}
        base.update(dict(a["chart"]))
        layer.chart = Chart.model_validate(base)
        changed.append("chart")
    return changed


def parse_path(v) -> list[list[float]]:
    """Path points from [[x, y], ...] or "x,y x,y ..." (0..1 fractions of the shape's box)."""
    if isinstance(v, str):
        try:
            pts = [[float(c) for c in pair.split(",")] for pair in v.replace(";", " ").split()]
        except ValueError:
            raise VeditError("Path points look like '0,0 0.5,1 1,0' (x,y pairs, 0..1 of the box)") from None
    else:
        pts = [[float(c) for c in pt] for pt in v]
    if len(pts) < 2 or any(len(pt) != 2 for pt in pts):
        raise VeditError("A path needs at least two x,y points")
    if any(not (-0.5 <= c <= 1.5) for pt in pts for c in pt):
        raise VeditError("Path points are fractions of the shape's box (0..1)")
    return pts


def kinetic_end(layer_data: dict, scale: float = 0.5) -> float:
    """When a kinetic text layer (as the browser sends it) has finished moving in: the live preview stops asking for
    new sprites after that."""
    import json

    keep = {k: layer_data.get(k) for k in ("id", "type", "text", "style", "split", "split_anim", "stagger", "split_dur", "split_ease")}
    return _kinetic_end(json.dumps({k: v for k, v in keep.items() if v is not None}, sort_keys=True), round(scale, 3))


@lru_cache(maxsize=256)
def _kinetic_end(blob: str, scale: float) -> float:
    import json

    L = Layer.model_validate(json.loads(blob))
    return Kinetic(L.text or "", L.style, scale, L).end


# ---- adding (CLI and editor share these) -----------------------------------------------------------------------

def add_counter(p, to: float, start: float = 0.0, end: float | None = None, x="50%", y="40%", duration: float = 1.2,
                ease_name: str = "expo_out", style: dict | None = None, **counter) -> Layer:
    """A text layer that counts up to `to` over `duration` seconds (preset count_up)."""
    from ..project import Preset
    from .overlays import _end

    c = Counter.model_validate({"to": to, **{k: v for k, v in counter.items() if v is not None}})
    st = TextStyle(**{**TextStyle(size=160).model_dump(), **(style or {})})
    l = Layer(id=p.new_id("t"), type="text", text=counter_text(c, 1.0), counter=c, start=start,
              end=end if end is not None else _end(p, start, max(3.0, duration + 1.5)), x=x, y=y, style=st,
              presets=[Preset(name="count_up", duration=duration, params={"ease": ease_name})])
    ease(ease_name, 0.5)
    p.layers.append(l)
    return l


def add_chart(p, kind: str, items: list[dict], start: float = 0.0, end: float | None = None, x="50%", y="50%",
              width: int | None = None, height: int | None = None, duration: float = 1.2, **chart) -> Layer:
    """A chart layer that grows in over `duration` seconds (preset draw_in)."""
    from ..project import Preset
    from .chart import DEFAULT_SIZE
    from .overlays import _end

    c = Chart.model_validate({"kind": kind, "items": items, **{k: v for k, v in chart.items() if v is not None}})
    w = width or min(DEFAULT_SIZE[0], round(p.canvas.width * 0.8))
    l = Layer(id=p.new_id("ch"), type="chart", chart=c, start=start, end=end if end is not None else _end(p, start, max(3.0, duration + 2)),
              x=x, y=y, width=w, height=height or round(w * DEFAULT_SIZE[1] / DEFAULT_SIZE[0]),
              presets=[Preset(name="draw_in", duration=duration, params={"ease": "ease_out"})] if duration > 0 else [])
    p.layers.append(l)
    return l

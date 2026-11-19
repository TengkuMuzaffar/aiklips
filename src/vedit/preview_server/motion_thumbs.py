"""Previews for the editor's Elements > Motion cards, drawn by the export's own renderer (render/layers.py sprites +
ops/animate.py motion): a still of the finished element and a short looping animated WebP of it moving in."""

from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image

from ..ops.animate import props_at
from ..project import Canvas, Chart, ChartItem, Counter, Layer, Preset, Project, TextStyle

W, H = 640, 400  # the card's 16:10 thumbnail, drawn at 2x
CV = Canvas(width=W, height=H, fps=15)

_BG = {"iso": "#F4F2F8", "kinetic": "#151515", "words": "#1E1B4B", "counter": "#0B1020", "bar": "#0B1A14", "line": "#1A0B1C", "donut": "#1A140B",
       "draw": "#2A1020", "morph": "#1D1440"}


def _card(key: str) -> tuple[Layer, float]:
    """The card's element on the thumbnail canvas, and how long its animation runs."""
    if key == "kinetic":
        return Layer(id="t", type="text", text="BIG IDEA", split="letter", split_anim="mask_up", stagger=0.05, split_ease="snap",
                     style=TextStyle(size=110, stroke_width=0)), 1.4
    if key == "words":
        return Layer(id="t", type="text", text="Make every second count", split="word", split_anim="rise", stagger=0.12,
                     style=TextStyle(size=62, stroke_width=0, max_width=560)), 1.6
    if key == "counter":
        return Layer(id="t", type="text", text="1,000+", counter=Counter(to=1000, suffix="+"), style=TextStyle(size=130, stroke_width=0),
                     presets=[Preset(name="count_up", duration=1.3)]), 1.8
    if key in ("bar", "line", "donut"):
        data = {"bar": [("Mon", 12), ("Tue", 30), ("Wed", 18), ("Thu", 42)], "line": [("J", 4), ("F", 9), ("M", 7), ("A", 15), ("M", 22)],
                "donut": [("Ads", 45), ("Organic", 35), ("Ref", 20)]}[key]
        return Layer(id="c", type="chart", width=520 if key != "donut" else 330, height=330,
                     chart=Chart(kind=key, items=[ChartItem(label=a, value=b) for a, b in data], text_size=30, line_width=8),
                     presets=[Preset(name="draw_in", duration=1.3)]), 1.8
    if key == "draw":
        return Layer(id="s", type="shape", shape="path", width=520, height=200, border_width=14, border_color="#F472B6",
                     path=[[0.0, 0.8], [0.25, 0.2], [0.5, 0.7], [0.75, 0.15], [1.0, 0.5]],
                     presets=[Preset(name="draw_in", duration=1.2)]), 1.6
    if key == "iso":
        from ..ops.iso import world

        return Layer(id="w", type="iso", width=380, iso=world("cafe"), presets=[Preset(name="draw_in", duration=1.6, params={"ease": "linear"})]), 2.0
    if key == "morph":
        return Layer(id="s", type="shape", shape="ellipse", width=260, height=260, fill="#A78BFA", morph_to="star",
                     presets=[Preset(name="morph_loop", params={"period": 2.4})]), 2.4
    raise KeyError(key)


KEYS = tuple(_BG)


def _frame(layer: Layer, src, t: float, dur: float, bg: str) -> Image.Image:
    img = Image.new("RGBA", (W, H), bg)
    pr = props_at(layer, t, max(dur, 3.0), CV)
    sprite, extra = src.get(t, pr["reveal"], pr)
    if sprite is None or pr["opacity"] <= 0.001:
        return img
    s = pr["scale"] * extra
    if abs(s - 1) > 1e-3:
        sprite = sprite.resize((max(1, round(sprite.width * s)), max(1, round(sprite.height * s))), Image.BICUBIC)
    img.alpha_composite(sprite, (round(W / 2 - sprite.width / 2), round(H / 2 - sprite.height / 2)))
    return img


@lru_cache(maxsize=32)
def thumb(key: str, animated: bool = False) -> bytes:
    """PNG of the finished element, or (animated) a looping WebP of it moving in, then holding."""
    from pathlib import Path

    from ..render.layers import _SpriteSource

    layer, dur = _card(key)
    layer.x, layer.y = "50%", "50%"
    src = _SpriteSource(Project(canvas=CV), Path("."), layer, 1.0)
    bg = _BG[key]
    buf = io.BytesIO()
    if not animated:
        at = 1.2 if key == "morph" else dur  # the morph's still: half way round its loop = the star
        _frame(layer, src, at, dur, bg).convert("RGB").save(buf, "PNG", optimize=True)
        return buf.getvalue()
    fps = 15
    frames = [_frame(layer, src, i / fps, dur, bg).convert("RGB").resize((W // 2, H // 2), Image.LANCZOS)
              for i in range(round(dur * fps))]
    hold = [frames[-1]] * (fps // 2 if key != "morph" else 0)  # rest on the result before it loops
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:] + hold, duration=round(1000 / fps), loop=0, quality=80)
    return buf.getvalue()


# ---- signature effect previews (Effects tab): the effect applied to an empty 16:10 canvas ----------------------

MW, MH = 1280, 800


def _compose(p: Project, sources: dict, t: float) -> Image.Image:
    """Every layer of `p` at time t over a dark card (same transform steps as render/layers.py)."""
    img = Image.new("RGBA", (MW, MH), "#101418")
    for l in p.layers:
        end = l.end if l.end is not None else p.duration
        if not (l.start <= t < end):
            continue
        pr = props_at(l, t - l.start, end - l.start, p.canvas)
        sprite, extra = sources[l.id].get(t, pr["reveal"], pr)
        s = pr["scale"] * extra
        sx, sy = s * pr["sx"], s * pr["sy"]
        if sprite is None or pr["opacity"] <= 0.001 or sx <= 0.001 or sy <= 0.001:
            continue
        if abs(sx - 1) > 1e-3 or abs(sy - 1) > 1e-3:
            sprite = sprite.resize((max(1, round(sprite.width * sx)), max(1, round(sprite.height * sy))), Image.BICUBIC)
        if abs(pr["rotation"]) > 0.01:
            sprite = sprite.rotate(-pr["rotation"], resample=Image.BICUBIC, expand=True)
        if pr["opacity"] < 0.999:
            sprite = sprite.copy()
            sprite.putalpha(sprite.getchannel("A").point(lambda v, o=pr["opacity"]: int(v * o)))
        img.alpha_composite(sprite, (round(pr["x"] - sprite.width / 2), round(pr["y"] - sprite.height / 2)))
    return img


def _fit_box(img: Image.Image) -> tuple[int, int, int, int]:
    """The 16:10 part of the card around what was drawn (with a margin), so small graphics fill the preview."""
    import numpy as np

    a = np.asarray(img.convert("RGB"), np.int16)
    ys, xs = np.nonzero(np.abs(a - a[0, 0]).sum(axis=2) > 24)
    if not len(xs):
        return 0, 0, MW, MH
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    w = max(x1 - x0, (y1 - y0) * MW / MH) * 1.25
    w = min(MW, max(w, MW * 0.45))
    h = w * MH / MW
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left, top = min(max(0, cx - w / 2), MW - w), min(max(0, cy - h / 2), MH - h)
    return round(left), round(top), round(left + w), round(top + h)


_STILL = {"cursor_click": 1.05}  # the moment that shows the effect best (the click's ripple)
_SAMPLE = {"chat_takeover": "them: did you see this?; me: no what; them: we went viral!", "cursor_click": "35%,70% > 62%,42%",
           "hud_lock": "TARGET ACQUIRED", "paper_title": "Hello there; a little story", "iso_world": "island",
           "growth_curve": "+340%", "checklist": "Plan; Shoot; Edit", "checklist_pop": "Fast; Cheap; Easy",
           "headline_storm": "AI took my job; Is editing dead?; Everyone is switching; The new way to edit"}


@lru_cache(maxsize=16)
def move_thumb(name: str, animated: bool = False) -> bytes:
    from pathlib import Path

    from ..ops import moves
    from ..render.layers import _SpriteSource

    if name not in moves.PREVIEWS:
        raise KeyError(name)
    p = Project(canvas=Canvas(width=MW, height=MH, fps=15))
    moves.apply(p, name, at=0.0, text=_SAMPLE[name], dur=4.0, box="30,30,40,40" if name == "hud_lock" else None)
    sources = {l.id: _SpriteSource(p, Path("."), l, 1.0) for l in p.layers}
    end = min(3.3, max(l.start for l in p.layers) + 1.2)
    still = _STILL.get(name, min(end + 0.6, min(l.end or 4.0 for l in p.layers) - 0.3))  # before anything fades out
    last = _compose(p, sources, still)
    box = _fit_box(last)
    buf = io.BytesIO()
    if not animated:
        last.crop(box).convert("RGB").resize((MW // 2, MH // 2), Image.LANCZOS).save(buf, "PNG", optimize=True)
        return buf.getvalue()
    fps = 15
    frames = [_compose(p, sources, i / fps).crop(box).convert("RGB").resize((MW // 4, MH // 4), Image.LANCZOS)
              for i in range(round(min(end + 0.8, 3.9) * fps))]
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:] + [frames[-1]] * 8, duration=round(1000 / fps), loop=0, quality=80)
    return buf.getvalue()

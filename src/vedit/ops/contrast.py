"""Can a person read this text against what is really behind it? Measured by code, never judged by the AI.

An AI editor sees colours as hex strings, so it happily puts light grey text on a white card: the words are "there"
for it, invisible for a viewer. This module finds the colour(s) behind every text / captions layer and measures the
WCAG 2 contrast ratio between the letters and that background.

Two ways to find the background:
  structural (exact, no render): walk down the layers drawn under the text at that moment: the text's own box
      (`--bg`), shapes that cover the whole text, the video track (gaps / Design background clips = canvas colour),
      the canvas. Gives up (None) on footage, images, partial overlaps, blend modes and adjustment layers.
  pixels (needs the rendered preview): sample the frame right around the letters (the glyph mask, grown past the
      outline and shadow, is cut out), so footage, gradients and images count exactly as the viewer sees them.

    issues(p, pdir)                    structural only: what `vedit` warns about right after an edit
    check(p, pdir, preview=None)       every text layer, pixels where the structure can't tell (audit)

Thresholds (WCAG 2): 4.5:1 for normal text, 3:1 for large text; under 3:1 nobody reads it comfortably. An outline
that stands out from the background (and from the fill) rescues low-contrast fill, as it does on video.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..project import Layer, Project, VeditError, parse_pos

MIN_NORMAL = 4.5
MIN_LARGE = 3.0
LARGE_PX = 48  # letter size (at a 1080-wide canvas) from which text counts as large
PIXEL_PERCENTILE = 20  # pixel backgrounds: the contrast most of the background around the letters reaches


# ---- colour maths --------------------------------------------------------------------------------------------

def rgba(color: str) -> tuple[float, float, float, float]:
    """'#RGB' / '#RRGGBB' / '#RRGGBBAA' / CSS name -> (r, g, b, a) in 0..1."""
    from PIL import ImageColor

    try:
        c = ImageColor.getcolor(color, "RGBA")
    except (ValueError, AttributeError):
        raise VeditError(f"Invalid colour '{color}'") from None
    return c[0] / 255, c[1] / 255, c[2] / 255, c[3] / 255


def _lin(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def luminance(rgb) -> np.ndarray:
    """Relative luminance (WCAG) of an (..., 3) array of 0..1 sRGB colours."""
    a = _lin(np.asarray(rgb, dtype=float))
    return a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722


def ratio(a, b) -> np.ndarray:
    la, lb = luminance(a), luminance(b)
    return (np.maximum(la, lb) + 0.05) / (np.minimum(la, lb) + 0.05)


def over(top: tuple[float, float, float], alpha: float, bottom: np.ndarray) -> np.ndarray:
    return np.asarray(top, dtype=float) * alpha + np.asarray(bottom, dtype=float) * (1 - alpha)


def hexc(rgb) -> str:
    r, g, b = (int(round(max(0.0, min(1.0, float(v))) * 255)) for v in rgb[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


# ---- what is behind ------------------------------------------------------------------------------------------

@dataclass
class Behind:
    colors: np.ndarray  # (N, 3) 0..1: one colour per possible background (structural) or per sampled pixel
    what: str  # human description: "shape s3 (#FFFFFF)", "the footage (pixels)"
    pixels: bool = False
    layers: list[str] = field(default_factory=list)


def _visible(l: Layer, t: float, total: float) -> bool:
    end = total if l.end is None else l.end
    return not l.hidden and l.start <= t < end


def _box(p: Project, pdir: Path | None, l: Layer):
    from .safezone import layer_box

    try:
        return layer_box(p, pdir, l)
    except (VeditError, OSError):
        return None


def _covers(outer, inner, tol: float = 2.0) -> bool:
    return outer[0] <= inner[0] + tol and outer[1] <= inner[1] + tol and outer[2] >= inner[2] - tol and outer[3] >= inner[3] - tol


def _meets(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _canvas_rgb(color: str) -> tuple[float, float, float]:
    try:
        return rgba(color)[:3]
    except VeditError:
        return (0.0, 0.0, 0.0)


def structural(p: Project, pdir: Path | None, l: Layer, box, t: float) -> Behind | None:
    """The colours under `l`'s box at timeline time t from the project alone, or None when only pixels can tell."""
    from ..audit import _plain

    total = p.duration
    layers = p.layers
    # drawing order (bottom -> top): tracks under the video, the video track, its own layers, the tracks over it
    split = next((i for i, o in enumerate(layers) if o.on_video or (o.track or 0) >= p.main_z), len(layers))
    order: list = list(layers[:split]) + ["VIDEO"] + list(layers[split:])
    me = order.index(l)
    pending: list[tuple[np.ndarray, float, str]] = []  # translucent things on the way down (top first)
    base: np.ndarray | None = None
    base_what = ""
    for item in reversed(order[:me]):
        if item == "VIDEO":
            if not p.clips or t >= p.clips_end:
                continue
            from ..render.frame import clip_at

            c = p.clips[clip_at(p, t)[0]]
            if c.gap:
                continue
            if _plain(p, c):  # Design background clip: the colour is in its file name (.gap-<hex>.mp4)
                name = Path(p.sources[c.source].path).stem
                base, base_what = np.array([_canvas_rgb("#" + name.split("-", 1)[1][:6])]), "the background"
                break
            return None  # footage: pixels only
        o: Layer = item
        if not _visible(o, t, total) or o.type in ("text", "captions"):
            continue
        b = _box(p, pdir, o)
        if b is None:
            if o.type == "adjust":
                return None
            continue
        if not _meets(b, box):
            continue
        if o.type != "shape" or o.blend != "normal" or any(k.fill for k in o.keyframes) or o.morph_to:
            return None  # images, video, charts, glass, adjustment layers, blends, animated fills: pixels only
        if o.fill is None and not o.fill_gradient:
            continue  # outline only: its inside shows what's under it
        if o.shape != "rect" or not _covers(b, box) or o.rotation % 90:
            return None  # partly behind the text, or a shape whose corners / curves we don't model
        cols = [rgba(c) for c in (o.fill_gradient or [o.fill])]
        alpha = min(c[3] for c in cols) * o.opacity
        rgbs = np.array([c[:3] for c in cols])
        label = f"shape {o.id} ({', '.join(hexc(c) for c in rgbs)})"
        if alpha >= 0.99:
            base, base_what = rgbs, label
            break
        pending.append((rgbs, alpha, label))
    if base is None:
        base, base_what = np.array([_canvas_rgb(p.canvas.background)]), f"the canvas ({p.canvas.background.upper()[:7]})"
    for rgbs, alpha, label in reversed(pending):  # composite bottom -> top
        base = np.array([over(c, alpha, b) for c in rgbs for b in base])
        base_what = f"{label} over {base_what}"
    return Behind(base, base_what)


def _glyph_ring(p: Project, l: Layer, sample: str, scale: float, frame: np.ndarray) -> np.ndarray | None:
    """Pixels of `frame` (rendered at `scale` of the canvas) right around the letters of `l`: the sprite's rectangle
    plus a margin, minus the letters grown past their outline and shadow."""
    from PIL import Image, ImageFilter

    from .text import render_text

    style = l.style.model_copy(update={"bg_color": None})  # the box is accounted for separately
    img = render_text(sample, style, scale=l.scale * scale)
    W, H = p.canvas.width, p.canvas.height
    cx, cy = parse_pos(l.x, W) * scale, parse_pos(l.y, H) * scale
    x0, y0 = int(round(cx - img.width / 2)), int(round(cy - img.height / 2))
    grow = max(3, int(round((l.style.stroke_width + 8) * l.scale * scale))) | 1
    mask = img.getchannel("A").point(lambda a: 255 if a > 8 else 0).filter(ImageFilter.MaxFilter(min(grow * 2 + 1, 31)))
    margin = grow * 2
    fh, fw = frame.shape[:2]
    full = Image.new("L", (fw, fh), 0)
    full.paste(mask, (x0, y0))
    m = np.asarray(full) > 0
    region = np.zeros_like(m)
    bx0, by0 = max(0, x0 - margin), max(0, y0 - margin)
    bx1, by1 = min(fw, x0 + img.width + margin), min(fh, y0 + img.height + margin)
    if bx1 <= bx0 or by1 <= by0:
        return None
    region[by0:by1, bx0:bx1] = True
    ring = region & ~m
    if ring.sum() < 40:
        return None
    return frame[ring][:, :3].astype(float) / 255


def _sample_text(l: Layer) -> str:
    from .motion import shown_text

    return shown_text(l)


# ---- the check -----------------------------------------------------------------------------------------------

@dataclass
class Result:
    layer: str
    severity: str | None  # must-fix | should-fix | None (fine)
    ratio: float
    need: float
    fill: str
    behind: str
    behind_hex: str
    what: str
    t: float
    rescued_by_outline: bool = False
    suggest: str | None = None


def _times(l: Layer, total: float) -> list[float]:
    end = total if l.end is None else min(l.end, total)
    if end <= l.start:
        return []
    d = end - l.start
    return sorted({round(l.start + d * f, 3) for f in (0.35, 0.75)})


def _fills(l: Layer) -> list[tuple[str, tuple[float, float, float, float]]]:
    out = [(c, rgba(c)) for c in (l.style.gradient or [l.style.color])]
    if l.type == "captions" and l.style.highlight_color:
        out.append((l.style.highlight_color, rgba(l.style.highlight_color)))
    return out


def evaluate(p: Project, l: Layer, behind: Behind, t: float) -> Result:
    """Contrast of `l`'s letters on `behind`; the worst fill colour counts."""
    st = l.style
    bg = rgba(st.bg_color) if st.bg_color else None
    under = behind.colors
    if bg is not None and bg[3] > 0.02:  # the text's own box sits between the letters and the background
        under = np.array([over(bg[:3], bg[3], b) for b in under])
    big = st.size * l.scale * (1080 / max(1, p.canvas.width)) >= LARGE_PX
    need = MIN_LARGE if big else MIN_NORMAL

    def score(rs: np.ndarray) -> float:
        return float(np.percentile(rs, PIXEL_PERCENTILE)) if behind.pixels else float(rs.min())

    # an outline thick enough to see, and distinct from the fill, separates the letters wherever the fill doesn't:
    # per background colour / pixel, the better of fill and outline counts (white + black outline reads on anything)
    stroke = None
    if st.stroke_width and st.stroke_width * l.scale >= max(2.0, st.size * l.scale * 0.035):
        s = rgba(st.stroke_color)
        stroke = (s, ratio(np.array([over(s[:3], s[3] * l.opacity, b) for b in under]), under))
    worst, worst_fill, rescued = None, None, False
    for name, f in _fills(l):
        a = f[3] * l.opacity
        fg = np.array([over(f[:3], a, b) for b in under])
        rs = ratio(fg, under)
        r = score(rs)
        if stroke is not None and float(ratio(np.array(f[:3]), np.array(stroke[0][:3]))) >= MIN_LARGE:
            r2 = score(np.maximum(rs, stroke[1]))
            if r2 > r:
                r, rescue = r2, True
            else:
                rescue = False
        else:
            rescue = False
        if worst is None or r < worst:
            worst, worst_fill, rescued = r, name, rescue
    assert worst is not None
    sev = None
    if worst < MIN_LARGE:
        sev = "must-fix"
    elif worst < need:
        sev = "should-fix"
    typical = np.median(under, axis=0)
    suggest = None
    if sev:
        dark, light = np.array([0.067, 0.067, 0.067]), np.array([1.0, 1.0, 1.0])
        pick = dark if score(ratio(np.broadcast_to(dark, under.shape), under)) >= score(ratio(np.broadcast_to(light, under.shape), under)) else light
        suggest = f"vedit text edit {l.id} --color {hexc(pick)}"
        if behind.pixels:  # busy footage: a box or outline keeps it readable whatever passes behind
            suggest += f"  (or --bg '#000000B0' / --stroke '#000000' --stroke-width 8)"
    return Result(l.id, sev, round(worst, 2), need, worst_fill or st.color, behind.what, hexc(typical),
                  (l.text or " ".join(w.text for w in l.words[:6]) or "")[:40], t, rescued, suggest)


def _text_layers(p: Project) -> list[Layer]:
    return [l for l in p.layers if l.type in ("text", "captions") and not l.hidden and (l.text or l.words)]


def issues(p: Project, pdir: Path | None) -> list[Result]:
    """Structural check only (fast, no render): text layers that fail where the background is known exactly."""
    from ..audit import text_box

    out = []
    W, H = p.canvas.width, p.canvas.height
    for l in _text_layers(p):
        try:
            b = text_box(p, l)
        except (VeditError, OSError):
            continue
        if b is None:
            continue
        box = (b[0] * W, b[1] * H, b[2] * W, b[3] * H)
        worst = None
        for t in _times(l, p.duration):
            try:
                bh = structural(p, pdir, l, box, t)
                if bh is None:
                    if l.style.bg_color and rgba(l.style.bg_color)[3] >= 0.95:  # an opaque box: that's the background
                        bh = Behind(np.array([rgba(l.style.bg_color)[:3]]), f"its own box ({l.style.bg_color})")
                    else:
                        continue
                r = evaluate(p, l, bh, t)
            except VeditError:
                continue
            if worst is None or r.ratio < worst.ratio:
                worst = r
        if worst is not None:
            out.append(worst)
    return out


def check(p: Project, pdir: Path, preview: Path | None = None, scale: float = 0.5) -> list[Result]:
    """Every text layer: structural where possible, else pixels of the rendered `preview` (at `scale`)."""
    from .. import media
    from ..project import state_dir

    results = {r.layer: r for r in issues(p, pdir)}
    if preview is None or not Path(preview).exists():
        return list(results.values())
    from PIL import Image

    tmp = state_dir(pdir) / "tmp"
    tmp.mkdir(exist_ok=True)
    frames: dict[float, np.ndarray] = {}
    W, H = p.canvas.width, p.canvas.height
    from ..audit import text_box

    for l in _text_layers(p):
        if l.id in results or l.type == "captions" or l.rotation % 360 or l.sx != 1 or l.sy != 1:
            continue
        if any(k.x is not None or k.y is not None or k.scale is not None for k in l.keyframes):
            continue  # it moves: the at-rest glyph mask wouldn't line up
        try:
            b = text_box(p, l)
        except (VeditError, OSError):
            continue
        if b is None:
            continue
        box = (b[0] * W, b[1] * H, b[2] * W, b[3] * H)
        worst = None
        for t in _times(l, p.duration):
            bh = structural(p, pdir, l, box, t)
            if bh is None:
                if t not in frames:
                    out = tmp / f"contrast-{t:.3f}.png"
                    try:
                        media.extract_frame(Path(preview), min(t, max(0.0, p.duration - 0.05)), out)
                        frames[t] = np.asarray(Image.open(out).convert("RGB"))
                    except (VeditError, OSError):
                        frames[t] = None  # type: ignore[assignment]
                    finally:
                        out.unlink(missing_ok=True)
                fr = frames[t]
                if fr is None:
                    continue
                px = _glyph_ring(p, l, _sample_text(l), fr.shape[1] / W, fr)
                if px is None:
                    continue
                bh = Behind(px, "what's behind it on screen", pixels=True)
            try:
                r = evaluate(p, l, bh, t)
            except VeditError:
                continue
            if worst is None or r.ratio < worst.ratio:
                worst = r
        if worst is not None:
            results[l.id] = worst
    return list(results.values())


def message(r: Result) -> str:
    return (f"{r.layer} \"{r.what}\" is hard to read: {r.fill.upper()} letters on {r.behind} have contrast "
            f"{r.ratio:.1f}:1 (needs {r.need:g}:1).")

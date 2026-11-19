"""Keyframe animation engine + presets (pure Python, no AI).

A layer's look at time t is: base props -> keyframes (interpolated with easing)
-> presets (in / out / loop modifiers). Claude can write keyframes as JSON for
any custom motion, or stack presets like ["pop_in", "bob", "fade_out"].
"""

from __future__ import annotations

import math
from typing import Callable

from ..project import Canvas, Keyframe, Layer, Preset, VeditError, parse_pos

PROPS = ("x", "y", "scale", "rotation", "opacity", "reveal", "w", "h", "sx", "sy", "draw", "morph", "count")
PROGRESS = ("draw", "morph", "count")  # 0..1 build-up values (ops/motion.py), clamped
COLOR_PROPS = ("fill",)


# ---- easing -------------------------------------------------------------------

def _bounce_out(p: float) -> float:
    n1, d1 = 7.5625, 2.75
    if p < 1 / d1:
        return n1 * p * p
    if p < 2 / d1:
        p -= 1.5 / d1
        return n1 * p * p + 0.75
    if p < 2.5 / d1:
        p -= 2.25 / d1
        return n1 * p * p + 0.9375
    p -= 2.625 / d1
    return n1 * p * p + 0.984375


def _elastic_out(p: float) -> float:
    if p in (0.0, 1.0):
        return p
    return 2 ** (-10 * p) * math.sin((p * 10 - 0.75) * (2 * math.pi) / 3) + 1


def _back_out(p: float, s: float = 1.70158) -> float:
    p -= 1
    return p * p * ((s + 1) * p + s) + 1


def _bezier(x1: float, y1: float, x2: float, y2: float) -> Callable[[float], float]:
    """CSS cubic-bezier(x1, y1, x2, y2) easing. Same steps as animate.js (Newton, then bisection), so the live
    preview and the export agree to the last digit."""
    def coord(t: float, a: float, b: float) -> float:
        return ((1 - 3 * b + 3 * a) * t + (3 * b - 6 * a)) * t * t + 3 * a * t

    def slope(t: float, a: float, b: float) -> float:
        return 3 * (1 - 3 * b + 3 * a) * t * t + 2 * (3 * b - 6 * a) * t + 3 * a

    def fn(p: float) -> float:
        if p <= 0 or p >= 1:
            return p
        t = p
        for _ in range(8):
            d = slope(t, x1, x2)
            if abs(d) < 1e-7:
                break
            t2 = t - (coord(t, x1, x2) - p) / d
            if t2 < 0 or t2 > 1:
                break
            t = t2
        if abs(coord(t, x1, x2) - p) > 1e-7:
            lo, hi = 0.0, 1.0
            t = p
            for _ in range(40):
                x = coord(t, x1, x2)
                if abs(x - p) < 1e-7:
                    break
                if x < p:
                    lo = t
                else:
                    hi = t
                t = (lo + hi) / 2
        return coord(t, y1, y2)

    return fn


# curves named after what motion designers ask for, as numbers (style rules: design/styles.py)
NAMED_BEZIERS = {
    "snap": (0.7, 0.0, 0.2, 1.0),  # hard start, settles fast: Swiss / kinetic type
    "smooth": (0.4, 0.0, 0.2, 1.0),  # material standard
    "expo_out": (0.16, 1.0, 0.3, 1.0),  # very fast out of the gate, long glide
    "circ_out": (0.0, 0.55, 0.45, 1.0),
    "anticipate": (0.36, -0.4, 0.5, 1.0),  # pulls back a little before it goes
    "overshoot": (0.34, 1.56, 0.64, 1.0),  # past the target, back again
}


def parse_bezier(name: str) -> tuple[float, float, float, float] | None:
    """(x1, y1, x2, y2) of "cubic-bezier(a,b,c,d)" / "bezier:a,b,c,d", else None."""
    s = name.strip().lower().replace(" ", "")
    for pre in ("cubic-bezier(", "bezier("):
        if s.startswith(pre) and s.endswith(")"):
            s = s[len(pre):-1]
            break
    else:
        if s.startswith("bezier:"):
            s = s[7:]
        else:
            return None
    try:
        v = [float(x) for x in s.split(",")]
    except ValueError:
        return None
    if len(v) != 4 or not (0 <= v[0] <= 1 and 0 <= v[2] <= 1):
        return None
    return v[0], v[1], v[2], v[3]


EASINGS: dict[str, Callable[[float], float]] = {
    "linear": lambda p: p,
    "ease_in": lambda p: p * p * p,
    "ease_out": lambda p: 1 - (1 - p) ** 3,
    "ease_in_out": lambda p: 4 * p**3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2,
    "back_out": _back_out,
    "back_in": lambda p: p * p * (2.70158 * p - 1.70158),
    "bounce_out": _bounce_out,
    "elastic_out": _elastic_out,
    "step": lambda p: 0.0 if p < 1 else 1.0,
    **{k: _bezier(*v) for k, v in NAMED_BEZIERS.items()},
}
_BEZ_CACHE: dict[str, Callable[[float], float]] = {}


def ease(name: str, p: float) -> float:
    p = max(0.0, min(1.0, p))
    fn = EASINGS.get(name) or _BEZ_CACHE.get(name)
    if fn is None:
        b = parse_bezier(name)
        if b is None:
            raise VeditError(f"Unknown easing '{name}'. Options: {', '.join(EASINGS)}, or cubic-bezier(x1,y1,x2,y2)")
        fn = _BEZ_CACHE[name] = _bezier(*b)
    return fn(p)


# ---- presets ------------------------------------------------------------------
# Each preset: (phase, default_duration, fn(props, p, t, params, canvas))
#   phase "in":   p goes 0 -> 1 over the first `duration` seconds
#   phase "out":  p goes 1 -> 0 over the last `duration` seconds (same fn, reversed)
#   phase "loop": p is unused; fn uses t (seconds since layer start)

def _fade(pr, p, t, k, c):
    pr["opacity"] *= ease("ease_out", p)


def _pop(pr, p, t, k, c):
    pr["scale"] *= max(0.0, ease("back_out", p))
    pr["opacity"] *= min(1.0, p * 3)


def _slide(pr, p, t, k, c):
    d = float(k.get("distance", 220))
    direction = k.get("direction", "up")
    off = (1 - ease("ease_out", p)) * d
    if direction == "up":
        pr["y"] += off
    elif direction == "down":
        pr["y"] -= off
    elif direction == "left":
        pr["x"] += off
    elif direction == "right":
        pr["x"] -= off
    else:
        raise VeditError("slide direction must be up/down/left/right")
    pr["opacity"] *= ease("ease_out", p)


def _zoom_in(pr, p, t, k, c):
    start = float(k.get("from", 1.8))
    pr["scale"] *= start + (1 - start) * ease("ease_out", p)
    pr["opacity"] *= ease("ease_out", p)


def _punch(pr, p, t, k, c):
    amount = float(k.get("amount", 0.25))
    pr["scale"] *= 1 + amount * (1 - ease("ease_out", p))


def _spin_in(pr, p, t, k, c):
    pr["rotation"] += float(k.get("degrees", -180)) * (1 - ease("ease_out", p))
    pr["scale"] *= ease("back_out", p)


def _drop_in(pr, p, t, k, c):
    pr["y"] -= (1 - ease("bounce_out", p)) * float(k.get("distance", 400))
    pr["opacity"] *= min(1.0, p * 4)


def _typewriter(pr, p, t, k, c):
    pr["reveal"] *= p


def _shake(pr, p, t, k, c):
    amp, f = float(k.get("amplitude", 10)), float(k.get("frequency", 18))
    pr["x"] += math.sin(t * f * 2.1) * amp
    pr["y"] += math.cos(t * f * 1.7) * amp * 0.6


def _bob(pr, p, t, k, c):
    amp, period = float(k.get("amplitude", 14)), float(k.get("period", 2.0))
    pr["y"] += math.sin(2 * math.pi * t / period) * amp


def _pulse(pr, p, t, k, c):
    amt, period = float(k.get("amount", 0.06)), float(k.get("period", 0.8))
    pr["scale"] *= 1 + amt * (0.5 + 0.5 * math.sin(2 * math.pi * t / period))


def _wiggle(pr, p, t, k, c):
    amp, period = float(k.get("degrees", 6)), float(k.get("period", 0.6))
    pr["rotation"] += math.sin(2 * math.pi * t / period) * amp


def _spin(pr, p, t, k, c):
    pr["rotation"] += 360 * t / float(k.get("period", 3.0))


def _float(pr, p, t, k, c):
    amp, period = float(k.get("amplitude", 18)), float(k.get("period", 3.0))
    pr["y"] += math.sin(2 * math.pi * t / period) * amp
    pr["rotation"] += math.sin(2 * math.pi * t / (period * 1.3)) * float(k.get("degrees", 2.5))


def _sway(pr, p, t, k, c):
    pr["rotation"] += math.sin(2 * math.pi * t / float(k.get("period", 2.4))) * float(k.get("degrees", 8))


def _orbit(pr, p, t, k, c):
    r, period = float(k.get("radius", 24)), float(k.get("period", 4.0))
    pr["x"] += math.cos(2 * math.pi * t / period) * r
    pr["y"] += math.sin(2 * math.pi * t / period) * r


def _drift(pr, p, t, k, c):
    pr["x"] += float(k.get("speed_x", 30)) * t
    pr["y"] += float(k.get("speed_y", 0)) * t


def _push(pr, p, t, k, c):
    pr["scale"] *= 1 + float(k.get("rate", 0.03)) * t


def _stretch_in(pr, p, t, k, c):
    v = max(0.0, ease("back_out", p))
    if k.get("axis", "x") == "y":
        pr["sy"] *= v
    else:
        pr["sx"] *= v
    pr["opacity"] *= min(1.0, p * 3)


def _grow_in(pr, p, t, k, c):
    v = ease("ease_out", p)
    if k.get("axis", "y") == "x":
        pr["sx"] *= v
    else:
        pr["sy"] *= v


def _rise_in(pr, p, t, k, c):
    pr["y"] += (1 - ease("ease_out", p)) * float(k.get("distance", 80))
    pr["scale"] *= 0.94 + 0.06 * ease("ease_out", p)
    pr["opacity"] *= ease("ease_out", p)


def _wipe(pr, p, t, k, c):
    pr["reveal"] *= ease("ease_in_out", p)


def _shrink_out(pr, p, t, k, c):
    pr["scale"] *= ease("ease_in_out", p)
    pr["opacity"] *= min(1.0, p * 2)


def _breathe(pr, p, t, k, c):
    amt, period = float(k.get("amount", 0.04)), float(k.get("period", 3.0))
    pr["scale"] *= 1 + amt * math.sin(2 * math.pi * t / period)


def _squash(pr, p, t, k, c):
    amt, period = float(k.get("amount", 0.08)), float(k.get("period", 0.9))
    v = math.sin(2 * math.pi * t / period)
    pr["sx"] *= 1 + amt * v
    pr["sy"] *= 1 - amt * v


def _color_cycle(pr, p, t, k, c):
    cols = k.get("colors") or ["#FF3B6B", "#FFC23B", "#22D3EE"]
    period = float(k.get("period", 3.0))
    if len(cols) < 2 or period <= 0:
        return
    x = (t / period % 1.0) * len(cols)
    i = int(x) % len(cols)
    pr["fill"] = mix_hex(cols[i], cols[(i + 1) % len(cols)], x - int(x))


def _hex4(v: str) -> tuple[int, int, int, int]:
    h = str(v).strip().lstrip("#")
    if len(h) in (3, 4):
        h = "".join(ch * 2 for ch in h)
    if len(h) == 6:
        h += "FF"
    if len(h) != 8:
        raise VeditError(f"Not a colour: {v!r} (use #RRGGBB or #RRGGBBAA)")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)


def mix_hex(a: str, b: str, p: float) -> str:
    """Colour between a and b (p 0..1), in RGB(A); #RRGGBB unless either has alpha. Same rounding as animate.js."""
    ca, cb = _hex4(a), _hex4(b)
    v = [math.floor(x + (y - x) * p + 0.5) for x, y in zip(ca, cb)]
    alpha = ca[3] != 255 or cb[3] != 255
    return "#" + "".join(f"{max(0, min(255, n)):02X}" for n in (v if alpha else v[:3]))


def _draw(pr, p, t, k, c):
    pr["draw"] *= ease(str(k.get("ease", "ease_in_out")), p)


def _count(pr, p, t, k, c):
    pr["count"] *= ease(str(k.get("ease", "expo_out")), p)


def _morph_loop(pr, p, t, k, c):
    period = float(k.get("period", 2.4))
    if period > 0:
        pr["morph"] = 0.5 - 0.5 * math.cos(2 * math.pi * t / period)


PRESETS: dict[str, tuple[str, float, Callable, str]] = {
    "fade_in": ("in", 0.4, _fade, "Fade in"),
    "fade_out": ("out", 0.4, _fade, "Fade out"),
    "pop_in": ("in", 0.35, _pop, "Scale up from 0 with a little overshoot"),
    "pop_out": ("out", 0.25, _pop, "Shrink away"),
    "slide_in": ("in", 0.45, _slide, "Slide in. params: direction=up|down|left|right, distance=px"),
    "slide_out": ("out", 0.4, _slide, "Slide out. params: direction, distance"),
    "zoom_in": ("in", 0.5, _zoom_in, "Zoom from large to normal. params: from=1.8"),
    "punch": ("in", 0.25, _punch, "Quick punch from slightly bigger. params: amount=0.25"),
    "spin_in": ("in", 0.5, _spin_in, "Spin + scale in. params: degrees=-180"),
    "drop_in": ("in", 0.7, _drop_in, "Drop from above with a bounce. params: distance=400"),
    "typewriter": ("in", 0.0, _typewriter, "Reveal text letter by letter (auto duration 0.045s/char)"),
    "shake": ("loop", 0.0, _shake, "Continuous shake. params: amplitude=10, frequency=18"),
    "bob": ("loop", 0.0, _bob, "Gentle float up and down. params: amplitude=14, period=2"),
    "pulse": ("loop", 0.0, _pulse, "Breathing scale pulse. params: amount=0.06, period=0.8"),
    "wiggle": ("loop", 0.0, _wiggle, "Rotate back and forth. params: degrees=6, period=0.6"),
    "spin": ("loop", 0.0, _spin, "Continuous rotation. params: period=3"),
    "float": ("loop", 0.0, _float, "Weightless float: drifts up and down with a slight tilt. params: amplitude=18, period=3, degrees=2.5"),
    "sway": ("loop", 0.0, _sway, "Slow side-to-side tilt, like a character idling. params: degrees=8, period=2.4"),
    "orbit": ("loop", 0.0, _orbit, "Small circular drift. params: radius=24, period=4"),
    "drift": ("loop", 0.0, _drift, "Slow steady pan across the frame (px per second). params: speed_x=30, speed_y=0"),
    "push": ("loop", 0.0, _push, "Slow camera push-in (Ken Burns on a layer). params: rate=0.03 per second"),
    "stretch_in": ("in", 0.45, _stretch_in, "Stretch open from a line, with overshoot. params: axis=x|y"),
    "grow_in": ("in", 0.5, _grow_in, "Grow from flat (bars, underlines). params: axis=y|x"),
    "rise_in": ("in", 0.5, _rise_in, "Rise a little while fading in (calm titles). params: distance=80"),
    "wipe_in": ("in", 0.5, _wipe, "Wipe on from the left"),
    "wipe_out": ("out", 0.4, _wipe, "Wipe off to the left"),
    "shrink_out": ("out", 0.3, _shrink_out, "Shrink to nothing"),
    "breathe": ("loop", 0.0, _breathe, "Slow, calm scale in and out. params: amount=0.04, period=3"),
    "squash_stretch": ("loop", 0.0, _squash, "Cartoon squash and stretch (wide <-> tall). params: amount=0.08, period=0.9"),
    "color_cycle": ("loop", 0.0, _color_cycle, "Shape fill cycles through colours. params: colors=[hex...], period=3"),
    "draw_in": ("in", 1.0, _draw, "Draw on: a shape's outline / a path draws itself, a chart grows. params: ease=ease_in_out"),
    "draw_out": ("out", 0.6, _draw, "Undraw: the outline / chart retracts. params: ease"),
    "count_up": ("in", 1.2, _count, "A counter counts from its `from` to its `to`. params: ease=expo_out"),
    "morph_loop": ("loop", 0.0, _morph_loop, "Shape morphs to its morph_to kind and back. params: period=2.4"),
}


def validate_preset(p: Preset) -> None:
    if p.name not in PRESETS:
        raise VeditError(f"Unknown preset '{p.name}'. Options: {', '.join(PRESETS)}")


def validate_keyframes(kfs: list[Keyframe]) -> None:
    for k in kfs:
        ease(k.ease, 0.5)
        if k.t < 0:
            raise VeditError("Keyframe t must be >= 0 (seconds from layer start)")
        if k.fill is not None:
            _hex4(k.fill)


def _preset_duration(layer: Layer, preset: Preset) -> float:
    phase, default, _, _ = PRESETS[preset.name]
    if preset.duration is not None:
        return max(0.01, preset.duration)
    if preset.name == "typewriter":
        return max(0.2, 0.045 * len(layer.text or ""))
    return default


def _interp_color(kfs: list[Keyframe], prop: str, t: float) -> str | None:
    pts = [(k.t, getattr(k, prop), k.ease) for k in kfs if getattr(k, prop) is not None]
    if not pts:
        return None
    pts.sort(key=lambda x: x[0])
    if t <= pts[0][0]:
        return mix_hex(pts[0][1], pts[0][1], 0)
    for (t0, v0, e0), (t1, v1, _) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            p = (t - t0) / (t1 - t0) if t1 > t0 else 1.0
            return mix_hex(v0, v1, ease(e0, p))
    return mix_hex(pts[-1][1], pts[-1][1], 0)


def _interp(kfs: list[Keyframe], prop: str, t: float, total: float) -> float | None:
    pts = [(k.t, getattr(k, prop), k.ease) for k in kfs if getattr(k, prop) is not None]
    if not pts:
        return None
    pts.sort(key=lambda x: x[0])
    conv = (lambda v: parse_pos(v, total)) if prop in ("x", "y") else float
    if t <= pts[0][0]:
        return conv(pts[0][1])
    for (t0, v0, e0), (t1, v1, _) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            p = (t - t0) / (t1 - t0) if t1 > t0 else 1.0
            a, b = conv(v0), conv(v1)
            return a + (b - a) * ease(e0, p)
    return conv(pts[-1][1])


def props_at(layer: Layer, t: float, duration: float, canvas: Canvas) -> dict:
    """Resolved transform of a layer at local time t (seconds since layer start), in canvas px. With `step_fps` the
    motion moves in steps (t snapped down to 1/step_fps), like stop motion. Besides the
    transform: w / h (the layer's width / height, None when it has none: animating them redraws a shape at that
    size), sx / sy (stretch of the drawn sprite) and fill (shape colour, a hex string or None)."""
    if layer.step_fps:
        t = math.floor(t * layer.step_fps + 1e-9) / layer.step_fps
    pr: dict = {
        "x": parse_pos(layer.x, canvas.width),
        "y": parse_pos(layer.y, canvas.height),
        "scale": layer.scale,
        "rotation": layer.rotation,
        "opacity": layer.opacity,
        "reveal": 1.0,
        "w": None if layer.width is None else float(layer.width),
        "h": None if layer.height is None else float(layer.height),
        "sx": layer.sx,
        "sy": layer.sy,
        "fill": layer.fill,
        "draw": 1.0,
        "morph": 0.0,
        "count": 1.0,
    }
    for prop in PROPS:
        total = canvas.width if prop == "x" else canvas.height
        v = _interp(layer.keyframes, prop, t, total)
        if v is not None:
            pr[prop] = v
    for prop in COLOR_PROPS:
        c = _interp_color(layer.keyframes, prop, t)
        if c is not None:
            pr[prop] = c
    for preset in layer.presets:
        phase, _, fn, _ = PRESETS[preset.name]
        d = _preset_duration(layer, preset)
        if phase == "in":
            if t < d:
                fn(pr, t / d, t, preset.params, canvas)
        elif phase == "out":
            if t > duration - d:
                fn(pr, max(0.0, (duration - t) / d), t, preset.params, canvas)
        else:
            fn(pr, 1.0, t, preset.params, canvas)
    pr["opacity"] = max(0.0, min(1.0, pr["opacity"]))
    pr["reveal"] = max(0.0, min(1.0, pr["reveal"]))
    pr["scale"] = max(0.0, pr["scale"])
    pr["sx"] = max(0.0, pr["sx"])
    pr["sy"] = max(0.0, pr["sy"])
    for k in PROGRESS:
        pr[k] = max(0.0, min(1.0, pr[k]))
    for k in ("w", "h"):
        if pr[k] is not None:
            pr[k] = max(1.0, pr[k])
    return pr

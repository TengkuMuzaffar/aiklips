"""Keyframe animation engine + presets (pure Python, no AI).

A layer's look at time t is: base props -> keyframes (interpolated with easing)
-> presets (in / out / loop modifiers). Claude can write keyframes as JSON for
any custom motion, or stack presets like ["pop_in", "bob", "fade_out"].
"""

from __future__ import annotations

import math
from typing import Callable

from ..project import Canvas, Keyframe, Layer, Preset, VeditError, parse_pos

PROPS = ("x", "y", "scale", "rotation", "opacity", "reveal")


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
}


def ease(name: str, p: float) -> float:
    p = max(0.0, min(1.0, p))
    try:
        return EASINGS[name](p)
    except KeyError:
        raise VeditError(f"Unknown easing '{name}'. Options: {', '.join(EASINGS)}") from None


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
}


def validate_preset(p: Preset) -> None:
    if p.name not in PRESETS:
        raise VeditError(f"Unknown preset '{p.name}'. Options: {', '.join(PRESETS)}")


def validate_keyframes(kfs: list[Keyframe]) -> None:
    for k in kfs:
        ease(k.ease, 0.5)
        if k.t < 0:
            raise VeditError("Keyframe t must be >= 0 (seconds from layer start)")


def _preset_duration(layer: Layer, preset: Preset) -> float:
    phase, default, _, _ = PRESETS[preset.name]
    if preset.duration is not None:
        return max(0.01, preset.duration)
    if preset.name == "typewriter":
        return max(0.2, 0.045 * len(layer.text or ""))
    return default


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


def props_at(layer: Layer, t: float, duration: float, canvas: Canvas) -> dict[str, float]:
    """Resolved transform of a layer at local time t (seconds since layer start), in canvas px."""
    pr = {
        "x": parse_pos(layer.x, canvas.width),
        "y": parse_pos(layer.y, canvas.height),
        "scale": layer.scale,
        "rotation": layer.rotation,
        "opacity": layer.opacity,
        "reveal": 1.0,
    }
    for prop in PROPS:
        total = canvas.width if prop == "x" else canvas.height
        v = _interp(layer.keyframes, prop, t, total)
        if v is not None:
            pr[prop] = v
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
    return pr

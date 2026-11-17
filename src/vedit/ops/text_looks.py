"""Text looks: a reusable bundle of font + colours + outline/glow/gradient + animation.

A look is picked once and reused, so every title in an edit (and every video of a brand) has the
same typography. Built-in looks ship here; users save their own from any layer
(`vedit text look save <name> --from t1`). Saved looks live in ~/.aiklips/text-looks.json.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .. import library
from ..project import Layer, Preset, TextStyle, VeditError
from . import fonts

# style: TextStyle fields. presets: animation (in, out, loop). layer: Layer props (rotation...).
BUILTIN: dict[str, dict[str, Any]] = {
    "viral-hook": {
        "about": "Tall white caps with a heavy outline. The standard scroll-stopping hook.",
        "style": dict(font="anton", size=112, color="#FFFFFF", stroke_color="#000000", stroke_width=10, uppercase=True,
                      letter_spacing=1, shadow=True),
        "presets": ["pop_in", "fade_out"],
    },
    "bold-yellow": {
        "about": "Loud yellow caps. Keywords, prices, 'wait for it'.",
        "style": dict(font="poppins-black", size=96, color="#FFE600", stroke_color="#000000", stroke_width=9, uppercase=True),
        "presets": ["punch", "fade_out"],
    },
    "clean-white": {
        "about": "Calm, modern, no outline. Tutorials and talking-head titles.",
        "style": dict(font="poppins-bold", size=78, color="#FFFFFF", stroke_width=0, shadow=True),
        "presets": [{"name": "slide_in", "params": {"direction": "up", "distance": 60}}, "fade_out"],
    },
    "boxed-label": {
        "about": "Black text on a yellow box. Labels, steps, list items.",
        "style": dict(font="poppins-black", size=72, color="#111111", stroke_width=0, bg_color="#FFE600", bg_padding=22,
                      bg_radius=14, shadow=False, uppercase=True),
        "presets": ["pop_in", "pop_out"],
    },
    "dark-box": {
        "about": "White text on a soft dark box. Readable on any footage.",
        "style": dict(font="poppins-bold", size=70, color="#FFFFFF", stroke_width=0, bg_color="#000000B8", bg_padding=24,
                      bg_radius=22, shadow=False),
        "presets": ["fade_in", "fade_out"],
    },
    "editorial": {
        "about": "Elegant serif, no outline. Beauty, fashion, food, premium products.",
        "style": dict(font="dm-serif", size=100, color="#FFFFFF", stroke_width=0, shadow=True, line_spacing=1.05),
        "presets": [{"name": "fade_in", "duration": 0.6}, {"name": "slide_in", "params": {"direction": "up", "distance": 40}}, "fade_out"],
    },
    "luxury": {
        "about": "Spaced champagne small caps. Real estate, jewellery, events.",
        "style": dict(font="playfair-sc", size=84, color="#F3E3C3", stroke_width=0, letter_spacing=8, shadow=True),
        "presets": [{"name": "fade_in", "duration": 0.8}, {"name": "fade_out", "duration": 0.6}],
    },
    "cinematic": {
        "about": "Wide-tracked condensed caps, no outline. Trailers, travel, mood pieces.",
        "style": dict(font="bebas", size=110, color="#FFFFFF", stroke_width=0, letter_spacing=16, shadow=True, uppercase=True),
        "presets": [{"name": "zoom_in", "params": {"from": 1.15}}, {"name": "fade_out", "duration": 0.6}],
    },
    "marker-note": {
        "about": "Hand-written yellow marker, slightly tilted. Personal notes and reactions.",
        "style": dict(font="marker", size=86, color="#FFE600", stroke_color="#000000", stroke_width=6, shadow=True),
        "presets": ["pop_in", "fade_out"],
        "layer": {"rotation": -4},
    },
    "neon": {
        "about": "Glowing magenta neon. Night, music, nightlife, gaming.",
        "style": dict(font="righteous", size=96, color="#FFFFFF", stroke_color="#FF2BD6", stroke_width=3, glow_color="#FF2BD6",
                      glow_size=22, shadow=False),
        "presets": ["fade_in", {"name": "pulse", "params": {"amount": 0.03, "period": 1.2}}, "fade_out"],
    },
    "retro-sunset": {
        "about": "Block caps with a yellow-to-red gradient and a deep purple outline.",
        "style": dict(font="bungee", size=92, gradient=["#FFE259", "#FF4E50"], stroke_color="#2B0A3D", stroke_width=9, shadow=True),
        "presets": ["drop_in", "fade_out"],
    },
    "comic-pop": {
        "about": "Comic-book punch word with a gradient and a heavy outline. WOW, BOOM, NO WAY.",
        "style": dict(font="bangers", size=124, gradient=["#FFF200", "#FF8A00"], stroke_color="#000000", stroke_width=11,
                      letter_spacing=3, shadow=True),
        "presets": ["punch", {"name": "shake", "params": {"amplitude": 4}}, "pop_out"],
    },
    "playful": {
        "about": "Chunky rounded white with a pink outline. Kids, snacks, fun products.",
        "style": dict(font="titan", size=96, color="#FFFFFF", stroke_color="#FF4F8B", stroke_width=10, shadow=True),
        "presets": ["drop_in", {"name": "bob", "params": {"amplitude": 8}}, "pop_out"],
    },
    "tech": {
        "about": "Mint monospace on a dark panel, typed in. Software, data, how-tos.",
        "style": dict(font="space-mono", size=64, color="#5EEAD4", stroke_width=0, bg_color="#0B1220D9", bg_padding=22,
                      bg_radius=10, shadow=False),
        "presets": ["typewriter", "fade_out"],
    },
    "minimal-caption": {
        "about": "Small clean white with a soft shadow. Quiet captions and footnotes.",
        "style": dict(font="poppins", size=60, color="#FFFFFF", stroke_width=0, shadow=True),
        "presets": ["fade_in", "fade_out"],
    },
}

# which built-in look fits a font feel best (used when a reference has no explicit look)
FOR_FEEL = {
    "condensed": "viral-hook", "bold_sans": "bold-yellow", "clean": "clean-white", "serif": "editorial",
    "handwritten": "marker-note", "rounded": "playful", "display": "comic-pop", "retro": "retro-sunset", "mono": "tech",
}

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def _store():
    return library.home_dir() / "text-looks.json"


def saved() -> dict[str, dict]:
    try:
        data = json.loads(_store().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def all_looks() -> dict[str, dict]:
    return {**BUILTIN, **saved()}


def get(name: str) -> dict:
    looks = all_looks()
    key = name.strip().lower()
    if key not in looks:
        raise VeditError(f"Unknown text look '{name}'. Looks: {', '.join(looks)}")
    return looks[key]


def _preset(p: str | dict) -> Preset:
    return Preset(name=p) if isinstance(p, str) else Preset(**p)


def style_for(name: str, base: TextStyle | None = None, **overrides) -> TextStyle:
    """The TextStyle of a look. `overrides` win over the look (e.g. a size the user asked for)."""
    look = get(name)
    data = TextStyle().model_dump()
    data.update(look.get("style", {}))
    data.update({k: v for k, v in overrides.items() if v is not None})
    data["look"] = name.strip().lower()
    return TextStyle(**data)


def apply(layer: Layer, name: str, animation: bool = True) -> None:
    """Restyle a layer with a look. Captions keep their own size and word timing."""
    from . import animate as anim

    look = get(name)
    keep = {"size": layer.style.size, "max_width": layer.style.max_width} if layer.type == "captions" else {}
    if layer.type == "captions":
        keep["highlight_color"] = look.get("style", {}).get("highlight_color") or layer.style.highlight_color
    layer.style = style_for(name, **keep)
    if layer.type == "captions":
        return
    for k, v in (look.get("layer") or {}).items():
        setattr(layer, k, v)
    if animation and look.get("presets"):
        presets = [_preset(p) for p in look["presets"]]
        for p in presets:
            anim.validate_preset(p)
        layer.presets = presets


def save(name: str, layer: Layer, about: str = "") -> dict:
    key = name.strip().lower()
    if not _NAME.match(key):
        raise VeditError("Look names are lowercase letters, digits and dashes, e.g. 'my-brand'")
    if key in BUILTIN:
        raise VeditError(f"'{key}' is a built-in look; pick another name")
    if layer.type not in ("text", "captions"):
        raise VeditError(f"Layer {layer.id} is not text")
    style = {k: v for k, v in layer.style.model_dump().items() if k != "look"}
    look = {"about": about or f"Saved from layer {layer.id}", "style": style,
            "presets": [p.model_dump(exclude_none=True) for p in layer.presets]}
    if layer.rotation:
        look["layer"] = {"rotation": layer.rotation}
    data = saved()
    data[key] = look
    _store().write_text(json.dumps(data, indent=2))
    return look


def delete(name: str) -> None:
    data = saved()
    key = name.strip().lower()
    if key not in data:
        raise VeditError(f"No saved look '{name}'" + (" (built-in looks can't be deleted)" if key in BUILTIN else ""))
    del data[key]
    _store().write_text(json.dumps(data, indent=2))


def catalog() -> list[dict]:
    user = saved()
    out = []
    for k, v in all_looks().items():
        st = v.get("style", {})
        out.append({"name": k, "about": v.get("about", ""), "font": st.get("font") or fonts.DEFAULT_FONT,
                    "animation": [p if isinstance(p, str) else p["name"] for p in v.get("presets", [])],
                    "saved": k in user})
    return out

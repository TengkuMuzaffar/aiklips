"""Bundled font library: every font has a short key, a feel and a note on when to use it.

The fonts live in assets/fonts/ (all SIL Open Font License or Apache 2.0, see assets/fonts/licenses/).
Keys are what the AI and the CLI use: `text add "HI" --font anton`. Any other font name or path
still works through `ops.text.find_font`.

A *feel* is the family of looks a reference's titles have (condensed, clean, serif...). The study
step records a feel, and `for_feel()` turns that into a concrete font, so a reference's typography
actually carries over to the edit.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

FONT_DIR = Path(__file__).resolve().parents[3] / "assets" / "fonts"

FEELS = ("condensed", "bold_sans", "clean", "serif", "handwritten", "rounded", "display", "retro", "mono")
Feel = Literal[FEELS]  # type: ignore[valid-type]

# key: (file, family label, feel, when to use it)
FONTS: dict[str, tuple[str, str, str, str]] = {
    "anton": ("Anton-Regular.ttf", "Anton", "condensed", "Tall, heavy hook titles; the classic viral-reel look"),
    "bebas": ("BebasNeue-Regular.ttf", "Bebas Neue", "condensed", "All-caps headlines, sporty / cinematic titles"),
    "barlow-condensed": ("BarlowCondensed-ExtraBold.ttf", "Barlow Condensed ExtraBold", "condensed", "Condensed but friendlier, long lines"),
    "archivo-black": ("ArchivoBlack-Regular.ttf", "Archivo Black", "bold_sans", "Wide, very heavy statements and prices"),
    "poppins-black": ("Poppins-Black.ttf", "Poppins Black", "bold_sans", "Loud modern sans: hooks, captions, CTAs"),
    "poppins-bold": ("Poppins-Bold.ttf", "Poppins Bold", "clean", "Clean modern default for titles and captions"),
    "poppins": ("Poppins-SemiBold.ttf", "Poppins SemiBold", "clean", "Calm, readable body text, minimal captions"),
    "kanit": ("Kanit-Bold.ttf", "Kanit Bold", "clean", "Techy, slightly condensed sans; good for tutorials"),
    "dm-serif": ("DMSerifDisplay-Regular.ttf", "DM Serif Display", "serif", "Editorial, premium, beauty / fashion / food"),
    "dm-serif-italic": ("DMSerifDisplay-Italic.ttf", "DM Serif Display Italic", "serif", "Elegant accent words next to a sans title"),
    "abril": ("AbrilFatface-Regular.ttf", "Abril Fatface", "serif", "Bold magazine headline, luxury"),
    "playfair-sc": ("PlayfairDisplaySC-Black.ttf", "Playfair Display SC Black", "serif", "Classic small caps, weddings, real estate"),
    "marker": ("PermanentMarker-Regular.ttf", "Permanent Marker", "handwritten", "Raw, personal, DIY / vlog annotations"),
    "pacifico": ("Pacifico-Regular.ttf", "Pacifico", "handwritten", "Script, cafes, cute / lifestyle"),
    "kalam": ("Kalam-Bold.ttf", "Kalam Bold", "handwritten", "Neat handwriting, notes and tips"),
    "titan": ("TitanOne-Regular.ttf", "Titan One", "rounded", "Chunky rounded, playful, kids / snacks"),
    "lilita": ("LilitaOne-Regular.ttf", "Lilita One", "rounded", "Rounded bold, gaming and fun captions"),
    "luckiest": ("LuckiestGuy-Regular.ttf", "Luckiest Guy", "rounded", "Cartoon energy, memes, big reactions"),
    "bangers": ("Bangers-Regular.ttf", "Bangers", "display", "Comic-book punch words: WOW, BOOM"),
    "righteous": ("Righteous-Regular.ttf", "Righteous", "retro", "Retro-futuristic, neon looks"),
    "bungee": ("Bungee-Regular.ttf", "Bungee", "display", "Signage / street style, block caps"),
    "rubik-mono": ("RubikMonoOne-Regular.ttf", "Rubik Mono One", "display", "Wide geometric caps, streetwear, brands"),
    "monoton": ("Monoton-Regular.ttf", "Monoton", "retro", "Neon-tube outline, use for one or two words only"),
    "space-mono": ("SpaceMono-Bold.ttf", "Space Mono Bold", "mono", "Code, tech, data, timestamps"),
}

# best first; used when a reference says "condensed" etc.
BY_FEEL: dict[str, list[str]] = {f: [k for k, v in FONTS.items() if v[2] == f] for f in FEELS}
DEFAULT_FONT = "poppins-black"

# words a model (or an older study) may have used in free text -> feel
_FEEL_WORDS = {
    "condensed": "condensed", "narrow": "condensed", "tall": "condensed", "impact": "condensed", "headline": "condensed",
    "serif": "serif", "editorial": "serif", "elegant": "serif", "luxury": "serif", "magazine": "serif", "classic": "serif",
    "hand": "handwritten", "script": "handwritten", "marker": "handwritten", "brush": "handwritten", "cursive": "handwritten",
    "round": "rounded", "bubbly": "rounded", "playful": "rounded", "cartoon": "rounded", "chunky": "rounded",
    "comic": "display", "street": "display", "block": "display", "wide": "display", "graffiti": "display",
    "retro": "retro", "neon": "retro", "vintage": "retro", "80s": "retro",
    "mono": "mono", "code": "mono", "typewriter": "mono", "tech": "mono",
    "clean": "clean", "minimal": "clean", "simple": "clean", "modern": "clean", "thin": "clean", "light": "clean",
    "bold": "bold_sans", "heavy": "bold_sans", "black": "bold_sans", "sans": "bold_sans",
}


def path(key: str) -> Path | None:
    """Bundled font file for a key ("anton") or family label ("Bebas Neue"), else None."""
    k = key.strip().lower().replace(" ", "-").replace("_", "-")
    if k in FONTS:
        return FONT_DIR / FONTS[k][0]
    for fk, (_file, family, _f, _n) in FONTS.items():
        if family.lower().replace(" ", "-") == k:
            return FONT_DIR / FONTS[fk][0]
    return None


def feel_of(text: str | None) -> str:
    """Map a free-text description ("bold condensed sans") onto a feel. Specific words (condensed,
    serif...) beat generic weight words (bold, heavy, sans); among equals the earliest word wins."""
    t = (text or "").lower().replace("-", " ").replace("_", " ")
    if t.replace(" ", "_") in FEELS:
        return t.replace(" ", "_")
    hits = sorted((feel == "bold_sans", m.start(), feel) for w, feel in _FEEL_WORDS.items()
                  for m in [re.search(rf"\b{re.escape(w)}", t)] if m)
    return hits[0][2] if hits else "bold_sans"


def for_feel(feel: str | None, pick: int = 0) -> str:
    """A font key for a feel (or a free-text description). `pick` rotates through the feel's fonts."""
    options = BY_FEEL.get(feel_of(feel)) or [DEFAULT_FONT]
    return options[pick % len(options)]


def catalog() -> list[dict]:
    return [{"key": k, "family": fam, "feel": feel, "use": note} for k, (_f, fam, feel, note) in FONTS.items()]

"""The isometric item library: 57 objects (nature, city, transport, office, home, people & signs, data) built from
the renderer's primitives (ops/iso.py), coloured by a theme palette, plus ready-made worlds.

Every item is a recipe in its own grid units: it fills its footprint (fw x fd) from (0, 0) with its front facing +y
(the viewer's left-hand side, where signs and screens read). `expand` scales it (`size`), turns it (`rot` quarter
turns) and puts it at (x, y, z). Colours: the item's `color` / `color2`, else its theme roles. Settings per item are in
`params` (floors, text, value, image ...); `catalog()` lists them.
"""

from __future__ import annotations

from typing import Callable

from PIL import Image

from ..project import IsoItem, IsoScene, VeditError
from .iso import Prim

THEMES: dict[str, dict[str, str]] = {
    "pastel": {"ground": "#E9E4F0", "grass": "#A8E6C1", "foliage": "#7BD6A4", "foliage2": "#5FC48F", "trunk": "#B98B6E",
               "water": "#9ED8F0", "sand": "#F3E1B6", "rock": "#C9C3D6", "primary": "#8FB8FF", "secondary": "#F7A8C8",
               "accent": "#FFD27A", "roof": "#F28B82", "wall": "#FFF6EE", "wall2": "#FDE2C8", "glass": "#BFE3FF",
               "metal": "#B7BED0", "dark": "#4B4A63", "light": "#FFFFFF", "skin": "#F6C9A8", "road": "#8E8BA3",
               "line": "#FFFFFF", "wood": "#D9A87C", "gold": "#F8C95A", "money": "#8FD9A8", "cloth": "#9AA8FF"},
    "bright": {"ground": "#DDE7C7", "grass": "#5DCB5A", "foliage": "#2FAE4E", "foliage2": "#1E8E3E", "trunk": "#8B5A2B",
               "water": "#3BA6F0", "sand": "#F1D18A", "rock": "#9AA0A6", "primary": "#2F7BF6", "secondary": "#F2545B",
               "accent": "#FFC233", "roof": "#E2453C", "wall": "#FAF7F0", "wall2": "#F2C38B", "glass": "#7FD0FF",
               "metal": "#9EA7B3", "dark": "#2B2D42", "light": "#FFFFFF", "skin": "#F1B98C", "road": "#585C66",
               "line": "#FFE14D", "wood": "#C98A4B", "gold": "#FFC20E", "money": "#3FBF6F", "cloth": "#5B6CFF"},
    "corporate": {"ground": "#EEF1F6", "grass": "#BFD8CF", "foliage": "#7FB8A4", "foliage2": "#5E9C88", "trunk": "#9C8A7A",
                  "water": "#A9C9E8", "sand": "#E8DCC4", "rock": "#B8C0CC", "primary": "#2563EB", "secondary": "#0EA5E9",
                  "accent": "#F59E0B", "roof": "#334155", "wall": "#F8FAFC", "wall2": "#E2E8F0", "glass": "#BFDBFE",
                  "metal": "#94A3B8", "dark": "#1E293B", "light": "#FFFFFF", "skin": "#EBC3A3", "road": "#64748B",
                  "line": "#F8FAFC", "wood": "#B9936C", "gold": "#F5B83D", "money": "#34D399", "cloth": "#3B82F6"},
    "night": {"ground": "#1E1B33", "grass": "#2F5D50", "foliage": "#2A7A63", "foliage2": "#1F5C4B", "trunk": "#5C4033",
              "water": "#1F4E79", "sand": "#6B5B45", "rock": "#4A4766", "primary": "#7C5CFF", "secondary": "#FF4FA3",
              "accent": "#3DF5FF", "roof": "#3A2F6B", "wall": "#2D2A4A", "wall2": "#3B3760", "glass": "#5EEAD4",
              "metal": "#6B6F8E", "dark": "#0E0C1A", "light": "#FFF3B0", "skin": "#D8A98A", "road": "#26233D",
              "line": "#FFE66D", "wood": "#6E4F3A", "gold": "#FFD24D", "money": "#34D399", "cloth": "#7C5CFF"},
}


def _theme(name: str) -> dict[str, str]:
    if name not in THEMES:
        raise VeditError(f"Iso themes: {', '.join(THEMES)}")
    return THEMES[name]


# ---- helpers: primitives in item-local grid units -----------------------------------------------------------------

def B(x, y, z, w, d, h, c, **kw) -> Prim:
    return Prim("box", x, y, z, w, d, h, c, **kw)


def CY(cx, cy, z, r, h, c, ry=None, **kw) -> Prim:  # cylinder centred at cx, cy
    ry = r if ry is None else ry
    return Prim("cylinder", cx - r, cy - ry, z, 2 * r, 2 * ry, h, c, **kw)


def CO(cx, cy, z, r, h, c, **kw) -> Prim:  # cone
    return Prim("cone", cx - r, cy - r, z, 2 * r, 2 * r, h, c, **kw)


def PY(x, y, z, w, d, h, c, **kw) -> Prim:  # pyramid
    return Prim("pyramid", x, y, z, w, d, h, c, **kw)


def SP(cx, cy, cz, r, c, rz=None, **kw) -> Prim:  # sphere centred at cx, cy, cz
    rz = r if rz is None else rz
    return Prim("sphere", cx - r, cy - r, cz - rz, 2 * r, 2 * r, 2 * rz, c, **kw)


def WE(x, y, z, w, d, h, c, dir="+x", **kw) -> Prim:  # wedge rising towards dir
    return Prim("wedge", x, y, z, w, d, h, c, dir=dir, **kw)


def _text_img(text: str, color: str, bg: str | None, px: int = 160) -> Image.Image:
    from ..project import TextStyle
    from .text import render_text

    img = render_text(text, TextStyle(size=px, color=color, stroke_width=0, font="poppins-black", max_width=px * 8))
    pad = round(px * 0.35)
    out = Image.new("RGBA", (img.width + pad * 2, img.height + pad), (0, 0, 0, 0) if not bg else _rgba(bg))
    out.alpha_composite(img, (pad, pad // 2))
    return out


def _rgba(c: str):
    from .text import rgba

    return rgba(c)


def _image(path: str | None) -> Image.Image | None:
    if not path:
        return None
    try:
        return Image.open(path).convert("RGBA")
    except OSError:
        return None


# ---- the library --------------------------------------------------------------------------------------------------
# name: (label, category, footprint (fw, fd), default params, recipe(c1, c2, pal, params) -> prims)

Recipe = Callable[[str, str, dict, dict], list[Prim]]
ITEMS: dict[str, tuple[str, str, tuple[float, float], dict, Recipe, tuple[str, str]]] = {}


def item(name: str, label: str, cat: str, fp: tuple[float, float], colors: tuple[str, str], **params):
    def deco(fn: Recipe) -> Recipe:
        ITEMS[name] = (label, cat, fp, params, fn, colors)
        return fn
    return deco


# nature
@item("tree", "Round tree", "Nature", (1, 1), ("foliage", "trunk"))
def _tree(c1, c2, P, k):
    return [CY(0.5, 0.5, 0, 0.09, 0.55, c2), SP(0.5, 0.5, 0.95, 0.42, c1)]


@item("pine", "Pine tree", "Nature", (1, 1), ("foliage2", "trunk"))
def _pine(c1, c2, P, k):
    return [CY(0.5, 0.5, 0, 0.08, 0.3, c2), CO(0.5, 0.5, 0.25, 0.42, 0.7, c1), CO(0.5, 0.5, 0.65, 0.32, 0.65, c1)]


@item("palm", "Palm tree", "Nature", (1, 1), ("foliage", "trunk"))
def _palm(c1, c2, P, k):
    out = [CY(0.5, 0.5, 0, 0.07, 1.3, c2)]
    for x, y, dr in ((0.5, 0.05, "-y"), (0.5, 0.5, "+y"), (0.05, 0.5, "-x"), (0.5, 0.5, "+x")):
        out.append(WE(min(x, 0.5) - (0.0 if dr in ("+x", "+y") else 0.0), min(y, 0.5), 1.15, 0.45 if "x" in dr else 0.18,
                      0.18 if "x" in dr else 0.45, 0.25, c1, dir={"+x": "-x", "-x": "+x", "+y": "-y", "-y": "+y"}[dr]))
    out.append(SP(0.5, 0.5, 1.33, 0.14, c1))
    return out


@item("bush", "Bush", "Nature", (1, 1), ("foliage", "foliage2"))
def _bush(c1, c2, P, k):
    return [SP(0.38, 0.55, 0.28, 0.28, c2), SP(0.62, 0.45, 0.32, 0.3, c1), SP(0.5, 0.7, 0.22, 0.22, c1)]


@item("flowers", "Flower bed", "Nature", (1, 1), ("grass", "secondary"))
def _flowers(c1, c2, P, k):
    out = [B(0, 0, 0, 1, 1, 0.15, c1)]
    for i, (x, y) in enumerate(((0.25, 0.25), (0.7, 0.3), (0.45, 0.6), (0.2, 0.75), (0.75, 0.75))):
        out.append(SP(x, y, 0.25, 0.09, (c2, P["accent"], P["light"])[i % 3]))
    return out


@item("rock", "Rock", "Nature", (1, 1), ("rock", "rock"))
def _rock(c1, c2, P, k):
    return [SP(0.45, 0.5, 0.2, 0.32, c1, rz=0.22), SP(0.72, 0.68, 0.12, 0.15, c2, rz=0.12)]


@item("grass", "Grass tile", "Nature", (1, 1), ("grass", "ground"))
def _grass(c1, c2, P, k):
    return [B(0, 0, 0, 1, 1, 0.12, c1)]


@item("water", "Water tile", "Nature", (1, 1), ("water", "light"))
def _water(c1, c2, P, k):
    return [B(0, 0, 0, 1, 1, 0.08, c1), B(0.2, 0.3, 0.08, 0.35, 0.05, 0.005, c2), B(0.5, 0.65, 0.08, 0.3, 0.05, 0.005, c2)]


@item("sand", "Sand tile", "Nature", (1, 1), ("sand", "sand"))
def _sand(c1, c2, P, k):
    return [B(0, 0, 0, 1, 1, 0.1, c1)]


@item("cloud", "Cloud", "Nature", (1, 1), ("light", "light"), height=2.5)
def _cloud(c1, c2, P, k):
    z = float(k.get("height", 2.5))
    return [SP(0.35, 0.5, z, 0.25, c1, rz=0.16), SP(0.6, 0.45, z + 0.08, 0.3, c1, rz=0.2), SP(0.8, 0.55, z, 0.2, c1, rz=0.14)]


# city
@item("building", "Building", "City", (1.6, 1.6), ("wall2", "glass"), floors=4)
def _building(c1, c2, P, k):
    f = max(1, min(30, int(k.get("floors", 4))))
    h = 0.45 * f
    out = [B(0, 0, 0, 1.6, 1.6, h, c1)]
    for i in range(f):
        z = 0.12 + i * 0.45
        out.append(B(0.15, 1.6, z, 1.3, 0.02, 0.22, c2, outline=False))
        out.append(B(1.6, 0.15, z, 0.02, 1.3, 0.22, c2, outline=False))
    out.append(B(0.1, 0.1, h, 1.4, 1.4, 0.12, P["metal"]))
    return out


@item("skyscraper", "Skyscraper", "City", (1.4, 1.4), ("glass", "metal"), floors=12)
def _sky(c1, c2, P, k):
    h = 0.32 * max(4, min(40, int(k.get("floors", 12))))
    return [B(0, 0, 0, 1.4, 1.4, h, c1), B(0.2, 0.2, h, 1.0, 1.0, 0.3, c2), CY(0.7, 0.7, h + 0.3, 0.04, 0.6, c2)]


@item("house", "House", "City", (1.4, 1.2), ("wall", "roof"))
def _house(c1, c2, P, k):
    return [B(0, 0, 0, 1.4, 1.2, 0.8, c1), WE(0, 0, 0.8, 1.4, 0.6, 0.5, c2, dir="+y"), WE(0, 0.6, 0.8, 1.4, 0.6, 0.5, c2, dir="-y"),
            B(0.55, 1.2, 0, 0.3, 0.02, 0.5, P["wood"], outline=False), B(1.4, 0.35, 0.35, 0.02, 0.5, 0.3, P["glass"], outline=False)]


@item("shop", "Shop", "City", (1.5, 1.2), ("wall", "secondary"), text="SHOP")
def _shop(c1, c2, P, k):
    sign = _text_img(str(k.get("text", "SHOP"))[:14], P["dark"], P["light"])
    return [B(0, 0, 0, 1.5, 1.2, 1.0, c1), B(0.15, 1.2, 0.1, 1.2, 0.02, 0.5, P["glass"], outline=False),
            WE(0, 1.2, 0.62, 1.5, 0.35, 0.18, c2, dir="-y"), B(0.2, 0.25, 1.0, 1.1, 0.12, 0.35, P["light"], face_img=sign)]


@item("cafe", "Café", "City", (1.6, 1.6), ("wall2", "accent"), text="CAFÉ")
def _cafe(c1, c2, P, k):
    sign = _text_img(str(k.get("text", "CAFÉ"))[:14], P["dark"], P["light"])
    return [B(0, 0, 0, 1.6, 1.0, 1.0, c1), WE(0, 1.0, 0.65, 1.6, 0.35, 0.18, c2, dir="-y"),
            B(0.2, 1.0, 0.1, 1.2, 0.02, 0.45, P["glass"], outline=False), B(0.3, 0.1, 1.0, 1.0, 0.12, 0.32, P["light"], face_img=sign),
            CY(1.2, 1.35, 0, 0.04, 0.35, P["metal"]), CY(1.2, 1.35, 0.35, 0.18, 0.04, P["wood"]),
            CY(0.5, 1.35, 0, 0.04, 0.35, P["metal"]), CY(0.5, 1.35, 0.35, 0.18, 0.04, P["wood"])]


@item("factory", "Factory", "City", (2, 1.4), ("wall2", "metal"))
def _factory(c1, c2, P, k):
    out = [B(0, 0, 0, 2, 1.4, 0.8, c1)]
    for i in range(3):
        out.append(WE(i * 0.66, 0, 0.8, 0.66, 1.4, 0.35, c2, dir="+x"))
    out += [CY(1.65, 0.35, 0.8, 0.14, 1.2, P["roof"]), CY(1.25, 0.3, 0.8, 0.11, 0.9, P["roof"])]
    return out


@item("road", "Road tile", "City", (1, 1), ("road", "line"))
def _road(c1, c2, P, k):
    return [B(0, 0, 0, 1, 1, 0.06, c1), B(0.47, 0.1, 0.06, 0.06, 0.3, 0.005, c2, outline=False),
            B(0.47, 0.6, 0.06, 0.06, 0.3, 0.005, c2, outline=False)]


@item("crossing", "Crossing", "City", (1, 1), ("road", "line"))
def _crossing(c1, c2, P, k):
    return [B(0, 0, 0, 1, 1, 0.06, c1)] + [B(0.08 + i * 0.18, 0.15, 0.06, 0.1, 0.7, 0.005, c2, outline=False) for i in range(5)]


@item("lamp_post", "Street lamp", "City", (1, 1), ("dark", "light"))
def _lamp_post(c1, c2, P, k):
    return [CY(0.5, 0.5, 0, 0.05, 1.3, c1), B(0.45, 0.45, 1.3, 0.3, 0.1, 0.06, c1), SP(0.72, 0.5, 1.26, 0.08, c2)]


@item("traffic_light", "Traffic light", "City", (1, 1), ("dark", "dark"))
def _traffic(c1, c2, P, k):
    return [CY(0.5, 0.5, 0, 0.05, 1.0, c1), B(0.38, 0.4, 1.0, 0.24, 0.2, 0.5, c2),
            SP(0.5, 0.61, 1.38, 0.06, "#EF4444"), SP(0.5, 0.61, 1.25, 0.06, "#F59E0B"), SP(0.5, 0.61, 1.12, 0.06, "#22C55E")]


@item("bench", "Bench", "City", (1, 0.5), ("wood", "dark"))
def _bench(c1, c2, P, k):
    return [B(0.1, 0.1, 0, 0.08, 0.3, 0.22, c2), B(0.82, 0.1, 0, 0.08, 0.3, 0.22, c2), B(0, 0.05, 0.22, 1, 0.38, 0.06, c1),
            B(0, 0.05, 0.28, 1, 0.06, 0.32, c1)]


@item("fountain", "Fountain", "City", (1.4, 1.4), ("rock", "water"))
def _fountain(c1, c2, P, k):
    return [CY(0.7, 0.7, 0, 0.65, 0.25, c1), CY(0.7, 0.7, 0.25, 0.55, 0.01, c2), CY(0.7, 0.7, 0.25, 0.12, 0.5, c1),
            SP(0.7, 0.7, 0.85, 0.16, c2)]


# transport
@item("car", "Car", "Transport", (1.4, 0.7), ("secondary", "glass"))
def _car(c1, c2, P, k):
    w = P["dark"]
    return [B(0.15, 0.02, 0, 0.25, 0.08, 0.2, w), B(1.0, 0.02, 0, 0.25, 0.08, 0.2, w), B(0.15, 0.6, 0, 0.25, 0.08, 0.2, w),
            B(1.0, 0.6, 0, 0.25, 0.08, 0.2, w), B(0, 0.06, 0.1, 1.4, 0.58, 0.32, c1), B(0.35, 0.1, 0.42, 0.7, 0.5, 0.25, c2)]


@item("bus", "Bus", "Transport", (2.4, 0.8), ("accent", "glass"))
def _bus(c1, c2, P, k):
    w = P["dark"]
    return [B(0.3, 0.02, 0, 0.3, 0.08, 0.22, w), B(1.8, 0.02, 0, 0.3, 0.08, 0.22, w), B(0.3, 0.7, 0, 0.3, 0.08, 0.22, w),
            B(1.8, 0.7, 0, 0.3, 0.08, 0.22, w), B(0, 0.06, 0.12, 2.4, 0.68, 0.75, c1), B(0.15, 0.74, 0.5, 2.1, 0.01, 0.26, c2, outline=False),
            B(2.4, 0.12, 0.45, 0.01, 0.56, 0.32, c2, outline=False)]


@item("truck", "Truck", "Transport", (2.2, 0.8), ("primary", "light"))
def _truck(c1, c2, P, k):
    w = P["dark"]
    return [B(0.2, 0.02, 0, 0.3, 0.08, 0.22, w), B(1.7, 0.02, 0, 0.3, 0.08, 0.22, w), B(0.2, 0.7, 0, 0.3, 0.08, 0.22, w),
            B(1.7, 0.7, 0, 0.3, 0.08, 0.22, w), B(0, 0.06, 0.12, 1.5, 0.68, 0.85, c2), B(1.5, 0.06, 0.12, 0.7, 0.68, 0.55, c1),
            B(1.85, 0.12, 0.45, 0.36, 0.56, 0.18, P["glass"])]


@item("bike", "Bike", "Transport", (1.1, 0.3), ("primary", "dark"))
def _bike(c1, c2, P, k):
    return [B(0.0, 0.12, 0, 0.4, 0.05, 0.4, c2), B(0.7, 0.12, 0, 0.4, 0.05, 0.4, c2),  # wheels (square-ish, toy style)
            B(0.2, 0.13, 0.2, 0.7, 0.04, 0.05, c1), B(0.45, 0.13, 0.2, 0.05, 0.04, 0.3, c1), B(0.85, 0.13, 0.2, 0.05, 0.04, 0.4, c1),
            B(0.35, 0.06, 0.5, 0.28, 0.16, 0.05, c2), B(0.8, 0.02, 0.6, 0.06, 0.26, 0.04, c2)]


@item("boat", "Boat", "Transport", (1.6, 0.7), ("light", "secondary"))
def _boat(c1, c2, P, k):
    return [B(0.2, 0, 0, 1.2, 0.7, 0.25, c2), WE(1.4, 0, 0, 0.2, 0.7, 0.25, c2, dir="-x"), WE(0, 0, 0, 0.2, 0.7, 0.25, c2, dir="+x"),
            B(0.4, 0.1, 0.25, 0.6, 0.5, 0.35, c1), B(0.45, 0.6, 0.35, 0.5, 0.01, 0.15, P["glass"], outline=False)]


@item("plane", "Plane", "Transport", (2, 1.8), ("light", "primary"), height=1.5)
def _plane(c1, c2, P, k):
    z = float(k.get("height", 1.5))
    return [B(0, 0.75, z, 2, 0.3, 0.3, c1), B(0.7, 0, z + 0.1, 0.5, 1.8, 0.06, c2), WE(0, 0.82, z + 0.3, 0.35, 0.16, 0.35, c2, dir="-x"),
            B(1.8, 0.78, z + 0.05, 0.2, 0.24, 0.2, P["glass"])]


# office / desk
@item("desk", "Desk", "Office", (2, 1), ("wood", "metal"))
def _desk(c1, c2, P, k):
    return [B(0.05, 0.05, 0, 0.08, 0.08, 0.7, c2), B(1.87, 0.05, 0, 0.08, 0.08, 0.7, c2), B(0.05, 0.87, 0, 0.08, 0.08, 0.7, c2),
            B(1.87, 0.87, 0, 0.08, 0.08, 0.7, c2), B(0, 0, 0.7, 2, 1, 0.08, c1)]


@item("chair", "Chair", "Office", (0.6, 0.6), ("dark", "metal"))
def _chair(c1, c2, P, k):
    return [CY(0.3, 0.3, 0, 0.25, 0.05, c2), CY(0.3, 0.3, 0.05, 0.04, 0.35, c2), B(0.02, 0.02, 0.4, 0.56, 0.56, 0.1, c1),
            B(0.02, 0.02, 0.5, 0.56, 0.08, 0.5, c1)]


@item("laptop", "Laptop", "Office", (0.8, 0.6), ("metal", "dark"), text="")
def _laptop(c1, c2, P, k):
    scr = _text_img(str(k.get("text")), P["light"], P["primary"], 120) if k.get("text") else None
    return [B(0, 0.05, 0, 0.8, 0.55, 0.04, c1), B(0, 0.02, 0.04, 0.8, 0.04, 0.5, c1),
            B(0.05, 0.06, 0.08, 0.7, 0.01, 0.42, P["primary"] if not scr else P["dark"], face_img=scr, outline=False)]


@item("monitor", "Monitor", "Office", (1, 0.4), ("dark", "primary"), text="", image="")
def _monitor(c1, c2, P, k):
    img = _image(k.get("image")) or (_text_img(str(k.get("text")), P["light"], c2, 120) if k.get("text") else None)
    return [B(0.35, 0.1, 0, 0.3, 0.25, 0.03, c1), B(0.46, 0.15, 0.03, 0.08, 0.06, 0.3, c1), B(0, 0.2, 0.3, 1, 0.08, 0.6, c1),
            B(0.04, 0.28, 0.34, 0.92, 0.01, 0.52, c2, face_img=img, outline=False)]


@item("phone", "Phone", "Office", (0.4, 0.7), ("dark", "primary"), image="")
def _phone(c1, c2, P, k):
    img = _image(k.get("image"))
    return [B(0, 0, 0, 0.4, 0.7, 0.05, c1), B(0.03, 0.04, 0.05, 0.34, 0.62, 0.005, c2, face_img=img, img_face="+z", outline=False)]


@item("mug", "Mug", "Office", (0.5, 0.5), ("secondary", "light"))
def _mug(c1, c2, P, k):
    return [CY(0.25, 0.25, 0, 0.17, 0.35, c1), B(0.4, 0.21, 0.08, 0.1, 0.08, 0.2, c1), CY(0.25, 0.25, 0.35, 0.13, 0.005, P["trunk"])]


@item("plant", "Potted plant", "Office", (0.6, 0.6), ("foliage", "roof"))
def _plant(c1, c2, P, k):
    return [CY(0.3, 0.3, 0, 0.2, 0.35, c2), SP(0.3, 0.3, 0.6, 0.27, c1), SP(0.2, 0.38, 0.5, 0.15, P["foliage2"])]


@item("books", "Book stack", "Office", (0.8, 0.6), ("primary", "secondary"))
def _books(c1, c2, P, k):
    return [B(0, 0, 0, 0.8, 0.55, 0.12, c1), B(0.04, 0.02, 0.12, 0.72, 0.52, 0.1, c2), B(0.02, 0.04, 0.22, 0.76, 0.5, 0.11, P["accent"])]


@item("whiteboard", "Whiteboard", "Office", (1.6, 0.4), ("light", "metal"), text="")
def _whiteboard(c1, c2, P, k):
    img = _text_img(str(k.get("text")), P["dark"], None, 120) if k.get("text") else None
    return [B(0.1, 0.15, 0, 0.06, 0.06, 1.0, c2), B(1.44, 0.15, 0, 0.06, 0.06, 1.0, c2), B(0, 0.2, 0.6, 1.6, 0.05, 0.9, c1, face_img=img)]


@item("server", "Server rack", "Office", (0.8, 0.8), ("dark", "accent"))
def _server(c1, c2, P, k):
    out = [B(0, 0, 0, 0.8, 0.8, 1.8, c1)]
    out += [B(0.08, 0.8, 0.2 + i * 0.3, 0.64, 0.01, 0.05, c2, outline=False) for i in range(5)]
    return out


# home
@item("sofa", "Sofa", "Home", (1.8, 0.8), ("cloth", "dark"))
def _sofa(c1, c2, P, k):
    return [B(0, 0, 0.08, 1.8, 0.8, 0.3, c1), B(0, 0, 0.38, 1.8, 0.22, 0.4, c1), B(0, 0, 0.38, 0.2, 0.8, 0.22, c1),
            B(1.6, 0, 0.38, 0.2, 0.8, 0.22, c1), B(0.1, 0.1, 0, 0.1, 0.1, 0.08, c2), B(1.6, 0.6, 0, 0.1, 0.1, 0.08, c2)]


@item("bed", "Bed", "Home", (1.4, 2), ("wood", "light"))
def _bed(c1, c2, P, k):
    return [B(0, 0, 0, 1.4, 2, 0.3, c1), B(0.05, 0.05, 0.3, 1.3, 1.9, 0.18, c2), B(0.2, 0.1, 0.48, 1.0, 0.35, 0.1, c2),
            B(0, 0, 0.3, 1.4, 0.08, 0.5, c1), B(0.05, 0.7, 0.48, 1.3, 1.25, 0.04, P["primary"])]


@item("table", "Table", "Home", (1.2, 0.8), ("wood", "wood"))
def _table(c1, c2, P, k):
    return [B(0.05, 0.05, 0, 0.08, 0.08, 0.6, c2), B(1.07, 0.05, 0, 0.08, 0.08, 0.6, c2), B(0.05, 0.67, 0, 0.08, 0.08, 0.6, c2),
            B(1.07, 0.67, 0, 0.08, 0.08, 0.6, c2), B(0, 0, 0.6, 1.2, 0.8, 0.07, c1)]


@item("lamp", "Floor lamp", "Home", (0.6, 0.6), ("light", "dark"))
def _lamp(c1, c2, P, k):
    return [CY(0.3, 0.3, 0, 0.2, 0.05, c2), CY(0.3, 0.3, 0.05, 0.03, 1.1, c2), CY(0.3, 0.3, 1.1, 0.24, 0.32, c1)]


@item("tv", "TV", "Home", (1.4, 0.5), ("dark", "primary"), text="", image="")
def _tv(c1, c2, P, k):
    img = _image(k.get("image")) or (_text_img(str(k.get("text")), P["light"], c2, 120) if k.get("text") else None)
    return [B(0.2, 0, 0, 1.0, 0.5, 0.35, P["wood"]), B(0, 0.2, 0.35, 1.4, 0.06, 0.8, c1),
            B(0.05, 0.26, 0.4, 1.3, 0.01, 0.7, c2, face_img=img, outline=False)]


@item("bookshelf", "Bookshelf", "Home", (1.2, 0.4), ("wood", "secondary"))
def _bookshelf(c1, c2, P, k):
    out = [B(0, 0, 0, 1.2, 0.4, 1.6, c1)]
    cols = [c2, P["primary"], P["accent"], P["foliage"]]
    for r in range(3):
        for i in range(5):
            out.append(B(0.08 + i * 0.21, 0.4, 0.12 + r * 0.5, 0.16, 0.01, 0.34, cols[(r + i) % 4], outline=False))
    return out


# people & signs
@item("person", "Person", "People & signs", (0.5, 0.4), ("cloth", "dark"), wave=False)
def _person(c1, c2, P, k):
    s = P["skin"]
    out = [B(0.1, 0.12, 0, 0.12, 0.16, 0.45, c2), B(0.28, 0.12, 0, 0.12, 0.16, 0.45, c2), B(0.06, 0.08, 0.45, 0.38, 0.24, 0.42, c1),
           SP(0.25, 0.2, 1.05, 0.16, s), B(0.0, 0.14, 0.5, 0.07, 0.12, 0.35, c1)]
    out.append(B(0.43, 0.14, 0.85, 0.07, 0.12, 0.35, c1) if k.get("wave") else B(0.43, 0.14, 0.5, 0.07, 0.12, 0.35, c1))
    return out


@item("crowd", "Group of people", "People & signs", (1.4, 1), ("cloth", "dark"))
def _crowd(c1, c2, P, k):
    out = []
    for dx, dy, col in ((0.0, 0.0, c1), (0.5, 0.45, P["secondary"]), (0.9, 0.05, P["accent"])):
        out += [Prim(p.kind, p.x + dx, p.y + dy, p.z, p.w, p.d, p.h, p.color if p.color != c1 else col) for p in _person(c1, c2, P, {})]
    return out


@item("sign", "Sign", "People & signs", (1.2, 0.3), ("light", "dark"), text="HELLO")
def _sign(c1, c2, P, k):
    img = _text_img(str(k.get("text", "HELLO"))[:24], P["dark"], None)
    return [B(0.15, 0.1, 0, 0.08, 0.08, 0.7, c2), B(0.97, 0.1, 0, 0.08, 0.08, 0.7, c2), B(0, 0.05, 0.6, 1.2, 0.12, 0.5, c1, face_img=img)]


@item("billboard", "Billboard", "People & signs", (2.4, 0.4), ("light", "metal"), text="YOUR AD", image="")
def _billboard(c1, c2, P, k):
    img = _image(k.get("image")) or _text_img(str(k.get("text", "YOUR AD"))[:28], P["light"], P["primary"])
    return [B(0.4, 0.15, 0, 0.1, 0.1, 1.2, c2), B(1.9, 0.15, 0, 0.1, 0.1, 1.2, c2), B(0, 0.1, 1.1, 2.4, 0.12, 1.2, c1, face_img=img)]


@item("flag", "Flag", "People & signs", (0.8, 0.3), ("secondary", "metal"))
def _flag(c1, c2, P, k):
    return [CY(0.1, 0.15, 0, 0.04, 1.4, c2), B(0.12, 0.12, 1.0, 0.6, 0.04, 0.38, c1)]


@item("pin", "Map pin", "People & signs", (0.6, 0.6), ("secondary", "light"))
def _pin(c1, c2, P, k):
    return [CY(0.3, 0.3, 0, 0.12, 0.02, P["dark"]), CY(0.3, 0.3, 0, 0.035, 0.6, c1), SP(0.3, 0.3, 0.8, 0.24, c1), SP(0.22, 0.25, 0.86, 0.08, c2)]


# data & business
@item("bar", "Bar tower", "Data", (0.8, 0.8), ("primary", "accent"), value=3.0, text="")
def _bar(c1, c2, P, k):
    h = max(0.05, float(k.get("value", 3.0)))
    out = [B(0, 0, 0, 0.8, 0.8, h, c1)]
    if k.get("text"):
        out.append(B(0.05, 0.8, max(0.0, h - 0.4), 0.7, 0.01, 0.35, c1, face_img=_text_img(str(k["text"])[:8], P["light"], None, 120), outline=False))
    return out


@item("coins", "Coin stack", "Data", (0.6, 0.6), ("gold", "gold"), count=5)
def _coins(c1, c2, P, k):
    return [CY(0.3, 0.3, i * 0.09, 0.26, 0.08, c1) for i in range(max(1, min(20, int(k.get("count", 5)))))]


@item("money", "Money stack", "Data", (0.8, 0.5), ("money", "light"), count=4)
def _money(c1, c2, P, k):
    out = []
    for i in range(max(1, min(12, int(k.get("count", 4))))):
        out += [B(0, 0, i * 0.1, 0.8, 0.45, 0.09, c1), B(0.35, 0, i * 0.1, 0.1, 0.45, 0.091, c2, outline=False)]
    return out


@item("gift", "Gift box", "Data", (0.7, 0.7), ("secondary", "accent"))
def _gift(c1, c2, P, k):
    return [B(0, 0, 0, 0.7, 0.7, 0.55, c1), B(0.3, 0, 0, 0.1, 0.7, 0.56, c2, outline=False), B(0, 0.3, 0, 0.7, 0.1, 0.56, c2, outline=False),
            B(-0.03, -0.03, 0.55, 0.76, 0.76, 0.1, c1), SP(0.35, 0.35, 0.72, 0.1, c2)]


@item("trophy", "Trophy", "Data", (0.6, 0.6), ("gold", "dark"))
def _trophy(c1, c2, P, k):
    return [B(0.08, 0.08, 0, 0.44, 0.44, 0.15, c2), CY(0.3, 0.3, 0.15, 0.05, 0.25, c1), CY(0.3, 0.3, 0.4, 0.22, 0.3, c1),
            SP(0.08, 0.3, 0.6, 0.07, c1), SP(0.52, 0.3, 0.6, 0.07, c1)]


@item("rocket", "Rocket", "Data", (0.8, 0.8), ("light", "secondary"), height=0.0)
def _rocket(c1, c2, P, k):
    z = float(k.get("height", 0.0))
    return [WE(0.0, 0.3, z, 0.25, 0.2, 0.35, c2, dir="+x"), WE(0.55, 0.3, z, 0.25, 0.2, 0.35, c2, dir="-x"),
            CY(0.4, 0.4, z, 0.2, 1.1, c1), CO(0.4, 0.4, z + 1.1, 0.2, 0.4, c2), SP(0.4, 0.4, z + 0.7, 0.08, P["glass"]),
            CO(0.4, 0.4, z - 0.25, 0.12, 0.25, P["accent"])]


# floors / bases
@item("plate", "Base plate", "Floors", (4, 4), ("ground", "dark"), w=4.0, d=4.0)
def _plate(c1, c2, P, k):
    w, d = float(k.get("w", 4)), float(k.get("d", 4))
    return [B(0, 0, -0.3, w, d, 0.3, c1)]


@item("island_base", "Island", "Floors", (5, 5), ("grass", "sand"), w=5.0, d=5.0)
def _island_base(c1, c2, P, k):
    w, d = float(k.get("w", 5)), float(k.get("d", 5))
    return [B(0, 0, -0.7, w, d, 0.55, c2), B(0.3, 0.3, -0.15, w - 0.6, d - 0.6, 0.15, c1)]


@item("floor_tiles", "Checker floor", "Floors", (4, 4), ("wall", "wall2"), w=4, d=4)
def _floor_tiles(c1, c2, P, k):
    w, d = int(k.get("w", 4)), int(k.get("d", 4))
    return [B(i, j, -0.12, 1, 1, 0.12, c1 if (i + j) % 2 else c2, outline=False) for i in range(w) for j in range(d)]


@item("wall", "Back wall", "Floors", (4, 0.2), ("wall2", "wall2"), w=4.0, height=2.0)
def _wall(c1, c2, P, k):
    return [B(0, 0, 0, float(k.get("w", 4)), 0.2, float(k.get("height", 2)), c1)]


@item("fence", "Fence", "Nature", (2, 0.2), ("wood", "wood"), w=2.0)
def _fence(c1, c2, P, k):
    w = float(k.get("w", 2))
    n = max(2, int(w / 0.4) + 1)
    out = [B(i * (w - 0.08) / (n - 1), 0.06, 0, 0.08, 0.08, 0.45, c1) for i in range(n)]
    return out + [B(0, 0.08, 0.3, w, 0.04, 0.06, c2), B(0, 0.08, 0.12, w, 0.04, 0.06, c2)]


@item("tent", "Tent", "Nature", (1.2, 1.2), ("accent", "dark"))
def _tent(c1, c2, P, k):
    return [PY(0, 0, 0, 1.2, 1.2, 1.0, c1), B(0.5, 1.19, 0, 0.2, 0.02, 0.35, c2, outline=False)]


@item("cone", "Traffic cone", "City", (0.5, 0.5), ("accent", "light"))
def _tcone(c1, c2, P, k):
    return [B(0, 0, 0, 0.5, 0.5, 0.05, c1), CO(0.25, 0.25, 0.05, 0.18, 0.5, c1), CY(0.25, 0.25, 0.22, 0.11, 0.07, c2)]


@item("mailbox", "Mailbox", "City", (0.4, 0.4), ("primary", "dark"))
def _mailbox(c1, c2, P, k):
    return [CY(0.2, 0.2, 0, 0.04, 0.6, c2), B(0.05, 0.02, 0.6, 0.3, 0.36, 0.28, c1)]


def catalog() -> list[dict]:
    return [{"kind": k, "label": v[0], "category": v[1], "footprint": list(v[2]), "params": v[3], "colors": list(v[5])}
            for k, v in ITEMS.items()]


# ---- expand: an item -> primitives in scene coordinates -----------------------------------------------------------

_ROT_DIR = {"+x": "+y", "+y": "-x", "-x": "-y", "-y": "+x"}


def expand(it: IsoItem, theme: str = "pastel") -> list[Prim]:
    if it.kind not in ITEMS:
        raise VeditError(f"Unknown iso item '{it.kind}'. Items: {', '.join(ITEMS)}")
    label, cat, (fw, fd), defaults, fn, roles = ITEMS[it.kind]
    P = _theme(theme)
    c1, c2 = it.color or P[roles[0]], it.color2 or P[roles[1]]
    prims = fn(c1, c2, P, {**defaults, **it.params})
    s = max(0.05, it.size)
    cx, cy = fw * s / 2, fd * s / 2
    out = []
    for p in prims:
        x, y, z, w, d, h, dr, face = p.x * s, p.y * s, p.z * s, p.w * s, p.d * s, p.h * s, p.dir, p.img_face
        for _ in range(it.rot % 4):  # a quarter turn about the footprint's centre
            x, y, w, d = cx - (y + d - cy), cy + (x - cx), d, w
            dr = _ROT_DIR[dr]
            face = _ROT_DIR.get(face, face)
            cx, cy = cx, cy
        if it.rot % 2:  # the footprint itself turned: keep its corner at (0, 0)
            x, y = x - (cx - cy), y + (cx - cy)
        out.append(Prim(p.kind, x + it.x, y + it.y, z + it.z, w, d, h, p.color, dir=dr, face_img=p.face_img, img_face=face,
                        outline=p.outline, tags=p.tags))
    return out


def footprint(kind: str, size: float = 1.0, rot: int = 0) -> tuple[float, float]:
    fw, fd = ITEMS[kind][2]
    return (fd * size, fw * size) if rot % 2 else (fw * size, fd * size)


# ---- ready-made worlds ----------------------------------------------------------------------------------------------

def I(kind, x, y, z=0.0, **kw) -> IsoItem:
    return IsoItem(kind=kind, x=x, y=y, z=z, **kw)


def _city(t):
    items = [I("plate", 0, 0, params={"w": 7, "d": 7}), I("road", 3, 0), I("road", 3, 1), I("crossing", 3, 2), I("road", 3, 3), I("road", 3, 4),
             I("road", 3, 5), I("road", 3, 6), I("skyscraper", 0.3, 0.3), I("building", 0.4, 2.3, params={"floors": 3}),
             I("house", 0.5, 4.6), I("shop", 4.6, 0.4, params={"text": "SHOP"}), I("building", 4.7, 2.2, params={"floors": 5}),
             I("tree", 5.2, 4.8), I("tree", 6.0, 5.8), I("lamp_post", 2.0, 5.6), I("car", 3.05, 4.2, rot=1), I("tree", 1.9, 3.8),
             I("person", 2.3, 2.6), I("cloud", 5, 1, params={"height": 3.6})]
    return IsoScene(items=items, theme=t)


def _island(t):
    return IsoScene(theme=t, items=[I("island_base", 0, 0), I("house", 1.0, 1.2), I("tree", 3.4, 0.8), I("palm", 3.6, 3.2), I("pine", 1.0, 3.4),
                                    I("bush", 2.4, 3.6), I("flowers", 2.6, 2.4), I("rock", 0.6, 0.6),
                                    I("cloud", 4.5, 0.5, params={"height": 3})])


def _desk(t):
    return IsoScene(theme=t, items=[I("floor_tiles", 0, 0, params={"w": 4, "d": 4}), I("wall", 0, 0, params={"w": 4, "height": 2.2}),
                                    I("desk", 1, 1.2), I("monitor", 1.5, 1.25, z=0.78), I("laptop", 2.1, 1.6, z=0.78), I("mug", 1.15, 1.6, z=0.78),
                                    I("chair", 1.6, 2.6), I("plant", 3.1, 0.4), I("books", 2.4, 1.25, z=0.78), I("bookshelf", 0.1, 0.25, rot=0)])


def _stack(t):
    P = _theme(t)
    cols = [P["primary"], P["secondary"], P["accent"], P["foliage"], P["glass"], P["roof"]]
    return IsoScene(theme=t, items=[I("plate", 0, 0, params={"w": 2.2, "d": 2.2})] + [
        IsoItem(kind="plate", x=0.1, y=0.1, z=0.3 + i * 0.7, color=cols[i], params={"w": 2, "d": 2}) for i in range(6)])


def _cafe(t):
    return IsoScene(theme=t, items=[I("plate", 0, 0, params={"w": 5, "d": 5}), I("cafe", 1.2, 0.6, params={"text": "CAFÉ"}),
                                    I("tree", 0.2, 0.4), I("tree", 3.6, 0.5), I("bench", 0.5, 3.4), I("person", 2.6, 3.0, params={"wave": True}),
                                    I("person", 3.4, 3.4, color="#F7A8C8"), I("flowers", 3.8, 3.8), I("lamp_post", 0.2, 2.4), I("bike", 1.6, 4.2)])


def _office(t):
    return IsoScene(theme=t, items=[I("floor_tiles", 0, 0, params={"w": 6, "d": 5}), I("wall", 0, 0, params={"w": 6, "height": 2.4}),
                                    I("desk", 0.6, 1.2), I("monitor", 1.1, 1.25, z=0.78), I("chair", 1.2, 2.5), I("desk", 3.3, 1.2),
                                    I("laptop", 3.9, 1.5, z=0.78), I("chair", 3.9, 2.5), I("whiteboard", 2.0, 0.25, params={"text": "Q4 GOALS"}),
                                    I("server", 5.0, 0.4), I("plant", 0.2, 4.2), I("person", 2.6, 3.4, params={"wave": True}), I("sofa", 3.6, 3.9)])


def _farm(t):
    items = [I("plate", 0, 0, color=_theme(t)["grass"], params={"w": 6, "d": 6}), I("house", 0.6, 0.6, color2="#C0392B"),
             I("fence", 0.2, 3.0, params={"w": 3}), I("tree", 4.4, 0.6), I("tree", 5.0, 1.6), I("tent", 3.8, 3.8), I("truck", 2.6, 0.8, rot=1)]
    items += [I("flowers", 0.5 + i, 3.6 + j) for i in range(3) for j in range(2)]
    return IsoScene(theme=t, items=items)


def _data(t):
    vals = [1.2, 2.0, 2.8, 4.0]
    items = [I("plate", 0, 0, params={"w": 5, "d": 2.2})]
    items += [I("bar", 0.3 + i * 1.15, 0.7, params={"value": v}) for i, v in enumerate(vals)]
    items += [I("rocket", 4.3, 1.4, params={"height": 4.6}), I("coins", 0.2, 1.6), I("trophy", 3.2, 1.5)]
    return IsoScene(theme=t, items=items)


WORLDS: dict[str, Callable[[str], IsoScene]] = {"city": _city, "island": _island, "desk": _desk, "stack": _stack, "cafe": _cafe,
                                               "office": _office, "farm": _farm, "data": _data}


# ---- editing a scene (CLI, editor and AI share these) -------------------------------------------------------------

ITEM_FIELDS = ("x", "y", "z", "rot", "size", "color", "color2", "label")


def _check(it: IsoItem) -> IsoItem:
    if it.kind not in ITEMS:
        raise VeditError(f"Unknown iso item '{it.kind}'. Items: {', '.join(ITEMS)} (`vedit iso items`)")
    it.rot %= 4
    it.size = max(0.1, min(6.0, it.size))
    from .text import rgba

    for c in (it.color, it.color2):
        if c:
            rgba(c)
    return it


def free_spot(sc: IsoScene, kind: str, size: float = 1.0, rot: int = 0) -> tuple[float, float, float]:
    """A grid spot for a new item: on the floor plate, clear of the other items (or right next to the world)."""
    fw, fd = footprint(kind, size, rot)
    floors = [i for i in sc.items if ITEMS.get(i.kind, ("", ""))[1] == "Floors" and i.kind != "wall"]
    taken = [(i.x, i.y, *footprint(i.kind, i.size, i.rot)) for i in sc.items if i not in floors and i.kind in ITEMS
             and ITEMS[i.kind][1] != "Floors"]
    if floors:
        f = floors[0]
        fx, fy = f.x, f.y
        FW, FD = footprint(f.kind, f.size, f.rot) if f.kind != "plate" else (float(f.params.get("w", 4)), float(f.params.get("d", 4)))
        top = 0.0
    else:
        fx = fy = 0.0
        FW = FD = max([x + w for x, y, w, d in taken] + [4.0])
        top = 0.0
    step = 0.5
    y = fy + 0.2
    while y + fd <= fy + FD + 1e-6:
        x = fx + 0.2
        while x + fw <= fx + FW + 1e-6:
            if all(x + fw <= ox or ox + ow <= x or y + fd <= oy or oy + od <= y for ox, oy, ow, od in taken):
                return round(x, 2), round(y, 2), top
            x += step
        y += step
    right = max([x + w for x, y, w, d in taken] + [fx + FW])
    return round(right + 0.3, 2), round(fy, 2), top


def add_item(sc: IsoScene, kind: str, x: float | None = None, y: float | None = None, **fields) -> int:
    """Add an item (auto-placed on a free spot when x / y are not given). Returns its index."""
    it = IsoItem(kind=kind, **{k: v for k, v in fields.items() if k in ITEM_FIELDS and v is not None},
                 params=dict(fields.get("params") or {}))
    if x is None or y is None:
        fx, fy, fz = free_spot(sc, kind, it.size, it.rot)
        x = fx if x is None else x
        y = fy if y is None else y
    it.x, it.y = float(x), float(y)
    sc.items.append(_check(it))
    return len(sc.items) - 1


def set_item(sc: IsoScene, index: int, params: dict | None = None, **fields) -> IsoItem:
    if not 0 <= index < len(sc.items):
        raise VeditError(f"No item #{index} (the scene has {len(sc.items)}: 0-{len(sc.items) - 1})")
    it = sc.items[index]
    for k, v in fields.items():
        if k in ITEM_FIELDS and v is not None:
            setattr(it, k, (None if v in ("", "none") else v) if k in ("color", "color2", "label") else v)
    if fields.get("kind"):
        it.kind = fields["kind"]
    if params:
        it.params = {**it.params, **{k: v for k, v in params.items() if v is not None}}
    return _check(it)


def remove_item(sc: IsoScene, index: int) -> IsoItem:
    if not 0 <= index < len(sc.items):
        raise VeditError(f"No item #{index}")
    return sc.items.pop(index)


def set_scene(sc: IsoScene, theme: str | None = None, light: float | None = None, outline: bool | None = None,
              build: str | None = None) -> None:
    if theme:
        _theme(theme)
        sc.theme = theme
    if light is not None:
        sc.light = max(0.0, min(0.6, float(light)))
    if outline is not None:
        sc.outline = "#00000030" if outline else None
    if build:
        if build not in ("drop", "grow", "pop"):
            raise VeditError("build: drop | grow | pop")
        sc.build = build


def parse_params(pairs: list[str] | None) -> dict:
    """k=v pairs -> params (numbers and true / false converted)."""
    out = {}
    for p in pairs or []:
        k, sep, v = p.partition("=")
        if not sep:
            raise VeditError(f"Use name=value for item settings, got '{p}'")
        v = v.strip()
        if v.lower() in ("true", "false"):
            out[k.strip()] = v.lower() == "true"
        else:
            try:
                out[k.strip()] = float(v) if "." in v else int(v)
            except ValueError:
                out[k.strip()] = v
    return out


def thumb(kind: str, theme: str = "pastel", size: int = 160) -> bytes:
    """PNG of one item on its own (the editor's item picker)."""
    import io

    from ..project import Layer
    from .iso import render_iso

    img = render_iso(Layer(id="t", type="iso", width=size, iso=IsoScene(items=[IsoItem(kind=kind)], theme=theme)), 1.0)
    img.thumbnail((size, size))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()

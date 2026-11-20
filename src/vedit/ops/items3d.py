"""3D object recipes: every object is built from code (no model files), from its settings, so the AI can make
variations by changing numbers and colours (`vedit 3d kinds` lists them).

A recipe is fn(params, colors, t) -> Mesh, with t = seconds (legs walk, wings flap, wheels and propellers turn while
`gait` is on). Front = +x (vehicles drive / animals walk that way at yaw 0), up = +y. Sizes are in rough metres;
the renderer scales every object to its layer's width.
"""

from __future__ import annotations

import math
from typing import Callable

from ..project import VeditError
from .mesh3d import Mesh, box, cone, cylinder, ellipsoid, extrude, frustum, heart_points, rx, ry, rz, sphere, star_points

DARK = "#2B2D42"
GLASS = "#A9DDF7"
WHITE = "#FFFFFF"


def _p(params: dict, key: str, default):
    v = params.get(key, default)
    if isinstance(default, bool):
        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        try:
            return type(default)(float(v))
        except (TypeError, ValueError):
            raise VeditError(f"3D setting {key} must be a number, got {v!r}") from None
    return str(v)


# ---- vehicles -------------------------------------------------------------------

def _wheels(m: Mesh, xs: list[float], half_w: float, r: float, t: float, speed: float, tyre: str = DARK, hub: str = "#D7DBE4"):
    spin = -math.degrees(t * speed / max(r, 0.05))  # rolling, without slipping, at `speed` m/s
    for x in xs:
        for side in (-1, 1):
            w = Mesh().add(cylinder(r, 0.32, tyre, seg=10), rx(90), (0, 0, -0.16))
            w.add(cylinder(r * 0.5, 0.34, hub, seg=6), rx(90), (0, 0, -0.17))
            m.add(w, rz(spin), (x, r, side * (half_w - 0.12)))


def _car(p: dict, c: tuple[str, str], t: float) -> Mesh:
    L = _p(p, "length", 4.0)
    W = _p(p, "width", 1.9)
    style = _p(p, "style", "sedan")  # sedan | sport | van
    r = 0.42 if style != "sport" else 0.38
    m = Mesh()
    body_h = 0.75 if style != "van" else 1.5
    m.add(box(L, body_h, W, c[0]), at=(0, r + 0.05 + body_h / 2 - 0.1, 0))
    top = r + body_h - 0.05
    if style == "van":
        m.add(extrude([(-L / 2 + 0.2, 0), (L / 2 - 0.9, 0), (L / 2 - 0.2, -0.0), (L / 2 - 0.5, 0.55), (-L / 2 + 0.2, 0.55)], W - 0.08,
                      c[1] or GLASS), at=(0, top, 0))
        m.add(box(L - 0.6, 0.12, W - 0.02, c[0]), at=(-0.1, top + 0.6, 0))
    else:
        cab_len = L * (0.5 if style == "sedan" else 0.42)
        h = 0.62 if style == "sedan" else 0.48
        back, front = -cab_len / 2 - 0.15, cab_len / 2 + 0.25
        off = -L * 0.09  # cabin sits back: long hood in front, so you can tell which way it faces
        m.add(extrude([(back, 0), (front, 0), (front - 0.75, h), (back + 0.2, h)], W - 0.18, c[1] or GLASS), at=(off, top, 0))
        m.add(box(front - back - 0.95, 0.1, W - 0.12, c[0]), at=(off + (back + 0.2 + front - 0.75) / 2, top + h + 0.04, 0))
    m.add(box(0.08, 0.26, W * 0.5, DARK), at=(L / 2 + 0.02, r + 0.3, 0))  # grille
    m.add(box(0.14, 0.14, W + 0.04, "#C9CED8"), at=(L / 2 + 0.03, r + 0.08, 0))  # bumpers
    m.add(box(0.14, 0.14, W + 0.04, "#C9CED8"), at=(-L / 2 - 0.03, r + 0.08, 0))
    if _p(p, "spoiler", style == "sport"):
        m.add(box(0.35, 0.08, W - 0.1, DARK), at=(-L / 2 + 0.2, top + 0.42, 0))
        for z in (-W / 3, W / 3):
            m.add(box(0.08, 0.36, 0.08, DARK), at=(-L / 2 + 0.25, top + 0.2, z))
    for z in (-W / 2 + 0.3, W / 2 - 0.3):
        m.add(box(0.06, 0.16, 0.34, "#FFF3B0"), at=(L / 2 + 0.02, r + 0.42, z))
        m.add(box(0.06, 0.14, 0.34, "#FF4D4D"), at=(-L / 2 - 0.02, r + 0.42, z))
    _wheels(m, [L / 2 - 0.8, -L / 2 + 0.8], W / 2, r, t, _p(p, "speed", 6.0))
    return m


def _truck(p: dict, c: tuple[str, str], t: float) -> Mesh:
    L = _p(p, "length", 6.0)
    m = Mesh()
    r = 0.5
    m.add(box(L * 0.68, 2.0, 2.2, c[1] or WHITE), at=(-L * 0.16, r + 1.15, 0))  # cargo box
    m.add(box(L * 0.28, 1.6, 2.1, c[0]), at=(L * 0.34, r + 0.95, 0))  # cab
    m.add(box(0.05, 0.6, 1.8, GLASS), at=(L * 0.48 + 0.01, r + 1.35, 0))
    m.add(box(L, 0.25, 1.6, DARK), at=(0, r + 0.1, 0))
    _wheels(m, [L * 0.36, -L * 0.1, -L * 0.36], 1.1, r, t, _p(p, "speed", 5.0))
    return m


def _bus(p: dict, c: tuple[str, str], t: float) -> Mesh:
    L = _p(p, "length", 8.0)
    m = Mesh()
    r = 0.5
    m.add(box(L, 2.4, 2.4, c[0]), at=(0, r + 1.3, 0))
    n = max(2, int(L / 1.4))
    for i in range(n):
        x = -L / 2 + 0.9 + i * (L - 1.8) / max(1, n - 1)
        for z in (-1.21, 1.21):
            m.add(box(0.95, 0.75, 0.04, c[1] or GLASS), at=(x, r + 1.9, z))
    m.add(box(0.05, 1.0, 2.0, c[1] or GLASS), at=(L / 2 + 0.01, r + 1.8, 0))
    _wheels(m, [L / 2 - 1.3, -L / 2 + 1.5], 1.2, r, t, _p(p, "speed", 5.0))
    return m


def _plane(p: dict, c: tuple[str, str], t: float) -> Mesh:
    m = Mesh()
    L = _p(p, "length", 4.0)
    m.add(cylinder(0.42, L, c[0], seg=10), rz(-90), (-L / 2, 0, 0))  # fuselage along x
    m.add(cone(0.42, 0.7, c[0], seg=10), rz(-90), (L / 2, 0, 0))
    m.add(cone(0.42, 1.0, c[0], seg=10), rz(90), (-L / 2, 0, 0))
    m.add(box(1.0, 0.08, _p(p, "span", 5.6), c[1] or WHITE), at=(0.2, -0.05, 0))
    m.add(box(0.6, 0.06, 2.0, c[1] or WHITE), at=(-L / 2 - 0.5, 0.05, 0))
    m.add(extrude([(-0.5, 0), (0.2, 0), (-0.25, 0.9), (-0.55, 0.9)], 0.08, c[1] or WHITE), at=(-L / 2 - 0.5, 0.2, 0))
    m.add(box(0.9, 0.3, 0.86, GLASS), at=(L / 2 - 0.9, 0.35, 0))
    prop = Mesh().add(box(0.06, 1.5, 0.14, DARK)).add(box(0.06, 0.14, 1.5, DARK))
    m.add(prop, rx(t * 1500 % 360 if _p(p, "propeller", True) else 0), (L / 2 + 0.72, 0, 0))
    return m


def _rocket(p: dict, c: tuple[str, str], t: float) -> Mesh:
    m = Mesh()
    H = _p(p, "height", 4.0)
    m.add(cylinder(0.55, H, c[0] or WHITE, seg=10), at=(0, 0, 0))
    m.add(cone(0.55, 1.2, c[1], seg=10), at=(0, H, 0))
    for a in range(0, 360, 120):
        m.add(extrude([(0.4, 0), (1.15, -0.25), (1.05, 0.5), (0.4, 1.1)], 0.1, c[1]), ry(a), (0, 0, 0))
    m.add(cylinder(0.22, 0.08, GLASS, seg=10), rx(90), (0, H * 0.65, 0.56))
    if _p(p, "flame", True):
        f = 0.9 + 0.25 * math.sin(t * 40) + 0.1 * math.sin(t * 23)
        m.add(cone(0.4, 1.3 * f, "#FFB020", seg=8), rx(180), (0, 0, 0))
        m.add(cone(0.22, 0.8 * f, "#FFF1A8", seg=6), rx(180), (0, -0.05, 0))
    return m


# ---- animals --------------------------------------------------------------------

def _legs(m: Mesh, spots: list[tuple[float, float]], hip: float, length: float, r: float, color: str, t: float, period: float,
          swing: float, foot: str | None = None):
    for i, (x, z) in enumerate(spots):
        phase = 0.0 if i in (0, 3) else math.pi  # diagonal pairs move together (trot)
        a = math.sin(2 * math.pi * t / period + phase) * swing
        leg = Mesh().add(cylinder(r, length, color, seg=6), at=(0, -length, 0))
        if foot:
            leg.add(sphere(r * 1.15, foot, seg=6, rings=4), at=(0.04, -length, 0))
        m.add(leg, rz(a), (x, hip, z))


def _quadruped(p: dict, c: tuple[str, str], t: float, cat: bool) -> Mesh:
    m = Mesh()
    fur, belly = c[0], c[1]
    period = _p(p, "stride", 0.5 if not cat else 0.6)
    walk = _p(p, "walk", True)
    tt = t if walk else 0.0
    body_len = _p(p, "body", 1.0 if not cat else 0.95)
    hip = 0.85 if not cat else 0.7
    bob = 0.03 * abs(math.sin(2 * math.pi * tt / period)) if walk else 0.0
    m.add(ellipsoid(1.05 * body_len, 0.48 if not cat else 0.4, 0.46 if not cat else 0.38, fur), at=(0, hip + 0.2 + bob, 0))
    m.add(ellipsoid(0.7 * body_len, 0.3, 0.32, belly), at=(0.05, hip + 0.02 + bob, 0))
    hx, hy = 1.0 * body_len + 0.05, hip + 0.75 + bob
    head = Mesh()
    head.add(sphere(0.42 if not cat else 0.36, fur, seg=10, rings=6))
    head.add(ellipsoid(0.26 if not cat else 0.16, 0.18 if not cat else 0.14, 0.2, belly), at=(0.36 if not cat else 0.28, -0.1, 0))
    head.add(sphere(0.08, DARK, seg=6, rings=4), at=(0.6 if not cat else 0.43, -0.05, 0))
    for z in (-0.17, 0.17):
        head.add(sphere(0.06, DARK, seg=6, rings=4), at=(0.33 if not cat else 0.3, 0.12, z * (1 if not cat else 0.85)))
    ears = _p(p, "ears", "floppy" if not cat else "pointy")
    for z in (-1, 1):
        if ears == "pointy":
            head.add(cone(0.13, 0.3, fur, seg=4), rx(-z * 12), (-0.05, 0.28, z * 0.2))
        else:
            head.add(ellipsoid(0.1, 0.26, 0.06, belly if cat else c[1], seg=6, rings=4), rx(z * 20), (-0.05, 0.05, z * 0.4))
    nod = 4 * math.sin(2 * math.pi * tt / period * 2) if walk else 0.0
    m.add(head, rz(nod), (hx, hy, 0))
    m.add(ellipsoid(0.2, 0.3, 0.26, fur, seg=8, rings=4), rz(-30), (hx - 0.3, hy - 0.35, 0))  # neck
    _legs(m, [(0.6 * body_len, -0.25), (0.6 * body_len, 0.25), (-0.6 * body_len, -0.25), (-0.6 * body_len, 0.25)], hip + 0.1,
          hip + 0.1, 0.11 if not cat else 0.09, fur, tt, period, 28 if walk else 0, foot=belly)
    tail_len = _p(p, "tail", 0.6 if not cat else 0.9)
    wag = math.sin(2 * math.pi * t / 0.35) * 30 if (_p(p, "wag", True) and not cat) else math.sin(2 * math.pi * t / 1.6) * 12
    tail = Mesh().add(cone(0.08, tail_len, fur, seg=6))
    m.add(tail, ry(wag) @ rz(35 if not cat else 15), (-1.0 * body_len, hip + 0.4, 0))
    return m


def _dog(p, c, t):
    return _quadruped(p, c, t, cat=False)


def _cat(p, c, t):
    return _quadruped(p, c, t, cat=True)


def _bird(p: dict, c: tuple[str, str], t: float) -> Mesh:
    m = Mesh()
    period = _p(p, "flap", 0.35)
    m.add(ellipsoid(0.7, 0.42, 0.42, c[0]), at=(0, 0, 0))
    m.add(ellipsoid(0.45, 0.25, 0.32, c[1]), at=(0.1, -0.14, 0))
    m.add(sphere(0.32, c[0], seg=10, rings=6), at=(0.62, 0.3, 0))
    m.add(cone(0.11, 0.34, "#FFB020", seg=6), rz(-90), (0.88, 0.28, 0))
    for z in (-0.2, 0.2):
        m.add(sphere(0.05, DARK, seg=6, rings=4), at=(0.8, 0.4, z))
    a = math.sin(2 * math.pi * t / period) * _p(p, "wing_angle", 40.0) if _p(p, "flying", True) else -70
    for side in (-1, 1):
        wing = Mesh().add(extrude([(-0.4, 0), (0.35, 0), (0.1, 1.1), (-0.5, 0.9)], 0.06, c[0]), rx(90 * side))
        m.add(wing, rx(-side * a), (0, 0.15, side * 0.3))
    m.add(extrude([(0, -0.15), (0, 0.15), (-0.6, 0.3), (-0.6, -0.25)], 0.08, c[1]), rx(90), (-0.6, 0.05, 0))
    return m


def _fish(p: dict, c: tuple[str, str], t: float) -> Mesh:
    m = Mesh()
    period = _p(p, "swim", 0.6)
    wag = math.sin(2 * math.pi * t / period) * 22
    m.add(ellipsoid(1.0, 0.55, 0.3, c[0], seg=12), ry(wag * 0.2))
    tail = Mesh().add(extrude([(0, 0), (-0.6, 0.45), (-0.5, 0), (-0.6, -0.45)], 0.06, c[1]))
    m.add(tail, ry(wag), (-0.9, 0, 0))
    m.add(extrude([(-0.3, 0), (0.3, 0), (-0.2, 0.4)], 0.05, c[1]), at=(0, 0.48, 0))
    for z in (-0.25, 0.25):
        m.add(sphere(0.08, WHITE, seg=6, rings=4), at=(0.65, 0.12, z))
        m.add(sphere(0.045, DARK, seg=6, rings=4), at=(0.7, 0.13, z * 1.15))
    return m


# ---- shapes ---------------------------------------------------------------------

def _cube(p, c, t):
    s = _p(p, "size", 1.0)
    m = box(s, s, s, c[0], outline=True)
    if c[1]:  # alternate face colours: front / back = color2
        from .text import rgba

        for i in (4, 5):
            m.faces[i].color = rgba(c[1])
    return m


def _sphere(p, c, t):
    return sphere(1.0, c[0], seg=_p(p, "segments", 14), rings=_p(p, "rings", 9))


def _gem(p, c, t):
    seg = _p(p, "facets", 8)
    crown = frustum(1.0, 0.62, 0.4, c[0], seg=seg, outline=True)
    del crown.faces[seg]  # its bottom sits on the pavilion
    pav = cone(1.0, 1.1, c[1] or c[0], seg=seg, outline=True)
    del pav.faces[-1]
    return Mesh().add(crown, at=(0, 0, 0)).add(pav, rx(180), (0, 0, 0))


def _coin(p, c, t):
    m = Mesh().add(cylinder(1.0, 0.18, c[0], seg=_p(p, "segments", 20), outline=False), rx(90), (0, 0, 0.09))
    m.add(cylinder(0.78, 0.2, c[1] or c[0], seg=_p(p, "segments", 20), outline=False), rx(90), (0, 0, 0.1))
    return m


def _heart(p, c, t):
    return extrude(heart_points(), _p(p, "depth", 0.45), c[0], c[1] or None)


def _star(p, c, t):
    return extrude(star_points(_p(p, "points", 5), _p(p, "inner", 0.45)), _p(p, "depth", 0.35), c[0], c[1] or None)


# kind -> (recipe, (color, color2) defaults, category, settings with their defaults, note)
ITEMS3D: dict[str, tuple[Callable, tuple[str, str], str, dict, str]] = {
    "car": (_car, ("#F2545B", GLASS), "vehicles", {"style": "sedan", "length": 4.0, "width": 1.9, "spoiler": False, "speed": 6.0},
            "style sedan|sport|van; speed = how fast the wheels turn"),
    "truck": (_truck, ("#2F7BF6", WHITE), "vehicles", {"length": 6.0, "speed": 5.0}, "cab = color, cargo box = color2"),
    "bus": (_bus, ("#FFC233", GLASS), "vehicles", {"length": 8.0, "speed": 5.0}, "windows = color2"),
    "plane": (_plane, ("#FFFFFF", "#2F7BF6"), "vehicles", {"length": 4.0, "span": 5.6, "propeller": True}, "wings / tail = color2"),
    "rocket": (_rocket, ("#FFFFFF", "#F2545B"), "vehicles", {"height": 4.0, "flame": True}, "points up (+y); fins = color2"),
    "dog": (_dog, ("#D9A87C", "#F6E6D2"), "animals", {"ears": "floppy", "tail": 0.6, "body": 1.0, "stride": 0.5, "walk": True, "wag": True},
            "ears floppy|pointy; stride = seconds per step"),
    "cat": (_cat, ("#8E8BA3", "#F2F0F7"), "animals", {"ears": "pointy", "tail": 0.9, "body": 0.95, "stride": 0.6, "walk": True},
            "belly / paws = color2"),
    "bird": (_bird, ("#3BA6F0", "#FFE7A8"), "animals", {"flap": 0.35, "wing_angle": 40.0, "flying": True}, "flap = seconds per wing beat"),
    "fish": (_fish, ("#FF8A3D", "#FFD27A"), "animals", {"swim": 0.6}, "tail and fins = color2"),
    "cube": (_cube, ("#8FB8FF", ""), "shapes", {"size": 1.0}, "color2 = front / back faces"),
    "sphere": (_sphere, ("#F7A8C8", ""), "shapes", {"segments": 14, "rings": 9}, "fewer segments = more faceted"),
    "gem": (_gem, ("#5EEAD4", "#2DD4BF"), "shapes", {"facets": 8}, "crown = color, pavilion = color2"),
    "coin": (_coin, ("#F8C95A", "#FFD978"), "shapes", {"segments": 20}, "flip it with spin3d axis=x"),
    "heart": (_heart, ("#F2545B", "#C81E3A"), "shapes", {"depth": 0.45}, "sides = color2"),
    "star": (_star, ("#FFC233", "#F59E0B"), "shapes", {"points": 5, "inner": 0.45, "depth": 0.35}, "sides = color2"),
}
TEXT_DEFAULTS = ("#FFD447", "#FF6B3D")
GAITED = {"car", "truck", "bus", "plane", "rocket", "dog", "cat", "bird", "fish"}  # change over time (wheels, legs ...)


def catalog() -> list[dict]:
    rows = [{"kind": "text", "category": "text", "params": {"smooth": 4, "spacing": 0.02},
             "note": "3D letters: --text, --font, --depth; front = color, sides = color2",
             "colors": list(TEXT_DEFAULTS)}]
    rows += [{"kind": k, "category": v[2], "params": v[3], "note": v[4], "colors": list(v[1])} for k, v in ITEMS3D.items()]
    rows.append({"kind": "model", "category": "import", "params": {"max_faces": 4000},
                 "note": "your own .glb / .gltf / .obj / .stl (--src); needs the 'ai' extra (trimesh)", "colors": ["#B7BED0", ""]})
    return rows


def build(kind: str, params: dict, color: str | None, color2: str | None, t: float) -> Mesh:
    if kind not in ITEMS3D:
        raise VeditError(f"Unknown 3D kind '{kind}'. Options: text, {', '.join(ITEMS3D)}, model")
    fn, (c1, c2), _cat_, defaults, _ = ITEMS3D[kind]
    unknown = set(params) - set(defaults)
    if unknown:
        raise VeditError(f"{kind} has no setting {', '.join(sorted(unknown))}. Settings: {', '.join(f'{k}={v}' for k, v in defaults.items())}")
    return fn(params, (color or c1, color2 if color2 is not None else c2), t)

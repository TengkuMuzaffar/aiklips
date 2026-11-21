"""Safe zones of the social apps, and where each layer really is on screen, so placement is measured by code instead
of guessed: `vedit platform`, `vedit safe check|fix`, the audit's safe-zone check, `vedit frame --guides` and the
editor's guides all use this one table.

A zone is the part of a 9:16 frame the app's own interface leaves visible: its top bar, the caption / sound bar at
the bottom and the like / comment / share column on the right cover the rest. The fractions are approximate (the apps
change their layouts now and then) and match the editor's guides (`SAFE` in static/index.html; a test keeps them equal).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from ..project import Layer, Project, VeditError, parse_pos

# canvas fractions covered by each app's interface (top, bottom, left, right) + what covers each side
PLATFORMS: dict[str, dict] = {
    "tiktok": {"name": "TikTok", "top": 0.083, "bottom": 0.25, "left": 0.055, "right": 0.13,
               "covers": ["Following / For You tabs", "caption and sound", "like / comment / share buttons"]},
    "reels": {"name": "Instagram Reels", "top": 0.115, "bottom": 0.22, "left": 0.055, "right": 0.11,
              "covers": ["Reels header", "username, caption and audio", "like / comment / share buttons"]},
    "shorts": {"name": "YouTube Shorts", "top": 0.08, "bottom": 0.2, "left": 0.055, "right": 0.13,
               "covers": ["search / menu", "title, channel and sound", "like / dislike / comment buttons"]},
    "all": {"name": "all apps", "top": 0.12, "bottom": 0.25, "left": 0.08, "right": 0.12,
            "covers": ["the apps' top bars", "captions and music", "the apps' buttons"]},
}
TITLE_SAFE = 0.05  # landscape / square: plain title-safe margin on every side
SLACK = 0.02  # a box may poke this far (fraction of the canvas) past a zone edge before it counts
CAPTION_SLACK = 0.07  # captions may sit a little lower (just over the app's caption bar)


@dataclass
class Zone:
    key: str  # tiktok | reels | shorts | all | title
    name: str
    x0: float  # canvas px
    y0: float
    x1: float
    y1: float
    covers: list[str]

    def describe(self, W: int, H: int) -> str:
        if self.key == "title":
            return f"title-safe area x {self.x0:.0f}-{self.x1:.0f}px, y {self.y0:.0f}-{self.y1:.0f}px (5% margins)"
        top, bottom, right = self.covers
        return (f"{self.name} safe area x {self.x0:.0f}-{self.x1:.0f}px, y {self.y0:.0f}-{self.y1:.0f}px "
                f"(x {self.x0 / W:.0%}-{self.x1 / W:.0%}, y {self.y0 / H:.0%}-{self.y1 / H:.0%}); "
                f"covered: above y {self.y0:.0f} = {top}, below y {self.y1:.0f} = {bottom}, right of x {self.x1:.0f} = {right}")


def zone(p: Project, platform: str | None = None) -> Zone:
    """The zone to keep things in: the project's platform (or the one given); a portrait canvas with none set uses
    "all apps" (the strictest), landscape / square a plain 5% title-safe margin."""
    W, H = p.canvas.width, p.canvas.height
    key = platform or p.platform
    if key in (None, "", "all") and H <= W:
        m = TITLE_SAFE
        return Zone("title", "title safe", W * m, H * m, W * (1 - m), H * (1 - m), [])
    z = PLATFORMS[key or "all"]
    return Zone(key or "all", z["name"], W * z["left"], H * z["top"], W * (1 - z["right"]), H * (1 - z["bottom"]),
                z["covers"])


def set_platform(p: Project, platform: str | None) -> str | None:
    key = (platform or "").strip().lower() or None
    if key in ("none", "off"):
        key = None
    aliases = {"instagram": "reels", "ig": "reels", "youtube": "shorts", "yt": "shorts", "tt": "tiktok"}
    key = aliases.get(key, key)
    if key is not None and key not in PLATFORMS:
        raise VeditError(f"Platform must be one of: {', '.join(PLATFORMS)}, or none")
    p.platform = key
    return key


# ---- where a layer is ---------------------------------------------------------------------------------------

MEASURE = 0.25  # sprites are measured at 1/4 size (fast; a few px of error at most)


def _rotated(cx: float, cy: float, w: float, h: float, deg: float) -> tuple[float, float, float, float]:
    a = math.radians(deg or 0.0)
    hw = (abs(w * math.cos(a)) + abs(h * math.sin(a))) / 2
    hh = (abs(w * math.sin(a)) + abs(h * math.cos(a))) / 2
    return cx - hw, cy - hh, cx + hw, cy + hh


def is_background(p: Project, l: Layer) -> bool:
    """Full-frame layers (a colour screen, b-roll filling the frame, textures): meant to cover the whole canvas, so the
    safe zone doesn't apply to them."""
    W, H = p.canvas.width, p.canvas.height
    if l.type == "adjust" or (l.type == "video" and l.fit in ("cover", "contain")):
        return True
    if l.type == "shape" and (l.width or 0) * l.scale * l.sx >= W * 0.9 and (l.height or 0) * l.scale * l.sy >= H * 0.9:
        return True
    if l.type == "video" and l.width and l.width * l.scale >= W * 0.95:
        return True
    return False


def layer_box(p: Project, pdir: Path | None, l: Layer) -> tuple[float, float, float, float] | None:
    """Where the layer's ink is at rest (its own position / scale / stretch / rotation, before animation), in canvas
    px (x0, y0, x1, y1); None when it can't be measured (missing media, empty text)."""
    W, H = p.canvas.width, p.canvas.height
    cx, cy = parse_pos(l.x, W), parse_pos(l.y, H)
    k = l.scale
    if l.type == "text":
        from ..audit import text_box

        b = text_box(p, l)
        if b is None:
            return None
        # text_box is at scale, without stretch / rotation: apply those about the centre
        x0, y0, x1, y1 = b[0] * W, b[1] * H, b[2] * W, b[3] * H
        bw, bh = (x1 - x0) * l.sx, (y1 - y0) * l.sy
        bx, by = cx + ((x0 + x1) / 2 - cx) * l.sx, cy + ((y0 + y1) / 2 - cy) * l.sy
        return _rotated(bx, by, bw, bh, l.rotation)
    if l.type == "video":
        if l.source not in p.sources:
            return None
        from ..render.layers import video_size

        w, h = video_size(p, l, 1.0)
        return _rotated(cx, cy, w * k * l.sx, h * k * l.sy, l.rotation)
    if l.type == "glass":
        return _rotated(cx, cy, (l.width or 600) * k * l.sx, (l.height or 300) * k * l.sy, l.rotation)
    if l.type in ("shape", "image", "chart", "iso", "obj3d"):
        from .overlay_style import render_shape, style_frame
        from .text import render_image

        try:
            if l.type == "chart":
                from .chart import render_chart

                img = render_chart(l, MEASURE)
            elif l.type == "iso":
                from .iso import render_iso

                img = render_iso(l, MEASURE)
            elif l.type == "obj3d":
                from .render3d import render_obj3d

                img = render_obj3d(l, MEASURE, pdir=pdir)
            elif l.type == "shape":
                img = render_shape(l, MEASURE)
            else:
                if not l.image or pdir is None:
                    return None
                from ..project import resolve_media

                img = style_frame(render_image(resolve_media(pdir, l.image), l.width, MEASURE), l, MEASURE)
        except (VeditError, OSError):
            return None
        bb = img.getchannel("A").getbbox() if img.mode == "RGBA" else img.getbbox()
        if not bb:
            return None
        # the sprite is centred on (x, y): ink offsets from its centre, then scale / stretch / rotate
        ox = ((bb[0] + bb[2]) / 2 - img.width / 2) / MEASURE * k * l.sx
        oy = ((bb[1] + bb[3]) / 2 - img.height / 2) / MEASURE * k * l.sy
        w, h = (bb[2] - bb[0]) / MEASURE * k * l.sx, (bb[3] - bb[1]) / MEASURE * k * l.sy
        return _rotated(cx + ox, cy + oy, w, h, l.rotation)
    return None


# ---- check / fix ----------------------------------------------------------------------------------------------

def _bottom(z: Zone, l: Layer, H: int) -> float:
    return z.y1 + (CAPTION_SLACK * H if l.type == "text" and l.words else 0)  # a caption phrase


def problems(p: Project, pdir: Path | None, l: Layer, z: Zone) -> tuple[list[str], float, tuple | None]:
    """What pokes out of the zone (readable phrases), the worst overshoot (canvas fraction), and the box."""
    W, H = p.canvas.width, p.canvas.height
    b = layer_box(p, pdir, l)
    if b is None:
        return [], 0.0, None
    x0, y0, x1, y1 = b
    cov = z.covers or ["the top edge", "the bottom edge", "the right edge"]
    out, worst = [], 0.0
    for over, text in (((z.x0 - x0) / W, f"left edge at x {x0:.0f}px (zone starts at {z.x0:.0f})"),
                       ((x1 - z.x1) / W, f"right edge at x {x1:.0f}px, under {cov[2]} (zone ends at {z.x1:.0f})"),
                       ((z.y0 - y0) / H, f"top at y {y0:.0f}px, under {cov[0]} (zone starts at {z.y0:.0f})"),
                       ((y1 - _bottom(z, l, H)) / H, f"bottom at y {y1:.0f}px, under {cov[1]} (zone ends at {z.y1:.0f})")):
        if over > SLACK:
            out.append(text)
            worst = max(worst, over)
    return out, worst, b


def checked_layers(p: Project) -> list[Layer]:
    """Layers the safe zone applies to: visible, not full-frame backgrounds."""
    return [l for l in p.layers if not l.hidden and not is_background(p, l)]


def check(p: Project, pdir: Path | None, platform: str | None = None) -> dict:
    z = zone(p, platform)
    W, H = p.canvas.width, p.canvas.height
    rows = []
    for l in checked_layers(p):
        probs, worst, b = problems(p, pdir, l, z)
        if b is None:
            continue
        rows.append({"layer": l.id, "type": l.type, "start": l.start, "end": l.end, "ok": not probs,
                     "box": [round(v) for v in b], "problems": probs, "worst": round(worst, 3),
                     "cut_off": b[0] < 0 or b[1] < 0 or b[2] > W or b[3] > H})
    return {"platform": z.key, "zone": {"x0": round(z.x0), "y0": round(z.y0), "x1": round(z.x1), "y1": round(z.y1)},
            "describe": z.describe(W, H), "layers": rows, "ok": all(r["ok"] for r in rows)}


def fit(p: Project, pdir: Path | None, l: Layer, platform: str | None = None, margin: float = 12.0) -> dict | None:
    """Move (and if it's too big, shrink) a layer so its box sits inside the zone, `margin` px from its edges.
    Keeps its size when it fits; position keyframes move along. Returns what changed, or None if it was fine."""
    z = zone(p, platform)
    H = p.canvas.height
    b = layer_box(p, pdir, l)
    if b is None:
        return None
    probs, _, _ = problems(p, pdir, l, z)
    if not probs:
        return None
    x0, y0, x1, y1 = b
    zx0, zy0, zx1, zy1 = z.x0 + margin, z.y0 + margin, z.x1 - margin, _bottom(z, l, H) - margin
    changed: dict = {}
    f = min(1.0, (zx1 - zx0) / max(1.0, x1 - x0), (zy1 - zy0) / max(1.0, y1 - y0))
    cx, cy = parse_pos(l.x, p.canvas.width), parse_pos(l.y, H)
    if f < 0.999:  # too big for the zone: shrink about the centre
        l.scale = round(l.scale * f, 3)
        changed["scale"] = l.scale
        x0, x1 = cx + (x0 - cx) * f, cx + (x1 - cx) * f
        y0, y1 = cy + (y0 - cy) * f, cy + (y1 - cy) * f
    dx = (zx0 - x0) if x0 < zx0 else (zx1 - x1) if x1 > zx1 else 0.0
    dy = (zy0 - y0) if y0 < zy0 else (zy1 - y1) if y1 > zy1 else 0.0
    if dx or dy:
        l.x, l.y = float(round(cx + dx)), float(round(cy + dy))  # Layer.x / y are float px or "NN%"
        changed.update(x=l.x, y=l.y)
        for kf in l.keyframes:  # an animated path moves with it
            if kf.x is not None:
                kf.x = round(parse_pos(kf.x, p.canvas.width) + dx)
            if kf.y is not None:
                kf.y = round(parse_pos(kf.y, H) + dy)
    return changed or None


def guides_image(path: Path, p: Project, platform: str | None = None) -> None:
    """Draw the zone on a saved frame (JPG): the covered parts tinted red, the zone outlined, for the AI to look at."""
    from PIL import Image, ImageDraw

    z = zone(p, platform)
    img = Image.open(path).convert("RGB")
    k = img.width / p.canvas.width
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    X0, Y0, X1, Y1 = z.x0 * k, z.y0 * k, z.x1 * k, z.y1 * k
    tint = (255, 30, 70, 120)  # strong enough to read over bright footage
    for box in ((0, 0, img.width, Y0), (0, Y1, img.width, img.height), (X1, Y0, img.width, Y1), (0, Y0, X0, Y1)):
        d.rectangle(box, fill=tint)
    d.rectangle((X0, Y0, X1, Y1), outline=(255, 30, 70, 255), width=max(2, round(img.width / 200)))
    Image.alpha_composite(img.convert("RGBA"), over).convert("RGB").save(path, quality=90)

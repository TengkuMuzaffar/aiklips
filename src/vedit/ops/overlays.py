"""Overlay layers beyond text: video (picture-in-picture, b-roll on top, textures), shapes, stacking order, frame
styling, and a small built-in library (light leaks, dust, grain, bokeh, VHS lines, frames, stickers).

Library media is generated on first use with FFmpeg / Pillow (no binary assets in the repo) into
~/.aiklips/overlays, then copied into the project's media/overlays so the project stays self-contained.
"""

from __future__ import annotations

import random
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import media
from . import shapes
from ..project import BLEND_MODES, SHAPE_KINDS, Layer, Project, Source, VeditError, normalize_tracks, spans_overlap, store_media_path

# ---- adding / changing layers ----------------------------------------------------------------------------------


def _end(p: Project, start: float, want: float | None) -> float | None:
    if want is None:
        return None
    # inside the timeline: up to its end; past it (or an empty timeline) the layer makes the timeline longer
    return round(min(p.duration, start + want), 3) if p.duration > start else round(start + want, 3)


def add_source(p: Project, pdir: Path, path: Path) -> str:
    """Register a media file as a project source (reusing an existing one for the same file)."""
    stored = store_media_path(pdir, path)
    for sid, s in p.sources.items():
        if s.path == stored:
            return sid
    sid = p.new_id("s")
    p.sources[sid] = Source(path=stored, **media.probe(path))
    return sid


def add_video(p: Project, source: str, start: float = 0.0, end: float | None = None, **kw: Any) -> Layer:
    """A video layer from a project source. Default: a picture-in-picture, lower right, as long as the source
    (or until the timeline ends)."""
    if source not in p.sources:
        raise VeditError(f"Unknown source '{source}'. Sources: {', '.join(p.sources) or 'none'}")
    src = p.sources[source]
    if not src.has_video:
        raise VeditError(f"Source {source} has no picture")
    src_in = float(kw.pop("src_in", 0.0) or 0.0)
    if end is None and not kw.get("loop"):
        end = round(min(p.duration, start + max(0.1, src.duration - src_in)), 3)
    layer = Layer(id=p.new_id("v"), type="video", source=source, src_in=src_in, start=start, end=end,
                  x=kw.pop("x", "70%"), y=kw.pop("y", "62%"), **kw)
    p.layers.append(layer)
    set_fields(layer, {})  # validate
    return layer


SHAPE_DEFAULTS: dict[str, dict[str, Any]] = shapes.DEFAULTS  # per kind; one definition in ops/shapes.py


def shape_thumb(shape: str, extra: dict | None = None, size: tuple[int, int] = (320, 200)) -> bytes:
    """PNG preview of a shape with its default look, on a mid-grey card (dark boxes and white lines both show)."""
    import io

    from PIL import Image

    from .overlay_style import render_shape

    if shape not in SHAPE_DEFAULTS:
        raise VeditError(f"Shape must be one of: {', '.join(SHAPE_DEFAULTS)}")
    fields = {k: v for k, v in {**SHAPE_DEFAULTS[shape], **(extra or {})}.items() if k in Layer.model_fields and k not in ("x", "y", "presets")}
    img = render_shape(Layer(id="preview", type="shape", shape=shape, **fields), 1.0)
    W, H = size
    img.thumbnail((int(W * 0.8), int(H * 0.7)))
    bg = Image.new("RGBA", size, (92, 100, 112, 255))
    bg.alpha_composite(img, ((W - img.width) // 2, (H - img.height) // 2))
    buf = io.BytesIO()
    bg.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def add_shape(p: Project, shape: str = "rect", start: float = 0.0, end: float | None = None, **kw: Any) -> Layer:
    if shape not in SHAPE_KINDS:
        raise VeditError(f"Shape must be one of: {', '.join(SHAPE_KINDS)}")
    kw = {**SHAPE_DEFAULTS[shape], **kw}
    layer = Layer(id=p.new_id("sh"), type="shape", shape=shape, start=start,
                  end=end if end is not None else _end(p, start, 3.0), **kw)
    p.layers.append(layer)
    return layer


def duplicate_layer(p: Project, layer_id: str, dx: float | str = 40, dy: float | str = 40) -> Layer:
    """A copy of a layer, nudged by dx / dy canvas px (0 = same place), drawn on top."""
    import re

    from ..project import parse_pos

    src = p.layer(layer_id)
    prefix = re.sub(r"\d+$", "", src.id) or "l"
    copy = src.model_copy(deep=True, update={"id": p.new_id(prefix), "track": None})
    for k, d, total in (("x", dx, p.canvas.width), ("y", dy, p.canvas.height)):
        if float(d or 0):
            setattr(copy, k, round(parse_pos(getattr(src, k), total) + float(d), 1))
    p.layers.append(copy)
    return copy


# fields of the overlay frame / video / shape that `layer_set` and `vedit layer style` can change
FRAME_FIELDS: dict[str, Callable[[Any], Any]] = {
    "blend": str, "radius": lambda v: max(0, int(float(v))), "border_width": lambda v: max(0, int(float(v))),
    "border_color": str, "mask": str, "chroma": lambda v: (str(v) or None) if v not in (None, "", "none") else None,
    "chroma_similarity": lambda v: min(0.9, max(0.01, float(v))), "src_in": lambda v: max(0.0, float(v)),
    "loop": lambda v: v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on"),
    "volume": lambda v: min(2.0, max(0.0, float(v))), "shape": str,
    "height": lambda v: None if v in (None, "") else max(1, int(float(v))),
    "fill": lambda v: (str(v) or None) if v not in (None, "", "none") else None,
    "fit": str,
    "flip_h": lambda v: v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on"),
    "flip_v": lambda v: v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on"),
    "fill_gradient": lambda v: _gradient_list(v),
    "gradient_angle": lambda v: float(v) % 360,
    "glass_blur": lambda v: min(80.0, max(0.0, float(v))),
    "sides": lambda v: min(24, max(3, int(float(v)))),
    "points": lambda v: min(24, max(3, int(float(v)))),
    "inner": lambda v: min(0.95, max(0.05, float(v))),
    "sx": lambda v: max(0.01, float(v)),
    "sy": lambda v: max(0.01, float(v)),
}


def _gradient_list(v: Any) -> list[str] | None:
    """A shape gradient from the GUI ("#top,#bottom") or JSON (a list); "", "none" or one colour = off."""
    if v in (None, "", "none"):
        return None
    cols = [c.strip() for c in (v.split(",") if isinstance(v, str) else v) if str(c).strip()]
    if len(cols) < 2:
        return None
    from .animate import mix_hex

    return [mix_hex(c, c, 0) for c in cols[:6]]


def set_fields(layer: Layer, values: dict) -> list[str]:
    """Set frame / video / shape fields with type checks. Returns the names changed."""
    changed = []
    for k, v in values.items():
        if k not in FRAME_FIELDS:
            continue
        try:
            val = FRAME_FIELDS[k](v)
        except (TypeError, ValueError):
            raise VeditError(f"Bad value for {k}: {v!r}") from None
        setattr(layer, k, val)
        changed.append(k)
    if layer.blend not in BLEND_MODES:
        raise VeditError(f"Blend must be one of: {', '.join(BLEND_MODES)}")
    if layer.mask not in ("rect", "circle"):
        raise VeditError("Mask must be rect or circle")
    if layer.fit not in ("free", "cover", "contain"):
        raise VeditError("Fit must be free, cover (fill the screen) or contain (fit the screen)")
    if "fit" in changed and layer.fit != "free":  # filling / fitting the screen centres it
        layer.x, layer.y = "50%", "50%"
    if layer.shape not in SHAPE_KINDS:
        raise VeditError(f"Shape must be one of: {', '.join(SHAPE_KINDS)}")
    for k in ("border_color", "chroma", "fill"):
        val = getattr(layer, k)
        if val is not None:
            from .text import rgba

            try:
                rgba(val)
            except Exception:
                raise VeditError(f"{k} must be a colour like #FFAA00, got {val!r}") from None
    return changed


def _shift(p: Project, layer: Layer, from_track: int, under_video: bool = False) -> None:
    """Open an empty track at `from_track`: every other layer on that track or above moves up one. The video track
    keeps its tracks under it: a new track under it (below `main_z`, or right at it with `under_video`) raises it."""
    for o in p.layers:
        if o is not layer and o.track is not None and o.track >= from_track:
            o.track += 1
    if from_track < p.main_z or (from_track == p.main_z and under_video):
        p.main_z += 1


def _free(p: Project, layer: Layer, track: int, start: float, end: float | None) -> bool:
    span = (start, float("inf") if end is None else end)
    return not any(o.track == track and spans_overlap(span, (o.start, float("inf") if o.end is None else o.end))
                   for o in p.layers if o is not layer)


def place_layer(p: Project, layer_id: str, track: int, start: float | None = None, end: float | None = ...,
                new_track: bool = False, under_video: bool = False) -> Layer:
    """Put a layer on a timeline track (0 = bottom overlay track, higher = on top), optionally at new times.

    `new_track` opens a new track at that position (the ones from there up move up one); at the video track's height
    (`main_z`) it opens above the video unless `under_video`. Dropped on a track where it would overlap another layer,
    it goes on a new track right above that one instead, like CapCut (on the same side of the video)."""
    normalize_tracks(p)
    layer = p.layer(layer_id)
    if layer.on_video:  # off the video track, onto a track row: start from the row just over the video
        layer.on_video, layer.track = False, p.main_z
    if start is not None:
        layer.start = max(0.0, float(start))
    if end is not ...:
        layer.end = None if end is None else float(end)
    if layer.end is not None and layer.end - layer.start < 0.05:
        raise VeditError("Layer end must be after its start")
    n = 1 + max((o.track for o in p.layers if o is not layer), default=-1)
    track = max(0, min(n, int(track)))
    if new_track:
        _shift(p, layer, track, under_video)
    elif not _free(p, layer, track, layer.start, layer.end):
        track += 1
        _shift(p, layer, track, under_video=track - 1 < p.main_z)
    layer.track = track
    normalize_tracks(p)
    return layer


def move_layer(p: Project, layer_id: str, to: str | int) -> int:
    """Change stacking order: up / down / front / back, or a track number (0 = bottom).

    Moves the layer one track up or down (to a new track when the one there is busy at that time), or above / below
    every other layer. Returns the layer's new index in project.layers (0 = drawn first, at the bottom)."""
    normalize_tracks(p)
    layer = p.layer(layer_id)
    t = layer.track
    others = [o for o in p.layers if o is not layer]
    top = max((o.track for o in others), default=-1)
    key = str(to)
    if key in ("front", "top"):
        if others and t <= top:  # not already alone on the highest track
            layer.track = top + 1
    elif key in ("back", "bottom"):
        if any(o.track <= t for o in others):
            _shift(p, layer, 0)
            layer.track = 0
    elif key in ("up", "down"):
        target = t + 1 if key == "up" else t - 1
        if 0 <= target <= top:
            if _free(p, layer, target, layer.start, layer.end):
                layer.track = target
            elif key == "up":
                _shift(p, layer, target + 1)
                layer.track = target + 1
            else:
                _shift(p, layer, target)
                layer.track = target
        elif key == "up" and any(o.track == t for o in others):
            layer.track = t + 1  # shares the top track: give it its own track above
    else:
        try:
            target = int(to)
        except (TypeError, ValueError):
            raise VeditError("Order must be up, down, front, back or a track number") from None
        place_layer(p, layer_id, target, new_track=not _free(p, layer, max(0, target), layer.start, layer.end))
    normalize_tracks(p)
    return p.layers.index(layer)


# ---- library ------------------------------------------------------------------------------------------------


@dataclass
class Item:
    key: str
    label: str
    kind: str  # "video" | "image" | "shape"
    about: str
    tags: tuple[str, ...]
    blend: str = "normal"
    layer: dict = field(default_factory=dict)  # extra layer fields
    span: float | None = 4.0  # seconds from the playhead; None = the whole timeline
    make: Callable[[Path, int, int], None] | None = None  # (out, W, H) for generated media


def _ff(args: list[str]) -> None:
    media.run([media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", *args], "ffmpeg (overlay)")


ENC = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium", "-movflags", "+faststart"]


def _drift(out: Path, W: int, H: int, blobs: list[tuple[float, float, float, tuple[int, int, int]]], secs: float = 8.0) -> None:
    """Soft colour blobs on black, panned slowly across (a 2x-wide picture cropped over time)."""
    from PIL import Image, ImageDraw, ImageFilter

    img = Image.new("RGB", (W * 2, H), "black")
    d = ImageDraw.Draw(img)
    for fx, fy, fr, col in blobs:  # position / radius as fractions of the frame
        r = fr * W
        d.ellipse((fx * W * 2 - r, fy * H - r, fx * W * 2 + r, fy * H + r), fill=col)
    img = img.filter(ImageFilter.GaussianBlur(W / 9))
    png = out.with_suffix(".png")
    img.save(png)
    _ff(["-loop", "1", "-framerate", "30", "-t", f"{secs}", "-i", str(png),
         "-vf", f"crop={W}:{H}:'(iw-ow)*t/{secs}':0,format=yuv420p", *ENC, str(out)])
    png.unlink(missing_ok=True)


def _light_leak(warm: bool):
    if warm:
        blobs = [(0.12, 0.25, 0.55, (255, 110, 20)), (0.45, 0.8, 0.45, (255, 60, 90)), (0.8, 0.35, 0.5, (255, 190, 70))]
    else:
        blobs = [(0.15, 0.3, 0.5, (60, 120, 255)), (0.5, 0.75, 0.45, (170, 90, 255)), (0.85, 0.3, 0.5, (60, 220, 255))]

    def make(out: Path, W: int, H: int) -> None:
        _drift(out, W, H, blobs)
    return make


# textures: noise doesn't compress, so they are made small and short (they loop) and scaled up when drawn
TEX = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "30", "-preset", "medium", "-movflags", "+faststart"]


def _dust(out: Path, W: int, H: int) -> None:
    # each pixel of a small frame is a speck with a small chance, new every frame; scaled up into soft blobs
    _ff(["-f", "lavfi", "-i", f"color=black:s={W // 4}x{H // 4}:d=4:r=24,format=gray,"
         "geq=lum='if(lt(random(1)\\,0.0012)\\,255\\,0)',"
         f"scale={W // 2}:{H // 2}:flags=bicubic,gblur=sigma=0.8,format=yuv420p", *TEX, str(out)])


def _grain(out: Path, W: int, H: int) -> None:
    _ff(["-f", "lavfi", "-i", f"color=0x808080:s={W // 2}x{H // 2}:d=3:r=24,noise=alls=30:allf=t+u,hue=s=0", *TEX, str(out)])


def _bokeh(out: Path, W: int, H: int) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    rnd = random.Random(7)
    img = Image.new("RGB", (W, H * 2), "black")
    d = ImageDraw.Draw(img)
    for _ in range(60):
        r = rnd.randint(W // 40, W // 10)
        x, y = rnd.randint(0, W), rnd.randint(0, H * 2)
        c = rnd.choice([(255, 190, 120), (255, 230, 170), (180, 210, 255), (255, 150, 200)])
        a = rnd.uniform(0.25, 0.7)
        d.ellipse((x - r, y - r, x + r, y + r), fill=tuple(int(v * a) for v in c))
    img = img.filter(ImageFilter.GaussianBlur(W / 120))
    png = out.with_suffix(".png")
    img.save(png)
    _ff(["-loop", "1", "-framerate", "30", "-t", "8", "-i", str(png),
         "-vf", f"crop={W}:{H}:0:'(ih-oh)*(1-t/8)',format=yuv420p", *ENC, str(out)])
    png.unlink(missing_ok=True)


def _vhs_lines(out: Path, W: int, H: int) -> None:
    _ff(["-f", "lavfi", "-i", f"color=0x808080:s={W // 2}x{H // 2}:d=3:r=24,"
         "geq=lum='if(lt(mod(Y\\,3)\\,1)\\,92\\,140)+10*sin(Y/30+T*6)':cb=128:cr=128,noise=alls=10:allf=t", *TEX, str(out)])


def _frame(out: Path, W: int, H: int) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
    m = W // 18 * 2
    ImageDraw.Draw(img).rounded_rectangle((m, m, W * 2 - m, H * 2 - m), radius=W // 10, outline=(255, 255, 255, 255), width=W // 60 * 2)
    img.resize((W, H), Image.LANCZOS).save(out)


def _sticker(text: str, bg: str, fg: str = "#FFFFFF"):
    def make(out: Path, W: int, H: int) -> None:
        from PIL import Image, ImageDraw

        from .fonts import DEFAULT_FONT as DEFAULT
        from .text import load_font, rgba

        size = 96
        font = load_font(DEFAULT, size)
        tw = int(font.getlength(text))
        pad_x, pad_y = 56, 34
        w, h = tw + 2 * pad_x, size + 2 * pad_y
        img = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, w * 2 - 1, h * 2 - 1), radius=h, fill=rgba(bg))
        img = img.resize((w, h), Image.LANCZOS)
        ImageDraw.Draw(img).text((w / 2, h / 2), text, font=font, fill=rgba(fg), anchor="mm")
        img.save(out)
    return make


LIBRARY: dict[str, Item] = {i.key: i for i in [
    Item("light_leak", "Light leak (warm)", "video", "Warm orange glow drifting across the frame", ("leak", "flare", "film", "warm", "transition"),
         blend="screen", span=3.0, make=_light_leak(True), layer={"loop": True, "opacity": 0.85}),
    Item("light_leak_cool", "Light leak (cool)", "video", "Blue / violet glow drifting across the frame", ("leak", "flare", "cool", "dreamy"),
         blend="screen", span=3.0, make=_light_leak(False), layer={"loop": True, "opacity": 0.85}),
    Item("dust", "Film dust", "video", "Flickering specks of dust, old film", ("film", "vintage", "specks", "analog"),
         blend="screen", span=None, make=_dust, layer={"loop": True, "opacity": 0.7}),
    Item("grain", "Grain texture", "video", "Moving film grain over everything", ("film", "texture", "noise", "analog"),
         blend="overlay", span=None, make=_grain, layer={"loop": True, "opacity": 0.6}),
    Item("bokeh", "Bokeh lights", "video", "Soft out-of-focus lights floating up", ("lights", "dreamy", "sparkle", "party"),
         blend="screen", span=None, make=_bokeh, layer={"loop": True, "opacity": 0.8}),
    Item("vhs_lines", "VHS scanlines", "video", "Rolling scanlines and tape noise", ("retro", "tape", "90s", "glitch"),
         blend="overlay", span=None, make=_vhs_lines, layer={"loop": True, "opacity": 0.7}),
    Item("frame", "White frame", "image", "Rounded white border around the shot", ("border", "frame", "clean"),
         span=None, make=_frame),
    Item("subscribe", "Subscribe button", "image", "Red SUBSCRIBE button (CTA)", ("cta", "youtube", "button", "sticker"),
         span=3.0, make=_sticker("SUBSCRIBE", "#FF0033"), layer={"y": "80%", "width": 520, "presets": [{"name": "pop_in"}, {"name": "pop_out"}]}),
    Item("new_badge", "NEW badge", "image", "Yellow NEW badge", ("badge", "sticker", "sale", "launch"),
         span=3.0, make=_sticker("NEW", "#FFE600", "#111111"), layer={"x": "80%", "y": "18%", "width": 260, "rotation": -8,
                                                                  "presets": [{"name": "pop_in"}, {"name": "pulse"}]}),
    Item("arrow", "Arrow · animated", "shape", "Pops in, then wiggles to point at something (rotate it)", ("pointer", "callout", "look"),
         span=3.0, layer={"shape": "arrow", "presets": [{"name": "pop_in"}, {"name": "wiggle", "params": {"degrees": 4}}]}),
    Item("circle_callout", "Ring · pops in", "shape", "Yellow ring that pops in around a detail", ("highlight", "callout", "ring", "circle"),
         span=3.0, layer={"shape": "ellipse", "presets": [{"name": "pop_in"}]}),
    Item("lower_box", "Lower-third box · slides in", "shape", "Dark box that slides in low on screen, behind a name / title", ("lower third", "box", "background", "caption"),
         span=4.0, layer={"shape": "rect", "y": "78%", "presets": [{"name": "slide_in"}]}),
]}


_GEN_LOCK = threading.Lock()


def library_dir() -> Path:
    from ..library import home_dir

    d = home_dir() / "overlays"
    d.mkdir(parents=True, exist_ok=True)
    return d


def catalog() -> list[dict]:
    return [{"key": i.key, "label": i.label, "kind": i.kind, "about": i.about, "tags": list(i.tags), "blend": i.blend}
            for i in LIBRARY.values()]


def library_file(key: str, W: int, H: int) -> Path:
    """The generated media for a library item at this canvas aspect (made once, then cached)."""
    it = LIBRARY[key]
    s = min(1.0, 1280 / max(W, H))  # textures don't need full resolution
    w, h = max(2, int(W * s) // 2 * 2), max(2, int(H * s) // 2 * 2)
    out = library_dir() / f"{key}-{w}x{h}.{'png' if it.kind == 'image' else 'mp4'}"
    with _GEN_LOCK:  # the thumbnail and the "add" request can ask for the same file at once
        if not out.exists():
            tmp = out.with_name(f"tmp-{uuid.uuid4().hex[:8]}-{out.name}")
            try:
                it.make(tmp, w, h)  # type: ignore[misc]
                tmp.replace(out)
            finally:
                tmp.unlink(missing_ok=True)
    return out


def add_from_library(p: Project, pdir: Path, key: str, start: float = 0.0) -> Layer:
    if key not in LIBRARY:
        raise VeditError(f"Unknown overlay '{key}'. Library: {', '.join(LIBRARY)}")
    it = LIBRARY[key]
    W, H = p.canvas.width, p.canvas.height
    start = 0.0 if it.span is None else max(0.0, min(start, max(0.0, p.duration - 0.2)))
    end = None if it.span is None else _end(p, start, it.span)
    extra = dict(it.layer)
    if it.kind == "shape":
        return add_shape(p, extra.pop("shape"), start, end, **extra)
    src = library_file(key, W, H)
    dest_dir = pdir / "media" / "overlays"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if not dest.exists():
        shutil.copy2(src, dest)
    if it.kind == "image":
        layer = Layer(id=p.new_id("img"), type="image", image=store_media_path(pdir, dest), start=start, end=end,
                      blend=it.blend, **{"width": W, **extra})
        p.layers.append(layer)
        return layer
    sid = add_source(p, pdir, dest)
    return add_video(p, sid, start, end, x="50%", y="50%", width=W, blend=it.blend, **extra)

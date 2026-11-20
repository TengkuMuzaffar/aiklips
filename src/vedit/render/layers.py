"""Render overlay layers (text / image / captions / video / shape) to transparent video files.

Each layer becomes a full-canvas RGBA video (PNG-in-MOV) for its own duration,
cached by a hash of everything that affects its pixels.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from PIL import Image

from .. import media
from ..ops.animate import props_at
from ..ops.animate import ease
from ..ops.overlay_style import render_shape, style_frame, video_width
from ..ops.text import find_font, render_image, render_text
from ..project import Layer, Project, VeditError, resolve_media, state_dir

RENDER_VERSION = "7"
FRAME_BUDGET = 256 * 1024 * 1024  # bytes of distinct layer frames kept between the measuring and the encoding pass


def even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def layer_span(layer: Layer, total: float) -> tuple[float, float]:
    end = total if layer.end is None else min(layer.end, total)
    return layer.start, max(0.0, end - layer.start)


def _cache_key(project: Project, pdir: Path, layer: Layer, scale: float) -> str:
    extra = {"font": find_font(layer.style.font) if layer.type in ("text", "captions") else None, "v": RENDER_VERSION,
             "scale": scale, "fps": project.canvas.fps}
    media_path = None
    if layer.type == "image" and layer.image:
        media_path = resolve_media(pdir, layer.image)
    elif layer.type == "video" and layer.source in project.sources:
        media_path = resolve_media(pdir, project.sources[layer.source].path)
    elif layer.type == "obj3d" and layer.obj3d and layer.obj3d.src:
        media_path = resolve_media(pdir, layer.obj3d.src)
    if layer.type == "obj3d":
        from ..ops.render3d import VERSION as R3D

        extra["r3d"] = R3D
    if media_path is not None:
        extra["mtime"] = media_path.stat().st_mtime if media_path.exists() else 0
    total = project.duration
    blob = json.dumps(
        [layer.model_dump(mode="json", by_alias=True), project.canvas.model_dump(mode="json"), layer_span(layer, total), extra],
        sort_keys=True,
    )
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _caption_groups(layer: Layer) -> list[list[int]]:
    groups: list[list[int]] = []
    cur: list[int] = []
    for i, w in enumerate(layer.words):
        if cur:
            prev = layer.words[cur[-1]]
            if len(cur) >= layer.words_per_group or w.start - prev.end > 0.5 or prev.text.rstrip()[-1:] in ".?!":
                groups.append(cur)
                cur = []
        cur.append(i)
    if cur:
        groups.append(cur)
    return groups


def video_size(project: Project, layer: Layer, scale: float) -> tuple[int, int]:
    """Pixel size of a video layer's frames at this render scale (before a circle cut-out squares them)."""
    src = project.sources[layer.source]  # type: ignore[index]
    aspect = (src.height or 1080) / (src.width or 1920)
    W, H = project.canvas.width, project.canvas.height
    if layer.fit == "cover":  # fills the canvas; the decoder crops the overflow
        return even(W * scale), even(H * scale)
    if layer.fit == "contain":  # the whole picture, as big as fits
        w = W if aspect <= H / W else W * (H / W) / aspect
        return even(w * scale), even(w * aspect * scale)
    w = even(video_width(layer, project.canvas) * scale)
    if layer.mask == "circle" and aspect < 1:  # the circle's diameter is the width: scale the short side to it
        return even(w / aspect), w
    return w, even(w * aspect)


def video_look(project: Project, pdir: Path, layer: Layer, w: int, h: int, scale: float, offset: float = 0.0) -> str:
    """Colour grade + filters of a video overlay as filter text to append after its scale (or ""). `offset`: the
    filter time of the layer's first frame (0 when decoding from src_in; src_in for the whole-source preview copy)."""
    from ..ops import color, fx_filters

    grade = color.filter_chain(layer.grade, pdir)
    look = ("," + grade) if grade else ""
    _, dur = layer_span(layer, project.duration)
    return look + fx_filters.simple_chain(layer, fx_filters.Ctx(w, h, scale, project.canvas.fps), offset, dur)


def _fit_vf(layer: Layer, w: int, h: int) -> str:
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic,crop={w}:{h}" if layer.fit == "cover"
          else f"scale={w}:{h}:flags=bicubic")
    return vf + (",hflip" if layer.flip_h else "") + (",vflip" if layer.flip_v else "")


class _VideoFrames:
    """Frames of a video layer, decoded in order at their on-canvas size (FFmpeg -> raw RGB pipe)."""

    def __init__(self, project: Project, pdir: Path, layer: Layer, scale: float, dur: float):
        src = project.sources[layer.source]  # type: ignore[index]
        self.w, self.h = video_size(project, layer, scale)
        self.layer, self.scale = layer, scale
        fps = project.canvas.fps
        self.proc = subprocess.Popen(
            [media.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", *(["-stream_loop", "-1"] if layer.loop else []),
             "-ss", f"{layer.src_in:.3f}", "-t", f"{dur + 1 / fps:.3f}", "-i", str(self._media(pdir, src, scale)),
             "-vf", f"fps={fps},{_fit_vf(layer, self.w, self.h)},setsar=1"
                    + video_look(project, pdir, layer, self.w, self.h, scale) + ",format=rgb24", "-an",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self.done = False

    @staticmethod
    def _media(pdir: Path, src, scale: float) -> Path:
        """The proxy when it is big enough for this render's size (previews), else the original."""
        from .. import proxies

        if scale < 1:
            px = proxies.available(pdir, src)
            if px is not None:
                return px
        return resolve_media(pdir, src.path)

    def next(self) -> Image.Image | None:
        if self.done:
            return None
        n = self.w * self.h * 3
        buf = self.proc.stdout.read(n) if self.proc.stdout else b""  # type: ignore[union-attr]
        if len(buf) < n:  # the source ended (and doesn't loop): nothing more to show
            self.close()
            return None
        return style_frame(Image.frombytes("RGB", (self.w, self.h), buf), self.layer, self.scale)

    def close(self) -> None:
        if not self.done:
            self.done = True
            if self.proc.stdout:
                self.proc.stdout.close()
            self.proc.kill()
            self.proc.wait()


class _SpriteSource:
    def __init__(self, project: Project, pdir: Path, layer: Layer, scale: float, dur: float = 0.0):
        self.layer, self.scale, self.pdir = layer, scale, pdir
        self.cache: dict = {}
        self.video: _VideoFrames | None = None
        if layer.type == "image":
            if not layer.image:
                raise VeditError(f"Image layer {layer.id} has no image")
            self.image = style_frame(render_image(resolve_media(pdir, layer.image), layer.width, scale), layer, scale)
        if layer.type == "shape":
            self.image = render_shape(layer, scale)
        if layer.type == "video":
            self.video = _VideoFrames(project, pdir, layer, scale, dur)
        if layer.type == "captions":
            self.groups = _caption_groups(layer)
        self.kinetic = None
        if layer.type == "text" and layer.split != "none" and not layer.counter:
            from ..ops.motion import Kinetic

            self.kinetic = Kinetic(layer.text or "", layer.style, scale, layer)

    def close(self) -> None:
        if self.video:
            self.video.close()

    def shape_at(self, pr: dict) -> Image.Image:
        """A shape redrawn crisp at its animated size / fill (cached per distinct size and colour)."""
        L = self.layer
        w = L.width if pr.get("w") is None else round(pr["w"])
        h = L.height if pr.get("h") is None else round(pr["h"])
        fill = pr.get("fill", L.fill)
        draw, morph = round(pr.get("draw", 1.0), 4), round(pr.get("morph", 0.0), 4) if L.morph_to else 0.0
        if (w, h, fill, draw, morph) == (L.width, L.height, L.fill, 1.0, 0.0):
            return self.image
        key = ("shape", w, h, fill, draw, morph)
        if key not in self.cache:
            if len(self.cache) > 256:
                self.cache.clear()
            self.cache[key] = render_shape(L, self.scale, w, h, fill, draw=draw, morph=morph)
        return self.cache[key]

    def get(self, t_abs: float, reveal: float, pr: dict | None = None) -> tuple[Image.Image | None, float]:
        """Sprite for this moment + an extra scale multiplier. Video layers: call once per frame, in order."""
        L = self.layer
        if L.type == "shape":
            return (self.shape_at(pr) if pr else self.image), 1.0
        if L.type == "image":
            return self.image, 1.0
        if L.type == "video":
            return self.video.next(), 1.0  # type: ignore[union-attr]
        if L.type == "iso":
            from ..ops.iso import render_iso

            key = ("iso", round((pr or {}).get("draw", 1.0), 4))
            if key not in self.cache:
                if len(self.cache) > 256:
                    self.cache.clear()
                self.cache[key] = render_iso(L, self.scale, key[1])
            return self.cache[key], 1.0
        if L.type == "obj3d":
            from ..ops.render3d import GAIT_FPS, animated, render_obj3d

            pr = pr or {}
            q = lambda v: round(float(v) * 4) / 4  # noqa: E731  quarter degrees: a steady spin reuses frames
            gt = round((t_abs - L.start) * GAIT_FPS) / GAIT_FPS if L.obj3d and animated(L.obj3d) else 0.0
            key = ("obj3d", q(pr.get("yaw", 0)), q(pr.get("pitch", 0)), q(pr.get("roll", 0)), gt)
            if key not in self.cache:
                if len(self.cache) > 256:
                    self.cache.clear()
                self.cache[key] = render_obj3d(L, self.scale, key[1], key[2], key[3], gt, self.pdir)
            return self.cache[key], 1.0
        if L.type == "chart":
            from ..ops.chart import render_chart

            key = ("chart", round((pr or {}).get("draw", 1.0), 4))
            if key not in self.cache:
                if len(self.cache) > 256:
                    self.cache.clear()
                self.cache[key] = render_chart(L, self.scale, key[1])
            return self.cache[key], 1.0
        if L.type == "text" and self.kinetic is not None:
            return self.kinetic.frame(t_abs - L.start), 1.0
        if L.type == "text":
            from ..ops.motion import shown_text

            text = shown_text(L, (pr or {}).get("count", 1.0))
            total = sum(len(w) for w in text.split())
            key = (text, round(total * reveal))
            if key not in self.cache:
                if len(self.cache) > 512:
                    self.cache.clear()
                self.cache[key] = render_text(text, L.style, self.scale, reveal=key[1] / total if total else 1.0)
            return self.cache[key], 1.0
        # captions
        words = L.words
        for gi, g in enumerate(self.groups):
            g_start = words[g[0]].start
            g_end = words[self.groups[gi + 1][0]].start if gi + 1 < len(self.groups) else words[g[-1]].end + 0.3
            if g_start <= t_abs < g_end:
                hi = 0
                for j, wi in enumerate(g):
                    if words[wi].start <= t_abs:
                        hi = j
                key = (gi, hi)
                if key not in self.cache:
                    self.cache[key] = render_text(" ".join(words[wi].text for wi in g), L.style, self.scale, highlight=hi)
                pop = 0.86 + 0.14 * ease("back_out", (t_abs - g_start) / 0.15)
                return self.cache[key], pop
        return None, 1.0


def ensure_layer(project: Project, pdir: Path, layer: Layer, scale: float = 1.0) -> Path | None:
    """Render (or reuse) the overlay video for one layer. Returns None if it is not visible."""
    lc = layer_clip(project, pdir, layer, scale)
    return lc[0] if lc else None


def _offset(path: Path) -> tuple[int, int]:
    x, y = path.stem.rsplit("@", 1)[1].split("_")
    return int(x), int(y)


def _changed_frames(frames: list) -> str | None:
    """A `select` expression keeping only the frames that differ from the one before (plus the last, so the stream
    lasts the layer's whole time), or None when that saves nothing. The file becomes variable frame rate: FFmpeg's
    overlay keeps showing a frame until the next one arrives, so a title that holds still for 20 s is decoded and
    converted to the video's colour format once instead of 600 times."""
    n = len(frames)
    keep = [i for i in range(n) if i == 0 or i == n - 1 or frames[i] is not frames[i - 1]]
    if len(keep) > n * 0.8:
        return None
    runs: list[list[int]] = []
    for i in keep:
        if runs and i == runs[-1][1] + 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    if len(runs) > 200:  # a very long expression: not worth it
        return None
    return "+".join(f"eq(n,{a})" if a == b else f"between(n,{a},{b})" for a, b in runs)


MISS = object()  # cached_clip: not rendered yet


def _cache_place(project: Project, pdir: Path, layer: Layer, scale: float) -> tuple[str, Path, str]:
    cdir = state_dir(pdir) / "cache" / "layers"
    cdir.mkdir(parents=True, exist_ok=True)
    return _cache_key(project, pdir, layer, scale), cdir, f"{layer.id}-s{scale:g}-"


def cached_clip(project: Project, pdir: Path, layer: Layer, scale: float = 1.0):
    """layer_clip's answer when it is already rendered (a clip, or None for a layer that is never visible), else MISS."""
    if layer.type == "glass" or layer_span(layer, project.duration)[1] <= 0.01:
        return None
    key, cdir, prefix = _cache_place(project, pdir, layer, scale)
    hit = next((p for p in cdir.glob(f"{prefix}{key}@*.mov") if not p.name.endswith(".tmp.mov")), None)
    if hit is not None:
        return (hit, *_offset(hit))
    if (cdir / f"{prefix}{key}.none").exists():
        return None
    return MISS


def layer_clip(project: Project, pdir: Path, layer: Layer, scale: float = 1.0) -> tuple[Path, int, int] | None:
    """Render (or reuse) one layer as an RGBA video cropped to the part of the canvas it ever covers, and where that
    crop sits on the canvas: (file, x, y). None if the layer is never visible.

    Cropping matters: a full-canvas RGBA frame is 8 MB at 1080x1920, and piping + encoding + overlaying those for a
    small caption or shape was most of a render's time."""
    if layer.type == "glass":  # drawn from the picture under it in the graph (render/graph.py _glass_layer)
        return None
    start, dur = layer_span(layer, project.duration)
    if dur <= 0.01:
        return None
    key, cdir, prefix = _cache_place(project, pdir, layer, scale)
    hit = cached_clip(project, pdir, layer, scale)
    if hit is not MISS:
        return hit

    canvas = project.canvas
    W, H = even(canvas.width * scale), even(canvas.height * scale)
    fps = canvas.fps
    n = max(1, round(dur * fps))

    def placed():
        """Each frame's (image, x, y) in order (None = shows nothing); unchanged frames repeat the same tuple."""
        src = _SpriteSource(project, pdir, layer, scale, dur)
        try:
            last_key, last = None, None
            for i in range(n):
                fr, last_key, last = _place(src, layer, start, i / fps, dur, canvas, scale, i, last_key, last)
                yield fr
        finally:
            src.close()

    if layer.type == "video":  # decoding is one ordered pass, so no measuring pass: keep the whole canvas
        x0, y0, bw, bh, frames = 0, 0, W, H, None
    else:
        # pass 1: where does the layer ever appear? Keep the frames for pass 2 unless they get big (long kinetic text
        # makes a new image every frame); then pass 2 draws them again (sprites are cached, only transforms repeat)
        frames, kept, x0, y0, x1, y1 = [], 0, W, H, 0, 0
        for fr in placed():
            if fr is not None and (not frames or fr is not frames[-1]):
                im, x, y = fr
                x0, y0, x1, y1 = min(x0, max(0, x)), min(y0, max(0, y)), max(x1, min(W, x + im.width)), max(y1, min(H, y + im.height))
                kept += im.width * im.height * 4
            if frames is not None:
                frames.append(fr)
                if kept > FRAME_BUDGET:
                    frames = None
        if x1 <= x0 or y1 <= y0:  # never on screen (off-canvas, fully transparent...): remember that, render nothing
            for old in cdir.glob(f"{prefix}*"):
                old.unlink(missing_ok=True)
            (cdir / f"{prefix}{key}.none").touch()
            return None
        x0, y0 = x0 // 2 * 2, y0 // 2 * 2  # even offsets and sizes keep the chroma planes aligned
        bw, bh = min(W - x0, even(x1 - x0 + 1)), min(H - y0, even(y1 - y0 + 1))
        if frames is None:
            frames = placed()

    sel = _changed_frames(frames) if isinstance(frames, list) else None
    vfr = ["-vf", f"select='{sel}'", *(["-fps_mode", "passthrough"] if media.ffmpeg_major() >= 5 else ["-vsync", "0"])] if sel else []
    out = cdir / f"{prefix}{key}@{x0}_{y0}.mov"
    tmp = out.with_suffix(".tmp.mov")
    proc = subprocess.Popen(
        [
            media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{bw}x{bh}", "-r", str(fps), "-i", "-", *vfr,
            "-c:v", "qtrle", "-pix_fmt", "argb", str(tmp),
        ],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    blank = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    blank_bytes = blank.tobytes()
    try:
        prev, prev_bytes = None, blank_bytes
        for fr in (frames if frames is not None else placed()):
            if fr is None:
                prev, prev_bytes = None, blank_bytes
            elif fr is not prev:  # unchanged frames are the same tuple: reuse its bytes
                img, x, y = fr
                frame = blank.copy()
                frame.paste(img, (x - x0, y - y0))
                prev, prev_bytes = fr, frame.tobytes()
            proc.stdin.write(prev_bytes)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode() if proc.stderr else ""
    if proc.wait() != 0:
        tmp.unlink(missing_ok=True)
        raise VeditError(f"Rendering layer {layer.id} failed: {err[-800:]}")
    tmp.replace(out)
    for old in cdir.glob(f"{prefix}*"):
        if old != out and not old.name.endswith(".tmp.mov"):
            old.unlink(missing_ok=True)
    return out, x0, y0


def _place(sprites: "_SpriteSource", layer: Layer, start: float, t: float, dur: float, canvas, scale: float, i: int,
           last_key, last):
    """The layer's picture at time `t` (seconds into the layer) and its top-left on the canvas, or None when it shows
    nothing. Returns (frame, key, frame) so an unchanged frame is the same object as the one before."""
    pr = props_at(layer, t, dur, canvas)
    sprite, extra = sprites.get(start + t, pr["reveal"], pr)
    s = pr["scale"] * extra
    sx, sy = s * pr["sx"], s * pr["sy"]
    wipe = pr["reveal"] if layer.type != "text" else 1.0  # text reveals letter by letter instead
    if sprite is None or pr["opacity"] <= 0.001 or sx <= 0.001 or sy <= 0.001 or wipe <= 0.001:
        return None, None, None
    live = (sprites.kinetic is not None and t < sprites.kinetic.end) or layer.type == "video"  # new image every frame
    fkey = (id(sprite), i if live else -1, round(sx, 4), round(sy, 4), round(pr["rotation"], 2), round(pr["opacity"], 3),
            round(pr["x"], 1), round(pr["y"], 1), round(wipe, 3))
    if fkey == last_key:
        return last, last_key, last
    img = sprite
    if wipe < 0.999:  # wipe on from the left: hide everything right of the revealed part
        img = img.copy()
        cut = round(img.width * wipe)
        img.paste((0, 0, 0, 0), (cut, 0, img.width, img.height))
    if abs(sx - 1) > 1e-3 or abs(sy - 1) > 1e-3:
        img = img.resize((max(1, round(img.width * sx)), max(1, round(img.height * sy))), Image.BICUBIC)
    if abs(pr["rotation"]) > 0.01:
        img = img.rotate(-pr["rotation"], resample=Image.BICUBIC, expand=True)
    if pr["opacity"] < 0.999:
        a = img.getchannel("A").point(lambda v, o=pr["opacity"]: int(v * o))
        img = img.copy()
        img.putalpha(a)
    fr = (img, round(pr["x"] * scale - img.width / 2), round(pr["y"] * scale - img.height / 2))
    return fr, fkey, fr

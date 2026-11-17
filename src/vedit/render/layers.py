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

RENDER_VERSION = "4"


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
    w = even(video_width(layer, project.canvas) * scale)
    aspect = (src.height or 1080) / (src.width or 1920)
    if layer.mask == "circle" and aspect < 1:  # the circle's diameter is the width: scale the short side to it
        return even(w / aspect), w
    return w, even(w * aspect)


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
             "-vf", f"fps={fps},scale={self.w}:{self.h}:flags=bicubic,setsar=1", "-an",
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
        self.layer, self.scale = layer, scale
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

    def close(self) -> None:
        if self.video:
            self.video.close()

    def get(self, t_abs: float, reveal: float) -> tuple[Image.Image | None, float]:
        """Sprite for this moment + an extra scale multiplier. Video layers: call once per frame, in order."""
        L = self.layer
        if L.type in ("image", "shape"):
            return self.image, 1.0
        if L.type == "video":
            return self.video.next(), 1.0  # type: ignore[union-attr]
        if L.type == "text":
            text = L.text or ""
            total = sum(len(w) for w in text.split())
            key = round(total * reveal)
            if key not in self.cache:
                self.cache[key] = render_text(text, L.style, self.scale, reveal=key / total if total else 1.0)
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
    start, dur = layer_span(layer, project.duration)
    if dur <= 0.01:
        return None
    key = _cache_key(project, pdir, layer, scale)
    cdir = state_dir(pdir) / "cache" / "layers"
    cdir.mkdir(parents=True, exist_ok=True)
    prefix = f"{layer.id}-s{scale:g}-"
    out = cdir / f"{prefix}{key}.mov"
    if out.exists():
        return out

    canvas = project.canvas
    W, H = even(canvas.width * scale), even(canvas.height * scale)
    fps = canvas.fps
    n = max(1, round(dur * fps))
    sprites = _SpriteSource(project, pdir, layer, scale, dur)
    tmp = out.with_suffix(".tmp.mov")
    proc = subprocess.Popen(
        [
            media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
            "-c:v", "qtrle", "-pix_fmt", "argb", str(tmp),
        ],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    blank = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    blank_bytes = blank.tobytes()
    last_key, last_bytes = None, blank_bytes
    try:
        for i in range(n):
            t = i / fps
            pr = props_at(layer, t, dur, canvas)
            sprite, extra = sprites.get(start + t, pr["reveal"])
            s = pr["scale"] * extra
            if sprite is None or pr["opacity"] <= 0.001 or s <= 0.001:
                proc.stdin.write(blank_bytes)
                continue
            fkey = (id(sprite), round(s, 4), round(pr["rotation"], 2), round(pr["opacity"], 3), round(pr["x"], 1), round(pr["y"], 1))
            if fkey == last_key:
                proc.stdin.write(last_bytes)
                continue
            img = sprite
            if abs(s - 1) > 1e-3:
                img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.BICUBIC)
            if abs(pr["rotation"]) > 0.01:
                img = img.rotate(-pr["rotation"], resample=Image.BICUBIC, expand=True)
            if pr["opacity"] < 0.999:
                a = img.getchannel("A").point(lambda v, o=pr["opacity"]: int(v * o))
                img = img.copy()
                img.putalpha(a)
            frame = blank.copy()
            frame.paste(img, (round(pr["x"] * scale - img.width / 2), round(pr["y"] * scale - img.height / 2)))
            last_key, last_bytes = fkey, frame.tobytes()
            proc.stdin.write(last_bytes)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    finally:
        sprites.close()
    err = proc.stderr.read().decode() if proc.stderr else ""
    if proc.wait() != 0:
        tmp.unlink(missing_ok=True)
        raise VeditError(f"Rendering layer {layer.id} failed: {err[-800:]}")
    tmp.replace(out)
    for old in cdir.glob(f"{prefix}*.mov"):
        if old != out and not old.name.endswith(".tmp.mov"):
            old.unlink(missing_ok=True)
    return out

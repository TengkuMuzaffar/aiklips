"""Render overlay layers (text / image / captions) to transparent video files.

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
from ..ops.text import find_font, render_image, render_text
from ..project import Layer, Project, VeditError, resolve_media, state_dir

RENDER_VERSION = "2"


def even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def layer_span(layer: Layer, total: float) -> tuple[float, float]:
    end = total if layer.end is None else min(layer.end, total)
    return layer.start, max(0.0, end - layer.start)


def _cache_key(project: Project, pdir: Path, layer: Layer, scale: float) -> str:
    extra = {"font": find_font(layer.style.font) if layer.type != "image" else None, "v": RENDER_VERSION, "scale": scale}
    if layer.type == "image" and layer.image:
        p = resolve_media(pdir, layer.image)
        extra["mtime"] = p.stat().st_mtime if p.exists() else 0
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


class _SpriteSource:
    def __init__(self, project: Project, pdir: Path, layer: Layer, scale: float):
        self.layer, self.scale = layer, scale
        self.cache: dict = {}
        if layer.type == "image":
            if not layer.image:
                raise VeditError(f"Image layer {layer.id} has no image")
            self.image = render_image(resolve_media(pdir, layer.image), layer.width, scale)
        if layer.type == "captions":
            self.groups = _caption_groups(layer)

    def get(self, t_abs: float, reveal: float) -> tuple[Image.Image | None, float]:
        """Sprite for this moment + an extra scale multiplier."""
        L = self.layer
        if L.type == "image":
            return self.image, 1.0
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
    sprites = _SpriteSource(project, pdir, layer, scale)
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
    err = proc.stderr.read().decode() if proc.stderr else ""
    if proc.wait() != 0:
        tmp.unlink(missing_ok=True)
        raise VeditError(f"Rendering layer {layer.id} failed: {err[-800:]}")
    tmp.replace(out)
    for old in cdir.glob(f"{prefix}*.mov"):
        if old != out and not old.name.endswith(".tmp.mov"):
            old.unlink(missing_ok=True)
    return out

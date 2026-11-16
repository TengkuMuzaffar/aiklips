"""Overlay sprites for the editor's live preview.

The live preview plays `base.mp4` (the timeline without overlay layers) and draws each layer in the browser
from these sprites. They come from the exact renderer the export uses (`ops.text.render_text` /
`render_image`), so text looks the same; the browser only applies the per-frame transform
(position, scale, rotation, opacity) with a port of `ops.animate.props_at`.

The layer is sent by the browser rather than read from project.json, so an edit can be previewed while
the user is still dragging a slider or typing, before it is saved.
"""

from __future__ import annotations

import hashlib
import io
import json
import threading
from collections import OrderedDict
from pathlib import Path

from ..ops.text import render_image, render_text
from ..project import Layer, VeditError, resolve_media
from ..render.layers import _caption_groups

_CACHE: OrderedDict[str, bytes] = OrderedDict()
_CACHE_MAX = 600
_LOCK = threading.Lock()


def _key(pdir: Path, layer: dict, scale: float, reveal: int | None, group: int | None, hi: int | None) -> str:
    extra = None
    if layer.get("type") == "image" and layer.get("image"):
        p = resolve_media(pdir, layer["image"])
        extra = p.stat().st_mtime if p.exists() else 0
    blob = json.dumps([str(pdir), layer, round(scale, 3), reveal, group, hi, extra], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def sprite_png(pdir: Path, layer_data: dict, scale: float = 0.5, reveal: int | None = None,
               group: int | None = None, hi: int | None = None) -> bytes:
    """PNG of one layer's sprite (tight box, drawn centred on the layer's x/y).

    text:     `reveal` = number of characters shown (typewriter); None = all
    captions: `group` = word group index (same grouping as the export), `hi` = highlighted word in it
    image:    the image at its layer width
    Returns an empty 1x1 PNG when there is nothing to draw.
    """
    scale = max(0.1, min(2.0, float(scale)))
    k = _key(pdir, layer_data, scale, reveal, group, hi)
    with _LOCK:
        if k in _CACHE:
            _CACHE.move_to_end(k)
            return _CACHE[k]
    layer = Layer.model_validate(layer_data)
    img = None
    if layer.type == "image":
        if not layer.image:
            raise VeditError(f"Image layer {layer.id} has no image")
        from ..ops.overlay_style import style_frame

        img = style_frame(render_image(resolve_media(pdir, layer.image), layer.width, scale), layer, scale)
    elif layer.type == "shape":
        from ..ops.overlay_style import render_shape

        img = render_shape(layer, scale)
    elif layer.type == "video":
        raise VeditError("Video layers are played by the browser, not drawn as sprites")
    elif layer.type == "text":
        text = layer.text or ""
        total = sum(len(w) for w in text.split())
        frac = 1.0 if reveal is None or not total else max(0, min(reveal, total)) / total
        img = render_text(text, layer.style, scale, reveal=frac)
    else:  # captions
        groups = _caption_groups(layer)
        if group is not None and 0 <= group < len(groups):
            g = groups[group]
            img = render_text(" ".join(layer.words[i].text for i in g), layer.style, scale,
                              highlight=max(0, min(hi or 0, len(g) - 1)))
    buf = io.BytesIO()
    if img is None:
        from PIL import Image

        img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    img.save(buf, "PNG", compress_level=1)  # speed over size: these never leave the machine
    data = buf.getvalue()
    with _LOCK:
        _CACHE[k] = data
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return data

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


def _key(pdir: Path, layer: dict, scale: float, reveal: int | None, group: int | None, hi: int | None, motion=None) -> str:
    extra = None
    if layer.get("type") == "image" and layer.get("image"):
        p = resolve_media(pdir, layer["image"])
        extra = p.stat().st_mtime if p.exists() else 0
    blob = json.dumps([str(pdir), layer, round(scale, 3), reveal, group, hi, extra, motion], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def sprite_png(pdir: Path, layer_data: dict, scale: float = 0.5, reveal: int | None = None,
               group: int | None = None, hi: int | None = None, motion: dict | None = None) -> bytes:
    """PNG of one layer's sprite (tight box, drawn centred on the layer's x/y).

    text:     `reveal` = number of characters shown (typewriter); None = all
    captions: `group` = word group index (same grouping as the export), `hi` = highlighted word in it
    image:    the image at its layer width
    motion:   {draw, morph, count: 0..1, kt: seconds since the layer start (kinetic text, 3D legs / wheels),
              yaw / pitch / roll: a 3D object's turn in degrees}, the moment's values
    Returns an empty 1x1 PNG when there is nothing to draw.
    """
    scale = max(0.1, min(2.0, float(scale)))
    mo = {k: round(max(0.0, min(1.0 if k != "kt" else 600.0, float(v))), 4) for k, v in (motion or {}).items()
          if k in ("draw", "morph", "count", "kt") and v is not None}
    mo.update({k: round(float(v), 2) for k, v in (motion or {}).items() if k in ("yaw", "pitch", "roll") and v is not None})
    k = _key(pdir, layer_data, scale, reveal, group, hi, mo)
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

        img = render_shape(layer, scale, draw=mo.get("draw", 1.0), morph=mo.get("morph", 0.0))
    elif layer.type == "chart":
        from ..ops.chart import render_chart

        img = render_chart(layer, scale, mo.get("draw", 1.0))
    elif layer.type == "iso":
        from ..ops.iso import render_iso

        img = render_iso(layer, scale, mo.get("draw", 1.0))
    elif layer.type == "obj3d":
        from ..ops.render3d import render_obj3d

        img = render_obj3d(layer, scale, mo.get("yaw"), mo.get("pitch"), mo.get("roll"), mo.get("kt", 0.0), pdir)
    elif layer.type in ("video", "glass"):
        raise VeditError("Video and glass layers are drawn by the browser, not as sprites")
    elif layer.type == "text" and layer.split != "none" and not layer.counter:
        from ..ops.motion import Kinetic

        img = Kinetic(layer.text or "", layer.style, scale, layer).frame(mo.get("kt", 600.0))
    elif layer.type == "text":
        from ..ops.motion import shown_text

        text = shown_text(layer, mo.get("count", 1.0))
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

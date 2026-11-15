"""Background removal -> grayscale alpha-mask video (white = keep).

fast:    MediaPipe selfie segmenter (Apache-2.0), near real-time on CPU.
quality: rembg + BiRefNet portrait model (MIT), much slower but cleaner hair/edges.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from ..project import Source, VeditError
from .effects import Writer, model_path, proc_size, read_frames, require, source_dims

SELFIE_URL = "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite"


def _fast_segmenter():
    mp = require("mediapipe", "ai")
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core.base_options import BaseOptions

    opts = vision.ImageSegmenterOptions(
        base_options=BaseOptions(model_asset_path=str(model_path("selfie_segmenter.tflite", SELFIE_URL))),
        running_mode=vision.RunningMode.VIDEO,
        output_confidence_masks=True,
        output_category_mask=False,
    )
    seg = vision.ImageSegmenter.create_from_options(opts)

    def run(frame: np.ndarray, ts_ms: int) -> np.ndarray:
        res = seg.segment_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame)), ts_ms)
        masks = res.confidence_masks
        return np.asarray(masks[-1].numpy_view(), dtype=np.float32).reshape(frame.shape[:2])

    return run


def _quality_segmenter():
    require("rembg", "bg-quality")
    from rembg import new_session, remove

    session = new_session("birefnet-portrait")

    def run(frame: np.ndarray, ts_ms: int) -> np.ndarray:
        m = remove(Image.fromarray(frame), session=session, only_mask=True)
        return np.asarray(m, dtype=np.float32) / 255.0

    return run


def compute_mask(path: Path, src: Source, params: dict, out: Path, log: Callable[[str], None]) -> Path:
    mode = params.get("mode", "fast")
    smooth = float(params.get("smooth", 0.5))  # temporal smoothing 0..0.9
    lo, hi = float(params.get("low", 0.3)), float(params.get("high", 0.7))
    if mode not in ("fast", "quality"):
        raise VeditError("bg mode must be 'fast' or 'quality'")
    w, h, fps = source_dims(src)
    pw, ph = proc_size(w, h, 640 if mode == "fast" else 1024)
    segment = _fast_segmenter() if mode == "fast" else _quality_segmenter()
    writer = Writer(out, w, h, fps, "gray")
    prev = None
    total = max(1, int(src.duration * fps))
    for i, frame in enumerate(read_frames(path, pw, ph, fps)):
        m = segment(frame, int(i * 1000 / fps))
        m = np.clip((m - lo) / max(1e-3, hi - lo), 0, 1)
        if prev is not None and smooth > 0:
            m = smooth * prev + (1 - smooth) * m
        prev = m
        full = Image.fromarray((m * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR)
        writer.write(np.asarray(full))
        if i % 60 == 0:
            log(f"  mask {i}/{total} frames")
    return writer.close()

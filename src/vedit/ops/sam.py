"""SAM 2.1 tiny (Segment Anything, Meta): a box or a few points on one frame -> the object's exact pixel outline.

Used by the outline tracker (`ops/outline.py`) on anchor frames only: where a mask starts, and where tracking
loses confidence and needs re-anchoring. Never per frame, so a clip costs a handful of calls (~0.25 s each on an
Apple-silicon GPU, ~0.5 s on CPU).

Optional: the `mask-ai` extra (`sam2` + PyTorch). The checkpoint (~156 MB) is downloaded from Meta on first use
into ~/.cache/aiklips/models. Code and weights are Apache-2.0 (see THIRD_PARTY_NOTICES.md).
"""

from __future__ import annotations

import importlib.util
import os
import threading

import numpy as np

from ..project import VeditError
from .effects import model_path, require

EXTRA = "mask-ai"
CHECKPOINT = "sam2.1_hiera_tiny.pt"
CHECKPOINT_URL = "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt"
CONFIG = "configs/sam2.1/sam2.1_hiera_t.yaml"
MODES = ("auto", "on", "off")  # auto: use SAM when the extra is installed; on: install it if needed; off: never

_lock = threading.Lock()


def quiet() -> None:
    """SAM 2 warns on every call that its optional compiled extension is missing (it isn't needed), and torch about
    torch.jit: noise in the editor's logs."""
    import os
    import warnings

    os.environ.setdefault("TQDM_DISABLE", "1")  # SAM 2's per-frame progress bars (also switched off in segment.py)
    warnings.filterwarnings("ignore", message=r".*cannot import name '_C' from 'sam2'.*")
    warnings.filterwarnings("ignore", category=FutureWarning, module=r"torch\.jit.*")
    warnings.filterwarnings("ignore", message=r".*torch\.jit\.script.*")


_predictor = None


def installed() -> bool:
    return importlib.util.find_spec("sam2") is not None and importlib.util.find_spec("torch") is not None


def wanted(mode: str | None) -> bool:
    """Should this mask use SAM? 'auto' only when it's already installed (never a surprise download)."""
    mode = mode or "auto"
    if mode == "off" or os.environ.get("VEDIT_NO_SAM"):
        return False
    return mode == "on" or installed()


def _get():
    global _predictor
    with _lock:
        if _predictor is None:
            require("sam2", EXTRA)
            quiet()
            import torch
            from sam2.build_sam import build_sam2
            from sam2.sam2_image_predictor import SAM2ImagePredictor

            ckpt = model_path(CHECKPOINT, CHECKPOINT_URL)
            dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
            try:
                model = build_sam2(CONFIG, str(ckpt), device=dev)
            except Exception:  # some ops are missing on older MPS builds: CPU always works
                model = build_sam2(CONFIG, str(ckpt), device="cpu")
            _predictor = SAM2ImagePredictor(model)
        return _predictor


def segment(frame_bgr: np.ndarray, box_px: tuple[float, float, float, float] | None = None,
            points: list[tuple[float, float, int]] | None = None) -> tuple[np.ndarray, float]:
    """Mask (bool, frame size) of the object in `box_px` (x0, y0, x1, y1) and/or at `points` [(x, y, 1=object /
    0=not)], with SAM's own quality score 0-1.

    With a box, SAM proposes three masks; the one that best fills the box without spilling out of it wins, and
    stray bits (a cable touching the label) are cut: whatever lies well outside the box, and small separate pieces."""
    if box_px is None and not points:
        raise VeditError("SAM needs a box or points")
    import torch

    pred = _get()
    rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
    kw: dict = {"multimask_output": box_px is not None}
    if box_px is not None:
        kw["box"] = np.asarray(box_px, np.float32)
    if points:
        kw["point_coords"] = np.asarray([p[:2] for p in points], np.float32)
        kw["point_labels"] = np.asarray([p[2] for p in points], np.int32)
    with _lock, torch.inference_mode():
        pred.set_image(rgb)
        masks, scores, _ = pred.predict(**kw)
    if box_px is None:
        return masks[0] > 0, float(scores[0])
    import cv2

    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = box_px
    px, py = 0.06 * (x1 - x0), 0.06 * (y1 - y0)
    room = np.zeros((h, w), bool)
    room[max(0, int(y0 - py)):int(np.ceil(y1 + py)) + 1, max(0, int(x0 - px)):int(np.ceil(x1 + px)) + 1] = True
    box_area = max(1.0, (x1 - x0) * (y1 - y0))
    best, best_rank = None, -1.0
    for m, sc in zip(masks, scores):
        m = (m > 0) & room
        n, lab, stats, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
        if n > 2:  # keep the main piece and anything sizeable next to it
            big = stats[1:, cv2.CC_STAT_AREA].max()
            m = np.isin(lab, [k for k in range(1, n) if stats[k, cv2.CC_STAT_AREA] >= 0.15 * big])
        fill = m.sum() / box_area
        rank = float(sc) * min(1.0, fill / 0.5)  # an object drawn round with a box fills a good part of it
        if rank > best_rank:
            best, best_rank, best_sc = m, rank, float(sc)
    return _trim_spurs(cv2, best), best_sc


def _trim_spurs(cv2, m: np.ndarray) -> np.ndarray:
    """Shave thin lumps off an outline (SAM bleeding onto the surface around a label): open the mask with a growing
    kernel while it's ragged (area well below its convex hull), never losing more than 15% of it."""
    area = int(m.sum())
    if area < 200:
        return m
    u8 = m.astype(np.uint8)

    def solidity(x: np.ndarray) -> float:
        cs, _ = cv2.findContours(x, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        hull = sum(cv2.contourArea(cv2.convexHull(c)) for c in cs)
        return float(x.sum()) / hull if hull else 1.0

    if solidity(u8) >= 0.92:
        return m
    best = u8
    for frac in (0.04, 0.07, 0.1):
        k = max(3, int(frac * np.sqrt(area)) | 1)
        o = cv2.morphologyEx(u8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        n, lab, stats, _ = cv2.connectedComponentsWithStats(o, connectivity=8)
        if n > 1:
            o = (lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
        if o.sum() < 0.85 * area:
            break
        best = o
        if solidity(o) >= 0.92:
            break
    return best.astype(bool)

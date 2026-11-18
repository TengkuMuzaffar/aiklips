"""Cut an image sheet (character sheet, storyboard grid, sticker sheet) into its panels.

Code finds the panels (no AI): the background colour is read from the border, then empty rows / columns between
panels (gutters) split the sheet, recursively (rows, then columns inside each row). `grid="RxC"` forces an even
grid instead. With `cutout`, the sheet's background colour becomes transparent, so a character drawn on a plain
background can be animated on top of anything.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from ..project import VeditError

MAX_PIECES = 24
MIN_AREA = 0.004  # a panel smaller than this fraction of the sheet is a speck, not a panel


def _bg(a: np.ndarray) -> np.ndarray:
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    return np.median(border[:, :3], axis=0)


def _content(a: np.ndarray, bg: np.ndarray, tol: float = 38.0) -> np.ndarray:
    diff = np.abs(a[:, :, :3].astype(np.int16) - bg.astype(np.int16)).sum(axis=2)
    m = diff > tol
    if a.shape[2] == 4:
        m &= a[:, :, 3] > 24
    return m


def _bands(profile: np.ndarray, min_gap: int, min_len: int) -> list[tuple[int, int]]:
    """Runs of 'has content' along one axis, separated by gaps of at least min_gap empty lines."""
    on = profile > 0
    out, start, gap = [], None, 0
    for i, v in enumerate(on):
        if v:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                end = i - gap + 1
                if end - start >= min_len:
                    out.append((start, end))
                start, gap = None, 0
    if start is not None and len(on) - gap - start >= min_len:
        out.append((start, len(on) - gap))
    return out


def find_panels(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Boxes (x0, y0, x1, y1) of the panels in a content mask."""
    H, W = mask.shape
    gap = max(4, int(min(W, H) * 0.012))
    boxes = []
    for y0, y1 in _bands(mask.sum(axis=1), gap, max(8, H // 60)):
        row = mask[y0:y1]
        for x0, x1 in _bands(row.sum(axis=0), gap, max(8, W // 60)):
            sub = mask[y0:y1, x0:x1]
            ys = np.where(sub.any(axis=1))[0]
            if len(ys):  # tighten vertically inside the column
                boxes.append((x0, y0 + int(ys[0]), x1, y0 + int(ys[-1]) + 1))
    area = W * H
    return [b for b in boxes if (b[2] - b[0]) * (b[3] - b[1]) >= area * MIN_AREA]


def _grid(spec: str, W: int, H: int) -> list[tuple[int, int, int, int]]:
    m = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", spec)
    if not m:
        raise VeditError("grid must look like 2x3 (rows x columns)")
    r, c = int(m.group(1)), int(m.group(2))
    if not (1 <= r * c <= MAX_PIECES):
        raise VeditError(f"grid must have 1-{MAX_PIECES} cells")
    return [(int(W * j / c), int(H * i / r), int(W * (j + 1) / c), int(H * (i + 1) / r)) for i in range(r) for j in range(c)]


def split(path: Path, out_dir: Path, grid: str | None = None, cutout: bool = False, pad: int = 12) -> list[dict]:
    """Write each panel as a PNG in out_dir. Returns [{path, box, width, height}] in reading order."""
    try:
        im = Image.open(path).convert("RGBA")
    except OSError as e:
        raise VeditError(f"Can't read image {path}: {e}") from None
    a = np.asarray(im)
    H, W = a.shape[:2]
    bg = _bg(a)
    mask = _content(a, bg)
    if grid:
        boxes = _grid(grid, W, H)
    else:
        boxes = find_panels(mask) or [(0, 0, W, H)]
    if len(boxes) > MAX_PIECES:
        raise VeditError(f"Found {len(boxes)} panels (max {MAX_PIECES}); give a grid like 3x4 instead")
    out_dir.mkdir(parents=True, exist_ok=True)
    alpha = None
    if cutout:
        soft = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
        alpha = np.minimum(np.asarray(soft), a[:, :, 3])
    pieces = []
    for i, (x0, y0, x1, y1) in enumerate(boxes, 1):
        x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
        crop = a[y0:y1, x0:x1].copy()
        if alpha is not None:
            crop[:, :, 3] = alpha[y0:y1, x0:x1]
        f = out_dir / f"{Path(path).stem}-p{i:02d}.png"
        Image.fromarray(crop).save(f)
        pieces.append({"path": str(f), "box": [x0, y0, x1, y1], "width": x1 - x0, "height": y1 - y0})
    return pieces


def contact(pieces: list[dict], out: Path, cell: int = 260) -> Path:
    """One numbered overview image of the pieces (what the AI looks at to label them)."""
    from PIL import ImageDraw

    n = len(pieces)
    cols = min(6, n) or 1
    rows = (n + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 30)), "#202024")
    d = ImageDraw.Draw(sheet)
    for i, pc in enumerate(pieces):
        im = Image.open(pc["path"]).convert("RGBA")
        im.thumbnail((cell - 16, cell - 16))
        x, y = (i % cols) * cell, (i // cols) * (cell + 30)
        bgc = Image.new("RGBA", im.size, "#3a3a40")
        bgc.alpha_composite(im)
        sheet.paste(bgc.convert("RGB"), (x + (cell - im.width) // 2, y + 30 + (cell - im.height) // 2))
        d.text((x + 8, y + 8), pc.get("id") or f"p{i + 1}", fill="#c5f467")
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out

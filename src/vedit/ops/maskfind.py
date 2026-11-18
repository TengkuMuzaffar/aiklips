"""Find what to mask: SAM outlines every object on one frame and numbers them on a picture.

`vedit mask find --at T [--near BOX]` is the first step of a precise mask: instead of guessing a box from a frame, the
AI looks at the numbered outlines, picks the one that is the thing to hide (a label, a logo, a screen), and adds the
mask with that candidate's exact box. `--near` looks closer at one area (a label on a bottle), so small things there
are found too.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..project import Project, VeditError, resolve_media
from . import sam
from .effects import proc_size, require, source_dims

MAX_CANDIDATES = 12
COLORS = [(66, 135, 245), (80, 220, 100), (245, 90, 90), (250, 200, 60), (200, 100, 240), (60, 220, 220),
          (255, 140, 40), (150, 230, 60), (240, 100, 180), (120, 160, 255), (255, 230, 120), (100, 255, 180)]


def find(pdir: Path, project: Project, at: float, near: list[float] | None, out: Path, log) -> dict:
    """Candidates on the source frame under timeline second `at`. near: normalised source box to look closer at."""
    from .track import _frames, locate

    if not sam.installed():
        raise VeditError("Finding objects needs SAM. Install it: vedit update --extra mask-ai (or pick a box by eye)")
    cv2 = require("cv2", "ai")
    c, t_src, _ = locate(project, at)
    src = project.sources[c.source]
    if not src.has_video:
        raise VeditError(f"{c.id} has no video")
    w, h, fps = source_dims(src)
    vw, vh = proc_size(w, h, 1024)
    frame = next(_frames(resolve_media(pdir, src.path), vw, vh, fps, t_src, 1.5 / fps), None)
    if frame is None:
        raise VeditError(f"Could not read the frame at {at}s")
    from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator

    gen = SAM2AutomaticMaskGenerator(sam._get().model, points_per_side=24 if near else 20, pred_iou_thresh=0.75,
                                     stability_score_thresh=0.88, box_nms_thresh=0.6, min_mask_region_area=40)
    ox = oy = 0
    img = frame
    if near:  # look closer: the area around the hint, twice its size, at full detail
        bx, by, bw, bh = near
        x0, y0 = max(0, int((bx - bw / 2) * vw)), max(0, int((by - bh / 2) * vh))
        x1, y1 = min(vw, int((bx + bw * 1.5) * vw)), min(vh, int((by + bh * 1.5) * vh))
        if x1 - x0 < 16 or y1 - y0 < 16:
            raise VeditError("--near is outside the picture")
        img, ox, oy = frame[y0:y1, x0:x1], x0, y0
    log("Finding objects with SAM (one frame)...")
    import torch

    with sam._lock, torch.inference_mode():
        found = gen.generate(np.ascontiguousarray(img[:, :, ::-1]))
    total = vw * vh
    cands = []
    for f in found:
        m = np.zeros((vh, vw), bool)
        m[oy:oy + img.shape[0], ox:ox + img.shape[1]] = f["segmentation"]
        a = m.sum() / total
        if not (0.0002 if near else 0.001) <= a <= 0.5:  # specks of text are noise unless looking closer
            continue
        cands.append({"m": m, "area": a, "score": float(f["predicted_iou"]) * float(f["stability_score"])})
    # one of near-duplicates; prefer what's near the hint, then sharp, then mid-sized
    if near:
        hint = np.zeros((vh, vw), bool)
        hint[int(near[1] * vh):int((near[1] + near[3]) * vh) + 1, int(near[0] * vw):int((near[0] + near[2]) * vw) + 1] = True
        for cd in cands:
            cd["score"] += 0.6 * (cd["m"] & hint).sum() / max(1, cd["m"].sum())
    cands.sort(key=lambda x: -x["score"])
    keep: list[dict] = []
    for cd in cands:
        if any((cd["m"] & k["m"]).sum() / max(1, (cd["m"] | k["m"]).sum()) > 0.8 for k in keep):
            continue
        keep.append(cd)
        if len(keep) >= MAX_CANDIDATES:
            break
    keep.sort(key=lambda x: (np.nonzero(x["m"])[0].mean() // (vh / 6), np.nonzero(x["m"])[1].mean()))  # reading order
    # the picture: numbered outlines over the frame, with a 10% grid
    pic = frame.copy()
    for g in range(1, 10):
        cv2.line(pic, (int(vw * g / 10), 0), (int(vw * g / 10), vh), (80, 80, 80), 1)
        cv2.line(pic, (0, int(vh * g / 10)), (vw, int(vh * g / 10)), (80, 80, 80), 1)
    rows = []
    start = project.clip_starts()[project.clips.index(c)]
    taken = np.zeros((vh, vw), np.uint8)  # where number badges already are
    for n, cd in enumerate(keep, 1):
        col = COLORS[(n - 1) % len(COLORS)][::-1]
        cs, _ = cv2.findContours(cd["m"].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        over = pic.copy()
        cv2.fillPoly(over, cs, col)
        pic = cv2.addWeighted(over, 0.22, pic, 0.78, 0)
        cv2.polylines(pic, cs, True, col, 2)
        ys, xs = np.nonzero(cd["m"])
        x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
        lab = str(n)
        (tw, th), _ = cv2.getTextSize(lab, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        # the badge goes where the shape is thickest and no other badge is (big shapes hold smaller ones)
        own = cd["m"].copy()
        for o in keep:  # not on a smaller shape inside this one (its own badge goes there)
            if o is not cd and o["area"] < cd["area"]:
                own &= ~o["m"]
        if own.sum() < 0.15 * cd["m"].sum():
            own = cd["m"]
        dt = cv2.distanceTransform(own.astype(np.uint8), cv2.DIST_L2, 3)
        dt[taken > 0] = 0
        cy, cx = np.unravel_index(int(np.argmax(dt)), dt.shape) if dt.max() > 0 else (int(ys.mean()), int(xs.mean()))
        cx, cy = int(min(max(cx, tw // 2 + 6), vw - tw // 2 - 6)), int(min(max(cy, th // 2 + 6), vh - th // 2 - 6))
        cv2.rectangle(taken, (cx - tw - 14, cy - th - 14), (cx + tw + 14, cy + th + 14), 1, -1)
        cv2.rectangle(pic, (cx - tw // 2 - 5, cy - th // 2 - 5), (cx + tw // 2 + 5, cy + th // 2 + 5), (20, 20, 20), -1)
        cv2.putText(pic, lab, (cx - tw // 2, cy + th // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2, cv2.LINE_AA)
        box = f"{x0 / vw * 100:.1f}%,{y0 / vh * 100:.1f}%,{(x1 - x0) / vw * 100:.1f}%,{(y1 - y0) / vh * 100:.1f}%"
        rows.append({"n": n, "box_source": box, "area": f"{cd['area'] * 100:.2f}%",
                     "center": [round(float(xs.mean()) / vw, 3), round(float(ys.mean()) / vh, 3)], "score": round(min(cd["score"], 1.0), 2),
                     "add": f"vedit mask add --at {at:g} --space source --box {box}"})
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), pic, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return {"at": at, "clip": c.id, "source_time": round(t_src, 3), "clip_start": round(start, 3), "image": str(out),
            "candidates": rows}

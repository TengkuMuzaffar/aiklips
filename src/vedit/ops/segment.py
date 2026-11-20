"""The "segment" tracker: SAM 2.1 tiny's video mode finds the object again in every frame.

The outline tracker (ops/outline.py) carries one outline along with optical flow, so it can only bend the shape it
started with: when an object turns and shows another side, grows, or changes shape, the outline lags behind. SAM 2's
video predictor keeps a memory of what the object looked like (the marked frame plus the last few frames) and
segments it afresh in each new frame, so the mask follows turning, resizing, perspective and things passing in front.

How it runs here:
- The marked frame's outline comes from SAM (image mode) as before; each separate region is its own object.
- Frames are fed at 512 px (the model's default is 1024): ~13x faster on an Apple GPU for almost the same accuracy
  (benchmark: 0.947 vs 0.952 mean IoU on the hard scene, 0.98 vs 0.98 on a turning box).
- Long ranges go in chunks of CHUNK frames, so memory stays flat. Every chunk is given the marked frame as a fixed
  reference, plus the previous chunk's last frame as where to continue from.
- Objects partly outside the picture at the marked frame grow as they come into view: SAM's memory alone would keep
  following only the part it first saw. Every GROW_EVERY frames SAM (image mode) re-outlines a box widened towards
  the side that was cut off; a larger outline that contains the tracked one becomes a new reference.
- Corrections (`params["fixes"]`, added with `vedit mask fix`): where the object is at a given moment, as a box.
  A fix is the truth from its moment on (away from the marked frame): tracking restarts there with the fix as the
  reference, so one fix repairs the whole stretch after it, and the old marked frame can't pull the mask back.
- Hard cuts end the track; frames where SAM finds nothing (hidden, out of view) get no mask, and the track picks the
  object up again when it comes back.

Same output as the outline tracker (per-frame polygons), so masks, focus and `vedit mask check` work unchanged.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from ..project import Project, resolve_media
from . import sam
from .effects import EFFECT_VERSION, cache_dir, need_compute, proc_size, require, source_dims

VERSION = "segment-2"  # bump when tracking changes (part of the cache key)
SIDE = 960  # frame size handed to SAM (long side); output masks are this size
SIZE = 512  # SAM 2's internal image size (1024 is the model default; 512 is ~13x faster, nearly as accurate)
CHUNK = 96  # frames per SAM 2 session
GROW_EVERY = 8  # frames between re-outlines of an object that was partly outside the picture
MIN_AREA = 0.00004  # share of the frame below which an object counts as not visible

_lock = threading.Lock()
_pred = None


def _get():
    global _pred
    with _lock:
        if _pred is None:
            require("sam2", sam.EXTRA)
            sam.quiet()
            import torch
            from sam2.build_sam import build_sam2_video_predictor

            from .effects import model_path

            ckpt = str(model_path(sam.CHECKPOINT, sam.CHECKPOINT_URL))
            fs = SIZE // 16
            ov = [f"++model.image_size={SIZE}", f"++model.memory_attention.layer.self_attention.feat_sizes=[{fs},{fs}]",
                  f"++model.memory_attention.layer.cross_attention.feat_sizes=[{fs},{fs}]"]
            dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
            try:
                pred = build_sam2_video_predictor(sam.CONFIG, ckpt, device=dev, hydra_overrides_extra=ov)
            except Exception:  # some ops are missing on older MPS builds: CPU always works
                pred = build_sam2_video_predictor(sam.CONFIG, ckpt, device="cpu", hydra_overrides_extra=ov)
            pred.fill_hole_area = 0  # needs SAM's compiled extension, which pip installs don't have
            import sam2.sam2_video_predictor as svp

            svp.tqdm = lambda it, *a, **k: it  # no progress bar per frame in the logs (we log our own)
            _pred = pred
        return _pred


class _Frames:
    """SAM 2 reads frames by index; these are kept small (SIZE x SIZE uint8) and normalised on demand."""

    def __init__(self, frames: list[np.ndarray]):
        import cv2
        import torch

        self.torch = torch
        self.small = [cv2.resize(f[:, :, ::-1], (SIZE, SIZE), interpolation=cv2.INTER_AREA) for f in frames]
        self.mean = torch.tensor((0.485, 0.456, 0.406), dtype=torch.float32)[:, None, None]
        self.std = torch.tensor((0.229, 0.224, 0.225), dtype=torch.float32)[:, None, None]

    def __len__(self) -> int:
        return len(self.small)

    def __getitem__(self, i: int):
        t = self.torch.from_numpy(np.ascontiguousarray(self.small[i])).permute(2, 0, 1).float() / 255.0
        return (t - self.mean) / self.std


def _session(pred, frames: list[np.ndarray]) -> dict:
    """A SAM 2 inference state over in-memory frames (the library's own init_state only reads JPEG folders)."""
    h, w = frames[0].shape[:2]
    st: dict = {"images": _Frames(frames), "num_frames": len(frames), "offload_video_to_cpu": True,
                "offload_state_to_cpu": False, "video_height": h, "video_width": w, "device": pred.device,
                "storage_device": pred.device, "point_inputs_per_obj": {}, "mask_inputs_per_obj": {},
                "cached_features": {}, "constants": {}, "obj_id_to_idx": OrderedDict(), "obj_idx_to_id": OrderedDict(),
                "obj_ids": [], "output_dict_per_obj": {}, "temp_output_dict_per_obj": {}, "frames_tracked_per_obj": {}}
    pred._get_image_feature(st, frame_idx=0, batch_size=1)
    return st


def _border_sides(m: np.ndarray, pad: int = 2) -> set[str]:
    sides = set()
    if m[:, :pad].any():
        sides.add("l")
    if m[:, -pad:].any():
        sides.add("r")
    if m[:pad].any():
        sides.add("t")
    if m[-pad:].any():
        sides.add("b")
    return sides


def _grow(cv2, frame: np.ndarray, m: np.ndarray, sides: set[str]) -> np.ndarray | None:
    """A bigger outline of an object that was cut off by the frame edge on `sides`, or None."""
    ys, xs = np.nonzero(m)
    if len(xs) < 20:
        return None
    h, w = m.shape
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    bw, bh = x1 - x0, y1 - y0
    # the hidden part was on the cut-off side: look up to 60% further that way (a little on the others)
    gx = {s: (0.6 if s in sides else 0.04) for s in "lrtb"}
    box = (max(0, x0 - gx["l"] * bw), max(0, y0 - gx["t"] * bh), min(w - 1, x1 + gx["r"] * bw), min(h - 1, y1 + gx["b"] * bh))
    try:
        c, score = sam.segment(frame, box)
    except Exception:
        return None
    if c is None or score < 0.6:
        return None
    a, ca = int(m.sum()), int(c.sum())
    if not 1.04 * a < ca < 2.5 * a or (c & m).sum() < 0.85 * a:
        return None
    extra = c & ~m
    ey, ex = np.nonzero(extra)
    if not len(ex):
        return None
    # the new part must lie on a cut-off side, not spill round the object (a label growing into the whole bottle)
    cx, cy = xs.mean(), ys.mean()
    dx, dy = ex.mean() - cx, ey.mean() - cy
    want = {"l": dx < 0 and abs(dx) >= abs(dy) * 0.5, "r": dx > 0 and abs(dx) >= abs(dy) * 0.5,
            "t": dy < 0 and abs(dy) >= abs(dx) * 0.5, "b": dy > 0 and abs(dy) >= abs(dx) * 0.5}
    return c if any(want[s] for s in sides) else None


def _seed_masks(cv2, frame: np.ndarray, params: dict, use_sam: bool) -> list[np.ndarray]:
    """The object(s) on the marked frame: one boolean mask per separate region."""
    from .outline import initial_regions, poly_mask

    h, w = frame.shape[:2]
    return [poly_mask(cv2, polys, w, h) > 127 for polys, _ in initial_regions(cv2, frame, params, use_sam)]


def _fix_masks(cv2, frame: np.ndarray, fix: dict) -> list[np.ndarray]:
    """A correction: the object's box at that moment -> SAM's outline (or the box itself)."""
    from .outline import _sam_region, poly_mask

    h, w = frame.shape[:2]
    out = []
    for bx, by, bw, bh in fix.get("boxes") or [fix["box"]]:
        x0, y0, x1, y1 = bx * w, by * h, (bx + bw) * w, (by + bh) * h
        m = _sam_region(cv2, frame, x0, y0, x1, y1)
        if m is None:
            m = poly_mask(cv2, [np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])], w, h) > 127
        out.append(m)
    return out


def _assign(fix: list[np.ndarray], prev: list[np.ndarray], n_obj: int) -> list[np.ndarray]:
    """Give each corrected outline to the tracked object it's nearest to (a mask may have several regions)."""
    shape = fix[0].shape
    out = [np.zeros(shape, bool) for _ in range(n_obj)]
    if n_obj == 1:
        out[0] = np.logical_or.reduce(fix)
        return out

    def centre(m: np.ndarray) -> np.ndarray | None:
        ys, xs = np.nonzero(m)
        return np.array([xs.mean(), ys.mean()]) if len(xs) else None

    cs = [centre(m) for m in prev]
    for m in fix:
        c = centre(m)
        if c is None:
            continue
        d = [np.linalg.norm(c - p) if p is not None else np.inf for p in cs]
        k = int(np.argmin(d)) if np.isfinite(min(d)) else 0
        out[k] |= m
    return out


def _run_direction(cv2, pred, ref: tuple[np.ndarray, list[np.ndarray]], frames: Iterator[tuple[int, np.ndarray]],
                   fixes: dict[int, dict], grow_sides: list[set[str]], log, total: int, done: list[int]):
    """Track from the marked frame outwards (frames: (index, frame) in tracking order, not including it).

    Yields (index, [mask per object], confidence). A correction (fixes: frame index -> {"boxes": ...}) is the truth
    from its frame on: tracking restarts there with it as the reference, so the old marked frame can't pull the mask
    back to where it was."""
    import torch

    from .outline import _thumb, is_cut

    n_obj = len(ref[1])
    seed: tuple[np.ndarray, list[np.ndarray]] | None = None
    thumb = _thumb(cv2, ref[0])
    since_grow = [0] * n_obj
    misses = [0] * n_obj
    prev = ref[1]
    it = iter(frames)
    ended = False
    carry: list[tuple[int, np.ndarray]] = []
    while carry or not ended:
        batch, carry = carry, []
        while not ended and len(batch) < CHUNK:
            nxt = next(it, None)
            if nxt is None:
                ended = True
                break
            th = _thumb(cv2, nxt[1])
            if is_cut(cv2, thumb, th):  # another shot: this object isn't in it (or not where it was)
                ended = True
                break
            thumb = th
            batch.append(nxt)
        if not batch:
            break
        if batch[0][0] in fixes:  # a correction here: it's the answer on this frame and the reference after it
            i0, f = batch[0]
            masks = _assign(_fix_masks(cv2, f, fixes.pop(i0)), prev, n_obj)
            prev = masks
            yield i0, masks, 1.0
            done[0] += 1
            ref, seed, carry = (f, masks), None, batch[1:]
            continue
        lead = [ref] + ([seed] if seed else [])
        imgs = [x[0] for x in lead] + [f for _, f in batch]
        base = len(lead)
        results: dict[int, list[np.ndarray]] = {}
        with torch.inference_mode():
            st = _session(pred, imgs)
            for k, (_, masks) in enumerate(lead):
                for o, m in enumerate(masks):
                    if m is not None and m.any():
                        pred.add_new_mask(st, frame_idx=k, obj_id=o + 1, mask=m)
            pos = base  # next frame (state index) to track
            while pos < len(imgs):
                # stop before a correction (it starts a new session) or where a growth check is due
                stop = len(imgs) - 1
                for j in range(pos, len(imgs)):
                    if batch[j - base][0] in fixes:
                        stop = j - 1
                        carry = batch[j - base:]
                        break
                    if any(grow_sides) and (j - base + 1) % GROW_EVERY == 0:
                        stop = j
                        break
                if stop < pos:
                    break
                results = {}
                for fi, ids, logits in pred.propagate_in_video(st, start_frame_idx=pos, max_frame_num_to_track=stop - pos):
                    if pos <= fi <= stop:
                        per = [np.zeros(imgs[0].shape[:2], bool) for _ in range(n_obj)]
                        for oid, lg in zip(ids, logits):
                            if 1 <= oid <= n_obj:
                                per[oid - 1] = (lg[0] > 0).cpu().numpy()
                        results[fi] = per
                for fi in range(pos, stop + 1):
                    per = results.get(fi)
                    if per is None:
                        continue
                    prev = per
                    yield batch[fi - base][0], per, 1.0
                    done[0] += 1
                # growth check at `stop`: an object that started cut off by the frame edge may now show more
                per = results.get(stop)
                if per is not None:
                    for o in range(n_obj):
                        if not grow_sides[o] or not per[o].any():
                            continue
                        since_grow[o] += stop - pos + 1
                        if since_grow[o] < GROW_EVERY:
                            continue
                        since_grow[o] = 0
                        g = _grow(cv2, imgs[stop], per[o], grow_sides[o])
                        if g is None:
                            misses[o] += 1
                            if misses[o] >= 4:  # nothing more has come into view for a while: it's all in
                                grow_sides[o] = set()
                            continue
                        misses[o] = 0
                        pred.add_new_mask(st, frame_idx=stop, obj_id=o + 1, mask=g)
                        per[o] = g
                pos = stop + 1
                if carry:
                    break
            del st
        last_i = pos - 1
        if last_i >= base and results.get(last_i) is not None:
            seed = (imgs[last_i], results[last_i])
        log(f"  segmenting... {min(100, int(100 * done[0] / max(1, total)))}%")
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()


def track_segment(pdir: Path, project: Project, source_id: str, params: dict, log: Callable[[str], None]) -> dict:
    from .outline import _reverse_chunks, contours_of
    from .track import _frames

    src = project.sources[source_id]
    path = resolve_media(pdir, src.path)
    w, h, fps = source_dims(src)
    n_frames = max(1, int(round(src.duration * fps)))
    st = path.stat()
    fixes_p = params.get("fixes") or []
    key_data = [str(path), st.st_size, st.st_mtime, params["box"], params.get("paint"), params.get("shape"), params.get("at"),
                params.get("range"), fixes_p, EFFECT_VERSION, VERSION, SIZE]
    key = hashlib.sha1(json.dumps(key_data, default=str).encode()).hexdigest()[:16]
    cache = cache_dir(pdir) / f"segment-{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    need_compute("object tracking")
    cv2 = require("cv2", "ai")
    pred = _get()
    pw, ph = proc_size(w, h, SIDE)
    a, b = params.get("range") or [0.0, src.duration]
    at = min(max(float(params.get("at", a)), a), b)
    fa, fb = max(0, int(a * fps)), min(n_frames, int(np.ceil(b * fps)) + 1)
    f0 = min(max(fa, int(round(at * fps))), max(fa, fb - 1))

    def read(s: float, d: float) -> Iterator[np.ndarray]:
        return _frames(path, pw, ph, fps, s, d)

    first = next(read(f0 / fps, 2.0 / fps), None)
    if first is None:
        raise RuntimeError(f"Could not read {path.name} at {f0 / fps:.2f}s")
    seeds = _seed_masks(cv2, first, params, True)
    pad = max(2, int(0.01 * max(pw, ph)))
    sides0 = [_border_sides(m, pad) for m in seeds]
    fixes = {}
    for fx in fixes_p:
        fi = int(round(float(fx["at"]) * fps))
        if fa <= fi < fb and fi != f0:
            fixes[fi] = fx
    log(f"Segmenting the object in every frame of {path.name} {a:.1f}-{b:.1f}s ({len(seeds)} region(s), "
        f"{len(fixes)} correction(s); SAM 2 video, one-time)...")
    out: dict[int, list[np.ndarray] | None] = {f0: seeds}
    conf: dict[int, float] = {f0: 1.0}
    done = [0]
    total = fb - fa
    fwd = ((i, f) for i, f in enumerate(read((f0 + 1) / fps, (fb - f0 - 1) / fps), start=f0 + 1) if i < fb)
    back = _reverse_chunks(read, fps, fa, f0)
    for frames, fx in ((fwd, {i: v for i, v in fixes.items() if i > f0}), (back, {i: v for i, v in fixes.items() if i < f0})):
        for i, per, c in _run_direction(cv2, pred, (first, seeds), frames, fx, [set(s) for s in sides0], log, total, done):
            out[i], conf[i] = per, c
    # one outline per frame from all objects; nothing where none is visible
    min_px = MIN_AREA * pw * ph
    idx = list(range(fa, fb))
    boxes = np.tile(np.array([float(v) for v in params["box"]]), (n_frames, 1))
    norm, confs, last_box, lost = [], [], None, 0
    for i in idx:
        per = out.get(i)
        m = np.logical_or.reduce(per) if per else None
        if m is None or m.sum() < min_px:
            norm.append(None)
            confs.append(0.0)
            lost += 1
            if last_box is not None:
                boxes[i] = last_box
            continue
        polys = contours_of(cv2, m)
        if not polys:
            norm.append(None)
            confs.append(0.0)
            continue
        allp = np.concatenate(polys, 0)
        x0, y0 = np.clip(allp.min(0), 0, [pw, ph])
        x1, y1 = np.clip(allp.max(0), 0, [pw, ph])
        last_box = [x0 / pw, y0 / ph, max(1e-3, (x1 - x0) / pw), max(1e-3, (y1 - y0) / ph)]
        boxes[i] = last_box
        norm.append([np.round(p / [pw, ph], 5).tolist() for p in polys])
        confs.append(round(conf.get(i, 1.0), 3))
    if fb > fa:
        boxes[:fa] = boxes[fa]
        boxes[fb:] = boxes[fb - 1]
    data = {"fps": fps, "boxes": np.round(boxes, 5).tolist(), "how": "segment", "tracker": "segment",
            "anchors": sorted([f0] + [int(round(float(x["at"]) * fps)) for x in fixes_p]),
            "outline": {"start": fa, "polys": norm, "conf": confs}}
    if lost:
        log(f"  object not visible in {lost} frame(s): no mask there")
    cache.write_text(json.dumps(data))
    return data

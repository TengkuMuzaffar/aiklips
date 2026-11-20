"""Outline (point) tracking for masks and object focus: follow the object's actual outline, not a box.

1. Outline on the frame the user marked: SAM 2.1 tiny turns each box (a mask may have several, joined with ';')
   into the object's exact outline (when the `mask-ai` extra is installed); brushed shapes stay exactly as
   painted; otherwise the box itself. Separate regions (two labels, or two faces of a box at an angle to each
   other) are tracked separately, each with its own perspective: one fit can't follow two surfaces.
2. Points: the outline becomes 20-120 vertices (corners + curves), plus up to 300 texture points inside it.
3. Tracking, frame to frame, forwards and backwards from the marked frame: every point is followed with
   Lucas-Kanade optical flow and checked by tracking it back again. The points' motion is fitted as one
   perspective transform (RANSAC), so a point that slides off at the edge or vanishes behind a hand is corrected
   by the others, and the outline is that transform applied to the anchor outline: no drift from frame to frame.
4. Snapping: each outline point is pulled (gently) to the strongest edge across the outline, within a few pixels.
5. Confidence per frame (how many points agree). When it stays low for a few frames (the object turning, deforming,
   blurring), SAM re-outlines the object around the predicted outline and, if it agrees, that frame becomes a new
   anchor. Never on a timer (a good track would only be swapped for a slightly different outline), and never a
   clearly smaller outline (that usually means something is in front of the object, not that it shrank). Only for
   SAM-made outlines: a painted shape is the user's, not an object boundary.
6. Lost: when too few points are left, the outline has left the frame, or the shot cuts to another scene, the
   mask stops there ("track until there are no pixels left") instead of sticking to the wrong thing.

Output (cached per source + params): {"fps", "boxes": [x, y, w, h] per source frame (normalised, for code that
only needs a box), "outline": {"start": f, "polys": [[[x, y], ...], ...] | None per frame from f}, "conf", ...}.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from ..project import Project, resolve_media
from . import sam
from .effects import EFFECT_VERSION, cache_dir, need_compute, proc_size, require, source_dims

VERSION = "outline-9"  # bump when tracking changes (part of the cache key)
SIDE = 960  # tracking resolution (long side)
MAX_FEATURES = 300
MAX_SAM_CALLS = 40
LOW_CONF = 0.45
WEAK_FRAMES = 3  # consecutive low-confidence frames before SAM is asked to re-outline
CHECK_EVERY = 1.0  # seconds between SAM drift checks on a SAM-made outline (adopted only when SAM clearly disagrees)
TRACKERS = ("auto", "segment", "outline", "box")


def resolve_tracker(params: dict) -> str:
    """The tracker a mask / focus really uses. auto: SAM 2 video ("segment") when SAM is available (and not switched
    off with --sam off), else outline. segment was asked for by name: it always uses SAM (installing it if needed)."""
    t = params.get("tracker") or "box"  # masks made before trackers existed used the box tracker
    if t == "segment":
        return "segment" if sam.wanted("on") else "outline"
    if t == "auto":
        return "segment" if sam.wanted(params.get("sam")) else "outline"
    return t


# ---- outline <-> points ----------------------------------------------------------------------------
def contours_of(cv2, mask: np.ndarray, min_share: float = 0.01) -> list[np.ndarray]:
    """Outer contours and holes of a mask as float Nx2 point lists (simplified, then evenly densified)."""
    m = mask.astype(np.uint8)
    cs, _ = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return []
    areas = [abs(cv2.contourArea(c)) for c in cs]
    big = max(areas)
    out = []
    for c, a in zip(cs, areas):
        if a < max(12.0, min_share * big):
            continue
        per = cv2.arcLength(c, True)
        ap = cv2.approxPolyDP(c, max(0.7, 0.004 * per), True).reshape(-1, 2).astype(np.float32)
        if len(ap) < 3:
            continue
        step = max(6.0, per / 120)
        pts = []
        for i in range(len(ap)):
            p, q = ap[i], ap[(i + 1) % len(ap)]
            n = max(1, int(np.ceil(np.linalg.norm(q - p) / step)))
            pts += [p + (q - p) * (k / n) for k in range(n)]
        out.append(np.asarray(pts, np.float32))
    return out


def poly_mask(cv2, polys: list[np.ndarray], w: int, h: int) -> np.ndarray:
    """Filled polygons (holes stay empty: OpenCV fills by crossing count) as a 0/255 mask."""
    m = np.zeros((h, w), np.uint8)
    if polys:
        cv2.fillPoly(m, [np.round(p).astype(np.int32) for p in polys], 255, cv2.LINE_AA)
    return m


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    u = np.count_nonzero(a | b)
    return np.count_nonzero(a & b) / u if u else 0.0


def snap(cv2, grad: np.ndarray, poly: np.ndarray, radius: float, pull: float = 0.5) -> np.ndarray:
    """Pull each vertex towards the strongest edge along the outline's normal (within `radius` px)."""
    n = len(poly)
    if n < 5 or radius < 1:
        return poly
    tang = np.roll(poly, -1, 0) - np.roll(poly, 1, 0)
    norm = np.stack([-tang[:, 1], tang[:, 0]], 1)
    norm /= np.maximum(np.linalg.norm(norm, axis=1, keepdims=True), 1e-6)
    offs = np.linspace(-radius, radius, int(2 * radius) * 2 + 1)
    h, w = grad.shape
    samp = poly[:, None, :] + norm[:, None, :] * offs[None, :, None]
    xs = np.clip(samp[..., 0], 0, w - 1).astype(np.int32)
    ys = np.clip(samp[..., 1], 0, h - 1).astype(np.int32)
    g = grad[ys, xs]  # n x len(offs)
    best = g.argmax(1)
    center = g[:, len(offs) // 2]
    peak = g[np.arange(n), best]
    ok = (peak > 20) & (peak > 1.25 * center)
    shift = np.where(ok, offs[best], 0.0)
    k = np.ones(5) / 5  # neighbouring vertices move together, so the outline stays smooth
    shift = np.convolve(np.concatenate([shift[-2:], shift, shift[:2]]), k, "valid")
    return poly + norm * (shift * pull)[:, None]


# ---- tracking --------------------------------------------------------------------------------------
class _Dir:
    """Tracks one direction (forwards or backwards in time) from an anchor."""

    def __init__(self, cv2, frame: np.ndarray, polys: list[np.ndarray], fps: float, use_sam: bool):
        self.cv2, self.fps, self.use_sam = cv2, fps, use_sam
        self.sam_calls, self.weak = 0, 0
        self.anchor(frame, polys, 0)

    def anchor(self, frame: np.ndarray, polys: list[np.ndarray], i: int) -> None:
        cv2 = self.cv2
        self.gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = self.gray.shape
        self.ref_polys = [p.copy() for p in polys]
        self.ref_area = max(1.0, float(np.count_nonzero(poly_mask(cv2, polys, w, h))))
        allp = np.concatenate(polys, 0)
        m = 2.5  # which sides of the picture cut the outline off: more of the object may come into view there
        self.cut_sides = {"l": allp[:, 0].min() <= m, "t": allp[:, 1].min() <= m, "r": allp[:, 0].max() >= w - 1 - m,
                          "b": allp[:, 1].max() >= h - 1 - m}
        self.anchor_i = self.last_try = i
        self.H = np.eye(3)
        self.cur_pts = self.ref_pts = np.zeros((0, 2), np.float32)
        self.fails = np.zeros(0, np.int32)
        self._seed(self.gray, polys, np.eye(3))
        self.n0 = max(1, len(self.ref_pts))

    def _seed(self, gray: np.ndarray, polys: list[np.ndarray], H: np.ndarray) -> None:
        """Add points to follow: texture inside the object, away from its edge (edge points are half background and
        slide when the background moves differently). Plain objects with little texture also get their outline.
        Points already being followed are kept: new ones are added beside them, so the fit stays anchored."""
        cv2 = self.cv2
        h, w = gray.shape
        m = poly_mask(cv2, polys, w, h)
        allp = np.concatenate(polys, 0)
        obj = float(min(np.ptp(allp[:, 0]), np.ptp(allp[:, 1])))
        # stay off the edge, but on a small object only a little: points squeezed into its middle can't measure how it
        # stretches as it turns
        size = max(2, int(min(0.012 * max(w, h), 0.06 * obj)))
        inner_m = cv2.erode(m, np.ones((size, size), np.uint8))
        if np.count_nonzero(inner_m) < 0.25 * np.count_nonzero(m):  # a thin object: use all of it
            inner_m = m
        for x, y in self.cur_pts:  # not on top of the points we already have
            cv2.circle(inner_m, (int(x), int(y)), 5, 0, -1)
        room = MAX_FEATURES - len(self.cur_pts)
        feat = cv2.goodFeaturesToTrack(gray, room, 0.01, 5, mask=inner_m) if room > 0 else None
        new = feat.reshape(-1, 2).astype(np.float32) if feat is not None else np.zeros((0, 2), np.float32)
        if len(self.cur_pts) + len(new) < 12:
            new = np.concatenate([new] + list(polys), 0).astype(np.float32)
        if not len(new):
            return
        ref = cv2.perspectiveTransform(new.reshape(-1, 1, 2), np.linalg.inv(H)).reshape(-1, 2).astype(np.float32)
        self.cur_pts = np.concatenate([self.cur_pts, new], 0).astype(np.float32)
        self.ref_pts = np.concatenate([self.ref_pts, ref], 0).astype(np.float32)
        self.fails = np.concatenate([self.fails, np.zeros(len(new), np.int32)])

    def step(self, frame: np.ndarray, i: int) -> tuple[list[np.ndarray] | None, float]:
        """Outline in this frame (frame px) and a 0-1 confidence; None when the object is lost."""
        cv2 = self.cv2
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        lk = {"winSize": (21, 21), "maxLevel": 3, "criteria": (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)}
        conf, Hn = 0.0, None
        if len(self.cur_pts) >= 4:
            p0 = self.cur_pts.reshape(-1, 1, 2)
            p1, st, _ = cv2.calcOpticalFlowPyrLK(self.gray, gray, p0, None, **lk)
            pb, st2, _ = cv2.calcOpticalFlowPyrLK(gray, self.gray, p1, None, **lk)
            fb = np.linalg.norm((pb - p0).reshape(-1, 2), axis=1)
            good = (st.ravel() == 1) & (st2.ravel() == 1) & (fb < 1.0)
            p1 = p1.reshape(-1, 2)
            inside = (p1[:, 0] >= 0) & (p1[:, 0] < w) & (p1[:, 1] >= 0) & (p1[:, 1] < h)
            good &= inside
            src, dst = self.ref_pts[good], p1[good]
            if len(src) >= 8:
                Hn, inl = cv2.findHomography(src, dst, cv2.RANSAC, 2.5)
                n_in = int(inl.sum()) if inl is not None else 0
            elif len(src) >= 3:
                A, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=2.5)
                Hn = None if A is None else np.vstack([A, [0, 0, 1]])
                n_in = int(inl.sum()) if inl is not None else 0
            else:
                n_in = 0
            if Hn is not None and not self._plausible(Hn, w, h):
                Hn = None
            if Hn is not None:
                conf = min(1.0, n_in / max(12.0, 0.35 * self.n0))
                # points that agree keep their tracked position; the rest are re-predicted from the fit for a few
                # frames (a hand passing, blur) and dropped if they don't come back
                pred = cv2.perspectiveTransform(self.ref_pts.reshape(-1, 1, 2), Hn).reshape(-1, 2)
                ok = good.copy()
                ok[good] = np.linalg.norm(p1[good] - pred[good], axis=1) < 3.0
                self.fails = np.where(ok, 0, self.fails + 1)
                keep = self.fails <= 8
                self.cur_pts = np.where(ok[:, None], p1, pred).astype(np.float32)[keep]
                self.ref_pts, self.fails = self.ref_pts[keep], self.fails[keep]
                self.H = Hn
                if int(ok.sum()) < 0.5 * self.n0:  # many lost (blur, occlusion, turning): add fresh ones
                    self._seed(gray, self._polys(Hn), Hn)
        self.gray = gray
        if Hn is None:
            return None, 0.0
        polys = self._polys(self.H)
        # the object has left the frame: nothing (or almost nothing) of it is visible any more
        allp = np.concatenate(polys, 0)
        vis = np.mean((allp[:, 0] >= 0) & (allp[:, 0] < w) & (allp[:, 1] >= 0) & (allp[:, 1] < h))
        if vis < 0.15:
            return None, 0.0
        return polys, conf

    def _polys(self, H: np.ndarray) -> list[np.ndarray]:
        return [self.cv2.perspectiveTransform(p.reshape(-1, 1, 2), H).reshape(-1, 2) for p in self.ref_polys]

    def _plausible(self, H: np.ndarray, w: int, h: int) -> bool:
        cv2 = self.cv2
        if not np.isfinite(H).all():
            return False
        # the anchor outline's bounding box must stay a sensible, convex quad near the picture: a fit through bad
        # points can fold or fling the outline (a self-crossing shape can still have a small area, so check this first)
        allp = np.concatenate(self.ref_polys, 0)
        (x0, y0), (x1, y1) = allp.min(0), allp.max(0)
        quad = cv2.perspectiveTransform(np.float32([[[x0, y0]], [[x1, y0]], [[x1, y1]], [[x0, y1]]]), H).reshape(-1, 2)
        prevq = cv2.perspectiveTransform(np.float32([[[x0, y0]], [[x1, y0]], [[x1, y1]], [[x0, y1]]]), self.H).reshape(-1, 2)
        if not cv2.isContourConvex(quad.astype(np.float32)):
            return False
        qa, pa = abs(cv2.contourArea(quad)), abs(cv2.contourArea(prevq))
        ref_box = max(1.0, (x1 - x0) * (y1 - y0))
        if not (0.2 * ref_box < qa < 5 * ref_box) or (pa > 0 and not (0.6 < qa / pa < 1.67)):
            return False
        if (quad[:, 0].min() < -w or quad[:, 0].max() > 2 * w or quad[:, 1].min() < -h or quad[:, 1].max() > 2 * h
                or np.abs(quad - prevq).max() > 0.25 * max(w, h)):
            return False  # far off the picture, or a jump no camera makes in one frame
        polys = self._polys(H)
        area = float(np.count_nonzero(poly_mask(cv2, polys, w, h))) or float(sum(abs(cv2.contourArea(p)) for p in polys))
        prev = float(sum(abs(cv2.contourArea(p)) for p in self._polys(self.H)))
        if prev > 0 and not (0.6 < sum(abs(cv2.contourArea(p)) for p in polys) / prev < 1.67):
            return False  # a jump in size from one frame to the next is a bad fit, not motion
        return 0.05 * self.ref_area < area or any(abs(cv2.contourArea(p)) > 0.05 * self.ref_area for p in polys)

    def maybe_reanchor(self, frame: np.ndarray, polys: list[np.ndarray], conf: float, i: int) -> list[np.ndarray]:
        """Ask SAM to re-outline the object around the predicted outline; adopt it as the new anchor if it agrees."""
        self.weak = self.weak + 1 if conf < LOW_CONF else 0
        since = abs(i - self.anchor_i)
        # an outline cut off by the picture's edge only knows the part that was visible: as the camera moves, ask SAM
        # for the whole object again (every ~0.4 s) so the mask grows over what comes into view
        growing = any(self.cut_sides.values()) and since >= 6 and abs(i - self.last_try) >= max(6, 0.4 * self.fps)
        # a track can be confidently wrong (points agree on a slightly wrong stretch as a surface turns), so SAM also
        # checks a SAM-made outline every second
        check = abs(i - self.last_try) >= CHECK_EVERY * self.fps
        if not self.use_sam or self.sam_calls >= MAX_SAM_CALLS or since < 6 or not (self.weak >= WEAK_FRAMES or growing or check):
            return polys
        self.weak_was, self.weak, self.last_try = self.weak, 0, i
        cv2 = self.cv2
        h, w = frame.shape[:2]
        pred = poly_mask(cv2, polys, w, h) > 127
        ys, xs = np.nonzero(pred)
        if len(xs) < 30:
            return polys
        bw, bh = np.ptp(xs), np.ptp(ys)
        pad = 0.04 * max(bw, bh)
        x0, y0, x1, y1 = xs.min() - pad, ys.min() - pad, xs.max() + pad, ys.max() + pad
        if growing:  # look further on the sides where the object was cut off
            s = self.cut_sides
            x0, x1 = x0 - (0.6 * bw if s["l"] else 0), x1 + (0.6 * bw if s["r"] else 0)
            y0, y1 = y0 - (0.6 * bh if s["t"] else 0), y1 + (0.6 * bh if s["b"] else 0)
        box = (max(0, x0), max(0, y0), min(w - 1, x1), min(h - 1, y1))
        self.sam_calls += 1
        try:
            m, score = sam.segment(frame, box)
        except Exception:
            self.use_sam = False  # SAM broke (memory, driver): carry on with points only
            return polys
        ratio = m.sum() / max(1, pred.sum())
        covers = np.count_nonzero(m & pred) / max(1, pred.sum())
        iou = _iou(m, pred)
        if score < 0.6 or iou >= 0.92:
            return polys  # SAM unsure, or the points are already right: keep the track as it is
        if growing and self.weak_was < WEAK_FRAMES and covers >= 0.8 and 0.9 < ratio < 2.5:
            ok = True  # same object, now with more of it in view
        elif iou >= 0.5 and 0.9 <= ratio < 1.6:
            ok = True
        else:
            # smaller: a surface turning away from the camera shrinks, but so does a label with a hand in front of it.
            # With the points still agreeing (high confidence) nothing is in front, so it really is smaller.
            ok = iou >= 0.5 and 0.55 <= ratio < 0.9 and conf >= 0.7
        if not ok:  # nothing new in view yet, or SAM sees something else: try again a little later
            return polys  # SAM disagrees too much, or sees less (something in front): trust the points
        new = contours_of(cv2, m)
        if not new:
            return polys
        self.anchor(frame, new, i)
        return new


def _thumb(cv2, frame: np.ndarray) -> np.ndarray:
    return cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (48, 48), interpolation=cv2.INTER_AREA).astype(np.float32)


def is_cut(cv2, a: np.ndarray, b: np.ndarray) -> bool:
    """A hard cut between two (thumbnail) frames: the picture changes almost completely, not just moves."""
    diff = float(np.mean(np.abs(a - b)))
    if diff < 18:
        return False
    ha = cv2.calcHist([a], [0], None, [32], [0, 256])
    hb = cv2.calcHist([b], [0], None, [32], [0, 256])
    # a pan or whip keeps the scene's tones (histogram); a cut swaps them. Only a near-total change counts alone.
    return cv2.compareHist(ha, hb, cv2.HISTCMP_CORREL) < 0.6 or diff > 80


def _reverse_chunks(read: Callable[[float, float], Iterator[np.ndarray]], fps: float, fa: int, f0: int,
                    chunk: int = 90) -> Iterator[tuple[int, np.ndarray]]:
    """Frames f0-1, f0-2, ... fa (newest first), read forwards a chunk at a time."""
    end = f0
    while end > fa:
        start = max(fa, end - chunk)
        frames = list(read(start / fps, (end - start) / fps))[: end - start]
        for k in range(len(frames) - 1, -1, -1):
            yield start + k, frames[k]
        end = start


def _smooth_runs(polys: list, anchors: list[int], strength: float) -> list:
    """Light zero-phase smoothing of each vertex over time, within runs that share an anchor (same points)."""
    a = min(max(strength, 0.0), 0.9) * 0.5
    if a <= 0:
        return polys
    out = list(polys)
    i = 0
    while i < len(polys):
        j = i
        while (j + 1 < len(polys) and polys[j + 1] is not None and polys[i] is not None and anchors[j + 1] == anchors[i]
               and [len(p) for p in polys[j + 1]] == [len(p) for p in polys[i]]):
            j += 1
        if polys[i] is not None and j - i >= 2:
            flat = np.stack([np.concatenate(p, 0) for p in polys[i:j + 1]])
            for rng in (range(1, len(flat)), range(len(flat) - 2, -1, -1)):
                for k in rng:
                    flat[k] = a * flat[k - 1 if rng.step == 1 else k + 1] + (1 - a) * flat[k]
            sizes = np.cumsum([len(p) for p in polys[i]])[:-1]
            for k in range(i, j + 1):
                out[k] = np.split(flat[k - i], sizes)
        i = j + 1
    return out


def _sam_region(cv2, frame: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> np.ndarray | None:
    """SAM's outline of the object in a box (px), or None when it doesn't fit the box (then the box itself is used)."""
    try:
        m, score = sam.segment(frame, (x0, y0, x1, y1))
    except Exception:
        return None
    if m is None or score < 0.5:
        return None
    h, w = frame.shape[:2]
    box_area = max(1.0, (x1 - x0) * (y1 - y0))
    inside = m[max(0, int(y0)):int(y1) + 1, max(0, int(x0)):int(x1) + 1].sum()
    if 0.08 * box_area < m.sum() < 1.3 * box_area and inside > 0.85 * m.sum():
        return m
    return None


def _merge_regions(cv2, masks: list[tuple[np.ndarray, str]]) -> list[tuple[np.ndarray, str]]:
    """Regions that touch or overlap are one object (several boxes drawn over one label): merge them."""
    out: list[tuple[np.ndarray, str]] = []
    k = np.ones((5, 5), np.uint8)
    for m, how in masks:
        grown = cv2.dilate(m.astype(np.uint8), k).astype(bool)
        hit = [j for j, (o, _) in enumerate(out) if (grown & o).any()]
        for j in sorted(hit, reverse=True):
            o, oh = out.pop(j)
            m = m | o
            how = how if how == oh else "mixed"
        out.append((m, how))
    return out


def initial_regions(cv2, frame: np.ndarray, params: dict, use_sam: bool) -> list[tuple[list[np.ndarray], str]]:
    """The outline on the marked frame, as separate regions [(polys, how)], how = sam | paint | box | mixed.

    Brushed shapes (and anything with erased parts) are the user's exact shape. Boxes, alone or several joined with
    ';', are outlined by SAM when it's available. Each separate region is tracked on its own."""
    from .track import region_mask

    h, w = frame.shape[:2]
    bx, by, bw, bh = (float(v) for v in params["box"])
    items = params.get("paint") or []
    flat = {**params, "feather": 0, "expand": 1.0}
    masks: list[tuple[np.ndarray, str]] = []
    if params.get("shape") == "paint" and items:
        if any("rect" not in it or it.get("erase") for it in items):
            m = region_mask(cv2, flat, params["box"], w, h, "mask") > 127
            n, lab = cv2.connectedComponents(m.astype(np.uint8), connectivity=8)
            masks = [(lab == k, "paint") for k in range(1, n)]
        else:
            for it in items:
                r = region_mask(cv2, {**flat, "paint": [it]}, params["box"], w, h, "mask") > 127
                ys, xs = np.nonzero(r)
                if not len(xs):
                    continue
                sm = _sam_region(cv2, frame, xs.min(), ys.min(), xs.max(), ys.max()) if use_sam else None
                masks.append((sm, "sam") if sm is not None else (r, "box"))
    else:
        sm = _sam_region(cv2, frame, bx * w, by * h, (bx + bw) * w, (by + bh) * h) if use_sam else None
        if sm is not None:
            masks = [(sm, "sam")]
        else:
            rect = np.float32([[bx * w, by * h], [(bx + bw) * w, by * h], [(bx + bw) * w, (by + bh) * h], [bx * w, (by + bh) * h]])
            masks = [(poly_mask(cv2, [rect], w, h) > 127, "box")]
    masks = _merge_regions(cv2, [(m, hw) for m, hw in masks if m.sum() >= 12])
    total = sum(int(m.sum()) for m, _ in masks) or 1
    regions = [(contours_of(cv2, m), hw) for m, hw in masks if m.sum() >= 0.02 * total]
    regions = [(pl, hw) for pl, hw in regions if pl]
    if not regions:
        rect = np.float32([[bx * w, by * h], [(bx + bw) * w, by * h], [(bx + bw) * w, (by + bh) * h], [bx * w, (by + bh) * h]])
        regions = [([rect], "box")]
    return regions


def initial_outline(cv2, frame: np.ndarray, params: dict, use_sam: bool) -> tuple[list[np.ndarray], str]:
    """All regions' outlines together (for callers that want one shape)."""
    regions = initial_regions(cv2, frame, params, use_sam)
    hows = {hw for _, hw in regions}
    return [p for pl, _ in regions for p in pl], hows.pop() if len(hows) == 1 else "mixed"


def track_outline(pdir: Path, project: Project, source_id: str, params: dict, log: Callable[[str], None]) -> dict:
    from .track import _frames

    src = project.sources[source_id]
    path = resolve_media(pdir, src.path)
    w, h, fps = source_dims(src)
    n_frames = max(1, int(round(src.duration * fps)))
    use_sam = sam.wanted(params.get("sam"))
    st = path.stat()
    key_data = [str(path), st.st_size, st.st_mtime, params["box"], params.get("paint"), params.get("shape"), params.get("at"),
                params.get("range"), params.get("smooth", 0.6), params.get("snap", True), use_sam, EFFECT_VERSION, VERSION]
    key = hashlib.sha1(json.dumps(key_data, default=str).encode()).hexdigest()[:16]
    cache = cache_dir(pdir) / f"outline-{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    need_compute("object tracking")
    cv2 = require("cv2", "ai")
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
    regions = initial_regions(cv2, first, params, use_sam)
    hows = sorted({hw for _, hw in regions})
    how = hows[0] if len(hows) == 1 else "mixed"
    log(f"Tracking the outline in {path.name} {a:.1f}-{b:.1f}s ({len(regions)} region(s): {', '.join(hows)}; "
        f"{sum(len(p) for pl, _ in regions for p in pl)} points; one-time)...")
    radius = 0.004 * max(pw, ph) if params.get("snap", True) else 0
    patience = max(3, int(0.5 * fps))
    anchors = [f0]
    sam_calls = 0
    per_region: list[tuple[dict, dict, dict]] = []  # each region's (outline, confidence, anchor) per frame
    for polys0, rhow in regions:
        out: dict[int, list[np.ndarray] | None] = {f0: polys0}
        conf: dict[int, float] = {f0: 1.0}
        anchor_of: dict[int, int] = {f0: f0}
        for frames in (((i, f) for i, f in enumerate(read(f0 / fps, (fb - f0) / fps), start=f0) if i < fb),
                       _reverse_chunks(read, fps, fa, f0)):
            tr = _Dir(cv2, first, polys0, fps, use_sam and rhow == "sam")
            misses, last, stopped = 0, polys0, False
            thumb = _thumb(cv2, first)
            for i, frame in frames:
                if i == f0:
                    continue
                if stopped:
                    out[i] = None
                    continue
                th = _thumb(cv2, frame)
                if is_cut(cv2, thumb, th):  # another shot: this object isn't in it (or not where it was)
                    stopped = True
                    out[i] = None
                    continue
                thumb = th
                polys, c = tr.step(frame, i)
                if polys is None:
                    misses += 1
                    if misses > patience:  # gone: stop here rather than stick to something else
                        stopped = True
                        out[i] = None
                        continue
                    polys, c = last, 0.0  # a short loss (blur, a hand passing): hold the last outline
                else:
                    misses = 0
                    before = tr.anchor_i
                    polys = tr.maybe_reanchor(frame, polys, c, i)
                    if tr.anchor_i != before:
                        anchors.append(i)
                        c = 1.0
                last = polys
                if radius:
                    g = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (0, 0), 1.0).astype(np.float32)
                    grad = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
                    polys = [snap(cv2, grad, p, radius) for p in polys]
                out[i], conf[i], anchor_of[i] = polys, c, tr.anchor_i
            sam_calls += tr.sam_calls
        per_region.append((out, conf, anchor_of))
    # one outline per frame: every region still in view (None only when all of them are gone)
    out, conf, anchor_of = {}, {}, {}
    for i in range(fa, fb):
        parts = [r[0].get(i) for r in per_region]
        seen = [(pl, r[1].get(i, 0.0)) for pl, r in zip(parts, per_region) if pl is not None]
        out[i] = [p for pl, _ in seen for p in pl] if seen else None
        conf[i] = min((c for _, c in seen), default=0.0)
        anchor_of[i] = hash(tuple((r[2].get(i, -1), len(pl) if pl is not None else -1) for pl, r in zip(parts, per_region)))
    idx = list(range(fa, fb))
    seq = _smooth_runs([out.get(i) for i in idx], [anchor_of.get(i, -1) for i in idx], float(params.get("smooth", 0.6)))
    boxes = np.tile(np.array([float(v) for v in params["box"]]), (n_frames, 1))
    norm = []
    last_box = None
    for k, i in enumerate(idx):
        pl = seq[k]
        if pl is None:
            norm.append(None)
            if last_box is not None:
                boxes[i] = last_box
            continue
        allp = np.concatenate(pl, 0)
        x0, y0 = np.clip(allp.min(0), 0, [pw, ph])
        x1, y1 = np.clip(allp.max(0), 0, [pw, ph])
        last_box = [x0 / pw, y0 / ph, max(1e-3, (x1 - x0) / pw), max(1e-3, (y1 - y0) / ph)]
        boxes[i] = last_box
        norm.append([np.round(p / [pw, ph], 5).tolist() for p in pl])
    if fb > fa:
        boxes[:fa] = boxes[fa]
        boxes[fb:] = boxes[fb - 1]
    lost = sum(1 for x in norm if x is None)
    low = sum(1 for i in idx if out.get(i) is not None and conf.get(i, 1.0) < LOW_CONF)
    data = {"fps": fps, "boxes": np.round(boxes, 5).tolist(), "how": how, "sam_calls": sam_calls, "anchors": sorted(anchors),
            "outline": {"start": fa, "polys": norm, "conf": [round(conf.get(i, 0.0), 3) for i in idx]}}
    if lost:
        log(f"  object out of view / lost in {lost} frame(s): no mask there")
    if low:
        log(f"  low tracking confidence in {low} frame(s)")
    cache.write_text(json.dumps(data))
    return data


def frame_polys(data: dict, i: int) -> list[np.ndarray] | None | bool:
    """Normalised outline polygons at source frame i; None = lost there; False = this track has no outline."""
    ol = data.get("outline")
    if not ol:
        return False
    k = i - ol["start"]
    if k < 0 or k >= len(ol["polys"]):
        return None
    pl = ol["polys"][k]
    return None if pl is None else [np.asarray(p, np.float32) for p in pl]

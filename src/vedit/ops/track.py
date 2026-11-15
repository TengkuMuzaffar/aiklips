"""Object tracking for region masks and object focus.

A region is picked once (a box drawn in the editor, coordinates from the AI, or an
object detected by name) and followed through the clip with OpenCV's CSRT tracker,
forwards and backwards from the moment it was picked. Boxes are smoothed so masks and
camera moves glide instead of jitter, and cached per source like other effects.

Effects built on it:
  mask   blur / pixelate / colour-fill a region, feathered, following the object
  focus  spotlight (blur + dim everything except the object) or follow (reframe the
         shot so the camera pans with the object)
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from .. import media
from ..project import Clip, Effect, Project, Source, VeditError, resolve_media
from .effects import EFFECT_VERSION, Writer, cache_dir, model_path, proc_size, require, source_dims

TRACK_SIDE = 640  # tracking resolution (long side)
MASK_SIDE = 960  # mask video resolution (long side); scaled up when compositing
DETECTOR_URL = "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float32/latest/efficientdet_lite0.tflite"
STYLES = ("blur", "pixelate", "fill", "dim")


# ---- geometry: canvas <-> source ---------------------------------------------------

def _clip_zoom_at(clip: Clip, u: float) -> float:
    """Zoom at fraction u (0..1) through the clip."""
    z = max(1.0, clip.zoom)
    if clip.zoom_to:
        z = z + (max(1.0, clip.zoom_to) - z) * min(max(u, 0.0), 1.0)
    return z


def canvas_to_source(project: Project, clip: Clip, x: float, y: float, u: float = 0.0) -> tuple[float, float]:
    """A canvas pixel (as rendered by graph.fit_filters) -> source pixel."""
    src = project.sources[clip.source]
    sw, sh = src.width or 1080, src.height or 1920
    W, H = project.canvas.width, project.canvas.height
    fx, fy = min(max(clip.focus_x, 0), 1), min(max(clip.focus_y, 0), 1)
    if clip.zoom_to:  # cover at 2W x 2H, then a centred zoompan
        z = _clip_zoom_at(clip, u)
        s1 = max(2 * W / sw, 2 * H / sh)
        ox, oy = (sw * s1 - 2 * W) * fx, (sh * s1 - 2 * H) * fy
        bx, by = W - W / z + 2 * x / z, H - H / z + 2 * y / z
        return (bx + ox) / s1, (by + oy) / s1
    z = max(1.0, clip.zoom)
    if clip.fit == "cover":
        s = max(W * z / sw, H * z / sh)
        ox, oy = (sw * s - W) * fx, (sh * s - H) * fy
        return (x + ox) / s, (y + oy) / s
    s = min(W * z / sw, H * z / sh)  # contain / blur: foreground is contained
    iw, ih = sw * s, sh * s
    ow, oh = min(iw, W), min(ih, H)
    cx, cy = (iw - ow) * fx, (ih - oh) * fy
    px, py = (W - ow) / 2, (H - oh) / 2
    return (x - px + cx) / s, (y - py + cy) / s


def parse_box(spec: str, total_w: float, total_h: float) -> tuple[float, float, float, float]:
    """'x,y,w,h' (top-left + size) in px or percent ('10%,20%,30%,15%')."""
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != 4:
        raise VeditError("--box must be x,y,w,h (top-left corner + size), in px or %, e.g. 10%,30%,40%,12%")

    def val(v: str, total: float) -> float:
        try:
            return float(v[:-1]) / 100 * total if v.endswith("%") else float(v)
        except ValueError:
            raise VeditError(f"Bad --box value '{v}'") from None

    x, y, w, h = val(parts[0], total_w), val(parts[1], total_h), val(parts[2], total_w), val(parts[3], total_h)
    if w <= 0 or h <= 0:
        raise VeditError("--box width and height must be positive")
    return x, y, w, h


def box_to_source(project: Project, clip: Clip, box: tuple[float, float, float, float], u: float) -> list[float]:
    """Canvas box -> normalised source box [x, y, w, h] (0..1), clamped to the frame."""
    src = project.sources[clip.source]
    sw, sh = src.width or 1080, src.height or 1920
    x0, y0 = canvas_to_source(project, clip, box[0], box[1], u)
    x1, y1 = canvas_to_source(project, clip, box[0] + box[2], box[1] + box[3], u)
    x0, x1 = sorted((min(max(x0, 0), sw), min(max(x1, 0), sw)))
    y0, y1 = sorted((min(max(y0, 0), sh), min(max(y1, 0), sh)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise VeditError("That box is outside the video picture for this clip (it may be on padding or a blurred border)")
    return [round(x0 / sw, 5), round(y0 / sh, 5), round((x1 - x0) / sw, 5), round((y1 - y0) / sh, 5)]


def locate(project: Project, t: float) -> tuple[Clip, float, float]:
    """Timeline seconds -> (clip, source seconds, fraction through the clip)."""
    starts = project.clip_starts()
    for c, s in zip(project.clips, starts):
        if s - 1e-6 <= t < s + c.duration or c is project.clips[-1]:
            off = min(max(t - s, 0.0), c.duration)
            return c, c.in_ + off * c.speed, off / c.duration if c.duration else 0.0
    raise VeditError("Timeline is empty")


def region(p: Project, d: Path, clip: str | None, at: float | None, box: str | None, obj: str | None,
           space: str = "canvas") -> tuple[Clip, dict, str]:
    """Where a mask / focus goes: (clip, params with a source-normalised box, note).

    box is "x,y,w,h" (top-left + size) in canvas px / % (or source px / % with space="source").
    """
    if clip:
        c = p.clip(clip)
        start = p.clip_starts()[p.clips.index(c)]
        if at is None:
            at = start
        if not start - 1e-3 <= at <= start + c.duration + 1e-3:
            raise VeditError(f"--at {at} is not inside {c.id} ({start:.2f}-{start + c.duration:.2f}s)")
        at = min(max(at, start), start + c.duration - 1e-3)
    elif at is None:
        if len(p.clips) != 1:
            raise VeditError("Say which clip with --clip, or give --at <timeline seconds> on the moment you mean")
        at = 0.0
    c, t_src, u = locate(p, at)
    src = p.sources[c.source]
    if not src.has_video:
        raise VeditError(f"{c.id} has no video")
    note = ""
    if obj:
        nbox, name, score = detect(resolve_media(d, src.path), src, t_src, obj)
        note = f"found {name} ({score:.0%})"
    elif box:
        if space == "source":
            sw, sh = src.width or 1080, src.height or 1920
            x, y, w, h = parse_box(box, sw, sh)
            nbox = [round(x / sw, 5), round(y / sh, 5), round(w / sw, 5), round(h / sh, 5)]
        else:
            nbox = box_to_source(p, c, parse_box(box, p.canvas.width, p.canvas.height), u)
    else:
        raise VeditError("Give --box x,y,w,h (canvas px or %, top-left + size) or --object NAME (e.g. person, car, bottle, any)")
    return c, {"box": nbox, "at": round(t_src, 3), "range": [c.in_, c.out]}, note


def new_mask_id(p: Project) -> str:
    used = {e.params.get("id") for c in p.clips for e in c.effects if e.type == "mask"}
    n = 1
    while f"mk{n}" in used:
        n += 1
    return f"mk{n}"


# ---- frames -------------------------------------------------------------------------------

def _frames(path: Path, w: int, h: int, fps: float, start: float, dur: float) -> Iterator[np.ndarray]:
    proc = subprocess.Popen(
        [media.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{start:.4f}", "-t", f"{dur:.4f}",
         "-i", str(path), "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,  # stopping early = "broken pipe" noise
    )
    assert proc.stdout is not None
    size = w * h * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        proc.wait()


# ---- detection ------------------------------------------------------------------------------

def detect(path: Path, src: Source, t_src: float, label: str) -> tuple[list[float], str, float]:
    """Find an object by COCO name ('person', 'car', 'bottle', 'cell phone'...) or 'any' at a source time."""
    mp = require("mediapipe", "ai")
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core.base_options import BaseOptions

    w, h, fps = source_dims(src)
    pw, ph = proc_size(w, h, TRACK_SIDE)
    frame = next(_frames(path, pw, ph, fps, t_src, 1.0 / fps * 2), None)
    if frame is None:
        raise VeditError(f"Could not read a frame at {t_src:.2f}s of {path.name}")
    opts = vision.ObjectDetectorOptions(
        base_options=BaseOptions(model_asset_path=str(model_path("efficientdet_lite0.tflite", DETECTOR_URL))),
        score_threshold=0.25, max_results=10,
    )
    with vision.ObjectDetector.create_from_options(opts) as det:
        res = det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame[:, :, ::-1])))
    want = label.strip().lower()
    found = []
    for d in res.detections:
        name = d.categories[0].category_name.lower()
        if want in ("any", "auto", "main", "object") or name == want:
            b = d.bounding_box
            found.append((d.categories[0].score * (b.width * b.height) ** 0.5, name, d.categories[0].score,
                          [b.origin_x / pw, b.origin_y / ph, b.width / pw, b.height / ph]))
    if not found:
        seen = sorted({d.categories[0].category_name for d in res.detections})
        raise VeditError(f"No '{label}' found at {t_src:.2f}s (seen: {', '.join(seen) or 'nothing'}). "
                         "Try another time, another name, or give --box.")
    _, name, score, box = max(found)
    return [round(min(max(v, 0.0), 1.0), 5) for v in box], name, round(score, 3)


# ---- tracking -------------------------------------------------------------------------------

def _create_tracker():
    cv2 = require("cv2", "ai")
    for make in ("TrackerCSRT_create", "legacy_TrackerCSRT_create", "TrackerKCF_create"):
        fn = getattr(cv2, make, None) or getattr(getattr(cv2, "legacy", None), make.replace("legacy_", ""), None)
        if fn:
            return fn()
    raise VeditError("This OpenCV build has no object tracker. Run: uv sync --extra ai")


def _run(frames: list[np.ndarray] | Iterator[np.ndarray], box_px: tuple[int, int, int, int]) -> list[list[float] | None]:
    tracker = None
    out: list[list[float] | None] = []
    last = list(box_px)
    for i, f in enumerate(frames):
        if tracker is None:
            tracker = _create_tracker()
            tracker.init(f, tuple(int(v) for v in box_px))
            out.append([float(v) for v in box_px])
            continue
        ok, b = tracker.update(f)
        if ok and b[2] > 1 and b[3] > 1:
            last = [float(v) for v in b]
            out.append(last)
        else:
            out.append(None)  # lost: filled from neighbours later
    return out


def _smooth(boxes: np.ndarray, strength: float) -> np.ndarray:
    """Zero-phase exponential smoothing (forward + backward) so motion stays in sync."""
    a = min(max(strength, 0.0), 0.97)
    if a <= 0 or len(boxes) < 3:
        return boxes
    out = boxes.copy()
    for rng in (range(1, len(out)), range(len(out) - 2, -1, -1)):
        prev = None
        for i in rng:
            j = i - 1 if rng.step == 1 else i + 1
            prev = out[j] if prev is None else prev
            out[i] = a * prev + (1 - a) * out[i]
            prev = out[i]
    return out


def track(pdir: Path, project: Project, clip_source: str, params: dict, log: Callable[[str], None]) -> dict:
    """Boxes (normalised x,y,w,h) for every source frame; cached by source + box + range."""
    src = project.sources[clip_source]
    path = resolve_media(pdir, src.path)
    w, h, fps = source_dims(src)
    n_frames = max(1, int(round(src.duration * fps)))
    box = [float(v) for v in params["box"]]
    st = path.stat()
    key_data = [str(path), st.st_size, st.st_mtime, box, params.get("at"), params.get("range"), params.get("track", True), EFFECT_VERSION, "t2"]
    key = hashlib.sha1(json.dumps(key_data).encode()).hexdigest()[:16]
    cache = cache_dir(pdir) / f"objtrack-{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())

    boxes = np.tile(np.array(box, np.float64), (n_frames, 1))
    if params.get("track", True):
        pw, ph = proc_size(w, h, TRACK_SIDE)
        a, b = params.get("range") or [0.0, src.duration]
        at = min(max(float(params.get("at", a)), a), b)
        fa, fb, f0 = int(a * fps), min(n_frames, int(np.ceil(b * fps)) + 1), min(n_frames - 1, int(round(at * fps)))
        bp = (int(box[0] * pw), int(box[1] * ph), max(4, int(box[2] * pw)), max(4, int(box[3] * ph)))
        log(f"Tracking the object in {path.name} {a:.1f}-{b:.1f}s (one-time)...")
        fwd = _run(_frames(path, pw, ph, fps, f0 / fps, (fb - f0) / fps), bp)
        back: list[list[float] | None] = []
        if f0 > fa:
            frames = list(_frames(path, pw, ph, fps, fa / fps, (f0 - fa + 1) / fps))
            back = _run(reversed(frames), bp)[1:][::-1]
            del frames
        seq = back + fwd
        raw = np.full((len(seq), 4), np.nan)
        for i, bx in enumerate(seq):
            if bx is not None:
                raw[i] = [bx[0] / pw, bx[1] / ph, bx[2] / pw, bx[3] / ph]
        idx = np.arange(len(seq))
        good = ~np.isnan(raw[:, 0])
        if good.sum() >= 1:
            for k in range(4):
                raw[:, k] = np.interp(idx, idx[good], raw[good, k])
        lost = int((~good).sum())
        raw = _smooth(raw, float(params.get("smooth", 0.6)))
        start = max(0, f0 - len(back))
        end = min(n_frames, start + len(raw))
        boxes[start:end] = raw[: end - start]
        boxes[:start] = boxes[start]
        boxes[end:] = boxes[end - 1]
        if lost:
            log(f"  object lost in {lost} frame(s); held its last position there")
    data = {"fps": fps, "boxes": np.round(boxes, 5).tolist()}
    cache.write_text(json.dumps(data))
    return data


# ---- effect artifacts ---------------------------------------------------------------------

def render_mask(pdir: Path, project: Project, source_id: str, eff: Effect, out: Path, log: Callable[[str], None]) -> Path:
    """Gray mask video for the whole source: white = region (or everything else when inverted)."""
    cv2 = require("cv2", "ai")
    p = eff.params
    data = track(pdir, project, source_id, p, log)
    src = project.sources[source_id]
    w, h, fps = source_dims(src)
    mw, mh = proc_size(w, h, MASK_SIDE)
    expand = float(p.get("expand", 1.0))
    feather = float(p.get("feather", 0.04 if eff.type == "mask" else 0.12))  # fraction of the short side
    fpx = max(0.0, feather * min(mw, mh))
    shape = p.get("shape", "rect" if eff.type == "mask" else "ellipse")
    invert = bool(p.get("invert", eff.type == "focus"))
    wr = Writer(out, mw, mh, fps, "gray")
    for bx, by, bw, bh in data["boxes"]:
        cx, cy = (bx + bw / 2) * mw, (by + bh / 2) * mh
        hw, hh = bw * mw * expand / 2, bh * mh * expand / 2
        m = np.zeros((mh, mw), np.uint8)
        if shape == "ellipse":
            cv2.ellipse(m, (int(cx), int(cy)), (max(1, int(hw)), max(1, int(hh))), 0, 0, 360, 255, -1, cv2.LINE_AA)
        else:
            r = int(min(hw, hh) * 0.25)  # softly rounded corners
            x0, y0, x1, y1 = int(cx - hw), int(cy - hh), int(cx + hw), int(cy + hh)
            cv2.rectangle(m, (x0 + r, y0), (x1 - r, y1), 255, -1)
            cv2.rectangle(m, (x0, y0 + r), (x1, y1 - r), 255, -1)
            for qx, qy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
                cv2.circle(m, (qx, qy), r, 255, -1, cv2.LINE_AA)
        if fpx >= 1:
            # grow by half the feather first so the fully-covered core is the box itself
            k = int(fpx / 2) * 2 + 1
            m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
            m = cv2.GaussianBlur(m, (0, 0), fpx / 2)
        wr.write(255 - m if invert else m)
    return wr.close()


def _expr(ts: list[float], vs: list[int]) -> str:
    """Piecewise-linear FFmpeg expression of t through (ts, vs), as a balanced if-tree (shallow nesting)."""
    T = f"clip(t,{ts[0]:.3f},{ts[-1]:.3f})"

    def node(lo: int, hi: int) -> str:
        if hi - lo == 1:
            a, b = vs[lo], vs[hi]
            if a == b:
                return str(a)
            return f"({a}+{b - a}*({T}-{ts[lo]:.3f})/{ts[hi] - ts[lo]:.3f})"
        mid = (lo + hi) // 2
        return f"if(lt({T},{ts[mid]:.3f}),{node(lo, mid)},{node(mid, hi)})"

    if len(ts) == 1:
        return str(vs[0])
    return node(0, len(ts) - 1)


def follow_crop(project: Project, pdir: Path, clip: Clip, eff: Effect, s0: float, slen: float, log) -> str:
    """crop=... filter that pans a canvas-shaped window to keep the tracked object centred."""
    data = track(pdir, project, clip.source, eff.params, log)
    src = project.sources[clip.source]
    sw, sh = src.width or 1080, src.height or 1920
    W, H = project.canvas.width, project.canvas.height
    ar = W / H
    cw, ch = (sh * ar, sh) if sw / sh > ar else (sw, sw / ar)
    z = float(eff.params.get("zoom") or (1.0 if abs(cw - sw) > 2 or abs(ch - sh) > 2 else 1.4))
    cw, ch = max(16, int(cw / z) // 2 * 2), max(16, int(ch / z) // 2 * 2)
    fps = data["fps"]
    boxes = np.asarray(data["boxes"])
    # extra camera smoothing: a camera operator doesn't follow every twitch
    centres = np.stack([(boxes[:, 0] + boxes[:, 2] / 2) * sw, (boxes[:, 1] + boxes[:, 3] / 2) * sh], 1)
    centres = _smooth(centres, float(eff.params.get("camera_smooth", 0.85)))
    step = max(1, int(round(fps * 0.2)))  # a keyframe every ~0.2s, linear in between
    f_a, f_b = int(s0 * fps), min(len(centres) - 1, int(np.ceil((s0 + slen) * fps)))
    frames = list(range(f_a, max(f_a, f_b) + 1, step))
    if frames[-1] != max(f_a, f_b):
        frames.append(max(f_a, f_b))
    ts, xs, ys = [], [], []
    for f in frames:
        cx, cy = centres[min(f, len(centres) - 1)]
        ts.append(max(0.0, f / fps - s0))
        xs.append(int(min(max(cx - cw / 2, 0), sw - cw)))
        ys.append(int(min(max(cy - ch / 2, 0), sh - ch)))
    return f"crop=w={cw}:h={ch}:x='{_expr(ts, xs)}':y='{_expr(ts, ys)}'"

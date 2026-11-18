"""Object tracking for region masks and object focus.

A region is picked once (a box drawn in the editor, coordinates from the AI, or an
object detected by name) and followed through the clip with OpenCV's CSRT tracker,
forwards and backwards from the moment it was picked. Boxes are smoothed so masks and
camera moves glide instead of jitter, and cached per source like other effects.

Effects built on it:
  mask   remove (fill from the surrounding background) / blur / pixelate / colour-fill a
         region, feathered, following the object. The region is a box, several boxes,
         or a shape painted with a brush / grid in the editor.
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
REMOVE_SIDE = 1280  # resolution of the reconstructed background for style "remove"
REMOVE_VERSION = "remove-10"  # bump when the reconstruction changes (part of the cache key); 10 = no ghost print, margin
MASK_VERSION = "mask-2"  # bump when region_mask changes (part of the cache key); 2 = erase strokes survive the feather
DETECTOR_URL = "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float32/latest/efficientdet_lite0.tflite"
STYLES = ("remove", "blur", "pixelate", "fill", "dim")
FOCUS_MODES = ("spotlight", "follow", "both")
# how a focused object is highlighted (render/graph.py `_focus_composite`); all compositing only, no re-tracking
FOCUS_STYLES = ("spotlight", "colorpop", "brighten", "glow", "outline")
FOCUS_LOOK = ("style", "color", "width", "strength", "blur", "dim")  # params `focus set` may change


def focus_params(params: dict, mode: str = "spotlight", style: str = "spotlight", *, color: str | None = None,
                 width: float | None = None, strength: float | None = None, blur: float = 14, dim: float = 0.35,
                 feather: float | None = None, expand: float | None = None, smooth: float = 0.6, track: bool = True,
                 zoom: float | None = None) -> dict:
    """Complete a focus effect's params from a picked region (`region()` output). Painted regions keep their shape and
    hug the object (no margin, light feather); boxes become a roomy feathered ellipse, as before."""
    if mode not in FOCUS_MODES:
        raise VeditError(f"mode must be one of {', '.join(FOCUS_MODES)}")
    if style not in FOCUS_STYLES:
        raise VeditError(f"style must be one of {', '.join(FOCUS_STYLES)}")
    painted = params.get("shape") == "paint"
    out = {**params, "mode": mode, "style": style, "blur": blur, "dim": dim, "smooth": smooth, "track": track,
           "shape": "paint" if painted else "ellipse", "invert": True,
           "feather": feather if feather is not None else (0.03 if painted else 0.12),
           "expand": expand if expand is not None else (1.0 if painted else 1.35)}
    for k, v in (("color", color), ("width", width), ("strength", strength), ("zoom", zoom)):
        if v is not None:
            out[k] = v
    return out


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


def _to_source_norm(project: Project, clip: Clip, x: float, y: float, u: float) -> tuple[float, float]:
    src = project.sources[clip.source]
    sw, sh = src.width or 1080, src.height or 1920
    sx, sy = canvas_to_source(project, clip, x, y, u)
    return min(max(sx / sw, 0.0), 1.0), min(max(sy / sh, 0.0), 1.0)


def paint_to_source(project: Project, clip: Clip, paint: list[dict], u: float) -> tuple[list[float], list[dict]]:
    """Painted region in canvas px -> (bounding box, items) in normalised source coordinates.

    Items are brush strokes {"pts": [[x, y], ...], "r": radius} or rectangles {"rect": [x, y, w, h]},
    either with "erase": true to cut away. The bounding box of what's painted is what gets tracked.
    """
    if not isinstance(paint, list) or not paint:
        raise VeditError("Nothing painted: paint over the region first")
    src = project.sources[clip.source]
    sw = src.width or 1080
    items, x0, y0, x1, y1 = [], 1.0, 1.0, 0.0, 0.0
    for it in paint:
        erase = bool(it.get("erase"))
        if "rect" in it:
            x, y, w, h = (float(v) for v in it["rect"])
            ax, ay = _to_source_norm(project, clip, x, y, u)
            bx, by = _to_source_norm(project, clip, x + w, y + h, u)
            if bx - ax <= 0 or by - ay <= 0:
                continue
            items.append({"rect": [round(ax, 5), round(ay, 5), round(bx - ax, 5), round(by - ay, 5)], "erase": erase})
            if not erase:
                x0, y0, x1, y1 = min(x0, ax), min(y0, ay), max(x1, bx), max(y1, by)
        else:
            pts = [_to_source_norm(project, clip, float(px), float(py), u) for px, py in it.get("pts", [])]
            if not pts:
                continue
            r_can = max(1.0, float(it.get("r", 20)))
            p0 = canvas_to_source(project, clip, 0, 0, u)
            p1 = canvas_to_source(project, clip, r_can, 0, u)
            r = abs(p1[0] - p0[0]) / sw
            items.append({"pts": [[round(a, 5), round(b, 5)] for a, b in pts], "r": round(r, 5), "erase": erase})
            if not erase:
                rh = r * sw / (src.height or 1920)
                x0, y0 = min(x0, min(a for a, _ in pts) - r), min(y0, min(b for _, b in pts) - rh)
                x1, y1 = max(x1, max(a for a, _ in pts) + r), max(y1, max(b for _, b in pts) + rh)
    x0, y0, x1, y1 = max(x0, 0.0), max(y0, 0.0), min(x1, 1.0), min(y1, 1.0)
    if x1 - x0 < 0.003 or y1 - y0 < 0.003:
        raise VeditError("The painted region is outside the video picture for this clip (or too small)")
    return [round(x0, 5), round(y0, 5), round(x1 - x0, 5), round(y1 - y0, 5)], items


def locate(project: Project, t: float) -> tuple[Clip, float, float]:
    """Timeline seconds -> (clip, source seconds, fraction through the clip)."""
    starts = project.clip_starts()
    for c, s in zip(project.clips, starts):
        if s - 1e-6 <= t < s + c.duration or c is project.clips[-1]:
            off = min(max(t - s, 0.0), c.duration)
            return c, c.in_ + off * c.speed, off / c.duration if c.duration else 0.0
    raise VeditError("Timeline is empty")


def keep_region(p: Project, c: Clip, u: float, keep: list[dict] | str | None) -> dict | None:
    """What must stay on top of a mask (the product in front of a label): painted items or "x,y,w,h;..." boxes
    in canvas px / %. Tracked on its own, since it moves differently from the thing being hidden."""
    if not keep:
        return None
    if isinstance(keep, str):
        keep = [{"rect": list(parse_box(part, p.canvas.width, p.canvas.height))} for part in keep.split(";") if part.strip()]
    kbox, kitems = paint_to_source(p, c, [{k: v for k, v in it.items() if k != "keep"} for it in keep], u)
    return {"box": kbox, "paint": kitems}


def region(p: Project, d: Path, clip: str | None, at: float | None, box: str | None, obj: str | None,
           space: str = "canvas", paint: list[dict] | None = None, keep: list[dict] | str | None = None) -> tuple[Clip, dict, str]:
    """Where a mask / focus goes: (clip, params with a source-normalised box, note).

    box is "x,y,w,h" (top-left + size) in canvas px / % (or source px / % with space="source");
    several boxes separated by ";" make one combined region. paint is a painted region in canvas px
    (see paint_to_source).
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
    if paint:  # painted "keep" strokes are a separate region
        keep = [it for it in paint if it.get("keep")] + (keep if isinstance(keep, list) else [])
        paint = [it for it in paint if not it.get("keep")]
    kept = keep_region(p, c, u, keep)

    def done(params: dict) -> tuple[Clip, dict, str]:
        if kept:
            params["keep"] = kept
        return c, params, note

    if box and ";" in box and not paint:  # several boxes -> one painted region of rectangles
        paint = []
        for part in (b for b in box.split(";") if b.strip()):
            if space == "source":
                raise VeditError("Several boxes only work in canvas space")
            paint.append({"rect": list(parse_box(part, p.canvas.width, p.canvas.height))})
    if paint:
        nbox, items = paint_to_source(p, c, paint, u)
        return done({"box": nbox, "at": round(t_src, 3), "range": [c.in_, c.out], "shape": "paint", "paint": items})
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
    return done({"box": nbox, "at": round(t_src, 3), "range": [c.in_, c.out]})


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
    """Boxes (normalised x,y,w,h) for every source frame; cached by source + box + range. With tracker "outline"
    (point tracking, ops/outline.py) or "segment" (SAM 2 video, ops/segment.py; "auto" picks it when SAM is
    installed) also the object's outline per frame."""
    from .outline import resolve_tracker

    how = resolve_tracker(params) if params.get("track", True) else "box"
    if how == "segment":
        from .segment import track_segment

        return track_segment(pdir, project, clip_source, params, log)
    if how == "outline":
        from .outline import track_outline

        return track_outline(pdir, project, clip_source, params, log)
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
    from .effects import need_compute

    need_compute("object tracking")
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

class KeepMasks:
    """Per-frame mask of what must stay on top (params["keep"]), following its own tracker."""

    def __init__(self, cv2, pdir: Path, project: Project, source_id: str, p: dict, log: Callable[[str], None]):
        self.cv2, self.k = cv2, p.get("keep")
        self.boxes = None
        if self.k:
            kp = {"box": self.k["box"], "at": self.k.get("at", p.get("at")), "range": p.get("range"), "track": p.get("track", True),
                  "smooth": p.get("smooth", 0.6)}
            self.boxes = track(pdir, project, source_id, kp, log)["boxes"]
            self.prm = {"box": self.k["box"], "shape": "paint", "paint": self.k["paint"], "expand": 1.0, "feather": 0}

    def __call__(self, i: int, mw: int, mh: int) -> np.ndarray | None:
        if self.boxes is None:
            return None
        m = region_mask(self.cv2, self.prm, self.boxes[min(i, len(self.boxes) - 1)], mw, mh)
        k = max(3, int(0.006 * max(mw, mh)) | 1)  # a hair of margin so no edge of it gets painted over
        return self.cv2.dilate(m, np.ones((k, k), np.uint8)) > 127


def mask_at(cv2, p: dict, data: dict, i: int, mw: int, mh: int, eff_type: str = "mask") -> np.ndarray:
    """The region's mask at source frame i: the tracked outline when there is one, else the tracked box."""
    from .outline import frame_polys

    boxes = data["boxes"]
    pl = frame_polys(data, i)
    box = boxes[min(i, len(boxes) - 1)]
    if pl is False:
        return region_mask(cv2, p, box, mw, mh, eff_type)
    return region_mask(cv2, p, box, mw, mh, eff_type, poly=pl or [])


def render_mask(pdir: Path, project: Project, source_id: str, eff: Effect, out: Path, log: Callable[[str], None]) -> Path:
    """Gray mask video for the whole source: white = region (or everything else when inverted)."""
    cv2 = require("cv2", "ai")
    p = eff.params
    data = track(pdir, project, source_id, p, log)
    src = project.sources[source_id]
    w, h, fps = source_dims(src)
    mw, mh = proc_size(w, h, MASK_SIDE)
    invert = bool(p.get("invert", eff.type == "focus"))
    keep = KeepMasks(cv2, pdir, project, source_id, p, log)
    wr = Writer(out, mw, mh, fps, "gray")
    for i in range(len(data["boxes"])):
        m = mask_at(cv2, p, data, i, mw, mh, eff.type)
        if invert:
            m = 255 - m
        km = keep(i, mw, mh)
        if km is not None:
            m = (m * (1 - cv2.GaussianBlur(km.astype(np.float32), (0, 0), 1.0))).astype(np.uint8)
        wr.write(m)
    return wr.close()


def region_mask(cv2, p: dict, box: list[float], mw: int, mh: int, eff_type: str = "mask",
                poly: list[np.ndarray] | None = None) -> np.ndarray:
    """Feathered 0-255 mask of the region at a tracked box (normalised x, y, w, h), or of a tracked outline
    (`poly`: normalised polygons from ops/outline.py; [] = the object is lost / out of view in this frame)."""
    bx, by, bw, bh = box
    expand = float(p.get("expand", 1.0))
    feather = float(p.get("feather", 0.04 if eff_type == "mask" else 0.12))  # fraction of the short side
    fpx = max(0.0, feather * min(mw, mh))
    if poly is not None:
        from .outline import poly_mask

        m = poly_mask(cv2, [np.asarray(q, np.float32) * [mw, mh] for q in poly], mw, mh)
        if expand > 1.0 and m.any():  # margin: a share of the object's size, so the edge pixels are covered too
            k = int((expand - 1.0) * 0.5 * np.sqrt(np.count_nonzero(m))) * 2 + 1
            if k >= 3:
                m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        if fpx >= 1 and m.any():
            k = int(fpx / 2) * 2 + 1
            m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
            m = cv2.GaussianBlur(m, (0, 0), fpx / 2)
        return m
    shape = p.get("shape", "rect" if eff_type == "mask" else "ellipse")
    cx, cy = (bx + bw / 2) * mw, (by + bh / 2) * mh
    hw, hh = bw * mw * expand / 2, bh * mh * expand / 2
    m = np.zeros((mh, mw), np.uint8)
    erased = None  # pixels the user erased: the feather must not grow back into them
    if shape == "paint" and p.get("paint"):
        # painted items live in source coords at the reference box; move/scale them with the tracked box
        r0 = [float(v) for v in p["box"]]
        sx, sy = bw / max(r0[2], 1e-6) * expand, bh / max(r0[3], 1e-6) * expand
        c0x, c0y = r0[0] + r0[2] / 2, r0[1] + r0[3] / 2

        def tx(x: float, y: float) -> tuple[int, int]:
            return int(round((cx / mw + (x - c0x) * sx) * mw)), int(round((cy / mh + (y - c0y) * sy) * mh))

        for it in p["paint"]:
            val = 0 if it.get("erase") else 255
            if "rect" in it:
                x, y, w, h = it["rect"]
                (ax, ay), (bx2, by2) = tx(x, y), tx(x + w, y + h)
                cv2.rectangle(m, (ax, ay), (bx2, by2), val, -1)
            else:
                pts = np.array([tx(x, y) for x, y in it["pts"]], np.int32)
                rad = max(1, int(round(float(it.get("r", 0.02)) * mw * (sx * sy) ** 0.5)))
                if len(pts) == 1:
                    cv2.circle(m, tuple(int(v) for v in pts[0]), rad, val, -1, cv2.LINE_AA)
                else:
                    cv2.polylines(m, [pts], False, val, rad * 2, cv2.LINE_AA)
                    for q in (pts[0], pts[-1]):  # round caps
                        cv2.circle(m, (int(q[0]), int(q[1])), rad, val, -1, cv2.LINE_AA)
        if any(it.get("erase") for it in p["paint"]):
            ref = np.zeros_like(m)  # the same items without erasing, to see which pixels erase strokes cleared
            for it in p["paint"]:
                if "rect" in it:
                    x, y, w, h = it["rect"]
                    (ax, ay), (bx2, by2) = tx(x, y), tx(x + w, y + h)
                    cv2.rectangle(ref, (ax, ay), (bx2, by2), 255, -1)
                else:
                    pts = np.array([tx(x, y) for x, y in it["pts"]], np.int32)
                    rad = max(1, int(round(float(it.get("r", 0.02)) * mw * (sx * sy) ** 0.5)))
                    cv2.polylines(ref, [pts], False, 255, rad * 2, cv2.LINE_AA)
                    for q in (pts[0], pts[-1]):
                        cv2.circle(ref, (int(q[0]), int(q[1])), rad, 255, -1, cv2.LINE_AA)
            erased = (ref > 128) & (m < 128)
    elif shape == "ellipse":
        cv2.ellipse(m, (int(cx), int(cy)), (max(1, int(hw)), max(1, int(hh))), 0, 0, 360, 255, -1, cv2.LINE_AA)
    else:
        r = int(min(hw, hh) * 0.25)  # softly rounded corners
        x0, y0, x1, y1 = int(cx - hw), int(cy - hh), int(cx + hw), int(cy + hh)
        cv2.rectangle(m, (x0 + r, y0), (x1 - r, y1), 255, -1)
        cv2.rectangle(m, (x0, y0 + r), (x1, y1 - r), 255, -1)
        for qx, qy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
            cv2.circle(m, (qx, qy), r, 255, -1, cv2.LINE_AA)
    if fpx >= 1:
        # grow by half the feather first so the fully-covered core is the region itself
        k = int(fpx / 2) * 2 + 1
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        m = cv2.GaussianBlur(m, (0, 0), fpx / 2)
    if erased is not None and erased.any():
        # keep erased areas clear after the grow + blur (only a slight soft edge where the eraser ran)
        soft = cv2.GaussianBlur(erased.astype(np.float32), (0, 0), 1.5)
        m = (m.astype(np.float32) * np.clip(1.0 - soft * 1.5, 0.0, 1.0)).astype(np.uint8)
    return m


# ---- style "remove": rebuild what's behind the region from its surroundings -------------------

def pull_push_fill(cv2, img: np.ndarray, hole: np.ndarray, known: np.ndarray | None = None) -> np.ndarray:
    """Fill hole pixels with a smooth membrane interpolated from the known pixels around them.

    Pull: average the known pixels down an image pyramid. Push: fill each level's gaps from the
    coarser level. No streaks or cone artefacts (unlike Telea), and stable from frame to frame
    because it only depends on the pixels around the hole. known may be a float weight map.
    """
    f = img.astype(np.float32)
    if known is None:
        w = (~hole).astype(np.float32)
    else:
        w = known.astype(np.float32) * (~hole)
    levels = [(f * w[..., None], w)]
    while min(levels[-1][1].shape) > 2:
        a, b = levels[-1]
        levels.append((cv2.resize(a, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA),
                       cv2.resize(b, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)))
    a, b = levels[-1]
    cur = a / np.maximum(b, 1e-6)[..., None]
    if (b <= 1e-6).any():  # coarsest cells with nothing known: use the mean of what is known
        mean = (f * w[..., None]).sum((0, 1)) / max(float(w.sum()), 1e-6)
        cur[b <= 1e-6] = mean
    for a, b in reversed(levels[:-1]):
        up = cv2.resize(cur, (b.shape[1], b.shape[0]), interpolation=cv2.INTER_LINEAR)
        k = np.clip(b * 4, 0, 1)[..., None]  # trust known samples, borrow the rest from below
        cur = np.where(b[..., None] > 1e-6, (a / np.maximum(b, 1e-6)[..., None]) * k + up * (1 - k), up)
    out = f.copy()
    out[hole] = cur[hole]
    return out


def remove_frame(cv2, frame: np.ndarray, m: np.ndarray, seed: int = 0, protect: np.ndarray | None = None) -> np.ndarray:
    """RGBA overlay (from a BGR frame) whose masked part shows the reconstructed background.

    protect marks pixels of something in front of the region (the product passing over the label):
    they are neither painted over nor used as samples for the fill, so it stays on top.
    """
    mh, mw = m.shape
    out = np.zeros((mh, mw, 4), np.uint8)
    prot = np.zeros((mh, mw), bool) if protect is None else protect
    hole = (m > 2) & ~prot
    if not hole.any():
        return out
    ys, xs = np.nonzero(hole)
    hy0, hy1, hx0, hx1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    pad = max(hy1 - hy0, hx1 - hx0) // 2 + 16
    y0, y1, x0, x1 = max(0, hy0 - pad), min(mh, hy1 + pad), max(0, hx0 - pad), min(mw, hx1 + pad)
    crop, hc, pc = frame[y0:y1, x0:x1], hole[y0:y1, x0:x1], prot[y0:y1, x0:x1]
    # only sample the band right around the hole, so far-away things (a dark edge, another object)
    # don't bleed into the middle of a big fill
    band = max(9, int(0.2 * min(hy1 - hy0, hx1 - hx0))) | 1
    near = cv2.dilate(hc.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (band, band))).astype(bool)
    ring = near & ~hc & ~pc
    weight = ring.astype(np.float32)
    if ring.sum() > 20:
        # favour pixels that look like the surface (the ring's typical colour) over edges of other things
        g = crop.astype(np.float32)
        med = np.median(g[ring], axis=0)
        dist = np.linalg.norm(g - med, axis=2)
        # scale from the closest third of the ring: a big chunk of another object in it doesn't widen it
        spread = max(14.0, float(np.percentile(dist[ring], 35)) * 2.0)
        weight *= np.exp(-((dist / spread) ** 2))
    filled = pull_push_fill(cv2, crop, hc, weight)
    # put back the grain / compression texture of the surroundings so the patch doesn't look smooth
    g = crop.astype(np.float32)
    hp = g - cv2.GaussianBlur(g, (0, 0), 1.5)
    if ring.sum() > 20:
        sd = 1.4826 * np.median(np.abs(hp[ring]), axis=0)  # robust: ignores edges in the ring
        noise = np.random.default_rng(seed).standard_normal(crop.shape).astype(np.float32)
        noise = cv2.GaussianBlur(noise, (0, 0), 0.7)
        noise *= sd / max(float(noise.std()), 1e-6)
        filled[hc] += noise[hc]
    out[y0:y1, x0:x1, :3] = np.clip(filled, 0, 255).astype(np.uint8)[..., ::-1]  # BGR -> RGB
    alpha = m.astype(np.float32)
    if protect is not None and prot.any():
        soft = cv2.GaussianBlur(prot.astype(np.float32), (0, 0), 1.2)  # thin soft edge against the object in front
        alpha *= 1 - np.clip(soft * 1.5, 0, 1)
    out[..., 3] = np.clip(alpha, 0, 255).astype(np.uint8)
    return out


class LabelLock:
    """Lines the region up with the frame where it was marked, and finds what's in front of it.

    Labels / logos are full of texture, so SIFT features inside them give a precise homography from
    the reference frame to every other frame: the mask follows perspective, not just a moving box.
    Frames warped back onto the reference and median-stacked give a "plate" of the label's surface.
    Anything at a different depth (the product in front, a hand) shifts against the label as the
    camera moves, so it's missing from the plate; in any frame (the reference too) pixels that don't
    match the plate are an object in front, and they are protected instead of painted over.
    """

    SAMPLES = 15

    def __init__(self, cv2, ref: np.ndarray, ref_mask: np.ndarray):
        self.cv2, self.ref, self.ref_mask = cv2, ref, ref_mask
        self.sift = cv2.SIFT_create(nfeatures=1500)
        self.matcher = cv2.BFMatcher(cv2.NORM_L2)
        feat = cv2.dilate((ref_mask > 128).astype(np.uint8) * 255, np.ones((7, 7), np.uint8))
        self.kp, self.des = self.sift.detectAndCompute(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), feat)
        self.ok = self.des is not None and len(self.kp) >= 12
        h, w = ref_mask.shape
        ys, xs = np.nonzero(ref_mask > 2)
        if not len(ys):
            self.ok = False
            ys, xs = np.array([0, h - 1]), np.array([0, w - 1])
        py, px = (ys.max() - ys.min()) // 4 + 8, (xs.max() - xs.min()) // 4 + 8
        self.roi = (max(0, ys.min() - py), min(h, ys.max() + py), max(0, xs.min() - px), min(w, xs.max() + px))
        self.plate: np.ndarray | None = None  # full-frame float, reference coordinates

    def homography(self, frame: np.ndarray, box_mask: np.ndarray, prev: np.ndarray | None = None) -> np.ndarray | None:
        """Reference -> frame homography, or None. With `prev` (the lock in the neighbouring frame) the search and the
        sanity checks follow where that lock puts the region, not the box tracker: the tracker's box can't rotate or
        tilt, so on a turning label it drifts and would make good locks look wrong."""
        cv2 = self.cv2
        if not self.ok:
            return None
        h, w = box_mask.shape
        if prev is not None:
            pred = cv2.warpPerspective(self.ref_mask, prev, (w, h), flags=cv2.INTER_NEAREST)
            if np.count_nonzero(pred > 128) >= 16:
                box_mask = pred
        ys, xs = np.nonzero(box_mask > 2)
        if not len(ys):
            return None
        bh, bw = ys.max() - ys.min(), xs.max() - xs.min()
        search = np.zeros((h, w), np.uint8)
        search[max(0, ys.min() - bh):ys.max() + bh, max(0, xs.min() - bw):xs.max() + bw] = 255
        kp, des = self.sift.detectAndCompute(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), search)
        if des is None or len(kp) < 12:
            return None
        good = [m for m, n in (p for p in self.matcher.knnMatch(self.des, des, k=2) if len(p) == 2) if m.distance < 0.75 * n.distance]
        if len(good) < 12:
            return None
        src = np.float32([self.kp[m.queryIdx].pt for m in good])
        dst = np.float32([kp[m.trainIdx].pt for m in good])
        H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
        if H is None or inl is None or int(inl.sum()) < 12:
            return None
        H = H / H[2, 2]
        corners = cv2.perspectiveTransform(np.float32([[[0, 0]], [[w, 0]], [[w, h]], [[0, h]]]), H).reshape(-1, 2)
        if not (0.15 * w * h < cv2.contourArea(corners) < 6 * w * h) or not cv2.isContourConvex(corners):
            return None
        # a look-alike (the same label on another box) can fool the matcher: the locked region must
        # sit where the tracker says and be about the tracked box's size
        m = cv2.warpPerspective(self.ref_mask, H, (w, h), flags=cv2.INTER_NEAREST)
        mys, mxs = np.nonzero(m > 128)
        if (not len(mys) or not 0.4 < len(mys) / max(float((box_mask > 128).sum()), 1.0) < 2.5
                or abs(mxs.mean() - xs.mean()) > 0.3 * max(bw, 1) or abs(mys.mean() - ys.mean()) > 0.3 * max(bh, 1)):
            return None
        if prev is not None:  # light temporal smoothing against jitter
            H = 0.6 * H + 0.4 * prev
        return H

    def follow(self, last: np.ndarray, last_H: np.ndarray, frame: np.ndarray) -> np.ndarray | None:
        """Reference -> frame homography by matching against the last locked frame instead of the reference. When a
        label turns far from how it looked in the reference (a box rotated 40 degrees), matching the reference fails,
        but neighbouring frames still look alike: chain the small frame-to-frame step onto the last lock."""
        cv2 = self.cv2
        if not self.ok:
            return None
        h, w = frame.shape[:2]
        pm = cv2.warpPerspective(self.ref_mask, last_H, (w, h), flags=cv2.INTER_NEAREST) > 128
        ys, xs = np.nonzero(pm)
        if len(ys) < 16:
            return None
        bh, bw = ys.max() - ys.min(), xs.max() - xs.min()
        feat = cv2.dilate(pm.astype(np.uint8) * 255, np.ones((7, 7), np.uint8))
        kp0, d0 = self.sift.detectAndCompute(cv2.cvtColor(last, cv2.COLOR_BGR2GRAY), feat)
        search = np.zeros((h, w), np.uint8)
        search[max(0, ys.min() - bh // 2):ys.max() + bh // 2, max(0, xs.min() - bw // 2):xs.max() + bw // 2] = 255
        kp1, d1 = self.sift.detectAndCompute(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), search)
        if d0 is None or d1 is None or len(kp0) < 12 or len(kp1) < 12:
            return None
        good = [m for m, n in (p for p in self.matcher.knnMatch(d0, d1, k=2) if len(p) == 2) if m.distance < 0.75 * n.distance]
        if len(good) < 12:
            return None
        src = np.float32([kp0[m.queryIdx].pt for m in good])
        dst = np.float32([kp1[m.trainIdx].pt for m in good])
        step, inl = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
        if step is None or inl is None or int(inl.sum()) < 12:
            return None
        H = step @ last_H
        H = H / H[2, 2]
        m = cv2.warpPerspective(self.ref_mask, H, (w, h), flags=cv2.INTER_NEAREST) > 128
        mys, mxs = np.nonzero(m)
        # a step between neighbouring frames is small: the region keeps its size and moves a little
        if (not len(mys) or not 0.6 < len(mys) / len(ys) < 1.6
                or abs(mxs.mean() - xs.mean()) > 0.35 * max(bw, 1) or abs(mys.mean() - ys.mean()) > 0.35 * max(bh, 1)):
            return None
        return H

    def build_plate(self, samples: list[tuple[np.ndarray, np.ndarray]]) -> None:
        """samples: (frame, H ref->frame). Median of the frames warped back onto the reference."""
        cv2 = self.cv2
        y0, y1, x0, x1 = self.roi
        h, w = self.ref_mask.shape
        stack = [cv2.GaussianBlur(self.ref.astype(np.float32), (0, 0), 2.0)[y0:y1, x0:x1]]
        for frame, H in samples:
            back = cv2.warpPerspective(frame, np.linalg.inv(H), (w, h), flags=cv2.INTER_LINEAR)
            valid = cv2.warpPerspective(np.ones(frame.shape[:2], np.uint8), np.linalg.inv(H), (w, h), flags=cv2.INTER_NEAREST)
            b = cv2.GaussianBlur(back.astype(np.float32), (0, 0), 2.0)[y0:y1, x0:x1]
            b[valid[y0:y1, x0:x1] == 0] = np.nan
            stack.append(b)
        if len(stack) < 4:
            return  # not enough views to tell what's in front
        plate = np.full((h, w, 3), np.nan, np.float32)
        plate[y0:y1, x0:x1] = np.nanmedian(np.stack(stack), axis=0)
        self.plate = plate

    def frame(self, frame: np.ndarray, box_mask: np.ndarray, H: np.ndarray | None) -> tuple[np.ndarray, np.ndarray | None]:
        """(mask, protect) for this frame; the tracked-box mask when it couldn't lock on."""
        cv2 = self.cv2
        if H is None:
            return box_mask, None
        h, w = box_mask.shape
        m = cv2.warpPerspective(self.ref_mask, H, (w, h), flags=cv2.INTER_LINEAR)
        # a hair of margin: on a turned label the lock is a pixel or two off at the far edge, enough for a coloured
        # border to peek out
        grow = max(3, int(0.012 * min(w, h))) | 1
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (grow, grow)))
        if self.plate is None:
            return m, None
        warped = cv2.warpPerspective(np.nan_to_num(self.plate, nan=-1000), H, (w, h), flags=cv2.INTER_LINEAR)
        cur = cv2.GaussianBlur(frame.astype(np.float32), (0, 0), 2.0)
        known = warped.min(axis=2) >= 0
        inside = (m > 128) & known
        if inside.sum() < 50:
            return m, None
        # allow for exposure / white-balance drift before comparing
        gain = np.clip(np.median(cur[inside] + 1, axis=0) / np.median(warped[inside] + 1, axis=0), 0.6, 1.7)
        diff = np.abs(cur - warped * gain).max(axis=2)
        occ = ((diff > 50) & (m > 2) & known).astype(np.uint8)
        occ = cv2.morphologyEx(occ, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        # an object in front is solid: keep sizeable blobs, close their holes, grow slightly
        n, lab, stats, _ = cv2.connectedComponentsWithStats(occ, connectivity=8)
        keep = np.zeros_like(occ)
        min_area = max(40, int(0.02 * inside.sum()))
        # something in front of the label (a hand, a hose) reaches in from outside, so it crosses the region's edge
        # (not counting the frame border). A blob wholly inside is the label's own print that didn't match the plate
        # (a label turned far from the reference, or sliding out of frame): left uncovered it shows as ghost fragments.
        reg = (m > 128).astype(np.uint8)
        edge = (reg - cv2.erode(reg, np.ones((7, 7), np.uint8))).astype(bool)
        edge[:3, :] = edge[-3:, :] = False
        edge[:, :3] = edge[:, -3:] = False
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] >= min_area and (edge & (lab == i)).any():
                keep[lab == i] = 1
        if not keep.any():
            return m, None
        keep = cv2.morphologyEx(keep, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        keep = cv2.dilate(keep, np.ones((3, 3), np.uint8))
        return m, keep.astype(bool)


def render_remove(pdir: Path, project: Project, source_id: str, eff: Effect, out: Path, log: Callable[[str], None]) -> Path:
    """RGBA overlay for the whole source: the region rebuilt from the background around it, each frame."""
    cv2 = require("cv2", "ai")
    p = eff.params
    data = track(pdir, project, source_id, p, log)
    src = project.sources[source_id]
    path = resolve_media(pdir, src.path)
    w, h, fps = source_dims(src)
    mw, mh = proc_size(w, h, REMOVE_SIDE)
    boxes = data["boxes"]
    n = len(boxes)
    a, b = p.get("range") or [0.0, src.duration]
    fa, fb = min(n, max(0, int(a * fps))), min(n, int(np.ceil(b * fps)) + 1)
    empty = np.zeros((mh, mw, 4), np.uint8)
    log(f"Rebuilding the background behind {p.get('id', 'the mask')} ({a:.1f}-{b:.1f}s, one-time)...")

    keep = KeepMasks(cv2, pdir, project, source_id, p, log)
    # pass 1: lock onto the region in every frame, and keep a few views for the surface plate
    lock, Hs = None, {}
    if p.get("protect", True):
        at = float(p.get("at", a))
        ref = next(_frames(path, mw, mh, fps, at, 2.0 / fps), None)
        if ref is not None:
            fi = min(n - 1, max(0, int(round(at * fps))))
            lock = LabelLock(cv2, ref.copy(), mask_at(cv2, p, data, fi, mw, mh, "mask"))
    if lock is not None and lock.ok:
        samples: list = []
        step = max(1, (fb - fa) // LabelLock.SAMPLES)
        lost_after = max(3, int(0.5 * fps))  # after this many missed frames, search around the tracker's box again
        f_at = min(max(fa, int(round(float(p.get("at", a)) * fps))), max(fa, fb - 1))

        state: dict = {}

        def visit(i: int, frame: np.ndarray) -> None:
            prev, miss, last = state.get("H"), state.get("miss", 0), state.get("frame")
            near = prev if miss <= lost_after else None
            H = lock.homography(frame, mask_at(cv2, p, data, i, mw, mh, "mask"), near)
            if H is None and near is not None and last is not None:
                H = lock.follow(last, near, frame)  # turned too far from the reference: step on from the last lock
            Hs[i] = H
            if H is None:
                state["miss"] = miss + 1
                return
            if (i - fa) % step == 0:
                samples.append((frame.copy(), H))
            state.update(H=H, miss=0, frame=frame.copy())

        # chain the lock outwards from the frame the region was marked on (where it is exactly right): forwards ...
        state.update(H=np.eye(3), miss=0, frame=None)
        i = f_at
        for frame in _frames(path, mw, mh, fps, f_at / fps, (fb - f_at) / fps):
            if i >= fb:
                break
            visit(i, frame)
            i += 1
        # ... and backwards, a chunk of frames at a time
        state.update(H=Hs.get(f_at) if Hs.get(f_at) is not None else np.eye(3), miss=0, frame=None)
        end = f_at
        while end > fa:
            start = max(fa, end - 90)
            chunk = list(_frames(path, mw, mh, fps, start / fps, (end - start) / fps))[: end - start]
            if state.get("frame") is None and f_at < fb:
                state["frame"] = next(_frames(path, mw, mh, fps, f_at / fps, 1.5 / fps), None)
            for k in range(len(chunk) - 1, -1, -1):
                visit(start + k, chunk[k])
            end = start
        lock.build_plate(samples)
        del samples
        # frames where the lock failed: hold the nearest good lock for short gaps (motion blur); when the
        # lock usually works, a long gap means the region has left the shot (the tracker may have latched
        # onto something else), so leave those frames alone instead of painting over the wrong thing
        good = sorted(k for k, v in Hs.items() if v is not None)
        if good:
            gap = max(2, int(0.4 * fps))
            mostly = len(good) >= 0.5 * max(1, len(Hs))
            for k in [k for k, v in Hs.items() if v is None]:
                j = good[int(np.argmin([abs(g - k) for g in good]))]
                Hs[k] = Hs[j] if abs(j - k) <= gap else ("skip" if mostly else None)

    # pass 2: rebuild the background in the region, keeping whatever is in front of it on top
    wr = Writer(out, mw, mh, fps, "rgba")
    for _ in range(fa):
        wr.write(empty)
    i = fa
    prev: tuple = (None, None)  # (last written patch, its lock) for steady_fill
    for frame in _frames(path, mw, mh, fps, fa / fps, (fb - fa) / fps):
        if i >= fb:
            break
        m = mask_at(cv2, p, data, i, mw, mh, "mask")
        prot = None
        if isinstance(Hs.get(i), str) or not m.any():  # "skip", or the outline tracker lost the object here
            wr.write(empty)
            prev = (None, None)
            i += 1
            continue
        if lock is not None:
            m, prot = lock.frame(frame, m, Hs.get(i))
        km = keep(i, mw, mh)
        if km is not None:
            prot = km if prot is None else (prot | km)
        out = remove_frame(cv2, frame, m, seed=i, protect=prot)
        H = Hs.get(i)
        H = H if isinstance(H, np.ndarray) else None
        if H is not None and prev[0] is not None:
            out = steady_fill(cv2, out, prev[0], H @ np.linalg.inv(prev[1]))
        prev = (out, H) if H is not None else (None, None)
        wr.write(out)
        i += 1
    for _ in range(i, n):
        wr.write(empty)
    return wr.close()


STEADY = 0.75  # share of the previous frame's rebuilt surface kept in each new frame (0 = rebuilt fresh every frame)


def steady_fill(cv2, out: np.ndarray, last: np.ndarray, M: np.ndarray, keep: float = STEADY) -> np.ndarray:
    """Stop a rebuilt patch from flickering: each frame's fill is made from slightly different surrounding pixels
    plus fresh grain, so on its own it shimmers. Move the previous frame's patch onto this frame (M = the lock's
    motion from that frame to this one, so it sticks to the surface as it turns) and blend it in."""
    h, w = out.shape[:2]
    moved = cv2.warpPerspective(last, M, (w, h), flags=cv2.INTER_LINEAR)
    both = (out[..., 3] > 0) & (moved[..., 3] > 0)
    if both.any():
        res = out.copy()
        res[..., :3][both] = (keep * moved[..., :3][both].astype(np.float32)
                              + (1 - keep) * out[..., :3][both].astype(np.float32)).round().astype(np.uint8)
        return res
    return out


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

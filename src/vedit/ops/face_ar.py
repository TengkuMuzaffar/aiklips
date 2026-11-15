"""Face mesh / AR effects using MediaPipe Face Landmarker (478 landmarks + head pose).

Tracking runs once per source and is cached (.npz). Effects are drawn from the cache:
  face_mesh     wireframe / glowing mesh overlay
  face_sticker  PNG that follows the face (glasses, hat, mask...)
  face_prop     3D model (.obj/.glb/.stl) attached to the head, software-rendered
  face_blur     mask for blurring / pixelating faces (privacy)
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..project import Effect, Source, VeditError, resolve_media
from .effects import EFFECT_VERSION, Writer, cache_dir, model_path, proc_size, read_frames, require, source_dims
from .text import rgba

LANDMARKER_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
N_LM = 478
FOV_Y = 63.0  # MediaPipe's default perspective camera

# landmark indices
L_EYE_OUT, R_EYE_OUT = 33, 263
FOREHEAD, CHIN, NOSE_TIP, MOUTH = 10, 152, 1, 13
L_CHEEK, R_CHEEK = 234, 454

ANCHORS = ("eyes", "head", "nose", "mouth", "face")


def _connections() -> tuple[list[tuple[int, int]], list[int]]:
    require("mediapipe", "ai")
    from mediapipe.tasks.python.vision import FaceLandmarksConnections as C

    tess = [(c.start, c.end) for c in C.FACE_LANDMARKS_TESSELATION]
    oval = sorted({c.start for c in C.FACE_LANDMARKS_FACE_OVAL} | {c.end for c in C.FACE_LANDMARKS_FACE_OVAL})
    return tess, oval


def track(pdir: Path, path: Path, src: Source, max_faces: int, log: Callable[[str], None]) -> dict[str, np.ndarray]:
    """Landmarks in pixels (frames, faces, 478, 3) + pose matrices (frames, faces, 4, 4); NaN = no face."""
    st = path.stat()
    key = hashlib.sha1(f"{path}{st.st_size}{st.st_mtime}{max_faces}{EFFECT_VERSION}".encode()).hexdigest()[:16]
    cache = cache_dir(pdir) / f"track-{key}.npz"
    if cache.exists():
        d = np.load(cache)
        return {"lm": d["lm"], "mat": d["mat"]}

    mp = require("mediapipe", "ai")
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core.base_options import BaseOptions

    opts = vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model_path("face_landmarker.task", LANDMARKER_URL))),
        running_mode=vision.RunningMode.VIDEO,
        num_faces=max_faces,
        output_facial_transformation_matrixes=True,
    )
    landmarker = vision.FaceLandmarker.create_from_options(opts)
    w, h, fps = source_dims(src)
    pw, ph = proc_size(w, h, 960)
    lms, mats = [], []
    log("Tracking faces (one-time)...")
    for i, frame in enumerate(read_frames(path, pw, ph, fps)):
        res = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame)), int(i * 1000 / fps))
        lm = np.full((max_faces, N_LM, 3), np.nan, np.float32)
        mt = np.full((max_faces, 4, 4), np.nan, np.float32)
        for f, face in enumerate(res.face_landmarks[:max_faces]):
            lm[f] = [(p.x * w, p.y * h, p.z * w) for p in face[:N_LM]]
            if res.facial_transformation_matrixes and f < len(res.facial_transformation_matrixes):
                mt[f] = np.asarray(res.facial_transformation_matrixes[f]).reshape(4, 4)
        lms.append(lm)
        mats.append(mt)
        if i % 90 == 0:
            log(f"  tracked {i} frames")
    landmarker.close()
    if not lms:
        raise VeditError("No frames could be read for face tracking")
    out = {"lm": np.stack(lms), "mat": np.stack(mats)}
    np.savez_compressed(cache, **out)
    return out


def _smooth(lm: np.ndarray, amount: float) -> np.ndarray:
    """Exponential smoothing over time to reduce landmark jitter."""
    if amount <= 0:
        return lm
    out = lm.copy()
    for i in range(1, len(out)):
        prev, cur = out[i - 1], out[i]
        ok = ~np.isnan(prev) & ~np.isnan(cur)
        cur[ok] = amount * prev[ok] + (1 - amount) * cur[ok]
    return out


def _hull(pts: np.ndarray) -> list[tuple[float, float]]:
    p = sorted(map(tuple, pts.tolist()))
    if len(p) < 3:
        return p

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for q in p:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    for q in reversed(p):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return lower[:-1] + upper[:-1]


# ---- per-effect frame painters -------------------------------------------------

def _mesh_painter(params: dict, w: int, h: int):
    tess, _ = _connections()
    color = rgba(params.get("color", "#00E5FF"), int(255 * float(params.get("opacity", 0.85))))
    width = max(1, int(params.get("width", max(1, w // 540))))
    glow = float(params.get("glow", 6))
    dots = bool(params.get("dots", False))

    def paint(img: Image.Image, faces: list[np.ndarray], mats: list[np.ndarray]) -> Image.Image:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for lm in faces:
            for a, b in tess:
                d.line([tuple(lm[a, :2]), tuple(lm[b, :2])], fill=color, width=width)
            if dots:
                r = width + 1
                for x, y, _ in lm:
                    d.ellipse([x - r, y - r, x + r, y + r], fill=color)
        if glow > 0:
            g = layer.filter(ImageFilter.GaussianBlur(glow))
            img = Image.alpha_composite(img, g)
        return Image.alpha_composite(img, layer)

    return paint


def _sticker_painter(pdir: Path, params: dict, w: int, h: int):
    if not params.get("image"):
        raise VeditError("face_sticker needs image=<path to PNG>")
    sticker = Image.open(resolve_media(pdir, params["image"])).convert("RGBA")
    anchor = params.get("anchor", "eyes")
    if anchor not in ANCHORS:
        raise VeditError(f"anchor must be one of {', '.join(ANCHORS)}")
    size = float(params.get("size", 1.0))
    ox, oy = float(params.get("offset_x", 0)), float(params.get("offset_y", 0))

    def paint(img: Image.Image, faces: list[np.ndarray], mats: list[np.ndarray]) -> Image.Image:
        for lm in faces:
            le, re_ = lm[L_EYE_OUT, :2], lm[R_EYE_OUT, :2]
            angle = math.degrees(math.atan2(re_[1] - le[1], re_[0] - le[0]))
            face_w = float(np.linalg.norm(lm[R_CHEEK, :2] - lm[L_CHEEK, :2]))
            face_h = float(np.linalg.norm(lm[CHIN, :2] - lm[FOREHEAD, :2]))
            if anchor == "eyes":
                c, sw = (le + re_) / 2, np.linalg.norm(re_ - le) * 1.45
            elif anchor == "head":
                c, sw = lm[FOREHEAD, :2] - np.array([0, face_h * 0.45]), face_w * 1.35
            elif anchor == "nose":
                c, sw = lm[NOSE_TIP, :2], face_w * 0.4
            elif anchor == "mouth":
                c, sw = lm[MOUTH, :2], face_w * 0.6
            else:
                c, sw = (lm[FOREHEAD, :2] + lm[CHIN, :2]) / 2, face_w * 1.15
            sw *= size
            if sw < 2:
                continue
            s = sticker.resize((int(sw), max(1, int(sticker.height * sw / sticker.width))), Image.LANCZOS)
            s = s.rotate(-angle, resample=Image.BICUBIC, expand=True)
            rad = math.radians(angle)
            dx, dy = ox * face_w, oy * face_w  # offsets follow head rotation
            cx = c[0] + dx * math.cos(rad) - dy * math.sin(rad)
            cy = c[1] + dx * math.sin(rad) + dy * math.cos(rad)
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            layer.paste(s, (int(cx - s.width / 2), int(cy - s.height / 2)))
            img = Image.alpha_composite(img, layer)
        return img

    return paint


def _prop_painter(pdir: Path, params: dict, w: int, h: int, fps: float):
    trimesh = require("trimesh", "ai")
    if not params.get("model"):
        raise VeditError("face_prop needs model=<path to .obj/.glb/.stl>")
    mesh = trimesh.load(resolve_media(pdir, params["model"]), force="mesh")
    v = np.asarray(mesh.vertices, np.float64)
    faces_idx = np.asarray(mesh.faces)
    v -= (v.max(0) + v.min(0)) / 2
    v *= float(params.get("size_cm", 12)) / max(1e-6, float(np.ptp(v, axis=0).max()))
    rx, ry, rz = (math.radians(float(params.get(k, 0))) for k in ("rot_x", "rot_y", "rot_z"))
    offset = np.array([float(params.get("x_cm", 0)), float(params.get("y_cm", 11)), float(params.get("z_cm", 0))])
    spin = float(params.get("spin", 0))  # degrees per second around Y
    color = np.array(rgba(params.get("color", "#FF4FA3"))[:3], np.float64)
    f = (h / 2) / math.tan(math.radians(FOV_Y / 2))
    light = np.array([0.3, 0.6, 0.75])
    light /= np.linalg.norm(light)
    frame_no = {"i": 0}

    def rot(ax: float, ay: float, az: float) -> np.ndarray:
        cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
        Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
        Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
        Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
        return Rz @ Ry @ Rx

    def paint(img: Image.Image, faces: list[np.ndarray], mats: list[np.ndarray]) -> Image.Image:
        t = frame_no["i"] / fps
        frame_no["i"] += 1
        d = ImageDraw.Draw(img)
        local = v @ rot(rx, ry + math.radians(spin * t), rz).T + offset
        for M in mats:
            if np.isnan(M).any():
                continue
            cam = (np.c_[local, np.ones(len(local))] @ M.T)[:, :3]
            z = -cam[:, 2]
            if (z <= 1).any():
                continue
            sx = w / 2 + f * cam[:, 0] / z
            sy = h / 2 - f * cam[:, 1] / z
            tri = cam[faces_idx]
            n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-9
            shade = 0.35 + 0.65 * np.clip(n @ light, 0, 1)
            order = np.argsort(-z[faces_idx].mean(1))  # far to near
            for k in order:
                if n[k, 2] <= 0:  # back-face
                    continue
                a, b, c = faces_idx[k]
                col = tuple(int(x) for x in np.clip(color * shade[k], 0, 255)) + (255,)
                d.polygon([(sx[a], sy[a]), (sx[b], sy[b]), (sx[c], sy[c])], fill=col)
        return img

    return paint


def render_effect(pdir: Path, path: Path, src: Source, eff: Effect, out: Path, log: Callable[[str], None]) -> Path:
    p = eff.params
    max_faces = int(p.get("faces", 1))
    tr = track(pdir, path, src, max_faces, log)
    w, h, fps = source_dims(src)
    lm_all = _smooth(tr["lm"], float(p.get("smooth", 0.4)))
    mats_all = tr["mat"]

    if eff.type == "face_blur":
        _, oval = _connections()
        expand = float(p.get("expand", 1.15))
        writer = Writer(out, w, h, fps, "gray")
        for i in range(len(lm_all)):
            m = Image.new("L", (w, h), 0)
            d = ImageDraw.Draw(m)
            for lm in lm_all[i]:
                if np.isnan(lm).any():
                    continue
                pts = lm[oval, :2]
                c = pts.mean(0)
                d.polygon(_hull(c + (pts - c) * expand), fill=255)
            writer.write(np.asarray(m.filter(ImageFilter.GaussianBlur(max(2, w // 200)))))
        return writer.close()

    if eff.type == "face_mesh":
        painter = _mesh_painter(p, w, h)
    elif eff.type == "face_sticker":
        painter = _sticker_painter(pdir, p, w, h)
    elif eff.type == "face_prop":
        painter = _prop_painter(pdir, p, w, h, fps)
    else:
        raise VeditError(f"Unknown face effect {eff.type}")

    writer = Writer(out, w, h, fps, "rgba")
    blank = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for i in range(len(lm_all)):
        faces = [lm for lm in lm_all[i] if not np.isnan(lm).any()]
        mats = [m for m in mats_all[i] if not np.isnan(m).any()]
        img = painter(blank.copy(), faces, mats) if faces else blank
        writer.write(np.asarray(img))
        if i % 90 == 0:
            log(f"  {eff.type} {i}/{len(lm_all)} frames")
    return writer.close()

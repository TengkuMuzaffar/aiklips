"""Per-clip effects that need frame-by-frame processing (background removal, face AR).

Effects are computed once on the *whole source* and cached in .vedit/cache/effects,
so later cuts, splits and speed changes never trigger a recompute.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from .. import media
from ..project import Effect, Project, Source, VeditError, resolve_media, state_dir

EFFECT_VERSION = "1"
MODEL_DIR = Path.home() / ".cache" / "aiklips" / "models"

# parameters that only change compositing (no recompute needed)
_COMPOSITE_ONLY = {"bg": {"replace", "key", "similarity", "blend", "spill", "shrink"}, "face_blur": {"style", "strength"},
                   "mask": {"style", "strength", "color", "label", "id"},
                   "focus": {"blur", "dim", "zoom", "camera_smooth", "mode", "label", "id", "style", "color", "width", "strength"}}


def model_path(name: str, url: str) -> Path:
    old = MODEL_DIR.parents[1] / "claude-cut" / "models"  # downloaded under the app's old name: reuse, don't re-download
    if old.is_dir() and not MODEL_DIR.exists():
        MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)
        try:
            old.rename(MODEL_DIR)
        except OSError:
            pass
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    p = MODEL_DIR / name
    if not p.exists():
        tmp = p.with_suffix(".part")
        try:
            urllib.request.urlretrieve(url, tmp)
        except Exception as e:
            raise VeditError(f"Could not download model {name} from {url}: {e}") from e
        tmp.replace(p)
    return p


def require(module: str, extra: str):
    if module == "mediapipe":  # silence MediaPipe / TFLite chatter
        import os

        os.environ.setdefault("GLOG_minloglevel", "2")
        os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    try:
        return __import__(module)
    except ImportError:
        pass
    from .. import updater

    updater.install_extra(extra)  # installed app or git clone: fetch it now (one time) instead of asking for uv
    return __import__(module)


def read_frames(path: Path, w: int, h: int, fps: float) -> Iterator[np.ndarray]:
    proc = subprocess.Popen(
        [media.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path),
         "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE,
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


class Writer:
    """Write gray masks (H.264) or RGBA overlays (QuickTime RLE)."""

    def __init__(self, out: Path, w: int, h: int, fps: float, kind: str):
        self.out, self.tmp = out, out.with_name(out.stem + ".part" + out.suffix)
        if kind == "gray":
            args = ["-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p"]
        else:
            args = ["-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "qtrle", "-pix_fmt", "argb"]
        self.proc = subprocess.Popen(
            [media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", *args, str(self.tmp)],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def write(self, arr: np.ndarray) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(np.ascontiguousarray(arr, dtype=np.uint8).tobytes())

    def close(self) -> Path:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        err = self.proc.stderr.read().decode() if self.proc.stderr else ""
        if self.proc.wait() != 0:
            self.tmp.unlink(missing_ok=True)
            raise VeditError(f"Writing {self.out.name} failed: {err[-600:]}")
        self.tmp.replace(self.out)
        return self.out


def proc_size(w: int, h: int, max_side: int) -> tuple[int, int]:
    s = min(1.0, max_side / max(w, h))
    return max(2, int(w * s) // 2 * 2), max(2, int(h * s) // 2 * 2)


def _key(src_path: Path, eff: Effect, extra: str = "") -> str:
    st = src_path.stat()
    params = {k: v for k, v in eff.params.items() if k not in _COMPOSITE_ONLY.get(eff.type, set())}
    if eff.type in ("mask", "focus") and params.get("tracker") not in (None, "box"):
        from . import outline, segment  # a better tracker must re-render the mask, even though its settings didn't change

        how = outline.resolve_tracker(params)
        extra = f"{extra}/{segment.VERSION if how == 'segment' else outline.VERSION}"  # outline: same key as before
    blob = json.dumps([str(src_path), st.st_size, st.st_mtime, eff.type, params, EFFECT_VERSION, extra], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


# While set, effects never start a slow one-time computation (masks, tracking, face AR): a missing artifact raises
# NotReady instead. The editor's instant preview frame uses it; the background render computes them.
CACHED_ONLY: contextvars.ContextVar[bool] = contextvars.ContextVar("vedit_cached_only", default=False)


class NotReady(VeditError):
    """An effect's one-time computation hasn't run yet (only raised while CACHED_ONLY is set)."""


def need_compute(what: str) -> None:
    if CACHED_ONLY.get():
        raise NotReady(f"{what} is still being computed")


def cache_dir(pdir: Path) -> Path:
    d = state_dir(pdir) / "cache" / "effects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prepare(eff: Effect, project: Project, pdir: Path, source_id: str, log: Callable[[str], None]) -> dict[str, Path]:
    """Return cached artifacts for an effect, computing them if needed."""
    src = project.sources[source_id]
    path = resolve_media(pdir, src.path)
    key = _key(path, eff)
    cdir = cache_dir(pdir)
    if eff.type == "bg" and eff.params.get("mode") == "chroma":
        return {}  # keyed inside the FFmpeg graph: nothing to precompute (ops/chroma.py)
    if eff.type in ("bg", "face_blur"):
        out = cdir / f"{eff.type}-{key}.mp4"
        if not out.exists():
            need_compute(eff.type)
            if eff.type == "bg":
                from . import bg_remove

                log(f"Computing background mask for {source_id} ({eff.params.get('mode', 'fast')} mode, one-time)...")
                bg_remove.compute_mask(path, src, eff.params, out, log)
            else:
                from . import face_ar

                log(f"Computing face mask for {source_id} (one-time)...")
                face_ar.render_effect(pdir, path, src, eff, out, log)
        return {"mask": out}
    if eff.type in ("mask", "focus"):
        from . import track

        art: dict[str, Path] = {}
        mode = eff.params.get("mode", "spotlight") if eff.type == "focus" else "mask"
        if eff.type == "mask" and eff.params.get("style") == "remove":  # rebuilt background, as an overlay
            out = cdir / f"remove-{_key(path, eff, track.REMOVE_VERSION)}.mov"
            if not out.exists():
                need_compute("mask")
                track.render_remove(pdir, project, source_id, eff, out, log)
            return {"overlay": out}
        if mode in ("mask", "spotlight", "both"):
            out = cdir / (f"mask-{_key(path, eff, track.MASK_VERSION)}.mp4" if eff.type == "mask" else f"{eff.type}-{key}.mp4")
            if not out.exists():
                need_compute(eff.type)
                log(f"Computing {eff.type} for {source_id} (one-time)...")
                track.render_mask(pdir, project, source_id, eff, out, log)
            art["mask"] = out
        if mode in ("follow", "both"):
            art["follow"] = path  # the crop path is built per clip in the render graph
        return art
    if eff.type in ("face_mesh", "face_sticker", "face_prop"):
        from . import face_ar

        out = cdir / f"{eff.type}-{key}.mov"
        if not out.exists():
            need_compute(eff.type)
            log(f"Rendering {eff.type} for {source_id} (one-time)...")
            face_ar.render_effect(pdir, path, src, eff, out, log)
        return {"overlay": out}
    raise VeditError(f"Unknown effect type {eff.type}")


def source_dims(src: Source) -> tuple[int, int, float]:
    return src.width or 1080, src.height or 1920, src.fps or 30.0

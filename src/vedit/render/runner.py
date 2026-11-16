"""Render previews and exports."""

from __future__ import annotations

import contextlib
import hashlib
import os
import sys
import time
import uuid
from pathlib import Path

from .. import media
from ..project import Project, dump_json, state_dir
from .graph import build

PREVIEW_SCALE = 0.5


def preview_dir(pdir: Path) -> Path:
    d = state_dir(pdir) / "preview"
    d.mkdir(exist_ok=True)
    return d


@contextlib.contextmanager
def _file_lock(path: Path):
    """Exclusive lock across processes (the editor's auto-render vs. `vedit frame`, etc.)."""
    with open(path, "a+b") as fh:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:  # LK_LOCK gives up after ~10s; keep waiting
                    time.sleep(0.5)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _playable(path: Path) -> bool:
    try:
        info = media.probe(path)
    except Exception:
        return False
    return info.get("has_video", False) and info.get("duration", 0) > 0


def project_hash(project: Project) -> str:
    return hashlib.sha1(dump_json(project).encode()).hexdigest()


def _stderr_progress(label: str):
    if not sys.stderr.isatty():
        return None
    def cb(p: float) -> None:
        bar = "#" * int(p * 30)
        sys.stderr.write(f"\r{label} [{bar:<30}] {p * 100:5.1f}%")
        sys.stderr.flush()
    return cb


def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")


def render(project: Project, pdir: Path, out: Path, scale: float, quality: str, progress: bool = True) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    # unique temp name: two renders must never write into the same file
    tmp = out.with_name(f"{out.stem}.part-{os.getpid()}-{uuid.uuid4().hex[:8]}{out.suffix}")
    try:
        cmd, total = build(project, pdir, tmp, scale, quality, log=_log)
        cb = _stderr_progress("rendering") if progress else None
        media.run_with_progress(cmd, total, cb)
        if cb:
            sys.stderr.write("\n")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    info = media.probe(out)
    return {
        "output": str(out),
        "duration": info["duration"],
        "width": info.get("width"),
        "height": info.get("height"),
        "seconds": round(time.time() - t0, 2),
    }


def render_preview(project: Project, pdir: Path, force: bool = False, progress: bool = True) -> dict:
    d = preview_dir(pdir)
    # Only one preview render per project at a time. A second caller waits, then
    # usually finds the fresh cache instead of rendering the same thing again.
    with _file_lock(d / ".lock"):
        return _render_preview_locked(project, pdir, d, force, progress)


def _render_preview_locked(project: Project, pdir: Path, d: Path, force: bool, progress: bool) -> dict:
    out = d / "preview.mp4"
    h = project_hash(project)
    hfile = d / "hash"
    if not force and out.exists() and hfile.exists() and hfile.read_text() == h:
        if _playable(out):  # a corrupt file with a matching hash is re-rendered
            return {"output": str(out), "cached": True, "duration": round(project.duration, 3)}
    hfile.unlink(missing_ok=True)  # never leave a hash pointing at a half-written file
    for stale in d.glob("preview.part*"):  # leftovers from crashed renders (we hold the lock)
        stale.unlink(missing_ok=True)
    res = render(project, pdir, out, PREVIEW_SCALE, "preview", progress)
    hfile.write_text(h)
    # timeline helpers for the preview UI
    try:
        media.contact_sheet(out, res["duration"], d / "thumbs.jpg", count=12, cols=12, width=90)
    except Exception:
        pass
    media.waveform(out, d / "wave.png")
    res["cached"] = False
    return res

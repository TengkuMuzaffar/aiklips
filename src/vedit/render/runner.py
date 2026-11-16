"""Render previews and exports."""

from __future__ import annotations

import contextlib
import hashlib
import json
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


def base_hash(project: Project) -> str:
    """Hash of the base preview (everything but overlay layers, notes and the clips' "why" notes)."""
    d = project.to_dict()
    for k in ("layers", "notes", "name"):
        d.pop(k, None)
    for c in d.get("clips", []):
        c.pop("meta", None)
    used = {c.source for c in project.clips} | {m.source for m in project.music}
    d["sources"] = {k: v for k, v in d.get("sources", {}).items() if k in used}  # sources only overlays use
    return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()


def timeline_hash(project: Project) -> str:
    """Hash of what the timeline strip (thumbnails + waveform) shows: everything except overlay layers and notes."""
    d = project.to_dict()
    d.pop("layers", None)
    d.pop("notes", None)
    return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()


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


def render(project: Project, pdir: Path, out: Path, scale: float, quality: str, progress: bool = True,
           on_progress=None, encode: list[str] | None = None, audio: bool = True, should_stop=None,
           layers: bool = True) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    # unique temp name: two renders must never write into the same file
    tmp = out.with_name(f"{out.stem}.part-{os.getpid()}-{uuid.uuid4().hex[:8]}{out.suffix}")
    try:
        cmd, total = build(project, pdir, tmp, scale, quality, log=_log, encode=encode, audio=audio, layers=layers)
        cb = on_progress or (_stderr_progress("rendering") if progress else None)
        media.run_with_progress(cmd, total, cb, should_stop)
        if cb and on_progress is None:
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


def render_preview(project: Project, pdir: Path, force: bool = False, progress: bool = True, should_stop=None,
                   base: bool = False) -> dict:
    """`should_stop` returning True aborts the FFmpeg run (media.Cancelled), e.g. when a newer edit arrives.

    `base=True` renders `base.mp4` without the overlay layers, for the editor's live preview (which draws the
    layers itself). Its cache ignores layer edits, so changing text never re-encodes the video."""
    d = preview_dir(pdir)
    # Only one preview render per project at a time. A second caller waits, then
    # usually finds the fresh cache instead of rendering the same thing again.
    with _file_lock(d / ".lock"):
        return _render_preview_locked(project, pdir, d, force, progress, should_stop, base)


def _render_preview_locked(project: Project, pdir: Path, d: Path, force: bool, progress: bool, should_stop=None,
                           base: bool = False) -> dict:
    out = d / ("base.mp4" if base else "preview.mp4")
    h = base_hash(project) if base else project_hash(project)
    hfile = d / ("base-hash" if base else "hash")
    if not force and out.exists() and hfile.exists() and hfile.read_text() == h:
        if _playable(out):  # a corrupt file with a matching hash is re-rendered
            return {"output": str(out), "cached": True, "duration": round(project.duration, 3)}
    hfile.unlink(missing_ok=True)  # never leave a hash pointing at a half-written file
    for stale in d.glob(f"{out.stem}.part*"):  # leftovers from crashed renders (we hold the lock)
        stale.unlink(missing_ok=True)
    res = render(project, pdir, out, PREVIEW_SCALE, "preview", progress, should_stop=should_stop, layers=not base)
    hfile.write_text(h)
    # timeline helpers for the preview UI: only when the clips/sound changed, not for text or overlay edits
    th, tfile = timeline_hash(project), d / "timeline-hash"
    if force or not tfile.exists() or tfile.read_text() != th or not (d / "wave.png").exists():
        tfile.unlink(missing_ok=True)
        try:
            media.contact_sheet(out, res["duration"], d / "thumbs.jpg", count=12, cols=12, width=90)
        except Exception:
            pass
        media.waveform(out, d / "wave.png")
        tfile.write_text(th)
    res["cached"] = False
    return res

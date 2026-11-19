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
# the editor's preview quality picker: Full (as exported), Half (default) or Quarter (fast on low-spec machines)
PREVIEW_QUALITIES = {"full": 1.0, "half": 0.5, "quarter": 0.25}


def preview_name(base: bool = False, scale: float = PREVIEW_SCALE) -> str:
    """File name of a preview render. The default scale keeps the old names (the CLI, audit and review use them);
    other scales get their own file, so switching quality back and forth reuses both renders."""
    stem = "base" if base else "preview"
    return f"{stem}.mp4" if scale == PREVIEW_SCALE else f"{stem}-{round(scale * 100)}.mp4"


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


def _effects_salt() -> str:
    """Versions of the computed effect artifacts (masks, rebuilt backgrounds): when the code that makes them changes,
    the project file doesn't, so without this a stale preview made with the old artifacts would be reused."""
    from ..ops import effects, outline, segment, track

    return f"{effects.EFFECT_VERSION}/{track.REMOVE_VERSION}/{track.MASK_VERSION}/{outline.VERSION}/{segment.VERSION}"


def project_hash(project: Project) -> str:
    return hashlib.sha1((dump_json(project) + _effects_salt()).encode()).hexdigest()


def base_hash(project: Project) -> str:
    """Hash of the base preview (everything but overlay layers, notes and the clips' "why" notes)."""
    d = project.to_dict()
    # these change the base picture: adjustment layers, and every layer under the video track (baked into the base)
    adjust = [l for l in d.get("layers", []) if l.get("type") == "adjust" or (
        not l.get("on_video") and (l.get("track") or 0) < project.main_z)]
    for k in ("layers", "notes", "name", "markers"):
        d.pop(k, None)
    d["adjust"] = adjust
    d["duration"] = round(project.duration, 3)  # a layer past the clips makes the base video longer
    for c in d.get("clips", []):
        c.pop("meta", None)
    used = ({c.source for c in project.clips} | {m.source for m in project.music}
            | {l.source for l in project.layers if l.source and not l.on_video and (l.track or 0) < project.main_z})
    d["sources"] = {k: v for k, v in d.get("sources", {}).items() if k in used}  # sources only overlays use
    d["effects_salt"] = _effects_salt()
    return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()


def timeline_hash(project: Project) -> str:
    """Hash of what the timeline strip (thumbnails + waveform) shows: everything except overlay layers and notes."""
    d = project.to_dict()
    d.pop("layers", None)
    d.pop("notes", None)
    d.pop("markers", None)
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


def _remove(path: Path) -> None:
    """Delete a temp file. Windows can hold a just-killed process's file open for a moment: retry briefly, and never
    let a leftover temp file turn a cancel into an error."""
    for _ in range(30):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.1)


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
        _remove(tmp)
    info = media.probe(out)
    return {
        "output": str(out),
        "duration": info["duration"],
        "width": info.get("width"),
        "height": info.get("height"),
        "seconds": round(time.time() - t0, 2),
    }


def render_preview(project: Project, pdir: Path, force: bool = False, progress: bool = True, should_stop=None,
                   base: bool = False, scale: float = PREVIEW_SCALE) -> dict:
    """`should_stop` returning True aborts the FFmpeg run (media.Cancelled), e.g. when a newer edit arrives.

    `base=True` renders `base.mp4` without the overlay layers, for the editor's live preview (which draws the
    layers itself). Its cache ignores layer edits, so changing text never re-encodes the video.
    `scale` is the size relative to the canvas (see PREVIEW_QUALITIES); each scale has its own file and cache."""
    d = preview_dir(pdir)
    # Only one preview render per project at a time. A second caller waits, then
    # usually finds the fresh cache instead of rendering the same thing again.
    with _file_lock(d / ".lock"):
        return _render_preview_locked(project, pdir, d, force, progress, should_stop, base, scale)


def _render_preview_locked(project: Project, pdir: Path, d: Path, force: bool, progress: bool, should_stop=None,
                           base: bool = False, scale: float = PREVIEW_SCALE) -> dict:
    out = d / preview_name(base, scale)
    h = base_hash(project) if base else project_hash(project)
    suffix = out.stem.removeprefix("base").removeprefix("preview")  # "" or "-25" / "-100"
    hfile = d / (("base-hash" if base else "hash") + suffix)
    if not force and out.exists() and hfile.exists() and hfile.read_text() == h:
        if _playable(out):  # a corrupt file with a matching hash is re-rendered
            return {"output": str(out), "cached": True, "duration": round(project.duration, 3)}
    hfile.unlink(missing_ok=True)  # never leave a hash pointing at a half-written file
    for stale in d.glob(f"{out.stem}.part*"):  # leftovers from crashed renders (we hold the lock)
        stale.unlink(missing_ok=True)
    res = render(project, pdir, out, scale, "preview", progress, should_stop=should_stop, layers=not base)
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

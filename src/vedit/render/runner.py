"""Render previews and exports."""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

from .. import media
from ..project import Project, dump_json, state_dir
from .graph import build

PREVIEW_SCALE = 0.5


def preview_dir(pdir: Path) -> Path:
    d = state_dir(pdir) / "preview"
    d.mkdir(exist_ok=True)
    return d


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
    tmp = out.with_name(out.stem + ".part" + out.suffix)
    cmd, total = build(project, pdir, tmp, scale, quality, log=_log)
    cb = _stderr_progress("rendering") if progress else None
    media.run_with_progress(cmd, total, cb)
    if cb:
        sys.stderr.write("\n")
    tmp.replace(out)
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
    out = d / "preview.mp4"
    h = project_hash(project)
    hfile = d / "hash"
    if not force and out.exists() and hfile.exists() and hfile.read_text() == h:
        return {"output": str(out), "cached": True, "duration": round(project.duration, 3)}
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

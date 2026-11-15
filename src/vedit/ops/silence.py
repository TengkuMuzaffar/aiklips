"""Automatic silence removal (no AI): ffmpeg silencedetect + padding."""

from __future__ import annotations

from pathlib import Path

from .. import media
from ..project import Project, resolve_media
from .cut import delete_range


def find_silences(project: Project, pdir: Path, noise_db: float = -35, min_silence: float = 0.5, pad: float = 0.12) -> list[tuple[float, float]]:
    """Silent ranges in timeline seconds, already shrunk by `pad` on both sides."""
    ranges: list[tuple[float, float]] = []
    starts = project.clip_starts()
    for c, s in zip(project.clips, starts):
        src = project.sources[c.source]
        if not src.has_audio:
            continue
        path = resolve_media(pdir, src.path)
        for a, b in media.detect_silence(path, c.in_, c.out - c.in_, noise_db, min_silence):
            ta = s + a / c.speed
            tb = s + b / c.speed
            # keep a little breathing room, but cut fully at the very start / end of a clip
            ta = ta if a <= 0.01 else ta + pad
            tb = tb if b >= (c.out - c.in_) - 0.01 else tb - pad
            if tb - ta > 0.05:
                ranges.append((round(ta, 3), round(tb, 3)))
    return ranges


def silence_cut(project: Project, pdir: Path, noise_db: float = -35, min_silence: float = 0.5, pad: float = 0.12) -> dict:
    before = project.duration
    ranges = find_silences(project, pdir, noise_db, min_silence, pad)
    for a, b in sorted(ranges, reverse=True):
        delete_range(project, a, b)
    return {
        "cuts": len(ranges),
        "ranges": ranges,
        "before": round(before, 3),
        "after": round(project.duration, 3),
        "removed_seconds": round(before - project.duration, 3),
    }

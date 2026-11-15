"""Timeline editing: split, delete, keep, trim, speed, move.

All times are *timeline* seconds (what the viewer sees) unless noted.
Overlay layers, captions and music are remapped so they stay in sync after cuts.
"""

from __future__ import annotations

from typing import Callable

from ..project import Clip, Project, VeditError

EPS = 1e-3


def _check_range(project: Project, a: float, b: float) -> tuple[float, float]:
    total = project.duration
    a, b = max(0.0, a), min(total, b)
    if b - a < EPS:
        raise VeditError(f"Empty range {a:.3f}-{b:.3f}s (timeline is {total:.3f}s long)")
    return a, b


def split_at(project: Project, t: float) -> int:
    """Split the clip under timeline time `t`. Returns the index of the first clip starting at `t`."""
    clips = project.clips
    starts = project.clip_starts()
    for i, c in enumerate(clips):
        s, e = starts[i], starts[i] + c.duration
        if abs(t - s) < EPS:
            return i
        if s < t < e - EPS:
            src_t = round(c.in_ + (t - s) * c.speed, 4)
            left = c.model_copy(update={"out": src_t}, deep=True)
            right = c.model_copy(update={"in_": src_t, "id": project.new_id("c")}, deep=True)
            clips[i : i + 1] = [left, right]
            return i + 1
        if abs(t - e) < EPS:
            return i + 1
    return len(clips)


def remap_overlays(project: Project, fn: Callable[[float], float]) -> list[str]:
    """Apply a timeline remap to layers, caption words and music. Returns removed layer ids."""
    removed = []
    keep = []
    for layer in project.layers:
        if layer.words:
            words = []
            for w in layer.words:
                s, e = fn(w.start), fn(w.end)
                if e - s > EPS:
                    words.append(w.model_copy(update={"start": round(s, 3), "end": round(e, 3)}))
            layer.words = words
            if not words:
                removed.append(layer.id)
                continue
        start = fn(layer.start)
        end = None if layer.end is None else fn(layer.end)
        if end is not None and end - start < EPS:
            removed.append(layer.id)
            continue
        layer.start = round(start, 3)
        layer.end = None if end is None else round(end, 3)
        keep.append(layer)
    project.layers = keep
    for m in project.music:
        m.start = round(fn(m.start), 3)
    return removed


def delete_range(project: Project, a: float, b: float) -> dict:
    a, b = _check_range(project, a, b)
    i = split_at(project, a)
    j = split_at(project, b)
    removed = project.clips[i:j]
    del project.clips[i:j]
    gap = b - a

    def fn(t: float) -> float:
        if t <= a:
            return t
        if t >= b:
            return t - gap
        return a

    dropped = remap_overlays(project, fn)
    return {"removed_seconds": round(gap, 3), "removed_clips": [c.id for c in removed], "dropped_layers": dropped}


def keep_range(project: Project, a: float, b: float) -> dict:
    a, b = _check_range(project, a, b)
    total = project.duration
    removed = 0.0
    if b < total - EPS:
        removed += delete_range(project, b, total)["removed_seconds"]
    if a > EPS:
        removed += delete_range(project, 0, a)["removed_seconds"]
    return {"removed_seconds": round(removed, 3), "duration": round(project.duration, 3)}


def trim(project: Project, head: float = 0.0, tail: float = 0.0) -> dict:
    total = project.duration
    if head + tail >= total:
        raise VeditError(f"Cannot trim {head + tail:.2f}s from a {total:.2f}s timeline")
    return keep_range(project, head, total - tail)


def set_speed_range(project: Project, a: float, b: float, speed: float) -> dict:
    if not (0.1 <= speed <= 16):
        raise VeditError("Speed must be between 0.1 and 16")
    a, b = _check_range(project, a, b)
    i = split_at(project, a)
    j = split_at(project, b)
    old_len = b - a
    for c in project.clips[i:j]:
        c.speed = speed
    new_len = sum(c.duration for c in project.clips[i:j])
    ratio = new_len / old_len

    def fn(t: float) -> float:
        if t <= a:
            return t
        if t >= b:
            return t + (new_len - old_len)
        return a + (t - a) * ratio

    remap_overlays(project, fn)
    return {"clips": [c.id for c in project.clips[i:j]], "new_length": round(new_len, 3)}


def set_clip_speed(project: Project, clip_id: str, speed: float) -> dict:
    idx = [c.id for c in project.clips].index(project.clip(clip_id).id)
    s = project.clip_starts()[idx]
    return set_speed_range(project, s, s + project.clips[idx].duration, speed)


def move_clip(project: Project, clip_id: str, to_index: int) -> None:
    c = project.clip(clip_id)
    project.clips.remove(c)
    to_index = max(0, min(len(project.clips), to_index))
    project.clips.insert(to_index, c)


def remove_clip(project: Project, clip_id: str) -> dict:
    idx = [c.id for c in project.clips].index(project.clip(clip_id).id)
    s = project.clip_starts()[idx]
    return delete_range(project, s, s + project.clips[idx].duration)


def retime_clip(project: Project, clip_id: str, new_in: float, new_out: float) -> dict:
    """Change a clip's source in/out points (dragging its edges in the timeline).

    Overlays that sit on this clip stay attached to the same source moment; everything
    after the clip shifts by the change in length.
    """
    c = project.clip(clip_id)
    src = project.sources[c.source]
    new_in = max(0.0, min(new_in, src.duration))
    new_out = max(0.0, min(new_out, src.duration))
    if new_out - new_in < 0.05:
        raise VeditError("A clip must be at least 0.05s long")
    idx = [x.id for x in project.clips].index(c.id)
    s = project.clip_starts()[idx]
    in0, d0, sp = c.in_, c.duration, c.speed
    c.in_, c.out = round(new_in, 4), round(new_out, 4)
    d1 = c.duration

    def fn(t: float) -> float:
        if t <= s:
            return t
        if t >= s + d0:
            return t + (d1 - d0)
        u = in0 + (t - s) * sp  # source time under this overlay moment
        return min(s + d1, max(s, s + (u - c.in_) / sp))

    remap_overlays(project, fn)
    return {"clip": c.id, "in": c.in_, "out": c.out, "duration": round(d1, 3)}


def append_clip(project: Project, source_id: str, start: float = 0.0, end: float | None = None, at: int | None = None) -> Clip:
    src = project.sources[source_id]
    end = src.duration if end is None else min(end, src.duration)
    if end - start < EPS:
        raise VeditError("Clip range is empty")
    clip = Clip(id=project.new_id("c"), source=source_id, in_=round(start, 3), out=round(end, 3))
    if at is None:
        project.clips.append(clip)
    else:
        project.clips.insert(max(0, min(len(project.clips), at)), clip)
    return clip


def jumpcut_zoom(project: Project, zoom: float = 1.12) -> list[str]:
    """Punch in on every other clip so jump cuts look intentional."""
    changed = []
    for i, c in enumerate(project.clips):
        c.zoom = zoom if i % 2 == 1 else 1.0
        c.zoom_to = None
        if i % 2 == 1:
            changed.append(c.id)
    return changed

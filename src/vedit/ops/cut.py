"""Timeline editing: split, delete, keep, trim, speed, move.

All times are *timeline* seconds (what the viewer sees) unless noted.
Overlay layers, captions and music are remapped so they stay in sync after cuts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..project import Clip, Project, VeditError

EPS = 1e-3


def _check_range(project: Project, a: float, b: float) -> tuple[float, float]:
    total = project.duration
    a, b = max(0.0, a), min(total, b)
    if b - a < EPS:
        raise VeditError(f"Empty range {a:.3f}-{b:.3f}s (timeline is {total:.3f}s long)")
    return a, b


def _split_windows(left: Clip, right: Clip, at: float) -> None:
    """Filters timed to part of a clip stay on the same moments after a split: the right half's windows shift by the
    left half's length, and each half drops the filters whose window is entirely in the other half."""
    def timed(e):
        return e.type == "fx" and (e.params.get("start") or e.params.get("end") not in (None, ""))

    keep = []
    for e in left.effects:
        if timed(e) and float(e.params.get("start") or 0) >= at - EPS:
            continue
        if timed(e) and e.params.get("end") not in (None, "") and float(e.params["end"]) > at:
            e.params["end"] = None  # runs to the (new) end of the left half
        keep.append(e)
    left.effects = keep
    keep = []
    for e in right.effects:
        if timed(e):
            end = e.params.get("end")
            if end not in (None, "") and float(end) <= at + EPS:
                continue
            e.params["start"] = round(max(0.0, float(e.params.get("start") or 0) - at), 3)
            if end not in (None, ""):
                e.params["end"] = round(float(end) - at, 3)
        keep.append(e)
    right.effects = keep


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
            right = c.model_copy(update={"in_": src_t, "id": project.new_id("c"), "transition": None}, deep=True)
            clips[i : i + 1] = [left, right]
            from .fx_filters import _new_id

            for eff in right.effects:  # each half gets its own filter ids so they can be edited separately
                if eff.type == "fx":
                    eff.params["id"] = "?"
                    eff.params["id"] = _new_id(project)
            _split_windows(left, right, t - s)
            return i + 1
        if abs(t - e) < EPS:
            return i + 1
    return len(clips)


def remap_overlays(project: Project, fn: Callable[[float], float]) -> list[str]:
    """Apply a timeline remap to layers, caption words, music and markers. Returns removed layer ids."""
    removed = []
    keep = []
    for layer in project.layers:
        start = fn(layer.start)
        if layer.words:  # a caption phrase: its words (seconds from the phrase's start) follow the footage too
            words = []
            for w in layer.words:
                s, e = fn(layer.start + w.start), fn(layer.start + w.end)
                if e - s > EPS:
                    words.append(w.model_copy(update={"start": round(max(0.0, s - start), 3), "end": round(max(0.0, e - start), 3)}))
            layer.words = words
            if not words:  # every word was cut: the phrase goes
                removed.append(layer.id)
                continue
            layer.text = " ".join(w.text for w in words)
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
    for mk in project.markers:  # markers stay on the same moment of footage
        mk.t = round(fn(mk.t), 3)
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


GAP_SECONDS = 600.0  # one generated file covers any gap up to this long (longer ones use several gap clips)


def gap_source(project: Project, pdir: Path) -> str:
    """The source gap clips play: GAP_SECONDS of the canvas background colour, tiny, no sound (made once per project)."""
    from .. import media
    from ..project import Source, store_media_path

    hexc = project.canvas.background.lstrip("#").lower()[:6] or "000000"
    path = pdir / "media" / f".gap-{hexc}.mp4"
    stored = store_media_path(pdir, path)
    for sid, s in project.sources.items():
        if s.path == stored and path.exists():
            return sid
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        media.run([media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                   "-i", f"color=c=0x{hexc}:s=16x16:r=30:d={GAP_SECONDS:g}", "-c:v", "libx264", "-preset", "ultrafast",
                   "-tune", "stillimage", "-pix_fmt", "yuv420p", str(path)], "make gap")
    sid = next((k for k, s in project.sources.items() if s.path == stored), None) or project.new_id("s")
    project.sources[sid] = Source(path=stored, **media.probe(path))
    return sid


def _gap_clips(project: Project, sid: str, length: float) -> list[Clip]:
    out = []
    while length > 0.01:
        part = min(length, project.sources[sid].duration)
        out.append(Clip(id=project.new_id("gap"), source=sid, in_=0.0, out=round(part, 3), gap=True))
        length -= part
    return out


def place_clip(project: Project, pdir: Path, clip_id: str, start: float) -> float:
    """Move a main-track clip to a timeline time (dragging it). Dropped on empty space it stays there and the space
    before it becomes a gap; dropped over another clip it goes before that clip (pointer in its first half) or after
    it, and the clips after move along to make room. Other overlays keep their times. Returns the clip's new start."""
    moving = project.clip(clip_id)
    if moving.gap:
        raise VeditError("Drag the clips around a gap, or trim / delete the gap itself")
    starts = project.clip_starts()
    items = [[s, c] for s, c in zip(starts, project.clips) if not c.gap and c is not moving]
    start = max(0.0, float(start))
    end = start + moving.duration
    key = start
    for s, c in items:
        if start < s + c.duration - EPS and s < end - EPS:  # overlaps c
            if start < s + c.duration / 2:
                start = key = s
                key -= 1e-6  # before c
            else:
                start = key = s + c.duration
            break
    items.append([key, moving])
    items.sort(key=lambda x: x[0])
    clips: list[Clip] = []
    t, sid, placed = 0.0, None, 0.0
    for s, c in items:
        s = start if c is moving else s
        if s > t + 0.01:
            sid = sid or gap_source(project, pdir)
            clips += _gap_clips(project, sid, s - t)
            t = s
        if c is moving:
            placed = t
        clips.append(c)
        t += c.duration
    project.clips = clips
    return round(placed, 3)


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


def transition_lengths(project: Project) -> list[float]:
    """Effective transition length into each clip (0 = hard cut), clamped to fit both neighbours."""
    out = []
    for i, c in enumerate(project.clips):
        if i == 0 or c.transition is None:
            out.append(0.0)
            continue
        prev = project.clips[i - 1]
        d = min(c.transition.duration, prev.duration, c.duration)
        out.append(d if d >= 0.04 else 0.0)
    # a clip can't spend more than its own length blending in and out
    for i in range(len(out) - 1):
        c = project.clips[i]
        if out[i] + out[i + 1] > c.duration * 2 - 1e-6:
            k = c.duration * 2 / (out[i] + out[i + 1])
            out[i], out[i + 1] = out[i] * k, out[i + 1] * k
    return out


def set_transition(project: Project, kind: str, duration: float, clip_id: str | None = None) -> list[str]:
    """Transition into `clip_id` (from the clip before it), or between every pair of clips."""
    from ..project import Transition, transition_name

    if not 0.05 <= duration <= 3:
        raise VeditError("Transition duration must be between 0.05 and 3 seconds")
    t = Transition(type=transition_name(kind), duration=round(duration, 3))
    if clip_id:
        c = project.clip(clip_id)
        if project.clips.index(c) == 0:
            raise VeditError(f"{c.id} is the first clip; a transition goes on the clip it leads *into* (e.g. the second clip)")
        targets = [c]
    else:
        targets = project.clips[1:]
    if not targets:
        raise VeditError("Transitions need at least two clips on the timeline")
    for c in targets:
        c.transition = t.model_copy()
    return [c.id for c in targets]


def clear_transition(project: Project, clip_id: str | None = None) -> list[str]:
    targets = [project.clip(clip_id)] if clip_id else project.clips
    for c in targets:
        c.transition = None
    return [c.id for c in targets]

"""Exact timing, done in code: clamp clips to their files, snap cuts to beats, fit the length.

The AI gives rough lengths ("~1.2 s of f2 from 3.0 s"). Here every clip boundary is moved
to the nearest beat of the chosen music (when the style cuts on the beat), the music offset
is moved onto a beat so the timeline starts on one, and the edit is trimmed to the target.
"""

from __future__ import annotations

import bisect

from .spec import EditPlan, Op

MIN_CLIP = 0.35
SCENE_RANGE = (0.6, 6.0)  # a graphic scene's length, seconds


def _clip_len(o: Op) -> float:
    if o.op == "scene":
        return float(o.duration or 0)
    return ((o.end or 0) - (o.start or 0)) / (o.speed or 1.0)


def sequence(plan: EditPlan) -> list[Op]:
    """What plays on the video track, in order: footage clips and graphic scenes."""
    return [o for o in plan.ops if o.op in ("clip", "scene")]


def resolve(plan: EditPlan, files: dict[str, dict], target_s: float) -> tuple[EditPlan, list[str]]:
    """files: fid -> upload meta (with 'beats' for audio). Returns the fixed plan and notes about changes."""
    notes: list[str] = []
    ops: list[Op] = []
    for o in plan.ops:
        if o.op in ("clip", "image", "music") and o.file not in files:
            notes.append(f"dropped {o.op}: unknown file {o.file}")
            continue
        if o.op == "clip":
            f = files[o.file]
            if f["kind"] != "video":
                notes.append(f"dropped clip: {o.file} is not a video")
                continue
            dur = float(f["duration"])
            o.speed = min(4.0, max(0.25, o.speed or 1.0))
            o.start = max(0.0, min(o.start or 0.0, dur - MIN_CLIP))
            o.end = max(o.start + MIN_CLIP, min(o.end if o.end is not None else o.start + 2.0, dur))
            if o.zoom is not None:
                o.zoom = min(2.0, max(1.0, o.zoom))
            if o.zoom_to is not None:
                o.zoom_to = min(2.0, max(1.0, o.zoom_to))
        if o.op == "scene":
            o.duration = round(min(SCENE_RANGE[1], max(SCENE_RANGE[0], o.duration or 1.8)), 3)
        if o.op == "image" and files[o.file]["kind"] != "image":
            notes.append(f"dropped image: {o.file} is not an image")
            continue
        if o.op == "music" and files[o.file]["kind"] != "audio":
            notes.append(f"dropped music: {o.file} is not audio")
            continue
        ops.append(o)
    plan.ops = ops
    if not any(o.op == "clip" for o in ops):
        raise ValueError("The plan has no usable clips")
    clips = sequence(plan)

    music = next((o for o in ops if o.op == "music"), None)
    beats: list[float] = []
    if music is not None:
        m = files[music.file]
        mdur = float(m.get("duration") or 0)
        music.offset = max(0.0, min(music.offset or 0.0, max(0.0, mdur - 1)))
        song = m.get("beats") or []
        if plan.beat_sync and len(song) >= 4:
            i = bisect.bisect_left(song, music.offset - 0.05)
            if i < len(song):
                music.offset = round(song[i], 3)  # timeline 0 lands on a beat
                beats = [round(b - music.offset, 3) for b in song[i:]]
        elif plan.beat_sync:
            notes.append("music has no steady beat: cuts not snapped")
    if beats:
        _snap(clips, files, beats)
        notes.append(f"snapped {len(clips)} cuts to the beat")

    _fit(plan, clips, target_s, beats, notes)
    total = sum(_clip_len(o) for o in sequence(plan))

    for o in plan.ops:
        if o.op in ("text", "image"):
            o.start = max(0.0, min(o.start or 0.0, total - 0.3))
            if beats and o.op == "text" and o.start > 0:
                o.start = _nearest(beats, o.start)
            if o.end is not None:
                o.end = min(total, max(o.start + 0.3, o.end))
        if o.op == "transition":
            n = len(sequence(plan))
            if (o.into is not None and not (1 <= o.into < n)) or n < 2:
                o.into = -1  # points at no cut: drop it
            o.duration = max(0.1, min(o.duration or 0.3, 0.8))
    plan.ops = [o for o in plan.ops if not (o.op == "transition" and o.into == -1)]
    return plan, notes


def _nearest(beats: list[float], t: float) -> float:
    i = bisect.bisect_left(beats, t)
    cands = [beats[j] for j in (i - 1, i) if 0 <= j < len(beats)]
    return min(cands, key=lambda b: abs(b - t)) if cands else t


def _snap(clips: list[Op], files: dict[str, dict], beats: list[float]) -> None:
    t = 0.0
    for o in clips:
        if o.op == "scene":  # a graphic scene: its length lands on the beat nearest to the planned one
            want = t + _clip_len(o)
            later = [b for b in beats if SCENE_RANGE[0] <= b - t <= SCENE_RANGE[1]]
            if later:
                o.duration = round(min(later, key=lambda b: abs(b - want)) - t, 3)
            t += _clip_len(o)
            continue
        dur = float(files[o.file]["duration"])
        sp = o.speed or 1.0
        want = t + _clip_len(o)
        later = [b for b in beats if b >= t + MIN_CLIP - 1e-6]
        if not later:
            t += _clip_len(o)
            continue
        candidates = sorted(later[:12], key=lambda b: abs(b - want))
        for b in candidates:
            length = b - t
            need = length * sp
            if need <= dur:
                o.end = round(min(dur, (o.start or 0) + need), 3)
                if o.end - (o.start or 0) < need - 1e-3:  # not enough room after start: slide start back
                    o.start = round(max(0.0, o.end - need), 3)
                break
        t += _clip_len(o)


def _fit(plan: EditPlan, clips: list[Op], target: float, beats: list[float], notes: list[str]) -> None:
    """Fit the length by dropping / shortening footage from the end. Scenes (title cards, the end card) stay."""
    tol = max(1.0, 0.1 * target)
    total = sum(_clip_len(o) for o in clips)
    foot = [o for o in clips if o.op == "clip"]
    while len(foot) > 1 and total - _clip_len(foot[-1]) >= target - tol / 2 and total > target + tol:
        last = foot.pop()
        plan.ops.remove(last)
        clips.remove(last)
        total -= _clip_len(last)
        notes.append("dropped a clip to fit the length")
    if total > target + tol and clips[-1].op == "scene":  # scenes after it: just take the excess off the last footage
        last = foot[-1]
        new = max(MIN_CLIP, _clip_len(last) - (total - target))
        last.end = round((last.start or 0) + new * (last.speed or 1.0), 3)
        notes.append(f"shortened clip {len(foot)} to fit the length")
        return
    if total > target + tol:
        last = clips[-1]
        start_t = total - _clip_len(last)
        end_t = target
        if beats:
            end_t = max([b for b in beats if start_t + MIN_CLIP <= b <= target + 0.01] or [target])
        last.end = round((last.start or 0) + max(MIN_CLIP, end_t - start_t) * (last.speed or 1.0), 3)
        notes.append(f"shortened the last clip to end at {end_t:.2f}s")

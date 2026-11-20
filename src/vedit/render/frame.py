"""One preview frame at a timeline moment, fast: the editor shows it while the full preview re-renders, and while
a slider is being dragged (with the dragged values applied before they are saved).

It renders only the clip under the playhead, trimmed to a couple of frames around that moment, through the same
graph as the preview (fit, zoom, colour grade, effects, filters), without overlay layers (the editor draws those)
or sound. Approximations, fine for a frame that is replaced a second later by the real render:
  - transitions are ignored (the clip is shown alone);
  - filters that move over time (shake, zoom pulse, grain) start their motion at this moment.
A slow zoom (zoom_to) is resolved to its zoom at this moment, so the framing is exact.
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path

from .. import media
from ..ops import effects
from ..project import Clip, Project, VeditError, state_dir
from .graph import build
from .runner import PREVIEW_SCALE


def clip_at(project: Project, t: float) -> tuple[int, float]:
    """Index of the clip shown at timeline time t, and t relative to that clip's start."""
    if not project.clips:
        raise VeditError("Timeline is empty")
    starts = project.clip_starts()
    t = max(0.0, min(t, project.duration - 1e-3))
    for i, (c, s) in enumerate(zip(project.clips, starts)):
        if s <= t < s + c.duration:
            return i, t - s
    return len(project.clips) - 1, project.clips[-1].duration - 1e-3


def frame_project(project: Project, t: float, clip_override: dict | None = None) -> Project:
    """A one-clip project whose first frame is what the timeline shows at t."""
    i, local = clip_at(project, t)
    clip = project.clips[i]
    if clip_override:
        if clip_override.get("id") not in (None, clip.id):
            # the dragged clip isn't under the playhead: show that clip's first frame instead
            j = next((k for k, c in enumerate(project.clips) if c.id == clip_override["id"]), None)
            if j is None:
                raise VeditError(f"No clip {clip_override['id']}")
            i, local, clip = j, 0.0, project.clips[j]
        clip = Clip.model_validate({**clip.model_dump(mode="json", by_alias=True), **clip_override})
    c = clip.model_copy(deep=True)
    src = project.sources[c.source]
    fps = project.canvas.fps
    src_t = c.in_ + local * c.speed
    span = max(2.0 / fps, 2.0 * c.speed / fps)  # two output frames of source
    end = min(c.out, src_t + span)
    if end - src_t < span:  # at the very end of the clip: step back so there is footage
        src_t = max(c.in_, end - span)
    if c.zoom_to:  # slow zoom: freeze it at the zoom it has reached here
        from ..project import zoom_at

        z = zoom_at(c, local / max(c.duration, 1e-6))
        c.zoom, c.zoom_to = z, z
    # filters timed to part of the clip: the mini clip starts `shift` s into the clip, so their windows move back by it
    shift = (src_t - clip.in_) / c.speed
    for e in c.effects:
        if e.type == "fx" and (e.params.get("start") or e.params.get("end") not in (None, "")
                               or e.params.get("fade_in") or e.params.get("fade_out")):
            e.params["start"] = float(e.params.get("start") or 0) - shift
            if e.params.get("end") not in (None, ""):
                e.params["end"] = float(e.params["end"]) - shift
            elif e.params.get("fade_out"):
                e.params["end"] = clip.duration - shift
    c.in_, c.out, c.transition = src_t, end, None
    # adjustment layers over this moment still apply (they change the picture), from the frame's time 0
    adjust = [l.model_copy(update={"start": 0.0, "end": None, "keyframes": [], "presets": []}) for l in project.layers
              if l.type == "adjust" and l.start <= t < (project.duration if l.end is None else l.end)]
    return project.model_copy(update={"clips": [c], "layers": adjust, "music": [],
                                      "canvas": project.canvas.model_copy(update={"loudness": None})})


_LOCK = threading.Lock()


def render_frame(project: Project, pdir: Path, t: float, clip_override: dict | None = None,
                 scale: float = PREVIEW_SCALE) -> bytes:
    """JPEG bytes of the timeline at t (no overlays). Raises effects.NotReady when a clip effect still needs its
    one-time computation (the background render is doing it)."""
    mini = frame_project(project, t, clip_override)
    tmpdir = state_dir(pdir) / "tmp"
    tmpdir.mkdir(exist_ok=True)
    out = tmpdir / f"frame-{os.getpid()}-{uuid.uuid4().hex[:8]}.jpg"
    token = effects.CACHED_ONLY.set(True)
    try:
        with _LOCK:  # one at a time: the graph file is shared, and the newest request matters most anyway
            cmd, _ = build(mini, pdir, out, scale, "frame", audio=False, layers=False,
                           encode=["-frames:v", "1", "-c:v", "mjpeg", "-q:v", "3",
                                   # full-range JPEG pixels, converted properly: without it dark areas come out
                                   # lighter than in the video preview
                                   "-pix_fmt", "yuvj420p", "-f", "image2", "-update", "1"])
            media.run(cmd, "ffmpeg (preview frame)")
        return out.read_bytes()
    finally:
        effects.CACHED_ONLY.reset(token)
        out.unlink(missing_ok=True)

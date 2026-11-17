"""Timeline tracks, shared by the editor's drag & drop (editor/actions.py) and the CLI, so the AI can do everything the
timeline can: place overlays on tracks, move clips between the main track and overlay tracks, hide tracks, colour a
video overlay or adjustment layer, trim music."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..project import Layer, MusicTrack, Project, VeditError
from . import cut

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_KEEP = object()  # "leave as it is" (None is a real value for an end: open-ended)


def place_layer(p: Project, layer_id: str, start: float | None = None, end: Any = _KEEP, track: int | None = None,
                new_track: bool = False, src_in: float | None = None) -> Layer:
    """New times and / or track for an overlay. A video layer trimmed from its left edge also moves `src_in`, so the
    picture keeps playing in sync (like trimming a clip), and can't stretch past its file unless it loops."""
    from . import overlays

    l = p.layer(layer_id)
    if l.type == "video" and l.source in p.sources:
        src = p.sources[l.source]
        if src_in is not None:
            l.src_in = min(max(0.0, float(src_in)), max(0.0, src.duration - 0.05))
        if not l.loop and start is not None:
            room = src.duration - l.src_in
            e = l.end if end is _KEEP else end
            if e is None or e - start > room:
                end = round(start + room, 3)
    row = (l.track or 0) if track is None else int(track)
    overlays.place_layer(p, l.id, row, start, ... if end is _KEEP else end, new_track)
    return l


def add_media_layer(p: Project, source_id: str, start: float = 0.0, track: int | None = None,
                    new_track: bool = False) -> Layer:
    """A video or image source as an overlay (picture-in-picture / b-roll / sticker), optionally on a given track."""
    from . import overlays

    if source_id not in p.sources:
        raise VeditError(f"No source {source_id}")
    src = p.sources[source_id]
    start = max(0.0, min(float(start or 0), max(0.0, p.duration - 0.5)))
    W = p.canvas.width
    if Path(src.path).suffix.lower() in IMAGE_EXT:
        l = Layer(id=p.new_id("img"), type="image", image=src.path, start=start,
                  end=round(min(max(p.duration, start + 0.5), start + 3), 3), width=int(W * 0.6))
        p.layers.append(l)
    elif src.has_video:
        l = overlays.add_video(p, source_id, start, None, x="50%", y="50%", width=int(W * 0.6))
    else:
        raise VeditError("Audio files go on the music track")
    if track is not None:
        overlays.place_layer(p, l.id, int(track), new_track=new_track)
    return l


def clip_to_overlay(p: Project, clip_id: str, start: float = 0.0, track: int = 0, new_track: bool = True) -> Layer:
    """A main-track clip moved onto the overlay tracks (CapCut): it leaves the main track (which closes the gap) and
    becomes a full-frame video layer at `start`, keeping its sound, colour and filters. Text / stickers sitting entirely
    on the clip travel with it."""
    from . import overlays

    c = p.clip(clip_id)
    if c.gap:
        raise VeditError(f"{c.id} is a gap, not a clip")
    if abs(c.speed - 1) > 1e-3:
        raise VeditError("Overlays play at normal speed: set this clip's speed back to 1× before moving it to an overlay track")
    sid, src_in, length, vol, grade = c.source, c.in_, c.out - c.in_, c.volume, c.grade
    fx = [e for e in c.effects if e.type == "fx"]
    s0 = p.clip_starts()[[x.id for x in p.clips].index(c.id)]
    d0 = c.duration
    riders = [l.model_copy(deep=True) for l in p.layers if l.type != "captions" and l.start >= s0 - 1e-3
              and l.end is not None and l.end <= s0 + d0 + 1e-3]
    cut.remove_clip(p, c.id)
    if p.duration <= 0.05:
        raise VeditError("Keep at least one clip on the main track")
    start = max(0.0, min(float(start or 0), p.duration - 0.1))
    rider_ids = {r.id for r in riders}
    p.layers = [l for l in p.layers if l.id not in rider_ids]
    for r in riders:
        r.start, r.end = round(start + r.start - s0, 3), round(min(p.duration, start + r.end - s0), 3)
        if r.end - r.start >= 0.05:
            p.layers.append(r)
    l = overlays.add_video(p, sid, start, round(min(p.duration, start + length), 3), src_in=src_in,
                           x="50%", y="50%", width=p.canvas.width, volume=vol)
    l.grade, l.effects = grade, fx
    overlays.place_layer(p, l.id, int(track or 0), new_track=new_track)
    return l


def overlay_to_clip(p: Project, layer_id: str, index: int | None = None):
    """A video overlay moved onto the main track: it becomes a clip there (inserted at `index`, default: the end)."""
    l = p.layer(layer_id)
    if l.type != "video" or not l.source:
        raise VeditError("Only video overlays can go on the main track")
    end = l.end if l.end is not None else p.duration
    src = p.sources[l.source]
    out = min(src.duration, l.src_in + max(0.05, end - l.start))
    p.layers.remove(l)
    clip = cut.append_clip(p, l.source, l.src_in, out, index)
    clip.volume = l.volume if l.volume > 0 else 1.0
    clip.grade = l.grade
    clip.effects = [e for e in l.effects if e.type == "fx"]
    return clip


def set_track_visible(p: Project, track: str | int | None, visible: bool) -> str:
    """The eye on a track: show / hide everything on an overlay track (1-based in messages, 0-based here), or the
    main video track ("main"). Hidden = not drawn in the preview or the export; sound still plays."""
    hide = not visible
    if track in ("main", None, ""):
        p.main_hidden = hide
        return f"main track {'hidden' if hide else 'shown'}"
    t = int(track)
    hit = [l for l in p.layers if l.track == t]
    if not hit:
        raise VeditError(f"No overlay track {t} (tracks: {', '.join(str(x) for x in sorted({l.track for l in p.layers})) or 'none'})")
    for l in hit:
        l.hidden = hide
    return f"track {t} {'hidden' if hide else 'shown'}"


GRADE_KEYS = ("brightness", "contrast", "saturation", "temperature", "tint", "intensity")


def grade_layer(p: Project, layer_id: str, values: dict | None = None, look: str | None = None, clear: bool = False) -> Layer:
    """Colour of a video overlay or an adjustment layer: correction values, a look (+ intensity), or clear."""
    from ..project import Grade
    from . import color

    l = p.layer(layer_id)
    if l.type not in ("adjust", "video"):
        raise VeditError(f"{l.id} is a {l.type} layer: colour goes on clips, video overlays or adjustment layers")
    if clear:
        l.grade = None
        return l
    vals = {k: float(v) for k, v in (values or {}).items() if k in GRADE_KEYS and v not in (None, "")}
    if look is not None:
        vals["look"] = None if look in ("", "natural") else color.look_name(look)
    l.grade = (l.grade or Grade()).model_copy(update=vals)
    return l


def set_music(p: Project, music_id: str, volume: float | None = None, start: float | None = None,
              in_: float | None = None, end: Any = _KEEP, duck: bool | None = None, loop: bool | None = None) -> MusicTrack:
    """Music settings, incl. trimming: `in_` = where in the song it starts, `end` = timeline time it stops (None: the end)."""
    m = next((m for m in p.music if m.id == music_id), None)
    if not m:
        raise VeditError(f"No music track {music_id}")
    if volume is not None:
        m.volume = float(volume)
    if start is not None:
        m.start = float(start)
    if in_ is not None:
        m.in_ = max(0.0, float(in_))
    if end is not _KEEP:
        m.end = None if end is None else float(end)
    if m.end is not None and m.end - m.start < 0.1:
        raise VeditError("Music end must be after its start")
    if duck is not None:
        m.duck = bool(duck)
    if loop is not None:
        m.loop = bool(loop)
    return m


def track_rows(p: Project) -> list[dict]:
    """Overlay tracks bottom to top, with what is on each, plus the main track."""
    rows: dict[int, list[Layer]] = {}
    for l in p.layers:
        rows.setdefault(l.track or 0, []).append(l)
    out = [{"track": "main", "hidden": p.main_hidden, "items": [c.id for c in p.clips],
            "gaps": [c.id for c in p.clips if c.gap]}]
    for t in sorted(rows):
        ls = rows[t]
        out.append({"track": t, "hidden": all(l.hidden for l in ls),
                    "items": [f"{l.id} ({l.type} {l.start:.2f}-{'end' if l.end is None else f'{l.end:.2f}'}s)" for l in ls]})
    return out

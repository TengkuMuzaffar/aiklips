"""Signature moves: named editing recipes that make an edit memorable, built from vedit's own primitives.

The AI picks a move and where (a clip, or a timeline moment); code does it the same way every time, as one
undoable step. The study step maps a reference's moves onto these names (references/dna.py), and code
confirms the ones it can measure.

    apply(project, "punch_in", at=3.2)
    apply(project, "flash_cut", clip="c4")
"""

from __future__ import annotations

from ..project import Layer, Preset, Project, TextStyle, VeditError
from . import cut, text_looks

# name: (needs, description)   needs: "at" = a timeline moment, "clip" = a clip it applies to, "+text" = a word/line
MOVES: dict[str, tuple[str, str]] = {
    "punch_in": ("at", "Jump-cut punch-in: from this moment the shot is 1.18x closer (beat drops, key words)"),
    "speed_ramp": ("clip", "The end of the clip speeds up 2.5x and whooshes into the next shot (into a reveal)"),
    "freeze_title": ("at+text", "Near-freeze (~0.8 s) on this moment with a title popping in (a reveal, a key fact)"),
    "flash_cut": ("clip", "White flash into the clip (beat hits, transformations)"),
    "whip": ("clip", "Whip-pan blur into the clip (energy, a change of place)"),
    "keyword_pop": ("at+text", "One huge punchy word that slams in and shakes (WOW, 50% OFF, WAIT)"),
    "before_after": ("clip", "Wipe into the clip with BEFORE on the shot before the cut and AFTER on this one"),
    "zoom_out_reveal": ("clip", "The clip starts 1.4x zoomed in and pulls back to show everything (reveal the product/scene)"),
}
MOVE_NAMES = tuple(MOVES)


def _at_clip(p: Project, at: float) -> int:
    for i, (c, s) in enumerate(zip(p.clips, p.clip_starts())):
        if s - 1e-3 <= at < s + c.duration:
            return i
    raise VeditError(f"No clip at {at:.2f}s (timeline is {p.duration:.2f}s)")


def _title(p: Project, text: str, start: float, end: float, look: str, y: str = "40%") -> Layer:
    l = Layer(id=p.new_id("t"), type="text", text=text.strip()[:60] or "WOW", start=round(start, 3), end=round(end, 3), x="48%", y=y,
              style=TextStyle(), why=None)
    text_looks.apply(l, look)
    l.style.max_width = 820
    p.layers.append(l)
    return l


def apply(p: Project, name: str, clip: str | None = None, at: float | None = None, text: str | None = None) -> str:
    if name not in MOVES:
        raise VeditError(f"Unknown move '{name}'. Moves: {', '.join(MOVES)}")
    needs = MOVES[name][0]
    if needs.startswith("at") and at is None:
        if clip is None:
            raise VeditError(f"{name} needs --at (timeline seconds) or --clip")
        idx = [c.id for c in p.clips].index(p.clip(clip).id)
        c = p.clips[idx]
        at = p.clip_starts()[idx] + c.duration * 0.35
    if needs == "clip" and clip is None:
        if at is None:
            raise VeditError(f"{name} needs --clip (or --at to pick the clip under that moment)")
        clip = p.clips[_at_clip(p, at)].id
    if "+text" in needs and not (text or "").strip():
        raise VeditError(f"{name} needs --text")

    if name == "punch_in":
        i = cut.split_at(p, at)
        if i >= len(p.clips):
            raise VeditError(f"{at:.2f}s is at the end of the timeline")
        c = p.clips[i]
        c.zoom, c.zoom_to = max(c.zoom, 1.18), None
        c.transition = None
        return f"punch-in at {at:.2f}s ({c.id})"
    if name == "speed_ramp":
        idx = [c.id for c in p.clips].index(p.clip(clip).id)
        c = p.clips[idx]
        if c.duration < 0.6:
            raise VeditError(f"{clip} is too short for a speed ramp")
        i = cut.split_at(p, p.clip_starts()[idx] + c.duration * 0.6)
        cut.set_clip_speed(p, p.clips[i].id, min(4.0, p.clips[i].speed * 2.5))
        return f"speed ramp at the end of {clip}"
    if name == "freeze_title":
        i = cut.split_at(p, at)
        if i >= len(p.clips):
            raise VeditError(f"{at:.2f}s is at the end of the timeline")
        hold_src = 0.08 * p.clips[i].speed
        j = cut.split_at(p, at + 0.08)
        if j - i != 1:
            raise VeditError("not enough footage after that moment to freeze")
        cut.set_clip_speed(p, p.clips[i].id, 0.1)
        dur = p.clips[i].duration
        l = _title(p, text or "", at, at + dur + 0.6, "viral-hook")
        return f"freeze {dur:.1f}s at {at:.2f}s with '{l.text}' (source {hold_src:.2f}s)"
    if name in ("flash_cut", "whip", "before_after"):
        idx = [c.id for c in p.clips].index(p.clip(clip).id)
        if idx == 0:
            raise VeditError(f"{name} goes INTO a clip; {clip} is the first clip")
        kind, dur = {"flash_cut": ("fadewhite", 0.25), "whip": ("hblur", 0.3), "before_after": ("wipeleft", 0.5)}[name]
        cut.set_transition(p, kind, dur, clip)
        if name == "before_after":
            s = p.clip_starts()[idx]
            prev = p.clips[idx - 1]
            _title(p, "BEFORE", max(s - min(1.5, prev.duration), 0.0), s - 0.15, "boxed-label", y="18%")
            _title(p, "AFTER", s + 0.15, s + min(1.8, p.clips[idx].duration), "boxed-label", y="18%")
        return f"{name} into {clip}"
    if name == "keyword_pop":
        l = _title(p, (text or "").upper(), at, at + 1.2, "comic-pop", y="42%")
        l.presets = [Preset(name="punch"), Preset(name="shake", params={"amplitude": 6}), Preset(name="pop_out")]
        return f"keyword '{l.text}' at {at:.2f}s"
    if name == "zoom_out_reveal":
        c = p.clip(clip)
        c.zoom, c.zoom_to = 1.4, 1.0
        return f"zoom-out reveal on {clip}"
    raise VeditError(f"move {name} not implemented")


def catalog() -> list[dict]:
    return [{"name": k, "needs": v[0], "about": v[1]} for k, v in MOVES.items()]

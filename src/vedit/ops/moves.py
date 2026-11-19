"""Signature moves: named editing recipes that make an edit memorable, built from vedit's own primitives.

The AI picks a move and where (a clip, or a timeline moment); code does it the same way every time, as one
undoable step. The study step maps a reference's moves onto these names (references/dna.py), and code
confirms the ones it can measure.

    apply(project, "punch_in", at=3.2)
    apply(project, "flash_cut", clip="c4")
"""

from __future__ import annotations

from ..project import Keyframe, Layer, Preset, Project, TextStyle, VeditError
from . import cut, text_looks

# name: (needs, description)   needs: "at" = a timeline moment, "clip" = a clip it applies to, "+text" = a word/line
# ("+lines" = several, separated by ';'), "+source" = a video to show
MOVES: dict[str, tuple[str, str]] = {
    "punch_in": ("at", "Jump-cut punch-in: from this moment the shot is 1.18x closer (beat drops, key words)"),
    "speed_ramp": ("clip", "The end of the clip speeds up 2.5x and whooshes into the next shot (into a reveal)"),
    "freeze_title": ("at+text", "Near-freeze (~0.8 s) on this moment with a title popping in (a reveal, a key fact)"),
    "flash_cut": ("clip", "White flash into the clip (beat hits, transformations)"),
    "whip": ("clip", "Whip-pan blur into the clip (energy, a change of place)"),
    "keyword_pop": ("at+text", "One huge punchy word that slams in and shakes (WOW, 50% OFF, WAIT)"),
    "before_after": ("clip", "Wipe into the clip with BEFORE on the shot before the cut and AFTER on this one"),
    "zoom_out_reveal": ("clip", "The clip starts 1.4x zoomed in and pulls back to show everything (reveal the product/scene)"),
    # signature effects (step 1): camera moves and graphics built from the same primitives
    "snap_zoom": ("at", "Snap zoom: the shot slams 1.35x closer in 0.2 s and holds (a reaction, the key word, a detail)"),
    "digital_zoom": ("at", "Digital zoom: three quick stepped punch-ins (1.12 / 1.26 / 1.42x), crunchy and urgent"),
    "growth_curve": ("at", "A curve that shoots up and draws itself, with the number counting up (results, growth, '+340%')"),
    "checklist": ("at+lines", "Checklist: the items appear one by one and each gets a tick drawn on ('Fast; Cheap; Easy')"),
    "checklist_pop": ("at+lines", "Checklist pop: each item pops in with a green check badge, bouncy (tips, features, steps)"),
    "headline_storm": ("at+lines", "Headline storm: headlines slam in one after another at angles and pile up (news, hype, 'everyone is talking')"),
    "broll_overlay": ("at+source", "B-roll overlay: another video fills the screen over the talking head for a moment, sound stays"),
    # step 2
    "chat_takeover": ("at+lines", "Chat takeover: the screen dims and a chat plays out, bubbles and typing dots ('them: hey; me: did you see?')"),
    "phone_mockup": ("at", "Phone mockup: the shot (or --source) plays inside a phone that rises in, on a clean backdrop"),
    "magazine_cover": ("at+lines", "Magazine cover: the person stands in front of a giant masthead, with cover lines ('TITLE; line; line')"),
    "zoom_portal": ("clip", "Zoom portal: the shot before zooms in fast and the next shot opens out of it"),
    "cursor_click": ("at", "Screencast cursor: an arrow glides to a spot and clicks with a ripple (--text '30%,70% > 60%,40%')"),
    "hud_lock": ("at", "HUD lock-on: corner brackets snap onto a target and a label types in (--box x,y,w,h in %, --text LABEL)"),
    "paper_title": ("at+lines", "Paper cut-out title: paper cards with shadows drop in and sway on twos ('TITLE; subtitle')"),
    # step 3
    "liquid_glass": ("at", "Liquid glass: a frosted glass panel over the shot (blurs what's behind), with a title on it (--text)"),
    "iso_world": ("at", "Isometric miniature world that builds itself block by block (--text city|island|desk|stack)"),
    "orbit": ("at+lines", "Orbit: words circle around the person, passing behind and in front of them ('fast; fun; free')"),
    "dolly_zoom": ("at", "Dolly zoom (vertigo): the person stays the same size while the background rushes in (--amount 1.4)"),
    "frame_break": ("at", "Frame break: the shot sits in a frame and the person steps out over its edge (--text caption)"),
    "studio_reveal": ("at", "Studio reveal: the background wipes away to a lit studio set while the person stays"),
}
ALIASES = {"frame_escape": "frame_break", "vertigo": "dolly_zoom", "glass": "liquid_glass", "isometric": "iso_world"}
# how each effect is shown in the editor: category for the Effects tab
CATEGORY = {k: "Camera" for k in ("punch_in", "snap_zoom", "digital_zoom", "zoom_out_reveal", "speed_ramp")} | {
    k: "Graphics" for k in ("growth_curve", "checklist", "checklist_pop", "headline_storm", "keyword_pop", "freeze_title")} | {
    k: "Transitions" for k in ("flash_cut", "whip", "before_after", "zoom_portal")} | {"broll_overlay": "B-roll"} | {
    "chat_takeover": "Graphics", "phone_mockup": "Frames", "magazine_cover": "Frames",
    "cursor_click": "Kits", "hud_lock": "Kits", "paper_title": "Kits",
    "liquid_glass": "Graphics", "iso_world": "Graphics", "orbit": "Subject", "dolly_zoom": "Subject", "frame_break": "Subject",
    "studio_reveal": "Subject"}
# what Generate plans / reference Style DNA may name: moves that need nothing but a clip, a moment and a text
MOVE_NAMES = tuple(k for k, v in MOVES.items() if "+source" not in v[0])


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


def apply(p: Project, name: str, clip: str | None = None, at: float | None = None, text: str | None = None,
          source: str | None = None, dur: float | None = None, amount: float | None = None, box: str | None = None) -> str:
    name = ALIASES.get(name, name)
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
    if "+lines" in needs and not lines(text):
        raise VeditError(f"{name} needs --text with the items separated by ';' (e.g. 'Fast; Cheap; Easy')")
    if "+source" in needs and not source:
        raise VeditError(f"{name} needs --source (an imported video id, e.g. s2)")
    if name in EFFECTS:
        before = {l.id for l in p.layers}
        msg = EFFECTS[name](p, at if at is not None else 0.0, text=text, source=source, dur=dur, amount=amount, box=box, clip=clip)
        new = [l.id for l in p.layers if l.id not in before]
        if len(new) >= 2:  # one effect = one group: a single row on the timeline, moved as one
            from . import groups

            g = groups.make(p, new, name.replace("_", " ").capitalize())
            msg += f" [group {g.id}: {len(new)} layers]"
        return msg

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
    return [{"name": k, "needs": v[0], "about": v[1], "category": CATEGORY.get(k, "Moves"),
             "preview": k in PREVIEWS} for k, v in MOVES.items()]


# ---- signature effects ------------------------------------------------------------------------------------------

def lines(text: str | None) -> list[str]:
    return [x.strip() for x in (text or "").replace("\n", ";").split(";") if x.strip()][:8]


def _zoom_steps(p: Project, at: float, steps: list[tuple[float, float, str]], hold: float | None) -> list[str]:
    """Cut the shot at `at` (+ each step's offset) and give each piece its zoom: (offset s, zoom, ease of the ramp into
    it). A ramp piece animates zoom -> next zoom with the ease; the last zoom holds until `hold` s after `at` (then the
    shot cuts back to its own framing) or the end of the clip."""
    i0 = _at_clip(p, at)
    base = p.clips[i0]
    z0 = max(1.0, base.zoom)
    if base.zoom_to:
        raise VeditError(f"{base.id} already has a slow zoom; pick a moment in another clip (or `vedit zoom 1 --clip {base.id}`)")
    end_clip = p.clip_starts()[i0] + base.duration
    cuts = [at + off for off, _, _ in steps] + ([at + hold] if hold else [])
    if max(cuts) >= end_clip - 0.05:
        raise VeditError(f"Not enough of {base.id} left after {at:.2f}s for this zoom (it ends at {end_clip:.2f}s)")
    ids = []
    for k, (off, z, ease_name) in enumerate(steps):
        i = cut.split_at(p, at + off)
        c = p.clips[i]
        c.transition = None
        if ease_name:  # a short ramp piece into this zoom, then the next cut holds it
            ramp = min(0.2, (steps[k + 1][0] - off) if k + 1 < len(steps) else 0.2)
            j = cut.split_at(p, at + off + ramp)
            c = p.clips[i]
            prev = steps[k - 1][1] if k else 1.0
            c.zoom, c.zoom_to, c.zoom_ease = z0 * prev, z0 * z, ease_name
            p.clips[j].zoom, p.clips[j].zoom_to, p.clips[j].transition = z0 * z, None, None
            ids += [c.id, p.clips[j].id]
        else:
            c.zoom, c.zoom_to = z0 * z, None
            ids.append(c.id)
    if hold:
        i = cut.split_at(p, at + hold)
        p.clips[i].zoom, p.clips[i].zoom_to, p.clips[i].transition = z0, None, None
    return ids


def _snap_zoom(p: Project, at: float, amount=None, dur=None, **_) -> str:
    z = max(1.05, min(2.5, amount or 1.35))
    _zoom_steps(p, at, [(0.0, z, "snap")], dur)
    return f"snap zoom to {z:g}x at {at:.2f}s" + (f", back after {dur:g}s" if dur else "")


def _digital_zoom(p: Project, at: float, amount=None, dur=None, **_) -> str:
    top = max(1.15, min(2.5, amount or 1.42))
    steps = [(0.0, 1 + (top - 1) * 0.29), (0.18, 1 + (top - 1) * 0.62), (0.36, top)]
    _zoom_steps(p, at, [(o, z, "") for o, z in steps], dur)
    return f"digital zoom in 3 steps to {top:g}x at {at:.2f}s" + (f", back after {dur:g}s" if dur else "")


def _layer_end(p: Project, at: float, dur: float) -> float:
    return round(at + dur, 3)


def _growth_curve(p: Project, at: float, text=None, dur=None, amount=None, **_) -> str:
    from . import motion

    W, H = p.canvas.width, p.canvas.height
    d = dur or 3.5
    k = max(1.5, min(12.0, amount or 4.0))  # how steep: last value / first
    vals = [round(10 * k ** (i / 7) ** 1.6, 2) for i in range(8)]
    ch = motion.add_chart(p, "line", [{"label": "", "value": v} for v in vals], at, _layer_end(p, at, d), "50%", "58%",
                          round(W * 0.8), round(H * 0.32 if H > W else H * 0.45), duration=1.4,
                          colors=["#A3E635"], show_values=False, show_labels=False, line_width=16)
    ch.presets[0].params = {"ease": "ease_in_out"}
    label = (text or "").strip()
    num = "".join(c for c in label if c.isdigit() or c in ".-")
    if num and num not in ("-", ".", "-."):
        pre = label[:label.index(num[0])] if num[0] in label else ""
        suf = label[label.rindex(num[-1]) + 1:] if num[-1] in label else ""
        t = motion.add_counter(p, float(num), at + 0.2, _layer_end(p, at, d), "50%", "28%", 1.4, "expo_out",
                               {"size": round(W * 0.16), "color": "#A3E635", "stroke_width": 0},
                               **{"prefix": pre, "suffix": suf, "decimals": len(num.split(".")[1]) if "." in num else 0})
        return f"growth curve at {at:.2f}s ({ch.id}) counting to {t.text}"
    if label:
        _title(p, label, at + 0.2, _layer_end(p, at, d), "viral-hook", y="28%")
    return f"growth curve at {at:.2f}s ({ch.id})"


def _left_text(p: Project, text: str, x0: float, y: float, start: float, end: float, size: int) -> Layer:
    """A left-aligned line of text whose left edge is at x0 (layers are positioned by their centre)."""
    from .text import render_text

    st = TextStyle(size=size, stroke_width=0, shadow=True, max_width=round(p.canvas.width * 0.7), align="left", font="poppins-bold")
    w = render_text(text, st).width
    l = Layer(id=p.new_id("t"), type="text", text=text, start=round(start, 3), end=round(end, 3), x=round(x0 + w / 2), y=round(y), style=st)
    p.layers.append(l)
    return l


def _checklist(p: Project, at: float, text=None, dur=None, pop: bool = False, **_) -> str:
    from . import overlays

    items = lines(text)
    W, H = p.canvas.width, p.canvas.height
    gap = 0.55 if pop else 0.7
    end = _layer_end(p, at, dur or max(3.0, 1.2 + gap * len(items) + 1.5))
    size = round(min(W, H) * (0.07 if len(items) <= 4 else 0.055))
    row = size * 1.9
    top = H * 0.5 - row * (len(items) - 1) / 2
    x_box = W * 0.16
    for i, it in enumerate(items):
        t0, y = at + i * gap, top + i * row
        box = overlays.add_shape(p, "ellipse" if pop else "rect", t0, end, x=round(x_box), y=round(y), width=round(size * 1.15),
                                 height=round(size * 1.15), radius=round(size * 0.22),
                                 fill="#22C55E" if pop else None, border_width=0 if pop else max(4, size // 12), border_color="#FFFFFF")
        tick = overlays.add_shape(p, "path", t0 + (0.12 if pop else 0.3), end, x=round(x_box), y=round(y), width=round(size * 0.7),
                                  height=round(size * 0.55), border_width=max(5, size // 9),
                                  border_color="#FFFFFF" if pop else "#A3E635", fill=None)
        tick.path = [[0.0, 0.55], [0.38, 1.0], [1.0, 0.0]]
        tick.presets = [Preset(name="draw_in", duration=0.3, params={"ease": "ease_out"})]
        label = _left_text(p, it, x_box + size * 1.0, y, t0, end, size)
        if pop:
            box.presets = [Preset(name="pop_in", duration=0.35)]
            label.presets = [Preset(name="pop_in", duration=0.35)]
        else:
            box.presets = [Preset(name="fade_in", duration=0.2)]
            label.presets = [Preset(name="slide_in", duration=0.35, params={"direction": "left", "distance": 60})]
    return f"{'checklist pop' if pop else 'checklist'} of {len(items)} at {at:.2f}s"


def _headline_storm(p: Project, at: float, text=None, dur=None, **_) -> str:
    import random

    items = lines(text)
    W, H = p.canvas.width, p.canvas.height
    gap = 0.32
    end = _layer_end(p, at, dur or max(2.5, gap * len(items) + 1.6))
    rnd = random.Random(len(items) * 7919 + sum(map(len, items)))  # same layout every time for the same headlines
    for i, it in enumerate(items):
        last = i == len(items) - 1
        size = round(W * (0.085 if last else 0.06))
        st = TextStyle(size=size, color="#111111", bg_color="#FFFFFF" if i % 3 else "#FFE600", bg_padding=round(size * 0.35),
                       bg_radius=6, stroke_width=0, uppercase=True, font="anton", max_width=round(W * 0.78))
        y = H * (0.5 if last else 0.22 + 0.56 * ((i * 0.37 + rnd.random() * 0.3) % 1))
        l = Layer(id=p.new_id("t"), type="text", text=it, start=round(at + i * gap, 3), end=end,
                  x=round(W * (0.5 if last else 0.3 + rnd.random() * 0.4)), y=round(y), rotation=0 if last else rnd.uniform(-9, 9),
                  style=st, presets=[Preset(name="punch", params={"amount": 0.45}), Preset(name="shake", params={"amplitude": 3})])
        p.layers.append(l)
    return f"headline storm of {len(items)} at {at:.2f}s"


def _broll_overlay(p: Project, at: float, source=None, dur=None, **_) -> str:
    from . import overlays

    src = p.sources.get(source or "")
    if src is None:
        raise VeditError(f"Unknown source '{source}'. Sources: {', '.join(p.sources) or 'none'}")
    d = min(dur or 2.5, max(0.3, src.duration))
    l = overlays.add_video(p, source, at, round(at + d, 3), x="50%", y="50%", fit="cover", volume=0.0)
    l.presets = [Preset(name="zoom_in", duration=0.25, params={"from": 1.12}), Preset(name="fade_out", duration=0.15)]
    return f"b-roll {source} over {at:.2f}-{at + d:.2f}s ({l.id}, its sound muted)"


# ---- step 2: chat, phone, magazine, zoom portal, screencast / HUD / paper kits ---------------------------------

def _pct(v: str, total: float) -> float:
    v = v.strip()
    return float(v[:-1]) / 100 * total if v.endswith("%") else float(v)


def _measure(text: str, st: TextStyle) -> tuple[int, int]:
    from .text import render_text

    img = render_text(text, st)
    return img.width, img.height


def _chat_takeover(p: Project, at: float, text=None, dur=None, **_) -> str:
    from . import overlays

    W, H = p.canvas.width, p.canvas.height
    msgs, side = [], "them"
    for raw in lines(text):
        who, sep, body = raw.partition(":")
        if sep and who.strip().lower() in ("me", "you", "them", "him", "her", "they", "a", "b"):
            side = "me" if who.strip().lower() in ("me", "b") else "them"
            msgs.append((side, body.strip()))
        else:
            msgs.append((side, raw))
        side = "me" if side == "them" else "them"
    gap, typing = 0.95, 0.55
    times, t = [], at + 0.35
    for who, _ in msgs:
        t += typing if who == "them" and times else 0.0
        times.append(t)
        t += gap
    end = _layer_end(p, at, dur or (times[-1] - at + 2.0))
    dim = overlays.add_shape(p, "rect", at, end, x="50%", y="50%", width=W, height=H, radius=0, fill="#0B0B10E0")
    dim.presets = [Preset(name="fade_in", duration=0.25), Preset(name="fade_out", duration=0.25)]
    size = round(W * 0.045)
    pad, gap_px, bottom = round(size * 0.6), round(size * 0.45), H * 0.7
    layers: list[tuple[Layer, float, float]] = []  # (layer, its time, its height)

    def bubble(txt: str, who: str, t0: float) -> tuple[Layer, float]:
        st = TextStyle(size=size, color="#FFFFFF" if who == "me" else "#111111", bg_color="#2F7BF6" if who == "me" else "#E9E9EE",
                       bg_padding=pad, bg_radius=round(size * 0.9), stroke_width=0, max_width=round(W * 0.6), align="left", font="poppins")
        w, h = _measure(txt, st)
        x = W * 0.92 - w / 2 if who == "me" else W * 0.08 + w / 2
        l = Layer(id=p.new_id("t"), type="text", text=txt, start=round(t0, 3), end=end, x=round(x), y=round(bottom - h / 2), style=st,
                  presets=[Preset(name="pop_in", duration=0.25), Preset(name="fade_out", duration=0.25)])
        p.layers.append(l)
        return l, h

    for (who, body), t0 in zip(msgs, times):
        if who == "them" and t0 - typing > at:  # typing dots first, in the slot the message will take
            dots, dh = bubble("\u2022 \u2022 \u2022", "them", t0 - typing)
            dots.end = round(t0, 3)
            dots.presets = [Preset(name="pop_in", duration=0.2), Preset(name="pulse", params={"amount": 0.08, "period": 0.5})]
        l, hgt = bubble(body, who, t0)
        # everything already on screen moves up to make room
        for prev, pt, _ in layers:
            shift_at = round(t0 - (typing if who == "them" else 0.0) - pt, 3)
            y_now = _y_at(prev, shift_at)
            prev.keyframes += [Keyframe(t=max(0.0, shift_at), y=y_now, ease="smooth"), Keyframe(t=shift_at + 0.25, y=y_now - hgt - gap_px)]
            prev.keyframes.sort(key=lambda k: k.t)
        layers.append((l, t0, hgt))
    return f"chat takeover: {len(msgs)} messages from {at:.2f}s"


def _y_at(l: Layer, t: float) -> float:
    ys = [k for k in l.keyframes if k.y is not None and k.t <= t + 1e-6]
    return float(ys[-1].y) if ys else float(l.y)


def _clip_source_at(p: Project, at: float) -> tuple[str, float]:
    i = _at_clip(p, at)
    c = p.clips[i]
    return c.source, c.in_ + (at - p.clip_starts()[i]) * c.speed


def _phone_mockup(p: Project, at: float, source=None, dur=None, **_) -> str:
    from . import overlays

    W, H = p.canvas.width, p.canvas.height
    if source:
        sid, src_in = source, 0.0
    else:
        sid, src_in = _clip_source_at(p, at)
    src = p.sources[sid]
    d = dur or 3.0
    end = _layer_end(p, at, d)
    pw = round(min(W * 0.56, H * 0.62 * (src.width or 1080) / (src.height or 1920)))
    ph = round(pw * (src.height or 1920) / (src.width or 1080))
    bezel = max(8, round(pw * 0.035))
    back = overlays.add_shape(p, "rect", at, end, x="50%", y="50%", width=W, height=H, radius=0, fill=None)
    overlays.set_fields(back, {"fill_gradient": "#1E1B4B,#0B1020", "gradient_angle": 90})
    back.presets = [Preset(name="fade_in", duration=0.2)]
    shadow = overlays.add_shape(p, "rect", at, end, x=round(W / 2 + pw * 0.03), y=round(H / 2 + pw * 0.06), width=pw + 2 * bezel,
                                height=ph + 2 * bezel, radius=round(pw * 0.14), fill="#00000070")
    phone = overlays.add_video(p, sid, at, end, x="50%", y="50%", width=pw + 2 * bezel, src_in=round(src_in, 3), volume=0.0,
                               radius=round(pw * 0.13), border_width=bezel, border_color="#0E0E10")
    notch = overlays.add_shape(p, "rect", at, end, x="50%", y=round(H / 2 - ph / 2 - bezel + bezel * 2.6), width=round(pw * 0.26),
                               height=round(bezel * 2.2), radius=bezel, fill="#0E0E10")
    for l in (shadow, phone, notch):
        l.presets = [Preset(name="rise_in", duration=0.5, params={"distance": round(H * 0.08)}), Preset(name="float", params={"amplitude": 8, "degrees": 1})]
    return f"phone mockup of {sid} at {at:.2f}s ({phone.id})"


def _magazine_cover(p: Project, at: float, text=None, dur=None, **_) -> str:
    from ..project import Effect
    from . import overlays

    W, H = p.canvas.width, p.canvas.height
    items = lines(text) or ["COVER"]
    d = dur or 3.0
    seg, full = _subject_split(p, at, d, "cover")
    end = _layer_end(p, at, d)
    mast_st = TextStyle(size=round(W * 0.26), color="#FFFFFF", stroke_width=0, uppercase=True, font="abril", max_width=round(W * 0.98),
                        letter_spacing=-4)
    mast = Layer(id=p.new_id("t"), type="text", text=items[0], start=round(at, 3), end=end, x="50%", y=round(H * 0.14), style=mast_st,
                 presets=[Preset(name="wipe_in", duration=0.4)])
    p.layers.append(mast)
    _under(p, mast)
    issue = Layer(id=p.new_id("t"), type="text", text="ISSUE 01 \u00b7 SPECIAL", start=round(at, 3), end=end, x=round(W * 0.27), y=round(H * 0.035),
                  style=TextStyle(size=round(W * 0.026), stroke_width=0, font="space-mono", letter_spacing=4))
    p.layers.append(issue)
    for k, line in enumerate(items[1:5]):
        st = TextStyle(size=round(W * (0.06 if k == 0 else 0.042)), color="#FFE600" if k == 0 else "#FFFFFF", stroke_width=0,
                       uppercase=True, font="anton", shadow=True, align="left", max_width=round(W * 0.5))
        _ = _measure(line, st)
        l = Layer(id=p.new_id("t"), type="text", text=line, start=round(at + 0.25 + 0.15 * k, 3), end=end,
                  x=round(W * 0.07 + _[0] / 2), y=round(H * (0.62 + 0.075 * k)), style=st, presets=[Preset(name="slide_in", duration=0.35,
                                                                                                        params={"direction": "left", "distance": 80})])
        p.layers.append(l)
    border = overlays.add_shape(p, "rect", at, end, x="50%", y="50%", width=W, height=H, radius=0, fill=None,
                                border_width=round(W * 0.018), border_color="#FFFFFF")
    border.presets = [Preset(name="fade_in", duration=0.2)]
    return f"magazine cover '{items[0]}' {at:.2f}-{at + d:.2f}s ({seg.id} keyed: the person stands in front of the masthead)"


def _subject_split(p: Project, at: float, d: float, what: str) -> tuple:
    """The person in front of anything: the shot's piece at..at+d keyed (background removed, `replace none`) on the
    video track, and the full shot again as a video layer on a track UNDER the video. Layers put under the video
    between the two (`_under`) appear behind the person. Returns (keyed clip, full-shot layer)."""
    from ..project import Effect
    from . import overlays

    i0 = _at_clip(p, at)
    c0 = p.clips[i0]
    if p.clip_starts()[i0] + c0.duration < at + d - 0.05:
        raise VeditError(f"Not enough of {c0.id} after {at:.2f}s for a {d:g}s {what}; give a shorter --dur")
    i = cut.split_at(p, at)
    cut.split_at(p, at + d)
    seg = p.clips[i]
    seg.transition = None
    seg.effects = [e for e in seg.effects if e.type != "bg"] + [Effect(type="bg", params={"smooth": 0.5, "mode": "fast", "replace": "none"})]
    full = overlays.add_video(p, seg.source, at, _layer_end(p, at, d), x="50%", y="50%", fit="cover", src_in=seg.in_, volume=0.0)
    full.width = p.canvas.width
    # right under the person, so this effect's tracks under the video sit together (one group row)
    overlays.place_layer(p, full.id, max(0, p.main_z), new_track=True, under_video=True)
    return seg, full


def _under(p: Project, layer: Layer, top: bool = True) -> None:
    """Put a layer on a new track under the video: right under the person (top) or at the very bottom."""
    from . import overlays

    overlays.place_layer(p, layer.id, max(0, p.main_z) if top else 0, new_track=True, under_video=True)


def _zoom_portal(p: Project, at: float, clip=None, amount=None, **_) -> str:
    if clip is None:
        raise VeditError("zoom_portal needs --clip (the shot it opens into)")
    idx = [c.id for c in p.clips].index(p.clip(clip).id)
    if idx == 0:
        raise VeditError(f"zoom_portal goes INTO a clip; {clip} is the first clip")
    prev = p.clips[idx - 1]
    if prev.zoom_to:
        raise VeditError(f"{prev.id} already has a slow zoom")
    ramp = min(0.55, prev.duration * 0.5)
    i = cut.split_at(p, p.clip_starts()[idx - 1] + prev.duration - ramp)
    c = p.clips[i]
    c.zoom, c.zoom_to, c.zoom_ease = max(1.0, prev.zoom), max(1.6, min(4.0, amount or 2.6)), "ease_in"
    cut.set_transition(p, "zoomin", 0.35, clip)
    return f"zoom portal into {clip}: the last {ramp:.2f}s of {prev.id} dives in, then {clip} opens out"


CURSOR = [[0.0, 0.0], [0.0, 0.84], [0.24, 0.64], [0.42, 1.0], [0.6, 0.92], [0.42, 0.58], [0.74, 0.58]]


def _cursor_click(p: Project, at: float, text=None, dur=None, **_) -> str:
    from . import overlays

    W, H = p.canvas.width, p.canvas.height
    spec = (text or "30%,70% > 60%,40%").replace("->", ">")
    try:
        a, b = [[_pct(v, tot) for v, tot in zip(pt.split(","), (W, H))] for pt in spec.split(">")]
    except ValueError:
        raise VeditError("cursor_click --text is 'x1,y1 > x2,y2' (px or %), e.g. '30%,70% > 60%,40%'") from None
    cw, ch = round(W * 0.06), round(W * 0.08)
    end = _layer_end(p, at, dur or 2.2)
    off = (cw / 2, ch / 2)  # the layer's centre is half a cursor right / down of its tip
    ring = overlays.add_shape(p, "ellipse", round(at + 0.85, 3), round(at + 1.45, 3), x=round(b[0]), y=round(b[1]),
                              width=round(W * 0.09), height=round(W * 0.09), fill=None, border_width=max(4, round(W * 0.006)),
                              border_color="#FFFFFF")
    ring.presets = [Preset(name="zoom_in", duration=0.35, params={"from": 0.3}), Preset(name="fade_out", duration=0.3)]
    cur = overlays.add_shape(p, "path", at, end, x=round(a[0] + off[0]), y=round(a[1] + off[1]), width=cw, height=ch, fill="#FFFFFF",
                             border_width=max(3, round(W * 0.004)), border_color="#111111")
    cur.path, cur.closed = CURSOR, True
    cur.keyframes = [Keyframe(t=0, x=round(a[0] + off[0]), y=round(a[1] + off[1]), scale=1, ease="smooth"),
                     Keyframe(t=0.75, x=round(b[0] + off[0]), y=round(b[1] + off[1]), scale=1, ease="ease_out"),
                     Keyframe(t=0.85, scale=0.82, ease="ease_out"), Keyframe(t=1.0, scale=1)]
    cur.presets = [Preset(name="fade_in", duration=0.15), Preset(name="fade_out", duration=0.2)]
    return f"cursor click at {at + 0.85:.2f}s ({cur.id})"


def _hud_lock(p: Project, at: float, text=None, dur=None, box=None, **_) -> str:
    from . import motion, overlays

    W, H = p.canvas.width, p.canvas.height
    try:
        bx, by, bw, bh = [float(v.strip().rstrip("%")) / 100 for v in (box or "30,35,40,25").split(",")]
    except ValueError:
        raise VeditError("hud_lock --box is x,y,w,h in % of the frame, e.g. 30,35,40,25") from None
    x0, y0, x1, y1 = bx * W, by * H, (bx + bw) * W, (by + bh) * H
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    end = _layer_end(p, at, dur or 2.5)
    arm = round(min(x1 - x0, y1 - y0) * 0.28)
    lw = max(4, round(W * 0.006))
    green = "#39FF88"
    corners = [(x0, y0, [[0, 1], [0, 0], [1, 0]]), (x1, y0, [[0, 0], [1, 0], [1, 1]]),
               (x1, y1, [[1, 0], [1, 1], [0, 1]]), (x0, y1, [[0, 0], [0, 1], [1, 1]])]
    for k, (px, py, path) in enumerate(corners):
        sx, sy = (1 if px > cx else -1), (1 if py > cy else -1)
        fx, fy = px - sx * arm / 2, py - sy * arm / 2
        l = overlays.add_shape(p, "path", round(at + 0.04 * k, 3), end, x=round(fx), y=round(fy), width=arm, height=arm,
                               border_width=lw, border_color=green, fill=None)
        l.path = path
        far = 0.35  # start further out, snap in onto the target
        l.keyframes = [Keyframe(t=0, x=round(fx + sx * (x1 - x0) * far), y=round(fy + sy * (y1 - y0) * far), ease="snap"),
                       Keyframe(t=0.35, x=round(fx), y=round(fy))]
        l.presets = [Preset(name="fade_in", duration=0.1), Preset(name="fade_out", duration=0.2)]
    cross = overlays.add_shape(p, "ring", round(at + 0.3, 3), end, x=round(cx), y=round(cy), width=round(arm * 0.6), height=round(arm * 0.6),
                               fill=green, inner=0.82)
    cross.presets = [Preset(name="pop_in", duration=0.2), Preset(name="pulse", params={"amount": 0.12, "period": 0.6})]
    scan = overlays.add_shape(p, "rect", round(at + 0.35, 3), end, x=round(cx), y=round(y0), width=round(x1 - x0), height=max(2, lw // 2),
                              radius=0, fill="#39FF8899")
    scan.keyframes = [Keyframe(t=0, y=round(y0), ease="linear"), Keyframe(t=0.9, y=round(y1), ease="linear")]
    label = (text or "TARGET ACQUIRED").upper()
    st = TextStyle(size=round(W * 0.03), color=green, stroke_width=0, font="space-mono", letter_spacing=3, bg_color="#030A06C0", bg_padding=8)
    w, h = _measure(label, st)
    t = Layer(id=p.new_id("t"), type="text", text=label, start=round(at + 0.4, 3), end=end, x=round(x0 + w / 2), y=round(y0 - h * 0.9), style=st)
    motion.set_fields(t, {"split": "letter", "split_anim": "type", "stagger": 0.03})
    p.layers.append(t)
    return f"HUD lock-on at {at:.2f}s on {round(bx * 100)},{round(by * 100)},{round(bw * 100)},{round(bh * 100)}%"


def _paper_title(p: Project, at: float, text=None, dur=None, **_) -> str:
    from . import overlays

    W, H = p.canvas.width, p.canvas.height
    items = lines(text) or ["Hello"]
    end = _layer_end(p, at, dur or 3.0)
    out = []

    def card(cx: float, cy: float, w: int, h: int, color: str, rot: float, t0: float) -> None:
        sh = overlays.add_shape(p, "rect", t0, end, x=round(cx + W * 0.012), y=round(cy + W * 0.016), width=w, height=h, radius=6,
                                fill="#00000055", rotation=rot)
        pc = overlays.add_shape(p, "rect", t0, end, x=round(cx), y=round(cy), width=w, height=h, radius=6, fill=color, rotation=rot)
        out.extend([sh, pc])

    def words(txt: str, cy: float, size: int, color: str, rot: float, t0: float) -> None:
        l = Layer(id=p.new_id("t"), type="text", text=txt, start=round(t0, 3), end=end, x="50%", y=round(cy), rotation=rot,
                  style=TextStyle(size=size, color=color, stroke_width=0, font="titan", max_width=round(W * 0.74)))
        p.layers.append(l)
        out.append(l)

    tsize = round(W * 0.1)
    tw, th = _measure(items[0], TextStyle(size=tsize, font="titan", stroke_width=0, max_width=round(W * 0.74)))
    card(W / 2, H * 0.42, round(tw + W * 0.1), round(th + W * 0.08), "#F4E3C1", -3, at)
    words(items[0], H * 0.42, tsize, "#3E2723", -3, at)
    if len(items) > 1:
        ssize = round(W * 0.05)
        sw, shh = _measure(items[1], TextStyle(size=ssize, font="titan", stroke_width=0, max_width=round(W * 0.74)))
        cy = H * 0.42 + th / 2 + W * 0.12
        card(W / 2 + W * 0.05, cy, round(sw + W * 0.08), round(shh + W * 0.05), "#E76F51", 4, at + 0.25)
        words(items[1], cy, ssize, "#FFF3E0", 4, at + 0.25)
    for l in out:  # stop motion: drops in, then sways, on twos
        l.step_fps = 12
        l.presets = [Preset(name="drop_in", duration=0.6, params={"distance": round(H * 0.25)}),
                     Preset(name="sway", params={"degrees": 2, "period": 2.2}), Preset(name="fade_out", duration=0.2)]
    return f"paper cut-out title '{items[0]}' at {at:.2f}s"


def _liquid_glass(p: Project, at: float, text=None, dur=None, **_) -> str:
    W, H = p.canvas.width, p.canvas.height
    end = _layer_end(p, at, dur or 3.0)
    lines_ = lines(text) or ["Liquid glass"]
    w, h = round(W * 0.78), round(W * (0.36 if len(lines_) > 1 else 0.3))
    gl = Layer(id=p.new_id("gl"), type="glass", start=round(at, 3), end=end, x="50%", y="50%", width=w, height=h,
               radius=round(W * 0.06), fill="#FFFFFF1C", glass_blur=26)
    p.layers.append(gl)
    st = TextStyle(size=round(W * 0.075), color="#FFFFFF", stroke_width=0, shadow=True, max_width=round(w * 0.86), font="poppins-bold")
    t = Layer(id=p.new_id("t"), type="text", text=lines_[0], start=round(at + 0.15, 3), end=end, x="50%",
              y=round(H / 2 - (h * 0.12 if len(lines_) > 1 else 0)), style=st, presets=[Preset(name="rise_in", duration=0.45), Preset(name="fade_out", duration=0.25)])
    p.layers.append(t)
    if len(lines_) > 1:
        sub = Layer(id=p.new_id("t"), type="text", text=lines_[1], start=round(at + 0.3, 3), end=end, x="50%", y=round(H / 2 + h * 0.17),
                    style=TextStyle(size=round(W * 0.04), color="#FFFFFFD0", stroke_width=0, max_width=round(w * 0.86), font="poppins"),
                    presets=[Preset(name="fade_in", duration=0.4), Preset(name="fade_out", duration=0.25)])
        p.layers.append(sub)
    return f"liquid glass panel at {at:.2f}s ({gl.id})"


def _iso_world(p: Project, at: float, text=None, dur=None, **_) -> str:
    from .iso import world

    kind, _, theme = (text or "city").strip().lower().partition(" ")
    theme = theme.strip() or "pastel"
    W, H = p.canvas.width, p.canvas.height
    l = Layer(id=p.new_id("iso"), type="iso", iso=world(kind, theme), start=round(at, 3), end=_layer_end(p, at, dur or 4.0), x="50%", y="55%",
              width=round(W * (0.5 if kind == "stack" else 0.8)),
              presets=[Preset(name="draw_in", duration=1.8, params={"ease": "linear"}), Preset(name="float", params={"amplitude": 10, "degrees": 0.5}),
                       Preset(name="fade_out", duration=0.3)])
    p.layers.append(l)
    return f"isometric {kind} at {at:.2f}s ({l.id}): builds itself block by block"


def _orbit(p: Project, at: float, text=None, dur=None, amount=None, **_) -> str:
    """Words on a tilted ring around the person: each word is two layers, one under the person (shown while it is
    behind) and one over (while in front), so it really passes behind them."""
    import math

    W, H = p.canvas.width, p.canvas.height
    words = lines(text) or ["orbit"]
    d = dur or 4.0
    seg, full = _subject_split(p, at, d, "orbit")
    end = _layer_end(p, at, d)
    cx, cy, rx, ry = W * 0.5, H * 0.42, W * 0.42, H * 0.06
    period = max(1.5, amount or 3.2)
    step = 1 / 12
    for k, word in enumerate(words):
        st = TextStyle(size=round(W * 0.07), color="#FFFFFF", stroke_width=0, shadow=True, uppercase=True, font="anton")
        front = Layer(id=p.new_id("t"), type="text", text=word, start=round(at, 3), end=end, x=round(cx), y=round(cy), style=st)
        p.layers.append(front)  # before the next new_id: ids come from the layers already in the project
        back = front.model_copy(update={"id": p.new_id("t")})
        p.layers.append(back)
        kf_f, kf_b = [], []
        n = int(d / step) + 1
        for i in range(n):
            t = i * step
            th = 2 * math.pi * (t / period + k / len(words))
            depth = math.sin(th)  # > 0: in front of the person (lower half of the ring)
            x, y = cx + rx * math.cos(th), cy + ry * depth
            s = 0.75 + 0.3 * (depth + 1) / 2
            kf_f.append(Keyframe(t=round(t, 3), x=round(x, 1), y=round(y, 1), scale=round(s, 3), opacity=1.0 if depth >= 0 else 0.0, ease="linear"))
            kf_b.append(Keyframe(t=round(t, 3), x=round(x, 1), y=round(y, 1), scale=round(s, 3), opacity=0.0 if depth >= 0 else 0.7, ease="linear"))
        front.keyframes, back.keyframes = kf_f, kf_b
        _under(p, back)
    return f"orbit of {len(words)} word(s) around the person {at:.2f}-{at + d:.2f}s ({seg.id} keyed)"


def _dolly_zoom(p: Project, at: float, dur=None, amount=None, **_) -> str:
    d = dur or 2.0
    seg, full = _subject_split(p, at, d, "dolly zoom")
    z = max(1.1, min(2.0, amount or 1.4))
    full.keyframes = [Keyframe(t=0, scale=1.0, ease="ease_in_out"), Keyframe(t=round(d, 3), scale=z)]
    from . import fx_filters

    fx_filters.add(p, full, "blur", {"amount": 4})  # a little depth of field hides the edge of the cut-out
    return f"dolly zoom {at:.2f}-{at + d:.2f}s: the background pushes to {z:g}x behind the person ({seg.id})"


def _frame_break(p: Project, at: float, text=None, dur=None, **_) -> str:
    from . import overlays

    W, H = p.canvas.width, p.canvas.height
    d = dur or 3.0
    seg, full = _subject_split(p, at, d, "frame break")
    end = _layer_end(p, at, d)
    x0, x1, y0, y1 = W * 0.09, W * 0.91, H * 0.3, H * 0.86  # the frame; the person steps out over its top
    paper = "#F2EFE9"
    bands = [((W / 2, y0 / 2), (W, y0)), ((W / 2, (y1 + H) / 2), (W, H - y1)), ((x0 / 2, H / 2), (x0, H)), (((x1 + W) / 2, H / 2), (W - x1, H))]
    parts = []
    for (bx, by), (bw, bh) in bands:  # everything outside the frame is a plain backdrop
        parts.append(overlays.add_shape(p, "rect", at, end, x=round(bx), y=round(by), width=max(2, round(bw)), height=max(2, round(bh)), radius=0, fill=paper))
    edge = overlays.add_shape(p, "rect", at, end, x=round(W / 2), y=round((y0 + y1) / 2), width=round(x1 - x0), height=round(y1 - y0),
                              radius=0, fill=None, border_width=max(6, round(W * 0.014)), border_color="#FFFFFF")
    parts.append(edge)
    for l in parts:
        l.presets = [Preset(name="fade_in", duration=0.2)]
        _under(p, l)
    if text:
        cap = Layer(id=p.new_id("t"), type="text", text=lines(text)[0], start=round(at + 0.3, 3), end=end, x="50%", y=round((y1 + H) / 2),
                    style=TextStyle(size=round(W * 0.06), color="#111111", stroke_width=0, font="dm-serif"), presets=[Preset(name="rise_in", duration=0.4)])
        p.layers.append(cap)
    return f"frame break {at:.2f}-{at + d:.2f}s: the person steps out over the frame ({seg.id} keyed)"


def _studio_reveal(p: Project, at: float, dur=None, **_) -> str:
    from . import overlays

    W, H = p.canvas.width, p.canvas.height
    d = dur or 3.0
    seg, full = _subject_split(p, at, d, "studio reveal")
    end = _layer_end(p, at, d)
    # the studio set, at the very bottom: backdrop, a soft key light behind the person, a floor
    back = overlays.add_shape(p, "rect", at, end, x="50%", y="50%", width=W, height=H, radius=0, fill=None)
    overlays.set_fields(back, {"fill_gradient": "#3A3A42,#0E0E12", "gradient_angle": 90})
    glow = overlays.add_shape(p, "ellipse", at, end, x="50%", y=round(H * 0.38), width=round(W * 1.1), height=round(W * 1.1), fill="#FFFFFF14",
                              border_width=0)
    floor = overlays.add_shape(p, "rect", at, end, x="50%", y=round(H * 0.88), width=W, height=round(H * 0.24), radius=0, fill=None)
    overlays.set_fields(floor, {"fill_gradient": "#00000000,#000000AA", "gradient_angle": 90})
    for l in (back, glow, floor):  # under the full shot, bottom to top
        overlays.place_layer(p, l.id, p.layer(full.id).track, new_track=True, under_video=True)
    # the real background wipes away (the full shot only covers the first 0.7 s, then wipes off)
    full.end = round(at + 0.7, 3)
    full.presets = [Preset(name="wipe_out", duration=0.6)]
    return f"studio reveal {at:.2f}-{at + d:.2f}s: the background wipes to a studio set ({seg.id} keyed)"


EFFECTS = {
    "snap_zoom": _snap_zoom, "digital_zoom": _digital_zoom, "growth_curve": _growth_curve,
    "checklist": lambda p, at, **k: _checklist(p, at, pop=False, **k),
    "checklist_pop": lambda p, at, **k: _checklist(p, at, pop=True, **k),
    "headline_storm": _headline_storm, "broll_overlay": _broll_overlay,
    "chat_takeover": _chat_takeover, "phone_mockup": _phone_mockup, "magazine_cover": _magazine_cover, "zoom_portal": _zoom_portal,
    "cursor_click": _cursor_click, "hud_lock": _hud_lock, "paper_title": _paper_title,
    "liquid_glass": _liquid_glass, "iso_world": _iso_world, "orbit": _orbit, "dolly_zoom": _dolly_zoom, "frame_break": _frame_break,
    "studio_reveal": _studio_reveal,
}
# effects whose look is drawn without the user's footage: the Effects tab shows a rendered preview
PREVIEWS = ("growth_curve", "checklist", "checklist_pop", "headline_storm", "chat_takeover", "cursor_click", "hud_lock", "paper_title",
            "iso_world")

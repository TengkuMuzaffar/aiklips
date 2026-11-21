"""Captions: SRT import (no AI) or offline auto-transcription with faster-whisper, turned into text.

Captions are ordinary text layers, one per phrase (a few words), in one group of kind "captions": the timeline shows
them as one "Captions" row that moves together, and each phrase can be edited, moved and animated like any text.
A phrase keeps its words' times (`Layer.words`, seconds from the phrase's start) so the word being spoken can be
highlighted (`Layer.highlight_words`, drawn in style.highlight_color). The group remembers its words per phrase
(`LayerGroup.per_group`); `regroup` rebuilds the phrases with another number."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .. import media
from ..project import Layer, LayerGroup, Preset, Project, TextStyle, VeditError, Word, normalize_tracks

CAPTION_STYLES: dict[str, dict] = {
    "bold": dict(size=84, color="#FFFFFF", stroke_color="#000000", stroke_width=8, highlight_color="#FFE600", uppercase=True, shadow=True),
    "boxed": dict(size=72, color="#FFFFFF", stroke_width=0, bg_color="#000000B0", bg_padding=22, bg_radius=20, highlight_color="#5EEAD4", shadow=False),
    "minimal": dict(size=64, color="#FFFFFF", stroke_width=0, highlight_color="#FFFFFF", shadow=True),
    "neon": dict(size=80, color="#FFFFFF", stroke_color="#7C3AED", stroke_width=7, highlight_color="#22D3EE", uppercase=True, shadow=True),
    "karaoke": dict(size=80, color="#9CA3AF", stroke_color="#000000", stroke_width=6, highlight_color="#FFFFFF", shadow=True),
}

_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def _ts(s: str) -> float:
    m = _TS.search(s)
    if not m:
        raise VeditError(f"Bad SRT timestamp: {s}")
    h, mi, se, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, "0")[:3]) / 1000


def parse_srt(text: str) -> list[Word]:
    """SRT cues -> words, spreading each cue's time over its words by length."""
    words: list[Word] = []
    for block in re.split(r"\n\s*\n", text.strip().replace("\r", "")):
        lines = [l for l in block.split("\n") if l.strip()]
        if len(lines) < 2:
            continue
        ti = 1 if "-->" in lines[1] else 0
        if "-->" not in lines[ti]:
            continue
        a, b = lines[ti].split("-->")
        start, end = _ts(a), _ts(b)
        toks = " ".join(lines[ti + 1 :]).split()
        toks = [re.sub(r"<[^>]+>", "", t) for t in toks]
        toks = [t for t in toks if t]
        if not toks:
            continue
        total = sum(len(t) + 1 for t in toks)
        t = start
        for tok in toks:
            d = (end - start) * (len(tok) + 1) / total
            words.append(Word(text=tok, start=round(t, 3), end=round(t + d, 3)))
            t += d
    if not words:
        raise VeditError("No captions found in SRT file")
    return words


def transcribe(audio_src: Path, model: str = "small", language: str | None = None, install: bool = True) -> list[Word]:
    """`install=False` (background studies): raise ImportError instead of installing the extra, which takes minutes
    with no progress the user can see."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        from .. import updater

        if not install:
            raise
        updater.install_extra("captions")  # installed app or git clone: fetch it now (one time)
        from faster_whisper import WhisperModel
    wav = audio_src.with_suffix(".captions.wav")
    media.run([media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(audio_src), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    wm = WhisperModel(model, device="auto", compute_type="int8")
    segments, _ = wm.transcribe(str(wav), language=language, word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        for w in seg.words or []:
            txt = w.word.strip()
            if txt:
                words.append(Word(text=txt, start=round(w.start, 3), end=round(w.end, 3)))
    wav.unlink(missing_ok=True)
    if not words:
        raise VeditError("No speech detected")
    return words


def style_for(name: str) -> TextStyle:
    if name not in CAPTION_STYLES:
        raise VeditError(f"Unknown caption style '{name}'. Options: {', '.join(CAPTION_STYLES)}")
    return TextStyle(max_width=900, **CAPTION_STYLES[name])


def _pop() -> Preset:
    """The phrase entrance: a quick pop from 86 % size (what captions always did)."""
    return Preset(name="pop_in", duration=0.15, params={"from": 0.86})


# ---- phrases -------------------------------------------------------------------------------------------------

def phrase_groups(words: list[Word], per_group: int) -> list[list[int]]:
    """Word indexes per phrase: a new phrase after `per_group` words, a pause over 0.5 s, or a sentence end."""
    groups: list[list[int]] = []
    cur: list[int] = []
    for i, w in enumerate(words):
        if cur:
            prev = words[cur[-1]]
            if len(cur) >= max(1, per_group) or w.start - prev.end > 0.5 or prev.text.rstrip()[-1:] in ".?!":
                groups.append(cur)
                cur = []
        cur.append(i)
    if cur:
        groups.append(cur)
    return groups


def word_at(layer: Layer, t: float) -> int:
    """The word being spoken `t` seconds into the phrase (the last one that has started; 0 before the first)."""
    hi = 0
    for j, w in enumerate(layer.words):
        if w.start <= t + 1e-9:
            hi = j
    return hi


def abs_words(layer: Layer) -> list[Word]:
    return [Word(text=w.text, start=round(layer.start + w.start, 3), end=round(layer.start + w.end, 3)) for w in layer.words]


def _phrases(project: Project, words: list[Word], style: TextStyle, per_group: int, x, y, highlight: bool,
             presets: list[Preset] | None, track: int | None, gid: str | None) -> list[Layer]:
    """Text layers for `words` (timeline seconds), one per phrase, appended to the project."""
    groups = phrase_groups(words, per_group)
    out: list[Layer] = []
    for gi, g in enumerate(groups):
        ws = [words[i] for i in g]
        start = ws[0].start
        end = words[groups[gi + 1][0]].start if gi + 1 < len(groups) else min(project.duration, ws[-1].end + 0.3)
        end = max(end, start + 0.05)
        l = Layer(id=project.new_id("cap"), type="text", text=" ".join(w.text for w in ws), start=round(start, 3),
                  end=round(end, 3), x=x, y=y, style=style.model_copy(deep=True), highlight_words=highlight,
                  words=[Word(text=w.text, start=round(w.start - start, 3), end=round(w.end - start, 3)) for w in ws],
                  presets=[p.model_copy(deep=True) for p in (presets if presets is not None else [_pop()])],
                  track=track, group=gid)
        project.layers.append(l)
        out.append(l)
    return out


def add_captions(project: Project, words: list[Word], style: str = "bold", y: str = "72%", per_group: int = 3) -> LayerGroup:
    """Captions for `words` (timeline seconds): one text layer per phrase, grouped as "Captions"."""
    from . import groups

    words = [w for w in words if w.start < project.duration]
    if not words:
        raise VeditError("All caption words are after the end of the timeline")
    ls = _phrases(project, words, style_for(style), per_group, "50%", y, True, None, None, None)
    g = groups.make(project, [l.id for l in ls], name="Captions", collapsed=False, min_layers=1)
    g.kind, g.per_group = "captions", max(1, per_group)
    return g


def captions_group(project: Project, gid: str | None = None) -> LayerGroup:
    """A captions group by id, or the only / first one."""
    from . import groups

    if gid:
        g = groups.get(project, gid)
        if g.kind != "captions":
            raise VeditError(f"Group {gid} ({g.name}) isn't captions")
        return g
    g = next((g for g in project.groups if g.kind == "captions"), None)
    if g is None:
        raise VeditError("This project has no captions. Add them with `vedit captions auto` (or `captions srt FILE`).")
    return g


def phrases(project: Project, gid: str) -> list[Layer]:
    return sorted((l for l in project.layers if l.group == gid), key=lambda l: l.start)


def regroup(project: Project, gid: str, per_group: int) -> list[Layer]:
    """Rebuild a captions group's phrases with `per_group` words each (style, position, entrance and track of the
    first phrase; per-phrase hand edits are replaced)."""
    g = captions_group(project, gid)
    old = phrases(project, g.id)
    if not old:
        raise VeditError(f"Captions {g.id} have no phrases")
    words = [w for l in old for w in abs_words(l)]
    first = old[0]
    ids = {l.id for l in old}
    project.layers = [l for l in project.layers if l.id not in ids]
    new = _phrases(project, words, first.style, per_group, first.x, first.y, first.highlight_words, first.presets,
                   first.track, g.id)
    g.per_group = max(1, per_group)
    normalize_tracks(project)
    return new


def style_group(project: Project, gid: str | None, preset: str) -> LayerGroup:
    """One of CAPTION_STYLES on every phrase of a captions group."""
    g = captions_group(project, gid)
    st = style_for(preset)
    for l in phrases(project, g.id):
        l.style = st.model_copy(deep=True)
    return g


def retext(layer: Layer, text: str) -> None:
    """New text for a phrase: the same number of words keeps their times; otherwise the phrase's time is spread over
    the new words by length (as an SRT cue is)."""
    layer.text = text
    if not layer.words:
        return
    toks = text.split()
    if len(toks) == len(layer.words):
        for w, t in zip(layer.words, toks):
            w.text = t
        return
    if not toks:
        layer.words = []
        return
    a, b = layer.words[0].start, max(layer.words[-1].end, layer.words[0].start + 0.05)
    total = sum(len(t) + 1 for t in toks)
    out, t = [], a
    for tok in toks:
        d = (b - a) * (len(tok) + 1) / total
        out.append(Word(text=tok, start=round(t, 3), end=round(t + d, 3)))
        t += d
    layer.words = out


def is_caption(project: Project, layer: Layer) -> bool:
    """A caption phrase: a text layer in a captions group (or any text with word times)."""
    if layer.type != "text":
        return False
    if layer.words:
        return True
    return bool(layer.group) and any(g.id == layer.group and g.kind == "captions" for g in project.groups)


# ---- project format 5 -> 6: the old "captions" layer type becomes phrases ------------------------------------

def migrate_v5(d: dict) -> dict:
    """project.json dict, version 5 -> 6 (before validation): every "captions" layer becomes text phrases in a
    captions group; `words_per_group` leaves every layer (it lives on the group now)."""
    layers = d.get("layers") or []
    groups = d.setdefault("groups", [])
    used = {l.get("id") for l in layers} | {c.get("id") for c in d.get("clips") or []}
    gids = {g.get("id") for g in groups}
    out = []
    for l in layers:
        per = int(l.pop("words_per_group", 3) or 3)
        if l.get("type") != "captions":
            out.append(l)
            continue
        words = [Word(**w) for w in l.get("words") or []]
        if not words:
            continue  # an empty captions layer drew nothing
        gid = next(f"g{n}" for n in range(1, len(gids) + 2) if f"g{n}" not in gids)
        gids.add(gid)
        groups.append({"id": gid, "name": "Captions", "collapsed": False, "kind": "captions", "per_group": per})
        base = {k: v for k, v in l.items() if k not in ("id", "type", "words", "start", "end", "text", "keyframes",
                                                         "presets", "group", "split")}
        grs = phrase_groups(words, per)
        for gi, g in enumerate(grs):
            ws = [words[i] for i in g]
            start = ws[0].start
            end = words[grs[gi + 1][0]].start if gi + 1 < len(grs) else (l.get("end") if l.get("end") is not None else ws[-1].end + 0.3)
            n = 1
            while f"{l['id']}_{n}" in used:
                n += 1
            lid = f"{l['id']}_{n}"
            used.add(lid)
            out.append({**base, "id": lid, "type": "text", "text": " ".join(w.text for w in ws), "start": round(start, 3),
                        "end": round(max(end, start + 0.05), 3), "group": gid, "highlight_words": True,
                        "words": [{"text": w.text, "start": round(w.start - start, 3), "end": round(w.end - start, 3)} for w in ws],
                        "presets": [_pop().model_dump()], "keyframes": []})
    d["layers"] = out
    return d

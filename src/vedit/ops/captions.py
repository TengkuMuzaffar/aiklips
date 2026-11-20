"""Captions: SRT import (no AI) or offline auto-transcription with faster-whisper."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .. import media
from ..project import Layer, Project, TextStyle, VeditError, Word

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
        if not updater.enabled():
            raise VeditError("Auto-captions need the 'captions' extra. Run: uv sync --extra captions") from None
        updater.install_extra("captions")
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


def add_captions(project: Project, words: list[Word], style: str = "bold", y: str = "72%", per_group: int = 3) -> Layer:
    words = [w for w in words if w.start < project.duration]
    if not words:
        raise VeditError("All caption words are after the end of the timeline")
    layer = Layer(
        id=project.new_id("cap"), type="captions", start=words[0].start, end=round(min(project.duration, words[-1].end + 0.3), 3),
        y=y, words=words, style=style_for(style), words_per_group=per_group,
    )
    project.layers.append(layer)
    return layer

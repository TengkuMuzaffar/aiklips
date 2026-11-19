"""Music loops made from scratch: a small sequencer + synth, no model, no downloads, no licence questions.

A loop is a few numbers (`MusicParams`), the way a producer would sketch one:
- **harmony**: key + scale, a chord progression in scale degrees (1-6-4-5), how many bars each chord lasts, sevenths;
- **drums**: one-bar step patterns (16 steps = 16th notes) for kick / snare / clap / hat / open hat / perc, played on
  a synthesized kit (808, punchy, boom bap, lo-fi, chip, soft), with swing, humanising and fills;
- **bass**: follows the chords (held roots, eighths, house offbeats, an 808 that follows the kick, a walking line);
- **chords**: pad, stab, pluck, electric-piano keys or an arpeggio;
- **lead**: a generated melody (motifs that repeat and answer each other, chord tones on the strong beats), an
  arpeggio, or sparse bells;
- **finishing**: sidechain pump, room reverb, lo-fi (wow, crush, dark), vinyl crackle, brightness, levels.

Everything is rendered circularly: note tails and the reverb that run past the end fold back onto the start, so the
WAV is exactly `bars` long and loops without a seam. Styles are hand-tuned starting points; `seed` picks another
melody and feel; the AI writes a loop by choosing the numbers.

    wav = render(style("lofi", bpm=80))   # bytes of a 44.1 kHz 16-bit stereo WAV that loops seamlessly
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import wave
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SR = 44100
MAX_SECONDS = 64.0
STEPS = 16  # steps per bar (16th notes, 4/4)

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_ENHARMONIC = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#", "CB": "B", "FB": "E", "E#": "F", "B#": "C"}
SCALES = {
    "major": (0, 2, 4, 5, 7, 9, 11), "minor": (0, 2, 3, 5, 7, 8, 10), "dorian": (0, 2, 3, 5, 7, 9, 10),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10), "lydian": (0, 2, 4, 6, 7, 9, 11), "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11), "pentatonic_major": (0, 2, 4, 7, 9), "pentatonic_minor": (0, 3, 5, 7, 10),
}
_PARENT = {"pentatonic_major": "major", "pentatonic_minor": "minor"}  # chords of a pentatonic loop come from its parent

Scale = Literal["major", "minor", "dorian", "mixolydian", "lydian", "phrygian", "harmonic_minor", "pentatonic_major", "pentatonic_minor"]
Wave = Literal["sine", "triangle", "square", "pulse", "saw", "organ", "epiano", "bell"]
Kit = Literal["808", "punchy", "boombap", "lofi", "chip", "soft"]
BassStyle = Literal["none", "root", "eighths", "offbeat", "kick", "walk"]
ChordStyle = Literal["none", "pad", "stab", "pluck", "keys", "arp"]
LeadStyle = Literal["none", "melody", "arp", "bells"]
_PATTERN_CHARS = set("xXo.-1234")
DRUMS = ("kick", "snare", "clap", "hat", "open_hat", "perc")


class MusicParams(BaseModel):
    """One music loop. Patterns: one character per 16th step ('x' accent, 'o' hit, '.' rest, '2'-'4' a roll of that
    many hits in the step); 16 characters = one bar, longer patterns span several bars, and they repeat."""

    model_config = ConfigDict(extra="ignore")
    name: str = Field("loop", description="Short name, e.g. 'rainy lofi'")
    style: str = Field("custom", description="The style it started from (see `music styles`)")
    # harmony + time
    bpm: float = Field(90.0, ge=50, le=200, description="Tempo in beats per minute")
    key: str = Field("A", description="Root note: C C# D D# E F F# G G# A A# B (flats are fine: Bb, Eb)")
    scale: Scale = Field("minor", description="major (bright, happy), minor (sad, serious), dorian (lofi, soulful), mixolydian (bluesy), lydian (dreamy), phrygian (dark, tense), harmonic_minor (exotic, dramatic), pentatonic_major / pentatonic_minor (simple, can't go wrong)")
    bars: int = Field(8, ge=1, le=32, description="Loop length in bars (4 beats each)")
    chords: list[int] = Field(default_factory=lambda: [1, 6, 3, 7], min_length=1, max_length=16, description="Progression in scale degrees 1-7, e.g. [1, 5, 6, 4]; it repeats to fill the loop")
    chord_bars: float = Field(1.0, ge=0.25, le=8, description="Bars each chord lasts (0.5 = two chords per bar)")
    sevenths: bool = Field(False, description="Add the 7th to every chord (jazzier, lofi, neo-soul)")
    swing: float = Field(0.0, ge=0, le=0.6, description="Delays the off 16ths: 0 straight, 0.15-0.3 laid-back groove")
    # drums
    kit: Kit = Field("punchy", description="808 (trap, long boomy kick), punchy (pop / EDM), boombap (dusty hip hop), lofi (soft, dark), chip (8-bit), soft (gentle)")
    kick: str = Field("x.......x.......", description="Kick pattern")
    snare: str = Field("....x.......x...", description="Snare pattern")
    clap: str = Field("", description="Clap pattern")
    hat: str = Field("o.o.o.o.o.o.o.o.", description="Closed hi-hat pattern")
    open_hat: str = Field("", description="Open hi-hat pattern")
    perc: str = Field("", description="Shaker / rim pattern")
    fill: bool = Field(True, description="A snare fill at the end of every 4th bar (and the loop's last bar)")
    humanize: float = Field(0.15, ge=0, le=1, description="Loose timing and velocity (0 = machine-tight)")
    # bass
    bass: BassStyle = Field("root", description="root (held chord roots), eighths (driving), offbeat (house), kick (808: a note on every kick), walk (walking line), none")
    bass_wave: Wave = Field("saw", description="Bass sound source")
    bass_octave: int = Field(2, ge=1, le=3, description="Bass octave (1 = sub, 2 = normal, 3 = high)")
    # chords
    chord_style: ChordStyle = Field("pad", description="pad (held, soft), stab (short hits), pluck (short, bright), keys (electric piano), arp (broken chords in 16ths), none")
    chord_wave: Wave = Field("saw", description="Chord sound source")
    chord_octave: int = Field(4, ge=2, le=5, description="Where the chords sit (4 = around middle C)")
    chord_rhythm: str = Field("", description="Step pattern for stab / pluck / keys chords; empty = the style's default")
    # lead
    lead: LeadStyle = Field("melody", description="melody (generated tune), arp (fast broken chords), bells (sparse high notes), none")
    lead_wave: Wave = Field("triangle", description="Lead sound source")
    lead_octave: int = Field(5, ge=3, le=6, description="Lead octave (5 = above middle C)")
    lead_density: float = Field(0.5, ge=0.05, le=1, description="How busy the lead is: 0.2 sparse, 0.5 normal, 0.9 busy")
    # tone + finishing
    brightness: float = Field(0.5, ge=0, le=1, description="Filter brightness of the synths: 0 dark / muffled, 1 bright")
    detune: float = Field(0.3, ge=0, le=1, description="Width / chorus of chords and lead (two detuned voices)")
    pump: float = Field(0.0, ge=0, le=1, description="Sidechain pump: bass, chords and lead duck on every kick (house, EDM)")
    lofi: float = Field(0.0, ge=0, le=1, description="Tape wow, bit crush, dark top end")
    vinyl: float = Field(0.0, ge=0, le=1, description="Vinyl crackle and hiss")
    room: float = Field(0.3, ge=0, le=1, description="Reverb amount on chords / lead (a little on drums)")
    room_size: float = Field(1.2, ge=0.2, le=4, description="Reverb length (s)")
    drums_level: float = Field(1.0, ge=0, le=1.5, description="Drums level (0 = no drums)")
    bass_level: float = Field(1.0, ge=0, le=1.5, description="Bass level")
    chords_level: float = Field(1.0, ge=0, le=1.5, description="Chords level")
    lead_level: float = Field(1.0, ge=0, le=1.5, description="Lead level")
    volume: float = Field(0.9, ge=0.05, le=1, description="Peak loudness of the file")
    seed: int = Field(7, ge=0, description="Randomness: another seed = another melody and feel over the same chords")

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        return NOTE_NAMES[key_pc(v)]

    @field_validator("chords")
    @classmethod
    def _chords(cls, v: list[int]) -> list[int]:
        if any(not 1 <= int(d) <= 7 for d in v):
            raise ValueError("chords are scale degrees 1-7")
        return [int(d) for d in v]

    @field_validator("kick", "snare", "clap", "hat", "open_hat", "perc", "chord_rhythm")
    @classmethod
    def _pattern(cls, v: str) -> str:
        v = "".join(ch for ch in (v or "") if ch not in " |_")
        bad = set(v) - _PATTERN_CHARS
        if bad:
            raise ValueError(f"pattern characters are x o . 2 3 4 (got {''.join(sorted(bad))!r})")
        if len(v) > STEPS * 8:
            raise ValueError("a pattern is at most 8 bars (128 steps)")
        return v

    @model_validator(mode="after")
    def _length(self):
        if self.bars * 240.0 / self.bpm > MAX_SECONDS + 1e-6:
            raise ValueError(f"{self.bars} bars at {self.bpm:g} bpm is {self.bars * 240 / self.bpm:.0f}s; a loop is at most {MAX_SECONDS:.0f}s")
        return self


# ---- styles: hand-tuned starting points ---------------------------------------------------------------------------

STYLES: dict[str, dict] = {
    "lofi": {"about": "Lo-fi hip hop: dusty swung drums, jazzy electric piano, vinyl crackle. Study / vlog / chill.",
             "bpm": 80, "key": "F", "scale": "major", "chords": [2, 5, 1, 6], "sevenths": True, "swing": 0.28, "kit": "lofi",
             "kick": "x.....x...x.....", "snare": "....x.......x...", "hat": "o.o.o.o.o.o.o.oo", "fill": False,
             "humanize": 0.4, "bass": "root", "bass_wave": "sine", "chord_style": "keys", "chord_wave": "epiano",
             "chord_rhythm": "x.......x.....o.", "lead": "melody", "lead_wave": "sine", "lead_density": 0.3,
             "brightness": 0.3, "detune": 0.1, "lofi": 0.55, "vinyl": 0.45, "room": 0.3},
    "hiphop": {"about": "Boom bap hip hop: hard dusty kick and snare, deep bass, keys. Street, confident, storytelling.",
               "bpm": 90, "key": "C", "scale": "minor", "chords": [1, 4, 6, 5], "chord_bars": 2, "sevenths": True,
               "swing": 0.18, "kit": "boombap", "kick": "x.....x...x.x...", "snare": "....x.......x...",
               "hat": "o.o.o.o.o.o.o.o.", "humanize": 0.3, "bass": "root", "bass_wave": "triangle",
               "chord_style": "keys", "chord_wave": "epiano", "chord_rhythm": "x.....x.........", "lead": "bells",
               "lead_wave": "bell", "lead_density": 0.35, "brightness": 0.35, "detune": 0.1, "lofi": 0.2, "vinyl": 0.15, "room": 0.25},
    "trap": {"about": "Trap: booming 808 bass on the kick, half-time clap, rolling hi-hats, dark bells. Hype, flex, edits.",
             "bpm": 140, "key": "F#", "scale": "minor", "chords": [1, 6, 7, 6], "chord_bars": 2, "kit": "808",
             "kick": "x......x..x.....x.........x.....", "snare": "", "clap": "........x...............x.......",
             "hat": "o.o.o.o.o.o3o.o.o.o.o.o.o.o.4.3.", "open_hat": "", "fill": False, "humanize": 0.05,
             "bass": "kick", "bass_wave": "sine", "bass_octave": 1, "chord_style": "pad", "chord_wave": "saw",
             "chord_octave": 4, "lead": "bells", "lead_wave": "bell", "lead_octave": 5, "lead_density": 0.45,
             "brightness": 0.3, "detune": 0.4, "room": 0.35, "chords_level": 0.6},
    "house": {"about": "House / EDM: four-on-the-floor kick, offbeat open hats, offbeat bass, pumping stabs. Fitness, fashion, party.",
              "bpm": 124, "key": "A", "scale": "minor", "chords": [1, 1, 6, 7], "kit": "punchy",
              "kick": "x...x...x...x...", "snare": "", "clap": "....x.......x...", "hat": "", "open_hat": "..o...o...o...o.",
              "perc": "o.oxo.oxo.oxo.ox", "humanize": 0.05, "bass": "offbeat", "bass_wave": "saw",
              "chord_style": "stab", "chord_wave": "saw", "chord_rhythm": "..x..x....x..x..", "lead": "none",
              "brightness": 0.6, "detune": 0.5, "pump": 0.65, "room": 0.3},
    "synthwave": {"about": "Synthwave / 80s retro: driving eighth-note bass, wide detuned pads, a saw lead, big reverb. Night drive, tech, gaming.",
                  "bpm": 100, "key": "E", "scale": "minor", "chords": [1, 6, 3, 7], "kit": "punchy",
                  "kick": "x.......x.......", "snare": "....x.......x...", "hat": "o.o.o.o.o.o.o.o.",
                  "bass": "eighths", "bass_wave": "saw", "chord_style": "pad", "chord_wave": "saw", "lead": "melody",
                  "lead_wave": "saw", "lead_density": 0.45, "brightness": 0.55, "detune": 0.7, "pump": 0.3,
                  "room": 0.55, "room_size": 2.2},
    "chiptune": {"about": "Chiptune / 8-bit: square-wave melody, fast arpeggios, triangle bass, crunchy drums. Games, playful, retro.",
                 "bpm": 150, "key": "C", "scale": "major", "chords": [1, 5, 6, 4], "kit": "chip",
                 "kick": "x.......x.x.....", "snare": "....x.......x...", "hat": "o.o.o.o.o.o.o.o.", "humanize": 0,
                 "bass": "eighths", "bass_wave": "triangle", "bass_octave": 2, "chord_style": "arp",
                 "chord_wave": "pulse", "chord_octave": 4, "lead": "melody", "lead_wave": "square", "lead_density": 0.6,
                 "brightness": 0.9, "detune": 0, "room": 0.08, "chords_level": 0.5},
    "pop": {"about": "Upbeat pop / corporate: bouncy plucks, steady drums, a friendly melody. Product demos, explainers, ads.",
            "bpm": 112, "key": "G", "scale": "major", "chords": [1, 5, 6, 4], "kit": "punchy",
            "kick": "x.......x.x.....", "snare": "....x.......x...", "clap": "....x.......x...", "hat": "o.o.o.o.o.o.o.o.",
            "bass": "eighths", "bass_wave": "square", "chord_style": "pluck", "chord_wave": "saw",
            "chord_rhythm": "x..x..x...x..x..", "lead": "melody", "lead_wave": "triangle", "lead_density": 0.45,
            "brightness": 0.6, "detune": 0.35, "pump": 0.15, "room": 0.3},
    "ambient": {"about": "Ambient: no drums, slow warm pads, distant bells, a long reverb. Calm, meditation, nature, emotional.",
                "bpm": 70, "key": "D", "scale": "lydian", "chords": [1, 2, 6, 5], "chord_bars": 2, "kick": "", "snare": "",
                "hat": "", "fill": False, "bass": "root", "bass_wave": "sine", "chord_style": "pad", "chord_wave": "triangle",
                "lead": "bells", "lead_wave": "bell", "lead_density": 0.2, "brightness": 0.35, "detune": 0.6,
                "room": 0.75, "room_size": 3.5},
    "cinematic": {"about": "Cinematic: big slow drums, wide string-like pads, a deep bass. Trailers, reveals, epic, dramatic.",
                  "bpm": 90, "key": "D", "scale": "harmonic_minor", "chords": [1, 6, 4, 5], "chord_bars": 2, "kit": "punchy",
                  "kick": "x.......x..x....", "snare": "........x.......", "hat": "", "perc": "..o...o...o...o.",
                  "bass": "root", "bass_wave": "saw", "bass_octave": 1, "chord_style": "pad", "chord_wave": "saw",
                  "lead": "melody", "lead_wave": "triangle", "lead_density": 0.25, "lead_octave": 5,
                  "brightness": 0.4, "detune": 0.8, "room": 0.65, "room_size": 2.8},
}
ALIASES = {"lo-fi": "lofi", "lo fi": "lofi", "chill": "lofi", "hip hop": "hiphop", "hip-hop": "hiphop", "boom bap": "hiphop",
           "boombap": "hiphop", "edm": "house", "dance": "house", "techno": "house", "8bit": "chiptune", "8-bit": "chiptune",
           "chip": "chiptune", "retro game": "chiptune", "game": "chiptune", "retrowave": "synthwave", "80s": "synthwave",
           "outrun": "synthwave", "corporate": "pop", "upbeat": "pop", "happy": "pop", "calm": "ambient",
           "meditation": "ambient", "relax": "ambient", "epic": "cinematic", "trailer": "cinematic", "drama": "cinematic"}
MOODS = {
    "happy": {"scale": "major"}, "bright": {"scale": "major", "brightness": +0.2},
    "sad": {"scale": "minor", "bpm": 0.88, "brightness": -0.15}, "dark": {"scale": "phrygian", "brightness": -0.25},
    "tense": {"scale": "harmonic_minor"}, "dreamy": {"scale": "lydian", "room": +0.2},
    "chill": {"bpm": 0.9, "brightness": -0.15, "lead_density": -0.15}, "energetic": {"bpm": 1.12, "lead_density": +0.15},
    "epic": {"room": +0.2, "detune": +0.2}, "soulful": {"scale": "dorian", "sevenths": True},
}


def _style_key(name: str) -> str:
    k = (name or "").strip().lower().replace("_", " ")
    k = ALIASES.get(k, k).replace(" ", "")
    if k not in STYLES:
        raise ValueError(f"No music style '{name}'. Styles: {', '.join(STYLES)}")
    return k


def styles() -> list[dict]:
    return [{"key": k, "about": v["about"], "bpm": v["bpm"]} for k, v in STYLES.items()]


def style(name: str, **over) -> MusicParams:
    """A style's settings with `over` on top (validated)."""
    k = _style_key(name)
    base = {x: y for x, y in STYLES[k].items() if x != "about"}
    return MusicParams(**{"name": k, "style": k, **base, **over})


def apply_mood(p: MusicParams, mood: str) -> MusicParams:
    """Nudge a loop toward a mood: scale changes are set, bpm is a factor, other numbers move by the given amount."""
    m = MOODS.get((mood or "").strip().lower())
    if m is None:
        raise ValueError(f"No mood '{mood}'. Moods: {', '.join(MOODS)}")
    d = p.model_dump()
    for k, v in m.items():
        if k == "bpm":
            d[k] = max(50.0, min(200.0, round(d[k] * v)))
        elif isinstance(v, (bool, str)):
            d[k] = v
        else:
            d[k] = max(0.0, min(1.0, d[k] + v))
    return fit_length(MusicParams(**d))


def fit_length(p: MusicParams) -> MusicParams:
    """Fewer bars if a faster / slower tempo pushed the loop past the maximum length."""
    bar = 240.0 / p.bpm
    if p.bars * bar <= MAX_SECONDS:
        return p
    return p.model_copy(update={"bars": max(1, int(MAX_SECONDS // bar))})


# ---- notes, keys, progressions ------------------------------------------------------------------------------------

def key_pc(text: str) -> int:
    t = (text or "").strip().upper()
    t = _ENHARMONIC.get(t, t)
    if t not in NOTE_NAMES:
        raise ValueError(f"Unknown key '{text}' (use C, C#, D, Eb, ... B)")
    return NOTE_NAMES.index(t)


def parse_key(text: str) -> tuple[str, str | None]:
    """'Am' -> ('A', 'minor'), 'F# minor' -> ('F#', 'minor'), 'Eb' -> ('D#', None), 'C major' -> ('C', 'major')."""
    t = (text or "").strip()
    low = t.lower().replace("-", " ")
    for word, sc in (("minor", "minor"), ("min", "minor"), ("major", "major"), ("maj", "major")):
        if low.endswith(" " + word) or (low.endswith(word) and len(low) > len(word) and low[: -len(word)][-1:] in "abcdefg#b"):
            return NOTE_NAMES[key_pc(t[: len(t) - len(word)].strip())], sc
    if len(t) >= 2 and t.endswith("m"):
        return NOTE_NAMES[key_pc(t[:-1])], "minor"
    return NOTE_NAMES[key_pc(t)], None


_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7}


def parse_chords(text: str) -> list[int]:
    """'1,6,4,5' / '1-6-4-5' / 'i VI III VII' / 'vi IV I V' -> scale degrees."""
    out = []
    for tok in text.replace(",", " ").replace("-", " ").split():
        tok = tok.strip().rstrip("7°o").lower()
        if tok.isdigit():
            out.append(int(tok))
        elif tok in _ROMAN:
            out.append(_ROMAN[tok])
        else:
            raise ValueError(f"Can't read chord '{tok}': use scale degrees 1-7 (1,6,4,5) or roman numerals (i VI III VII)")
    if not out:
        raise ValueError("No chords given")
    return out


def bars_for(seconds: float, bpm: float) -> int:
    """The musical loop length (1, 2, 4, 8, 12, 16, 24 or 32 bars) closest to `seconds` that fits the maximum."""
    bar = 240.0 / bpm
    ok = [b for b in (1, 2, 4, 8, 12, 16, 24, 32) if b * bar <= MAX_SECONDS + 1e-6] or [1]
    return min(ok, key=lambda b: (abs(b * bar - seconds), b))


def loop_seconds(p: MusicParams) -> float:
    return p.bars * 240.0 / p.bpm


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _deg(scale: tuple, root: int, i: int) -> int:
    """MIDI note of 0-based scale index `i` (any integer: negative = below, >= len = octaves up)."""
    o, j = divmod(i, len(scale))
    return root + 12 * o + scale[j]


def _chord_pcs(p: MusicParams, degree: int) -> list[int]:
    """Pitch classes (root first) of the chord built on a scale degree from stacked thirds."""
    sc = SCALES[_PARENT.get(p.scale, p.scale)]
    pc = key_pc(p.key)
    idx = [degree - 1, degree + 1, degree + 3] + ([degree + 5] if p.sevenths else [])
    return [(_deg(sc, pc, i)) % 12 for i in idx]


def _voicing(pcs: list[int], center: int) -> list[int]:
    """Each chord tone in the octave nearest `center` (a close, smooth voicing that never jumps far)."""
    out = []
    for pc in pcs:
        m = center - 6 + ((pc - (center - 6)) % 12)
        out.append(m)
    return sorted(out)


def _progression(p: MusicParams) -> list[tuple[int, int, list[int]]]:
    """[(start step, length in steps, chord pitch classes)] filling the loop."""
    total = p.bars * STEPS
    each = max(1, round(p.chord_bars * STEPS))
    out, s, i = [], 0, 0
    while s < total:
        n = min(each, total - s)
        out.append((s, n, _chord_pcs(p, p.chords[i % len(p.chords)])))
        s += n
        i += 1
    return out


def _chord_at(prog, step: int) -> list[int]:
    for s, n, pcs in prog:
        if s <= step < s + n:
            return pcs
    return prog[-1][2]


def _pattern_hits(pat: str, total_steps: int) -> list[tuple[float, float]]:
    """[(step position, velocity)] of a pattern repeated over the loop; rolls put n hits in one step."""
    if not pat:
        return []
    out = []
    for s in range(total_steps):
        ch = pat[s % len(pat)]
        if ch in ".-":
            continue
        if ch in "234":
            n = int(ch)
            out += [(s + k / n, 0.55 + 0.1 * k) for k in range(n)]
        else:
            out.append((float(s), 1.0 if ch == "x" else 0.72 if ch == "o" else 0.95))
    return out


# ---- the synth ----------------------------------------------------------------------------------------------------

def _partials(w: str) -> list[tuple[float, float, float | None]]:
    """(frequency ratio, amplitude, own decay in seconds or None) of each partial of a wave."""
    if w == "sine":
        return [(1, 1, None)]
    if w == "triangle":
        return [(h, (1 if (h // 2) % 2 == 0 else -1) / h ** 2, None) for h in range(1, 32, 2)]
    if w == "square":
        return [(h, 1 / h, None) for h in range(1, 48, 2)]
    if w == "pulse":  # 25 % duty: the classic NES lead
        return [(h, math.sin(math.pi * h * 0.25) / h, None) for h in range(1, 48) if abs(math.sin(math.pi * h * 0.25)) > 1e-6]
    if w == "saw":
        return [(h, 1 / h, None) for h in range(1, 48)]
    if w == "organ":
        return [(1, 1, None), (2, 0.5, None), (3, 0.35, None), (4, 0.2, None), (6, 0.12, None), (8, 0.1, None)]
    if w == "epiano":  # a soft fundamental and a bright tine that dies fast
        return [(1, 1, None), (2, 0.28, 0.8), (3, 0.09, 0.35), (4, 0.05, 0.2), (14.1, 0.05, 0.025)]
    if w == "bell":  # inharmonic, the high partials die first
        return [(1, 1, 1.6), (2.0, 0.55, 1.1), (2.76, 0.4, 0.6), (5.4, 0.22, 0.25), (8.93, 0.12, 0.12)]
    raise ValueError(w)


class _Env(BaseModel):
    a: float = 0.005
    d: float = 0.2
    s: float = 0.7
    r: float = 0.15
    cut: float = 2000.0  # filter cutoff (Hz) after the envelope
    cut_peak: float = 0.0  # extra cutoff at the start of the note, dying with cut_decay
    cut_decay: float = 0.15


def _env_for(role: str, p: MusicParams) -> _Env:
    b = p.brightness
    if role == "bass":
        if p.bass == "kick":
            return _Env(a=0.004, d=0.6, s=0.75, r=0.12, cut=900, cut_peak=600 * b)
        return _Env(a=0.004, d=0.18, s=0.65, r=0.08, cut=250 + 1200 * b, cut_peak=1500 * b, cut_decay=0.1)
    if role == "chords":
        cs = p.chord_style
        if cs == "pad":
            return _Env(a=min(0.6, 0.25 * 240 / p.bpm / 2 + 0.1), d=0.6, s=0.8, r=0.7, cut=500 + 3000 * b, cut_peak=400 * b, cut_decay=0.8)
        if cs == "keys":
            return _Env(a=0.003, d=1.4, s=0.35, r=0.35, cut=1500 + 5000 * b, cut_peak=2000, cut_decay=0.25)
        if cs == "stab":
            return _Env(a=0.003, d=0.16, s=0.0, r=0.08, cut=900 + 2500 * b, cut_peak=3000 * b, cut_decay=0.06)
        return _Env(a=0.002, d=0.3, s=0.0, r=0.12, cut=600 + 2000 * b, cut_peak=6000 * b, cut_decay=0.08)  # pluck / arp
    if p.lead == "bells" or p.lead_wave == "bell":
        return _Env(a=0.002, d=1.8, s=0.0, r=1.0, cut=3000 + 8000 * b)
    if p.lead == "arp":
        return _Env(a=0.002, d=0.18, s=0.2, r=0.08, cut=900 + 3500 * b, cut_peak=4000 * b, cut_decay=0.06)
    return _Env(a=0.012, d=0.35, s=0.6, r=0.18, cut=900 + 4500 * b, cut_peak=1500 * b, cut_decay=0.2)


def _note(wave_: str, midi: float, gate: float, env: _Env, detune: float, seed: int) -> np.ndarray:
    """One note, stereo (n, 2): additive partials through a (per-partial) lowpass that follows the filter envelope."""
    n = int((gate + env.r) * SR) + 1
    t = np.arange(n, dtype=np.float64) / SR
    # ADSR: attack, decay toward sustain while the key is held, then release from wherever it got to
    amp = np.where(t < env.a, t / max(env.a, 1e-4), env.s + (1 - env.s) * np.exp(-(t - env.a) / max(env.d, 1e-3)))
    gi = min(n - 1, int(gate * SR))
    held = amp[gi]
    rel = t >= gate
    amp[rel] = held * np.exp(-(t[rel] - gate) / max(env.r / 4, 1e-3))
    cut = env.cut + env.cut_peak * np.exp(-t / env.cut_decay) if env.cut_peak else np.full(n, env.cut)
    rng = np.random.default_rng(seed)
    voices = [(0.0, 0.5)] if detune <= 0 else [(-detune * 14, 0.25), (detune * 14, 0.75)]
    out = np.zeros((n, 2))
    f0 = _hz(midi)
    parts = _partials(wave_)
    for cents, pan in voices:
        fv = f0 * 2 ** (cents / 1200)
        x = np.zeros(n)
        for r, a, dk in parts:
            fr = fv * r
            if fr > 15000:
                continue
            g = a / np.sqrt(1 + (fr / cut) ** 4)
            if float(np.max(np.abs(g))) < 0.002:
                continue
            if dk:
                g = g * np.exp(-t / dk)
            x += g * np.sin(2 * np.pi * fr * t + rng.uniform(0, 2 * np.pi))
        out[:, 0] += x * math.cos(pan * math.pi / 2)
        out[:, 1] += x * math.sin(pan * math.pi / 2)
    fade = min(n, int(0.003 * SR))
    out[-fade:] *= np.linspace(1, 0, fade)[:, None]
    return out * amp[:, None] / len(voices) ** 0.5


def _band(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Smooth band-pass (frequency domain), lo/hi in Hz (0 / None = open)."""
    size = 1 << (len(x) - 1).bit_length()
    X = np.fft.rfft(x, size)
    f = np.fft.rfftfreq(size, 1 / SR)
    f[0] = 1e-3
    g = np.ones_like(f)
    if lo:
        g /= np.sqrt(1 + (lo / f) ** 4)
    if hi:
        g /= np.sqrt(1 + (f / hi) ** 4)
    return np.fft.irfft(X * g, size)[: len(x)]


def _drum(kind: str, kit: str, rng: np.random.Generator) -> np.ndarray:
    """One mono drum hit."""
    def tt(sec):
        return np.arange(int(sec * SR)) / SR

    noise = lambda n: rng.uniform(-1, 1, n)  # noqa: E731
    if kind == "kick":
        hi, lo, sweep, decay, click, drive = {"808": (120, 46, 0.05, 0.9, 0.25, 0.25), "punchy": (170, 52, 0.035, 0.3, 0.6, 0.4),
                                              "boombap": (140, 50, 0.04, 0.32, 0.5, 0.5), "lofi": (110, 48, 0.04, 0.25, 0.15, 0.2),
                                              "chip": (380, 60, 0.02, 0.13, 0.3, 0.0), "soft": (95, 50, 0.05, 0.35, 0.1, 0.0)}[kit]
        t = tt(decay * 4 + 0.05)
        f = lo + (hi - lo) * np.exp(-t / sweep)
        ph = 2 * np.pi * np.cumsum(f) / SR
        x = np.sin(ph) * np.exp(-t / decay) * (1 - np.exp(-t / 0.0008))
        if kit == "chip":
            x = np.sign(x) * np.abs(x) ** 0.3 * np.exp(-t / decay)
        cl = _band(noise(len(t)), 1500, 9000) * np.exp(-t / 0.004) * click * 1.5
        x = x + cl
        if drive:
            k = 1 + drive * 5
            x = np.tanh(x * k) / math.tanh(k)
        if kit == "lofi":
            x = _band(x, 0, 2500)
        return x
    if kind == "snare":
        tone_f, tone_d, lo, hi, nd = {"808": (190, 0.06, 1200, 9000, 0.14), "punchy": (200, 0.07, 1500, 10000, 0.15),
                                      "boombap": (180, 0.06, 900, 7000, 0.17), "lofi": (180, 0.05, 700, 4500, 0.13),
                                      "chip": (250, 0.03, 2000, 9000, 0.09), "soft": (210, 0.05, 1000, 7000, 0.1)}[kit]
        t = tt(nd * 5)
        body = np.sin(2 * np.pi * tone_f * t) * np.exp(-t / tone_d) * 0.8
        nz = _band(noise(len(t)), lo, hi) * np.exp(-t / nd) * 2.2
        return body + nz
    if kind == "clap":
        t = tt(0.5)
        nz = _band(noise(len(t)), 900, 2600 if kit != "lofi" else 2000) * 2.5
        env = np.exp(-t / 0.14) * 0.6
        for d in (0.0, 0.011, 0.022):
            m = t >= d
            env[m] += np.exp(-(t[m] - d) / 0.005)
        return nz * env
    if kind in ("hat", "open_hat"):
        lo, hi = (5000, 12000) if kit == "chip" else (4000, 9000) if kit == "lofi" else (7000, 16000)
        d = 0.03 if kind == "hat" else 0.22
        t = tt(d * 6)
        x = _band(noise(len(t)), lo, hi) * np.exp(-t / d) * 2.0
        if kit == "chip":
            x = np.round(x * 4) / 4
        return x
    if kind == "perc":
        if kit == "chip":
            t = tt(0.06)
            return np.sign(np.sin(2 * np.pi * 1000 * t)) * np.exp(-t / 0.015) * 0.6
        t = tt(0.15)
        return _band(noise(len(t)), 3500, 11000) * (1 - np.exp(-t / 0.006)) * np.exp(-t / 0.035) * 1.6
    raise ValueError(kind)


_PAN = {"kick": 0.5, "snare": 0.5, "clap": 0.48, "hat": 0.62, "open_hat": 0.6, "perc": 0.35}


# ---- writing the parts --------------------------------------------------------------------------------------------

def _time(p: MusicParams, pos: float) -> float:
    """Seconds of a step position, with swing on the off 16ths."""
    step = 60.0 / p.bpm / 4
    s = int(math.floor(pos + 1e-9))
    return (pos + (p.swing * 0.5 if s % 2 == 1 else 0.0)) * step


def _drum_hits(p: MusicParams, rng: np.random.Generator) -> dict[str, list[tuple[float, float]]]:
    total = p.bars * STEPS
    hits = {k: _pattern_hits(getattr(p, k), total) for k in DRUMS}
    if p.fill and p.bars >= 2 and (p.snare or p.clap):
        fill_bars = {b for b in range(p.bars) if (b + 1) % 4 == 0} | {p.bars - 1}
        key = "snare" if p.snare else "clap"
        for b in fill_bars:
            a = b * STEPS + 12
            hits[key] = [h for h in hits[key] if not a <= h[0] < a + 4]
            hits[key] += [(a + i, 0.45 + 0.15 * i) for i in range(4)] + [(a + 3.5, 0.9)]
            hits["open_hat"] = [h for h in hits["open_hat"] if not a <= h[0] < a + 4]
    j = 0.006 * p.humanize
    out = {}
    for k, hs in hits.items():
        out[k] = [(max(0.0, _time(p, s) + (rng.uniform(-j, j) if j and s > 0 else 0.0)),
                   max(0.1, v * (1 + rng.uniform(-0.15, 0.15) * p.humanize))) for s, v in hs]
    return out


def _bass_notes(p: MusicParams, prog, kick_steps: list[float]) -> list[tuple[float, float, int, float]]:
    """[(step, length steps, midi, velocity)]."""
    if p.bass == "none":
        return []
    base = 12 * (p.bass_octave + 1)
    def root(pcs):
        return base + pcs[0]
    out = []
    for s, n, pcs in prog:
        r = root(pcs)
        if p.bass == "root":
            out.append((s, n, r, 1.0))
        elif p.bass == "eighths":
            out += [(s + i, 1.8, r + (12 if i % 8 == 6 and n >= 8 else 0), 1.0 if i % 4 == 0 else 0.8) for i in range(0, n, 2)]
        elif p.bass == "offbeat":
            out += [(s + i, 1.6, r, 1.0) for i in range(2, n, 4)]
        elif p.bass == "walk":
            fifth = (pcs[2] - pcs[0]) % 12
            third = (pcs[1] - pcs[0]) % 12
            line = [0, third, fifth, 12 if fifth == 7 else fifth]
            for i in range(0, n, 4):
                out.append((s + i, 3.6, r + line[(i // 4) % 4], 1.0 if i % 16 == 0 else 0.85))
        elif p.bass == "kick":
            ks = sorted(k for k in kick_steps if s <= k < s + n) or [s]
            for a, b in zip(ks, ks[1:] + [s + n]):
                out.append((a, max(1.0, b - a), r, 1.0))
    return out


def _chord_notes(p: MusicParams, prog) -> list[tuple[float, float, int, float]]:
    if p.chord_style == "none":
        return []
    center = 12 * (p.chord_octave + 1) + 7
    out = []
    default_rhythm = {"stab": "x..x..x...x..x..", "pluck": "x..x..x.x..x..x.", "keys": "x.......x......."}
    for s, n, pcs in prog:
        v = _voicing(pcs, center)
        if p.chord_style == "pad":
            out += [(s, n, m, 0.9) for m in v]
        elif p.chord_style == "arp":
            seq = v + [m + 12 for m in v]
            seq = seq + seq[-2:0:-1]
            out += [(s + i, 0.9, seq[i % len(seq)], 1.0 if i % 4 == 0 else 0.75) for i in range(n)]
        else:
            rh = p.chord_rhythm or default_rhythm[p.chord_style]
            hs = [(int(pos), vel) for pos, vel in _pattern_hits(rh, n) if pos == int(pos)]
            for k, (i, vel) in enumerate(hs):
                nxt = hs[k + 1][0] if k + 1 < len(hs) else n
                ln = (nxt - i) if p.chord_style == "keys" else min(nxt - i, 2)
                out += [(s + i, ln, m, vel) for m in v]
    return out


def _lead_notes(p: MusicParams, prog, rng: np.random.Generator) -> list[tuple[float, float, int, float]]:
    if p.lead == "none":
        return []
    scale = SCALES[p.scale]
    root = 12 * (p.lead_octave + 1) + key_pc(p.key)
    total = p.bars * STEPS
    if p.lead == "arp":
        out = []
        rate = 1 if p.lead_density >= 0.5 else 2
        for s, n, pcs in prog:
            v = _voicing(pcs, root + 5)
            seq = v + [m + 12 for m in v]
            seq = seq + seq[-2:0:-1]
            out += [(s + i, rate * 0.9, seq[(i // rate) % len(seq)], 1.0 if i % 4 == 0 else 0.7) for i in range(0, n, rate)]
        return out

    grid = 4 if p.lead == "bells" else 2  # quarters for bells, eighths for a melody
    phrase = STEPS * (2 if p.bars >= 2 else 1)
    dens = p.lead_density * (0.55 if p.lead == "bells" else 1.0)

    def rhythm():
        on = []
        for pos in range(0, phrase, grid):
            w = 1.35 if pos % 8 == 0 else 1.0 if pos % 4 == 0 else 0.7
            if pos == 0 or rng.random() < min(0.95, dens * w):
                on.append(pos)
            if p.lead == "melody" and dens > 0.6 and pos % 4 == 2 and rng.random() < (dens - 0.6):
                on.append(pos + 1)  # a quick 16th pickup
        if len(on) > 2 and phrase - on[-1] < 4:
            on.pop()  # breathe before the next phrase
        return sorted(set(on))

    def contour(n):  # scale-index moves: mostly steps, a few leaps
        return [int(rng.choice([-2, -1, -1, 0, 1, 1, 2, 3, -3], p=[.1, .2, .2, .06, .2, .14, .05, .03, .02])) for _ in range(n)]

    motifs = {}
    for name in "AB":
        r = rhythm()
        motifs[name] = (r, contour(len(r)))
    form = "AABA" if total // phrase >= 4 else "AB" if total // phrase >= 2 else "A"

    def nearest_chord_tone(i: int, pcs: list[int]) -> int:
        best = min(range(i - 3, i + 4), key=lambda k: (_deg(scale, root, k) % 12 not in pcs, abs(k - i)))
        return best

    out = []
    cur = 2 if p.scale not in _PARENT else 1
    for ph in range(total // phrase):
        kind = form[ph % len(form)]
        r, moves = motifs[kind]
        last_phrase = ph == total // phrase - 1
        for k, pos in enumerate(r):
            step = ph * phrase + pos
            pcs = _chord_at(prog, step)
            cur = max(-2, min(len(scale) + 4, cur + moves[k]))
            if pos % 8 == 0 or k == len(r) - 1:
                cur = nearest_chord_tone(cur, pcs)
            if last_phrase and k == len(r) - 1:
                cur = nearest_chord_tone(cur, pcs[:1])  # settle on the chord's root before looping round
            nxt = r[k + 1] if k + 1 < len(r) else phrase
            ln = min(nxt - pos, 8 if p.lead == "bells" else 6)
            vel = 1.0 if pos % 8 == 0 else 0.85 if pos % 4 == 0 else 0.72
            out.append((step, max(1, ln) * 0.92, _deg(scale, root, cur), vel))
    return out


# ---- mixing (circular: the loop's end flows into its start) -------------------------------------------------------

def _fold(buf: np.ndarray, L: int) -> np.ndarray:
    out = np.zeros((L,) + buf.shape[1:])
    for i in range(0, len(buf), L):
        seg = buf[i:i + L]
        out[: len(seg)] += seg
    return out


def _circ_filter(x: np.ndarray, gain_fn) -> np.ndarray:
    L = len(x)
    X = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(L, 1 / SR)
    f[0] = 1e-3
    return np.fft.irfft(X * gain_fn(f)[:, None], L, axis=0)


def _reverb(x: np.ndarray, size: float, rng: np.random.Generator) -> np.ndarray:
    """Stereo noise-tail reverb, applied circularly so the tail wraps into the loop's start."""
    L = len(x)
    m = int(size * SR)
    t = np.arange(m) / SR
    pre = int(0.012 * SR)
    mono = x.mean(axis=1)
    X = np.fft.rfft(mono)
    wet = np.zeros_like(x)
    for ch in range(2):
        ir = rng.standard_normal(m) * np.exp(-6.9 * t / size)
        ir = np.concatenate([np.zeros(pre), ir])
        ir = _fold(ir[:, None], L)[:, 0] if len(ir) > L else np.pad(ir, (0, L - len(ir)))
        wet[:, ch] = np.fft.irfft(X * np.fft.rfft(ir), L)
    wet = _circ_filter(wet, lambda f: 1 / np.sqrt(1 + (f / 6000) ** 2) / np.sqrt(1 + (200 / f) ** 2))
    rd, rw = float(np.sqrt(np.mean(x ** 2))) or 1.0, float(np.sqrt(np.mean(wet ** 2))) or 1.0
    return wet * rd / rw


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


def synth(p: MusicParams) -> np.ndarray:
    """Stereo float samples (n, 2) in -1..1, exactly one loop long, seamless."""
    rng = np.random.default_rng(p.seed)
    L = int(round(loop_seconds(p) * SR))
    tail = int(4.5 * SR)
    total = p.bars * STEPS
    prog = _progression(p)
    step_s = 60.0 / p.bpm / 4

    drums = np.zeros((L + tail, 2))
    kick_times: list[float] = []
    if p.drums_level > 0:
        hits = _drum_hits(p, rng)
        kick_times = [t for t, _ in hits["kick"]]
        drng = np.random.default_rng(p.seed + 11)
        for k in DRUMS:
            if not hits[k]:
                continue
            takes = [_drum(k, p.kit, drng) for _ in range(3)]
            pan = _PAN[k]
            cl, cr = math.cos(pan * math.pi / 2), math.sin(pan * math.pi / 2)
            for i, (t, v) in enumerate(hits[k]):
                x = takes[i % 3] * v
                a = int(t * SR)
                drums[a:a + len(x), 0] += x * cl
                drums[a:a + len(x), 1] += x * cr
    kick_steps = [s for s, _ in _pattern_hits(p.kick, total)] if p.kick else []

    def play(notes, role, wave_, detune) -> np.ndarray:
        buf = np.zeros((L + tail, 2))
        env = _env_for(role, p)
        cache: dict = {}
        for s, ln, midi, vel in notes:
            gate = ln * step_s
            key = (midi, round(gate, 3))
            if key not in cache:
                cache[key] = _note(wave_, midi, gate, env, detune, seed=p.seed * 131 + midi)
            x = cache[key] * vel
            a = int(_time(p, s) * SR)
            if p.humanize and role == "lead":
                a += int(rng.uniform(-0.004, 0.004) * p.humanize * SR)
            a = max(0, a)
            n = min(len(x), len(buf) - a)
            buf[a:a + n] += x[:n]
        return buf

    bass = play(_bass_notes(p, prog, kick_steps), "bass", p.bass_wave, 0.0)
    if p.bass == "kick" and p.bass_wave == "sine":  # an 808 is a sine pushed into saturation
        bass = np.tanh(bass * 2.2) / math.tanh(2.2)
    chords = play(_chord_notes(p, prog), "chords", p.chord_wave, p.detune)
    lead = play(_lead_notes(p, prog, np.random.default_rng(p.seed + 3)), "lead", p.lead_wave, p.detune * 0.6)

    buses = {"drums": (drums, 0.2, p.drums_level), "bass": (bass, 0.15, p.bass_level),
             "chords": (chords, 0.085, p.chords_level), "lead": (lead, 0.075, p.lead_level)}
    mixed = {}
    for k, (b, target, level) in buses.items():
        b = _fold(b, L)
        r = _rms(b)
        mixed[k] = b * (target * level / r) if r > 1e-9 and level > 0 else np.zeros((L, 2))

    if p.pump > 0:
        times = kick_times or [i * 4 * step_s for i in range(total // 4)]
        t = np.arange(L) / SR
        g = np.ones(L)
        for kt in times:
            dt = (t - kt) % (L / SR)
            g *= 1 - p.pump * 0.75 * np.exp(-dt / 0.13) * (dt < 0.45)
        for k in ("bass", "chords", "lead"):
            mixed[k] = mixed[k] * g[:, None]

    music = sum(mixed.values())
    if p.room > 0:
        send = mixed["chords"] * 0.9 + mixed["lead"] * 0.8 + mixed["drums"] * 0.2
        if _rms(send) > 1e-9:
            music = music + _reverb(send, p.room_size, np.random.default_rng(p.seed + 5)) * p.room * 0.9

    if p.lofi > 0:
        # tape wow: a slow wobble with a whole number of cycles per loop, so it loops too
        k = max(1, round(L / SR / 2.2))
        idx = np.arange(L) + p.lofi * 0.0018 * SR * np.sin(2 * np.pi * k * np.arange(L) / L)
        ext = np.concatenate([music[-2:], music, music[:3]])
        music = np.stack([np.interp(idx + 2, np.arange(len(ext)), ext[:, c]) for c in range(2)], axis=1)
        top = 14000 - 9500 * p.lofi
        music = _circ_filter(music, lambda f: 1 / np.sqrt(1 + (f / top) ** 4))
        peak = float(np.max(np.abs(music))) or 1.0
        levels = 2 ** (16 - 7 * p.lofi)
        music = np.round(music / peak * levels) / levels * peak
    if p.vinyl > 0:
        vr = np.random.default_rng(p.seed + 9)
        lvl = _rms(music) or 0.05
        hiss = _band(vr.uniform(-1, 1, L), 800, 9000)
        crackle = np.zeros(L)
        for _ in range(int(L / SR * (4 + 10 * p.vinyl))):
            a = int(vr.integers(0, L - 300))
            crackle[a:a + 200] += vr.uniform(0.3, 1.0) ** 3 * np.exp(-np.arange(200) / 18) * vr.choice([-1, 1])
        crackle = _band(crackle, 1500, 12000)
        noise = (hiss / (_rms(hiss) or 1) * 0.04 + crackle / (np.max(np.abs(crackle)) or 1) * 0.5) * lvl * p.vinyl
        music = music + np.stack([noise, np.roll(noise, 37)], axis=1)

    music = _circ_filter(music, lambda f: 1 / np.sqrt(1 + (30 / f) ** 4) / np.sqrt(1 + (f / 18000) ** 8))
    peak = float(np.max(np.abs(music))) or 1.0
    music = np.tanh(music / peak * 1.6) / math.tanh(1.6)  # gentle glue
    peak = float(np.max(np.abs(music))) or 1.0
    return (music / peak * p.volume).astype(np.float32)


def render(p: MusicParams) -> bytes:
    """A 44.1 kHz 16-bit stereo WAV (bytes), exactly one loop long."""
    pcm = (np.clip(synth(p), -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def save(p: MusicParams, folder: Path) -> Path:
    """Write the loop (and its settings, `.json` beside it) into `folder`, named stably for the same settings."""
    folder.mkdir(parents=True, exist_ok=True)
    d = p.model_dump()
    h = hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:8]
    slug = "".join(c if c.isalnum() else "-" for c in p.name.lower()).strip("-")[:24] or "loop"
    out = folder / f"loop-{slug}-{h}.wav"
    if not out.exists():
        out.write_bytes(render(p))
    out.with_suffix(".json").write_text(json.dumps(d, indent=1))
    return out


def params_of(path: Path) -> MusicParams | None:
    """The settings a generated loop was made with (from the `.json` beside it), or None for other music."""
    side = Path(path).with_suffix(".json")
    if not side.exists():
        return None
    try:
        return MusicParams(**json.loads(side.read_text()))
    except (ValueError, OSError):
        return None


# ---- AI: describe the music, get the numbers ----------------------------------------------------------------------

AI_SYSTEM = """You write background music loops for videos on a small sequencer + synth. You never produce audio;
you choose its settings (MusicParams). The loop plays under a video, often under speech, so it must support, not
compete: keep the lead sparse (lead_density 0.2-0.45) for talking-head videos, busier only for music-only edits.
- Harmony: key, scale, chords as scale degrees (1-7) that repeat, chord_bars per chord, sevenths for jazzy / lofi.
  Proven progressions: 1-5-6-4 (pop, uplifting), 1-6-3-7 in minor (epic, synthwave), 2-5-1-6 with sevenths (lofi,
  jazzy), 1-6-4-5 (classic, warm), 1-4 or 1-1-6-7 (house, hypnotic), 6-4-1-5 (emotional pop), 1-6-7-6 minor (trap).
- Drums: patterns of 16 steps per bar ('x' accent, 'o' hit, '.' rest, '2'/'3'/'4' rolls - trap hats). Empty = off.
  Backbeat snare/clap on steps 5 and 13 ("....x.......x..."); half-time = only step 9; four-on-the-floor kick
  "x...x...x...x...". Kits: 808, punchy, boombap, lofi, chip, soft. swing 0.15-0.3 for lofi / hip hop groove.
- bass: root / eighths / offbeat (house) / kick (808 following the kick: bass_wave sine, bass_octave 1) / walk.
- chord_style: pad / stab / pluck / keys (epiano) / arp; lead: melody / arp / bells / none.
- Tone: brightness, detune (width), pump (sidechain, house / EDM 0.5-0.7), lofi + vinyl (lo-fi), room + room_size.
- bars: 4 or 8 for most loops (8 gives the melody room for a question and an answer).
Start from the closest style and change only what the description asks for. Name the loop in 2-3 words.
Never imitate a specific existing song, artist or melody; write an original loop in the requested genre."""


class MusicDesign(BaseModel):
    params: MusicParams
    note: str = Field("", description="One short sentence for the user about the loop")


def design(prompt: str, current: MusicParams | None = None, model: str | None = None) -> dict:
    """Ask the AI to write a loop for a description. Raises ai.client.AIError when no model is available."""
    from .ai import client as aic

    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Describe the music first (e.g. 'calm lofi for a study vlog')")
    lines = [f'Music wanted: "{prompt[:500]}"', "", "Styles (name: settings; unlisted settings are the defaults):"]
    lines += [f"- {k}: {json.dumps(v)}" for k, v in STYLES.items()]
    if current:
        lines += ["", "The user's current loop (change it if the request sounds like a tweak, e.g. 'faster', 'darker'):",
                  json.dumps(current.model_dump())]
    with aic.use_models({aic.TIERS.get("music", "editor"): model} if model else {}):
        out, usage = aic.call("music", "\n".join(lines), MusicDesign, system=AI_SYSTEM, quality="economy")
    return {"params": fit_length(out.params).model_dump(), "note": out.note[:300], "usage": usage}


# ---- into a project -----------------------------------------------------------------------------------------------

def _source(p, pdir: Path, params: MusicParams) -> str:
    from .project import Source, store_media_path

    path = save(params, pdir / "media" / "music")
    with wave.open(str(path)) as w:
        length = w.getnframes() / w.getframerate()
    sid = p.new_id("s")
    p.sources[sid] = Source(path=store_media_path(pdir, path), duration=round(length, 3), has_video=False, has_audio=True)
    return sid


def add_to_project(p, pdir: Path, params: MusicParams, start: float = 0.0, volume: float = 0.25,
                   hold: float | None = None, duck: bool = True):
    """Render the loop into media/music and put it on a looping music track from `start`, for `hold` seconds or to
    the end of the edit. It ducks under speech by default, like any background music."""
    from .project import MusicTrack

    sid = _source(p, pdir, params)
    start = max(0.0, float(start))
    m = MusicTrack(id=p.new_id("m"), source=sid, start=start, volume=max(0.0, min(2.0, float(volume))), loop=True,
                   duck=bool(duck), end=(start + float(hold)) if hold else None, fade_in=0.5, fade_out=1.5)
    p.music.append(m)
    return m


def track_params(p, pdir: Path, music_id: str) -> MusicParams:
    """The settings of a generated loop already on the timeline."""
    m = next((x for x in p.music if x.id == music_id), None)
    if m is None:
        raise ValueError(f"No music track {music_id}")
    src = p.sources.get(m.source)
    prm = params_of(Path(pdir) / src.path) if src else None
    if prm is None:
        raise ValueError(f"{music_id} isn't a loop made with `music make` (it has no settings to change)")
    return prm


def replace_on_track(p, pdir: Path, music_id: str, params: MusicParams):
    """Swap a track's loop for a new version, keeping its start, volume, ducking, length and fades."""
    m = next((x for x in p.music if x.id == music_id), None)
    if m is None:
        raise ValueError(f"No music track {music_id}")
    m.source = _source(p, pdir, params)
    m.in_ = 0.0
    return m

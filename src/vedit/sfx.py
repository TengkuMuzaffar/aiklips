"""Sound effects made from scratch: a small synth, no model, no downloads.

A sound is a few numbers (`SfxParams`) built from layers the way real sounds are:
- a **voice**: a source (sine / triangle / square / saw, a glottal `voice`, or coloured noise) through a resonant
  filter (low / high / band-pass) whose cutoff can sweep, an optional **vowel** (formant filters: a, e, i, o, u, er,
  morphing from one to another) for screams, yells, moans and growls, a pitch sweep + pitch curve, jitter, roughness,
  breath, attack / fade-out, wobble, flutter, lo-fi;
- a **transient**: a 1-2 ms bright tick, the snap at the start of every click, tap and hit;
- a **body**: damped resonances (`modes`), the ring of the plastic / wood / glass / metal that was struck;
- **particles**: many tiny events scattered in time and stereo (rain drops, fire crackles, claps, bubbles, ticks);
- **movement**: a loudness curve (`env_points`) and a slow random drift (`motion`: gusts, swells, rain coming and
  going) that moves the loudness and the filter;
- **hits**: the sound again, later and quieter (a mouse button's release, a heartbeat's second thump);
- **finishing**: drive, echo, room (reverb), stereo width, low cut, peak normalising; `loop` makes a seamless loop
  (an ambience bed that plays under a scene).

Presets are hand-tuned starting points, `pitch` / `length` are the big knobs on top, `vary` makes natural variations,
and the AI designs a sound by choosing the numbers.

    wav = render(preset("rain"))   # bytes of a 44.1 kHz 16-bit stereo WAV that loops seamlessly
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import random
import wave
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

SR = 44100
MAX_LEN = 12.0
Wave = Literal["sine", "triangle", "square", "saw", "noise", "voice"]
Vowel = Literal["none", "a", "e", "i", "o", "u", "er"]
GrainKind = Literal["drop", "crackle", "tick", "clap", "bubble"]

# formants (Hz) and their loudness, adult voice; `formant_shift` scales them (+3 st ~ a smaller / higher voice)
FORMANTS = {"a": (730, 1090, 2440), "e": (530, 1840, 2480), "i": (300, 2290, 3010), "o": (570, 840, 2410),
            "u": (320, 870, 2240), "er": (490, 1350, 1690)}
FORMANT_GAIN = (1.0, 0.55, 0.3)
FORMANT_BW = (90, 110, 160)


class Mode(BaseModel):
    """One resonance of the struck object: a sine that rings and dies away."""

    model_config = ConfigDict(extra="ignore")
    freq: float = Field(..., ge=20, le=18000, description="Hz")
    decay: float = Field(0.01, ge=0.0005, le=5, description="Seconds for the ring to fall to about a third")
    gain: float = Field(1.0, ge=0, le=1)


class Hit(BaseModel):
    """The sound played again (a button's release, a double click, a bounce)."""

    model_config = ConfigDict(extra="ignore")
    at: float = Field(..., ge=0.0, le=MAX_LEN, description="Seconds after the start")
    gain: float = Field(0.5, ge=0, le=1.5)
    pitch: float = Field(0.0, ge=-24, le=24, description="Semitones relative to the first")


class SfxParams(BaseModel):
    """One sound effect. Frequencies in Hz, times in seconds, amounts 0-1."""

    model_config = ConfigDict(extra="ignore")
    name: str = Field("sound", description="Short name, e.g. 'soft whoosh'")
    # voice
    wave: Wave = Field("sine", description="Voice source: sine (pure), triangle (soft), square (8-bit, hollow), saw (buzzy), noise (air, hiss), voice (vocal cords: use with a vowel)")
    tone: float = Field(1.0, ge=0, le=1, description="Loudness of the voice layer (0 = only the other layers)")
    pitch_start: float = Field(440.0, ge=20, le=12000, description="Voice pitch at the start (Hz)")
    pitch_end: float = Field(440.0, ge=20, le=12000, description="Voice pitch at the end (Hz); higher than start = rising")
    pitch_points: list[list[float]] = Field(default_factory=list, description="Pitch curve on top of the sweep: [[time 0-1, semitones], ...], e.g. a scream's rise and fall")
    duration: float = Field(0.5, ge=0.01, le=MAX_LEN, description="Length in seconds (of one hit, or of the loop)")
    attack: float = Field(0.01, ge=0, le=MAX_LEN, description="Fade-in (s); long = swells in")
    decay: float = Field(0.3, ge=0.002, le=MAX_LEN, description="Fade-out at the end (s)")
    curve: float = Field(1.0, ge=0.2, le=5, description="Sweep shape: 1 = even, >1 = slow then fast, <1 = fast then slow")
    noise: float = Field(0.0, ge=0, le=1, description="Noise mixed into the voice (0 = pure tone, 1 = only noise)")
    noise_color: Literal["white", "pink", "brown"] = Field("white", description="white = hiss, pink = rain / surf, brown = rumble / wind / roar")
    filter_type: Literal["lowpass", "highpass", "bandpass"] = Field("lowpass", description="lowpass = muffles highs, highpass = thins out lows, bandpass = keeps a band")
    filter_start: float = Field(12000.0, ge=40, le=20000, description="Filter frequency at the start (Hz)")
    filter_end: float = Field(12000.0, ge=40, le=20000, description="Filter frequency at the end (Hz); sweeping it = a whoosh")
    resonance: float = Field(0.7, ge=0.3, le=20, description="Filter Q: 0.7 = smooth, 2-6 = focused, 10+ = whistling")
    vowel: Vowel = Field("none", description="Formant filter that makes the voice sound like a vowel (a e i o u er); none = off")
    vowel_end: Vowel | None = Field(None, description="Vowel it morphs into by the end (e.g. a -> o); null = same vowel")
    formant_shift: float = Field(0.0, ge=-12, le=12, description="Vocal tract size in semitones: + smaller (child, woman, cartoon), - bigger (man, monster)")
    jitter: float = Field(0.0, ge=0, le=1, description="Random pitch wander (strained, shaky voice)")
    roughness: float = Field(0.0, ge=0, le=1, description="Rasp / growl / scream distortion of the voice")
    breath: float = Field(0.0, ge=0, le=1, description="Breathy air in the voice (whisper at 1)")
    vibrato_rate: float = Field(0.0, ge=0, le=40, description="Wobble speed (Hz)")
    vibrato_depth: float = Field(0.0, ge=0, le=12, description="Wobble size in semitones")
    tremolo: float = Field(0.0, ge=0, le=1, description="Volume flutter amount (at the wobble speed, or 12 Hz)")
    crush: float = Field(0.0, ge=0, le=1, description="Bit-crush / lo-fi grit")
    # transient + body
    transient: float = Field(0.0, ge=0, le=1, description="Sharp 1-2 ms tick at the start: the snap of a click / tap / hit")
    transient_tone: float = Field(5000.0, ge=500, le=16000, description="Brightness of the tick (Hz)")
    modes: list[Mode] = Field(default_factory=list, description="Body resonances (freq Hz, decay s, gain): plastic 1-6 kHz short; wood 200-1500 Hz; glass / metal high and long")
    body: float = Field(1.0, ge=0, le=2, description="Loudness of the body resonances")
    body_tune: float = Field(0.0, ge=-24, le=24, description="Moves every body resonance by semitones (smaller / bigger object)")
    body_ring: float = Field(1.0, ge=0.2, le=5, description="Multiplies how long the body rings (damped <-> ringing)")
    # particles
    grain_rate: float = Field(0.0, ge=0, le=6000, description="Particles per second (0 = off): drizzle 100, rain 800, downpour 3000, fire crackle 20-40, applause 40-80")
    grain_kind: GrainKind = Field("drop", description="drop (rain on a surface), crackle (fire, vinyl), tick (gravel, clock, insects), clap (applause), bubble (water)")
    grain_freq: float = Field(2500.0, ge=60, le=12000, description="Typical pitch / brightness of a particle (Hz)")
    grain_spread: float = Field(12.0, ge=0, le=36, description="How much particles differ in pitch (semitones)")
    grain_len: float = Field(0.015, ge=0.001, le=0.4, description="Length of one particle (s)")
    grain_level: float = Field(1.0, ge=0, le=2, description="Loudness of the particle layer")
    grain_vary: float = Field(0.6, ge=0, le=1, description="Loudness spread: 0 = all equal, 1 = mostly quiet with a few loud ones")
    # movement
    env_points: list[list[float]] = Field(default_factory=list, description="Loudness curve: [[time 0-1, level 0-1.5], ...], e.g. a swell or a stutter")
    motion: float = Field(0.0, ge=0, le=1, description="Slow random drift of loudness and filter (gusts of wind, rain coming and going)")
    motion_rate: float = Field(0.3, ge=0.02, le=20, description="Speed of that drift (Hz): 0.1-0.5 gusts, 2-8 flicker / shake")
    # hits + finishing
    hits: list[Hit] = Field(default_factory=list, description="Repeats: the release of a button, a double click, bounces")
    drive: float = Field(0.0, ge=0, le=1, description="Saturation: thicker, louder, grittier (screams, hits)")
    echo: float = Field(0.0, ge=0, le=1, description="Echo amount")
    echo_delay: float = Field(0.12, ge=0.02, le=1, description="Time between echoes (s)")
    room: float = Field(0.0, ge=0, le=1, description="Room reverb amount (0 = dry, close up)")
    room_size: float = Field(0.4, ge=0.05, le=3, description="Reverb length (s): 0.1 small desk, 0.5 room, 2 hall / cave")
    width: float = Field(0.3, ge=0, le=1, description="Stereo width")
    low_cut: float = Field(30.0, ge=20, le=2000, description="Removes rumble below this (Hz)")
    loop: bool = Field(False, description="A seamless loop (ambience bed: rain, wind, fire, applause) that repeats under the scene")
    volume: float = Field(0.8, ge=0, le=1, description="Peak loudness of the file")
    seed: int = Field(7, ge=0, description="Randomness; another seed = another take of the same sound")


# ---- presets: hand-tuned starting points --------------------------------------------------------------------------

def _m(*rows) -> list[dict]:
    return [{"freq": f, "decay": d, "gain": g} for f, d, g in rows]


_CLICK = {"wave": "noise", "noise": 1, "attack": 0.0, "filter_type": "bandpass", "low_cut": 250, "width": 0.25}
_AMB = {"loop": True, "wave": "noise", "noise": 1, "attack": 0.0, "decay": 0.002, "width": 0.9}
PRESETS: dict[str, dict] = {
    # ---- clicks & taps: transient + short body, often a release
    "mouse click": {"cat": "Clicks & taps", "about": "Mouse button: press and release", **_CLICK, "duration": 0.03, "decay": 0.02, "tone": 0.35,
                    "filter_start": 3800, "filter_end": 3000, "resonance": 2.5, "transient": 0.9, "transient_tone": 6000,
                    "modes": _m((2650, 0.004, 1), (4900, 0.0025, 0.55), (1350, 0.006, 0.35)), "body": 0.8,
                    "hits": [{"at": 0.085, "gain": 0.55, "pitch": 1.5}], "room": 0.06, "room_size": 0.12},
    "soft click": {"cat": "Clicks & taps", "about": "Quiet modern UI click", **_CLICK, "duration": 0.025, "decay": 0.02, "tone": 0.2,
                   "filter_start": 2200, "filter_end": 1800, "resonance": 1.5, "transient": 0.5, "transient_tone": 3500,
                   "modes": _m((1900, 0.005, 1), (3700, 0.003, 0.35)), "body": 0.9, "room": 0.05, "room_size": 0.1, "volume": 0.7},
    "ui tick": {"cat": "Clicks & taps", "about": "Tiny crisp tick (menus, scrolling, toggles)", **_CLICK, "duration": 0.012, "decay": 0.01, "tone": 0.25,
                "filter_start": 6000, "filter_end": 5000, "resonance": 3, "transient": 1, "transient_tone": 9000,
                "modes": _m((4200, 0.0018, 1), (7300, 0.0012, 0.4)), "body": 0.7, "low_cut": 600, "volume": 0.65},
    "trackpad tap": {"cat": "Clicks & taps", "about": "Soft dull tap", **_CLICK, "duration": 0.04, "decay": 0.03, "tone": 0.25,
                     "filter_start": 1400, "filter_end": 900, "resonance": 1.2, "transient": 0.35, "transient_tone": 2500,
                     "modes": _m((850, 0.009, 1), (2100, 0.004, 0.4), (330, 0.012, 0.3)), "body": 1, "low_cut": 120, "volume": 0.75},
    "keyboard key": {"cat": "Clicks & taps", "about": "Mechanical keyboard keystroke", **_CLICK, "duration": 0.035, "decay": 0.03, "tone": 0.4,
                     "filter_start": 2600, "filter_end": 2000, "resonance": 1.8, "transient": 0.8, "transient_tone": 5000,
                     "modes": _m((1550, 0.012, 1), (3150, 0.006, 0.5), (480, 0.016, 0.45)), "body": 0.9,
                     "hits": [{"at": 0.095, "gain": 0.35, "pitch": -1}], "room": 0.08, "room_size": 0.15, "low_cut": 150},
    "switch": {"cat": "Clicks & taps", "about": "Light switch / toggle thunk", **_CLICK, "duration": 0.05, "decay": 0.04, "tone": 0.3,
               "filter_start": 1800, "filter_end": 1200, "resonance": 1.5, "transient": 0.85, "transient_tone": 4500,
               "modes": _m((1150, 0.02, 1), (2750, 0.012, 0.45), (5400, 0.006, 0.3), (420, 0.025, 0.35)), "body": 1,
               "room": 0.1, "room_size": 0.2, "low_cut": 120},
    "pen click": {"cat": "Clicks & taps", "about": "Ballpoint pen click, plastic and springy", **_CLICK, "duration": 0.02, "decay": 0.015, "tone": 0.3,
                  "filter_start": 4500, "filter_end": 4000, "resonance": 3, "transient": 1, "transient_tone": 8000,
                  "modes": _m((3300, 0.005, 1), (6200, 0.003, 0.5), (9100, 0.002, 0.25)), "body": 0.8,
                  "hits": [{"at": 0.05, "gain": 0.7, "pitch": 2}], "low_cut": 500},
    "camera shutter": {"cat": "Clicks & taps", "about": "Photo shutter: clack, whirr, clack", **_CLICK, "duration": 0.05, "decay": 0.04, "tone": 0.7,
                       "filter_start": 3200, "filter_end": 2200, "resonance": 1.2, "transient": 0.9, "transient_tone": 6500,
                       "modes": _m((2100, 0.008, 1), (4300, 0.005, 0.5), (900, 0.01, 0.4)), "body": 0.7,
                       "hits": [{"at": 0.07, "gain": 0.45, "pitch": -2}, {"at": 0.13, "gain": 0.8, "pitch": 1}],
                       "room": 0.08, "room_size": 0.15},
    "wood knock": {"cat": "Clicks & taps", "about": "Knuckle on a wooden table", **_CLICK, "duration": 0.12, "decay": 0.1, "tone": 0.15,
                   "filter_start": 900, "filter_end": 500, "resonance": 1, "transient": 0.6, "transient_tone": 3000,
                   "modes": _m((310, 0.05, 1), (760, 0.03, 0.55), (1480, 0.015, 0.3), (2900, 0.006, 0.15)), "body": 1.2,
                   "room": 0.12, "room_size": 0.3, "low_cut": 80},
    "glass tap": {"cat": "Clicks & taps", "about": "Fingernail on a glass", **_CLICK, "duration": 0.6, "decay": 0.55, "tone": 0.05,
                  "filter_start": 6000, "filter_end": 6000, "resonance": 1, "transient": 0.7, "transient_tone": 9000,
                  "modes": _m((2120, 0.35, 1), (5640, 0.22, 0.45), (8830, 0.12, 0.25), (12100, 0.06, 0.15)), "body": 1,
                  "room": 0.15, "room_size": 0.5, "low_cut": 400},
    # ---- whooshes
    "whoosh": {"cat": "Whooshes", "about": "Air swoosh for a transition", "wave": "noise", "noise": 1, "duration": 0.7, "attack": 0.3, "decay": 0.35,
               "filter_type": "bandpass", "resonance": 0.9, "filter_start": 350, "filter_end": 4500, "curve": 1.4, "width": 0.7, "room": 0.12, "room_size": 0.6},
    "swish": {"cat": "Whooshes", "about": "Short fast swipe", "wave": "noise", "noise": 1, "duration": 0.28, "attack": 0.08, "decay": 0.18,
              "filter_type": "bandpass", "resonance": 1.2, "filter_start": 1500, "filter_end": 8000, "curve": 0.7, "width": 0.6, "low_cut": 300},
    "riser": {"cat": "Whooshes", "about": "Builds tension before a reveal", "wave": "saw", "noise": 0.45, "duration": 2.5, "attack": 2.2, "decay": 0.08,
              "pitch_start": 110, "pitch_end": 880, "filter_start": 500, "filter_end": 9000, "resonance": 2, "curve": 1.8,
              "vibrato_rate": 6, "vibrato_depth": 0.15, "width": 0.8, "room": 0.2, "room_size": 1.2},
    "tape stop": {"cat": "Whooshes", "about": "Slowing-down tape, a comedic stop", "wave": "saw", "noise": 0.1, "duration": 0.8, "attack": 0.005, "decay": 0.2,
                  "pitch_start": 330, "pitch_end": 30, "curve": 2.2, "filter_start": 5000, "filter_end": 300, "volume": 0.6},
    # ---- hits & drops
    "impact": {"cat": "Hits & drops", "about": "Deep boom for a hit or title slam", "wave": "sine", "noise": 0.25, "duration": 1.2, "attack": 0.001, "decay": 1.1,
               "pitch_start": 120, "pitch_end": 38, "curve": 0.4, "filter_start": 2500, "filter_end": 200, "transient": 0.8, "transient_tone": 3000,
               "modes": _m((62, 0.5, 0.6)), "drive": 0.35, "room": 0.25, "room_size": 1.4, "width": 0.6},
    "drop": {"cat": "Hits & drops", "about": "Falling tone, the end of a build", "wave": "sine", "noise": 0.05, "duration": 0.9, "attack": 0.005, "decay": 0.6,
             "pitch_start": 600, "pitch_end": 45, "curve": 0.6, "filter_start": 6000, "filter_end": 800, "drive": 0.2, "room": 0.15, "room_size": 0.8},
    "heartbeat": {"cat": "Hits & drops", "about": "Low double thump", "wave": "sine", "duration": 0.18, "attack": 0.003, "decay": 0.16,
                  "pitch_start": 85, "pitch_end": 45, "curve": 0.5, "filter_start": 600, "filter_end": 300, "transient": 0.2, "transient_tone": 1200,
                  "hits": [{"at": 0.24, "gain": 0.7, "pitch": -1}], "drive": 0.2, "width": 0},
    # ---- nature & ambience: particles + a noise bed + slow movement, as seamless loops
    "rain": {"cat": "Nature & ambience", "about": "Steady rain (loops)", **_AMB, "duration": 8, "tone": 0.45, "noise_color": "pink",
             "filter_start": 6500, "filter_end": 6500, "low_cut": 250, "grain_rate": 900, "grain_kind": "drop", "grain_freq": 2800,
             "grain_spread": 14, "grain_len": 0.012, "grain_level": 1.2, "grain_vary": 0.75, "motion": 0.15, "motion_rate": 0.15,
             "room": 0.1, "room_size": 0.3},
    "heavy rain": {"cat": "Nature & ambience", "about": "Downpour (loops)", **_AMB, "duration": 8, "tone": 0.8, "noise_color": "pink",
                   "filter_start": 5000, "filter_end": 5000, "low_cut": 120, "grain_rate": 3200, "grain_kind": "drop", "grain_freq": 2200,
                   "grain_spread": 16, "grain_len": 0.01, "grain_level": 1, "grain_vary": 0.7, "motion": 0.25, "motion_rate": 0.12,
                   "room": 0.15, "room_size": 0.5},
    "drizzle": {"cat": "Nature & ambience", "about": "Light rain, separate drops (loops)", **_AMB, "duration": 8, "tone": 0.12, "noise_color": "pink",
                "filter_start": 7000, "filter_end": 7000, "low_cut": 400, "grain_rate": 140, "grain_kind": "drop", "grain_freq": 3400,
                "grain_spread": 12, "grain_len": 0.014, "grain_level": 1.4, "grain_vary": 0.8, "room": 0.12, "room_size": 0.35},
    "wind": {"cat": "Nature & ambience", "about": "Gusty wind (loops)", **_AMB, "duration": 10, "noise_color": "brown", "filter_type": "bandpass",
             "filter_start": 520, "filter_end": 520, "resonance": 2.2, "motion": 0.75, "motion_rate": 0.22, "low_cut": 60, "room": 0.1, "room_size": 0.8},
    "fire": {"cat": "Nature & ambience", "about": "Crackling campfire (loops)", **_AMB, "duration": 8, "tone": 0.5, "noise_color": "brown",
             "filter_start": 800, "filter_end": 800, "low_cut": 50, "motion": 0.35, "motion_rate": 1.2, "grain_rate": 28, "grain_kind": "crackle",
             "grain_freq": 3000, "grain_len": 0.004, "grain_level": 1.5, "grain_vary": 0.9, "width": 0.6},
    "water drips": {"cat": "Nature & ambience", "about": "Drips in a cave (loops)", **_AMB, "duration": 8, "tone": 0.0, "grain_rate": 2.5,
                    "grain_kind": "bubble", "grain_freq": 950, "grain_spread": 10, "grain_len": 0.07, "grain_level": 1, "grain_vary": 0.4,
                    "room": 0.45, "room_size": 1.6, "low_cut": 200},
    "bubbles": {"cat": "Nature & ambience", "about": "Bubbling water", "wave": "noise", "noise": 1, "tone": 0.0, "duration": 2, "attack": 0.05, "decay": 0.4,
                "grain_rate": 22, "grain_kind": "bubble", "grain_freq": 600, "grain_spread": 14, "grain_len": 0.05, "grain_level": 1,
                "grain_vary": 0.5, "width": 0.6, "room": 0.1, "room_size": 0.3, "low_cut": 150},
    "applause": {"cat": "Nature & ambience", "about": "Crowd clapping (loops)", **_AMB, "duration": 8, "tone": 0.2, "noise_color": "pink",
                 "filter_type": "bandpass", "filter_start": 1500, "filter_end": 1500, "resonance": 0.8, "grain_rate": 70, "grain_kind": "clap",
                 "grain_freq": 1300, "grain_spread": 9, "grain_len": 0.025, "grain_level": 1, "grain_vary": 0.5, "motion": 0.1,
                 "motion_rate": 0.3, "room": 0.25, "room_size": 0.9, "low_cut": 200},
    # ---- voices & creatures: a vocal-cord source through vowel formants
    "scream": {"cat": "Voices & creatures", "about": "High scream, 'aaah' (game / cartoon style)", "wave": "voice", "duration": 1.6, "attack": 0.04, "decay": 0.45,
               "pitch_start": 640, "pitch_end": 520, "pitch_points": [[0, -6], [0.12, 3], [0.6, 2], [1, -5]], "vowel": "a", "formant_shift": 3,
               "jitter": 0.5, "roughness": 0.55, "breath": 0.2, "vibrato_rate": 6, "vibrato_depth": 0.3, "drive": 0.5,
               "filter_start": 9000, "filter_end": 7000, "room": 0.15, "room_size": 0.6, "low_cut": 150},
    "yell": {"cat": "Voices & creatures", "about": "Man shouting 'hey!'", "wave": "voice", "duration": 0.7, "attack": 0.02, "decay": 0.3,
             "pitch_start": 230, "pitch_end": 170, "pitch_points": [[0, -3], [0.2, 2], [1, -2]], "vowel": "e", "vowel_end": "e", "formant_shift": 0,
             "jitter": 0.3, "roughness": 0.35, "breath": 0.15, "drive": 0.35, "room": 0.12, "room_size": 0.5, "low_cut": 90},
    "monster growl": {"cat": "Voices & creatures", "about": "Deep creature growl", "wave": "voice", "duration": 1.6, "attack": 0.12, "decay": 0.5,
                      "pitch_start": 70, "pitch_end": 55, "vowel": "o", "vowel_end": "u", "formant_shift": -7, "jitter": 0.7, "roughness": 1,
                      "breath": 0.35, "drive": 0.7, "motion": 0.3, "motion_rate": 7, "room": 0.2, "room_size": 0.9, "low_cut": 40},
    "ghost moan": {"cat": "Voices & creatures", "about": "Spooky 'oooooh'", "wave": "voice", "duration": 2.6, "attack": 0.7, "decay": 0.9,
                   "pitch_start": 330, "pitch_end": 240, "pitch_points": [[0, 0], [0.4, 3], [1, -2]], "vowel": "o", "vowel_end": "u",
                   "formant_shift": 1, "breath": 0.55, "vibrato_rate": 4.5, "vibrato_depth": 0.6, "room": 0.55, "room_size": 2.2, "width": 0.7},
    "robot voice": {"cat": "Voices & creatures", "about": "Robotic 'aaa-iii' beep voice", "wave": "square", "duration": 0.8, "attack": 0.01, "decay": 0.15,
                    "pitch_start": 120, "pitch_end": 120, "vowel": "a", "vowel_end": "i", "crush": 0.3, "tremolo": 0.3, "vibrato_rate": 30,
                    "room": 0.1, "room_size": 0.3, "volume": 0.7},
    # ---- UI & game
    "pop": {"cat": "UI & game", "about": "Bubble pop for text or stickers appearing", "wave": "sine", "duration": 0.08, "attack": 0.001, "decay": 0.07,
            "pitch_start": 380, "pitch_end": 1300, "curve": 0.5, "transient": 0.3, "transient_tone": 3000, "low_cut": 150},
    "blip": {"cat": "UI & game", "about": "Short 8-bit beep", "wave": "square", "duration": 0.12, "attack": 0.003, "decay": 0.06,
             "pitch_start": 880, "pitch_end": 880, "filter_start": 6000, "filter_end": 6000, "volume": 0.55},
    "coin": {"cat": "UI & game", "about": "Reward / success chime", "wave": "square", "duration": 0.35, "attack": 0.002, "decay": 0.25,
             "pitch_start": 988, "pitch_end": 1976, "curve": 5, "filter_start": 8000, "filter_end": 8000, "volume": 0.5},
    "laser": {"cat": "UI & game", "about": "Sci-fi zap", "wave": "saw", "duration": 0.35, "attack": 0.002, "decay": 0.3,
              "pitch_start": 2400, "pitch_end": 180, "curve": 0.5, "filter_start": 9000, "filter_end": 2000, "resonance": 3, "volume": 0.55},
    "glitch": {"cat": "UI & game", "about": "Digital stutter for glitch cuts", "wave": "square", "noise": 0.4, "duration": 0.4, "attack": 0.0, "decay": 0.05,
               "pitch_start": 300, "pitch_end": 1200, "tremolo": 1, "vibrato_rate": 28, "vibrato_depth": 7, "crush": 0.7, "volume": 0.5},
    "error": {"cat": "UI & game", "about": "Wrong / buzzer", "wave": "saw", "duration": 0.45, "attack": 0.005, "decay": 0.1,
              "pitch_start": 150, "pitch_end": 130, "filter_start": 1800, "filter_end": 1200, "tremolo": 0.6, "vibrato_rate": 18, "volume": 0.55},
    # ---- tones & magic
    "ding": {"cat": "Tones & magic", "about": "Bright bell / notification", "wave": "sine", "tone": 0.4, "duration": 1.2, "attack": 0.001, "decay": 1.15,
             "pitch_start": 1568, "pitch_end": 1568, "transient": 0.25, "transient_tone": 8000,
             "modes": _m((1568, 0.6, 1), (4280, 0.25, 0.35), (7900, 0.1, 0.15)), "room": 0.2, "room_size": 0.9, "width": 0.5},
    "sparkle": {"cat": "Tones & magic", "about": "Magic shimmer", "wave": "triangle", "noise": 0.15, "duration": 1.1, "attack": 0.02, "decay": 0.9,
                "pitch_start": 2000, "pitch_end": 3200, "vibrato_rate": 22, "vibrato_depth": 2, "tremolo": 0.5, "echo": 0.4, "echo_delay": 0.08,
                "filter_start": 12000, "filter_end": 9000, "room": 0.3, "room_size": 1.2, "width": 0.9, "volume": 0.5},
}
_META = ("cat", "about")
ALIASES = {"click": "mouse click", "tap": "trackpad tap", "key": "keyboard key", "knock": "wood knock", "shutter": "camera shutter",
           "tick": "ui tick", "growl": "monster growl", "moan": "ghost moan", "robot": "robot voice", "clapping": "applause",
           "campfire": "fire", "downpour": "heavy rain", "drip": "water drips"}


def _base(key: str) -> SfxParams:
    return SfxParams(name=key, **{x: y for x, y in PRESETS[key].items() if x not in _META})


def presets() -> list[dict]:
    return [{"key": k, "cat": v["cat"], "about": v["about"], "params": _base(k).model_dump()} for k, v in PRESETS.items()]


def preset(key: str, pitch: float = 0.0, length: float | None = None, **over) -> SfxParams:
    """A preset, moved `pitch` semitones and stretched to `length` seconds, with any field overridden."""
    k = key.lower().replace("_", " ").replace("-", " ").strip()
    k = ALIASES.get(k, k)
    if k not in PRESETS:
        raise ValueError(f"Unknown sound '{key}'. Pick one of: {', '.join(PRESETS)}")
    base = _base(k)
    return adjust(SfxParams(**{**base.model_dump(), **over}) if over else base, pitch, length)


def adjust(p: SfxParams, pitch: float = 0.0, length: float | None = None) -> SfxParams:
    """The two big knobs. Pitch: every pitch, filter, particle and resonance moves by semitones (a voice's vowels move
    a third as much, so it sounds higher, not like another vowel). Length: every time stretches (a loop just gets
    longer; its particles keep their size)."""
    d = p.model_dump()
    if pitch:
        k = 2 ** (pitch / 12)
        for f, lo, hi in (("pitch_start", 20, 12000), ("pitch_end", 20, 12000), ("filter_start", 40, 20000),
                          ("filter_end", 40, 20000), ("transient_tone", 500, 16000), ("grain_freq", 60, 12000)):
            d[f] = max(lo, min(hi, d[f] * k))
        d["body_tune"] = max(-24.0, min(24.0, d["body_tune"] + pitch))
        d["formant_shift"] = max(-12.0, min(12.0, d["formant_shift"] + pitch / 3))
    if length:
        length = max(0.01, min(MAX_LEN, float(length)))
        k = length / d["duration"]
        d["duration"] = length
        if not d["loop"]:
            d["attack"], d["decay"] = min(length, d["attack"] * k), max(0.002, min(length, d["decay"] * k))
            d["body_ring"] = max(0.2, min(5.0, d["body_ring"] * k))
            d["hits"] = [{**h, "at": min(MAX_LEN, h["at"] * k)} for h in d["hits"]]
    return SfxParams(**d)


def vary(p: SfxParams, amount: float = 0.25, seed: int | None = None) -> SfxParams:
    """A natural variation: the whole sound shifts a little, and each resonance, the particles, the voice, the tick and
    the release move on their own (two real clicks never sound identical). 0.1 = subtle, 0.25 = clear, 0.6 = wild."""
    r = random.Random(seed)
    a = max(0.0, min(1.0, amount))
    d = adjust(p, pitch=r.gauss(0, 6 * a)).model_dump()
    j = lambda v, lo, hi, s=a: max(lo, min(hi, v * math.exp(r.gauss(0, s))))
    c = lambda v, lo, hi, s: max(lo, min(hi, v + r.gauss(0, s)))
    d["filter_start"], d["filter_end"] = j(d["filter_start"], 40, 20000), j(d["filter_end"], 40, 20000)
    d["resonance"] = j(d["resonance"], 0.3, 20, a / 2)
    if not d["loop"]:
        d["duration"] = j(d["duration"], 0.01, MAX_LEN, a / 2)
        d["attack"], d["decay"] = min(d["duration"], d["attack"]), max(0.002, min(d["duration"], j(d["decay"], 0.002, MAX_LEN, a / 2)))
    d["curve"] = j(d["curve"], 0.2, 5, a / 2)
    if d["wave"] != "noise":
        d["noise"] = c(d["noise"], 0.0, 1.0, a / 3)
    d["modes"] = [{"freq": max(20, min(18000, m["freq"] * 2 ** (r.gauss(0, 4 * a) / 12))),
                   "decay": j(m["decay"], 0.0005, 5), "gain": max(0.0, min(1.0, m["gain"] * math.exp(r.gauss(0, a))))} for m in d["modes"]]
    if d["transient"]:
        d["transient"] = max(0.0, min(1.0, d["transient"] * math.exp(r.gauss(0, a / 2))))
        d["transient_tone"] = j(d["transient_tone"], 500, 16000)
    d["hits"] = [{"at": j(h["at"], 0.0, MAX_LEN, a / 2), "gain": j(h["gain"], 0, 1.5), "pitch": c(h["pitch"], -24, 24, 2 * a)} for h in d["hits"]]
    if d["grain_rate"]:
        d["grain_rate"] = j(d["grain_rate"], 0, 6000, a)
        d["grain_freq"], d["grain_len"] = j(d["grain_freq"], 60, 12000, a / 2), j(d["grain_len"], 0.001, 0.4, a / 2)
    if d["vowel"] != "none":
        d["formant_shift"] = c(d["formant_shift"], -12, 12, 2 * a)
        d["jitter"], d["roughness"] = c(d["jitter"], 0, 1, a / 3), c(d["roughness"], 0, 1, a / 3)
    d["pitch_points"] = [[t, c(v, -24, 24, 2 * a)] for t, v in (pp[:2] for pp in d["pitch_points"] if len(pp) >= 2)]
    if d["motion"]:
        d["motion_rate"] = j(d["motion_rate"], 0.02, 20, a / 2)
    d["seed"] = r.randrange(1 << 30)
    return SfxParams(**d)


def variations(p: SfxParams, n: int = 6, amount: float = 0.25, seed: int | None = None) -> list[SfxParams]:
    """`n` takes of the same sound, each a different natural variation."""
    r = random.Random(seed)
    return [vary(p, amount, r.randrange(1 << 30)).model_copy(update={"name": p.name}) for _ in range(max(1, min(24, n)))]


# ---- the synth ------------------------------------------------------------------------------------------------------

def _sweep(a: float, b: float, n: int, curve: float) -> np.ndarray:
    """From a to b, moving evenly in octaves (the way ears hear pitch and brightness)."""
    x = np.linspace(0.0, 1.0, n) ** curve
    return np.exp(np.log(a) + (np.log(b) - np.log(a)) * x)


def _points(pts: list[list[float]], n: int, default: float) -> np.ndarray:
    """A curve through [[time 0-1, value], ...] (straight lines between points), or `default` everywhere."""
    pts = sorted((max(0.0, min(1.0, float(q[0]))), float(q[1])) for q in pts if len(q) >= 2)
    if not pts:
        return np.full(n, default)
    return np.interp(np.linspace(0.0, 1.0, n), [q[0] for q in pts], [q[1] for q in pts])


def _drift(n: int, rate: float, rng: np.random.Generator) -> np.ndarray:
    """Smooth random movement in -1..1 at about `rate` changes per second."""
    k = max(2, int(n / SR * rate) + 3)
    pts = rng.uniform(-1, 1, k)
    x = np.interp(np.linspace(0, k - 1, n), np.arange(k), pts)
    w = max(1, int(SR / max(rate, 0.02) / 4))
    if w > 1 and n > w:  # round off the corners: a running mean (cumulative sum, so any width costs the same)
        c = np.cumsum(np.pad(x, (w, w), mode="edge"))
        x = ((c[w:] - c[:-w]) / w)[w // 2:w // 2 + n]
    return x / (np.max(np.abs(x)) or 1.0)


def _colored(n: int, color: str, rng: np.random.Generator) -> np.ndarray:
    x = rng.uniform(-1, 1, n)
    if color == "white" or n < 16:
        return x
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    f[0] = f[1]
    X *= (1 / np.sqrt(f)) if color == "pink" else (1 / f)
    y = np.fft.irfft(X, n)
    return y / (np.max(np.abs(y)) or 1.0)


def _svf(x: np.ndarray, cutoff: np.ndarray, q: float, kind: str) -> np.ndarray:
    """Resonant state-variable filter (topology-preserving transform, stable at any cutoff), coefficients per 32 samples."""
    k = 1.0 / q
    ic1 = ic2 = 0.0
    B = 32
    xs = x.tolist()
    cs = cutoff[::B].tolist()
    o = [0.0] * len(xs)
    for bi, i in enumerate(range(0, len(xs), B)):
        g = math.tan(math.pi * min(cs[bi], SR * 0.45) / SR)
        a1 = 1.0 / (1.0 + g * (g + k))
        a2, a3 = g * a1, g * g * a1
        for jj in range(i, min(i + B, len(xs))):
            v0 = xs[jj]
            v3 = v0 - ic2
            v1 = a1 * ic1 + a2 * v3
            v2 = ic2 + a2 * ic1 + a3 * v3
            ic1 = 2.0 * v1 - ic1
            ic2 = 2.0 * v2 - ic2
            o[jj] = v2 if kind == "lowpass" else v1 * k if kind == "bandpass" else v0 - k * v1 - v2
    return np.asarray(o)


def _glottal(phase: np.ndarray) -> np.ndarray:
    """Vocal-cord pulses (Rosenberg shape, then its slope, as the mouth radiates it): a buzzy, voice-like source."""
    op, cl = 0.6, 0.12
    p = np.where(phase < op, 0.5 * (1 - np.cos(np.pi * phase / op)),
                 np.where(phase < op + cl, np.cos(np.pi * (phase - op) / (2 * cl)), 0.0))
    d = np.diff(p, prepend=p[0])
    return d / (np.max(np.abs(d)) or 1.0)


def _formants(x: np.ndarray, p: SfxParams, n: int) -> np.ndarray:
    """Three parallel band-pass filters at the vowel's formants (morphing to `vowel_end`)."""
    a = FORMANTS[p.vowel]
    b = FORMANTS[p.vowel_end] if p.vowel_end and p.vowel_end != "none" else a
    k = 2 ** (p.formant_shift / 12)
    out = np.zeros(n)
    for i in range(3):
        f = _sweep(a[i] * k, b[i] * k, n, p.curve)
        bw = FORMANT_BW[i] * (1 + p.roughness)
        q = max(0.7, float(np.mean(f)) / bw)
        out += FORMANT_GAIN[i] * _svf(x, f, q, "bandpass")
    return out * 2.0


def _voice(p: SfxParams, n: int, rng: np.random.Generator, drift: np.ndarray) -> np.ndarray:
    t = np.arange(n) / SR
    freq = _sweep(p.pitch_start, p.pitch_end, n, p.curve)
    if p.pitch_points:
        freq = freq * 2 ** (_points(p.pitch_points, n, 0.0) / 12)
    if p.vibrato_depth and p.vibrato_rate:
        freq = freq * 2 ** (p.vibrato_depth / 12 * np.sin(2 * np.pi * p.vibrato_rate * t))
    if p.jitter:
        freq = freq * 2 ** (p.jitter * 0.8 * _drift(n, 18, rng) / 12)
    freq = np.minimum(freq, SR * 0.45)
    phase = np.cumsum(freq / SR) % 1.0
    tone = {"sine": lambda: np.sin(2 * np.pi * phase), "triangle": lambda: 4 * np.abs(phase - 0.5) - 1,
            "square": lambda: np.where(phase < 0.5, 0.7, -0.7), "saw": lambda: (2 * phase - 1) * 0.7,
            "voice": lambda: _glottal(phase)}.get(p.wave, lambda: np.zeros(n))()
    noise_amt = 1.0 if p.wave == "noise" else p.noise
    x = tone * (1 - noise_amt) + _colored(n, p.noise_color, rng) * noise_amt
    if p.roughness:  # a sub-harmonic flutter + noise riding on the pulses: rasp, growl, scream
        sub = np.sin(np.pi * np.cumsum(freq / SR) * 2)  # half the pitch
        x = x * (1 + p.roughness * 0.8 * sub) + p.roughness * 0.35 * rng.uniform(-1, 1, n) * np.abs(tone)
    if p.breath:
        x = x * (1 - p.breath * 0.7) + p.breath * _colored(n, "pink", rng) * 0.8
    if p.vowel != "none":
        x = _formants(x, p, n)
    cut = _sweep(p.filter_start, p.filter_end, n, p.curve)
    if p.motion:
        cut = np.clip(cut * 2 ** (p.motion * drift), 40, 20000)
    if p.filter_type != "lowpass" or float(np.min(cut)) < 19000:
        x = _svf(x, cut, p.resonance, p.filter_type)
        if p.filter_type == "bandpass":
            x *= 1.5 + 0.3 * p.resonance  # a band-pass keeps little energy; bring it back
    if p.tremolo:
        rate = p.vibrato_rate or 12.0
        x = x * (1 - p.tremolo * 0.9 * (np.sin(2 * np.pi * rate * t) > 0))
    if p.crush:
        steps = 2 ** (12 - 9 * p.crush)
        hold = max(1, int(1 + p.crush * 12))
        x = np.repeat(np.round(x * steps)[::hold] / steps, hold)[:n]
    return x


def _grain(kind: str, f: float, length: float, rng: np.random.Generator) -> np.ndarray:
    m = max(16, int(length * SR))
    t = np.arange(m) / SR
    if kind in ("drop", "bubble"):  # impact tick + a small air bubble ringing with a rising pitch (Minnaert)
        y = np.zeros(m)
        if kind == "drop":
            y += rng.uniform(-1, 1, m) * np.exp(-t / 0.0004) * 0.6
        if kind == "bubble" or rng.random() < 0.6:
            rise = 1.0 + (2.5 if kind == "bubble" else 1.2) * t / length
            y += np.sin(2 * np.pi * np.cumsum(f * rise) / SR) * np.exp(-t / (length / 3)) * np.minimum(1, t / 0.0004)
    elif kind == "crackle":
        y = np.diff(rng.uniform(-1, 1, m) * np.exp(-t / (length / 4)), prepend=0.0)
    elif kind == "clap":  # a few quick bursts (palms meeting), band-passed
        env = sum(np.where(t >= o, np.exp(-(t - o) / 0.0025), 0.0) * g for o, g in ((0, 1), (0.003, 0.6), (0.0075, 0.4)))
        y = _svf(rng.uniform(-1, 1, m) * env * np.exp(-t / (length / 2)), np.full(m, f), 1.2, "bandpass")
    else:  # tick
        y = _svf(rng.uniform(-1, 1, m) * np.exp(-t / (length / 4)), np.full(m, f), 2.0, "bandpass")
    return y / (np.max(np.abs(y)) or 1.0)


def _particles(p: SfxParams, n: int, rng: np.random.Generator) -> np.ndarray:
    """Stereo particle layer: a bank of varied particles placed at random times, loudness and stereo positions."""
    count = int(rng.poisson(p.grain_rate * n / SR))
    out = np.zeros((n, 2))
    if not count:
        return out
    bank = [_grain(p.grain_kind, min(SR * 0.4, p.grain_freq * 2 ** (rng.uniform(-0.5, 0.5) * p.grain_spread / 12)),
                   p.grain_len * math.exp(rng.normal(0, 0.3)), rng) for _ in range(48)]
    at = rng.integers(0, n, count)
    which = rng.integers(0, len(bank), count)
    gain = rng.uniform(0.05, 1.0, count) ** (1 + 4 * p.grain_vary)
    pan = np.clip(rng.normal(0, 0.45 * (0.3 + p.width), count), -1, 1)
    gl, gr = gain * np.sqrt((1 - pan) / 2), gain * np.sqrt((1 + pan) / 2)
    for s, w, l, r in zip(at.tolist(), which.tolist(), gl.tolist(), gr.tolist()):
        g = bank[w]
        e = min(n, s + len(g))
        out[s:e, 0] += g[:e - s] * l
        out[s:e, 1] += g[:e - s] * r
    rms = float(np.sqrt(np.mean(out ** 2))) or 1.0
    return out * (0.22 * p.grain_level / rms)


def _amp(p: SfxParams, n: int, drift: np.ndarray, loop: bool) -> np.ndarray:
    """Loudness over time: attack / fade-out (not for loops), the curve, the drift."""
    env = np.ones(n)
    if not loop:
        a = int(min(p.attack, p.duration) * SR)
        if a > 1:
            env[:a] = np.linspace(0, 1, a) ** 1.5
        d = int(min(p.decay, p.duration) * SR)
        if d > 1:
            env[-d:] *= np.linspace(1, 0, d) ** 2
    if p.env_points:
        env *= np.clip(_points(p.env_points, n, 1.0), 0, 1.5)
    if p.motion:
        env *= np.exp(p.motion * 1.2 * drift)
    return env


def _one_hit(p: SfxParams, n_voice: int, rng: np.random.Generator, drift: np.ndarray, loop: bool) -> np.ndarray:
    """Voice + transient + body for one strike, mono."""
    ring = p.body_ring
    tail = max([m.decay * ring * 5 for m in p.modes if m.gain > 0] or [0.0])  # body rings ~-43 dB down
    n = max(n_voice, int(min(MAX_LEN, tail) * SR))
    x = np.zeros(n)
    if p.tone:
        x[:n_voice] += _voice(p, n_voice, rng, drift) * _amp(p, n_voice, drift, loop) * p.tone
    if p.transient:
        m = max(8, int(0.0015 * SR))
        tick = rng.uniform(-1, 1, m) * np.linspace(1, 0, m) ** 3
        x[:m] += _svf(tick, np.full(m, p.transient_tone), 0.9, "bandpass") * 3.0 * p.transient
    if p.modes and p.body:
        t = np.arange(n) / SR
        k = 2 ** (p.body_tune / 12)
        att = np.minimum(1.0, t / 0.0003)  # 0.3 ms: no click from a sine starting at full level
        for md in p.modes:
            f = md.freq * k
            if md.gain <= 0 or f >= SR * 0.45:
                continue
            x += md.gain * p.body * 0.6 * att * np.exp(-t / (md.decay * ring)) * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))
    return x


def _shift(x: np.ndarray, semis: float) -> np.ndarray:
    if not semis:
        return x
    return np.interp(np.arange(0, len(x) - 1, 2 ** (semis / 12)), np.arange(len(x)), x)


def _fft_conv(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    n = len(x) + len(ir) - 1
    size = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[:n]


def _room(x: np.ndarray, p: SfxParams, rng: np.random.Generator) -> np.ndarray:
    """Stereo reverb: two decorrelated noise tails (exponential decay, softened highs) convolved with the sound."""
    m = int(p.room_size * SR)
    t = np.arange(m) / SR
    pre = int(0.008 * SR)
    out = []
    for _ in range(2):
        ir = rng.standard_normal(m) * np.exp(-6.9 * t / p.room_size)
        ir = np.concatenate([np.zeros(pre), np.convolve(ir, np.ones(4) / 4, mode="same")])
        out.append(_fft_conv(x, ir))
    wet = np.stack(out, axis=1)
    rms_dry, rms_wet = float(np.sqrt(np.mean(x ** 2))) or 1.0, float(np.sqrt(np.mean(wet ** 2))) or 1.0
    return wet * (rms_dry / rms_wet) * 0.8


def _low_cut(x: np.ndarray, hz: float) -> np.ndarray:
    """Remove rumble below hz (a gentle ramp over one octave, done in the frequency domain)."""
    size = 1 << (len(x) - 1).bit_length()
    X = np.fft.rfft(x, size, axis=0)
    f = np.fft.rfftfreq(size, 1 / SR)
    gain = np.clip(np.log2(np.maximum(f, 1e-6) / (hz / 2)), 0, 1)
    return np.fft.irfft(X * gain[:, None], size, axis=0)[:len(x)]


def synth(p: SfxParams) -> np.ndarray:
    """Stereo float samples (n, 2) in -1..1. A `loop` sound is exactly `duration` long and repeats without a seam."""
    rng = np.random.default_rng(p.seed)
    L = max(1, int(p.duration * SR))
    xf = int(min(1.5, p.duration * 0.25) * SR) if p.loop else 0  # loops render past the end, then fold it onto the start
    n_voice = L + xf
    drift = _drift(n_voice, p.motion_rate, np.random.default_rng(p.seed + 1)) if p.motion else np.zeros(n_voice)
    parts = [(0, _one_hit(p, n_voice, rng, drift, p.loop))]
    for h in p.hits:
        if h.gain > 0:
            parts.append((int(h.at * SR), _shift(_one_hit(p, n_voice, rng, drift, p.loop), h.pitch) * h.gain))
    n = max(off + len(x) for off, x in parts)
    x = np.zeros(n)
    for off, y in parts:
        x[off:off + len(y)] += y
    # stereo: mid / side with a 0.7 ms decorrelated side, then the particles (already placed in stereo)
    d = max(1, int(0.0007 * SR))
    side = np.concatenate([np.zeros(d), x[:-d]]) - x
    st = np.stack([x + side * p.width * 0.5, x - side * p.width * 0.5], axis=1)
    if p.grain_rate and p.grain_level:
        g = _particles(p, n_voice, rng) * _amp(p, n_voice, drift, p.loop)[:, None]
        st[:n_voice] += g
    if p.drive:
        k = 1 + p.drive * 8
        st = np.tanh(st * k / (np.max(np.abs(st)) or 1)) / math.tanh(k)
    if p.echo:
        dd = int(p.echo_delay * SR)
        y = np.zeros((len(st) + dd * 4, 2))
        y[:len(st)] = st
        for i in range(1, 5):
            y[i * dd:i * dd + len(st)] += st * (p.echo * 0.6) ** i
        st = y
    if p.room:
        wet = _room(st.mean(axis=1), p, rng)
        if p.width < 1:  # narrow the reverb too
            mid = wet.mean(axis=1, keepdims=True)
            wet = mid + (wet - mid) * (0.4 + 0.6 * p.width)
        full = np.zeros((max(len(st), len(wet)), 2))
        full[:len(st)] += st
        full[:len(wet)] += wet * p.room
        st = full
    st = _low_cut(st, p.low_cut)
    if p.loop:
        # fold everything after the loop's end (the tail, the reverb) back onto its start with an equal-power fade,
        # so the last sample flows into the first
        if len(st) < L + xf:
            st = np.concatenate([st, np.zeros((L + xf - len(st), 2))])
        out = st[:L].copy()
        if xf:
            th = np.linspace(0, np.pi / 2, xf)[:, None]
            out[:xf] = out[:xf] * np.sin(th) + st[L:L + xf] * np.cos(th)
        st = out
    else:
        # trim the silent tail, short fade
        loud = np.nonzero(np.max(np.abs(st), axis=1) > 1e-4 * (np.max(np.abs(st)) or 1))[0]
        st = st[:max(L, (loud[-1] + 1) if len(loud) else 1)]
        fade = min(len(st), int(0.004 * SR))
        st[-fade:] *= np.linspace(1, 0, fade)[:, None]
    peak = float(np.max(np.abs(st))) or 1.0
    return (st / peak * p.volume).astype(np.float32)


def render(p: SfxParams) -> bytes:
    """A 44.1 kHz 16-bit stereo WAV (bytes)."""
    pcm = (np.clip(synth(p), -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def save(p: SfxParams, folder: Path) -> Path:
    """Write the sound into `folder` under a name that is stable for the same settings."""
    folder.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(json.dumps(p.model_dump(), sort_keys=True).encode()).hexdigest()[:8]
    slug = "".join(c if c.isalnum() else "-" for c in p.name.lower()).strip("-")[:24] or "sound"
    out = folder / f"sfx-{slug}-{h}.wav"
    if not out.exists():
        out.write_bytes(render(p))
    return out


# ---- AI: describe a sound, get the numbers -----------------------------------------------------------------------

AI_SYSTEM = """You design sound effects for video editing on a layered synthesizer. You never produce audio; you
choose its settings. Layers:
- voice: a source (sine/triangle/square/saw, `voice` = vocal cords, or noise in white/pink/brown) through a resonant
  filter (lowpass / highpass / bandpass, Q = resonance) sweeping filter_start -> filter_end; pitch sweep
  pitch_start -> pitch_end plus `pitch_points` [[time 0-1, semitones], ...]; attack / decay; vibrato, tremolo, crush.
  `vowel` (a e i o u er, morphing to `vowel_end`) runs it through formant filters so it speaks; formant_shift = vocal
  tract size (+3 woman / child / cartoon, -6 monster); jitter, roughness (rasp, growl, scream), breath. `tone` = level.
- transient: a 1-2 ms tick (`transient`, `transient_tone` Hz). Every click, tap, knock and hit has one.
- body: `modes` [{freq, decay, gain}], damped resonances of the struck object (body / body_tune / body_ring scale them).
- particles: grain_rate per second of grain_kind (drop = rain on a surface, crackle = fire / vinyl, tick = gravel /
  clock / insects, clap = applause, bubble = water), grain_freq, grain_spread (semitones), grain_len, grain_level,
  grain_vary (mostly quiet with a few loud ones at 1).
- movement: env_points [[time 0-1, level], ...] (a loudness curve), motion + motion_rate (slow random drift of
  loudness and filter: gusts 0.1-0.4 Hz, flicker / shake 2-8 Hz).
- hits [{at, gain, pitch}]: the sound again (a button's release ~0.06-0.11 s later, a second thump).
- finishing: drive, echo, room + room_size (reverb), width, low_cut, volume, seed (another take). `loop` = a seamless
  loop that plays under a whole scene (ambiences); loops use duration 6-12 s and no attack / decay.
Recipes:
- Clicks / taps / keys: tone 0.2-0.4 bandpass noise (resonance 1.5-3), transient 0.5-1, 2-3 modes. Plastic 1.5-6 kHz,
  decays 0.002-0.006 s; mechanical keys add a 400-600 Hz mode; wood 200-1500 Hz 0.015-0.05 s; glass / metal high
  modes with long decays and inharmonic ratios. Small room 0.05-0.12.
- Rain: loop, pink noise bed (tone 0.1-0.8, lowpass 5-7 kHz, low_cut 120-400) + drop particles 100 (drizzle) to 3000
  (downpour) per second at 2-3.5 kHz, spread 12-16, len ~0.012, motion 0.15-0.25 at 0.1-0.2 Hz.
- Wind: loop, brown noise, bandpass 300-900 Hz, resonance 1.5-4 (higher = whistling), motion 0.6-0.9 at 0.15-0.3 Hz.
- Fire: loop, brown noise lowpass ~800 Hz (roar) with motion ~0.35 at 1-2 Hz + crackle particles 15-40/s.
- Water: bubble particles 300-1200 Hz (len 0.04-0.08); drips = few per second with a big room.
- Applause / crowd clapping: loop, clap particles 40-120/s around 1.3 kHz.
- Screams / yells / moans / growls: wave voice + a vowel, pitch_points for the rise and fall, jitter 0.3-0.7,
  roughness (scream 0.5-0.7, growl 0.8-1), drive 0.3-0.7. Scream: 500-900 Hz, vowel a, formant_shift +2..+4.
  Man yell 150-250 Hz. Growl 50-90 Hz, vowel o/u, formant_shift -5..-8. Moan: breath 0.4-0.6, vibrato, big room.
  These sound like a game or cartoon voice, not a real recording: say so in the note.
- Air / whoosh = bandpass noise sweeping. Rising pitch = tension / success; falling = drop / fail.
- Hits / booms = sine 40-150 Hz falling + transient + a low mode, drive 0.2-0.4, room.
- Bells = sine modes, long decays, inharmonic. Retro / game = square wave; harsh / sci-fi = saw.
Never copy a specific brand's sound (an operating system's or a product's); make a sound of the same kind.
Start from the closest preset and change only what the description asks for."""


class SfxDesign(BaseModel):
    params: SfxParams
    note: str = Field("", description="One short sentence for the user about the sound (or its limits)")


def design(prompt: str, current: SfxParams | None = None, model: str | None = None) -> dict:
    """Ask the AI to set the synth for a description. Raises ai.client.AIError when no model is available."""
    from .ai import client as aic

    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Describe the sound first (e.g. 'soft whoosh for a slide transition')")
    lines = [f'Sound wanted: "{prompt[:500]}"', "", "Presets (name: settings; unlisted settings are the defaults):"]
    lines += [f"- {k}: {json.dumps({x: y for x, y in v.items() if x != 'cat'})}" for k, v in PRESETS.items()]
    if current:
        lines += ["", "The user's current sound (change it if the request sounds like a tweak, e.g. 'higher', 'longer'):",
                  json.dumps(current.model_dump())]
    with aic.use_models({aic.TIERS.get("sfx", "editor"): model} if model else {}):
        out, usage = aic.call("sfx", "\n".join(lines), SfxDesign, system=AI_SYSTEM, quality="economy")
    return {"params": out.params.model_dump(), "note": out.note[:300], "usage": usage}


# ---- into a project -----------------------------------------------------------------------------------------------

def add_to_project(p, pdir: Path, params: SfxParams, start: float = 0.0, volume: float = 0.8, hold: float | None = None):
    """Render the sound into the project's media/sfx folder and put it on a music track at `start` (no ducking).
    A one-shot plays once; a loop repeats for `hold` seconds, or to the end of the edit when `hold` is None."""
    from .project import MusicTrack, Source, store_media_path

    path = save(params, pdir / "media" / "sfx")
    with wave.open(str(path)) as w:
        length = w.getnframes() / w.getframerate()
    sid = p.new_id("s")
    p.sources[sid] = Source(path=store_media_path(pdir, path), duration=round(length, 3), has_video=False, has_audio=True)
    start = max(0.0, float(start))
    loop = bool(params.loop)
    m = MusicTrack(id=p.new_id("m"), source=sid, start=start, volume=max(0.0, min(2.0, float(volume))), loop=loop, duck=False,
                   end=(start + float(hold)) if loop and hold else None, fade_in=0.6 if loop else 0.0, fade_out=0.8 if loop else 0.0)
    p.music.append(m)
    return m

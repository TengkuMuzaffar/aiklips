"""Sound: measure what each clip's own audio is, and audit how footage sound and music are used.

The LLMs we use can't hear, so this is all code (ffmpeg + numpy), like the rest of the auditor:

    stats(path, a, b)     level (dB), peak, and a speech score 0..1 for a time range of a file (cached)
    check_sound(p, pdir)  findings: background noise fighting the music, music drowning speech, muted speech,
                          cuts that clip a word, volume jumps between clips, music that stops early
    auto(p, pdir)         sensible defaults: keep speech, mute non-speech footage under music, duck the music

Speech score: energy in the voice band (300-3400 Hz) that starts and stops 2-4 times a second (words, syllables)
with big level swings. Measured: speech ~0.9, talking in wind ~0.5+, a tone with a pause / music / wind / hum ~0.
Speech under loud music in the footage itself scores low, which is why the planner also uses the footage log's
"person talking" flag, and an explicit clip audio mode (`ClipMeta.audio`) always wins.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

import numpy as np

from . import media
from .project import Clip, ClipMeta, Project, VeditError, resolve_media, state_dir

SR = 16000
HOP = 320  # 20 ms frames
VOICE_LO, VOICE_HI = 300, 3400
SPEECH = 0.4  # speech score at or above this = the clip has speech (talking in wind ~0.5, wind/music ~0)
NOISY_DB = -42.0  # non-speech footage louder than this is audible under music
LOUD_NOISE_DB = -26.0


def _f(v: float) -> str:
    return f"{v:.3f}"


def pcm(path: Path, a: float = 0.0, b: float | None = None) -> np.ndarray | None:
    cmd = [media.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", _f(max(0.0, a))]
    if b is not None:
        cmd += ["-t", _f(max(0.02, b - a))]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        return None
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def db(power: float) -> float:
    return round(10 * math.log10(max(power, 1e-9)), 1)


def analyse(y: np.ndarray | None) -> dict:
    """{db: mean level dBFS, peak: dBFS, voice: speech score 0..1, active: share of frames with sound}."""
    if y is None or y.size < SR // 10:
        return {"db": -90.0, "peak": -90.0, "voice": 0.0, "active": 0.0}
    n = y.size // HOP
    frames = y[: n * HOP].reshape(n, HOP).astype(np.float64)
    power = (frames ** 2).mean(axis=1)
    fdb = 10 * np.log10(np.maximum(power, 1e-9))
    spec = np.abs(np.fft.rfft(frames * np.hanning(HOP), n=512, axis=1)) ** 2
    freqs = np.fft.rfftfreq(512, 1 / SR)
    band = (freqs >= VOICE_LO) & (freqs <= VOICE_HI)
    vb = spec[:, band].sum(axis=1)
    tot = spec.sum(axis=1) + 1e-12
    floor = np.percentile(fdb, 20)
    active = fdb > max(-55.0, floor + 8)
    act_ratio = float(active.mean())
    vb_ratio = float(np.median(vb[active] / tot[active])) if active.any() else 0.0
    # speech = voice-band energy that starts and stops several times a second (words, syllables) with big level
    # swings. A steady tone (even with a pause), music, hum, wind and traffic have few onsets.
    env = 10 * np.log10(np.maximum(vb, 1e-12))
    swing = float(np.std(env))
    smooth = np.convolve(env, np.ones(3) / 3, mode="same")
    on = smooth > np.percentile(smooth, 90) - 10
    onsets = float(np.sum(on[1:] & ~on[:-1]) / (n / 50))  # per second

    def ramp(v, lo, hi):
        return max(0.0, min(1.0, (v - lo) / (hi - lo)))

    score = ramp(vb_ratio, 0.3, 0.55) * ramp(swing, 6.0, 14.0) * ramp(onsets, 0.8, 2.0)
    if not 0.15 <= act_ratio <= 0.97:
        score *= 0.4  # speech has pauses; wall-to-wall sound (or near silence) is not talking
    return {"db": db(float(power.mean())), "peak": round(float(20 * np.log10(max(np.abs(y).max(), 1e-9))), 1),
            "voice": round(score, 2), "active": round(act_ratio, 2)}


def stats(path: Path, a: float, b: float, cache: Path | None = None) -> dict:
    path = Path(path)
    key = None
    if cache is not None:
        st = path.stat()
        key = hashlib.sha1(f"{path.resolve()}|{st.st_mtime}|{st.st_size}|{a:.2f}|{b:.2f}|v1".encode()).hexdigest()[:16]
        f = cache / f"{key}.json"
        if f.exists():
            try:
                return json.loads(f.read_text())
            except json.JSONDecodeError:
                pass
    out = analyse(pcm(path, a, b))
    if key is not None:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{key}.json").write_text(json.dumps(out))
    return out


def level(path: Path, a: float, b: float) -> float:
    y = pcm(path, a, b)
    if y is None or not y.size:
        return -90.0
    return db(float((y.astype(np.float64) ** 2).mean()))


def _cache(pdir: Path) -> Path:
    return state_dir(pdir) / "cache" / "sound"


def clip_stats(p: Project, pdir: Path, c: Clip) -> dict | None:
    src = p.sources[c.source]
    if not src.has_audio:
        return None
    path = resolve_media(pdir, src.path)
    st = stats(path, c.in_, c.out, _cache(pdir))
    if st and st["voice"] < SPEECH and studied_speech(path, c.in_, c.out):
        st = {**st, "studied_speech": True}
    return st


def studied_speech(path: Path, a: float, b: float) -> bool:
    """Did the media library's study hear a voice (or see someone talking) in a shot inside [a, b] of this file?
    The whole-clip score averages pauses in, so a real talking head can land just under SPEECH."""
    try:
        from . import media_library

        uid = media_library.id_for_path(path)
        if not uid:
            return False
        fm = media_library._footage()
        d = fm._dir(uid)
        if not (d / "index.json").exists():
            return False  # not studied yet: don't start a study from the audit
        measured = media_library._load(d / "measure.json", {}) or {}
        notes = media_library._load(d / "notes.json", {}) or {}
        for s in fm.segments(uid):
            if s["to"] <= a or s["from"] >= b:
                continue
            k = str(s["k"])
            if (measured.get(k) or {}).get("voice", 0) >= SPEECH or (notes.get(k) or {}).get("talking"):
                return True
    except Exception:  # noqa: BLE001 - a bonus signal; the measurement still decides without it
        return False
    return False


def is_speech(c: Clip, st: dict | None) -> bool:
    """Does the clip's own audio contain speech? A clip marked `voice` does (detection can miss speech under
    loud sound); otherwise the measurement decides. `mute`/`ambient` are volume choices, not content: a muted
    speaking clip still has speech, and the audit says so."""
    if c.meta and c.meta.audio == "voice":
        return True
    return bool(st) and (st["voice"] >= SPEECH or bool(st.get("studied_speech")))


# ---- audit ------------------------------------------------------------------------------------

def _finding(sev: str, msg: str, at: float | None, evidence: str, fix: str | None) -> dict:
    return {"severity": sev, "category": "sound", "at": None if at is None else round(at, 2), "message": msg,
            "evidence": evidence, "fix": fix}


def is_sfx(p: Project, m) -> bool:
    """A sound effect (`vedit sfx add`) shares the music tracks but isn't music: it never 'runs out', and it doesn't
    carry the video."""
    src = p.sources.get(m.source)
    return bool(src) and Path(src.path).name.startswith("sfx-")


def check_sound(p: Project, pdir: Path) -> list[dict]:
    out: list[dict] = []
    if not p.clips:
        return out
    starts = p.clip_starts()
    total = p.duration
    music = [m for m in p.music if m.volume > 0.02 and m.start < total and not is_sfx(p, m)]
    rows = []
    for c, t0 in zip(p.clips, starts):
        st = clip_stats(p, pdir, c)
        rows.append((c, t0, st))
        if not st:
            continue
        speech = is_speech(c, st)
        ev = f"{c.id}: level {st['db']} dB, speech score {st['voice']}, clip volume {c.volume:g}"
        if music and not speech and c.volume >= 0.15 and st["db"] > NOISY_DB:
            loud = st["db"] > LOUD_NOISE_DB
            out.append(_finding("must-fix" if loud else "should-fix",
                                f"{c.id} plays its own background sound (no speech, {st['db']:.0f} dB) under the music. "
                                "Mute it so the music carries this part.", t0, ev, f"vedit volume 0 --clip {c.id}"))
        if music and speech and c.volume > 0.1:
            for m in music:
                if not m.duck and m.volume > 0.25:
                    out.append(_finding("must-fix", f"Music {m.id} (volume {m.volume:g}, not ducked) fights the speech in {c.id}.",
                                        t0, ev, f"vedit music set {m.id} --duck --volume 0.4"))
        if speech and c.volume < 0.1 and st["voice"] >= 0.55:
            out.append(_finding("should-fix", f"{c.id} has speech but is muted. Unmute it (the music ducks under it), "
                                "or use a non-speaking shot here.", t0, ev, f"vedit volume 1 --clip {c.id}"))
    # cuts that clip a word: the outgoing clip is still loud in its last 60 ms
    for (c, t0, st), nxt in zip(rows, rows[1:]):
        if not st or not is_speech(c, st) or c.volume < 0.1:
            continue
        path = resolve_media(pdir, p.sources[c.source].path)
        tail = level(path, max(c.in_, c.out - 0.06), c.out)
        if tail > max(st["db"] - 2, -36):
            out.append(_finding("should-fix", f"The cut out of {c.id} at {t0 + c.duration:.2f}s chops a word "
                                f"(still {tail:.0f} dB in the last 60 ms). End the clip on a pause.", t0 + c.duration,
                                f"tail {tail} dB vs clip {st['db']} dB", f"shorten or lengthen {c.id} by ~0.1-0.2 s"))
    # volume jumps between neighbouring speech clips
    spoken = [(c, t0, st["db"] + 20 * math.log10(max(c.volume, 1e-4))) for c, t0, st in rows if st and is_speech(c, st) and c.volume > 0.1]
    for (a, _ta, la), (b, tb, lb) in zip(spoken, spoken[1:]):
        if abs(la - lb) > 8:
            ratio = round(10 ** ((la - lb) / 20) * b.volume, 2)
            out.append(_finding("should-fix", f"Speech volume jumps {abs(la - lb):.0f} dB from {a.id} to {b.id}.", tb,
                                f"{a.id} {la:.0f} dB, {b.id} {lb:.0f} dB", f"vedit volume {min(2.0, ratio)} --clip {b.id}"))
    for m in music:
        if not m.loop:
            src = p.sources[m.source]
            ends = m.start + max(0.0, src.duration - m.in_)
            if ends < total - 0.5:
                out.append(_finding("should-fix", f"Music {m.id} runs out at {ends:.1f}s, before the video ends ({total:.1f}s).",
                                    ends, f"song {src.duration:.1f}s from {m.in_:.1f}s", f"vedit music set {m.id} --start 0  (or loop / pick an earlier offset)"))
    heard = [(c, st) for c, _t, st in rows if st and c.volume >= 0.05 and st.get("db", -90) >= -45]
    if not music and rows and not any(is_speech(c, st) for c, st in heard):
        muted = sum(1 for c, _t, st in rows if st and c.volume < 0.05)
        out.append(_finding("must-fix" if not heard else "should-fix",
                            "No music and no speech: the video plays " + ("silent" if not heard else "with only background noise")
                            + (f" ({muted} clips are muted)" if muted else "") + ".", None,
                            f"{len(rows)} clips, {len(heard)} audible, music tracks: none",
                            "vedit music make pop --volume 0.5  (or music add <song>)"))
    return out


def auto(p: Project, pdir: Path) -> list[str]:
    """Keep speech, mute non-speech footage when there is music, duck the music under speech."""
    changes = []
    music = [m for m in p.music if m.volume > 0.02 and not is_sfx(p, m)]
    any_speech = False
    for c in p.clips:
        st = clip_stats(p, pdir, c)
        if not st:
            continue
        speech = st["voice"] >= SPEECH
        any_speech |= speech
        mode = "voice" if speech else ("mute" if music else "ambient")
        vol = {"voice": 1.0, "mute": 0.0, "ambient": 1.0}[mode]
        if c.volume != vol:
            changes.append(f"{c.id}: {mode} (volume {c.volume:g} -> {vol:g})")
        c.volume = vol
        c.meta = (c.meta or ClipMeta()).model_copy(update={"audio": mode})
    for m in music:
        if any_speech and not m.duck:
            m.duck = True
            m.volume = max(m.volume, 0.4)
            changes.append(f"{m.id}: ducked under speech, volume {m.volume:g}")
    return changes


def report(p: Project, pdir: Path) -> list[dict]:
    rows = []
    for c, t0 in zip(p.clips, p.clip_starts()):
        st = clip_stats(p, pdir, c)
        rows.append({"clip": c.id, "at": round(t0, 2), "volume": c.volume, "mode": c.meta.audio if c.meta else None,
                     **(st or {"db": None, "voice": None}), "speech": is_speech(c, st) if st else False})
    return rows


def describe(st: dict | None, volume: float) -> str:
    if not st:
        return "no audio"
    kind = "speech" if st["voice"] >= SPEECH else "background sound" if st["db"] > NOISY_DB else "near silent"
    return f"{kind} ({st['db']:.0f} dB, speech {st['voice']:.2f}), volume {volume:g}"


def require_audio(path: Path) -> None:
    if not media.probe(path).get("has_audio"):
        raise VeditError(f"{path.name} has no audio")

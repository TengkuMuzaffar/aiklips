"""Measure a video with code (ffmpeg + numpy), no AI.

Everything an LLM is bad at lives here: exact cut times, shot lengths, tempo, the beat
grid, how many cuts land on a beat, loudness and motion. The numbers are exact and
free, and the AI step trusts them over its own eyes.

    metrics = analyze(video, out_dir)    # writes metrics.json, thumb.jpg, sheet-*.jpg
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable

import numpy as np

from .. import media
from ..project import VeditError

SR = 22050
HOP = 512
NFFT = 1024
FPS_ENV = SR / HOP  # onset-envelope frames per second (~43)
LAG = NFFT / 2 / SR  # an onset frame is centred half a window after its start


# ---- picture ------------------------------------------------------------------------------

def detect_cuts(path: Path, threshold: float = 0.3) -> list[float]:
    """Hard cuts (shot changes), via ffmpeg's scene-change score."""
    proc = subprocess.run(
        [media.ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(path), "-an",
         "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise VeditError("cut detection failed:\n" + "\n".join(proc.stderr.strip().splitlines()[-8:]))
    out: list[float] = []
    for line in proc.stderr.splitlines():
        if "showinfo" in line:
            m = re.search(r"pts_time:([\d.]+)", line)
            if m:
                t = round(float(m.group(1)), 3)
                if t > 0.1 and (not out or t - out[-1] > 0.15):
                    out.append(t)
    return out


def motion(path: Path, fps: int = 10, size: int = 64) -> dict:
    """Frame-difference energy and brightness per frame (small grey frames)."""
    proc = subprocess.run(
        [media.ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(path), "-an",
         "-vf", f"fps={fps},scale={size}:{size},format=gray", "-f", "rawvideo", "-"],
        capture_output=True,
    )
    raw = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = raw.size // (size * size)
    if n < 2:
        return {"fps": fps, "energy": [], "mean": 0.0, "flashes": []}
    frames = raw[: n * size * size].reshape(n, size, size).astype(np.float32) / 255.0
    energy = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    bright = frames.mean(axis=(1, 2))
    jumps = np.where(np.abs(np.diff(bright)) > 0.25)[0]
    return {
        "fps": fps,
        "energy": [round(float(e), 4) for e in energy],
        "mean": round(float(energy.mean()), 4),
        "high_motion_ratio": round(float((energy > 0.08).mean()), 3),
        "flashes": [round((i + 1) / fps, 2) for i in jumps],
    }


# ---- sound ----------------------------------------------------------------------------------

def load_audio(path: Path, start: float = 0.0, duration: float | None = None) -> np.ndarray | None:
    cmd = [media.ffmpeg_bin(), "-hide_banner", "-nostdin", "-ss", f"{start:.3f}"]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        return None
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def onset_envelope(y: np.ndarray) -> np.ndarray:
    """Spectral flux: how much new energy appears in each ~23 ms frame."""
    if y.size < NFFT * 2:
        return np.zeros(0, dtype=np.float32)
    n = 1 + (y.size - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    frames = y[idx] * np.hanning(NFFT)[None, :].astype(np.float32)
    mag = np.log1p(100 * np.abs(np.fft.rfft(frames, axis=1)))
    flux = np.maximum(0, np.diff(mag, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")  # remove slow loudness drift
    flux = np.maximum(flux, 0)
    peak = flux.max()
    return (flux / peak).astype(np.float32) if peak > 0 else flux.astype(np.float32)


def tempo(env: np.ndarray, lo: float = 60, hi: float = 200) -> tuple[float | None, float]:
    """(BPM, confidence 0..1) from the autocorrelation of the onset envelope."""
    if env.size < FPS_ENV * 4 or env.std() < 1e-4:
        return None, 0.0
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[e.size - 1:]
    if ac[0] <= 0:
        return None, 0.0
    ac = ac / ac[0]
    best, best_score = None, -1.0
    for bpm in np.arange(lo, hi + 0.01, 0.5):
        lag = 60.0 * FPS_ENV / bpm
        if lag * 2 >= ac.size - 1:
            continue
        def at(l: float) -> float:
            i = int(l)
            return float(ac[i] + (ac[i + 1] - ac[i]) * (l - i))
        # a true beat period also correlates at 2x the lag; prefer tempi near 120 (log-normal prior)
        score = (at(lag) + 0.5 * at(lag * 2)) * np.exp(-0.5 * (np.log2(bpm / 120.0) / 0.9) ** 2)
        if score > best_score:
            best, best_score = float(bpm), score
    if best is None:
        return None, 0.0
    conf = float(np.clip(best_score / 0.6, 0, 1))
    return round(best, 1), round(conf, 2)


def beat_grid(env: np.ndarray, bpm: float, duration: float) -> list[float]:
    """Beat times (s) for a steady tempo: choose the phase where onsets line up best."""
    period = 60.0 * FPS_ENV / bpm
    best_off, best = 0.0, -1.0
    for off in np.arange(0, period, 0.5):
        pos = np.arange(off, env.size - 1, period).astype(int)
        s = float(env[pos].sum())
        if s > best:
            best_off, best = off, s
    beats = np.arange(best_off, env.size, period) / FPS_ENV + LAG
    return [round(float(b), 3) for b in beats if b <= duration]


def onsets(env: np.ndarray, k: float = 2.5, min_gap: float = 0.1) -> list[float]:
    """Sharp hits (snares, whooshes, SFX): local peaks well above the average."""
    if env.size < 3:
        return []
    th = env.mean() + k * env.std()
    peaks = np.where((env[1:-1] > th) & (env[1:-1] >= env[:-2]) & (env[1:-1] >= env[2:]))[0] + 1
    out: list[float] = []
    for p in peaks:
        t = p / FPS_ENV + LAG
        if not out or t - out[-1] >= min_gap:
            out.append(round(float(t), 3))
    return out


def loudness(y: np.ndarray, step: float = 0.25) -> list[float]:
    n = int(SR * step)
    if y.size < n:
        return []
    m = y.size // n
    rms = np.sqrt((y[: m * n].reshape(m, n) ** 2).mean(axis=1))
    peak = rms.max() or 1.0
    return [round(float(v / peak), 3) for v in rms]


def analyze_audio(path: Path, duration: float, start: float = 0.0) -> dict | None:
    y = load_audio(path, start, duration)
    if y is None or y.size < SR:
        return None
    env = onset_envelope(y)
    bpm, conf = tempo(env)
    beats = beat_grid(env, bpm, duration) if bpm and conf >= 0.15 else []
    rms = loudness(y)
    quiet = float(np.mean(np.array(rms) < 0.08)) if rms else 1.0
    level_db = float(20 * np.log10(max(1e-6, float(np.sqrt((y ** 2).mean())))))
    return {
        "bpm": bpm if beats else None, "tempo_confidence": conf, "beats": beats,
        "onsets": onsets(env), "rms": rms, "rms_step": 0.25, "silence_ratio": round(quiet, 3),
        "level_db": round(level_db, 1),
    }


def near_beat_ratio(times: list[float], beats: list[float], tol: float = 0.08) -> float | None:
    """Share of `times` within ±tol of a beat. Random timing scores around 2*tol/beat_period."""
    if not times or len(beats) < 4:
        return None
    b = np.array(beats)
    hits = sum(1 for t in times if np.abs(b - t).min() <= tol)
    return round(hits / len(times), 3)


# ---- pacing --------------------------------------------------------------------------------

def pacing(cuts: list[float], duration: float) -> dict:
    edges = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
    shots = [round(b - a, 3) for a, b in zip(edges, edges[1:]) if b - a > 0.02]
    if not shots:
        return {"shots": [], "shot_count": 0, "avg_shot_s": round(duration, 2), "median_shot_s": round(duration, 2),
                "cuts_per_10s": 0.0, "rhythm": "steady"}
    half = len(shots) // 2
    rhythm = "steady"
    if len(shots) >= 6:
        a, b = float(np.mean(shots[:half])), float(np.mean(shots[half:]))
        cv = float(np.std(shots) / max(1e-6, np.mean(shots)))
        if b < 0.75 * a:
            rhythm = "accelerating"
        elif b > 1.33 * a:
            rhythm = "decelerating"
        elif cv > 0.8:
            rhythm = "varied"
    return {
        "shots": shots, "shot_count": len(shots), "avg_shot_s": round(float(np.mean(shots)), 2),
        "median_shot_s": round(float(np.median(shots)), 2), "min_shot_s": round(min(shots), 2),
        "max_shot_s": round(max(shots), 2), "cuts_per_10s": round(len(cuts) / max(duration, 0.1) * 10, 2), "rhythm": rhythm,
    }


# ---- contact sheets ----------------------------------------------------------------------------

def _tile(frames: list[tuple[Path, str]], out: Path, cols: int = 4, width: int = 300, title: str = "") -> None:
    from PIL import Image, ImageDraw, ImageFont

    ims = [(Image.open(p).convert("RGB"), label) for p, label in frames if p.exists()]
    if not ims:
        return
    tw = width
    th = max(int(im.height * tw / im.width) for im, _ in ims)
    rows = -(-len(ims) // cols)
    head = 34 if title else 0
    sheet = Image.new("RGB", (cols * (tw + 6) + 6, head + rows * (th + 28) + 6), "#111111")
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=18)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    if title:
        d.text((10, 8), title, fill="#5eead4", font=font)
    for i, (im, label) in enumerate(ims):
        im = im.resize((tw, int(im.height * tw / im.width)))
        x, y = 6 + (i % cols) * (tw + 6), head + 6 + (i // cols) * (th + 28)
        sheet.paste(im, (x, y))
        d.text((x + 4, y + im.height + 4), label, fill="#e6e9ef", font=font)
    sheet.save(out, "JPEG", quality=85)


def sample_frames(path: Path, times: list[float], out_dir: Path, prefix: str, width: int = 300) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, t in enumerate(times):
        dest = out_dir / f"{prefix}-{i:03d}.jpg"
        try:
            media.extract_frame(path, max(0.0, t), dest, width)
            paths.append(dest)
        except VeditError:
            pass
    return paths


def make_sheets(path: Path, duration: float, shots: list[float], out_dir: Path) -> list[str]:
    """Sheet 1: the hook (first 3 s at 4 fps). Sheet 2: one frame per shot (max 16)."""
    frames_dir = out_dir / "frames"
    sheets: list[str] = []
    hook_t = [round(i * 0.25, 2) for i in range(12) if i * 0.25 < duration - 0.05]
    hook = sample_frames(path, hook_t, frames_dir, "hook")
    if hook:
        dest = out_dir / "sheet-1.jpg"
        _tile([(p, f"{t:.2f}s") for p, t in zip(hook, hook_t)], dest, cols=4, title="HOOK: first 3 seconds, 4 frames per second")
        sheets.append(dest.name)
    mids, t0 = [], 0.0
    for s in shots:
        mids.append(round(t0 + s / 2, 2))
        t0 += s
    if len(mids) > 16:
        mids = [mids[int(i * len(mids) / 16)] for i in range(16)]
    if mids:
        shot_frames = sample_frames(path, mids, frames_dir, "shot")
        dest = out_dir / "sheet-2.jpg"
        _tile([(p, f"shot @ {t:.2f}s") for p, t in zip(shot_frames, mids)], dest, cols=4,
              title=f"ONE FRAME PER SHOT ({len(shots)} shots)")
        sheets.append(dest.name)
    return sheets


# ---- everything -----------------------------------------------------------------------------

def analyze(path: Path, out_dir: Path, progress: Callable[[str], None] | None = None, transcript: bool = True) -> dict:
    say = progress or (lambda _s: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    say("Reading the file")
    info = media.probe(path)
    if not info["has_video"]:
        raise VeditError("A reference must be a video")
    dur = info["duration"]
    media.extract_frame(path, min(1.0, dur / 3), out_dir / "thumb.jpg", 360)
    say("Finding cuts")
    cuts = detect_cuts(path)
    pace = pacing(cuts, dur)
    say("Measuring motion")
    mot = motion(path)
    audio = None
    if info["has_audio"]:
        say("Listening: tempo and beats")
        audio = analyze_audio(path, dur)
    beats = (audio or {}).get("beats") or []
    sync = near_beat_ratio(cuts, beats)
    period = 60.0 / audio["bpm"] if audio and audio.get("bpm") else None
    chance = round(min(1.0, 2 * 0.08 / period), 3) if period else None
    say("Making contact sheets")
    sheets = make_sheets(path, dur, pace["shots"], out_dir)
    text = None
    if transcript and info["has_audio"] and dur <= 240:
        try:
            from ..ops import captions as caps

            say("Transcribing speech")
            words = caps.transcribe(path, "base")
            text = " ".join(w.text for w in words)[:3000] or None
            if words:
                text_words = [{"t": round(w.start, 2), "w": w.text} for w in words[:400]]
                (out_dir / "words.json").write_text(json.dumps(text_words))
        except (VeditError, ImportError, Exception):  # captions extra not installed, or model download failed
            text = None
    metrics = {
        **info, "cuts": cuts, "pacing": pace, "motion": {k: v for k, v in mot.items() if k != "energy"},
        "motion_energy": mot.get("energy", []), "audio": audio,
        "cuts_on_beat": sync, "cuts_on_beat_chance": chance, "first_cut_s": cuts[0] if cuts else None,
        "flashes_on_beat": near_beat_ratio(mot.get("flashes", []), beats), "sheets": sheets, "transcript": text,
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics))
    return metrics


def summary_for_ai(m: dict) -> dict:
    """The metrics without the long per-frame curves (those are for charts, not for the prompt)."""
    a = m.get("audio") or {}
    return {
        "duration_s": m["duration"], "size": f"{m.get('width')}x{m.get('height')}", "fps": m.get("fps"),
        "cuts_s": m["cuts"][:80], "pacing": {k: v for k, v in m["pacing"].items() if k != "shots"},
        "shot_lengths_s": m["pacing"]["shots"][:60],
        "motion": m.get("motion"),
        "audio": None if not a else {
            "bpm": a.get("bpm"), "tempo_confidence": a.get("tempo_confidence"), "beat_count": len(a.get("beats", [])),
            "hits_s": a.get("onsets", [])[:60], "silence_ratio": a.get("silence_ratio"), "level_db": a.get("level_db"),
        },
        "cuts_on_beat": m.get("cuts_on_beat"), "cuts_on_beat_if_random": m.get("cuts_on_beat_chance"),
        "flashes_on_beat": m.get("flashes_on_beat"), "first_cut_s": m.get("first_cut_s"),
        "transcript": m.get("transcript"),
    }

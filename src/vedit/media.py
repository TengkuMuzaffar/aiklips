"""FFmpeg / ffprobe helpers."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Callable

from .project import VeditError

INSTALL_HINT = (
    "Install FFmpeg first: macOS `brew install ffmpeg`, Windows `winget install Gyan.FFmpeg`, "
    "Linux `sudo apt install ffmpeg` (or run scripts/setup.sh / scripts/setup.ps1)."
)


def ffmpeg_bin() -> str:
    b = shutil.which("ffmpeg")
    if not b:
        raise VeditError("ffmpeg not found. " + INSTALL_HINT)
    return b


def ffprobe_bin() -> str:
    b = shutil.which("ffprobe")
    if not b:
        raise VeditError("ffprobe not found. " + INSTALL_HINT)
    return b


@lru_cache
def ffmpeg_major() -> int:
    out = subprocess.run([ffmpeg_bin(), "-version"], capture_output=True, text=True).stdout
    m = re.search(r"ffmpeg version n?(\d+)", out)
    return int(m.group(1)) if m else 6


def run(cmd: list[str], what: str = "ffmpeg") -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise VeditError(f"{what} failed:\n{tail}")
    return proc


class Cancelled(VeditError):
    """The user stopped a render."""


def run_with_progress(cmd: list[str], total: float, on_progress: Callable[[float], None] | None,
                      should_stop: Callable[[], bool] | None = None) -> None:
    """Run ffmpeg with -progress pipe:1 and report 0..1 progress. `should_stop` returning True kills it (Cancelled)."""
    cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        if should_stop is not None and should_stop():
            proc.kill()
            proc.wait()
            raise Cancelled("Export cancelled")
        if line.startswith("out_time_us=") and on_progress and total > 0:
            try:
                us = int(line.split("=", 1)[1])
                on_progress(min(1.0, us / 1e6 / total))
            except ValueError:
                pass
    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        tail = "\n".join(stderr.strip().splitlines()[-20:])
        raise VeditError(f"Render failed:\n{tail}")


def probe(path: Path) -> dict:
    if not path.exists():
        raise VeditError(f"File not found: {path}")
    proc = run(
        [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        "ffprobe",
    )
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or 0) or float((v or a or {}).get("duration") or 0)
    info = {"duration": round(duration, 3), "has_video": v is not None, "has_audio": a is not None}
    if v:
        w, h = int(v["width"]), int(v["height"])
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(float(sd["rotation"]))
        rot = int(v.get("tags", {}).get("rotate", rot))
        if abs(rot) % 180 == 90:  # ffmpeg auto-rotates on decode
            w, h = h, w
        num, _, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 30.0
        if not (1 <= fps <= 240):
            fps = 30.0
        info.update(width=w, height=h, fps=round(fps, 3))
    if duration <= 0:
        raise VeditError(f"Could not read duration of {path}")
    return info


def detect_silence(path: Path, start: float, duration: float, noise_db: float, min_silence: float) -> list[tuple[float, float]]:
    """Return silent intervals relative to `start` using ffmpeg's silencedetect."""
    proc = run(
        [
            ffmpeg_bin(), "-hide_banner", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(path),
            "-vn", "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-",
        ],
        "silence detection",
    )
    out: list[tuple[float, float]] = []
    s: float | None = None
    for line in proc.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            s = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end: (-?[\d.]+)", line)
        if m and s is not None:
            out.append((s, min(duration, float(m.group(1)))))
            s = None
    if s is not None:
        out.append((s, duration))
    return out


def extract_frame(video: Path, t: float, out: Path, width: int | None = None) -> None:
    vf = ["-vf", f"scale={width}:-2"] if width else []
    q = ["-q:v", "4"] if out.suffix.lower() in (".jpg", ".jpeg") else []
    run([ffmpeg_bin(), "-y", "-hide_banner", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", *vf, *q, str(out)], "frame extraction")


def contact_sheet(video: Path, duration: float, out: Path, count: int = 12, cols: int = 6, width: int = 180) -> None:
    rows = max(1, -(-count // cols))
    every = max(duration / count, 0.04)
    run(
        [
            ffmpeg_bin(), "-y", "-hide_banner", "-i", str(video),
            "-vf", f"fps=1/{every:.4f},scale={width}:-2,tile={cols}x{rows}:padding=4:color=#111111",
            "-frames:v", "1", *(["-q:v", "4"] if out.suffix.lower() in (".jpg", ".jpeg") else []), str(out),
        ],
        "contact sheet",
    )


def waveform(video: Path, out: Path, width: int = 1600, height: int = 120) -> bool:
    try:
        run(
            [
                ffmpeg_bin(), "-y", "-hide_banner", "-i", str(video), "-filter_complex",
                f"aformat=channel_layouts=mono,showwavespic=s={width}x{height}:colors=#5eead4", "-frames:v", "1", str(out),
            ],
            "waveform",
        )
        return True
    except VeditError:
        return False


def open_file(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform == "win32":
        import os

        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])

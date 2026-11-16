"""Exports: pick the file type, size, frame rate, quality and where the file goes.

    s = ExportSettings(format="hevc", fps=60, scale=1.0, quality="high", folder="~/Desktop")
    res = run(project, pdir, s, on_progress=lambda f, stage: ...)

Settings are remembered per project in .vedit/export.json. Every format is a set of FFmpeg encoder arguments
for the same render graph (render/graph.py), except GIF, which renders a short MP4 first and then builds an
optimised palette from it (a GIF made in one pass looks banded).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import media
from ..project import Project, VeditError, state_dir

Quality = Literal["high", "standard", "small"]

# id: label, extension, what it's for, encoders needed, has audio, encoder args per quality
FORMATS: dict[str, dict] = {
    "mp4": {"label": "MP4 · H.264", "ext": ".mp4", "about": "Plays everywhere. Best for TikTok, Reels, Shorts, WhatsApp.",
            "needs": ["libx264", "aac"], "audio": True},
    "hevc": {"label": "MP4 · HEVC (H.265)", "ext": ".mp4", "about": "Same quality at about half the size. iPhone, Mac, YouTube.",
             "needs": ["libx265", "aac"], "audio": True},
    "prores": {"label": "MOV · ProRes 422", "ext": ".mov", "about": "Near-lossless master for Premiere, Final Cut or DaVinci. Very large.",
               "needs": ["prores_ks", "pcm_s16le"], "audio": True},
    "webm": {"label": "WebM · VP9", "ext": ".webm", "about": "For websites and landing pages. Slower to export.",
             "needs": ["libvpx-vp9", "libopus"], "audio": True},
    "gif": {"label": "GIF · animated", "ext": ".gif", "about": "Silent, looping preview for chats, docs and emails. Kept short and small.",
            "needs": ["libx264", "gif"], "audio": False},
}

_CRF = {"mp4": (18, 21, 26), "hevc": (20, 24, 28), "webm": (28, 33, 38)}
_ABR = {"high": "192k", "standard": "160k", "small": "128k"}
_QI = {"high": 0, "standard": 1, "small": 2}


def encode_args(fmt: str, quality: str) -> list[str]:
    q = _QI.get(quality, 0)
    if fmt == "mp4":
        return ["-c:v", "libx264", "-preset", "medium", "-crf", str(_CRF["mp4"][q]), "-profile:v", "high", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", _ABR[quality], "-movflags", "+faststart"]
    if fmt == "hevc":
        return ["-c:v", "libx265", "-preset", "medium", "-crf", str(_CRF["hevc"][q]), "-tag:v", "hvc1", "-pix_fmt", "yuv420p",
                "-x265-params", "log-level=error", "-c:a", "aac", "-b:a", _ABR[quality], "-movflags", "+faststart"]
    if fmt == "prores":
        profile = {"high": "3", "standard": "2", "small": "1"}[quality]  # HQ / 422 / LT
        return ["-c:v", "prores_ks", "-profile:v", profile, "-vendor", "apl0", "-pix_fmt", "yuv422p10le", "-c:a", "pcm_s16le"]
    if fmt == "webm":
        return ["-c:v", "libvpx-vp9", "-crf", str(_CRF["webm"][q]), "-b:v", "0", "-row-mt", "1", "-deadline", "good", "-cpu-used", "4",
                "-pix_fmt", "yuv420p", "-c:a", "libopus", "-b:a", _ABR[quality]]
    raise VeditError(f"Unknown format {fmt}")


@lru_cache(maxsize=1)
def _encoders() -> frozenset[str]:
    out = subprocess.run([media.ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return frozenset(m.group(1) for m in re.finditer(r"^\s[VAS][.FSXBD]{5}\s+(\S+)", out, re.M))


def available() -> dict[str, bool]:
    have = _encoders()
    return {k: all(e in have for e in v["needs"]) for k, v in FORMATS.items()}


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ExportSettings(_M):
    format: Literal[tuple(FORMATS)] = "mp4"  # type: ignore[valid-type]
    fps: float | None = Field(None, description="Frames per second; None = the project's frame rate")
    scale: float = Field(1.0, ge=0.25, le=2.0, description="Resolution: 1 = the canvas size")
    quality: Quality = "high"
    audio: bool = True
    folder: str | None = Field(None, description="Where the file goes; None = the project's exports folder")
    filename: str | None = Field(None, description="File name without extension; None = the project name")
    gif_fps: float = Field(15, ge=5, le=30)
    gif_width: int = Field(540, ge=160, le=1080)


# ---- remembered settings -------------------------------------------------------------------------

def _store(pdir: Path) -> Path:
    return state_dir(pdir) / "export.json"


def load_settings(pdir: Path) -> ExportSettings:
    try:
        return ExportSettings.model_validate_json(_store(pdir).read_text())
    except (OSError, ValueError):
        return ExportSettings()


def save_settings(pdir: Path, s: ExportSettings) -> None:
    _store(pdir).write_text(s.model_dump_json(indent=1))


# ---- where the file goes -------------------------------------------------------------------------

_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(name: str) -> str:
    n = _BAD.sub("-", name).strip(" .-")
    return n[:120] or "video"


def folder_for(pdir: Path, s: ExportSettings) -> Path:
    f = Path(s.folder).expanduser() if s.folder else pdir / "exports"
    if not f.is_absolute():
        f = (pdir / f).resolve()
    return f


def destination(project: Project, pdir: Path, s: ExportSettings) -> Path:
    """The file this export will write. Never overwrites: name, name-2, name-3..."""
    folder = folder_for(pdir, s)
    base = safe_name(s.filename or project.name)
    ext = FORMATS[s.format]["ext"]
    dest = folder / f"{base}{ext}"
    n = 2
    while dest.exists():
        dest = folder / f"{base}-{n}{ext}"
        n += 1
    return dest


def output_size(project: Project, s: ExportSettings) -> tuple[int, int]:
    from .layers import even

    if s.format == "gif":
        w = min(s.gif_width, project.canvas.width)
        return even(w), even(project.canvas.height * w / project.canvas.width)
    return even(project.canvas.width * s.scale), even(project.canvas.height * s.scale)


def describe(project: Project, pdir: Path, s: ExportSettings) -> dict:
    w, h = output_size(project, s)
    fps = s.gif_fps if s.format == "gif" else (s.fps or project.canvas.fps)
    return {"format": s.format, "label": FORMATS[s.format]["label"], "width": w, "height": h, "fps": fps,
            "audio": bool(s.audio and FORMATS[s.format]["audio"]), "folder": str(folder_for(pdir, s)),
            "file": destination(project, pdir, s).name}


# ---- the export ----------------------------------------------------------------------------------

def run(project: Project, pdir: Path, s: ExportSettings, on_progress: Callable[[float, str], None] | None = None,
        should_stop: Callable[[], bool] | None = None) -> dict:
    """Render the project with these settings. Progress is reported as (0..1, stage)."""
    from .runner import render

    if not available().get(s.format):
        raise VeditError(f"{FORMATS[s.format]['label']} needs FFmpeg encoders {', '.join(FORMATS[s.format]['needs'])}, "
                         "which this FFmpeg build doesn't have.")
    say = on_progress or (lambda _f, _st: None)
    dest = destination(project, pdir, s)
    folder = dest.parent
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise VeditError(f"Can't create the folder {folder}: {e}") from None
    t0 = time.time()
    p = project.model_copy(deep=True)
    if s.format == "gif":
        p.canvas.fps = s.gif_fps
        scale = min(s.gif_width, p.canvas.width) / p.canvas.width
        tmp = folder / f".{dest.stem}.gif-src-{uuid.uuid4().hex[:6]}.mp4"
        try:
            say(0.0, "Preparing titles and effects")
            render(p, pdir, tmp, scale, "export", progress=False, on_progress=lambda f: say(f * 0.8, "Rendering frames"),
                   encode=encode_args("mp4", "high"), audio=False, should_stop=should_stop)
            say(0.8, "Building the GIF palette")
            vf = ("split[a][b];[a]palettegen=stats_mode=diff:max_colors=256[p];"
                  "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
            cmd = [media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(tmp),
                   "-filter_complex", vf, "-loop", "0", str(dest)]
            media.run_with_progress(cmd, p.duration, lambda f: say(0.8 + f * 0.2, "Building the GIF palette"), should_stop)
        finally:
            tmp.unlink(missing_ok=True)
    else:
        if s.fps:
            p.canvas.fps = s.fps
        audio = bool(s.audio and FORMATS[s.format]["audio"])
        say(0.0, "Preparing titles and effects")  # overlays render before the first video frame
        render(p, pdir, dest, s.scale, "export", progress=False, on_progress=lambda f: say(f, "Rendering"),
               encode=encode_args(s.format, s.quality), audio=audio, should_stop=should_stop)
    say(1.0, "Done")
    info = media.probe(dest)
    return {"output": str(dest), "folder": str(folder), "file": dest.name, "bytes": dest.stat().st_size,
            "format": s.format, "label": FORMATS[s.format]["label"], "width": info.get("width"), "height": info.get("height"),
            "fps": p.canvas.fps, "duration": info["duration"], "seconds": round(time.time() - t0, 1)}


# ---- native folder picker (the server runs on the user's machine) --------------------------------

def pick_folder(start: str | None = None) -> str | None:
    """Open the OS folder chooser. Returns the chosen folder, or None if cancelled / not possible."""
    import sys

    start_dir = Path(start).expanduser() if start else Path.home()
    if sys.platform == "darwin":
        script = ('set p to POSIX path of (choose folder with prompt "Export to which folder?" default location '
                  f'(POSIX file "{str(start_dir).replace(chr(34), "")}"))\nreturn p')
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return (proc.stdout.strip().rstrip("/") or None) if proc.returncode == 0 else None
    if sys.platform.startswith("linux") and shutil.which("zenity"):
        proc = subprocess.run(["zenity", "--file-selection", "--directory", f"--filename={start_dir}/"], capture_output=True, text=True)
        return (proc.stdout.strip() or None) if proc.returncode == 0 else None
    if sys.platform == "win32":
        ps = ("Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; "
              f"$d.SelectedPath = '{start_dir}'; if ($d.ShowDialog() -eq 'OK') {{ $d.SelectedPath }}")
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
        return proc.stdout.strip() or None
    return None


def reveal(path: Path) -> None:
    """Show the file in Finder / Explorer (or open its folder)."""
    import sys

    if sys.platform == "darwin" and path.exists():
        subprocess.Popen(["open", "-R", str(path)])
    elif sys.platform == "win32" and path.exists():
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        media.open_file(path.parent if path.is_file() else path)


def options(project: Project, pdir: Path) -> dict:
    s = load_settings(pdir)
    av = available()
    return {"settings": s.model_dump(), "preview": describe(project, pdir, s),
            "formats": [{"id": k, "label": v["label"], "about": v["about"], "ext": v["ext"], "audio": v["audio"], "available": av[k]}
                        for k, v in FORMATS.items()],
            "canvas": {"width": project.canvas.width, "height": project.canvas.height, "fps": project.canvas.fps},
            "duration": round(project.duration, 2), "default_folder": str(pdir / "exports")}


def json_result(r: dict) -> str:
    return json.dumps(r)

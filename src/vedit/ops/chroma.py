"""Green / blue screen keying for main-track clips (`bg remove --mode chroma`).

Unlike the AI modes (bg_remove.py), keying runs entirely inside the FFmpeg graph: no frame loop in Python, no
model, no cached mask file. It is real time at 1080p on a CPU, and every setting is a compositing parameter, so
changing one never recomputes anything and the editor's instant frame shows it straight away.

The key compares colour only (YUV U/V), so uneven lighting on the screen still keys cleanly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .. import media
from ..project import Clip, Project, VeditError, resolve_media
from .text import rgba

# defaults tuned on real green-screen footage: wide enough for creased cloth, tight enough to keep green-ish shirts
DEFAULTS = {"key": "#00B140", "similarity": 0.12, "blend": 0.08, "spill": 0.6, "shrink": 1}
PARAMS = tuple(DEFAULTS)  # all of them only change compositing (see effects._COMPOSITE_ONLY)


def clean(params: dict) -> dict:
    """Validate and clamp user values; unknown keys pass through untouched."""
    out = dict(params)
    if "key" in out and out["key"] not in (None, "", "auto"):
        rgba(str(out["key"]))  # raises on a bad colour
    for k, lo, hi in (("similarity", 0.01, 0.6), ("blend", 0.0, 0.5), ("spill", 0.0, 1.0)):
        if out.get(k) is not None:
            out[k] = round(min(hi, max(lo, float(out[k]))), 4)
    if out.get("shrink") is not None:
        out["shrink"] = int(min(4, max(0, int(out["shrink"]))))
    return out


def screen_type(key: str) -> str:
    """'green' or 'blue': which channel the screen spills, for despill."""
    r, g, b = rgba(key)[:3]
    return "blue" if b > g else "green"


def key_chain(params: dict) -> str:
    """Filter chain (no labels) that turns the clip into RGBA with the screen transparent and its spill removed."""
    p = {**DEFAULTS, **{k: v for k, v in params.items() if v is not None}}
    key = str(p["key"]) if p["key"] not in ("", "auto") else DEFAULTS["key"]
    r, g, b = rgba(key)[:3]
    # 4:4:4 first: keying the subsampled chroma of 4:2:0 video gives 2 px stair-steps along every edge
    chain = f"format=yuv444p,chromakey=color=0x{r:02X}{g:02X}{b:02X}:similarity={p['similarity']}:blend={p['blend']},format=gbrap"
    if float(p["spill"]) > 0:
        chain += f",despill=type={screen_type(key)}:mix={p['spill']}:expand=0"
    return chain


def shrink_chain(n: int) -> str | None:
    """Alpha-only clean-up: pull the matte edge in by ~n px and soften it a touch (kills the green fringe)."""
    if n <= 0:
        return None
    return ",".join(["alphaextract"] + ["erosion"] * n + ["gblur=sigma=0.7"])


def _grab(path: Path, t: float, side: int = 320) -> np.ndarray:
    """One RGB frame at source second t, long side ~`side` px."""
    proc = subprocess.run(
        [media.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
         "-frames:v", "1", "-vf", f"scale='if(gt(iw,ih),{side},-2)':'if(gt(iw,ih),-2,{side})'",
         "-f", "image2pipe", "-vcodec", "ppm", "-"],
        capture_output=True, check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise VeditError(f"Could not read a frame from {path.name} at {t:.2f}s")
    from io import BytesIO

    from PIL import Image

    return np.asarray(Image.open(BytesIO(proc.stdout)).convert("RGB"))


def find_key(rgb: np.ndarray) -> str:
    """The screen colour in a frame: median of the pixels where green (or blue) clearly dominates.

    The whole frame is used, not just its border: screens are lit brightest in the middle and fall off towards the
    edges, and the dominance test already leaves out the subject."""
    px = rgb.reshape(-1, 3)
    f = px.astype(np.int16)
    r, g, b = f[:, 0], f[:, 1], f[:, 2]
    green = g - np.maximum(r, b) > 40
    blue = b - np.maximum(r, g) > 40
    pick = green if green.sum() >= blue.sum() else blue
    if pick.sum() < max(20, 0.05 * len(px)):
        raise VeditError("Couldn't find a green or blue screen in this shot. Give the colour with --key #RRGGBB.")
    m = np.median(px[pick], axis=0).astype(int)
    return f"#{m[0]:02X}{m[1]:02X}{m[2]:02X}"


def sample_key(path: Path, t: float) -> str:
    return find_key(_grab(path, t))


def clip_key(pdir: Path, project: Project, clip: Clip) -> str:
    """Sample the screen colour of a clip (half a second in, or its middle when shorter)."""
    t = clip.in_ + min(0.5, (clip.out - clip.in_) / 2)
    return sample_key(resolve_media(pdir, project.sources[clip.source].path), t)

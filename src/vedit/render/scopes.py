"""Video scopes of one preview frame: luma waveform (exposure, 0-100 IRE) and vectorscope (hue / saturation, skin
line). The editor shows them next to the colour controls; they are drawn from the instant frame (render/frame.py),
so they follow a slider while it is dragged."""

from __future__ import annotations

import subprocess

from .. import media
from ..project import VeditError

SCOPES = {
    "waveform": "scale=360:-2,format=yuv444p,waveform=intensity=0.35:graticule=green:flags=numbers+dots:scale=ire:"
                "fitmode=size,scale=360:200,format=rgb24",
    "vectorscope": "scale=360:-2,format=yuv444p,vectorscope=mode=color3:graticule=color:intensity=0.6:flags=name,"
                   "scale=220:220,format=rgb24",
}


def scope_png(frame: bytes, kind: str) -> bytes:
    """PNG of a scope for one encoded frame (JPEG / PNG bytes)."""
    if kind not in SCOPES:
        raise VeditError(f"Scope must be one of: {', '.join(SCOPES)}")
    proc = subprocess.run(
        [media.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "image2pipe", "-i", "-",
         "-vf", SCOPES[kind], "-frames:v", "1", "-f", "image2", "-c:v", "png", "-"],
        input=frame, capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise VeditError(f"Scope failed: {proc.stderr.decode(errors='replace')[-300:]}")
    return proc.stdout

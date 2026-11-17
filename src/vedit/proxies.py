"""Proxy media: small, fast-seeking copies of big sources (4K / HEVC phone video) for previews.

Previews and the editor's instant frames decode the proxy instead of the original, which is most of their cost
with 4K footage. Exports always use the originals. Proxies are built in the background by the editor (or with
`vedit proxy make`) into .vedit/proxies, named by the original's path, size and modification time.

Clips with tracked / AI effects (masks, background removal, face AR, object focus) keep using the original even in
previews: those effects were computed in the original's pixel coordinates.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import media
from .project import Clip, Project, Source, resolve_media, state_dir

PROXY_LONG_SIDE = 1280  # enough for the half-size preview of a 1080x1920 canvas


def needs_proxy(src: Source) -> bool:
    return bool(src.has_video and max(src.width or 0, src.height or 0) > PROXY_LONG_SIDE)


def proxy_dir(pdir: Path) -> Path:
    d = state_dir(pdir) / "proxies"
    d.mkdir(exist_ok=True)
    return d


def proxy_path(pdir: Path, src: Source) -> Path:
    orig = resolve_media(pdir, src.path)
    try:
        st = orig.stat()
        tag = f"{orig}|{st.st_size}|{st.st_mtime}"
    except FileNotFoundError:
        tag = str(orig)
    return proxy_dir(pdir) / f"{orig.stem[:40]}-{hashlib.sha1(tag.encode()).hexdigest()[:12]}.mp4"


def available(pdir: Path, src: Source) -> Path | None:
    """The finished proxy for this source, if there is one."""
    if not needs_proxy(src):
        return None
    p = proxy_path(pdir, src)
    return p if p.exists() else None


def media_for(pdir: Path, project: Project, clip: Clip, preview: bool) -> Path:
    """The file a clip should be decoded from: the proxy in previews when it exists and the clip allows it."""
    src = project.sources[clip.source]
    orig = resolve_media(pdir, src.path)
    if not preview or any(e.type != "fx" for e in clip.effects):
        return orig
    return available(pdir, src) or orig


def make(pdir: Path, src: Source, should_stop=None) -> Path | None:
    """Build the proxy for one source (no-op when it isn't needed or exists). Returns its path."""
    if not needs_proxy(src):
        return None
    out = proxy_path(pdir, src)
    if out.exists():
        return out
    orig = resolve_media(pdir, src.path)
    if not orig.exists():
        return None
    tmp = out.with_name(f"tmp-{out.name}")
    L = PROXY_LONG_SIDE
    cmd = [media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(orig),
           "-vf", f"scale='if(gt(iw,ih),{L},-2)':'if(gt(iw,ih),-2,{L})':flags=bicubic,format=yuv420p",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-g", "15", "-bf", "0",  # short GOP: fast seeking
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(tmp)]
    try:
        media.run_with_progress(cmd, src.duration, None, should_stop)
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def status(pdir: Path, project: Project) -> list[dict]:
    rows = []
    for sid, src in project.sources.items():
        if not src.has_video:
            continue
        p = available(pdir, src)
        rows.append({"source": sid, "needed": needs_proxy(src), "ready": p is not None, "path": str(p) if p else None,
                     "size": f"{src.width}x{src.height}"})
    return rows


def clean(pdir: Path, project: Project) -> int:
    """Delete proxies no source uses any more. Returns how many were removed."""
    keep = {proxy_path(pdir, s).name for s in project.sources.values() if needs_proxy(s)}
    n = 0
    for f in proxy_dir(pdir).glob("*.mp4"):
        if f.name not in keep:
            f.unlink(missing_ok=True)
            n += 1
    return n

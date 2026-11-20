"""Proxy media: big sources get small copies that previews decode; exports and tracked effects use the original."""

import subprocess

import pytest

from vedit import media, proxies
from vedit.project import Effect, load
from vedit.render.graph import build

from .conftest import needs_ffmpeg


@pytest.fixture(scope="module")
def big_clip(tmp_path_factory):
    out = tmp_path_factory.mktemp("big") / "big.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=2560x1440:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=f=440:d=3", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], check=True)
    return out


def _inputs(cmd: list[str]) -> list[str]:
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]


def test_only_big_video_needs_a_proxy():
    from vedit.project import Source

    assert proxies.needs_proxy(Source(path="a.mp4", duration=1, width=3840, height=2160))
    assert proxies.needs_proxy(Source(path="a.mp4", duration=1, width=1080, height=1920))  # tall phone video
    assert not proxies.needs_proxy(Source(path="a.mp4", duration=1, width=1280, height=720))
    assert not proxies.needs_proxy(Source(path="a.m4a", duration=1, has_video=False))


@needs_ffmpeg
def test_proxy_is_used_by_previews_only(cli, big_clip):
    cli("import", str(big_clip))
    d = cli.project
    p = load(d)
    src = p.sources["s1"]
    assert proxies.available(d, src) is None
    assert cli("proxy", "status")["sources"][0]["ready"] is False

    cli("proxy", "make")
    px = proxies.available(d, src)
    info = media.probe(px)
    assert (info["width"], info["height"]) == (1280, 720)
    assert info["duration"] == pytest.approx(src.duration, abs=0.1)

    orig = str(d / src.path) if not src.path.startswith("/") else src.path
    prev, _ = build(p, d, d / "a.mp4", 0.5, "preview")
    exp, _ = build(p, d, d / "b.mp4", 1.0, "export")
    assert str(px) in _inputs(prev)
    assert str(px) not in _inputs(exp) and orig in _inputs(exp)  # the export reads the original (the library copy)

    # a clip with a tracked effect keeps the original (its masks are in the original's pixels)
    p.clips[0].effects.append(Effect(type="mask", params={"id": "mk1"}))
    assert str(proxies.media_for(d, p, p.clips[0], preview=True)) == orig


@needs_ffmpeg
def test_preview_and_frame_render_from_proxy(cli, big_clip):
    from vedit.render.frame import render_frame
    from vedit.render.runner import render_preview

    cli("import", str(big_clip))
    cli("proxy", "make")
    d = cli.project
    res = render_preview(load(d), d, progress=False)
    assert res["duration"] == pytest.approx(3.0, abs=0.1)
    assert render_frame(load(d), d, 1.0)[:2] == b"\xff\xd8"  # a JPEG


@needs_ffmpeg
def test_clean_removes_unused_proxies(cli, big_clip):
    cli("import", str(big_clip))
    cli("proxy", "make")
    d = cli.project
    stale = proxies.proxy_dir(d) / "old-123.mp4"
    stale.write_bytes(b"x")
    assert cli("proxy", "clean")["removed"] == 1
    assert not stale.exists() and proxies.available(d, load(d).sources["s1"]) is not None

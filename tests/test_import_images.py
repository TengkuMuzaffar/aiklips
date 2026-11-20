"""Still images (a logo PNG) import as sources, become layers or clips, and render."""

import shutil
import subprocess
from pathlib import Path

import pytest

from vedit import media
from vedit.project import VeditError, load

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _png(path: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0xE8552B:s=128x128",
                    "-frames:v", "1", str(path)], check=True)
    return path


def test_probe_reads_a_still_image(tmp_path):
    info = media.probe(_png(tmp_path / "logo.png"))
    assert info["duration"] == media.STILL_SECONDS and info["has_video"] and not info["has_audio"]
    assert (info["width"], info["height"]) == (128, 128)


def test_import_a_logo_then_use_it_as_layer_and_clip(tmp_path):
    from vedit.editor import actions
    from vedit.generate.executor import create_project

    pdir = tmp_path / "proj"
    create_project(pdir, "t", "reels")
    up = _png(tmp_path / "upload.png")
    res = actions.import_file(pdir, "glowbee logo.png", up, append=False, index=None)
    assert res["has_video"] and res["clip"] is None
    actions.apply(pdir, "layer_add_media", {"source": res["source"], "start": 0})
    assert any(l.type == "image" for l in load(pdir).layers)
    actions.apply(pdir, "insert_source", {"source": res["source"]})  # dropped on the video track: a 5 s still clip
    p = load(pdir)
    assert p.clips and p.clips[-1].out == media.STILL_SECONDS
    from vedit.render import runner

    out = tmp_path / "out.mp4"
    runner.render(p, pdir, out, 0.25, "preview", progress=False)
    assert media.probe(out)["duration"] >= media.STILL_SECONDS - 0.2  # the picture holds for the whole clip
    still = tmp_path / "f.png"
    media.extract_frame(out, p.duration - 1.0, still)
    from PIL import Image
    r, g, b = Image.open(still).convert("RGB").resize((1, 1)).getpixel((0, 0))
    assert r > 150 and b < 120  # orange, not black: the logo is still on screen near the end


def test_a_bad_file_leaves_no_copy_behind(tmp_path):
    from vedit.editor import actions
    from vedit.generate.executor import create_project

    pdir = tmp_path / "proj"
    create_project(pdir, "t", "reels")
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not a picture")
    with pytest.raises(VeditError):
        actions.import_file(pdir, "broken.png", bad, append=False, index=None)
    assert not list((pdir / "media").glob("broken*"))


def test_remove_media_with_what_uses_it_and_undo(tmp_path):
    from vedit import history
    from vedit.editor import actions
    from vedit.generate.executor import create_project

    pdir = tmp_path / "proj"
    create_project(pdir, "t", "reels")
    a = actions.import_file(pdir, "a.png", _png(tmp_path / "a.png"), append=False, index=None)["source"]
    b = actions.import_file(pdir, "b.png", _png(tmp_path / "b.png"), append=False, index=None)["source"]
    actions.apply(pdir, "insert_source", {"source": a})
    actions.apply(pdir, "layer_add_media", {"source": b, "start": 0})
    r = actions.apply(pdir, "source_remove", {"sources": [a, b]})  # several at once, one undo step
    p = load(pdir)
    assert a not in p.sources and b not in p.sources and not p.clips and not any(l.type == "image" for l in p.layers)
    assert "+2 on the timeline" in r["message"]
    from vedit.generate import footage

    assert list(footage.root().glob("*/file.png"))  # the file stays on disk (in the media library)
    history.undo(pdir)
    p = load(pdir)
    assert a in p.sources and b in p.sources and p.clips
    with pytest.raises(VeditError):
        actions.apply(pdir, "source_remove", {"sources": ["s99"]})

"""Green / blue screen keying on main-track clips (`bg remove --mode chroma`, ops/chroma.py): keyed inside the
FFmpeg graph, so there is no precompute, the instant frame works at once, and tuning never recomputes."""

import io
import subprocess

import numpy as np
import pytest
from PIL import Image

from vedit.editor import actions
from vedit.ops import chroma
from vedit.project import load
from vedit.render.frame import render_frame
from vedit.render.graph import filter_text

from .conftest import needs_ffmpeg

SCREEN = "#22C94A"  # a realistic (not pure) studio green


@pytest.fixture(scope="module")
def green_clip(tmp_path_factory):
    """3s 640x360: an orange square (the 'person') in front of an unevenly lit green screen."""
    out = tmp_path_factory.mktemp("gs") / "gs.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=c=0x{SCREEN[1:]}:s=640x360:d=3:r=25",
         "-f", "lavfi", "-i", "color=c=0xC86440:s=200x200:d=3:r=25",
         "-filter_complex", "[0][1]overlay=220:80,vignette=PI/5,format=yuv420p", "-c:v", "libx264", str(out)],
        check=True,
    )
    return out


def _px(img: Image.Image, fx: float, fy: float) -> tuple[int, int, int]:
    """Pixel at a fraction of the frame."""
    return img.getpixel((int(img.width * fx), int(img.height * fy)))[:3]


def test_find_key_ignores_the_subject_and_rejects_no_screen():
    frame = np.zeros((90, 160, 3), np.uint8)
    frame[:] = (30, 200, 70)
    frame[20:70, 50:110] = (200, 100, 60)  # subject in the middle
    r, g, b = chroma.rgba(chroma.find_key(frame))[:3]
    assert abs(r - 30) < 4 and abs(g - 200) < 4 and abs(b - 70) < 4
    blue = np.zeros((90, 160, 3), np.uint8)
    blue[:] = (20, 60, 210)
    assert chroma.screen_type(chroma.find_key(blue)) == "blue"
    with pytest.raises(Exception, match="green or blue screen"):
        chroma.find_key(np.full((90, 160, 3), 128, np.uint8))


def test_clean_clamps_and_validates():
    out = chroma.clean({"similarity": 5, "blend": -1, "spill": 2, "shrink": 9, "key": "#00ff00"})
    assert out == {"similarity": 0.6, "blend": 0.0, "spill": 1.0, "shrink": 4, "key": "#00ff00"}
    with pytest.raises(Exception):
        chroma.clean({"key": "not-a-colour"})
    assert chroma.shrink_chain(0) is None
    assert chroma.shrink_chain(2).count("erosion") == 2
    assert "despill" not in chroma.key_chain({"key": "#00ff00", "spill": 0})
    assert "despill=type=blue" in chroma.key_chain({"key": "#1040E0"})


@needs_ffmpeg
def test_chroma_keys_in_the_graph_without_precompute(cli, green_clip):
    cli("canvas", "--preset", "landscape")  # same shape as the clip, so frame fractions match the source
    cli("import", str(green_clip))
    res = cli("bg", "remove", "--mode", "chroma", "--replace", "color:#0000FF")
    key = res["keys"]["c1"]
    kr, kg, kb = chroma.rgba(key)[:3]
    assert kg - max(kr, kb) > 60, key  # sampled the screen, not the orange subject
    d = cli.project
    p = load(d)
    script = filter_text(p, d)
    assert "chromakey=" in script and "despill" in script and "erosion" in script
    assert not list((d / ".vedit").rglob("bg-*.mp4"))  # nothing computed or cached

    # the instant frame works at once (the AI modes raise NotReady here)
    img = Image.open(io.BytesIO(render_frame(p, d, 1.0))).convert("RGB")
    r, g_, b = _px(img, 0.05, 0.5)  # screen -> replacement blue
    assert b > 180 and g_ < 90, (r, g_, b)
    r, g_, b = _px(img, 0.5, 0.5)  # subject untouched (orange)
    assert r > 150 and b < 120 and g_ < 140, (r, g_, b)


@needs_ffmpeg
def test_tune_changes_only_compositing(cli, green_clip):
    cli("import", str(green_clip))
    cli("bg", "remove", "--mode", "chroma", "--key", SCREEN)
    cli("bg", "tune", "--similarity", "0.2", "--spill", "0", "--shrink", "0")
    e = load(cli.project).clips[0].effects[0]
    assert e.params["similarity"] == 0.2 and e.params["spill"] == 0 and e.params["shrink"] == 0
    assert e.params["key"] == SCREEN
    cli("bg", "tune", "--spill", "0.5", "--clip", "c1")
    cli("bg", "clear")
    cli("bg", "tune", "--spill", "0.5", ok=False)  # nothing to tune any more
    cli("bg", "remove", "--mode", "nope", ok=False)


@needs_ffmpeg
def test_editor_actions(cli, green_clip):
    cli("import", str(green_clip))
    d = cli.project
    p = load(d)
    actions._PDIR[0] = d
    actions._OPS["bg_remove"](p, {"clip": "c1", "mode": "chroma", "replace": "blur:20"})
    e = p.clips[0].effects[0]
    assert e.params["mode"] == "chroma" and e.params["key"].startswith("#") and e.params["replace"] == "blur:20"
    actions._OPS["bg_set"](p, {"clip": "c1", "similarity": 0.3, "replace": "color:#FF0000"})
    assert e.params["similarity"] == 0.3 and e.params["replace"] == "color:#FF0000"
    actions._OPS["bg_remove"](p, {"clip": "c1", "mode": "off"})
    assert not p.clips[0].effects

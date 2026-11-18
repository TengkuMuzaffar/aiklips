"""Outline (point) tracking for masks: exact-ground-truth clips from scripts/mask_bench.py."""

import importlib.util
import subprocess
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import needs_ffmpeg

cv2 = pytest.importorskip("cv2")

from vedit.ops import outline, sam  # noqa: E402
from vedit.ops import track as tr  # noqa: E402
from vedit.project import load  # noqa: E402

_spec = importlib.util.spec_from_file_location("mask_bench", Path(__file__).resolve().parents[1] / "scripts" / "mask_bench.py")
mb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mb)


def _label() -> np.ndarray:
    rng = np.random.default_rng(1)
    label = np.full((150, 260, 3), 230, np.uint8)
    for _ in range(30):
        cv2.putText(label, "".join(rng.choice(list("HONDA0123456789")) for _ in range(5)),
                    (int(rng.integers(0, 200)), int(rng.integers(20, 150))), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 200), 2)
    return label


def _ious(cli, quads, tracker, sam_mode="off", loose=0.0):
    r = mb.score(cli, cli.project, quads, tracker, sam_mode, loose)
    return r


@pytest.fixture
def moving(tmp_path, cli):
    clip = tmp_path / "moving.mp4"
    quads = mb.make_clip(clip, _label(), 3.0, hard=True)
    cli("import", str(clip))
    return quads


def test_points_roundtrip_and_holes():
    m = np.zeros((200, 200), np.uint8)
    cv2.rectangle(m, (40, 40), (160, 160), 1, -1)
    cv2.rectangle(m, (80, 80), (120, 120), 0, -1)  # a hole
    polys = outline.contours_of(cv2, m)
    assert len(polys) == 2 and all(len(p) >= 8 for p in polys)  # densified, outer + hole
    back = outline.poly_mask(cv2, polys, 200, 200) > 127
    assert back[100, 100] == 0 and back[50, 50] == 1
    assert outline._iou(back, m > 0) > 0.95


def test_snap_pulls_the_outline_onto_the_edge():
    img = np.zeros((100, 100), np.float32)
    img[:, 50:] = 255
    g = cv2.magnitude(cv2.Sobel(img, cv2.CV_32F, 1, 0), cv2.Sobel(img, cv2.CV_32F, 0, 1))
    line = np.float32([[47, y] for y in range(10, 90, 4)])  # 3 px left of the edge
    out = outline.snap(cv2, g, np.concatenate([line, line[::-1] + [0.01, 0]]), radius=5, pull=1.0)
    assert abs(np.median(out[: len(line), 0]) - 49.5) < 1.5


def test_cut_detection():
    rng = np.random.default_rng(0)
    scene = cv2.GaussianBlur(cv2.resize(rng.integers(0, 255, (12, 24)).astype(np.float32), (96, 48)), (0, 0), 1.5)
    a = scene[:, :48].copy()
    assert not outline.is_cut(cv2, a, scene[:, 10:58] * 0.95)  # a fast pan with an exposure change is not a cut
    assert outline.is_cut(cv2, a, np.full_like(a, 20))  # to a dark title card is
    other = cv2.GaussianBlur(cv2.resize(np.random.default_rng(9).integers(0, 90, (12, 12)).astype(np.float32), (48, 48)), (0, 0), 1.5)
    assert outline.is_cut(cv2, a, other)  # to another, darker scene is


@needs_ffmpeg
def test_outline_tracker_beats_the_box_tracker(cli, moving):
    box = _ious(cli, moving, "box")
    out = _ious(cli, moving, "outline")
    assert out["mean_iou"] > 0.9 and out["frames_over_0.8"] > 0.95, out
    assert out["mean_iou"] > box["mean_iou"] + 0.15, (box, out)


@needs_ffmpeg
def test_mask_stops_when_the_object_leaves_and_at_a_cut(tmp_path, cli):
    label = _label()
    clip = tmp_path / "leave.mp4"
    W, H, n = 360, 640, 60
    proc = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", "30",
                             "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], stdin=subprocess.PIPE)
    rng = np.random.default_rng(2)
    bg = cv2.GaussianBlur(cv2.resize(rng.integers(0, 255, (80, 45, 3)).astype(np.uint8), (W, H)), (0, 0), 2)
    for i in range(n):
        f = bg.copy()
        if i < 40:  # slides right and out of the picture
            x = 60 + i * 9
            sub = f[200:350, x:x + 260]
            sub[:] = label[:, : sub.shape[1]]
        else:  # then a cut to another shot
            f = np.full_like(bg, 30)
        proc.stdin.write(f.tobytes())
    proc.stdin.close()
    proc.wait()
    cli("import", str(clip))
    cli("mask", "add", "--at", "0", "--box", "60,200,260,150", "--space", "source", "--style", "blur", "--sam", "off")
    p = load(cli.project)
    e = p.clips[0].effects[0]
    data = tr.track(cli.project, p, p.clips[0].source, e.params, lambda s: None)
    assert e.params["tracker"] == "auto" and outline.resolve_tracker(e.params) == "outline" and data["how"] == "box"
    assert outline.frame_polys(data, 5) is not None
    assert all(outline.frame_polys(data, i) is None for i in range(45, n))  # gone: no mask stuck on the next shot
    m = tr.mask_at(cv2, e.params, data, 50, 360, 640)
    assert not m.any()


@needs_ffmpeg
def test_old_masks_keep_the_box_tracker(cli, test_clip):
    cli("import", str(test_clip))
    cli("mask", "add", "--at", "1", "--box", "10%,10%,20%,20%", "--tracker", "box")
    p = load(cli.project)
    e = p.clips[0].effects[0]
    data = tr.track(cli.project, p, p.clips[0].source, e.params, lambda s: None)
    assert "outline" not in data and outline.frame_polys(data, 3) is False
    cli("mask", "set", e.params["id"], "--tracker", "outline")
    assert load(cli.project).clips[0].effects[0].params["tracker"] == "outline"
    with pytest.raises(AssertionError):
        cli("mask", "add", "--at", "1", "--box", "10%,10%,20%,20%", "--tracker", "magic")


SAM_READY = sam.installed() and (Path.home() / ".cache" / "aiklips" / "models" / sam.CHECKPOINT).exists()


@needs_ffmpeg
@pytest.mark.skipif(not SAM_READY, reason="mask-ai extra / SAM checkpoint not installed")
def test_sam_finds_the_real_outline_from_a_rough_box(cli, moving):
    plain = _ious(cli, moving, "outline", "off", loose=0.12)
    with_sam = _ious(cli, moving, "outline", "on", loose=0.12)
    assert with_sam["how"] == "sam"
    assert with_sam["mean_iou"] > 0.88 and with_sam["mean_iou"] > plain["mean_iou"] + 0.15, (plain, with_sam)


@needs_ffmpeg
def test_mask_set_all_switches_every_mask_in_one_step(cli, test_clip):
    cli("import", str(test_clip))
    cli("mask", "add", "--at", "1", "--box", "10%,10%,20%,20%", "--tracker", "box")
    cli("mask", "add", "--at", "2", "--box", "50%,50%,20%,20%", "--tracker", "box")
    steps = len(cli("history")["history"])
    cli("mask", "set", "all", "--tracker", "outline")
    assert {e.params["tracker"] for e in load(cli.project).clips[0].effects} == {"outline"}
    assert len(cli("history")["history"]) == steps + 1


def test_a_new_tracker_version_re_renders_outline_masks(tmp_path, monkeypatch):
    from vedit.ops import effects
    from vedit.project import Effect

    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    e = Effect(type="mask", params={"id": "mk1", "tracker": "outline", "box": [0, 0, 1, 1]})
    old = effects._key(f, e)
    monkeypatch.setattr(outline, "VERSION", "outline-test")
    assert effects._key(f, e) != old
    box = Effect(type="mask", params={"id": "mk1", "tracker": "box", "box": [0, 0, 1, 1]})
    k = effects._key(f, box)
    monkeypatch.setattr(outline, "VERSION", "outline-test-2")
    assert effects._key(f, box) == k  # the box tracker's renders are unaffected

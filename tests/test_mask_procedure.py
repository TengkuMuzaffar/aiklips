"""Precise masks: the segment tracker (SAM 2 video), `mask find`, `mask check`, `mask fix` and the auditor's check."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import needs_ffmpeg

cv2 = pytest.importorskip("cv2")

from vedit.ops import maskcheck, outline, sam  # noqa: E402
from vedit.ops import track as tr  # noqa: E402
from vedit.project import Effect, load  # noqa: E402

_spec = importlib.util.spec_from_file_location("mask_bench", Path(__file__).resolve().parents[1] / "scripts" / "mask_bench.py")
mb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mb)
SAM_READY = sam.installed() and (Path.home() / ".cache" / "aiklips" / "models" / sam.CHECKPOINT).exists()


def _label() -> np.ndarray:
    rng = np.random.default_rng(1)
    label = np.full((150, 260, 3), 230, np.uint8)
    for _ in range(30):
        cv2.putText(label, "".join(rng.choice(list("HONDA0123456789")) for _ in range(5)),
                    (int(rng.integers(0, 200)), int(rng.integers(20, 150))), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 200), 2)
    return label


def test_auto_uses_segment_only_when_sam_is_there(monkeypatch):
    monkeypatch.delenv("VEDIT_NO_SAM", raising=False)
    monkeypatch.setattr(sam, "installed", lambda: True)
    assert outline.resolve_tracker({"tracker": "auto"}) == "segment"
    assert outline.resolve_tracker({"tracker": "auto", "sam": "off"}) == "outline"
    assert outline.resolve_tracker({"tracker": "segment", "sam": "off"}) == "segment"  # asked for by name
    assert outline.resolve_tracker({"tracker": "outline"}) == "outline"
    assert outline.resolve_tracker({}) == "box"  # masks from before trackers existed
    monkeypatch.setattr(sam, "installed", lambda: False)
    assert outline.resolve_tracker({"tracker": "auto"}) == "outline"
    assert outline.resolve_tracker({"tracker": "segment"}) == "segment"  # installs SAM on the next render


def test_mask_check_and_find_are_read_only_for_the_ai_but_fix_is_an_edit():
    from vedit.editor.agent import vedit_kind

    assert vedit_kind(["mask", "check", "mk1"]) == "read"
    assert vedit_kind(["mask", "find", "--at", "2"]) == "read"
    assert vedit_kind(["mask", "fix", "mk1", "--at", "2", "--box", "1,2,3,4"]) == "edit"


def test_segment_masks_re_render_when_the_tracker_changes(tmp_path, monkeypatch):
    from vedit.ops import effects, segment

    monkeypatch.setattr(sam, "installed", lambda: True)
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    e = Effect(type="mask", params={"id": "mk1", "tracker": "auto", "box": [0, 0, 1, 1]})
    k = effects._key(f, e)
    monkeypatch.setattr(segment, "VERSION", "segment-test")
    assert effects._key(f, e) != k
    fixed = Effect(type="mask", params={**e.params, "fixes": [{"at": 1.0, "boxes": [[0, 0, 0.5, 0.5]]}]})
    assert effects._key(f, fixed) != effects._key(f, e)


@needs_ffmpeg
def test_mask_fix_adds_removes_and_switches_to_segment(cli, test_clip):
    cli("import", str(test_clip))
    cli("mask", "add", "--at", "1", "--box", "10%,10%,20%,20%", "--tracker", "outline", "--sam", "off")
    r = cli("mask", "fix", "mk1", "--at", "2", "--box", "12%,11%,20%,20%")
    assert r["tracker"] == "segment" and len(r["fixes"]) == 1
    e = load(cli.project).clips[0].effects[0]
    assert e.params["tracker"] == "segment" and e.params["sam"] == "on"  # a fix needs SAM
    assert abs(e.params["fixes"][0]["at"] - 2.0) < 1e-6 and len(e.params["fixes"][0]["boxes"][0]) == 4
    cli("mask", "fix", "mk1", "--at", "3", "--space", "source", "--box", "50%,50%,10%,10%")
    cli("mask", "fix", "mk1", "--at", "2", "--box", "13%,11%,20%,20%")  # same moment again: replaced, not added
    fx = load(cli.project).clips[0].effects[0].params["fixes"]
    assert [f["at"] for f in fx] == [2.0, 3.0] and fx[1]["boxes"][0] == [0.5, 0.5, 0.1, 0.1]
    cli("mask", "fix", "mk1", "--remove-at", "3")
    assert [f["at"] for f in load(cli.project).clips[0].effects[0].params["fixes"]] == [2.0]
    cli("mask", "fix", "mk1", "--clear")
    assert "fixes" not in load(cli.project).clips[0].effects[0].params
    cli("mask", "fix", "mk1", "--at", "2", ok=False)  # needs a box


@needs_ffmpeg
def test_mask_check_profiles_motion_and_draws_a_sheet(cli, tmp_path, monkeypatch):
    clip = tmp_path / "m.mp4"
    quads = mb.make_clip(clip, _label(), 2.0, hard=False)
    cli("import", str(clip))
    q = quads[0]
    (x0, y0), (x1, y1) = q.min(0), q.max(0)
    box = f"{x0 / mb.W * 100:.2f}%,{y0 / mb.H * 100:.2f}%,{(x1 - x0) / mb.W * 100:.2f}%,{(y1 - y0) / mb.H * 100:.2f}%"
    cli("mask", "add", "--at", "0", "--space", "source", "--box", box, "--tracker", "outline", "--sam", "off")
    monkeypatch.setattr(sam, "installed", lambda: False)  # motion + jumps + sheet only (no SAM re-check)
    r = cli("mask", "check", "mk1")
    assert r["tracker"] == "outline" and r["frames"] >= 55
    mo = r["motion"]
    assert mo["visible_share"] > 0.95 and 0.6 < mo["scale_min"] <= 1.0 <= mo["scale_max"] < 1.6
    assert r["verdict"] in ("good", "check") and r["advice"]
    assert Path(r["sheet"]).exists() and cv2.imread(r["sheet"]).shape[1] > 1000


def test_check_flags_a_mask_that_jumps(tmp_path, monkeypatch, make_project):
    """A tracked outline that snaps to something else for a frame is flagged at that moment, with a fix command."""
    from vedit.ops import maskcheck as mc

    p = make_project(1)
    c = p.clips[0]
    src = p.sources[c.source]
    fps = 30.0
    n = int(round(c.out * fps)) + 1
    sq = lambda x, y, s: [[[x, y], [x + s, y], [x + s, y + s], [x, y + s]]]  # noqa: E731
    polys = [sq(0.3 + 0.001 * i, 0.4, 0.2) for i in range(n)]
    polys[int(c.in_ * fps) + 20] = sq(0.7, 0.05, 0.08)  # one frame somewhere else, much smaller
    data = {"fps": fps, "boxes": [[0.3, 0.4, 0.2, 0.2]] * n, "outline": {"start": 0, "polys": polys, "conf": [1.0] * n}}
    monkeypatch.setattr(tr, "track", lambda *a, **k: data)
    monkeypatch.setattr(mc, "source_dims", lambda s: (360, 640, fps))
    monkeypatch.setattr(sam, "installed", lambda: False)
    import vedit.ops.track as trm

    monkeypatch.setattr(trm, "_frames", lambda *a, **k: iter([np.zeros((640, 360, 3), np.uint8)]))
    e = Effect(type="mask", params={"id": "mk1", "box": [0.3, 0.4, 0.2, 0.2], "at": c.in_, "range": [c.in_, c.out], "tracker": "outline"})
    del src
    r = mc.check(tmp_path, p, c, e, tmp_path / "s.jpg", lambda m: None)
    kinds = {it["kind"] for it in r["issues"]}
    assert "size jump" in kinds or "position jump" in kinds
    assert r["verdict"] != "good" and any("mask fix mk1" in a for a in r["advice"])


def test_audit_checks_masks_once_per_setup(tmp_path, monkeypatch, make_project):
    from vedit import audit as au

    p = make_project(1)
    p.clips[0].effects.append(Effect(type="mask", params={"id": "mk1", "box": [0.1, 0.1, 0.2, 0.2], "tracker": "outline"}))
    calls = []

    def fake(pdir, project, clip, eff, out, log, verify=True):
        calls.append(eff.params["id"])
        return {"verdict": "poor", "issues": [{"t": 1.5, "kind": "outline disagrees", "detail": "x"}], "agreement": {"min": 0.4}}

    monkeypatch.setattr(maskcheck, "check", fake)
    (tmp_path / ".vedit").mkdir(exist_ok=True)
    f1 = au.check_masks(p, tmp_path)
    f2 = au.check_masks(p, tmp_path)
    assert calls == ["mk1"]  # cached the second time
    assert f1 == f2 and f1[0]["severity"] == "must-fix" and "mask fix mk1" in f1[0]["fix"] and f1[0]["at"] == 1.5


@needs_ffmpeg
@pytest.mark.skipif(not SAM_READY, reason="mask-ai extra / SAM checkpoint not installed")
def test_segment_tracker_follows_a_label_coming_into_view(cli, tmp_path):
    """The label starts half outside the picture and the camera pans until it's all in view: SAM 2's memory alone
    keeps only the half it first saw; growth re-outlines bring in the rest (the outline tracker scored 0.90 here)."""
    clip = tmp_path / "edge.mp4"
    truth = mb.make_scene(clip, _label(), 3.0, "edge")
    cli("import", str(clip))
    r = mb.score_scene(cli, cli.project, truth, "segment", "on")
    assert r["how"] == "segment" and r["mean_iou"] > 0.9 and r["iou_by_second"][-1] > 0.9, r


@needs_ffmpeg
@pytest.mark.skipif(not SAM_READY, reason="mask-ai extra / SAM checkpoint not installed")
def test_a_fix_puts_the_mask_where_it_says(cli, tmp_path):
    """Two identical labels; the mask starts on the left one. A fix at 1 s says it's the right one from then on."""
    W, H, fps = 360, 640, 30
    label = _label()
    rng = np.random.default_rng(0)
    bg = cv2.GaussianBlur(cv2.resize(rng.integers(0, 255, (H // 8, W // 8, 3)).astype(np.uint8), (W, H)), (0, 0), 2)
    import subprocess

    clip = tmp_path / "two.mp4"
    proc = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                             "-r", str(fps), "-i", "-", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(clip)],
                            stdin=subprocess.PIPE)
    small = cv2.resize(label, (120, 70))
    for _ in range(60):
        f = bg.copy()
        f[200:270, 20:140] = small
        f[400:470, 220:340] = small
        proc.stdin.write(f.tobytes())
    proc.stdin.close()
    proc.wait()
    cli("import", str(clip))
    cli("mask", "add", "--at", "0", "--space", "source", "--box", f"{20 / W * 100}%,{200 / H * 100}%,{120 / W * 100}%,{70 / H * 100}%",
        "--tracker", "segment")
    cli("mask", "fix", "mk1", "--at", "1", "--space", "source",
        "--box", f"{220 / W * 100}%,{400 / H * 100}%,{120 / W * 100}%,{70 / H * 100}%")
    p = load(cli.project)
    e = p.clips[0].effects[0]
    data = tr.track(cli.project, p, p.clips[0].source, e.params, lambda s: None)
    assert data["tracker"] == "segment" and 30 in data["anchors"]
    early, late = tr.mask_at(cv2, {**e.params, "expand": 1.0, "feather": 0}, data, 5, W, H) > 127, \
        tr.mask_at(cv2, {**e.params, "expand": 1.0, "feather": 0}, data, 50, W, H) > 127
    assert early[235, 80] and not early[435, 280]  # before the fix: the left label
    assert late[435, 280] and not late[235, 80]  # after it: the right one

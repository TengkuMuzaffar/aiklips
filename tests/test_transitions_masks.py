"""Transitions between clips, tracked region masks and object focus."""

import importlib.util
import subprocess

import pytest

from vedit import media
from vedit.ops import cut, track
from vedit.project import Clip, VeditError, load

from .conftest import needs_ffmpeg

needs_cv2 = pytest.mark.skipif(importlib.util.find_spec("cv2") is None, reason="needs the 'ai' extra (OpenCV)")


# ---- transitions -------------------------------------------------------------------

def test_set_and_clear_transitions(make_project):
    p = make_project(3)
    assert cut.set_transition(p, "crossfade", 0.4) == ["c2", "c3"]
    assert p.clip("c2").transition.type == "fade"  # alias resolved
    assert p.clip("c1").transition is None
    with pytest.raises(VeditError):
        cut.set_transition(p, "fade", 0.5, "c1")  # nothing to transition from
    with pytest.raises(VeditError):
        cut.set_transition(p, "not-a-transition", 0.5)
    cut.clear_transition(p, "c2")
    assert p.clip("c2").transition is None and p.clip("c3").transition is not None


def test_transitions_keep_timeline_length_and_split_drops_it(make_project):
    p = make_project(2)
    before = p.duration
    cut.set_transition(p, "slideleft", 0.6)
    assert p.duration == pytest.approx(before)
    cut.split_at(p, 7.5)  # inside c2: the new right half is a hard cut
    assert p.clips[1].transition is not None and p.clips[2].transition is None


def test_transition_lengths_are_clamped_to_short_clips(make_project):
    p = make_project(1)
    p.clips = [Clip(id="c1", source="s1", in_=0, out=0.3), Clip(id="c2", source="s1", in_=1, out=5),
               Clip(id="c3", source="s1", in_=5, out=5.2)]
    cut.set_transition(p, "fade", 2.0)
    tl = cut.transition_lengths(p)
    assert tl[0] == 0 and tl[1] == pytest.approx(0.3) and tl[2] == pytest.approx(0.2)


@needs_ffmpeg
def test_export_with_transitions_keeps_duration(cli, test_clip, tmp_path):
    cli("import", str(test_clip), str(test_clip))
    cli("delete", "--from", "2", "--to", "3")
    cli("transition", "fade", "--duration", "0.8")
    cli("transition", "zoomin", "--clip", "c2", "--duration", "0.5")
    graph = __import__("vedit.render.graph", fromlist=["filter_text"]).filter_text(load(cli.project), cli.project)
    assert "xfade=transition=fade" in graph and "xfade=transition=zoomin" in graph
    out = tmp_path / "t.mp4"
    r = cli("export", str(out), "--scale", "0.25")
    assert r["duration"] == pytest.approx(11.0, abs=0.05)
    assert media.probe(out)["duration"] == pytest.approx(11.0, abs=0.15)


# ---- geometry ----------------------------------------------------------------------

def test_canvas_box_maps_to_source_for_cover(make_project):
    p = make_project(1)  # 1920x1080 source on a 1080x1920 canvas, cover, centred
    c = p.clips[0]
    # cover scale = 1920/1080; the visible source strip is 1080/s wide, centred
    s = 1920 / 1080
    x, y = track.canvas_to_source(p, c, 540, 960)
    assert x == pytest.approx(960, abs=0.5) and y == pytest.approx(540, abs=0.5)
    box = track.box_to_source(p, c, (0, 0, 1080, 1920), 0)
    assert box[2] == pytest.approx(1080 / s / 1920, abs=1e-3) and box[3] == pytest.approx(1.0, abs=1e-3)


def test_parse_box_percent_and_errors():
    assert track.parse_box("10%,50%,20%,5%", 1000, 2000) == (100, 1000, 200, 100)
    with pytest.raises(VeditError):
        track.parse_box("1,2,3", 10, 10)


def test_ffmpeg_expression_is_piecewise_linear():
    e = track._expr([0, 1, 2, 3], [0, 10, 10, 40])
    assert e.count("if(") == 2  # balanced tree, not a chain
    assert "(10+30*" in e  # last segment ramps 10 -> 40


# ---- masks & focus (real tracking) --------------------------------------------------

@pytest.fixture(scope="module")
def moving_label(tmp_path_factory):
    """4s 640x360 clip with a white label drifting right on a textured background."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    out = tmp_path_factory.mktemp("trk") / "label.mp4"
    W, H, fps = 640, 360, 30
    proc = subprocess.Popen([media.ffmpeg_bin(), "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                             "-r", str(fps), "-i", "-", "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    rng = np.random.default_rng(1)
    bg = cv2.GaussianBlur((rng.random((H, W, 3)) * 255).astype(np.uint8), (0, 0), 9)
    for i in range(4 * fps):
        f = bg.copy()
        x = 200 + int(30 * i / fps)
        cv2.rectangle(f, (x, 140), (x + 180, 210), (255, 255, 255), -1)
        cv2.putText(f, "LABEL", (x + 20, 190), cv2.FONT_HERSHEY_DUPLEX, 1.2, (0, 0, 200), 2)
        proc.stdin.write(f.tobytes())
    proc.stdin.close()
    proc.wait()
    return out


@needs_ffmpeg
@needs_cv2
def test_tracked_mask_follows_the_label(cli, moving_label):
    cli("import", str(moving_label))
    r = cli("mask", "add", "--box", "200,140,180,70", "--space", "source", "--at", "0")
    assert r["mask"] == "mk1"
    p = load(cli.project)
    data = track.track(cli.project, p, "s1", p.clips[0].effects[0].params, lambda m: None)
    first, last = data["boxes"][0], data["boxes"][-1]
    # the label moves 30px/s * ~4s = ~117px right on a 640px frame
    assert (last[0] - first[0]) * 640 == pytest.approx(117, abs=20)
    assert abs(last[1] - first[1]) * 360 < 12  # well inside the mask's 10% margin
    cli("mask", "set", "mk1", "--style", "pixelate")
    assert load(cli.project).clips[0].effects[0].params["style"] == "pixelate"
    cli("frame", "--at", "1.0,3.5")  # renders without error
    cli("mask", "remove", "all")
    assert load(cli.project).clips[0].effects == []


@needs_ffmpeg
@needs_cv2
def test_focus_follow_and_spotlight_render(cli, moving_label, tmp_path):
    cli("import", str(moving_label))
    cli("focus", "add", "--box", "200,140,180,70", "--space", "source", "--mode", "both")
    graph = __import__("vedit.render.graph", fromlist=["filter_text"]).filter_text(load(cli.project), cli.project)
    assert "crop=w=" in graph and "colorchannelmixer" in graph
    r = cli("export", str(tmp_path / "f.mp4"), "--scale", "0.25")
    assert r["duration"] == pytest.approx(4.0, abs=0.1)
    cli("focus", "clear")
    assert load(cli.project).clips[0].effects == []


@needs_ffmpeg
def test_mask_needs_a_box_or_object(cli, test_clip):
    cli("import", str(test_clip))
    cli("mask", "add", ok=False)
    cli("mask", "add", "--box", "10%,10%,20%,20%", "--style", "sparkles", ok=False)


# ---- painted regions & style "remove" -----------------------------------------------------

def test_several_boxes_become_one_painted_region(make_project):
    p = make_project(1)  # 1920x1080 source, cover on a 1080x1920 canvas
    _, params, _ = track.region(p, None, None, 1.0, "10%,10%,20%,5%;30%,20%,10%,10%", None)
    assert params["shape"] == "paint" and len(params["paint"]) == 2
    x, y, w, h = params["box"]  # bounds of both rectangles, in source fractions
    r0, r1 = params["paint"][0]["rect"], params["paint"][1]["rect"]
    assert x == pytest.approx(r0[0]) and y == pytest.approx(r0[1])
    assert x + w == pytest.approx(r1[0] + r1[2]) and y + h == pytest.approx(r1[1] + r1[3])


def test_painted_region_errors_and_erase(make_project):
    p = make_project(1)
    c = p.clips[0]
    with pytest.raises(VeditError):
        track.paint_to_source(p, c, [], 0)
    with pytest.raises(VeditError):  # only erasing = nothing selected
        track.paint_to_source(p, c, [{"rect": [100, 100, 200, 200], "erase": True}], 0)
    box, items = track.paint_to_source(p, c, [{"pts": [[540, 960], [640, 960]], "r": 40},
                                              {"rect": [560, 940, 20, 20], "erase": True}], 0)
    assert items[1]["erase"] and not items[0]["erase"]
    assert box[2] > 0 and box[3] > 0


@needs_cv2
def test_painted_mask_follows_the_tracked_box():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    prm = {"box": [0.4, 0.4, 0.2, 0.2], "shape": "paint", "feather": 0, "expand": 1.0,
           "paint": [{"rect": [0.4, 0.4, 0.2, 0.2]}, {"rect": [0.45, 0.45, 0.05, 0.05], "erase": True}]}
    m = track.region_mask(cv2, prm, [0.4, 0.4, 0.2, 0.2], 100, 100)
    assert m[50, 58] == 255 and m[47, 47] == 0 and m[10, 10] == 0  # filled, erased hole, outside
    moved = track.region_mask(cv2, prm, [0.1, 0.1, 0.2, 0.2], 100, 100)
    assert moved[20, 28] == 255 and moved[50, 58] == 0  # the shape moved with the box
    assert np.count_nonzero(moved) == np.count_nonzero(m)


@needs_cv2
def test_remove_fills_from_the_surroundings():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    frame = np.full((200, 300, 3), (60, 120, 180), np.uint8)  # plain "cardboard" (BGR)
    cv2.rectangle(frame, (100, 70), (200, 130), (255, 255, 255), -1)  # a white label
    cv2.putText(frame, "LOGO", (110, 115), cv2.FONT_HERSHEY_DUPLEX, 1.0, (0, 0, 220), 2)
    m = np.zeros((200, 300), np.uint8)
    cv2.rectangle(m, (95, 65), (205, 135), 255, -1)
    out = track.remove_frame(cv2, frame, m, seed=1)
    assert out.shape == (200, 300, 4) and out[100, 150, 3] == 255 and out[5, 5, 3] == 0
    inside = out[75:125, 105:195, :3].reshape(-1, 3).astype(float)  # RGB
    assert np.abs(inside.mean(0) - [180, 120, 60]).max() < 6  # rebuilt as the background colour
    assert inside.std(0).max() < 8  # no trace of the white label or red text


@needs_ffmpeg
@needs_cv2
def test_remove_style_renders_as_an_overlay(cli, moving_label):
    cli("import", str(moving_label))
    r = cli("mask", "add", "--box", "200,140,180,70", "--space", "source", "--at", "0")
    assert load(cli.project).clips[0].effects[0].params["style"] == "remove"  # the default
    graph = __import__("vedit.render.graph", fromlist=["filter_text"]).filter_text(load(cli.project), cli.project)
    assert "[ovl" in graph and "gblur" not in graph  # composited as a rebuilt-background overlay
    cli("frame", "--at", "1.0,3.5")
    cli("mask", "add", "--at", "2", "--paint", '[{"pts": [[200, 900], [500, 900]], "r": 60}]')
    p = load(cli.project)
    assert p.clips[0].effects[1].params["shape"] == "paint" and p.clips[0].effects[1].params["expand"] == 1.0
    cli("mask", "add", "--at", "2", "--paint", "not json", ok=False)
    assert r["mask"] == "mk1"


def test_keep_strokes_become_a_separate_region(make_project):
    p = make_project(1)
    _, params, _ = track.region(p, None, None, 1.0, None, None, paint=[
        {"rect": [300, 600, 400, 200]}, {"rect": [200, 780, 700, 400], "keep": True}])
    assert len(params["paint"]) == 1 and "keep" in params
    assert params["keep"]["box"][1] > params["box"][1]  # the product sits below the label
    _, params, _ = track.region(p, None, None, 1.0, "30%,30%,20%,10%", None, keep="20%,38%,60%,30%")
    assert params["keep"]["paint"][0]["rect"]
    with pytest.raises(VeditError):  # only keep strokes = nothing to hide
        track.region(p, None, None, 1.0, None, None, paint=[{"rect": [200, 780, 700, 400], "keep": True}])


@needs_cv2
def test_remove_never_paints_over_or_samples_what_is_in_front():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    frame = np.full((200, 300, 3), (60, 120, 180), np.uint8)  # cardboard
    cv2.rectangle(frame, (100, 60), (200, 120), (255, 255, 255), -1)  # label
    cv2.rectangle(frame, (0, 110), (300, 200), (200, 200, 200), -1)  # silver product in front, overlapping it
    m = np.zeros((200, 300), np.uint8)
    cv2.rectangle(m, (90, 50), (210, 140), 255, -1)  # sloppy mask reaching into the product
    prot = np.zeros((200, 300), bool)
    prot[110:, :] = True
    out = track.remove_frame(cv2, frame, m, seed=1, protect=prot)
    assert out[130, 150, 3] == 0  # the product stays on top
    inside = out[62:105, 105:195, :3].reshape(-1, 3).astype(float)
    assert np.abs(inside.mean(0) - [180, 120, 60]).max() < 8  # filled with cardboard, not grey product

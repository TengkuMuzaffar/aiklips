"""Effects limited to part of a clip / overlay (start, end, fade in / out): render, split, instant frame, editing."""

import subprocess

import numpy as np
import pytest
from PIL import Image

from vedit import media
from vedit.ops import cut, fx_filters, overlays
from vedit.project import Clip, Project, Source, VeditError
from vedit.render import graph
from vedit.render.frame import frame_project


@pytest.fixture
def proj(test_clip):
    p = Project()
    p.canvas.width, p.canvas.height = 320, 180
    p.sources["s1"] = Source(path=str(test_clip), **media.probe(test_clip))
    p.clips.append(Clip(id="c1", source="s1", in_=0, out=4))
    return p


def grab(path, t):
    out = subprocess.run([media.ffmpeg_bin(), "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(180, 320, 3)


def test_invert_only_between_start_and_end(proj, tmp_path):
    e = fx_filters.add(proj, proj.clips[0], "invert")
    fx_filters.set_params(proj, e.params["id"], {"start": 1.0, "end": 2.0})
    plain = Project(**{**proj.model_dump(by_alias=True), "clips": [Clip(id="c1", source="s1", in_=0, out=4)]})
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    for p, out in ((proj, a), (plain, b)):
        args, _ = graph.build(p, tmp_path, out)
        subprocess.run(args, check=True, capture_output=True)
    diff = lambda t: np.abs(grab(a, t).astype(int) - grab(b, t).astype(int)).mean()
    assert diff(0.5) < 4 and diff(3.0) < 4  # untouched outside the window
    assert diff(1.5) > 40  # inverted inside it


def test_fade_and_whole_clip_use_valid_graphs(proj, tmp_path):
    e = fx_filters.add(proj, proj.clips[0], "glow")
    fx_filters.set_params(proj, e.params["id"], {"start": 0.5, "fade_in": 0.5, "fade_out": 0.5})
    fx_filters.add(proj, proj.clips[0], "vignette")  # whole clip, plain chain
    args, _ = graph.build(proj, tmp_path, tmp_path / "o.mp4")
    r = subprocess.run(args, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]


def test_timing_validation(proj):
    e = fx_filters.add(proj, proj.clips[0], "blur")
    fid = e.params["id"]
    fx_filters.set_params(proj, fid, {"end": 9})  # past the clip: whole rest of the clip
    assert e.params["end"] is None
    with pytest.raises(VeditError):
        fx_filters.set_params(proj, fid, {"start": 2, "end": 1})


def test_split_keeps_effects_on_the_same_moments(proj):
    e = fx_filters.add(proj, proj.clips[0], "blur")
    fx_filters.set_params(proj, e.params["id"], {"start": 1.0, "end": 3.0})
    late = fx_filters.add(proj, proj.clips[0], "invert")
    fx_filters.set_params(proj, late.params["id"], {"start": 3.0})
    cut.split_at(proj, 2.0)
    left, right = proj.clips
    lw = [(x.params["key"], x.params.get("start"), x.params.get("end")) for x in left.effects]
    rw = [(x.params["key"], x.params.get("start"), x.params.get("end")) for x in right.effects]
    assert lw == [("blur", 1.0, None)]  # invert (3s+) lives only in the right half
    assert rw == [("blur", 0.0, 1.0), ("invert", 1.0, None)]


def test_instant_frame_shifts_windows(proj):
    e = fx_filters.add(proj, proj.clips[0], "invert")
    fx_filters.set_params(proj, e.params["id"], {"start": 1.0, "end": 2.0})
    mini = frame_project(proj, 1.5)
    w = fx_filters.window(mini.clips[0].effects[0], mini.clips[0].duration)
    assert w is not None and w[0] < 0 < w[1]  # the frame at 1.5s is inside the window


def test_video_overlay_timed_filter_chain_runs(proj, tmp_path, test_clip):
    from vedit.render.layers import video_look

    l = overlays.add_video(proj, "s1", 0.0, 3.0)
    e = fx_filters.add(proj, l, "glow")
    fx_filters.set_params(proj, e.params["id"], {"start": 1.0, "end": 2.0, "fade_in": 0.3})
    fx_filters.add(proj, l, "blur")
    vf = "scale=160:90" + video_look(proj, tmp_path, l, 160, 90, 0.5, offset=0.0)
    r = subprocess.run([media.ffmpeg_bin(), "-v", "error", "-i", str(test_clip), "-t", "2.5", "-vf", vf, "-f", "null", "-"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_moving_an_effect_past_its_old_end(proj):
    """The timeline sends start + end together; the new start can be after the old end (the bug in the editor)."""
    e = fx_filters.add(proj, proj.clips[0], "invert")
    fid = e.params["id"]
    fx_filters.set_params(proj, fid, {"start": 0.5, "end": 1.0})
    fx_filters.set_params(proj, fid, {"start": 2.5, "end": 3.0})
    assert (e.params["start"], e.params["end"]) == (2.5, 3.0)


def test_replace_keeps_timing(proj):
    e = fx_filters.add(proj, proj.clips[0], "blur", {"amount": 30})
    fx_filters.set_params(proj, e.params["id"], {"start": 1, "end": 2, "fade_in": 0.2})
    fx_filters.replace(proj, e.params["id"], "glow")
    assert e.params["key"] == "glow" and "amount" in e.params and e.params["amount"] != 30 or e.params["key"] == "glow"
    assert (e.params["start"], e.params["end"], e.params["fade_in"]) == (1, 2, 0.2)


def test_hidden_tracks_are_not_drawn(proj, tmp_path):
    from vedit.project import Layer

    proj.layers.append(Layer(id="t1", type="text", text="HI", start=0, end=2, hidden=True))
    proj.main_hidden = True
    out = tmp_path / "h.mp4"
    args, _ = graph.build(proj, tmp_path, out)
    subprocess.run(args, check=True, capture_output=True)
    assert grab(out, 1.0).mean() < 3  # background only: no footage, no text

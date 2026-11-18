"""CapCut-style overlay tracks: rows that stack (top = drawn on top), shared by layers whose times don't overlap,
drag between tracks, video / music trimming from either edge."""

from pathlib import Path

from vedit.editor import actions
from vedit.ops import overlays
from vedit.project import Layer, MusicTrack, normalize_tracks
from vedit.render import graph


def ids(p):
    return [(l.id, l.track) for l in p.layers]


def test_new_layers_share_the_top_track_when_free(make_project):
    p = make_project()
    p.layers += [Layer(id="a", type="text", start=0, end=2), Layer(id="b", type="text", start=3, end=5),
                 Layer(id="c", type="text", start=1, end=4)]
    normalize_tracks(p)
    assert ids(p) == [("a", 0), ("b", 0), ("c", 1)]  # c overlaps both: new track above, so it is still drawn on top


def test_overlap_on_one_track_is_resolved_by_bumping_the_later_layer(make_project):
    p = make_project()
    p.layers += [Layer(id="a", type="text", start=0, end=4, track=0), Layer(id="b", type="text", start=2, end=5, track=0),
                 Layer(id="c", type="text", start=0, end=5, track=1)]
    normalize_tracks(p)
    assert ids(p) == [("a", 0), ("b", 1), ("c", 2)]


def test_place_layer_moves_between_tracks(make_project):
    p = make_project()
    p.layers += [Layer(id="a", type="text", start=0, end=2), Layer(id="b", type="text", start=3, end=5),
                 Layer(id="v", type="text", start=1, end=4)]
    overlays.place_layer(p, "v", 0, start=6, end=8)  # free spot on the bottom track: joins it
    assert ids(p) == [("a", 0), ("b", 0), ("v", 0)]
    overlays.place_layer(p, "v", 0, start=1, end=2.5)  # busy there: new track right above
    assert ids(p) == [("a", 0), ("b", 0), ("v", 1)]
    overlays.place_layer(p, "v", 0, new_track=True)  # new track at the bottom: goes under everything
    assert ids(p) == [("v", 0), ("a", 1), ("b", 1)]
    assert [l.id for l in p.layers][0] == "v"  # list order (render order) follows the tracks


def test_layer_place_trims_video_overlay_like_a_clip(tmp_path, make_project):
    from vedit.project import save

    p = make_project()
    l = overlays.add_video(p, "s1", 0.0, 6.0)
    save(tmp_path, p)
    actions.apply(tmp_path, "layer_place", {"layer": l.id, "start": 2.0, "end": 6.0, "src_in": 2.0})
    from vedit.project import load

    v = load(tmp_path).layer(l.id)
    assert (v.start, v.end, v.src_in) == (2.0, 6.0, 2.0)
    # stretched past the end of its 10s file: clamped to what is left (8s from src_in 2)
    actions.apply(tmp_path, "layer_place", {"layer": l.id, "start": 0.0, "end": 9.5})
    v = load(tmp_path).layer(l.id)
    assert v.end == 8.0


def test_music_trim_end_and_in(tmp_path, make_project, test_clip):
    from vedit.project import load, save

    p = make_project()
    p.sources["s1"].path = str(test_clip)
    p.sources["s1"].duration = 6.0
    p.clips[0].out = 6.0
    p.music.append(MusicTrack(id="m1", source="s1", start=1.0, loop=False))
    save(tmp_path, p)
    actions.apply(tmp_path, "music_set", {"music": "m1", "start": 2.0, "in": 1.0, "end": 6.0})
    m = load(tmp_path).music[0]
    assert (m.start, m.in_, m.end) == (2.0, 1.0, 6.0)
    args, _ = graph.build(load(tmp_path), tmp_path, tmp_path / "o.mp4")
    flag = next(a for a in args if "filter_complex" in a)
    fc = Path(args[args.index(flag) + 1]).read_text()
    assert "atrim=duration=4" in fc  # plays 2s..6s


def test_layer_add_media_on_a_new_bottom_track(tmp_path, make_project):
    from vedit.project import load, save

    p = make_project()
    p.layers.append(Layer(id="t1", type="text", text="Hi", start=0, end=5))
    save(tmp_path, p)
    actions.apply(tmp_path, "layer_add_media", {"source": "s1", "start": 1.0, "track": 0, "new_track": True})
    q = load(tmp_path)
    assert [l.type for l in q.layers] == ["video", "text"]  # the video sits under the text
    assert q.layers[0].start == 1.0


def test_clip_dragged_up_becomes_an_overlay_on_its_own_track(tmp_path, make_project):
    from vedit.project import load, save

    p = make_project(2)  # c1 0-5, c2 5-10 of s1
    p.layers.append(Layer(id="t1", type="text", text="Hi", start=0, end=4))
    save(tmp_path, p)
    actions.apply(tmp_path, "clip_to_overlay", {"clip": "c2", "start": 1.0, "track": 0, "new_track": True})
    q = load(tmp_path)
    assert [c.id for c in q.clips] == ["c1"]  # its place became a gap, dropped at the end of the video track
    v = q.layers[0]
    assert (v.type, v.source, v.src_in, v.start, v.end, v.track) == ("video", "s1", 5.0, 1.0, 6.0, 0)  # all 5s of it
    assert v.width == q.canvas.width and v.volume == 1.0  # full frame, keeps its sound
    assert q.layers[1].id == "t1" and q.layers[1].track == 1  # new bottom track: the text stays on top


def test_text_on_a_clip_moved_to_overlay_goes_with_it(tmp_path, make_project):
    from vedit.project import load, save

    p = make_project(2)  # c2 is 5-10
    p.layers.append(Layer(id="t1", type="text", text="On c2", start=6, end=8))
    save(tmp_path, p)
    actions.apply(tmp_path, "clip_to_overlay", {"clip": "c2", "start": 1.0, "track": 0, "new_track": True})
    t = load(tmp_path).layer("t1")
    assert (t.start, t.end) == (2.0, 4.0)  # same place on the footage, which now starts at 1s


def test_overlay_dragged_down_becomes_a_clip(tmp_path, make_project):
    from vedit.project import load, save

    p = make_project(1)
    l = overlays.add_video(p, "s1", 2.0, 5.0, src_in=3.0)
    save(tmp_path, p)
    actions.apply(tmp_path, "overlay_to_clip", {"layer": l.id, "index": 0})
    q = load(tmp_path)
    assert not q.layers
    c = q.clips[0]
    assert (c.source, c.in_, c.out) == ("s1", 3.0, 6.0) and len(q.clips) == 2


def test_effects_and_grade_on_a_video_overlay_are_rendered(make_project, tmp_path, test_clip):
    from vedit import media
    from vedit.ops import fx_filters
    from vedit.project import Grade, Source
    from vedit.render.layers import video_look

    p = make_project()
    p.sources["s1"] = Source(path=str(test_clip), **media.probe(test_clip))
    l = overlays.add_video(p, "s1", 0.0, 2.0)
    actions_p = fx_filters.add(p, l, "blur")
    l.grade = Grade(saturation=0.0)
    vf = video_look(p, tmp_path, l, 320, 180, 0.5)
    assert "blur" in vf.lower() and vf.startswith(",")
    # every split filter gets its own pads, so two in a row still make a valid single-input chain
    fx_filters.add(p, l, "glow")
    fx_filters.add(p, l, "glow")
    vf = video_look(p, tmp_path, l, 320, 180, 0.5)
    import subprocess
    r = subprocess.run([media.ffmpeg_bin(), "-v", "error", "-i", str(test_clip), "-t", "0.2", "-vf",
                        "scale=320:180" + vf, "-f", "null", "-"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert actions_p.params["key"] == "blur"


def test_text_layers_still_refuse_effects(make_project):
    import pytest
    from vedit.ops import fx_filters
    from vedit.project import VeditError

    p = make_project()
    p.layers.append(Layer(id="t1", type="text", text="x"))
    with pytest.raises(VeditError):
        fx_filters.add(p, p.layers[0], "blur")


def test_video_overlay_fill_fit_and_flip(make_project, tmp_path, test_clip):
    import pytest
    from vedit import media
    from vedit.project import Source, VeditError
    from vedit.render.layers import _VideoFrames, video_size

    p = make_project()
    p.sources["s1"] = Source(path=str(test_clip), **media.probe(test_clip))  # 1280x720
    l = overlays.add_video(p, "s1", 0.0, 1.0, x="70%", y="62%")
    overlays.set_fields(l, {"fit": "cover", "flip_h": "true"})
    assert (l.x, l.y, l.flip_h) == ("50%", "50%", True)  # filling the screen centres it
    assert video_size(p, l, 0.5) == (540, 960)
    fr = _VideoFrames(p, tmp_path, l, 0.5, 0.2)
    img = fr.next()
    fr.close()
    assert img is not None and img.size == (540, 960)
    overlays.set_fields(l, {"fit": "contain"})
    assert video_size(p, l, 0.5) == (540, 304)
    with pytest.raises(VeditError):
        overlays.set_fields(l, {"fit": "stretch"})


def test_colour_screen_element_covers_the_canvas_on_its_own_track(tmp_path, make_project):
    """Elements tab: a colour screen dragged onto "New track" at the bottom is a full-canvas rect under the text."""
    from vedit.project import load, save

    p = make_project()
    p.layers.append(Layer(id="t1", type="text", text="Hi", start=0, end=5))
    save(tmp_path, p)
    actions.apply(tmp_path, "layer_add_shape", {"shape": "rect", "full": True, "fill": "#123456", "start": 1.0,
                                                "track": 0, "new_track": True})
    q = load(tmp_path)
    sh = q.layers[0]
    assert [l.type for l in q.layers] == ["shape", "text"] and sh.track == 0 and q.layers[1].track == 1
    assert (sh.width, sh.height, sh.radius, sh.fill, sh.start) == (q.canvas.width, q.canvas.height, 0, "#123456", 1.0)


def test_library_element_dropped_on_a_track(tmp_path, make_project):
    from vedit.project import load, save

    p = make_project()
    p.layers += [Layer(id="t1", type="text", text="A", start=0, end=5, track=0),
                 Layer(id="t2", type="text", text="B", start=0, end=5, track=1)]
    save(tmp_path, p)
    actions.apply(tmp_path, "layer_add_adjust", {"start": 6.0, "track": 0})  # free there: shares track 0
    q = load(tmp_path)
    adj = next(l for l in q.layers if l.type == "adjust")
    assert adj.track == 0 and q.layer("t2").track == 1


def test_clip_moved_off_the_video_track_leaves_a_gap(tmp_path, make_project):
    """Equal tracks: the other clips keep their times (no ripple), like on any other track."""
    from vedit.project import load, save

    p = make_project(3)  # c1 0-3.33, c2 3.33-6.67, c3 6.67-10
    p.layers.append(Layer(id="t1", type="text", text="Hi", start=0, end=10))
    save(tmp_path, p)
    actions.apply(tmp_path, "clip_to_overlay", {"clip": "c2", "start": 3.333, "track": 0, "new_track": True})
    q = load(tmp_path)
    assert [c.gap for c in q.clips] == [False, True, False] and abs(q.clip_starts()[2] - 6.667) < 1e-3
    assert q.layer("t1").end == 10


def test_nothing_may_leave_zero_empty(tmp_path, make_project):
    import pytest

    from vedit.project import VeditError, load, save

    p = make_project(1)
    p.layers.append(Layer(id="t1", type="text", text="Hi", start=2, end=4))
    save(tmp_path, p)
    with pytest.raises(VeditError, match="0s"):  # c1 is the only thing at 0s
        actions.apply(tmp_path, "clip_place", {"clip": "c1", "start": 1.0})
    actions.apply(tmp_path, "layer_place", {"layer": "t1", "start": 0.0})  # now t1 covers 0s too...
    actions.apply(tmp_path, "clip_place", {"clip": "c1", "start": 1.0})  # ...so the clip may move right
    q = load(tmp_path)
    assert q.clips[0].gap and q.clip_starts()[1] == 1.0 and q.duration == 11.0
    with pytest.raises(VeditError, match="0s"):  # and now t1 is the only one
        actions.apply(tmp_path, "layer_place", {"layer": "t1", "start": 0.5})


def test_video_track_moves_among_the_tracks(tmp_path, make_project):
    from vedit.project import load, save

    p = make_project(1)
    p.layers += [Layer(id="a", type="text", start=0, end=2, track=0), Layer(id="b", type="text", start=0, end=2, track=1)]
    save(tmp_path, p)
    actions.apply(tmp_path, "track_video", {"to": "up"})
    assert load(tmp_path).main_z == 1  # a is under the video now, b over it
    actions.apply(tmp_path, "track_video", {"to": "top"})
    assert load(tmp_path).main_z == 2
    actions.apply(tmp_path, "track_video", {"to": "up"})  # already the top: stays
    assert load(tmp_path).main_z == 2


def test_video_layer_dropped_on_the_video_track_keeps_its_time(tmp_path, make_project):
    from vedit.project import load, save

    p = make_project(1)  # c1 0-10
    l = overlays.add_video(p, "s1", 12.0, 14.0, src_in=1.0)
    save(tmp_path, p)
    actions.apply(tmp_path, "overlay_to_clip", {"layer": l.id, "start": 12.0})
    q = load(tmp_path)
    assert [c.gap for c in q.clips] == [False, True, False] and q.clip_starts()[2] == 12.0

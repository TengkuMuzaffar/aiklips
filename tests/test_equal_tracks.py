"""Equal tracks: the video track (project.clips) sits at any height among the layer tracks (`Project.main_z` = how
many tracks are under it), the timeline is as long as whatever ends last, a project may have no clips at all."""

import subprocess

from vedit import media
from vedit.project import Layer, Project, Source, normalize_tracks
from vedit.render import graph


def _render(p: Project, tmp_path, at: float, fx: float = 0.1, fy: float = 0.1) -> tuple[int, int, int]:
    """Render the project at 1/4 size and return the RGB of the pixel at (fx, fy) of the frame at `at` seconds."""
    out = tmp_path / "out.mp4"
    cmd, _ = graph.build(p, tmp_path, out, scale=0.25)
    subprocess.run(cmd, check=True)
    w, h = p.canvas.width // 4, p.canvas.height // 4
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(out), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
    i = (int(h * fy) * w + int(w * fx)) * 3
    return raw[i], raw[i + 1], raw[i + 2]


def _screen(colour: str, **kw) -> Layer:
    return Layer(id=kw.pop("id", "sh1"), type="shape", shape="rect", width=1080, height=1920, radius=0, fill=colour, **kw)


def _red(px) -> bool:
    return px[0] > 180 and px[1] < 80 and px[2] < 80


def test_new_layer_goes_above_a_top_video_track(make_project):
    p = make_project()
    p.layers.append(Layer(id="a", type="text", start=0, end=2, track=0))
    p.main_z = 1  # the video is over track 0
    p.layers.append(Layer(id="b", type="text", start=3, end=4))
    normalize_tracks(p)
    assert (p.layer("b").track, p.main_z) == (1, 1)  # a new track above the video, not onto track 0 under it


def test_main_z_follows_renumbering(make_project):
    p = make_project()
    p.layers += [Layer(id="a", type="text", start=0, end=2, track=0), Layer(id="b", type="text", start=0, end=2, track=3)]
    p.main_z = 2  # between a (0) and b (3): empty tracks 1, 2 disappear
    normalize_tracks(p)
    assert (p.layer("a").track, p.layer("b").track, p.main_z) == (0, 1, 1)


def test_duration_is_the_longest_track(make_project):
    p = make_project()  # 10s of clips
    p.layers.append(Layer(id="t", type="text", start=8, end=14))
    p.layers.append(Layer(id="o", type="text", start=0))  # open-ended: runs to the end, doesn't set it
    assert (p.clips_end, p.duration) == (10.0, 14.0)


def test_colour_screen_under_a_letterboxed_video_shows_in_the_bars(tmp_path, test_clip):
    p = Project()
    p.sources["s1"] = Source(path=str(test_clip), **media.probe(test_clip))
    from vedit.project import Clip

    p.clips.append(Clip(id="c1", source="s1", in_=0, out=2, fit="contain"))  # 16:9 in 9:16: bars top and bottom
    p.layers.append(_screen("#FF0000", start=0, end=2, track=0))
    assert _red(_render(p, tmp_path, 1.0, 0.5, 0.5))  # main_z 0: the screen is over the whole video
    p.main_z = 1  # now under it: red only in the letterbox bars
    assert _red(_render(p, tmp_path, 1.0, 0.5, 0.1)) and not _red(_render(p, tmp_path, 1.0, 0.5, 0.5))


def test_timeline_of_layers_only_and_past_the_clips(tmp_path, test_clip, make_project):
    p = Project()
    p.layers.append(_screen("#FF0000", start=0, end=1.5))
    assert _red(_render(p, tmp_path, 1.0))  # no clips at all
    from vedit.project import Clip

    p.sources["s1"] = Source(path=str(test_clip), **media.probe(test_clip))
    p.clips.append(Clip(id="c1", source="s1", in_=0, out=1))
    p.layers[0].start, p.layers[0].end = 1.0, 2.5
    assert p.duration == 2.5 and _red(_render(p, tmp_path, 2.0))  # after the clips end, the screen still plays


def test_gap_grade_filter_and_keyed_green_screen_show_the_track_under(tmp_path, test_clip):
    from vedit.ops.cut import gap_source
    from vedit.project import Clip, Effect, Grade

    p = Project()
    p.sources["s1"] = Source(path=str(test_clip), **media.probe(test_clip))
    gid = gap_source(p, tmp_path)
    p.clips += [Clip(id="g1", source=gid, in_=0, out=1, gap=True),  # the video track starts at 1s
                Clip(id="c1", source="s1", in_=0, out=1.5, fit="contain", grade=Grade(saturation=1.3),
                     effects=[Effect(type="fx", params={"key": "vignette", "id": "fx1"})])]
    p.layers.append(_screen("#FF0000", start=0, end=2.5, track=0))
    p.main_z = 1
    assert _red(_render(p, tmp_path, 0.5, 0.5, 0.5))  # the gap is transparent
    px = _render(p, tmp_path, 1.8, 0.5, 0.1)
    assert _red(px)  # graded + filtered clip keeps its transparent bars
    # keyed green screen with no replacement: the screen shows where the green was
    p.clips = [Clip(id="c1", source="s1", in_=0, out=1.5, fit="cover",
                    effects=[Effect(type="bg", params={"mode": "chroma", "key": "#00FF00", "replace": "none",
                                                       "similarity": 0.99})])]
    p.layers[0].end = 1.5
    assert _red(_render(p, tmp_path, 0.7, 0.5, 0.5))  # similarity 0.99 keys out (nearly) everything


def test_new_tracks_keep_their_side_of_the_video(make_project):
    from vedit.ops import overlays

    p = make_project()
    p.layers += [Layer(id="u", type="text", start=0, end=2, track=0), Layer(id="o", type="text", start=0, end=2, track=1),
                 Layer(id="n", type="text", start=0, end=2, track=1)]
    p.main_z = 1  # u under the video, o over it (n gets bumped on normalize)
    normalize_tracks(p)
    assert (p.layer("u").track, p.layer("o").track, p.main_z) == (0, 1, 1)
    overlays.place_layer(p, "n", 0, new_track=True)  # a new bottom track: under the video, u stays under too
    assert (p.layer("n").track, p.layer("u").track, p.layer("o").track, p.main_z) == (0, 1, 2, 2)
    overlays.place_layer(p, "n", 2, new_track=True, under_video=True)  # right under the video
    assert (p.layer("u").track, p.layer("n").track, p.main_z, p.layer("o").track) == (0, 1, 2, 2)
    overlays.place_layer(p, "n", 2, new_track=True)  # right over the video
    assert (p.layer("u").track, p.main_z, p.layer("n").track, p.layer("o").track) == (0, 1, 1, 2)
    overlays.place_layer(p, "u", 0)  # dropped on a busy track under the video: bumped, still under it
    assert p.layer("u").track < p.main_z


def test_elements_on_the_video_track(tmp_path, make_project):
    """An element can sit on the video track itself, in a gap or after the clips, but never over a clip."""
    import pytest

    from vedit.editor import actions
    from vedit.project import VeditError, load, save

    p = make_project(1)  # c1 0-10
    p.layers += [Layer(id="u", type="text", start=0, end=12, track=0), Layer(id="sh", type="shape", start=2, end=4)]
    p.main_z = 1
    save(tmp_path, p)
    with pytest.raises(VeditError, match="has a clip there"):
        actions.apply(tmp_path, "layer_place", {"layer": "sh", "video_track": True})  # 2-4 is over c1
    actions.apply(tmp_path, "layer_place", {"layer": "sh", "video_track": True, "start": 10.5})
    q = load(tmp_path)
    sh = q.layer("sh")
    assert sh.on_video and (sh.start, sh.end) == (10.5, 12.5) and q.duration == 12.5
    assert [l.id for l in q.layers] == ["u", "sh"]  # drawn after the track under the video
    with pytest.raises(VeditError, match="has a clip there"):  # the clip can't grow / move over it now
        actions.apply(tmp_path, "clip_place", {"clip": "c1", "start": 2.0})
    actions.apply(tmp_path, "layer_place", {"layer": "sh", "track": 1})  # dragged off onto a track row
    q = load(tmp_path)
    assert not q.layer("sh").on_video and q.layer("sh").track == 1


def test_video_track_element_renders_over_the_track_under(tmp_path):
    p = Project()
    p.layers += [_screen("#0000FF", id="bg", start=0, end=2, track=0),
                 _screen("#FF0000", id="vt", start=0, end=2, on_video=True)]
    p.main_z = 1
    normalize_tracks(p)
    assert [l.id for l in p.layers] == ["bg", "vt"]
    assert _red(_render(p, tmp_path, 1.0, 0.5, 0.5))


def test_cli_puts_an_element_on_the_video_track(cli, test_clip):
    from vedit.project import load

    cli("import", str(test_clip))  # 6s clip
    sh = cli("shape", "add", "rect", "--full", "--fill", "#FF0000", "--start", "1", "--end", "2")["layer"]
    cli("place", sh, "--video-track", ok=False)  # 1-2s is over the clip
    cli("place", sh, "--video-track", "--at", "6")
    p = load(cli.project)
    assert p.layer(sh).on_video and (p.layer(sh).start, p.layer(sh).end) == (6.0, 7.0) and p.duration == 7.0
    rows = cli("track", "list")["tracks"]
    assert any(r["track"] == "video" and any(sh in i for i in r["items"]) for r in rows)

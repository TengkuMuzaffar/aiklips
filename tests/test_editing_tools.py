"""Phase 6 editor tools: markers / chapters, keyframes on any property, trim to playhead, paste attributes,
clip sound fades + voice clean-up, scopes."""

import io
import re
import subprocess

import pytest
from PIL import Image

from vedit.editor import actions
from vedit.ops import cut, editing, fx_filters
from vedit.project import Effect, Grade, Layer, TextStyle, VeditError, load
from vedit.render.runner import PREVIEW_SCALE, base_hash, render

from .conftest import needs_ffmpeg


# ---- markers ---------------------------------------------------------------------------------------------------

def test_markers_follow_cuts_and_make_chapters(make_project):
    p = make_project(2)  # 10 s
    a = editing.add_marker(p, 6.0, "Demo")
    editing.add_marker(p, 0.4, "Intro")
    assert [m.label for m in p.markers] == ["Intro", "Demo"]  # kept in time order
    h = base_hash(p)
    editing.set_marker(p, a.id, label="The demo")
    assert base_hash(p) == h  # markers never re-render the video
    cut.delete_range(p, 1.0, 3.0)
    assert editing.marker(p, a.id).t == pytest.approx(4.0)  # same footage moment, 2 s earlier
    lines, issues = editing.chapters(p)
    assert lines == ["0:00 Intro", "0:04 The demo"]  # the first chapter is always 0:00
    assert any("at least 3" in i for i in issues) and any("10 s" in i for i in issues)
    editing.remove_marker(p, a.id)
    with pytest.raises(VeditError):
        editing.marker(p, a.id)


def test_cli_markers(cli, test_clip):
    cli("import", str(test_clip))
    m = cli("marker", "add", "--at", "2", "--label", "Hook")["marker"]
    cli("marker", "set", m, "--label", "Hook!")
    assert cli("marker", "list")["markers"][0]["label"] == "Hook!"
    assert cli("marker", "chapters")["chapters"] == ["0:00 Hook!"]
    cli("marker", "remove", "all")
    assert cli("marker", "list")["markers"] == []


# ---- keyframes -------------------------------------------------------------------------------------------------

def test_keyframes_set_merge_remove():
    l = Layer(id="t", type="text", text="x", start=2.0)
    editing.set_keyframe(l, 0.0, "x", "20%")
    editing.set_keyframe(l, 0.01, "scale", 1.5)  # within 0.02 s: the same keyframe
    editing.set_keyframe(l, 1.0, "x", "80%")
    assert len(l.keyframes) == 2 and l.keyframes[0].x == "20%" and l.keyframes[0].scale == 1.5
    with pytest.raises(VeditError):
        editing.set_keyframe(l, 0, "colour", 1)
    assert editing.remove_keyframe(l, 0.0, "x") == 1 and l.keyframes[0].scale == 1.5
    assert editing.remove_keyframe(l, 0.0) == 1 and len(l.keyframes) == 1  # empty keyframe dropped


@needs_ffmpeg
def test_keyframe_ops_use_timeline_time(cli, test_clip):
    cli("import", str(test_clip))
    d = cli.project
    actions.apply(d, "layer_add_text", {"text": "Hi", "start": 1.0})
    lid = load(d).layers[0].id
    actions.apply(d, "keyframe_set", {"layer": lid, "t": 1.5, "prop": "opacity", "value": 0.2})
    actions.apply(d, "keyframe_set", {"layer": lid, "t": 2.5, "prop": "opacity", "value": 1})
    k = load(d).layers[0].keyframes
    assert [x.t for x in k] == [0.5, 1.5] and k[0].opacity == 0.2
    actions.apply(d, "keyframe_remove", {"layer": lid, "t": 1.5})
    assert [x.t for x in load(d).layers[0].keyframes] == [1.5]


# ---- trim to playhead ------------------------------------------------------------------------------------------------

def test_trim_to_playhead(make_project):
    p = make_project(2)  # c1 0-5, c2 5-10
    editing.trim_to(p, 1.0, "start")  # c1 now starts 1 s into its footage
    assert p.clips[0].in_ == pytest.approx(1.0) and p.duration == pytest.approx(9.0)
    editing.trim_to(p, 6.0, "end")  # 6.0 is 2 s into c2 (which starts at 4.0 now)
    assert p.clips[1].out == pytest.approx(7.0) and p.duration == pytest.approx(6.0)


# ---- paste attributes -----------------------------------------------------------------------------------------------

def test_paste_clip_attributes(make_project):
    p = make_project(3)
    a, b, c = p.clips
    a.grade = Grade(saturation=0.4)
    a.fit, a.zoom, a.fade_in, a.voice = "contain", 1.3, 0.5, "studio"
    fx_filters.add(p, a, "grain")
    b.effects.append(Effect(type="mask", params={"id": "mk1"}))
    n = editing.paste_attributes(p, "clip", a.id, [b.id, c.id, a.id], ["colour", "effects", "framing", "sound"])
    assert n == 2
    assert b.grade.saturation == 0.4 and b.grade is not a.grade
    assert [e.type for e in b.effects] == ["mask", "fx"]  # its own mask kept, filters replaced
    ids = [e.params["id"] for x in p.clips for e in x.effects if e.type == "fx"]
    assert len(set(ids)) == 3
    assert (c.fit, c.zoom, c.fade_in, c.voice) == ("contain", 1.3, 0.5, "studio")
    a.speed = 2.0
    editing.paste_attributes(p, "clip", a.id, [b.id], ["speed"])
    assert b.speed == 2.0 and b.duration == pytest.approx(10 / 3 / 2, abs=1e-3)
    with pytest.raises(VeditError):
        editing.paste_attributes(p, "clip", a.id, [b.id], ["style"])


def test_paste_layer_attributes(make_project):
    p = make_project(1)
    src = Layer(id="t1", type="text", text="A", style=TextStyle(size=140, color="#FF0000"), blend="screen", scale=1.4,
                presets=[{"name": "pop_in"}])
    txt = Layer(id="t2", type="text", text="B")
    img = Layer(id="i1", type="shape")
    p.layers += [src, txt, img]
    editing.paste_attributes(p, "layer", "t1", ["t2", "i1"], ["style", "frame", "transform", "animation"])
    assert txt.style.size == 140 and txt.blend == "screen" and txt.scale == 1.4 and txt.presets[0].name == "pop_in"
    assert img.style.size == 80 and img.blend == "screen"  # text style isn't pasted onto a shape


# ---- sound ------------------------------------------------------------------------------------------------------------

def _level(path, start, dur) -> float:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(dur), "-i", str(path), "-af", "volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(re.search(r"mean_volume: (-?[\d.]+|-inf) dB", out).group(1).replace("-inf", "-200"))


@needs_ffmpeg
def test_sound_fades_and_voice(cli, test_clip):
    cli("import", str(test_clip))
    d = cli.project
    # measured without loudness normalisation, which would lift the quiet fade back up
    def unnormalised():
        p = load(d)
        p.canvas.loudness = None
        return p

    plain = d / "plain.mp4"
    render(unnormalised(), d, plain, PREVIEW_SCALE, "preview", progress=False)
    cli("sound", "fade", "--in", "1.0", "--out", "1.0")
    cli("sound", "voice", "studio")
    p = unnormalised()
    assert p.clips[0].fade_in == 1.0 and p.clips[0].voice == "studio"
    faded = d / "faded.mp4"
    render(p, d, faded, PREVIEW_SCALE, "preview", progress=False)
    assert _level(faded, 0.0, 0.25) < _level(plain, 0.0, 0.25) - 6  # quieter at the very start
    assert _level(faded, 5.75, 0.25) < _level(plain, 5.75, 0.25) - 6  # and at the very end
    assert _level(faded, 1.2, 0.5) > -40  # still sound in the middle
    actions.apply(d, "clip_set", {"clip": "c1", "voice": "off", "fade_in": 0})
    assert load(d).clips[0].voice == "off"
    with pytest.raises(VeditError):
        actions.apply(d, "clip_set", {"clip": "c1", "voice": "robot"})


# ---- scopes ---------------------------------------------------------------------------------------------------------

@needs_ffmpeg
def test_scopes_api(cli, test_clip):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    cli("import", str(test_clip))
    client = TestClient(create_app(cli.project, watch=False, chat=False))
    for kind, size in (("waveform", (360, 200)), ("vectorscope", (220, 220))):
        r = client.post("/api/scope", json={"t": 1.0, "kind": kind})
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert Image.open(io.BytesIO(r.content)).size == size
    assert client.post("/api/scope", json={"t": 1.0, "kind": "histogram"}).status_code == 400


# ---- adjustment layers --------------------------------------------------------------------------------------------------

@needs_ffmpeg
def test_adjustment_layer_grades_a_stretch(cli, test_clip):
    from vedit import media
    from vedit.render.frame import render_frame

    cli("import", str(test_clip))
    d = cli.project
    h0 = base_hash(load(d))
    lid = cli("adjust", "add", "--start", "1", "--end", "2", "--saturation", "0")["layer"]
    assert base_hash(load(d)) != h0  # it changes the base picture, so the live preview re-renders

    def colourful(img: Image.Image) -> float:
        import numpy as np

        a = np.asarray(img.convert("RGB"), float)
        return float((a.max(2) - a.min(2)).mean())

    for layers in (True, False):  # the export and the editor's base video both apply it
        out = d / f"adj-{layers}.mp4"
        render(load(d), d, out, PREVIEW_SCALE, "preview", progress=False, layers=layers)
        vals = []
        for t in (0.5, 1.5, 2.5):
            f = d / f"adj-{layers}-{t}.png"
            media.extract_frame(out, t, f)
            vals.append(colourful(Image.open(f)))
        assert vals[1] < 12 < vals[0] and vals[2] > 12, vals  # grey only inside 1-2 s
    assert colourful(Image.open(io.BytesIO(render_frame(load(d), d, 1.5)))) < 12  # instant frame too
    assert colourful(Image.open(io.BytesIO(render_frame(load(d), d, 0.5)))) > 12

    actions.apply(d, "fx_add", {"layer": lid, "key": "vignette"})
    fid = load(d).layer(lid).effects[0].params["id"]
    actions.apply(d, "fx_set", {"id": fid, "amount": 0.9})  # filters on a layer edit like clip filters
    assert load(d).layer(lid).effects[0].params["amount"] == 0.9
    actions.apply(d, "layer_grade", {"layer": lid, "look": "teal-orange", "intensity": 0.5})
    assert load(d).layer(lid).grade.intensity == 0.5
    actions.apply(d, "layer_grade", {"layer": lid, "clear": True})
    assert load(d).layer(lid).grade is None
    actions.apply(d, "layer_add_text", {"text": "x"})
    with pytest.raises(VeditError):
        actions.apply(d, "fx_add", {"layer": load(d).layers[-1].id, "key": "blur"})


# ---- named versions + templates -------------------------------------------------------------------------------------------

def test_named_versions(cli, test_clip, monkeypatch):
    from vedit import history

    cli("import", str(test_clip))
    d = cli.project
    cli("text", "add", "First")
    h = history.head(d)
    r = cli("version", "save", "client v1")
    assert r["step"] == h and history.head(d) == h  # a bookmark: no extra undo step
    cli("text", "add", "Second")
    assert [v["name"] for v in cli("version", "list")["versions"]] == ["client v1"]
    assert any(e.get("version") == "client v1" for e in history.log(d))
    # trimming old history keeps named versions
    monkeypatch.setattr(history, "MAX_SNAPSHOTS", 1)
    cli("text", "add", "Third")
    assert [v["step"] for v in history.versions(d)] == [h]
    cli("checkout", str(h))
    assert [l.text for l in load(d).layers] == ["First"]


def test_templates_round_trip(cli, test_clip, tmp_path):
    from vedit.ops import templates

    pic = tmp_path / "logo.png"
    Image.new("RGBA", (40, 40), (255, 0, 0, 255)).save(pic)
    cli("import", str(test_clip))
    t = cli("text", "add", "Name Surname", "--start", "2", "--end", "4", "--y", "80%")["layer"]
    b = cli("shape", "add", "rect", "--start", "1.5", "--end", "4.5", "--y", "80%")["layer"]
    i = cli("image", "add", str(pic), "--start", "2", "--end", "3")["layer"]
    a = cli("adjust", "add", "--start", "1.5", "--end", "4.5", "--fx", "vignette")["layer"]
    cli("template", "save", "Lower third", "--layer", t, "--layer", b, "--layer", i, "--layer", a)
    rows = cli("template", "list")["templates"]
    assert rows[0]["key"] == "lower-third" and rows[0]["layers"] == 4

    ids = cli("template", "apply", "lower-third", "--at", "0.5")["layers"]
    p = load(cli.project)
    new = [p.layer(x) for x in ids]
    assert [x.type for x in new] == ["text", "shape", "image", "adjust"]  # stacking order kept
    assert new[1].start == pytest.approx(0.5) and new[0].start == pytest.approx(1.0) and new[0].end == pytest.approx(3.0)
    fx_ids = [e.params["id"] for c in [*p.clips, *p.layers] for e in c.effects]
    assert len(fx_ids) == len(set(fx_ids))  # the copied vignette got a fresh id
    cli("template", "delete", "lower-third")
    assert templates.catalog() == []
    cli("template", "apply", "lower-third", ok=False)

"""Design studio engine: scenes (ripple, duplicate, remove, undo), the new shape kinds, stretch / size / fill motion
and how the renderer draws them."""

import json

import numpy as np
import pytest
from typer.testing import CliRunner

from vedit.cli import app
from vedit.ops import scenes, shapes
from vedit.ops.animate import mix_hex, props_at
from vedit.ops.overlay_style import render_shape
from vedit.ops.overlays import SHAPE_DEFAULTS
from vedit.project import SCHEMA_VERSION, SHAPE_KINDS, Canvas, Keyframe, Layer, Preset, Project, load, migrate

from .conftest import needs_ffmpeg


@pytest.fixture
def design(tmp_path, monkeypatch):
    """Run vedit against a new design project (two scenes: 3 s and 2 s)."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    d = tmp_path / "des"

    def run(*args: str, ok: bool = True) -> dict:
        res = runner.invoke(app, ["--json", "-p", str(d), *args])
        if ok:
            assert res.exit_code == 0, f"{args} failed: {res.output}\n{res.exception!r}"
            return json.loads([ln for ln in res.output.strip().splitlines() if ln.startswith("{")][-1])
        assert res.exit_code != 0
        return {}

    res = runner.invoke(app, ["--json", "design", "new", str(d), "--format", "square", "--dur", "3"])
    assert res.exit_code == 0, res.output
    run("scene", "add", "--dur", "2", "--background", "#22113A", "--transition", "pop")
    run.dir = d  # type: ignore[attr-defined]
    return run


def _layers(d):
    return {l.id: l for l in load(d).layers}


# ---- scenes ---------------------------------------------------------------------------------------------------

@needs_ffmpeg
def test_design_new_and_scenes_keep_main_track_in_step(design):
    p = load(design.dir)
    assert p.kind == "design" and [s.id for s in p.scenes] == ["sc1", "sc2"]
    assert p.duration == pytest.approx(5.0)
    bg = p.layer(p.scenes[1].bg_layer)
    assert (bg.start, bg.end, bg.fill, bg.track) == (3.0, 5.0, "#22113A", 0)


@needs_ffmpeg
def test_scene_length_ripples_later_layers(design):
    design("shape", "add", "star", "--scene", "sc1")
    design("shape", "add", "heart", "--scene", "sc2", "--start", "3.5")
    L = _layers(design.dir)
    assert (L["sh1"].scene, L["sh1"].start, L["sh1"].end) == ("sc1", 0.0, 3.0)
    assert (L["sh2"].scene, L["sh2"].start, L["sh2"].end) == ("sc2", 3.5, 5.0)
    assert [x.name for x in L["sh2"].presets] == ["pop_in"]  # the scene's transition = its enter animation

    design("scene", "set", "sc1", "--dur", "4")
    L = _layers(design.dir)
    assert (L["sh1"].start, L["sh1"].end) == (0.0, 4.0)
    assert (L["sh2"].start, L["sh2"].end) == (4.5, 6.0)
    assert load(design.dir).duration == pytest.approx(6.0)

    design("scene", "set", "sc1", "--dur", "1")  # shorter: its layers are trimmed to fit
    L = _layers(design.dir)
    assert (L["sh1"].start, L["sh1"].end) == (0.0, 1.0)
    assert (L["sh2"].start, L["sh2"].end) == (1.5, 3.0)

    design("undo")
    assert _layers(design.dir)["sh2"].start == 4.5


@needs_ffmpeg
def test_scene_move_dup_remove(design):
    design("shape", "add", "star", "--scene", "sc1")
    design("shape", "add", "heart", "--scene", "sc2")
    design("scene", "move", "sc2", "0")
    L = _layers(design.dir)
    assert (L["sh2"].start, L["sh2"].end) == (0.0, 2.0) and (L["sh1"].start, L["sh1"].end) == (2.0, 5.0)

    out = design("scene", "dup", "sc2")
    p = load(design.dir)
    assert [s.id for s in p.scenes] == ["sc2", out["scene"], "sc1"]
    copies = [l for l in p.layers if l.scene == out["scene"] and l.type == "shape" and l.shape == "heart"]
    assert len(copies) == 1 and (copies[0].start, copies[0].end) == (2.0, 4.0)
    assert p.scene(out["scene"]).bg_layer and p.scene(out["scene"]).bg_layer != p.scene("sc2").bg_layer
    assert p.layer("sh1").start == 4.0

    design("scene", "remove", "sc2")
    p = load(design.dir)
    assert "sh2" not in {l.id for l in p.layers} and p.scenes[0].id == out["scene"]
    assert p.layer("sh1").start == 2.0 and p.duration == pytest.approx(5.0)
    design("scene", "remove", "sc1")
    design("scene", "remove", out["scene"], ok=False)  # the last scene stays


@needs_ffmpeg
def test_layers_follow_their_times_into_scenes(design):
    """Moving a layer on the timeline into another scene makes it that scene's (on save), spanning two = global."""
    design("shape", "add", "star", "--scene", "sc1")
    design("layer", "set", "sh1", "--start", "3.2", "--end", "4.5")
    l = _layers(design.dir)["sh1"]
    assert l.scene == "sc2" and [x.name for x in l.presets] == ["pop_in"]
    design("layer", "set", "sh1", "--start", "1")
    assert _layers(design.dir)["sh1"].scene is None


def test_scene_ops_need_a_design(cli):
    cli("scene", "add", ok=False)


def test_old_projects_migrate():
    assert migrate({"version": 1, "name": "x"})["version"] == SCHEMA_VERSION == 3


# ---- shapes ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", SHAPE_KINDS)
def test_every_shape_kind_draws_inside_its_box(kind):
    L = Layer(id="s", type="shape", shape=kind, **SHAPE_DEFAULTS[kind])
    img = render_shape(L, 0.5)
    assert img.size == (round(L.width * 0.5), round(L.height * 0.5))
    a = np.asarray(img.getchannel("A"))
    assert a.max() > 150 and a.mean() > 2  # something is drawn


def test_polygon_outlines_fill_their_box():
    for kind in ("triangle", "diamond", "polygon", "star", "heart"):
        pts = shapes.polygon(kind, 200, 100)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        assert min(xs) == pytest.approx(0, abs=0.5) and max(xs) == pytest.approx(200, abs=0.5), kind
        assert min(ys) == pytest.approx(0, abs=0.5) and max(ys) == pytest.approx(100, abs=0.5), kind
    assert len(shapes.polygon("star", 10, 10, points=7)) == 14


def test_gradient_fill_runs_between_its_colours():
    L = Layer(id="s", type="shape", shape="rect", width=200, height=100, radius=0, fill_gradient=["#FF0000", "#0000FF"],
              gradient_angle=0)
    a = np.asarray(render_shape(L, 1.0))
    assert a[50, 2, 0] > 240 and a[50, 2, 2] < 15  # left: red
    assert a[50, -3, 2] > 240 and a[50, -3, 0] < 15  # right: blue


def test_border_stays_inside_polygon_kinds():
    L = Layer(id="s", type="shape", shape="diamond", width=200, height=200, fill=None, border_width=10,
              border_color="#FFFFFF")
    a = np.asarray(render_shape(L, 1.0).getchannel("A"))
    assert a[20, 20] == 0 and a[100, 100] == 0  # outside the diamond and its middle: clear
    assert a[100, 12] > 200 and a[100, 40] == 0  # the border runs along the inside of the edge, about 10 px wide


# ---- motion ---------------------------------------------------------------------------------------------------

def test_size_stretch_and_fill_keyframes():
    L = Layer(id="s", type="shape", shape="rect", width=100, height=50, keyframes=[
        Keyframe(t=0, w=100, sx=1, fill="#000000", ease="linear"),
        Keyframe(t=1, w=300, sx=2, fill="#FFFFFF", ease="linear")])
    pr = props_at(L, 0.5, 2, Canvas())
    assert pr["w"] == pytest.approx(200) and pr["h"] == 50 and pr["sx"] == pytest.approx(1.5)
    assert pr["fill"] == "#808080"
    assert mix_hex("#00000000", "#FF0000FF", 1) == "#FF0000FF"


def test_new_presets_change_stretch_and_reveal():
    L = Layer(id="s", type="shape", presets=[Preset(name="stretch_in"), Preset(name="wipe_out")])
    assert props_at(L, 0.0, 3, Canvas())["sx"] == pytest.approx(0)
    assert props_at(L, 2.9, 3, Canvas())["reveal"] < 0.5
    sq = Layer(id="q", type="shape", presets=[Preset(name="squash_stretch")])
    pr = props_at(sq, 0.2, 3, Canvas())
    assert pr["sx"] > 1 > pr["sy"]


@needs_ffmpeg
def test_render_draws_animated_size_stretch_and_wipe(design):
    """The export redraws the shape at its keyframed width and colour, stretches with sx and wipes it on."""
    design("shape", "add", "rect", "--scene", "sc1", "--width", "200", "--height", "200", "--fill", "#FF0000",
           "--radius", "0")
    design("animate", "keyframes", "sh1",
           json.dumps([{"t": 0, "w": 200, "fill": "#FF0000", "ease": "step"}, {"t": 1, "w": 400, "fill": "#00FF00"}]))
    design("layer", "set", "sh1", "--sy", "2")
    out = design("frame", "--at", "0.5,2")
    from PIL import Image

    early, late = (np.asarray(Image.open(p).convert("RGB")).astype(int) for p in out["images"])
    H, W = early.shape[:2]
    k = W / 1080
    # at 0.5 s: red, 200 wide x 400 tall (sy 2) around the centre
    assert early[H // 2, W // 2, 0] > 200 and early[H // 2, W // 2, 1] < 60
    assert early[round(H / 2 + 180 * k), W // 2, 0] > 200  # stretched down past its own 100 px half-height
    assert early[H // 2, round(W / 2 + 150 * k), 0] < 80  # but not wider than 200
    # at 2 s: green, 400 wide
    assert late[H // 2, round(W / 2 + 150 * k), 1] > 200


# ---- text styles from the studio toolbar (bold, italic, underline, strike, lists) ---------------------------

def test_text_styles_change_the_sprite():
    from vedit.ops.text import render_text
    from vedit.project import TextStyle

    base = render_text("Hello", TextStyle(size=80, stroke_width=0, shadow=False))
    bold = render_text("Hello", TextStyle(size=80, stroke_width=0, shadow=False, bold=True))
    ink = lambda im: int((np.asarray(im.getchannel("A")) > 128).sum())  # noqa: E731
    assert ink(bold) > ink(base) * 1.1  # heavier letters
    assert render_text("Hello", TextStyle(size=80, italic=True)).width > render_text("Hello", TextStyle(size=80)).width  # slanted
    under = render_text("Hello", TextStyle(size=80, stroke_width=0, shadow=False, underline=True))
    assert ink(under) > ink(base)  # a line under the words
    a = np.asarray(under.getchannel("A"))
    rows = (a > 128).sum(1)
    assert rows.max() > base.width * 0.7  # one row spans (almost) the whole word: the underline
    bul = render_text("One\nTwo", TextStyle(size=60, list_style="bullet", align="left"))
    num = render_text("One\nTwo", TextStyle(size=60, list_style="number", align="left"))
    plain = render_text("One\nTwo", TextStyle(size=60, align="left"))
    assert bul.width > plain.width and num.width > plain.width


def test_text_style_flags_from_cli(cli):
    out = cli("text", "add", "Sale", "--bold", "--underline", "--list", "bullet", "--align", "left")
    l = load(cli.project).layer(out["layer"])
    assert l.style.bold and l.style.underline and l.style.list_style == "bullet" and l.style.align == "left"
    cli("text", "edit", out["layer"], "--no-bold", "--italic")
    l = load(cli.project).layer(out["layer"])
    assert not l.style.bold and l.style.italic

"""Motion graphics on our own tracks: draw-on, paths, morph, counters, kinetic text, charts, motion styles."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from vedit.ops import motion, shapes
from vedit.ops.chart import item_progress, parse_items, render_chart
from vedit.ops.overlay_style import render_shape
from vedit.project import Chart, ChartItem, Counter, Layer, TextStyle, VeditError, load

ANIMATE_JS = Path(__file__).parents[1] / "src/vedit/preview_server/static/animate.js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _ink(img: Image.Image) -> int:
    import numpy as np

    return int((np.asarray(img.getchannel("A")) > 128).sum())


def test_draw_on_grows_with_progress_and_ends_as_the_full_shape():
    l = Layer(id="s", type="shape", shape="star", width=300, height=300, fill="#FFC23B")
    inks = [_ink(render_shape(l, 0.5, draw=d)) for d in (0.0, 0.3, 0.7, 0.999)]
    assert inks[0] < inks[1] < inks[2] < inks[3]
    full = render_shape(l, 0.5)
    assert _ink(render_shape(l, 0.5, draw=1.0)) == _ink(full) > inks[3]  # 1 = the filled shape again


def test_paths_open_and_closed_and_trim():
    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    assert shapes.trim(pts, False, 0.5) == [(0.0, 0.0), (10.0, 0.0)]
    assert shapes.trim(pts, False, 0.75)[-1] == (10.0, 5.0)
    assert shapes.trim(pts, True, 1.0)[-1] == pytest.approx((0.0, 0.0))  # closed: back to the start
    open_ = Layer(id="p", type="shape", shape="path", width=400, height=200, fill="#FF0000", path=[[0, 1], [0.5, 0], [1, 1]])
    closed = open_.model_copy(update={"closed": True})
    assert 0 < _ink(render_shape(open_, 0.5)) < _ink(render_shape(closed, 0.5))


def test_morph_blends_between_kinds():
    a, b = shapes.any_outline("ellipse", 100, 100), shapes.any_outline("star", 100, 100)
    p0, _ = shapes.morph(a, b, 0, 100)
    p1, _ = shapes.morph(a, b, 1, 100)
    assert len(p0) == len(p1) == shapes.MORPH_POINTS
    assert p0[0] == pytest.approx((50.0, 0.0)) and p1[0] == pytest.approx((50.0, 0.0))  # both start at the top centre
    l = Layer(id="m", type="shape", shape="ellipse", width=200, height=200, fill="#FFFFFF", morph_to="star")
    circle, star = _ink(render_shape(l, 0.5)), _ink(render_shape(l, 0.5, morph=1))
    assert star < _ink(render_shape(l, 0.5, morph=0.5)) < circle


def test_counter_text():
    c = Counter(**{"from": 0}, to=1250000, prefix="$", suffix="+")
    assert motion.counter_text(c, 1) == "$1,250,000+" and motion.counter_text(c, 0.5) == "$625,000+"
    assert motion.counter_text(Counter(to=-3.5, decimals=2, separator=""), 1) == "-3.50"
    assert motion.counter_text(Counter(to=99.95, decimals=1), 1) == "100.0"


@needs_node
def test_js_counter_text_matches_python():
    cases = [({"from": 0, "to": 1250000, "prefix": "$", "suffix": "+"}, p) for p in (0, 0.123, 0.5, 0.987, 1)]
    cases += [({"from": 10, "to": -3.5, "decimals": 2, "separator": ""}, p) for p in (0, 0.3, 1)]
    cases += [({"to": 12345.678, "decimals": 1, "separator": " "}, p) for p in (0.01, 0.77, 1)]
    py = [motion.counter_text(Counter.model_validate(c), p) for c, p in cases]
    script = (f"const A = require({json.dumps(str(ANIMATE_JS))});let d='';process.stdin.on('data',c=>d+=c).on('end',()=>"
              "process.stdout.write(JSON.stringify(JSON.parse(d).map(([c,p])=>A.counterText(c,p)))));")
    js = json.loads(subprocess.run(["node", "-e", script], input=json.dumps(cases), capture_output=True, text=True, check=True).stdout)
    assert js == py


def test_kinetic_text_pieces_and_frames():
    style = TextStyle(size=60)
    words = Layer(id="t", type="text", text="Make every second count", split="word", stagger=0.1, split_dur=0.4)
    k = motion.Kinetic(words.text, style, 1.0, words)
    assert len(k.pieces) == 4 and k.end == pytest.approx(0.3 + 0.4)
    letters = Layer(id="t", type="text", text="AB CD", split="letter", split_anim="mask_up", stagger=0.05)
    assert len(motion.Kinetic(letters.text, style, 1.0, letters).pieces) == 4
    sizes = {k.frame(t).size for t in (0.0, 0.15, 0.5, 5.0)}
    assert len(sizes) == 1  # same box at every moment: the layer never jumps
    assert _ink(k.frame(0.0)) == 0 and _ink(k.frame(0.15)) < _ink(k.frame(5.0))
    assert k.frame(5.0) is k.full


def test_chart_grows_and_staggers():
    c = Chart(items=[ChartItem(label="A", value=1), ChartItem(label="B", value=2)], stagger=1.0)
    assert item_progress(c, 0, 2, 0.5) == pytest.approx(1.0) and item_progress(c, 1, 2, 0.5) == pytest.approx(0.0)
    assert item_progress(c.model_copy(update={"stagger": 0}), 1, 2, 0.5) == pytest.approx(0.5)
    assert parse_items("Mon=12, Tue=30:#FF0000")[1] == {"label": "Tue", "value": 30.0, "color": "#FF0000"}
    with pytest.raises(VeditError):
        parse_items("Mon=twelve")
    for kind in ("bar", "hbar", "line", "donut"):
        l = Layer(id="c", type="chart", width=400, height=300, chart=c.model_copy(update={"kind": kind, "show_values": False,
                                                                                         "show_labels": False}))
        assert _ink(render_chart(l, 0.5, 0.0)) <= _ink(render_chart(l, 0.5, 0.5)) < _ink(render_chart(l, 0.5, 1.0)), kind


def test_cli_motion_commands_and_style(cli):
    from vedit.audit import check_style

    p = cli.project  # a project of layers only: motion graphics need no clips
    t = cli("counter", "add", "1250", "--prefix", "$", "--y", "30%")
    assert t["text"] == "$1,250"
    ch = cli("chart", "add", "bar", "--data", "A=3, B=7, C=5", "--suffix", "k")["layer"]
    cli("chart", "set", ch, "--kind", "donut", "--data", "A=1, B=2")
    title = cli("text", "add", "BIG IDEA", "--y", "15%")["layer"]
    cli("motion", "set", title, "--split", "letter", "--anim", "mask_up", "--ease", "cubic-bezier(.7,0,.2,1)")
    sh = cli("shape", "add", "path", "--path", "0,1 0.5,0 1,1", "--width", "300", "--height", "100")["layer"]
    cli("motion", "draw", sh, "--dur", "0.8", "--out")
    blob = cli("shape", "add", "ellipse")["layer"]
    cli("motion", "morph", blob, "--to", "heart", "--at", "0.5")
    proj = load(p)
    assert proj.layer(ch).chart.kind == "donut" and len(proj.layer(ch).chart.items) == 2
    assert proj.layer(title).split == "letter" and proj.layer(title).split_ease == "cubic-bezier(.7,0,.2,1)"
    assert proj.layer(sh).path == [[0, 1], [0.5, 0], [1, 1]] and [x.name for x in proj.layer(sh).presets] == ["draw_in", "draw_out"]
    assert [k.morph for k in proj.layer(blob).keyframes] == [0, 1]
    with pytest.raises(Exception):
        cli("motion", "set", title, "--anim", "spin")
    # a style: its rules reach the audit
    cli("style", "set", "swiss")
    cli("animate", "preset", title, "fade_in")
    found = check_style(load(p), p)
    assert any("fade_in" in f["message"] and f["category"] == "style" for f in found)
    assert cli("style", "show", "data-story")["ease"] == "expo_out"
    with pytest.raises(Exception):
        cli("style", "set", "watercolour")  # planned, not available yet
    # the export draws all of it
    imgs = cli("frame", "--at", "0.3,1.5", "--width", "240")["images"]
    assert all(Path(i).exists() for i in imgs)


def test_sprites_follow_the_motion_values(tmp_path):
    from vedit.preview_server.sprites import sprite_png

    l = Layer(id="s", type="shape", shape="star", width=300, height=300).model_dump(mode="json", by_alias=True)
    half, full = sprite_png(tmp_path, l, 0.5, motion={"draw": 0.5}), sprite_png(tmp_path, l, 0.5)
    assert half != full
    c = Layer(id="t", type="text", text="Go", counter=Counter(to=50)).model_dump(mode="json", by_alias=True)
    k = Layer(id="k", type="text", text="Hello there", split="word").model_dump(mode="json", by_alias=True)
    assert sprite_png(tmp_path, k, 0.5, motion={"kt": 0.05}) != sprite_png(tmp_path, k, 0.5, motion={"kt": 600})
    assert motion.kinetic_end(k) == pytest.approx(0.06 + 0.45)
    assert sprite_png(tmp_path, c, 0.5, motion={"count": 0.1}) != sprite_png(tmp_path, c, 0.5)


def test_motion_plan_builds_the_new_elements(tmp_path):
    from vedit.design import builder
    from vedit.design.spec import MotionPlan
    from vedit.project import Canvas, Project, save

    pdir = tmp_path / "plan"
    pdir.mkdir()
    save(pdir, Project(canvas=Canvas()))
    plan = MotionPlan.model_validate({"style": "data-story", "scenes": [{"id": "S1", "dur": 3, "elements": [
        {"kind": "counter", "count_to": 98, "suffix": "%", "y": "35%"},
        {"kind": "chart", "chart": "hbar", "data": "Q1=3, Q2=7"},
        {"kind": "text", "text": "Grew fast", "split": "word", "split_anim": "rise", "split_ease": "nope"},
        {"kind": "shape", "shape": "path", "points_xy": [[0, 0], [1, 1]], "enter": "draw_in"},
        {"kind": "shape", "shape": "ellipse", "morph_to": "star", "path": [{"t": 0, "morph": 0, "ease": "snap"}, {"t": 1, "morph": 1}]},
    ]}]})
    rep = builder.build(plan, pdir, {})
    assert not rep["skipped"], rep["skipped"]
    p = load(pdir)
    kinds = {l.type for l in p.layers}
    assert {"chart", "text", "shape"} <= kinds and p.motion_style == "data-story"
    counter = next(l for l in p.layers if l.counter)
    assert counter.text == "98%" and "count_up" in [x.name for x in counter.presets]
    chart = next(l for l in p.layers if l.type == "chart")
    assert "draw_in" in [x.name for x in chart.presets]
    kt = next(l for l in p.layers if l.type == "text" and l.split == "word")
    assert kt.split_ease == "ease_out"  # an unknown easing falls back instead of failing the build
    morph = next(l for l in p.layers if l.morph_to == "star")
    assert morph.keyframes[0].ease == "snap" and morph.keyframes[1].morph == 1

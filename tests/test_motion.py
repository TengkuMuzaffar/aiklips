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


def test_signature_effects_step1(cli, test_clip):
    """snap / digital zoom, growth curve, checklists, headline storm, b-roll overlay: one undoable step each."""
    from vedit.ops import moves
    from vedit.project import ZOOM_EASES, zoom_at

    cli("import", str(test_clip))
    cli("import", str(test_clip), "--at", "99")
    cli("remove-clip", "c2")  # keep the second source for b-roll
    assert cli("fx", "apply", "snap_zoom", "--at", "0.5", "--dur", "0.6")["done"].startswith("snap zoom")
    p = load(cli.project)
    ramp = next(c for c in p.clips if c.zoom_to)
    assert ramp.zoom_ease == "snap" and ramp.zoom_to == pytest.approx(1.35) and ramp.duration == pytest.approx(0.2, abs=0.02)
    assert zoom_at(ramp, 0.5) > 1 + 0.35 * 0.9  # snap: nearly there half way through the ramp
    assert [round(c.zoom, 2) for c in p.clips][-1] == 1.0  # cut back after the hold
    cli("fx", "apply", "digital_zoom", "--at", "2.0")
    assert sorted({round(c.zoom, 2) for c in load(cli.project).clips}) == [1.0, 1.12, 1.26, 1.35, 1.42]
    cli("fx", "apply", "growth_curve", "--at", "1", "--text", "+340%")
    cli("fx", "apply", "checklist_pop", "--at", "0.2", "--text", "Fast; Cheap; Easy")
    cli("fx", "apply", "headline_storm", "--at", "1.5", "--text", "One; Two; Three")
    src = next(s for s in load(cli.project).sources if s != load(cli.project).clips[0].source)
    cli("fx", "apply", "broll_overlay", "--at", "1", "--source", src, "--dur", "1")
    p = load(cli.project)
    counter = next(l for l in p.layers if l.counter)
    assert counter.text == "+340%" and any(l.type == "chart" for l in p.layers)
    ticks = [l for l in p.layers if l.shape == "path"]
    assert len(ticks) == 3 and all(x.presets[0].name == "draw_in" for x in ticks)
    assert {l.text for l in p.layers if l.type == "text"} >= {"Fast", "Cheap", "Easy", "One", "Two", "Three"}
    assert any(l.type == "video" and l.fit == "cover" for l in p.layers)
    with pytest.raises(Exception):
        cli("fx", "apply", "checklist", "--at", "1")  # needs the items
    assert "broll_overlay" not in moves.MOVE_NAMES and "snap_zoom" in moves.MOVE_NAMES
    assert set(ZOOM_EASES) >= {"linear", "snap", "ease_out"}
    imgs = cli("frame", "--at", "0.6,2.5", "--width", "200")["images"]  # the eased zoom renders
    assert all(Path(i).exists() for i in imgs)


def test_effect_previews_render():
    from vedit.ops.moves import PREVIEWS
    from vedit.preview_server.motion_thumbs import move_thumb

    for k in PREVIEWS:
        assert move_thumb(k).startswith(b"\x89PNG")
    assert move_thumb("checklist", True)[:4] == b"RIFF"


def test_signature_effects_step2(cli, test_clip):
    """chat takeover, phone mockup, magazine cover, zoom portal and the screencast / HUD / paper kits."""
    cli("import", str(test_clip))
    cli("split", "--at", "5")
    cli("fx", "apply", "chat_takeover", "--at", "0.2", "--text", "them: hi; me: hey; them: look at this")
    p = load(cli.project)
    bubbles = [l for l in p.layers if l.type == "text" and l.style.bg_color]
    assert {l.text for l in bubbles} >= {"hi", "hey", "look at this"} and any("•" in l.text for l in bubbles)  # typing dots
    first = next(l for l in bubbles if l.text == "hi")
    assert first.keyframes and first.keyframes[-1].y < first.y  # pushed up by the later messages
    assert next(l for l in bubbles if l.text == "hey").style.bg_color == "#2F7BF6"  # mine: right, blue
    cli("fx", "apply", "phone_mockup", "--at", "1", "--dur", "1.5")
    phone = next(l for l in load(cli.project).layers if l.type == "video")
    assert phone.border_width > 0 and phone.radius > 0 and phone.src_in == pytest.approx(1.0, abs=0.05)
    cli("fx", "apply", "hud_lock", "--at", "2", "--box", "20,20,60,40", "--text", "target")
    cli("fx", "apply", "cursor_click", "--at", "3", "--text", "10%,80% > 50%,50%")
    cli("fx", "apply", "paper_title", "--at", "6", "--text", "Hi; there")
    p = load(cli.project)
    assert sum(1 for l in p.layers if l.shape == "path") >= 5  # 4 brackets + the cursor
    assert any(l.text == "TARGET" and l.split == "letter" for l in p.layers)
    assert all(l.step_fps == 12 for l in p.layers if l.start == pytest.approx(6.0) or l.start == pytest.approx(6.25))
    cli("fx", "apply", "zoom_portal", "--clip", "c2")
    p = load(cli.project)
    dive = next(c for c in p.clips if c.zoom_to)
    assert dive.zoom_ease == "ease_in" and p.clips[[c.id for c in p.clips].index(dive.id) + 1].transition.type == "zoomin"
    cli("fx", "apply", "magazine_cover", "--at", "1.0", "--dur", "1.5", "--text", "ICON; The comeback")
    p = load(cli.project)
    keyed = next(c for c in p.clips if any(e.type == "bg" for e in c.effects))
    assert next(e for e in keyed.effects if e.type == "bg").params["replace"] == "none" and p.main_z >= 2
    under = [l for l in p.layers if (l.track or 0) < p.main_z and not l.on_video]
    assert {l.type for l in under} == {"video", "text"}  # the full shot and the masthead sit under the person


def test_effect_layers_are_grouped(cli, test_clip):
    """An effect's layers form one group on tracks of their own; groups move / rename / ungroup / delete together."""
    cli("import", str(test_clip))
    cli("text", "add", "Plain", "--end", "2")
    cli("fx", "apply", "chat_takeover", "--at", "0.5", "--text", "them: hi; me: hey")
    cli("fx", "apply", "checklist", "--at", "1", "--text", "A; B")
    p = load(cli.project)
    assert [g.name for g in p.groups] == ["Chat takeover", "Checklist"] and [g.id for g in p.groups] == ["g1", "g2"]
    for g in p.groups:  # a group's tracks hold only its own layers
        tracks = {l.track for l in p.layers if l.group == g.id}
        assert all(l.group == g.id for l in p.layers if l.track in tracks)
    cli("text", "add", "Later", "--start", "1", "--end", "2")
    p = load(cli.project)
    later = next(l for l in p.layers if l.text == "Later")
    assert later.group is None and not any(l.group for l in p.layers if l.track == later.track and l is not later)
    s0 = min(l.start for l in p.layers if l.group == "g2")
    cli("group", "move", "g2", "--by", "0.5")
    assert min(l.start for l in load(cli.project).layers if l.group == "g2") == pytest.approx(s0 + 0.5)
    cli("group", "set", "g2", "--name", "Steps", "--expanded")
    g2 = next(g for g in load(cli.project).groups if g.id == "g2")
    assert g2.name == "Steps" and not g2.collapsed
    assert {r["id"] for r in cli("group", "list")["groups"]} == {"g1", "g2"}
    n = len(load(cli.project).layers)
    gone = cli("group", "remove", "g1")["removed"]
    assert len(load(cli.project).layers) == n - len(gone) and [g.id for g in load(cli.project).groups] == ["g2"]
    cli("group", "ungroup", "g2")
    p = load(cli.project)
    assert not p.groups and not any(l.group for l in p.layers)
    ids = [l.id for l in p.layers][:2]
    g = cli("group", "make", *ids, "--name", "Mine")["group"]
    cli("layer", "remove", ids[0])
    cli("layer", "remove", ids[1])
    assert not load(cli.project).groups, g  # an empty group goes away

def test_iso_items_library_and_editing(cli):
    """64 iso items from 6 primitives, themes, auto-placement, item editing via the CLI, and every item renders."""
    from vedit.ops import iso_items
    from vedit.ops.iso import render_iso, world
    from vedit.project import IsoItem, IsoScene

    assert len(iso_items.ITEMS) >= 50
    for theme in iso_items.THEMES:
        assert set(iso_items.THEMES["pastel"]) == set(iso_items.THEMES[theme]), theme
    for kind in iso_items.ITEMS:  # every item draws something, and turned it still does
        for rot in (0, 1):
            img = render_iso(Layer(id="i", type="iso", width=120, iso=IsoScene(items=[IsoItem(kind=kind, rot=rot)])), 1.0)
            assert _ink(img) > 20, (kind, rot)
    for w in iso_items.WORLDS:
        assert world(w, "night").items
    cli("iso", "add", "empty", "--theme", "bright")
    a = cli("iso", "item", "add", "iso1", "tree")
    b = cli("iso", "item", "add", "iso1", "tree")
    assert (a["x"], a["y"]) != (b["x"], b["y"])  # auto-placed on different free spots
    cli("iso", "item", "add", "iso1", "shop", "--at", "2,2", "--param", "text=OPEN", "--label", "Store")
    cli("iso", "item", "set", "iso1", "3", "--rot", "1", "--color", "#FF0000", "--param", "text=CLOSED")
    lst = cli("iso", "list", "iso1")["items"]
    assert lst[3]["kind"] == "shop" and lst[3]["rot"] == 1 and lst[3]["params"]["text"] == "CLOSED" and lst[3]["label"] == "Store"
    cli("iso", "item", "remove", "iso1", "1")
    cli("iso", "set", "iso1", "--build", "pop", "--theme", "night", "--no-outline")
    p = load(cli.project)
    sc = p.layer("iso1").iso
    assert len(sc.items) == 3 and sc.build == "pop" and sc.theme == "night" and sc.outline is None
    with pytest.raises(Exception):
        cli("iso", "item", "add", "iso1", "dragon")
    assert any(r["kind"] == "rocket" for r in cli("iso", "items")["items"])
    imgs = cli("frame", "--at", "0.4,3", "--width", "200")["images"]
    assert all(Path(i).exists() for i in imgs)

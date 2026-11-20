"""Design studio AI actions (storyboard, design a scene, animate a selection) with a fake LLM, the model the user
picked in the studio, and the studio's HTTP routes."""

import json
import shutil

import pytest
from typer.testing import CliRunner

from vedit.ai import client as ai
from vedit.cli import app
from vedit.design import studio_ai
from vedit.project import load

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def design(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "des"
    res = CliRunner().invoke(app, ["--json", "design", "new", str(d), "--dur", "4"])
    assert res.exit_code == 0, res.output
    return d


@pytest.fixture
def fake_ai():
    calls = []

    def backend(task, system, prompt, schema, images, model):
        calls.append((task, model, prompt))
        if task == "design_storyboard":
            return {"title": "Launch", "palette": ["#101014", "#C5F467"], "note": "hook, proof, ask",
                    "scenes": [{"name": "Hook", "dur": 6, "notes": 'Big word "FAST"', "background": "#101014,#2B0F2E", "transition": "pop"},
                               {"name": "Proof", "dur": 6, "notes": "Three stars", "background": "not a colour", "transition": "spin"},
                               {"name": "Ask", "dur": 8, "notes": '"Try it today"', "transition": "fade"}]}
        if task == "design_scene":
            return {"note": "a star and a word", "elements": [
                {"kind": "shape", "shape": "star", "gradient": ["#FFC23B", "#FF3B6B"], "points": 6, "width": 400, "height": 400,
                 "x": "50%", "y": "40%", "enter": "stretch_in", "loop": "breathe", "why": "energy"},
                {"kind": "text", "text": "FAST", "look": "bold-yellow", "y": "70%", "at": 0.4, "enter": "rise_in"},
                {"kind": "image", "file": "nope"}]}
        if task == "design_animate":
            ids = [json.loads(line)["id"] for line in prompt.splitlines() if line.startswith("{")]
            return {"note": "bouncy", "layers": [{"id": ids[0], "enter": "pop_in", "exit": "pop_out",
                                                  "path": [{"t": 0, "sx": 0.6, "fill": "#FF0000"}, {"t": 1, "sx": 1, "fill": "#00FF00", "ease": "bounce_out"}]},
                                                 {"id": "ghost", "enter": "fade_in"}]}
        raise AssertionError(task)

    ai.set_backend(backend)
    yield calls
    ai.set_backend(None)


@needs_ffmpeg
def test_storyboard_proposes_then_applies(design, fake_ai):
    sb = studio_ai.storyboard(design, "A fast app launch", length=10, style="playful", model="openai/gpt-x")
    assert fake_ai[0][:2] == ("design_storyboard", "openai/gpt-x")  # the model the user picked, any provider
    assert "playful" in fake_ai[0][2].lower() or "bright palette" in fake_ai[0][2].lower()
    assert [s["name"] for s in sb["scenes"]] == ["Hook", "Proof", "Ask"]
    assert sum(s["dur"] for s in sb["scenes"]) == pytest.approx(10, abs=0.6)  # code fits it to the asked length
    assert sb["scenes"][0]["background"] == "#101014,#2B0F2E" and sb["scenes"][1]["background"] is None
    assert sb["scenes"][1]["transition"] == "fade"  # an unknown entrance falls back
    assert len(load(design).scenes) == 1  # nothing changes before the user applies it

    r = studio_ai.apply_storyboard(design, sb["scenes"], "replace")
    p = load(design)
    assert r["added"] == 3 and [s.name for s in p.scenes] == ["Hook", "Proof", "Ask"]
    assert p.duration == pytest.approx(sum(s["dur"] for s in sb["scenes"]))
    bg = p.layer(p.scenes[0].bg_layer)
    assert bg.fill_gradient == ["#101014", "#2B0F2E"]
    studio_ai.apply_storyboard(design, sb["scenes"][:1], "append")
    assert len(load(design).scenes) == 4


@needs_ffmpeg
def test_design_scene_builds_elements_inside_the_scene(design, fake_ai):
    studio_ai.apply_storyboard(design, [{"name": "A", "dur": 3}, {"name": "B", "dur": 5, "notes": 'Big "FAST"', "transition": "zoom"}])
    p = load(design)
    sc = p.scenes[1]
    r = studio_ai.design_scene(design, sc.id)
    assert r["added"] == 2 and r["skipped"] == 1
    p = load(design)
    ls = [l for l in p.layers if l.scene == sc.id and l.id != sc.bg_layer]
    star = next(l for l in ls if l.type == "shape")
    assert star.shape == "star" and star.points == 6 and star.fill_gradient == ["#FFC23B", "#FF3B6B"]
    assert {x.name for x in star.presets} >= {"stretch_in", "breathe"}
    txt = next(l for l in ls if l.type == "text")
    assert txt.start == pytest.approx(3.4) and txt.end == pytest.approx(8.0)
    assert all(3.0 <= l.start and l.end <= 8.0 for l in ls)
    assert "Big" in fake_ai[-1][2] and "SHAPES" in fake_ai[-1][2]  # notes + the code-made vocabulary
    from vedit import history

    assert history.log(design, 1)[0]["message"].startswith("AI design:")  # one undo step


@needs_ffmpeg
def test_animate_selection(design, fake_ai):
    CliRunner().invoke(app, ["--json", "-p", str(design), "shape", "add", "rect"])
    studio_ai.animate(design, ["sh1"], "bouncy")
    l = load(design).layer("sh1")
    assert [x.name for x in l.presets] == ["pop_in", "pop_out"]
    assert l.keyframes[1].sx == 1 and l.keyframes[1].fill == "#00FF00" and l.keyframes[1].ease == "bounce_out"


@needs_ffmpeg
def test_studio_routes(tmp_path, monkeypatch, fake_ai):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    monkeypatch.setenv("VEDIT_PROJECTS_DIR", str(tmp_path / "projects"))
    client = TestClient(create_app(None, watch=False, chat=False))
    r = client.post("/api/design/new", json={"format": "square", "name": "Promo"})
    assert r.status_code == 200, r.text
    assert client.get("/editor", follow_redirects=False).headers["location"] == "/studio"
    page = client.get("/studio")
    assert page.status_code == 200 and "studio.js" in page.text
    for f in ("studio.js", "shapes.js", "animate.js"):
        assert client.get(f"/static/{f}").status_code == 200
    assert any(p["name"] == "Promo" for p in client.get("/api/design/projects").json()["projects"])
    st = client.get("/api/state").json()["project"]
    assert st["kind"] == "design" and st["canvas"]["width"] == 1080 and st["canvas"]["height"] == 1080
    sb = client.post("/api/design/storyboard", json={"brief": "launch", "length": 12}).json()
    assert client.post("/api/design/storyboard/apply", json={"scenes": sb["scenes"]}).json()["added"] == 3
    # a drag = one undo step: batch of a move and a stretch
    client.post("/api/op", json={"op": "layer_add_shape", "args": {"shape": "heart", "start": 0}})
    r = client.post("/api/op", json={"op": "batch", "args": {"ops": [
        {"op": "layer_set", "args": {"layer": "sh1", "x": 100, "y": 200}},
        {"op": "keyframe_set", "args": {"layer": "sh1", "t": 0.5, "prop": "w", "value": 500}}]}})
    assert r.status_code == 200, r.text
    l = next(x for x in client.get("/api/state").json()["project"]["layers"] if x["id"] == "sh1")
    assert l["x"] == 100 and l["keyframes"][0]["w"] == 500
    client.post("/api/undo")
    l = next(x for x in client.get("/api/state").json()["project"]["layers"] if x["id"] == "sh1")
    assert l["x"] != 100 and not l["keyframes"]
    assert client.post("/api/design/scene", json={"scene": "nope"}).status_code == 400
    ai.set_backend(lambda **k: (_ for _ in ()).throw(ai.AIUnavailable("no model")))
    assert client.post("/api/design/storyboard", json={"brief": "x"}).status_code == 503


@needs_ffmpeg
def test_old_beta_designs_open_as_scenes(tmp_path, monkeypatch):
    from vedit.design import builder
    from vedit.design.spec import Element, MotionPlan, Scene
    from vedit.generate.executor import create_project
    from vedit.ops import scenes

    d = tmp_path / "old"
    create_project(d, "old", "reels")
    builder.build(MotionPlan(scenes=[Scene(id="S1", dur=3, purpose="hook", elements=[Element(kind="text", text="Hi")]),
                                     Scene(id="S2", dur=2, purpose="end", elements=[])]), d, {})
    # as the old wizard left it: a plain project
    data = json.loads((d / "project.json").read_text())
    data.update(kind="edit", scenes=[])
    (d / "project.json").write_text(json.dumps(data))
    assert scenes.adopt_legacy(d) is True
    p = load(d)
    assert p.kind == "design" and [s.dur for s in p.scenes] == [3, 2] and p.scenes[0].notes == "hook"
    assert scenes.adopt_legacy(d) is False

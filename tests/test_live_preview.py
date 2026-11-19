"""Live preview: base video without overlays + browser-drawn layers.

The browser computes layer motion with static/animate.js (a port of ops/animate.py) and draws sprites made by the
export's own text renderer. These tests keep the two in step so the live preview matches the export.
"""

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from vedit.ops.animate import PRESETS, props_at
from vedit.ops.text import render_text
from vedit.project import Canvas, ClipMeta, Keyframe, Layer, Preset, TextStyle, Word, load
from vedit.render.layers import _caption_groups
from vedit.render.runner import base_hash, preview_dir, render_preview

from .conftest import needs_ffmpeg

ANIMATE_JS = Path(__file__).parents[1] / "src/vedit/preview_server/static/animate.js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _layers() -> list[Layer]:
    out = [
        Layer(id="kf", type="text", text="Keyframes", x="50%", y=300, keyframes=[
            Keyframe(t=0, x="10%", scale=0.5, opacity=0, ease="back_out"),
            Keyframe(t=0.8, x="60%", scale=1.2, opacity=1, rotation=15, ease="bounce_out"),
            Keyframe(t=1.6, x=700, y="80%", rotation=-30, ease="elastic_out"),
            Keyframe(t=2.4, reveal=0.5, ease="step"),
        ]),
        Layer(id="stretch", type="shape", shape="star", width=300, height=200, sx=1.2, fill="#112233", keyframes=[
            Keyframe(t=0, w=100, h=80, sx=0.5, fill="#FF0000", ease="back_out"),
            Keyframe(t=1.0, w=420, sy=1.6, fill="#00FF0080", ease="elastic_out"),
            Keyframe(t=2.2, h=500, sx=1.0, sy=0.7, fill="#0000FF", ease="ease_in"),
        ]),
        Layer(id="cycle", type="shape", shape="rect", presets=[Preset(name="color_cycle", params={"colors": ["#000", "#FFFFFF", "#FF00FF"], "period": 1.3}),
                                                                 Preset(name="squash_stretch"), Preset(name="stretch_in", params={"axis": "y"})]),
        Layer(id="typ", type="text", text="typed out slowly", presets=[Preset(name="typewriter"), Preset(name="fade_out")]),
        # motion: progress props, cubic-bezier and named bezier easings
        Layer(id="mo", type="shape", shape="ellipse", morph_to="star", keyframes=[
            Keyframe(t=0, draw=0, morph=0, count=0, x=100, ease="cubic-bezier(.7,0,.2,1)"),
            Keyframe(t=0.9, draw=0.6, morph=1, x=500, ease="snap"),
            Keyframe(t=1.7, draw=1, count=1, x=300, ease="overshoot"),
            Keyframe(t=2.5, morph=0.2, x=900, ease="anticipate"),
        ]),
        Layer(id="steps", type="shape", shape="rect", step_fps=12, presets=[Preset(name="sway"), Preset(name="drop_in")],
              keyframes=[Keyframe(t=0, x=0, ease="smooth"), Keyframe(t=2, x=500)]),
        Layer(id="eases", type="text", text="e", keyframes=[
            Keyframe(t=0, x=0, ease="expo_out"), Keyframe(t=0.6, x=400, ease="circ_out"), Keyframe(t=1.2, x=100, ease="smooth"),
            Keyframe(t=1.8, x=600, ease="bezier:0.1,0.9,0.9,0.1"), Keyframe(t=2.6, x=50)]),
    ]
    params = {"slide_in": {"direction": "left", "distance": 300}, "slide_out": {"direction": "down"}, "shake": {"amplitude": 20}}
    for name in PRESETS:  # every preset, alone and with a custom duration
        out.append(Layer(id=f"p-{name}", type="text", text="Hello", x=400, y="30%", scale=1.1, rotation=5,
                         presets=[Preset(name=name, params=params.get(name, {}))]))
        out.append(Layer(id=f"d-{name}", type="text", text="Hello", presets=[Preset(name=name, duration=0.9)]))
    out.append(Layer(id="stack", type="text", text="stack", presets=[Preset(name="pop_in"), Preset(name="bob"),
                                                                      Preset(name="wiggle"), Preset(name="slide_out")]))
    return out


@needs_node
def test_js_props_at_matches_python():
    canvas = Canvas()
    layers = _layers()
    dur = 3.0
    times = [i * 0.05 for i in range(int(dur / 0.05) + 1)]
    py = {L.id: [props_at(L, t, dur, canvas) for t in times] for L in layers}
    payload = {"canvas": canvas.model_dump(mode="json"), "dur": dur, "times": times,
               "layers": [L.model_dump(mode="json", by_alias=True) for L in layers]}
    script = (
        f"const A = require({json.dumps(str(ANIMATE_JS))});"
        "let d='';process.stdin.on('data',c=>d+=c).on('end',()=>{const p=JSON.parse(d);const o={};"
        "for(const L of p.layers)o[L.id]=p.times.map(t=>A.propsAt(L,t,p.dur,p.canvas));"
        "process.stdout.write(JSON.stringify(o));});"
    )
    res = subprocess.run(["node", "-e", script], input=json.dumps(payload), capture_output=True, text=True, check=True)
    js = json.loads(res.stdout)
    for lid, frames in py.items():
        for t, a, b in zip(times, frames, js[lid]):
            for k in ("x", "y", "scale", "rotation", "opacity", "reveal", "w", "h", "sx", "sy", "draw", "morph", "count"):
                if a[k] is None:
                    assert b[k] is None, f"{lid} {k} at t={t:.2f}: python None js {b[k]}"
                    continue
                assert b[k] == pytest.approx(a[k], abs=1e-6), f"{lid} {k} at t={t:.2f}: python {a[k]} js {b[k]}"
            assert b["fill"] == a["fill"], f"{lid} fill at t={t:.2f}: python {a['fill']} js {b['fill']}"


@needs_node
def test_js_caption_groups_match_python():
    words = [Word(text=t, start=s, end=s + 0.3) for t, s in
             [("one", 0), ("two", 0.4), ("three.", 0.8), ("four", 1.2), ("five", 2.5), ("six", 2.9), ("", 3.2), ("eight", 3.5)]]
    for wpg in (1, 2, 3, 5):
        L = Layer(id="c", type="captions", words=words, words_per_group=wpg)
        script = (f"const A = require({json.dumps(str(ANIMATE_JS))});"
                  f"process.stdout.write(JSON.stringify(A.captionGroups({json.dumps(L.model_dump(mode='json'))})));")
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
        assert json.loads(out) == _caption_groups(L)


def test_base_hash_ignores_overlays(make_project):
    p = make_project(2)
    h = base_hash(p)
    p.layers.append(Layer(id="t1", type="text", text="hi", start=0, end=1))
    p.clips[0].meta = ClipMeta(why="because")
    assert base_hash(p) == h
    p.clips[0].speed = 2
    assert base_hash(p) != h


def test_preview_hashes_change_when_effect_code_changes(make_project, monkeypatch):
    """A new mask / background-rebuild version must re-render the preview: the project file itself doesn't change."""
    from vedit.ops import track
    from vedit.render.runner import project_hash

    p = make_project(1)
    h, b = project_hash(p), base_hash(p)
    monkeypatch.setattr(track, "REMOVE_VERSION", track.REMOVE_VERSION + "-next")
    assert project_hash(p) != h and base_hash(p) != b


@needs_ffmpeg
def test_text_edits_never_rerender_the_base_video(cli, test_clip):
    cli("import", str(test_clip))
    d = cli.project
    assert render_preview(load(d), d, progress=False, base=True)["cached"] is False
    cli("text", "add", "Hello", "--y", "25%")
    assert render_preview(load(d), d, progress=False, base=True)["cached"] is True
    # the full preview (what export / `vedit frame` see) still includes the text
    assert render_preview(load(d), d, progress=False)["cached"] is False
    assert (preview_dir(d) / "base.mp4").exists() and (preview_dir(d) / "preview.mp4").exists()


@needs_ffmpeg
def test_sprite_and_mode_api(cli, test_clip):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    cli("import", str(test_clip))
    client = TestClient(create_app(cli.project, watch=False, chat=False))
    style = TextStyle(size=90, color="#ff0000", stroke_width=4)
    layer = Layer(id="t1", type="text", text="Live text", style=style).model_dump(mode="json", by_alias=True)

    r = client.post("/api/sprite", json={"layer": layer, "scale": 0.5})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    got = Image.open(io.BytesIO(r.content))
    want = render_text("Live text", style, 0.5)
    assert got.size == want.size  # the export's own renderer, at preview scale
    assert got.convert("RGBA").tobytes() == want.tobytes()

    part = Image.open(io.BytesIO(client.post("/api/sprite", json={"layer": layer, "scale": 0.5, "reveal": 4}).content))
    assert part.size == got.size  # typewriter keeps the box, only reveals part of it

    words = [{"text": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate(["a", "b", "c", "d"])]
    cap = Layer(id="c1", type="captions", words=words, words_per_group=2).model_dump(mode="json", by_alias=True)
    assert client.post("/api/sprite", json={"layer": cap, "group": 1, "hi": 1}).status_code == 200
    assert client.post("/api/sprite", json={"layer": {"type": "nope"}}).status_code == 400

    assert client.get("/api/state").json()["render"]["final"] is False
    assert client.post("/api/preview-mode", json={"final": True}).json() == {"final": True}
    assert client.get("/api/state").json()["render"]["final"] is True
    assert client.get("/static/animate.js").status_code == 200
    assert client.get("/static/../app.py").status_code == 404

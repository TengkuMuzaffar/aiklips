"""Instant preview frame (render/frame.py, POST /api/frame): one frame at the playhead in a fraction of a second,
matching the full preview, with unsaved slider values applied, never starting slow effect computations."""

import io

import pytest
from PIL import Image, ImageChops, ImageStat

from vedit import media
from vedit.ops import fx_filters
from vedit.ops.effects import NotReady
from vedit.project import Effect, load
from vedit.render.frame import clip_at, frame_project, render_frame
from vedit.render.runner import PREVIEW_SCALE, render

from .conftest import needs_ffmpeg


def _img(b: bytes) -> Image.Image:
    return Image.open(io.BytesIO(b)).convert("RGB")


def _diff(a: Image.Image, b: Image.Image) -> float:
    return ImageStat.Stat(ImageChops.difference(a, b).convert("L")).mean[0]


def test_clip_at_and_slow_zoom_is_resolved(make_project):
    p = make_project(2)  # c1 0-5s, c2 5-10s
    assert clip_at(p, 0) == (0, 0)
    i, local = clip_at(p, 7.5)
    assert i == 1 and local == pytest.approx(2.5)
    assert clip_at(p, 99)[0] == 1  # past the end: the last clip
    p.clips[1].zoom, p.clips[1].zoom_to = 1.0, 2.0
    mini = frame_project(p, 7.5)
    c = mini.clips[0]
    assert c.zoom == pytest.approx(1.5) and c.zoom_to == pytest.approx(1.5)
    assert c.in_ == pytest.approx(7.5) and c.out > c.in_ and c.transition is None
    assert mini.layers == [] and mini.music == []


def test_override_replaces_settings_for_this_frame_only(make_project):
    p = make_project(2)
    over = p.clips[0].model_dump(mode="json", by_alias=True)
    over["grade"] = {"saturation": 0}
    mini = frame_project(p, 1.0, over)
    assert mini.clips[0].grade.saturation == 0
    assert p.clips[0].grade is None  # the project itself is untouched
    # dragging a clip that isn't under the playhead shows that clip
    over2 = {**p.clips[1].model_dump(mode="json", by_alias=True), "zoom": 1.3}
    assert frame_project(p, 1.0, over2).clips[0].zoom == pytest.approx(1.3)


@needs_ffmpeg
def test_frame_matches_the_full_preview(cli, test_clip):
    cli("import", str(test_clip))
    cli("color", "set", "--saturation", "0.3", "--brightness", "0.1")
    d = cli.project
    p = load(d)
    fx_filters.add(p, p.clips[0], "vignette", {"amount": 1})
    full = d / "full.mp4"
    render(p, d, full, PREVIEW_SCALE, "preview", progress=False, layers=False)
    for t in (0.5, 4.0):
        png = d / f"full-{t}.png"
        media.extract_frame(full, t, png)
        ref = Image.open(png).convert("RGB")
        got = _img(render_frame(p, d, t))
        assert got.size == ref.size
        assert _diff(got, ref) < 3, f"frame at {t}s differs from the preview"


@needs_ffmpeg
def test_unsaved_values_show_in_the_frame(cli, test_clip):
    cli("import", str(test_clip))
    d = cli.project
    p = load(d)
    plain = _img(render_frame(p, d, 1.0))
    over = p.clips[0].model_dump(mode="json", by_alias=True)
    over["grade"] = {"saturation": 0}
    grey = _img(render_frame(p, d, 1.0, over))
    def colourfulness(im: Image.Image) -> float:
        r, g, b = im.split()
        return _diff(r, g) + _diff(g, b)

    # the colour is (almost) gone: the grade works in YUV, so the preview itself keeps a faint cast
    assert colourfulness(grey) < colourfulness(plain) / 5
    assert _diff(plain, grey) > 5


@needs_ffmpeg
def test_frame_never_starts_slow_effect_work(cli, test_clip):
    cli("import", str(test_clip))
    d = cli.project
    p = load(d)
    p.clips[0].effects.append(Effect(type="bg", params={"mode": "fast", "replace": "color:#00ff00"}))
    with pytest.raises(NotReady):
        render_frame(p, d, 1.0)


@needs_ffmpeg
def test_frame_api(cli, test_clip):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    cli("import", str(test_clip))
    client = TestClient(create_app(cli.project, watch=False, chat=False))
    r = client.post("/api/frame", json={"t": 2.0})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert _img(r.content).width > 0
    clip = client.get("/api/state").json()["project"]["clips"][0]
    r = client.post("/api/frame", json={"t": 2.0, "clip": {**clip, "zoom": 1.6}})
    assert r.status_code == 200
    assert client.post("/api/frame", json={"t": 2.0, "clip": {**clip, "id": "nope"}}).status_code == 400

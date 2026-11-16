"""Fast preview loop: undo coalescing for property drags, cancellable renders, and skipping
timeline thumbnails when only overlays changed."""

import pytest

from vedit import history, media
from vedit.editor import actions
from vedit.project import load
from vedit.render.runner import preview_dir, render_preview, timeline_hash

from .conftest import needs_ffmpeg


def test_coalesce_key_matches_same_field_only():
    k = actions.coalesce_key
    assert k("layer_set", {"layer": "t1", "style": {"size": 80}}) == k("layer_set", {"layer": "t1", "style": {"size": 96}})
    assert k("layer_set", {"layer": "t1", "style": {"size": 80}}) != k("layer_set", {"layer": "t2", "style": {"size": 80}})
    assert k("layer_set", {"layer": "t1", "style": {"size": 80}}) != k("layer_set", {"layer": "t1", "style": {"color": "#fff"}})
    assert k("split", {"at": 1.0}) is None  # structural edits always get their own undo step


@needs_ffmpeg
def test_slider_drag_is_one_undo_step(cli, test_clip):
    cli("import", str(test_clip))
    d = cli.project
    st = actions.apply(d, "layer_add_text", {"text": "Hi", "start": 0})
    assert st["ok"]
    lid = load(d).layers[0].id
    before = history.head(d)
    for size in (90, 100, 110, 120):  # one drag
        actions.apply(d, "layer_set", {"layer": lid, "style": {"size": size}})
    assert history.head(d) == before + 1
    assert load(d).layers[0].style.size == 120
    actions.apply(d, "layer_set", {"layer": lid, "style": {"color": "#ff0000"}})  # a different field: new step
    assert history.head(d) == before + 2

    history.undo(d)
    history.undo(d)
    assert load(d).layers[0].style.size == 80  # the whole drag undone at once


@needs_ffmpeg
def test_coalescing_stops_after_a_pause(cli, test_clip, monkeypatch):
    cli("import", str(test_clip))
    d = cli.project
    actions.apply(d, "clip_set", {"clip": "c1", "volume": 0.5})
    h = history.head(d)
    monkeypatch.setattr(history, "COALESCE_SECONDS", 0.0)
    actions.apply(d, "clip_set", {"clip": "c1", "volume": 0.6})
    assert history.head(d) == h + 1


def test_timeline_hash_ignores_layers(make_project):
    from vedit.project import Layer

    p = make_project(2)
    h = timeline_hash(p)
    p.layers.append(Layer(id="t1", type="text", text="hello", start=0, end=1))
    assert timeline_hash(p) == h
    p.clips[0].volume = 0.3
    assert timeline_hash(p) != h


@needs_ffmpeg
def test_text_edit_keeps_timeline_strip(cli, test_clip):
    cli("import", str(test_clip))
    d = cli.project
    render_preview(load(d), d, progress=False)
    wave = preview_dir(d) / "wave.png"
    t0 = wave.stat().st_mtime_ns
    actions.apply(d, "layer_add_text", {"text": "Hi", "start": 0})
    res = render_preview(load(d), d, progress=False)
    assert res["cached"] is False
    assert wave.stat().st_mtime_ns == t0  # only the overlay changed: thumbnails + waveform reused


@needs_ffmpeg
def test_preview_render_can_be_cancelled(cli, test_clip):
    cli("import", str(test_clip))
    d = cli.project
    with pytest.raises(media.Cancelled):
        render_preview(load(d), d, force=True, progress=False, should_stop=lambda: True)
    assert not list(preview_dir(d).glob("preview.part*")), "temp files left behind"
    assert not (preview_dir(d) / "hash").exists()  # a cancelled render never looks cached
    assert render_preview(load(d), d, progress=False)["cached"] is False

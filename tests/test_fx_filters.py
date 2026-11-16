"""Clip filters (ops/fx_filters.py): every filter renders through real FFmpeg and changes the picture; editing
ops (add / set / reorder / copy / remove / split) keep ids unique and settings in range."""

import pytest
from PIL import Image, ImageChops, ImageStat

from vedit import history, media
from vedit.editor import actions
from vedit.ops import cut, fx_filters
from vedit.project import VeditError, load
from vedit.render.runner import render

from .conftest import needs_ffmpeg

# settings strong enough that the change is easy to measure on the test pattern
STRONG = {"blur": {"amount": 30}, "glow": {"amount": 1}, "vignette": {"amount": 1}, "grain": {"amount": 50},
          "rgb_split": {"amount": 30}, "pixelate": {"size": 60}, "film": {"amount": 1}, "vhs": {"amount": 1},
          "shake": {"amount": 60}, "zoom_pulse": {"amount": 0.3}, "posterize": {"levels": 2}, "sharpen": {"amount": 2.5},
          "denoise": {"amount": 12}}


@pytest.fixture(scope="module")
def short_project(tmp_path_factory, test_clip):
    """A portrait 1-second project (so cinema bars apply) rendered at quarter size: fast enough to try every filter."""
    from typer.testing import CliRunner

    from vedit.cli import app

    d = tmp_path_factory.mktemp("fx") / "proj"
    r = CliRunner()
    for args in (["init", str(d)], ["-p", str(d), "import", str(test_clip)], ["-p", str(d), "canvas", "--preset", "reels"],
                 ["-p", str(d), "keep", "--from", "1", "--to", "2"]):
        assert r.invoke(app, ["--json", *args]).exit_code == 0
    return d


def _frame(project, d, name, t=0.5) -> Image.Image:
    out = d / f"{name}.mp4"
    render(project, d, out, 0.25, "preview", progress=False)
    png = d / f"{name}.png"
    media.extract_frame(out, t, png)
    return Image.open(png).convert("RGB")


@needs_ffmpeg
@pytest.mark.parametrize("key", list(fx_filters.FILTERS))
def test_every_filter_renders_and_changes_the_picture(short_project, key):
    d = short_project
    base_p = load(d)
    base = _frame(base_p, d, "base")
    p = load(d)
    fx_filters.add(p, p.clips[0], key, STRONG.get(key, {}))
    img = _frame(p, d, f"fx-{key}")
    assert img.size == base.size
    diff = ImageStat.Stat(ImageChops.difference(base, img).convert("L")).mean[0]
    # subtle by design: trails only smear moving things, sharpen/denoise only touch fine detail
    floor = {"trails": 0.3, "sharpen": 0.5, "denoise": 0.3}.get(key, 2)
    assert diff > floor, f"{key} did not change the picture (mean diff {diff:.2f})"
    if key == "letterbox":
        top = img.crop((0, 0, img.width, 8)).convert("L")
        assert ImageStat.Stat(top).mean[0] < 8  # black bar at the top of the portrait frame


@needs_ffmpeg
def test_switched_off_filter_renders_like_none(short_project):
    d = short_project
    base = _frame(load(d), d, "base-off")
    p = load(d)
    eff = fx_filters.add(p, p.clips[0], "invert")
    eff.params["on"] = False
    img = _frame(p, d, "off")
    assert ImageStat.Stat(ImageChops.difference(base, img).convert("L")).mean[0] < 1


def test_add_set_move_copy_remove(make_project):
    p = make_project(3)
    c1 = p.clips[0]
    a = fx_filters.add(p, c1, "blur", {"amount": 500})
    assert a.params["amount"] == 80  # clamped to the range
    b = fx_filters.add(p, c1, "vignette")
    assert a.params["id"] != b.params["id"]
    with pytest.raises(VeditError):
        fx_filters.add(p, c1, "nope")
    with pytest.raises(VeditError):
        fx_filters.add(p, c1, "blur", {"colour": 1})
    with pytest.raises(VeditError):
        fx_filters.set_params(p, b.params["id"], {"amount": "lots"})
    with pytest.raises(VeditError):
        fx_filters.add(p, c1, "mirror", {"direction": "sideways"})

    fx_filters.set_params(p, a.params["id"], {"amount": 20, "on": "false"})
    assert a.params["amount"] == 20 and a.params["on"] is False
    fx_filters.reset(p, a.params["id"])
    assert a.params["amount"] == 12 and a.params["on"] is True

    fx_filters.move(p, b.params["id"], 0)
    assert [e.params["key"] for e in c1.effects] == ["vignette", "blur"]

    assert fx_filters.copy_to(p, c1, p.clips) == 2
    ids = [e.params["id"] for c in p.clips for e in c.effects]
    assert len(ids) == 6 and len(set(ids)) == 6
    assert [e.params["key"] for e in p.clips[2].effects] == ["vignette", "blur"]

    fx_filters.remove(p, a.params["id"])
    assert [e.params["key"] for e in c1.effects] == ["vignette"]


def test_split_gives_each_half_its_own_filter_ids(make_project):
    p = make_project(1)
    fx_filters.add(p, p.clips[0], "grain")
    cut.split_at(p, 4.0)
    ids = [e.params["id"] for c in p.clips for e in c.effects]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_catalog_lists_every_filter_with_settings():
    cat = {f["key"]: f for f in fx_filters.catalog()}
    assert set(cat) == set(fx_filters.FILTERS)
    assert {f["category"] for f in cat.values()} <= set(fx_filters.CATEGORIES)
    assert cat["mirror"]["params"][0]["choices"] == ["horizontal", "vertical", "both"]


@needs_ffmpeg
def test_gui_ops_and_undo(cli, test_clip):
    cli("import", str(test_clip))
    d = cli.project
    actions.apply(d, "fx_add", {"clip": "c1", "key": "blur"})
    fid = load(d).clips[0].effects[0].params["id"]
    h = history.head(d)
    for v in (14, 18, 22):  # one slider drag
        actions.apply(d, "fx_set", {"id": fid, "amount": v})
    assert history.head(d) == h + 1 and load(d).clips[0].effects[0].params["amount"] == 22
    actions.apply(d, "fx_add", {"key": "vignette"})  # no clip: every clip
    actions.apply(d, "fx_move", {"id": load(d).clips[0].effects[1].params["id"], "index": 0})
    assert [e.params["key"] for e in load(d).clips[0].effects] == ["vignette", "blur"]
    actions.apply(d, "fx_remove", {"id": fid})
    assert [e.params["key"] for e in load(d).clips[0].effects] == ["vignette"]
    actions.apply(d, "split", {"at": 3.0})
    second = load(d).clips[1]
    assert [e.params["key"] for e in second.effects] == ["vignette"]  # the split copies the filters
    actions.apply(d, "move_apply", {"name": "flash_cut", "clip": second.id})
    history.undo(d)
    assert len(load(d).clips) == 2


@needs_ffmpeg
def test_cli_fx_commands(cli, test_clip):
    cli("import", str(test_clip))
    assert len(cli("fx", "filters")["filters"]) == len(fx_filters.FILTERS)
    r = cli("fx", "add", "grain", "--clip", "c1", "--set", "amount=30")
    fid = r["added"][0]
    assert cli("fx", "set", fid, "amount=40", "on=false")["params"]["amount"] == 40
    assert "grain" in cli("info")["clips"][0]["effects"][0]
    cli("fx", "remove", "all")
    assert cli("info")["clips"][0]["effects"] == []
    cli("fx", "add", "nope", ok=False)

"""Design: sheet splitting, MotionPlan validation / fitting / patching and the builder (`vedit design build`)."""

import json
import re
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from vedit.design import builder, planner, sheet
from vedit.design.spec import Element, MotionPatch, MotionPlan, PatchOp, Scene

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def grid_sheet(tmp_path) -> Path:
    """A 2x3 character sheet: six blobs on white."""
    im = Image.new("RGB", (900, 600), "white")
    d = ImageDraw.Draw(im)
    for r in range(2):
        for c in range(3):
            x, y = 40 + c * 290, 40 + r * 280
            d.ellipse((x + 30, y + 20, x + 200, y + 220), fill=(200, 40 + 60 * c, 80 * r))
    out = tmp_path / "sheet.png"
    im.save(out)
    return out


def _plan(clip="f1", piece="p1", durs=(4, 5, 3)) -> MotionPlan:
    return MotionPlan(title="Test", background="#14141c", scenes=[
        Scene(id="S1", dur=durs[0], purpose="hook", transition="pop", elements=[
            Element(kind="text", text="MEET BLOB", look="bold-yellow", y="25%", enter="drop_in"),
            Element(kind="image", file=piece, width=500, y="60%", loop="float")]),
        Scene(id="S2", dur=durs[1], purpose="demo", background="#22303a", transition="slide", elements=[
            Element(kind="clip", file=clip, frame="card", y="45%", src_in=1.0,
                    path=[{"t": 0, "scale": 0.9}, {"t": 5, "scale": 1.0}]),
            Element(kind="text", text="It moves", at=1, y="70%", enter="typewriter")]),
        Scene(id="S3", dur=durs[2], purpose="cta", elements=[
            Element(kind="shape", shape="rect", width=700, height=200, fill="#c5f467", loop="pulse"),
            Element(kind="text", text="Follow", y="50%", color="#111111")]),
    ])


# ---- sheet -----------------------------------------------------------------------------------

def test_sheet_split_finds_grid_panels(grid_sheet, tmp_path):
    pieces = sheet.split(grid_sheet, tmp_path / "out", cutout=True)
    assert len(pieces) == 6
    xs = [p["box"][0] for p in pieces[:3]]
    assert xs == sorted(xs)  # reading order
    im = Image.open(pieces[0]["path"])
    assert im.mode == "RGBA" and im.getpixel((2, 2))[3] == 0  # background cut out
    assert im.getpixel((im.width // 2, im.height // 2))[3] == 255


def test_sheet_forced_grid(grid_sheet, tmp_path):
    assert len(sheet.split(grid_sheet, tmp_path / "g", grid="2x2")) == 4
    with pytest.raises(Exception):
        sheet.split(grid_sheet, tmp_path / "g", grid="banana")


# ---- planner (pure) -----------------------------------------------------------------------------

FILES = {"f1": {"kind": "video", "path": "x.mp4", "duration": 6.0}, "p1": {"kind": "image", "path": "p.png"}}


def test_validate_good_plan():
    assert planner.validate(_plan(), FILES, 12) == []


def test_validate_catches_problems():
    p = _plan(durs=(30, 30, 15))
    p.scenes[0].elements[0].enter = "bogus"
    p.scenes[0].elements[1].loop = "pop_in"  # an 'in' preset used as a loop
    p.scenes[1].elements[0].file = "p1"      # an image used as a clip
    p.scenes[2].elements[1].text = "one two three four five six seven eight nine ten eleven twelve thirteen"
    errs = "\n".join(planner.validate(p, FILES, 30))
    for want in ("above 60s", "unknown preset bogus", "'in' preset", "is a image, not a clip", "too long"):
        assert want in errs, (want, errs)


def test_fit_scales_to_target_and_snaps_to_beats():
    p = planner.fit(_plan(durs=(4, 4, 4)), 24.0)
    assert p.length == pytest.approx(24.0, abs=0.01)
    beats = [i * 0.5 for i in range(100)]
    p = planner.fit(_plan(durs=(3.9, 4.1, 4)), 12.0, beats)
    assert p.scenes[0].dur == pytest.approx(4.0, abs=0.01) and p.length == pytest.approx(12.0, abs=0.01)
    p = planner.fit(_plan(durs=(40, 40, 40)), 90)
    assert p.length <= 60.0


def test_patch_changes_only_targets():
    p = _plan()
    patch = MotionPatch(ops=[PatchOp(op="set", target="S2.E2", element=Element(kind="text", y="40%")),
                             PatchOp(op="remove", target="S1.E2"),
                             PatchOp(op="add", target="S3", element=Element(kind="text", text="New")),
                             PatchOp(op="set", target="S9.E1")])
    new, notes = planner.apply_patch(p, patch)
    assert new.scenes[1].elements[1].y == "40%" and new.scenes[1].elements[1].text == "It moves"
    assert [e.kind for e in new.scenes[0].elements] == ["text"]
    assert new.scenes[2].elements[-1].text == "New"
    assert new.scenes[1].elements[0] == p.scenes[1].elements[0]
    assert notes == ["no scene S9.E1"]


# ---- builder ----------------------------------------------------------------------------------

@needs_ffmpeg
def test_builder_makes_a_project(test_clip, grid_sheet, tmp_path):
    from vedit import history
    from vedit.audit import audit
    from vedit.generate.executor import create_project
    from vedit.project import load

    pieces = sheet.split(grid_sheet, tmp_path / "pieces", cutout=True)
    files = builder.files_from_json({"f1": str(test_clip), "p1": pieces[0]["path"]})
    pdir = tmp_path / "proj"
    create_project(pdir, "t", "reels")
    rep = builder.build(_plan(), pdir, files)
    assert rep["skipped"] == [] and rep["length"] == 12.0
    p = load(pdir)
    assert p.duration == pytest.approx(12.0, abs=0.05)
    kinds = [l.type for l in p.layers]
    assert kinds.count("text") == 3 and "video" in kinds and "image" in kinds and kinds.count("shape") == 2
    assert p.kind == "design" and [s.dur for s in p.scenes] == [4, 5, 3]  # opens in the Design studio
    assert p.layer(p.scenes[1].bg_layer).fill == "#22303a" and p.layers[0].id == p.scenes[1].bg_layer
    v = next(l for l in p.layers if l.type == "video")
    assert v.start == 4.0 and v.end == 9.0 and v.src_in == 1.0 and v.radius == 36 and v.keyframes
    t1 = next(l for l in p.layers if l.text == "MEET BLOB")
    assert [x.name for x in t1.presets] == ["drop_in", "fade_out"]
    assert builder.saved_plan(pdir).title == "Test"
    assert any("AI design: scene 2" in h["message"] for h in history.log(pdir))
    # rebuilding replaces the timeline instead of stacking a second copy
    builder.build(_plan(), pdir, files)
    assert len(load(pdir).layers) == len(p.layers)
    found = audit(load(pdir), pdir, render=False)["findings"]
    assert not any(f["category"] == "pacing" for f in found)  # the background clip is not "a long shot"


@needs_ffmpeg
def test_builder_skips_bad_elements(test_clip, tmp_path):
    from vedit.generate.executor import create_project

    files = builder.files_from_json({"f1": str(test_clip)})
    pdir = tmp_path / "proj"
    create_project(pdir, "t", "reels")
    rep = builder.build(_plan(piece="p9"), pdir, files)
    assert any("S1.E2" in s for s in rep["skipped"]) and "S2.E1 clip" in rep["applied"]


def test_motion_check_flags_frozen_stretch():
    from vedit.audit import check_motion
    from vedit.project import Clip, Layer, Preset, Project, Source

    p = Project()
    p.sources["s1"] = Source(path="media/.gap-101014.mp4", duration=600, has_audio=False)
    p.clips = [Clip(id="c1", source="s1", **{"in": 0}, out=10)]
    p.layers = [Layer(id="t1", type="text", text="hi", start=0, end=10, presets=[{"name": "fade_in"}])]
    f = check_motion(p, Path("."))
    assert len(f) == 1 and f[0]["category"] == "motion"
    p.layers[0].presets.append(Preset(name="bob"))
    assert check_motion(p, Path(".")) == []


# ---- the whole job, fake LLM ------------------------------------------------------------------

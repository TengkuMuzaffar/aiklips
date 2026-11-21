"""Generate's `vedit` ops: the generation editor runs the same CLI commands as the editor chat AI."""

import shutil

import pytest
import typer

from vedit.cli import app
from vedit.generate import commands, executor, planner
from vedit.generate.spec import EditPlan, Op, PatchOp, PlanPatch

FILES = {"f1": {"path": "/media/clip.mp4", "kind": "video"}, "m1": {"path": "/media/song.wav", "kind": "audio"}}


def test_every_cli_command_is_allowed_or_denied():
    """A new CLI command must be a deliberate choice for Generate, like the editor/CLI parity test."""
    top = set(typer.main.get_command(app).commands)
    assert not (commands.ALLOWED & set(commands.DENIED))
    missing = top - commands.ALLOWED - set(commands.DENIED)
    assert not missing, f"decide in generate/commands.py whether Generate may run: {sorted(missing)}"
    assert not (commands.ALLOWED | set(commands.DENIED)) - top, "stale names in ALLOWED / DENIED"


def test_parse_allows_editing_commands_and_blocks_the_rest():
    assert commands.parse("vedit sfx add whoosh --at 1.2", FILES) == ["sfx", "add", "whoosh", "--at", "1.2"]
    assert commands.parse("uv run vedit --json fx apply snap_zoom --at 2", FILES)[:2] == ["fx", "apply"]
    assert commands.parse('text add "50/50 deal" --y 20%', FILES)[2] == "50/50 deal"  # a slash in text is not a path
    for bad, why in [("export", "not available"), ("undo", "not available"), ("split --at 2", "clip ops"),
                     ("mask add --at 1 --box 1,2,3,4", "slow"), ("captions auto", "only `captions style\\|words`"),
                     ("text look save mine --from t1", "not available"), ("color lut /tmp/x.cube", "not available"),
                     ("frobnicate", "unknown command"), ("text add hi --project /tmp/x", "--project"), ('text add "oops', "parse")]:
        with pytest.raises(ValueError, match=why):
            commands.parse(bad, FILES)


def test_file_arguments_only_take_batch_files_or_library_items():
    assert commands.parse("music add m1 --volume 0.3", FILES)[2] == "/media/song.wav"
    assert commands.parse("overlay add light_leak --blend screen", FILES)[2] == "light_leak"
    assert commands.parse("overlay add f1 --circle", FILES)[2] == "/media/clip.mp4"
    for bad in ("image add /etc/passwd", "overlay add ~/secret.mp4", "music add other.wav"):
        with pytest.raises(ValueError, match="file ids"):
            commands.parse(bad, FILES)


def test_reference_lists_commands_from_the_cli():
    ref = commands.reference()
    assert "sfx add PRESET" in ref and "fx apply NAME" in ref and "shape add" in ref and "music make" in ref
    assert "\nexport" not in ref and "\nmask add" not in ref and "captions auto" not in ref and "text look save" not in ref
    assert "whoosh" in ref and "liquid_glass" in ref and "light_leak" in ref and "pop_in" in ref


def test_validate_and_patch_cover_vedit_ops():
    log = {"f1": {"kind": "video", "segments": [{"id": "f1.s1", "from": 0, "to": 6}]}}
    clip = Op(op="clip", file="f1", segment="f1.s1", start=0, end=2, beat="b1", why="shows the thing")
    plan = EditPlan(beats=[{"id": "b1", "role": "hook"}], ops=[clip, Op(op="vedit", command="export")])
    assert any("vedit op 1" in e and "not available" in e for e in planner.validate_plan(plan, log))
    plan.ops[1].command = "sfx add pop --at @C1.start"
    assert not planner.validate_plan(plan, log)
    assert "V1: vedit sfx add pop --at @C1.start" in planner.plan_listing(plan)
    patch = PlanPatch(ops=[PatchOp(op="replace_command", target="V1", command="sfx add whoosh --at @C1.end-0.1"),
                           PatchOp(op="add_command", command="fx add vignette"),
                           PatchOp(op="add_command", command="export")])
    new, problems = planner.apply_patch(plan, patch, log)
    assert [o.command for o in new.ops if o.op == "vedit"] == ["sfx add whoosh --at @C1.end-0.1", "fx add vignette"]
    assert len(problems) == 1 and "export" in problems[0]


def test_enforce_keeps_the_models_explicit_choices():
    d = {"text": {"y": "25%", "size": 72, "color": "#FFFFFF", "uppercase": False, "bg": None, "presets": ["pop_in"],
                  "font": "inter-black", "accent_font": "dm-serif-italic", "letter_spacing": 0.0},
         "transitions": {"default": "none", "accents": [], "frequency": "rare", "duration": 0.3},
         "jumpcut_zoom": None, "music": None, "captions": None, "beat_sync": False, "grade": None, "hook_duration_s": 1.5}
    from vedit.generate.spec import Creative
    from vedit.ops import fonts

    other = [f for f in fonts.FONTS if f not in ("inter-black", "dm-serif-italic")][:2]
    plan = EditPlan(ops=[Op(op="text", text="a", color="#FF3366", size=110, y="18%", presets=["drop_in"], letter_spacing=2),
                         Op(op="text", text="b", font=other[0]), Op(op="text", text="c", font=other[1]),
                         Op(op="jumpcut_zoom", level=1.15), Op(op="vedit", command="sfx add pop --at 1")])
    out = planner.enforce(plan, d, Creative(index=1, angle="x", hook_text="a"))
    a, b, c = [o for o in out.ops if o.op == "text"]
    assert (a.color, a.size, a.y, a.presets, a.letter_spacing) == ("#FF3366", 110, "18%", ["drop_in"], 2)
    assert a.font == "inter-black" and b.font == other[0] and c.font == "inter-black"  # two fonts at most
    assert any(o.op == "jumpcut_zoom" and o.level == 1.15 for o in out.ops)
    assert any(o.op == "vedit" for o in out.ops)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_executor_runs_vedit_ops_with_references(test_clip, tmp_path):
    from vedit import history, media
    from vedit.project import load

    info = media.probe(test_clip)
    files = {"f1": {"path": str(test_clip), "kind": "video", "duration": info["duration"], "width": info.get("width"),
                    "height": info.get("height"), "fps": info.get("fps"), "has_audio": True}}
    plan = EditPlan(ops=[
        Op(op="clip", file="f1", start=0, end=2), Op(op="clip", file="f1", start=3, end=5),
        Op(op="text", text="Hook", start=0, end=1.5),
        Op(op="vedit", name="card", command="shape add rect --x 50% --y 25% --width 800 --height 180 "
                                            "--fill #111111 --start @T1.start --end @T1.end"),
        Op(op="vedit", command="layer set @card --opacity 0.8"),
        Op(op="vedit", command="sfx add whoosh --at @C2.start-0.2 --volume 0.5"),
        Op(op="vedit", command='text add "Follow @aiklips" --start @C2.mid --y 30%'),
        Op(op="vedit", command="layer set @nope.start --opacity 1"),
        Op(op="vedit", command="export"),
    ])
    pdir = tmp_path / "v01"
    rep = executor.execute(plan, pdir, files, "test", "reels")
    p = load(pdir)
    card = next(l for l in p.layers if l.type == "shape")
    hook = next(l for l in p.layers if l.text == "Hook")
    assert (card.start, card.end, card.opacity) == (hook.start, hook.end, 0.8)
    assert any(l.text == "Follow @aiklips" and abs(l.start - 3.0) < 0.05 for l in p.layers)  # @aiklips stays text; C2 mid = 3 s
    assert len(p.music) == 1 and abs(p.music[0].start - 1.8) < 0.05  # the whoosh, 0.2 s before the cut
    assert len(rep["skipped"]) == 2 and "@nope" in rep["skipped"][0] and "export" in rep["skipped"][1]
    assert sum(1 for s in history.log(pdir) if "AI:" not in s.get("message", "")) >= 4  # one undo step per command


def test_generate_can_use_3d_objects():
    ok = commands.parse("3d add car --param style=sport --from -30%,68%", FILES)
    assert ok[:2] == ["3d", "add"]
    with pytest.raises(ValueError, match="3D models from files"):
        commands.parse("3d add model --src /etc/x.glb", FILES)
    ref = commands.reference()
    assert "3d add KIND" in ref and "3d move LAYER" in ref and "dog (ears=floppy" in ref
    assert "title_3d" in planner._moves_text(planner.StyleDNA())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_executor_builds_3d_moments(test_clip, tmp_path):
    from vedit import media
    from vedit.project import load

    info = media.probe(test_clip)
    files = {"f1": {"path": str(test_clip), "kind": "video", "duration": info["duration"], "width": info.get("width"),
                    "height": info.get("height"), "fps": info.get("fps"), "has_audio": True}}
    plan = EditPlan(ops=[
        Op(op="clip", file="f1", start=0, end=2), Op(op="clip", file="f1", start=3, end=5),
        Op(op="move", type="title_3d", start=0.1, text="NEW DROP"),
        Op(op="move", type="drive_by_3d", start=2.2, text="car #E11D48"),
        Op(op="vedit", name="coin", command="3d add coin --y 40% --width 400 --start @C2.start --end @C2.end --no-intro"),
        Op(op="vedit", command="3d spin @coin --axis x --period 1.2"),
        Op(op="vedit", command="3d move @coin --from 20%,40% --to 80%,40% --loop pingpong --dur 1"),
    ])
    pdir = tmp_path / "v01"
    rep = executor.execute(plan, pdir, files, "test", "reels")
    assert not rep["skipped"], rep["skipped"]
    p = load(pdir)
    objs = {l.obj3d.kind: l for l in p.layers if l.type == "obj3d"}
    assert set(objs) == {"text", "car", "coin"}
    assert objs["text"].obj3d.text == "NEW DROP" and objs["car"].obj3d.color == "#E11D48"
    assert any(pr.name == "travel" for pr in objs["car"].presets)
    coin = objs["coin"]
    assert abs(coin.start - 2.0) < 0.05 and {pr.name for pr in coin.presets} == {"spin3d", "travel"}

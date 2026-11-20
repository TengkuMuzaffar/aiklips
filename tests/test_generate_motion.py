"""Generate from a motion-design reference: palette instead of a wash-out grade, graphic scenes, never silent."""

import shutil
import subprocess

import pytest

from vedit.generate import executor, planner, timing
from vedit.generate.spec import Brief, Creative, EditPlan, Op, PatchOp, PlanPatch
from vedit.references.dna import StyleDNA

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
AXES = {"hook": "reference", "order": "story", "pacing": "reference", "caption_style": "reference", "text_animation": "reference",
        "transitions": "reference", "music_section": "start", "color_look": "reference", "font": "reference", "structure": "reference"}


def motion_dna() -> StyleDNA:
    dna = StyleDNA(summary="Clean minimal brand motion-graphics promo with kinetic typography on white", tags=["motion-graphics"])
    dna.color.look, dna.color.measured = "clean-bright", {"luma": 0.86, "p05": 0.5, "p95": 0.99, "sat": 0.14, "warmth": -0.05, "tint": 0.1}
    dna.color.palette = {"background": "#F8FAF9", "bg_share": 0.63, "surface": "#D5ECDE", "ink": None, "accents": ["#1AAA4B"]}
    dna.text.color, dna.text.animation_in = "#111111", ["pop_in", "slide_in"]
    return dna


@needs_ffmpeg
def test_palette_finds_canvas_and_accent(tmp_path):
    from vedit.references import analysis

    out = tmp_path / "mg.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0xF8FAF9:s=360x640:d=2:r=10",
                    "-vf", "drawbox=x=60:y=200:w=240:h=160:color=0x1AAA4B:t=fill,drawbox=x=60:y=420:w=240:h=40:color=0x111111:t=fill",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    pal = analysis.palette(out)
    assert pal["background"] and planner._light(pal["background"]) and pal["bg_share"] > 0.5
    assert pal["accents"] and pal["accents"][0][1:3] < "40"  # a green: low red
    assert pal["ink"] and not planner._light(pal["ink"])


def test_motion_reference_never_grades_footage_toward_its_canvas():
    dna = motion_dna()
    assert planner.is_motion(dna)
    d = planner.directives(dna, Brief(length_s=20), AXES, {}, False)
    assert d["grade"]["target"] is None and d["grade"]["look"] == "natural"     # clips matched to each other only
    md = d["motion_design"]
    assert md["canvas"] == "#F8FAF9" and md["accents"] == ["#1AAA4B"] and md["ink"] == "#111111" and md["kinetic"] == "pop"
    assert d["text"]["bg"] == "#F8FAF9E6" and d["text"]["color"] == "#111111"  # titles over footage on a canvas card
    assert d["clip_count"] < round(20 / d["avg_shot_s"])                         # room for scenes
    footage_ref = StyleDNA(summary="Fast handheld vlog cuts", tags=["vlog"])
    footage_ref.color.measured = {"luma": 0.5, "p05": 0.1, "p95": 0.9, "sat": 0.3, "warmth": 0.0, "tint": 0.0}
    d2 = planner.directives(footage_ref, Brief(), AXES, {}, False)
    assert not planner.is_motion(footage_ref) and d2["grade"]["target"] and d2["motion_design"] is None


def test_no_song_means_a_made_loop_not_silence():
    dna = motion_dna()
    d = planner.directives(dna, Brief(), AXES, {}, False)
    assert d["music"] is None and d["music_make"]["style"] in ("pop", "house") and d["music_make"]["volume"] == 0.5
    c = Creative(index=1, angle="x", hook_text="Hook?", cta_text="Book now")
    plan = EditPlan(beats=[{"id": "b1", "role": "hook"}, {"id": "b2", "role": "cta"}],
                    ops=[Op(op="clip", file="f1", start=0, end=2, beat="b1")])
    out = planner.enforce(plan, d, c)
    cmds = [o.command for o in out.ops if o.op == "vedit"]
    assert len(cmds) == 1 and cmds[0].startswith("music make ") and "--volume 0.5" in cmds[0]
    planner.audio_defaults(out, {})
    assert next(o for o in out.ops if o.op == "clip").audio == "mute"  # b-roll under the made music
    # the model's own loop wins
    plan = EditPlan(ops=[Op(op="clip", file="f1", start=0, end=2), Op(op="vedit", command="vedit music make lofi --mood chill")])
    assert [o.command for o in planner.enforce(plan, d, c).ops if o.op == "vedit"] == ["vedit music make lofi --mood chill"]
    assert "music make" in planner._edit_prompt(_spec(), dna, {}, c, AXES, d)


def _spec():
    from vedit.generate.spec import BatchSpec
    return BatchSpec(refs=[1], footage=["x"])


def test_enforce_fills_scenes_from_the_palette_and_adds_an_end_card():
    dna = motion_dna()
    d = planner.directives(dna, Brief(), AXES, {}, False)
    c = Creative(index=1, angle="x", hook_text="Hook?", cta_text="Book now")
    plan = EditPlan(beats=[{"id": "b1", "role": "hook"}, {"id": "b2", "role": "cta"}],
                    ops=[Op(op="clip", file="f1", start=0, end=2, beat="b1"), Op(op="text", text="Hook?", start=0, end=1.5)])
    out = planner.enforce(plan, d, c)
    seq = timing.sequence(out)
    assert [o.op for o in seq] == ["clip", "scene"]                          # the model skipped scenes: an end card at least
    card = seq[-1]
    assert (card.text, card.fill, card.color, card.type, card.beat) == ("Book now", "#F8FAF9", "#111111", "pop", "b2")
    plan = EditPlan(ops=[Op(op="scene", text="Need a gown?", fill="#1AAA4B", duration=1.5), Op(op="clip", file="f1", start=0, end=2)])
    out = planner.enforce(plan, d, c)
    s = timing.sequence(out)[0]
    assert s.color == "#111111" if planner._light("#1AAA4B") else s.color == "#FFFFFF"  # readable on its own canvas
    assert sum(o.op == "scene" for o in out.ops) == 1                         # the model's scenes stand; no extra end card
    assert "MOTION DESIGN" in planner._edit_prompt(_spec(), dna, {}, c, AXES, d) and "#1AAA4B" in planner._edit_prompt(_spec(), dna, {}, c, AXES, d)


def test_timing_keeps_scenes_and_trims_footage():
    files = {"f1": {"kind": "video", "duration": 30.0}}
    plan = EditPlan(ops=[Op(op="scene", text="Hi", duration=2.0), Op(op="clip", file="f1", start=0, end=6),
                         Op(op="clip", file="f1", start=10, end=16), Op(op="scene", text="Bye", duration=9),
                         Op(op="transition", type="fade", into=2, duration=0.3)])
    plan, notes = timing.resolve(plan, files, target_s=10)
    seq = timing.sequence(plan)
    # 20 s planned for 10: the second clip goes, the first is shortened; both scenes stay (the end card clamped to 6 s)
    assert [o.op for o in seq] == ["scene", "clip", "scene"] and seq[-1].duration == 6.0
    assert abs(sum(timing._clip_len(o) for o in seq) - 10) <= 1.0
    assert any(o.op == "transition" and o.into == 2 for o in plan.ops)  # into the end card: indexes count scenes


def test_patch_moves_clips_around_scenes():
    log = {"f1": {"kind": "video", "segments": [{"id": "f1.s1", "from": 0, "to": 10}]}}
    a, b = Op(op="clip", file="f1", segment="f1.s1", start=0, end=1), Op(op="clip", file="f1", segment="f1.s1", start=2, end=3)
    plan = EditPlan(ops=[Op(op="scene", text="Hi", duration=1.5), a, b, Op(op="scene", text="Bye", duration=2)])
    assert "S2: scene #4 of the sequence" in planner.plan_listing(plan)
    new, problems = planner.apply_patch(plan, PlanPatch(ops=[PatchOp(op="move_clip", target="C2", to=1),
                                                             PatchOp(op="edit_scene", target="S2", text="Book now", duration=2.5)]), log)
    seq = timing.sequence(new)
    assert not problems and [o.op for o in seq] == ["scene", "clip", "clip", "scene"]
    assert (seq[1].start, seq[2].start) == (2, 0) and (seq[3].text, seq[3].duration) == ("Book now", 2.5)


@needs_ffmpeg
def test_executor_builds_scenes_on_the_video_track(test_clip, tmp_path):
    from vedit import media
    from vedit.ops import color
    from vedit.project import load
    from vedit.sound import check_sound

    info = media.probe(test_clip)
    files = {"f1": {"path": str(test_clip), "kind": "video", "duration": info["duration"], "width": info.get("width"),
                    "height": info.get("height"), "fps": info.get("fps"), "has_audio": True}}
    plan = EditPlan(beats=[{"id": "b1", "role": "hook"}, {"id": "b2", "role": "demo"}, {"id": "b3", "role": "cta"}], ops=[
        Op(op="scene", text="Need a gown?", fill="#F8FAF9", color="#111111", type="pop", duration=1.5, beat="b1", why="asks the hook"),
        Op(op="clip", file="f1", start=0, end=2, beat="b2", audio="mute"),
        Op(op="scene", fill="#1AAA4B", duration=0.6),
        Op(op="clip", file="f1", start=3, end=4.5, beat="b2", audio="mute"),
        Op(op="scene", text="Book now", fill="#F8FAF9", color="#111111", duration=2.0, beat="b3"),
        Op(op="transition", type="slideleft", into=2, duration=0.2),
        Op(op="vedit", command="sfx add whoosh --at @S2.start --volume 0.5"),
        Op(op="vedit", command="layer set @S3_text --y 40%"),
    ], grade={"look": "natural", "intensity": 0.6, "target": None, "strength": 0.6})
    pdir = tmp_path / "v01"
    rep = executor.execute(plan, pdir, files, "test", "reels")
    assert not rep["skipped"], rep["skipped"]
    p = load(pdir)
    assert [c.gap for c in p.clips] == [True, False, True, False]   # the end card's gap is dropped; its canvas holds the time
    assert 6.5 <= p.duration <= 7.7
    canvases = [l for l in p.layers if l.type == "shape" and l.width == p.canvas.width]
    assert [l.fill for l in canvases] == ["#F8FAF9", "#1AAA4B", "#F8FAF9"]
    hook = next(l for l in p.layers if l.text == "Need a gown?")
    assert hook.split == "word" and hook.split_anim == "pop" and hook.start < 0.2
    assert next(l for l in p.layers if l.text == "Book now").y == "40%"
    assert p.clips[0].meta and p.clips[0].meta.role == "hook"
    assert all(c.grade is None for c in p.clips if c.gap) and all(c.grade for c in p.clips if not c.gap)  # gaps never graded
    assert color.clip_stats(p, pdir, p.clips[0]) is None
    assert p.clips[2].transition and p.clips[2].transition.type == "slideleft"   # into the colour wipe
    msgs = [f["message"] for f in check_sound(p, pdir)]
    assert not any("runs out" in m for m in msgs)                                  # a whoosh is not music
    assert any(f["severity"] == "must-fix" and "silent" in f["message"] for f in check_sound(p, pdir))

"""Reference recipe: the Style DNA turned into vedit commands that rebuild the edit."""

import shlex

import pytest
import typer

from vedit.cli import app
from vedit.references import recipe
from vedit.references.dna import StyleDNA, merge

FILL = {"<clip>": "c1", "<song>": "song.mp3", "<HOOK>": "Hook", "<WORD>": "WOW", "<T>": "1", "<A>": "0", "<B>": "2",
        "<S>": "0", "<E>": "1", "<beat time>": "1.5", "<text layer id>": "t1", "<ref id>": "1"}


def _fill(cmd: str) -> list[str]:
    for k, v in FILL.items():
        cmd = cmd.replace(k, v)
    assert "<" not in cmd, cmd
    return shlex.split(cmd)[1:]  # drop "vedit"


def _parses(args: list[str]) -> None:
    """Resolve the sub-command and parse its options with the real CLI, without running it."""
    cmd = typer.main.get_command(app)
    ctx = cmd.make_context("vedit", list(args))
    depth = 0
    while hasattr(cmd, "resolve_command"):  # a group (typer's own Group class isn't a click.Group subclass)
        name, sub, rest = cmd.resolve_command(ctx, list(getattr(ctx, "_protected_args", [])) + list(ctx.args))
        assert sub is not None, f"unknown command {name}"
        cmd, ctx = sub, sub.make_context(name, rest, parent=ctx)
        depth += 1
    assert depth >= 1


RICH = StyleDNA(
    pacing={"avg_shot_s": 0.9, "cuts_per_10s": 11, "speed_ramps": True},
    hook={"duration_s": 2.5, "pattern": "question", "first_cut_s": 0.8},
    text={"present": True, "position_y": "22%", "size": "huge", "uppercase": True, "color": "#FFE600", "stroke": True,
          "bg_box": True, "font": "anton", "letter_spacing": 8, "animation_in": ["pop_in"], "animation_out": ["fade_out"],
          "on_beat": True},
    captions={"present": True, "style": "karaoke", "words_at_once": 2, "y": "70%"},
    motion={"jumpcut_zoom": 1.15, "ken_burns": True, "zoom_punch_on_beat": True, "shake": True},
    transitions={"default": "hblur", "accents": ["zoomin"], "frequency": "often", "duration_s": 0.25},
    sound={"bpm": 120, "cut_on_beat": True, "voiceover": True, "music": True, "music_energy": "high"},
    color={"look": "teal-orange", "intensity": 0.7},
    signature_moves=[{"move": "keyword_pop", "at": 3.1, "note": "a giant word slams in"}, {"move": "whip"}],
    gaps=[{"what": "3D text flip", "closest": "text.animation_in = drop_in"}],
)


@pytest.mark.parametrize("dna", [StyleDNA(), RICH], ids=["defaults", "rich"])
def test_every_recipe_command_is_a_real_vedit_command(dna):
    steps = recipe.build(dna, {"width": 1080, "height": 1920}, ref_id=4)
    assert steps and steps[-1]["command"] == "vedit audit"
    for s in steps:
        assert s["command"].startswith("vedit ") and s["why"]
        _parses(_fill(s["command"]))


def test_recipe_follows_the_dna():
    steps = recipe.build(RICH, {"width": 1080, "height": 1920}, ref_id=4)
    cmds = [s["command"] for s in steps]
    joined = "\n".join(cmds)
    assert cmds[0] == "vedit canvas --preset reels"
    assert "vedit color match --ref 4 --strength 0.8" in cmds
    assert "vedit color look teal-orange --intensity 0.7" in cmds
    assert "--font anton" in joined and "--uppercase" in joined and "--size 118" in joined
    assert "vedit jumpcut-zoom --zoom 1.15" in cmds
    assert "vedit fx apply keyword_pop --at 3.1" in joined
    assert "vedit transition hblur --duration 0.25" in cmds
    assert "--duck" in joined and "vedit sound voice clean" in cmds
    # captions come after every cut / speed / move, right before the audit
    assert cmds[-2].startswith("vedit captions auto --style karaoke")
    assert recipe.gaps(RICH) == [{"what": "3D text flip", "closest": "text.animation_in = drop_in"}]
    assert "3D text flip" in recipe.as_text(steps, recipe.gaps(RICH))


def test_quiet_reference_gets_a_short_recipe():
    dna = StyleDNA(text={"present": False}, sound={"music": False}, color={"look": "natural"})
    cmds = [s["command"] for s in recipe.build(dna, {"width": 1920, "height": 1080}, ref_id=1)]
    assert cmds[0] == "vedit canvas --preset landscape"
    assert not any(c.startswith(("vedit text add", "vedit music add", "vedit color look", "vedit captions")) for c in cmds)


def test_gaps_survive_merge():
    assert merge([RICH, StyleDNA()], lead=0).gaps[0].what == "3D text flip"

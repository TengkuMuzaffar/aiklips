"""The AI edits only through the `vedit` CLI, so every editor action (timeline drag, inspector field) needs a CLI
command too. This fails when a new `@op` in editor/actions.py has no CLI equivalent listed here: add the command (or,
if it really is UI-only, add it to UI_ONLY with the reason), and document it in CLAUDE.md."""

import json

from typer.main import get_command

from vedit.cli import app
from vedit.editor import actions
from vedit.project import load

# editor action -> the CLI command the AI uses for the same edit
CLI_FOR_OP = {
    "move_clip": "move", "retime_clip": "trim", "clip_set": "speed", "clip_why": "why",
    "color_look": "color look", "color_match": "color match", "color_set": "color set", "color_clear": "color clear",
    "transition_set": "transition", "bg_remove": "bg remove", "bg_set": "bg tune", "mask_add": "mask add", "mask_set": "mask set", "focus_add": "focus add", "focus_set": "focus set",
    "remove_clip": "remove-clip", "remove_effect": "fx remove",
    "fx_add": "fx add", "fx_set": "fx set", "fx_reset": "fx set", "fx_remove": "fx remove", "fx_replace": "fx replace",
    "fx_move": "fx move", "fx_copy": "fx copy", "move_apply": "fx apply", "track_visible": "track hide", "track_video": "track video", "motion_set": "motion set", "group_make": "group make", "group_set": "group set", "group_move": "group move", "group_remove": "group remove", "group_ungroup": "group ungroup", "chart_add": "chart add", "counter_add": "counter add", "platform_set": "platform", "safe_fix": "safe fix",
    "marker_add": "marker add", "marker_set": "marker set", "marker_remove": "marker remove",
    "keyframe_set": "animate keyframes", "keyframe_remove": "animate keyframes", "keyframes_clear": "animate clear",
    "trim_to": "trim-to", "paste_attrs": "paste", "split": "split", "delete_range": "delete", "insert_source": "import",
    "add_music": "music add", "music_set": "music set", "music_remove": "music remove",
    "layer_set": "layer set", "text_look": "text look apply", "layer_add_text": "text add", "layer_add_video": "overlay add",
    "layer_add_adjust": "adjust add", "layer_grade": "color set", "template_apply": "template apply",
    "layer_add_shape": "shape add", "overlay_add": "overlay add", "layer_place": "place", "layer_add_media": "overlay add",
    "clip_place": "place", "clip_to_overlay": "to-overlay", "overlay_to_clip": "to-clip", "layer_move": "layer order",
    "layer_remove": "layer remove", "preset_add": "animate preset", "preset_remove": "animate preset",
    "canvas_set": "canvas",
    "scene_add": "scene add", "scene_set": "scene set", "scene_move": "scene move", "scene_dup": "scene dup",
    "scene_remove": "scene remove", "layer_dup": "layer dup",
}
UI_ONLY = {
    "batch": "groups other editor actions (each has its own CLI command) into one undo step",
    "rename": "project name in the home screen; not an edit the AI is asked for",
}


def _cli_commands() -> set[str]:
    root = get_command(app)
    out: set[str] = set()

    def walk(cmd, prefix):
        for name, sub in getattr(cmd, "commands", {}).items():
            full = f"{prefix} {name}".strip()
            out.add(full)
            walk(sub, full)

    walk(root, "")
    return out


def test_every_editor_action_has_a_cli_command():
    cmds = _cli_commands()
    missing = sorted(k for k in actions._OPS if k not in CLI_FOR_OP and k not in UI_ONLY)
    assert not missing, f"editor actions with no CLI path for the AI: {missing}"
    broken = {k: v for k, v in CLI_FOR_OP.items() if v not in cmds}
    assert not broken, f"mapped to CLI commands that don't exist: {broken}"
    stale = sorted(k for k in CLI_FOR_OP if k not in actions._OPS)
    assert not stale, f"no longer editor actions, drop them here: {stale}"


def test_claude_md_documents_every_top_level_command():
    from pathlib import Path

    text = (Path(__file__).parents[1] / "CLAUDE.md").read_text()
    sheet = text[text.index("## Command cheat-sheet"):text.index("**References / Generate:**")]
    skip = {"app", "use", "ref", "gen", "sheet", "frame", "preview", "serve", "history", "checkout", "redo", "undo",
            "projects", "init", "info", "import"}  # in the workflow section / obvious
    top = {c.split()[0] for c in _cli_commands()} - skip
    missing = sorted(c for c in top if c not in sheet)
    assert not missing, f"commands missing from the CLAUDE.md cheat-sheet: {missing}"


# ---- the new commands, end to end ------------------------------------------------------------------------------

def _two_clips(cli, test_clip):
    cli("import", str(test_clip))
    cli("split", "--at", "3")
    return load(cli.project)


def test_place_clip_leaves_gap_and_info_shows_it(cli, test_clip):
    _two_clips(cli, test_clip)
    cli("place", "c2", "--at", "5")
    info = cli("info")
    assert [c.get("gap", False) for c in info["clips"]] == [False, True, False]
    assert info["clips"][2]["timeline"][0] == 5.0


def test_to_overlay_and_back(cli, test_clip):
    _two_clips(cli, test_clip)
    cli("fx", "add", "blur", "--clip", "c2")
    r = cli("to-overlay", "c2", "--at", "0.5")
    p = load(cli.project)
    l = p.layer(r["layer"])
    assert [c.id for c in p.clips] == ["c1"] and l.type == "video" and l.start == 0.5
    assert [e.params["key"] for e in l.effects] == ["blur"]
    info = cli("info")
    row = next(x for x in info["layers"] if x["id"] == l.id)
    assert row["track"] == 0 and row["fit"] == "free" and row["effects"][0].startswith("blur")
    r = cli("to-clip", l.id)
    p = load(cli.project)
    assert len(p.clips) == 2 and not p.layers and p.clip(r["clip"]).effects[0].params["key"] == "blur"


def test_place_layer_on_track_and_hide_track(cli, test_clip):
    cli("import", str(test_clip))
    a = cli("text", "add", "A", "--start", "0", "--end", "2")["layer"]
    b = cli("text", "add", "B", "--start", "3", "--end", "5")["layer"]
    p = load(cli.project)
    assert p.layer(a).track == p.layer(b).track == 0
    cli("place", b, "--track", "0", "--new-track")
    p = load(cli.project)
    assert p.layer(b).track == 0 and p.layer(a).track == 1
    cli("place", b, "--at", "1", "--end", "4")
    assert (load(cli.project).layer(b).start, load(cli.project).layer(b).end) == (1.0, 4.0)
    cli("track", "hide", "1")
    assert load(cli.project).layer(a).hidden
    cli("track", "hide", "main")
    assert load(cli.project).main_hidden and cli("info")["main_hidden"]
    rows = cli("track", "list")["tracks"]
    assert rows[0]["track"] == "video" and rows[0]["hidden"] and rows[2]["hidden"]  # video track at the bottom
    assert cli("track", "video", "up")["height"] == 1
    assert [r["track"] for r in cli("track", "list")["tracks"]] == [0, "video", 1]
    cli("track", "video", "bottom")
    cli("track", "show", "main")
    cli("track", "show", "1")
    p = load(cli.project)
    assert not p.main_hidden and not p.layer(a).hidden
    cli("track", "hide", "7", ok=False)


def test_overlay_fit_flip_and_colour(cli, test_clip):
    cli("import", str(test_clip))
    lid = cli("overlay", "add", str(test_clip), "--end", "3")["layer"]
    cli("layer", "style", lid, "--fit", "cover", "--flip-h")
    l = load(cli.project).layer(lid)
    assert (l.fit, l.flip_h, l.flip_v, l.x) == ("cover", True, False, "50%")
    cli("layer", "style", lid, "--fit", "sideways", ok=False)
    cli("color", "set", "--layer", lid, "--saturation", "0")
    cli("color", "look", "warm-film", "--layer", lid, "--intensity", "0.5")
    g = load(cli.project).layer(lid).grade
    assert (g.saturation, g.look, g.intensity) == (0.0, "warm-film", 0.5)
    assert load(cli.project).clips[0].grade is None  # clips untouched
    cli("color", "clear", "--layer", lid)
    assert load(cli.project).layer(lid).grade is None
    t = cli("text", "add", "hi")["layer"]
    cli("color", "set", "--layer", t, "--saturation", "0", ok=False)


def test_timed_fx_and_replace(cli, test_clip):
    cli("import", str(test_clip))
    fid = cli("fx", "add", "blur", "--clip", "c1", "--start", "1", "--end", "2.5", "--fade-in", "0.3")["added"][0]
    pr = load(cli.project).clips[0].effects[0].params
    assert (pr["start"], pr["end"], pr["fade_in"]) == (1.0, 2.5, 0.3)
    assert "1-2.5s fade 0.3/0" in cli("info")["clips"][0]["effects"][0]
    cli("fx", "set", fid, "end=none")
    assert load(cli.project).clips[0].effects[0].params["end"] is None
    cli("fx", "replace", fid, "glow")
    pr = load(cli.project).clips[0].effects[0].params
    assert (pr["key"], pr["id"], pr["start"]) == ("glow", fid, 1.0)


def test_music_trim(cli, test_clip):
    cli("import", str(test_clip))
    mid = cli("music", "add", str(test_clip))["music"]
    cli("music", "set", mid, "--offset", "1.5", "--end", "4", "--no-loop")
    m = load(cli.project).music[0]
    assert (m.in_, m.end, m.loop) == (1.5, 4.0, False)
    cli("music", "set", mid, "--end", "none")
    assert load(cli.project).music[0].end is None
    cli("music", "set", mid, "--start", "3", "--end", "3.05", ok=False)


def test_trim_to_and_paste(cli, test_clip):
    _two_clips(cli, test_clip)
    cli("trim-to", "--at", "1", "--side", "start")
    assert load(cli.project).clips[0].in_ == 1.0
    cli("color", "look", "moody", "--clip", "c1")
    cli("paste", "--from", "c1", "--to", "c2", "--group", "colour")
    assert load(cli.project).clip("c2").grade.look == "moody"


def test_editor_context_describes_effect_and_layer_track(tmp_path, test_clip, cli):
    from vedit.editor.agent import ChatSession

    cli("import", str(test_clip))
    fid = cli("fx", "add", "blur", "--clip", "c1", "--start", "1", "--end", "2")["added"][0]
    lid = cli("text", "add", "hi")["layer"]
    s = ChatSession.__new__(ChatSession)
    s.pdir = cli.project
    ctx = s.context_block({"playhead": 1, "selection": {"kind": "fx", "id": fid}})
    assert f"effect {fid} (blur) on c1, 1-2s" in ctx
    ctx = s.context_block({"selection": {"kind": "layer", "id": lid}})
    assert "track 0" in ctx
    assert json.dumps(ctx)  # plain text


def test_editor_context_lists_attachments_and_platform(tmp_path, test_clip, cli):
    """The chat composer can attach several clips / layers / ranges / moments / references to one message."""
    from vedit.editor.agent import ChatSession

    cli("import", str(test_clip))
    cli("split", "--at", "3")
    lid = cli("text", "add", "Hook", "--end", "2")["layer"]
    cli("platform", "tiktok")
    s = ChatSession.__new__(ChatSession)
    s.pdir = cli.project
    ctx = s.context_block({"attachments": [
        {"kind": "clip", "id": "c2"}, {"kind": "layer", "id": lid}, {"kind": "range", "start": 1, "end": 2.5},
        {"kind": "time", "t": 4.2}, {"kind": "ref", "id": 6, "name": "motion design 1"}, {"kind": "clip", "id": "gone"}]})
    assert "attached by the user" in ctx
    assert "1. clip c2 at timeline 3.00-6.00s" in ctx and f"2. text layer {lid}" in ctx and '"Hook"' in ctx
    assert "3. timeline range 1.00-2.50s" in ctx and "4. the moment 4.20s" in ctx
    assert '5. reference video 6 "motion design 1"' in ctx and "vedit ref recipe 6" in ctx
    assert "6. clip gone (no longer in the project)" in ctx
    assert "TikTok safe area" in ctx

"""The AI editor never changes aiklips itself, whatever the user clicks: hard guardrails in editor/agent.py.

The blocked cases are what an editor chat actually did once (edits to src/ and tests/, pytest, /tmp scripts)."""

import asyncio
from pathlib import Path

import pytest

from vedit.editor import permissions
from vedit.editor.agent import REPO_ROOT, ChatSession, bash_guard, guard

SRC = str(REPO_ROOT / "src/vedit/ops/track.py")


@pytest.fixture
def pdir(tmp_path, monkeypatch):
    monkeypatch.setenv("VEDIT_HOME", str(tmp_path / "home"))
    d = tmp_path / "proj"
    (d / ".vedit").mkdir(parents=True)
    (d / "media").mkdir()
    (d / "media" / "a.mp4").write_bytes(b"x")
    return d


@pytest.mark.parametrize("cmd", [
    "uv run pytest tests/test_transitions_masks.py -q 2>&1 | tail -15",
    "uv run pytest -q -x 2>&1 | tail -8",
    "cat > /tmp/dbg_lock.py <<'EOF'\nimport sys\nEOF",
    "uv run python /tmp/dbg_flicker.py 2>&1 | tail -2",
    "uv run vedit --json info | python3 -c 'import json,sys'",
    "python3 -c 'open(\"src/vedit/cli.py\",\"w\")'",
    "/usr/bin/python3.12 x.py", "uv sync", "uv add numpy", "uv pip install x", "git checkout src", "git apply p.diff",
    "sed -i '' s/a/b/ src/vedit/cli.py", "perl -pi -e s/a/b/ x", "cp /tmp/x src/vedit/cli.py", "rm -rf src",
    "echo hi > src/vedit/x.py", "echo $(rm -rf ~)", "ls `rm x`", "(cd src && rm x)", "PYTHONPATH=src vedit info",
    "ffmpeg -y -i a.mp4 src/vedit/cli.py", "cd src/vedit && ffmpeg -y -i /tmp/a.mp4 cli.py", "mkdir src/vedit/hack",
    "uv run vedit --json export src/vedit/cli.py", "bash -c 'vedit info'", "npx something", "curl -o x https://e.x",
])
def test_blocked_commands(cmd, pdir):
    assert bash_guard(cmd, pdir), cmd


@pytest.mark.parametrize("cmd", [
    "uv run vedit --json info", "uv run vedit --json frame --at 1,2 && uv run vedit --json sheet",
    "uv run vedit --json text add 'Hi' --y 20%", "vedit --json export", "ls media", "cat src/vedit/cli.py | head -5",
    "ffprobe -v error media/a.mp4", "for t in 1 2; do uv run vedit --json frame --at $t; done",
    f"ffmpeg -i {REPO_ROOT}/projects/x/media/a.mp4 -f null /dev/null",
])
def test_editing_commands_still_pass(cmd, pdir):
    assert bash_guard(cmd, pdir) is None, cmd


def test_file_tools_only_inside_the_project(pdir):
    assert guard("Edit", {"file_path": SRC}, pdir)
    assert guard("Write", {"file_path": str(REPO_ROOT / "tests/test_x.py")}, pdir)
    assert guard("Write", {"file_path": str(Path.home() / ".claude/settings.json")}, pdir)
    assert guard("Write", {"file_path": "/tmp/dbg.py"}, pdir)
    assert guard("Write", {"file_path": str(pdir / ".vedit/history.json")}, pdir)
    assert guard("Write", {"file_path": str(pdir / "media/a.mp4")}, pdir)
    assert guard("Write", {"file_path": str(pdir / "../proj/../../etc/x")}, pdir)
    assert guard("Write", {"file_path": str(pdir / "notes.md")}, pdir) is None
    assert guard("Edit", {"file_path": str(pdir / "project.json")}, pdir) is None


def test_clicking_allow_cannot_override(pdir):
    s = ChatSession(pdir)
    events = []

    async def emit(ev, persist=True):
        events.append(ev)
        if ev["type"] == "permission":  # a user who clicks Allow on everything
            asyncio.get_running_loop().call_soon(s.answer_permission, ev["id"], True)

    s.emit = emit
    for tool, data in [("Edit", {"file_path": SRC}), ("Bash", {"command": "uv run pytest -q"}),
                       ("Bash", {"command": "cat > /tmp/x.py <<'EOF'\nx\nEOF"})]:
        r = asyncio.run(s._permission(tool, data, None))
        assert r.behavior == "deny" and "aiklips" in r.message, (tool, data)
    assert not [e for e in events if e["type"] == "permission"]  # never even offered
    assert any("guardrail" in e.get("text", "") for e in events)


def test_hook_blocks_before_claude_codes_own_allow_rules(pdir):
    s = ChatSession(pdir)

    async def emit(ev, persist=True):
        pass

    s.emit = emit
    out = asyncio.run(s._pre_tool({"tool_name": "Bash", "tool_input": {"command": "uv sync"}}, None, {}))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert asyncio.run(s._pre_tool({"tool_name": "Bash", "tool_input": {"command": "uv run vedit --json info"}}, None, {})) == {}


def test_blocked_programs_cannot_be_always_allowed(pdir):
    for item in ("python3", "pytest", "sed", "git", "Edit", "Write"):
        with pytest.raises(permissions.PermissionSettingsError):
            permissions.check_item(item)
    assert permissions.check_item("ffmpeg") == "ffmpeg"

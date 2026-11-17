"""Settings page: AI editor model default, permission declarations, usage summary."""

import asyncio
import json
import time

import pytest

from vedit.ai import client as ai
from vedit.editor import permissions


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VEDIT_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("VEDIT_AI_MODEL", raising=False)
    return tmp_path / "home"


def test_always_allow_items_are_checked():
    assert permissions.check_item(" ffmpeg ") == "ffmpeg"
    assert permissions.check_item("WebFetch") == "WebFetch"
    for bad in ("rm", "python3", "curl", "mcp__drive__read", "ffmpeg -i x", "", "a;b"):
        with pytest.raises(permissions.PermissionSettingsError):
            permissions.check_item(bad)


def test_permissions_round_trip_and_defaults(home):
    assert permissions.load() == {"always_allow": [], "unlisted": "ask"}
    permissions.save({"always_allow": ["ffmpeg", "ffmpeg", "WebFetch"], "unlisted": "deny"})
    assert permissions.load() == {"always_allow": ["WebFetch", "ffmpeg"], "unlisted": "deny"}
    (home / "permissions.json").write_text("{broken")
    assert permissions.load()["unlisted"] == "ask"
    d = permissions.declaration()
    assert d["allowed"] and d["always_ask"] and d["data"]


def test_role_save_keeps_chat_default():
    ai.save_settings({"chat": "anthropic/claude-sonnet-5-5", "editor": "anthropic/claude-haiku-5-5"})
    ai.save_settings({**ai.settings(), "editor": ""})
    assert ai.settings() == {"chat": "anthropic/claude-sonnet-5-5"}


def test_usage_summary_groups_and_skips_old(home):
    now = time.time()
    rows = [
        {"time": now - 60, "task": "chat", "model": "anthropic/claude-opus-5-5", "input_tokens": 1000, "cache_read": 500, "output_tokens": 200, "cost_usd": 0.05, "seconds": 3},
        {"time": now - 30, "task": "editor", "model": "deepseek/deepseek-chat", "input_tokens": 400, "output_tokens": 100, "cost_usd": None, "seconds": 2},
        {"time": now - 40 * 86400, "task": "chat", "model": "anthropic/claude-opus-5-5", "input_tokens": 9999, "output_tokens": 9999},
    ]
    (home / "ai-usage.jsonl").write_text("\n".join(map(json.dumps, rows)) + "\nnot json\n")
    u = ai.usage_summary(30)
    assert u["total"]["calls"] == 2 and u["total"]["input_tokens"] == 1900 and u["total"]["output_tokens"] == 300
    assert u["total"]["cost_known"] == 1 and u["total"]["cost_usd"] == 0.05
    assert {m["key"] for m in u["models"]} == {"anthropic/claude-opus-5-5", "deepseek/deepseek-chat"}
    assert {t["key"] for t in u["tasks"]} == {"chat", "editor"}


def _session(tmp_path):
    from vedit.editor.agent import ChatSession

    pdir = tmp_path / "proj"
    (pdir / ".vedit").mkdir(parents=True)
    s = ChatSession(pdir)
    events = []

    async def emit(ev, persist=True):
        events.append(ev)

    s.emit = emit
    return s, pdir, events


def test_agent_uses_global_allow_list_and_deny_mode(tmp_path):
    pytest.importorskip("claude_agent_sdk")
    from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

    s, pdir, events = _session(tmp_path)
    cmd = {"command": f"cd {pdir.as_posix()} && ffmpeg -y -i a.mp4 b.png"}

    permissions.save({"always_allow": ["ffmpeg"], "unlisted": "ask"})
    assert isinstance(asyncio.run(s._permission("Bash", cmd, None)), PermissionResultAllow)

    # not on any list + "block without asking": denied at once, no prompt shown
    permissions.save({"always_allow": [], "unlisted": "deny"})
    assert isinstance(asyncio.run(s._permission("Bash", cmd, None)), PermissionResultDeny)
    assert not any(e["type"] == "permission" for e in events)
    assert any(e["type"] == "notice" and "Blocked" in e["text"] for e in events)


def test_new_project_starts_on_the_chat_default(tmp_path):
    ai.save_settings({"chat": "anthropic/claude-haiku-5-5"})
    s, _, _ = _session(tmp_path)
    assert s.model == "anthropic/claude-haiku-5-5"


def test_settings_api(tmp_path):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    with TestClient(create_app(None, watch=False, chat=False)) as c:
        assert c.get("/settings").status_code == 200
        assert c.get("/api/settings").json() == {"project": None, "chat_default": "anthropic/claude-opus-5-5", "chat_model": None}

        r = c.post("/api/settings/chat-model", json={"model": "anthropic/claude-sonnet-5-5"})
        assert r.status_code == 200 and r.json()["chat_default"] == "anthropic/claude-sonnet-5-5"
        assert c.post("/api/settings/chat-model", json={"model": "nope/model"}).status_code == 400

        p = c.post("/api/settings/permissions", json={"add": "ffmpeg"}).json()
        assert p["always_allow"] == ["ffmpeg"] and p["project_allow"] is None and p["rules"]["allowed"]
        assert c.post("/api/settings/permissions", json={"add": "rm"}).status_code == 400
        assert c.post("/api/settings/permissions", json={"scope": "project", "add": "ffmpeg"}).status_code == 400
        assert c.post("/api/settings/permissions", json={"unlisted": "deny"}).json()["unlisted"] == "deny"
        assert c.post("/api/settings/permissions", json={"remove": "ffmpeg"}).json()["always_allow"] == []

        u = c.get("/api/settings/usage?days=7").json()
        assert u["total"]["calls"] == 0 and u["days"] == 7

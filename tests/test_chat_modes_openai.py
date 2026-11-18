"""Chat modes (auto / ask per edit / plan), the auditor picker, and OpenAI providers through the local bridge."""

import asyncio
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vedit.editor import models as registry
from vedit.editor import openai_bridge as bridge
from vedit.editor.agent import ChatSession, vedit_edits, vedit_kind


# ---- which vedit commands are edits ------------------------------------------------------------
@pytest.mark.parametrize("args,kind", [
    (["info"], "read"), (["frame", "--at", "2"], "read"), (["audit"], "read"), (["layer", "list"], "read"),
    (["text", "look", "list"], "read"), (["silence-cut", "--dry-run"], "read"), (["audit-settings"], "read"),
    (["mask"], "read"), (["fx", "filters"], "read"), (["trim", "--help"], "read"),
    (["why", "c1", "hook"], "note"),
    (["trim", "--head", "1"], "edit"), (["text", "add", "Hi"], "edit"), (["layer", "set", "t1", "--y", "30%"], "edit"),
    (["audit-settings", "--threshold", "70"], "edit"), (["silence-cut"], "edit"), (["export"], "edit"),
    (["text", "look", "apply", "bold"], "edit"),
])
def test_vedit_kind(args, kind):
    assert vedit_kind(args) == kind


def test_vedit_edits_reads_chains_and_global_options():
    assert vedit_edits("uv run vedit --json info && uv run vedit --json frame --at 1,2") == []
    assert vedit_edits("uv run vedit --json -p x trim --head 1 && uv run vedit --json info") == ["trim --head 1"]
    assert vedit_edits("vedit why c1 'proof' --role proof") == ["note:why c1 proof"]
    assert vedit_edits("ls media") == []


# ---- modes in the permission callback --------------------------------------------------------------
def _session(tmp_path, monkeypatch):
    monkeypatch.setenv("VEDIT_HOME", str(tmp_path / "home"))
    (tmp_path / "p" / ".vedit").mkdir(parents=True)
    s = ChatSession(tmp_path / "p")
    events = []

    async def emit(ev, persist=True):
        events.append(ev)

    s.emit = emit
    s._broadcast = emit
    return s, events


def _run_with_answer(s, events, coro, answer):
    """Run a permission check; answer the card it shows (if any) with `answer`."""
    async def go():
        task = asyncio.create_task(coro)
        for _ in range(200):
            await asyncio.sleep(0.005)
            if task.done():
                break
            if s.pending:
                s.answer_permission(next(iter(s.pending)), answer)
        return await task
    return asyncio.run(go())


EDIT = {"command": "uv run vedit --json text add Hello"}
LOOK = {"command": "uv run vedit --json info"}


def test_auto_mode_runs_edits_without_asking(tmp_path, monkeypatch):
    s, events = _session(tmp_path, monkeypatch)
    assert s.mode == "auto"
    r = _run_with_answer(s, events, s._permission("Bash", EDIT, None), False)
    assert r.behavior == "allow" and not [e for e in events if e["type"] == "permission"]


def test_ask_mode_asks_per_edit_and_allow_all_for_the_request(tmp_path, monkeypatch):
    s, events = _session(tmp_path, monkeypatch)
    asyncio.run(s.set_mode("ask"))
    assert ChatSession(s.pdir).mode == "ask"  # saved per project
    assert _run_with_answer(s, events, s._permission("Bash", LOOK, None), False).behavior == "allow"  # looking is free
    assert _run_with_answer(s, events, s._permission("Bash", {"command": "uv run vedit why c1 x"}, None), False).behavior == "allow"
    assert not [e for e in events if e["type"] == "permission"]
    r = _run_with_answer(s, events, s._permission("Bash", EDIT, None), False)
    assert r.behavior == "deny" and [e for e in events if e["type"] == "permission"][-1]["kind"] == "edit"
    assert _run_with_answer(s, events, s._permission("Bash", EDIT, None), True).behavior == "allow"
    assert _run_with_answer(s, events, s._permission("Bash", EDIT, None), "turn").behavior == "allow"
    n = len(events)
    assert _run_with_answer(s, events, s._permission("Bash", EDIT, None), False).behavior == "allow"  # rest of the turn
    assert not [e for e in events[n:] if e["type"] == "permission"]


def test_plan_mode_blocks_edits_and_approval_switches_mode(tmp_path, monkeypatch):
    s, events = _session(tmp_path, monkeypatch)
    asyncio.run(s.set_mode("plan"))
    assert _run_with_answer(s, events, s._permission("Bash", LOOK, None), False).behavior == "allow"
    r = _run_with_answer(s, events, s._permission("Bash", EDIT, None), True)
    assert r.behavior == "deny" and "plan" in r.message.lower()
    assert _run_with_answer(s, events, s._permission("Bash", {"command": "uv run vedit why c1 x"}, None), True).behavior == "deny"
    assert _run_with_answer(s, events, s._permission("Write", {"file_path": str(s.pdir / "x.txt")}, None), True).behavior == "deny"

    asked = []

    async def fake_ask(text, ctx):
        asked.append(text)

    s.ask = fake_ask
    asyncio.run(s._offer_plan("1. Trim the first 2s\n2. Add a hook title"))
    offer = [e for e in events if e["type"] == "plan_offer"][-1]
    asyncio.run(s.answer_plan("wrong-id", "auto"))
    assert not asked and s.mode == "plan"
    asyncio.run(s.answer_plan(offer["id"], "revise:shorter please"))
    assert "shorter please" in asked[-1] and s.mode == "plan" and s.plan_offer is None
    asyncio.run(s._offer_plan("1. Trim"))
    asyncio.run(s.answer_plan(s.plan_offer["id"], "ask"))
    assert s.mode == "ask" and asked[-1].startswith("Plan approved") and ChatSession(s.pdir).mode == "ask"
    assert {"type": "permission_done", "id": offer["id"]} in events  # the card goes away in every open pane


def test_auditor_setting_migrates_the_old_tick_box(tmp_path, monkeypatch):
    s, _ = _session(tmp_path, monkeypatch)
    assert s.auditor == "checks"  # a new project
    s.store.write_text(json.dumps({"auto_audit_ai": False}))
    assert ChatSession(s.pdir).auditor == "off"  # the user had it unticked: that meant "no auditor"
    s.store.write_text(json.dumps({"auto_audit_ai": True}))
    assert ChatSession(s.pdir).auditor == "ai"
    s.auditor, s.mode = "off", "plan"
    ctx = s.context_block({})
    assert "auditor: off" in ctx and "chat mode: plan" in ctx


# ---- OpenAI bridge: format conversion ---------------------------------------------------------------
def test_anthropic_request_becomes_openai():
    body = {"model": "gpt-x", "max_tokens": 100, "stream": True, "system": [{"type": "text", "text": "be brief"}],
            "tools": [{"name": "Bash", "description": "run", "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}}],
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [
                    {"type": "text", "text": "a.mp4"}, {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAA"}}]},
                    {"type": "text", "text": "next"}]}]}
    out = bridge.to_openai(body, "gpt-x", "https://api.openai.com/v1")
    roles = [m["role"] for m in out["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "user"]
    assert out["messages"][2]["tool_calls"][0]["function"] == {"name": "Bash", "arguments": '{"command": "ls"}'}
    assert out["messages"][3] == {"role": "tool", "tool_call_id": "t1", "content": "a.mp4"}
    assert out["messages"][4]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert out["max_completion_tokens"] == 100 and out["stream_options"]["include_usage"]
    assert out["tools"][0]["function"]["name"] == "Bash"


def test_openai_stream_becomes_anthropic_events():
    chunks = [{"choices": [{"delta": {"content": "Hel"}}]}, {"choices": [{"delta": {"content": "lo"}}]},
              {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "Bash", "arguments": '{"comm'}}]}}]},
              {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'and": "ls"}'}}]}, "finish_reason": "tool_calls"}]},
              {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 7}}]
    lines = [f"data: {json.dumps(c)}\n".encode() for c in chunks] + [b"data: [DONE]\n"]
    ev = list(bridge.stream_events(iter(lines), "gpt-x"))
    names = [n for n, _ in ev]
    assert names[0] == "message_start" and names[-2:] == ["message_delta", "message_stop"]
    text = "".join(d["delta"]["text"] for n, d in ev if n == "content_block_delta" and d["delta"]["type"] == "text_delta")
    args = "".join(d["delta"]["partial_json"] for n, d in ev if n == "content_block_delta" and d["delta"]["type"] == "input_json_delta")
    assert text == "Hello" and json.loads(args) == {"command": "ls"}
    starts = [d["content_block"] for n, d in ev if n == "content_block_start"]
    assert [b["type"] for b in starts] == ["text", "tool_use"] and starts[1]["id"] == "call_1"
    assert names.count("content_block_stop") == 2
    md = dict(ev)["message_delta"]
    assert md["delta"]["stop_reason"] == "tool_use" and md["usage"]["output_tokens"] == 7


# ---- OpenAI bridge: end to end against a fake OpenAI server ----------------------------------------
class _FakeOpenAI(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        raw = json.dumps({"data": [{"id": "gpt-5.1"}, {"id": "text-embedding-3-large"}, {"id": "whisper-1"}]}).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        _FakeOpenAI.seen.append((self.path, self.headers.get("authorization"), body))
        if body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for c in ({"choices": [{"delta": {"content": "ok"}}]}, {"choices": [{"delta": {}, "finish_reason": "stop"}]}):
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        raw = json.dumps({"id": "x", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 3, "completion_tokens": 1}}).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def fake_openai(tmp_path, monkeypatch):
    monkeypatch.setenv("VEDIT_HOME", str(tmp_path / "home"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _FakeOpenAI.seen = []
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def test_openai_provider_end_to_end(fake_openai):
    with pytest.raises(registry.RegistryError):  # Anthropic-style providers still refuse /v1
        registry.upsert_provider({"name": "X", "base_url": fake_openai, "api_key": "k"})
    pv = registry.upsert_provider({"name": "OpenAI", "id": "openai", "api": "openai", "base_url": fake_openai, "api_key": "sk-real"})
    assert pv["api"] == "openai" and registry.public()["providers"][1]["api"] == "openai"
    assert [m["id"] for m in registry.list_models("openai")] == ["gpt-5.1"]  # embeddings / speech filtered out
    registry.add_model({"provider": "openai", "id": "gpt-5.1", "vision": True})
    assert registry.test("openai/gpt-5.1").startswith("OK, gpt-5.1 replied: ok")

    env = registry.env_for("openai/gpt-5.1")
    assert env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:") and env["ANTHROPIC_BASE_URL"].endswith("/p/openai")
    assert env["ANTHROPIC_API_KEY"] == bridge.local_token() != "sk-real" and env["ANTHROPIC_MODEL"] == "gpt-5.1"

    # what Claude Code does: a streamed /v1/messages call to the bridge
    req = urllib.request.Request(env["ANTHROPIC_BASE_URL"] + "/v1/messages", method="POST",
                                 data=json.dumps({"model": "gpt-5.1", "max_tokens": 50, "stream": True,
                                                  "messages": [{"role": "user", "content": "hi"}]}).encode(),
                                 headers={"x-api-key": env["ANTHROPIC_API_KEY"], "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        sse = r.read().decode()
    assert "event: message_start" in sse and '"text": "ok"' in sse and "event: message_stop" in sse
    path, auth, body = _FakeOpenAI.seen[-1]
    assert path == "/v1/chat/completions" and auth == "Bearer sk-real" and body["model"] == "gpt-5.1"

    bad = urllib.request.Request(env["ANTHROPIC_BASE_URL"] + "/v1/messages", method="POST", data=b"{}",
                                 headers={"x-api-key": "wrong", "content-type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(bad, timeout=10)
    assert e.value.code == 401

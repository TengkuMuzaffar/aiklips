"""OpenAI (ChatGPT) models for the AI editor and Generate.

Every AI call in aiklips goes through Claude Code (Claude Agent SDK), and Claude Code only speaks the Anthropic
Messages API. A provider with `"api": "openai"` (OpenAI itself, or any OpenAI-compatible server: Groq, Ollama,
LM Studio, vLLM, Azure-style proxies...) is reached through this bridge: a tiny HTTP server on 127.0.0.1 that
takes Anthropic `/v1/messages` requests from Claude Code, sends them on as OpenAI `/chat/completions`, and turns the
answer (streamed or not, text and tool calls) back into Anthropic's format.

    base = ensure_running()                       # http://127.0.0.1:<port>
    ANTHROPIC_BASE_URL = f"{base}/p/<provider id>"  # set by models.env_for()
    ANTHROPIC_API_KEY  = local_token()            # a per-process secret, not the user's OpenAI key

The real API key is read from the model registry on each request and never leaves this process except to the
provider's own base URL.
"""

from __future__ import annotations

import json
import secrets
import threading
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

UA = "aiklips/1.0"
_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None
_token = secrets.token_urlsafe(24)


class BridgeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def local_token() -> str:
    return _token


def ensure_running() -> str:
    """Start the bridge once per process (daemon thread); returns its base URL."""
    global _server
    with _lock:
        if _server is None:
            _server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            _server.daemon_threads = True
            threading.Thread(target=_server.serve_forever, name="openai-bridge", daemon=True).start()
        return f"http://127.0.0.1:{_server.server_address[1]}"


def is_openai_host(base: str) -> bool:
    return "api.openai.com" in base or ".openai.azure.com" in base


# ---- Anthropic request -> OpenAI request ------------------------------------------------------
def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _image_part(block: dict) -> dict | None:
    src = block.get("source") or {}
    if src.get("type") == "base64":
        return {"type": "image_url", "image_url": {"url": f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"}}
    if src.get("type") == "url":
        return {"type": "image_url", "image_url": {"url": src.get("url", "")}}
    return None


def to_openai(body: dict, model: str, base: str) -> dict:
    msgs: list[dict] = []
    system = body.get("system")
    if system:
        msgs.append({"role": "system", "content": _text_of(system)})
    for m in body.get("messages", []):
        role, content = m.get("role"), m.get("content")
        if isinstance(content, str):
            msgs.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                     for b in content if b.get("type") == "tool_use"]
            out: dict = {"role": "assistant", "content": text or (None if calls else "")}
            if calls:
                out["tool_calls"] = calls
            msgs.append(out)
            continue
        # user turn: tool results become "tool" messages (they must come right after the assistant's calls)
        parts: list[dict] = []
        late_images: list[dict] = []
        for b in content:
            t = b.get("type")
            if t == "tool_result":
                inner = b.get("content")
                text = _text_of(inner) if not isinstance(inner, str) else inner
                if b.get("is_error"):
                    text = "Error: " + (text or "tool failed")
                msgs.append({"role": "tool", "tool_call_id": b.get("tool_use_id", ""), "content": text or "(no output)"})
                if isinstance(inner, list):  # OpenAI tool messages can't hold images: send them right after
                    late_images += [p for p in (_image_part(x) for x in inner if x.get("type") == "image") if p]
            elif t == "text":
                parts.append({"type": "text", "text": b.get("text", "")})
            elif t == "image":
                p = _image_part(b)
                if p:
                    parts.append(p)
        if late_images:
            parts = [{"type": "text", "text": "(images returned by the tool call above)"}] + late_images + parts
        if parts:
            msgs.append({"role": "user", "content": parts if any(p["type"] != "text" for p in parts)
                         else "\n".join(p["text"] for p in parts)})
    out = {"model": model, "messages": msgs, "stream": bool(body.get("stream"))}
    if out["stream"]:
        out["stream_options"] = {"include_usage": True}
    mt = body.get("max_tokens")
    if mt:
        out["max_completion_tokens" if is_openai_host(base) else "max_tokens"] = int(mt)
    if body.get("temperature") is not None and not is_openai_host(base):  # OpenAI reasoning models reject it
        out["temperature"] = body["temperature"]
    if body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"][:4]
    tools = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", "")[:1024] or t["name"],
                                                "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
             for t in body.get("tools") or [] if t.get("name") and not t.get("type", "").startswith(("web_search", "bash_", "text_editor", "computer"))]
    if tools:
        out["tools"] = tools
        tc = body.get("tool_choice") or {}
        kind = tc.get("type")
        if kind == "any":
            out["tool_choice"] = "required"
        elif kind == "tool":
            out["tool_choice"] = {"type": "function", "function": {"name": tc.get("name")}}
        elif kind == "none":
            out["tool_choice"] = "none"
    return out


# ---- OpenAI response -> Anthropic response -----------------------------------------------------
STOP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use", "function_call": "tool_use",
        "content_filter": "refusal"}


def _usage(u: dict | None) -> dict:
    u = u or {}
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    return {"input_tokens": max(0, (u.get("prompt_tokens") or 0) - cached), "output_tokens": u.get("completion_tokens") or 0,
            "cache_read_input_tokens": cached, "cache_creation_input_tokens": 0}


def _args(raw: str) -> dict:
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {"value": v}
    except json.JSONDecodeError:
        return {}


def from_openai(data: dict, model: str) -> dict:
    ch = (data.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content: list[dict] = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for c in msg.get("tool_calls") or []:
        fn = c.get("function") or {}
        content.append({"type": "tool_use", "id": c.get("id") or f"toolu_{uuid.uuid4().hex[:20]}", "name": fn.get("name", ""),
                        "input": _args(fn.get("arguments", ""))})
    if msg.get("refusal") and not content:
        content.append({"type": "text", "text": msg["refusal"]})
    return {"id": data.get("id") or f"msg_{uuid.uuid4().hex[:20]}", "type": "message", "role": "assistant", "model": model,
            "content": content, "stop_reason": STOP.get(ch.get("finish_reason") or "stop", "end_turn"), "stop_sequence": None,
            "usage": _usage(data.get("usage"))}


def stream_events(lines: Iterator[bytes], model: str) -> Iterator[tuple[str, dict]]:
    """OpenAI SSE chunks -> Anthropic SSE events (name, data)."""
    yield "message_start", {"type": "message_start", "message": {
        "id": f"msg_{uuid.uuid4().hex[:20]}", "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}}
    idx = -1
    open_kind: str | None = None  # "text" | "tool:<openai index>"
    tool_blocks: dict[int, int] = {}
    finish, usage = None, None

    def close():
        nonlocal open_kind
        if open_kind is not None:
            open_kind = None
            return [("content_block_stop", {"type": "content_block_stop", "index": idx})]
        return []

    for raw in lines:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if chunk.get("error"):
            err = chunk["error"]
            raise BridgeError(502, err.get("message") if isinstance(err, dict) else str(err))
        if chunk.get("usage"):
            usage = chunk["usage"]
        for ch in chunk.get("choices") or []:
            d = ch.get("delta") or {}
            if d.get("content"):
                if open_kind != "text":
                    yield from close()
                    idx += 1
                    open_kind = "text"
                    yield "content_block_start", {"type": "content_block_start", "index": idx, "content_block": {"type": "text", "text": ""}}
                yield "content_block_delta", {"type": "content_block_delta", "index": idx, "delta": {"type": "text_delta", "text": d["content"]}}
            for tc in d.get("tool_calls") or []:
                ti = int(tc.get("index", 0))
                fn = tc.get("function") or {}
                if ti not in tool_blocks:
                    yield from close()
                    idx += 1
                    tool_blocks[ti] = idx
                    open_kind = f"tool:{ti}"
                    yield "content_block_start", {"type": "content_block_start", "index": idx, "content_block": {
                        "type": "tool_use", "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:20]}", "name": fn.get("name", ""), "input": {}}}
                if fn.get("arguments") and open_kind == f"tool:{ti}":
                    yield "content_block_delta", {"type": "content_block_delta", "index": tool_blocks[ti],
                                                  "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]}}
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
    yield from close()
    u = _usage(usage)
    yield "message_delta", {"type": "message_delta", "delta": {"stop_reason": STOP.get(finish or "stop", "end_turn"), "stop_sequence": None},
                            "usage": {"output_tokens": u["output_tokens"], "input_tokens": u["input_tokens"],
                                      "cache_read_input_tokens": u["cache_read_input_tokens"]}}
    yield "message_stop", {"type": "message_stop"}


# ---- upstream ---------------------------------------------------------------------------------
def _provider(pid: str) -> dict:
    from . import models as registry

    p = registry.provider(registry.load(), pid)
    if p is None or p.get("api") != "openai":
        raise BridgeError(404, f"No OpenAI-style provider '{pid}'")
    return p


def _open(p: dict, body: dict, timeout: float = 600):
    from . import models as registry

    secret = registry.api_key(p)
    headers = {"content-type": "application/json", "accept": "text/event-stream" if body.get("stream") else "application/json",
               "user-agent": UA}
    if secret:
        headers["authorization"] = f"Bearer {secret}"
    req = urllib.request.Request(p["base_url"].rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 method="POST", headers=headers)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            err = json.loads(raw).get("error") or {}
            detail = err.get("message") if isinstance(err, dict) else str(err)
        except (json.JSONDecodeError, AttributeError):
            detail = raw[:300]
        raise BridgeError(e.code, detail or f"HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise BridgeError(502, f"Can't reach {p['base_url']}: {getattr(e, 'reason', e)}") from None


def complete(pid: str, anthropic_body: dict) -> dict:
    """One non-streamed Anthropic-format call through an OpenAI-style provider (used by Test in Settings)."""
    p = _provider(pid)
    body = to_openai({**anthropic_body, "stream": False}, anthropic_body["model"], p["base_url"])
    with _open(p, body, timeout=60) as r:
        return from_openai(json.loads(r.read() or b"{}"), anthropic_body["model"])


_ERR_TYPE = {400: "invalid_request_error", 401: "authentication_error", 403: "permission_error", 404: "not_found_error",
             413: "request_too_large", 429: "rate_limit_error", 500: "api_error", 529: "overloaded_error"}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # keep the user's terminal quiet
        pass

    def _json(self, status: int, data: dict) -> None:
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, status: int, msg: str) -> None:
        self._json(status, {"type": "error", "error": {"type": _ERR_TYPE.get(status, "api_error"), "message": msg}})

    def do_GET(self):
        self._error(404, "Only /v1/messages is served here.")

    def do_POST(self):
        auth = self.headers.get("x-api-key") or (self.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        if not secrets.compare_digest(auth or "", _token):
            return self._error(401, "Bad local bridge token.")
        parts = self.path.split("?")[0].strip("/").split("/")  # p/<pid>/v1/messages[/count_tokens]
        if len(parts) < 4 or parts[0] != "p" or parts[2:4] != ["v1", "messages"]:
            return self._error(404, f"Unknown path {self.path}")
        pid = parts[1]
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        except json.JSONDecodeError:
            return self._error(400, "Body is not JSON.")
        if parts[4:5] == ["count_tokens"]:  # rough estimate: OpenAI has no endpoint for it
            return self._json(200, {"input_tokens": max(1, len(json.dumps(body)) // 4)})
        try:
            p = _provider(pid)
            model = str(body.get("model") or "")
            req = to_openai(body, model, p["base_url"])
            if not req["stream"]:
                with _open(p, req) as r:
                    return self._json(200, from_openai(json.loads(r.read() or b"{}"), model))
            r = _open(p, req)
        except BridgeError as e:
            return self._error(e.status, str(e))
        except Exception as e:  # noqa: BLE001 - always answer Claude Code with an Anthropic-style error
            return self._error(500, f"{type(e).__name__}: {e}")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            with r:
                for name, data in stream_events(iter(r.readline, b""), model):
                    self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                    self.wfile.flush()
        except BridgeError as e:
            self._sse_error(str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            self._sse_error(f"{type(e).__name__}: {e}")

    def _sse_error(self, msg: str) -> None:
        try:
            data = {"type": "error", "error": {"type": "api_error", "message": msg}}
            self.wfile.write(f"event: error\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()
        except OSError:
            pass

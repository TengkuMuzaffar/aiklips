"""AI chat pane: a Claude Code agent (Claude Agent SDK) that edits the open project.

Every message from the user is sent with an <editor_context> block describing what
is selected in the GUI and where the playhead is, so "make this clip slower" works.
The agent may run `vedit` (plus read-only tools) freely; anything else pauses and asks the
user to Allow or Deny it in the chat pane.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from ..project import load, state_dir
from . import models as registry

REPO_ROOT = Path(__file__).resolve().parents[3]

NO_VISION_NOTE = """
The current model cannot see images. Do NOT Read .jpg/.png files (frame, sheet) — they
will fail. Verify edits from `uv run vedit --json info` output instead, and tell the user
to check the preview pane for anything visual.
""".strip()

SYSTEM_APPEND = """
You are running inside the claude-cut desktop editor. The user sees four panes:
an inspector (top-left), the video preview (top-right), this chat (bottom-left) and a
drag-and-drop timeline (bottom-right). The preview and timeline refresh automatically
after every vedit command, so never run `vedit serve`, `vedit app` or `--open`.

The active project is already selected for you (VEDIT_PROJECT), so run plain
`uv run vedit --json <command>` without --project.

Each user message starts with an <editor_context> block: the playhead time and the
item the user has selected in the GUI. Words like "this", "here", "this clip" or
"now" refer to that selection / playhead. The user can also edit by hand in the GUI,
so always run `uv run vedit --json info` before acting on stale assumptions.

Keep replies short: what you changed, in timeline seconds. To check visual results,
run `vedit frame --at T` (several at once: `--at 72,75.5,78`) or `vedit sheet` and read
the JPGs it returns. Don't write shell loops or scripts; one vedit command per step.
""".strip()

_ALLOWED_TOOLS = {"Read", "Glob", "Grep", "Skill", "TodoWrite", "ToolSearch"}
_CMD_OK = re.compile(r"^\s*(uv\s+run\s+(-q\s+)?(--project\s+\S+\s+)?vedit\b|vedit\b|ffprobe\b|ls\b|cat\s+\S*project\.json)")


_FOR_LOOP = re.compile(r"^\s*for\s+(\w+)\s+in\s+([\w.,:\s-]+?)\s*;\s*do\s+(.+?)\s*;?\s*done\s*$", re.S)


def _bash_allowed(cmd: str) -> bool:
    """Only vedit (and a few read-only helpers), optionally chained or in a simple for-loop."""
    if "`" in cmd or "$(" in cmd or ">" in cmd or "<" in cmd:
        return False
    m = _FOR_LOOP.match(cmd)
    if m:  # e.g. for t in 72 75 78; do uv run vedit frame --at $t; done
        var, body = m.group(1), m.group(3)
        if re.search(r"\$(?!\{?" + var + r"\b)", body):  # only the loop variable may be expanded
            return False
        cmd = body
    elif "$" in cmd:
        return False
    parts = [s for s in re.split(r"&&|\|\||;|\n|\|", cmd) if s.strip()]
    return bool(parts) and all(_CMD_OK.match(s) for s in parts)


def _short_cmd(cmd: str) -> str:
    cmd = re.sub(r"uv\s+run\s+(-q\s+)?(--project\s+\S+\s+)?", "", cmd.strip())
    return cmd.replace("vedit --json ", "vedit ")


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


class ChatSession:
    def __init__(self, pdir: Path):
        self.pdir = pdir
        self.sockets: set[WebSocket] = set()
        self.client = None
        self.busy = False
        self.lock = asyncio.Lock()
        self.store = state_dir(pdir) / "chat.json"
        data = {}
        if self.store.exists():
            try:
                data = json.loads(self.store.read_text())
            except json.JSONDecodeError:
                data = {}
        self.log: list[dict] = data.get("log", [])[-300:]
        self.session_id: str | None = data.get("session_id")
        self.model: str = registry.normalize_key(data.get("model"))
        try:
            registry.resolve(self.model)
        except registry.RegistryError:
            self.model = registry.DEFAULT_KEY
        self.pending: dict[str, tuple[asyncio.Future, dict]] = {}  # permission requests awaiting the user

    # ---- plumbing --------------------------------------------------------------
    def _save(self) -> None:
        self.store.write_text(json.dumps({"session_id": self.session_id, "model": self.model, "log": self.log[-300:]}))

    async def emit(self, ev: dict, persist: bool = True) -> None:
        ev.setdefault("time", time.time())
        if persist:
            self.log.append(ev)
            self._save()
        dead = []
        for ws in list(self.sockets):
            try:
                await ws.send_json(ev)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.sockets.discard(ws)

    async def attach(self, ws: WebSocket) -> None:
        self.sockets.add(ws)
        await ws.send_json({"type": "history", "log": self.log, "busy": self.busy, "model": self.model, "models": registry.public()})
        for _, ev in self.pending.values():  # re-show prompts still waiting for an answer
            await ws.send_json(ev)

    async def _permission(self, tool: str, data: dict, ctx: Any):
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

        if tool in _ALLOWED_TOOLS:
            return PermissionResultAllow()
        if tool == "Bash" and _bash_allowed(str(data.get("command", ""))):
            return PermissionResultAllow()
        if tool in ("Edit", "Write") and Path(str(data.get("file_path", ""))).resolve() == (self.pdir / "project.json").resolve():
            return PermissionResultAllow()
        if tool == "Bash":
            detail = str(data.get("command", ""))
        elif "file_path" in data:
            detail = str(data["file_path"])
        else:
            detail = json.dumps(data, default=str)
        rid = f"perm-{time.time_ns()}"
        ev = {"type": "permission", "id": rid, "tool": tool, "detail": detail[:2000], "time": time.time()}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[rid] = (fut, ev)
        await self.emit(ev, persist=False)
        try:
            allow = await asyncio.wait_for(fut, timeout=600)
        except asyncio.TimeoutError:
            allow = False
        finally:
            self.pending.pop(rid, None)
        await self.emit({"type": "notice", "text": f"{'Allowed' if allow else 'Denied'} {tool}: {detail[:120]}"})
        await self.emit({"type": "permission_done", "id": rid}, persist=False)
        if allow:
            return PermissionResultAllow()
        return PermissionResultDeny(message="The user denied this. Prefer a `uv run vedit ...` command, "
                                            "or ask the user how they want to proceed.")

    def answer_permission(self, rid: str, allow: bool) -> None:
        item = self.pending.get(rid)
        if item and not item[0].done():
            item[0].set_result(bool(allow))

    def _deny_pending(self) -> None:
        for fut, _ in list(self.pending.values()):
            if not fut.done():
                fut.set_result(False)

    def _cli_log(self, line: str) -> None:
        """Claude Code's stderr (warnings like an unrecognized third-party model name) → .vedit/agent.log."""
        line = line.rstrip()
        if not line:
            return
        try:
            log = state_dir(self.pdir) / "agent.log"
            if log.exists() and log.stat().st_size > 2_000_000:
                log.replace(log.with_suffix(".log.1"))
            with log.open("a") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
        except OSError:
            pass

    async def _connect(self, resume: bool) -> None:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        root = REPO_ROOT if (REPO_ROOT / "CLAUDE.md").exists() else self.pdir
        provider, model = registry.resolve(self.model)
        env = {
            "VEDIT_PROJECT": str(self.pdir),
            # The editor agent only needs vedit; don't load the user's claude.ai connectors
            # (Canva, Drive, ...) into it. Also avoids the "connectors are disabled" notice
            # when another provider's key is in use.
            "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
            **registry.env_for(self.model),
        }
        append = SYSTEM_APPEND + ("" if model.get("vision") else "\n\n" + NO_VISION_NOTE)
        self.provider = provider["id"]
        opts = ClaudeAgentOptions(
            cwd=str(root),
            model=model["id"],
            system_prompt={"type": "preset", "preset": "claude_code", "append": append},
            setting_sources=["project"],
            can_use_tool=self._permission,
            env=env,
            resume=self.session_id if resume else None,
            max_buffer_size=32 * 1024 * 1024,  # tool results can carry images (frames Claude looks at)
            stderr=self._cli_log,  # keep Claude Code's diagnostics out of the user's terminal
        )
        self.client = ClaudeSDKClient(options=opts)
        await self.client.connect()

    async def _ensure(self) -> None:
        if self.client is not None:
            return
        if self.session_id:
            try:
                await self._connect(resume=True)
                return
            except Exception:
                self.session_id = None
        await self._connect(resume=False)

    # ---- conversation ------------------------------------------------------------
    def context_block(self, ctx: dict) -> str:
        lines = [f"project: {self.pdir}"]
        try:
            p = load(self.pdir)
            lines.append(f"timeline: {p.duration:.2f}s, {len(p.clips)} clips, {len(p.layers)} layers, canvas {p.canvas.width}x{p.canvas.height}")
            if ctx.get("playhead") is not None:
                lines.append(f"playhead: {float(ctx['playhead']):.2f}s")
            sel = ctx.get("selection") or {}
            kind, sid = sel.get("kind"), sel.get("id")
            if kind == "clip":
                starts = dict(zip([c.id for c in p.clips], p.clip_starts()))
                c = p.clip(sid)
                lines.append(f"selected: clip {c.id} at timeline {starts[c.id]:.2f}-{starts[c.id] + c.duration:.2f}s "
                             f"(source {c.source} {c.in_:.2f}-{c.out:.2f}s, speed {c.speed:g}, effects: {', '.join(e.type for e in c.effects) or 'none'})")
            elif kind == "layer":
                l = p.layer(sid)
                label = l.text or l.image or " ".join(w.text for w in l.words[:8])
                lines.append(f"selected: {l.type} layer {l.id} {l.start:.2f}-{(l.end if l.end is not None else p.duration):.2f}s \"{label[:60]}\"")
            elif kind == "music":
                lines.append(f"selected: music track {sid}")
            elif kind == "source":
                s = p.sources[sid]
                lines.append(f"selected: media source {sid} ({Path(s.path).name}, {s.duration:.2f}s, not necessarily on the timeline)")
            else:
                lines.append("selected: nothing")
        except Exception as e:  # never block a message because context failed
            lines.append(f"(context unavailable: {e})")
        return "<editor_context>\n" + "\n".join(lines) + "\n</editor_context>"

    async def ask(self, text: str, ctx: dict) -> None:
        if self.lock.locked():
            await self.emit({"type": "notice", "text": "Still working on the previous request. Press Stop to interrupt."}, persist=False)
            return
        async with self.lock:
            self.busy = True
            await self.emit({"type": "user", "text": text, "context": ctx.get("label")})
            await self.emit({"type": "status", "busy": True}, persist=False)
            try:
                await self._ensure()
                await self.client.query(self.context_block(ctx) + "\n\n" + text)
                await self._pump()
            except Exception as e:
                msg = str(e) or type(e).__name__
                if getattr(self, "provider", registry.BUILTIN) != registry.BUILTIN or isinstance(e, registry.RegistryError):
                    if not isinstance(e, registry.RegistryError):
                        msg += "  (Check this provider's base URL, API key and model id in Manage models → Test.)"
                elif "auth" in msg.lower() or "login" in msg.lower():
                    msg += "  (Sign in once by running `claude` in a terminal, then try again.)"
                await self.emit({"type": "error", "text": msg})
                await self._reset()
            finally:
                self.busy = False
                await self.emit({"type": "status", "busy": False}, persist=False)

    async def _pump(self) -> None:
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
        from claude_agent_sdk.types import ToolResultBlock, ToolUseBlock, UserMessage

        async for msg in self.client.receive_response():
            if isinstance(msg, AssistantMessage):
                if msg.error:
                    await self.emit({"type": "error", "text": f"Claude error: {msg.error}"})
                for b in msg.content:
                    if isinstance(b, TextBlock) and b.text.strip():
                        await self.emit({"type": "assistant", "text": b.text})
                    elif isinstance(b, ToolUseBlock):
                        if b.name == "Bash":
                            summary = _short_cmd(str(b.input.get("command", "")))
                        elif b.name == "Read":
                            fp = str(b.input.get("file_path", ""))
                            summary = ("look at " if fp.lower().endswith((".png", ".jpg")) else "read ") + Path(fp).name
                        elif b.name == "Skill":
                            summary = f"load skill {b.input.get('skill') or b.input.get('name') or ''}"
                        else:
                            summary = b.name
                        await self.emit({"type": "tool", "id": b.id, "name": b.name, "summary": summary})
            elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                for b in msg.content:
                    if isinstance(b, ToolResultBlock):
                        txt = _result_text(b.content).strip()
                        ok = not b.is_error and '"ok": false' not in txt
                        await self.emit({"type": "tool_result", "id": b.tool_use_id, "ok": ok, "text": txt[:300]})
            elif isinstance(msg, ResultMessage):
                self.session_id = msg.session_id or self.session_id
                await self.emit({"type": "done", "ok": not msg.is_error, "cost": msg.total_cost_usd if getattr(self, "provider", registry.BUILTIN) == registry.BUILTIN else None,
                                 "seconds": round((msg.duration_ms or 0) / 1000, 1),
                                 "error": None if not msg.is_error else (msg.result or msg.subtype)})

    async def set_model(self, key: str) -> None:
        try:
            old_p, old_m = registry.resolve(self.model)
            old_provider, old_vision = old_p["id"], old_m.get("vision")
        except registry.RegistryError:
            old_provider = old_vision = None
        try:
            provider, model = registry.resolve(key)
        except registry.RegistryError as e:
            await self.emit({"type": "error", "text": str(e)}, persist=False)
            return
        self.model = registry.key(model)
        self._save()
        same = provider["id"] == old_provider == registry.BUILTIN and model.get("vision") == old_vision
        if self.client is not None and same and not self.busy:
            try:
                await self.client.set_model(model["id"])
            except Exception:
                await self._reset()
        else:
            # Other providers get their own process env (base URL, key), and a fresh context:
            # a transcript from one provider isn't guaranteed to replay on another.
            await self._reset()
            if provider["id"] != old_provider:
                self.session_id = None
        note = f"Model: {model['label']} ({provider['name']})"
        if provider["id"] != old_provider:
            note += " · the AI starts a fresh context; the chat above stays for you"
        await self.emit({"type": "notice", "text": note})

    async def models_changed(self) -> None:
        """Registry was edited in Manage models: push it to every open chat pane."""
        try:
            registry.resolve(self.model)
        except registry.RegistryError:
            await self.set_model(registry.DEFAULT_KEY)
        pub = registry.public()
        for ws in list(self.sockets):
            try:
                await ws.send_json({"type": "models", "models": pub, "model": self.model})
            except Exception:
                self.sockets.discard(ws)

    async def interrupt(self) -> None:
        self._deny_pending()
        if self.client is not None and self.busy:
            try:
                await self.client.interrupt()
            except Exception:
                pass
            await self.emit({"type": "notice", "text": "Stopped."})

    async def new_conversation(self) -> None:
        await self._reset()
        self.session_id = None
        self.log = []
        self._save()
        await self.emit({"type": "history", "log": [], "busy": False, "model": self.model, "models": registry.public()}, persist=False)

    async def _reset(self) -> None:
        self._deny_pending()
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None

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
import shlex
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
to check the preview pane for anything visual. To mask or focus on something you can't see,
use `--object NAME` (person, car, bottle, cell phone, any...) or ask the user to draw the box
with the Mask / Focus buttons under the preview.
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

To hide something (label, logo, brand, plate) use `vedit mask add` (tracked; default style
`remove` rebuilds the background so it looks like the thing was never there; blur / pixelate
for privacy), never PNG / colour patches made with ffmpeg. For an exact shape, give several
boxes joined with ';'. For object focus use `vedit focus add`; for transitions
`vedit transition`. Load the `masking-focus` / `transitions` skill first.
The user can also box, brush or grid-paint a mask (Mask button under the preview) or draw a focus box.

Keep the edit consistent: one text look for all titles (`vedit text look apply <name>`, bundled
fonts via `vedit fonts`, load the `animation` skill), and for colour run `vedit color match`
then one `vedit color look <name>` on every clip (load the `color` skill). Say why you chose a look.
""".strip()

_ALLOWED_TOOLS = {"Read", "Glob", "Grep", "LS", "Skill", "TodoWrite", "ToolSearch", "BashOutput", "KillShell", "KillBash"}

# Programs that only read (or print). Anything else needs the user's OK.
_READ_ONLY = {"ls", "cat", "head", "tail", "wc", "grep", "rg", "echo", "printf", "pwd", "ffprobe", "sort", "uniq",
              "jq", "true", "file", "stat", "du", "which", "date", "basename", "dirname", "tr", "cut", "column"}
# Never offered as "always allow": these can do anything.
_NEVER_ALWAYS = {"rm", "sudo", "sh", "bash", "zsh", "fish", "curl", "wget", "python", "python3", "node", "osascript",
                 "dd", "chmod", "chown", "mv", "kill", "killall", "eval", "exec", "xargs", "env", "ssh", "scp", "open",
                 "git", "pip", "npm", "npx", "brew", "uv", "find", "sed", "awk", "perl", "ruby", "tee", "cp", "ln", "source", "."}
_SAFE_VARS = {"VEDIT_PROJECT", "HOME", "PWD"}
_VAR = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class _Unsafe(Exception):
    pass


def _scan(cmd: str) -> tuple[list[str], set[str]]:
    """Split a shell command on unquoted && || ; | newlines, respecting quotes.

    Raises _Unsafe for anything that could run hidden code or write files: command
    substitution, backticks, subshells, backgrounding, file redirection (except to
    /dev/null or 2>&1). Returns (segments, variables expanded outside single quotes).
    """
    segs, cur, used = [], [], set()
    i, n, q = 0, len(cmd), None  # q: None | "'" | '"' | "$'"
    while i < n:
        ch = cmd[i]
        if q == "'":
            cur.append(ch)
            q = None if ch == "'" else q
            i += 1
            continue
        if q == "$'":
            cur.append(ch)
            if ch == "\\" and i + 1 < n:
                cur.append(cmd[i + 1])
                i += 2
                continue
            q = None if ch == "'" else q
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            cur.append(cmd[i : i + 2])
            i += 2
            continue
        if ch == "`":
            raise _Unsafe
        if ch == "$":
            nxt = cmd[i + 1 : i + 2]
            if nxt == "'" and q is None:
                q = "$'"
                cur.append("$'")
                i += 2
                continue
            if nxt == "{":
                j = cmd.find("}", i)
                name = cmd[i + 2 : j] if j > 0 else ""
                if not _VAR.fullmatch(name):
                    raise _Unsafe
                used.add(name)
                cur.append(cmd[i : j + 1])
                i = j + 1
                continue
            m = _VAR.match(cmd, i + 1)
            if not m:
                raise _Unsafe  # $( $(( $? $$ ...
            used.add(m.group(0))
            cur.append(cmd[i : m.end()])
            i = m.end()
            continue
        if q == '"':
            cur.append(ch)
            q = None if ch == '"' else q
            i += 1
            continue
        # unquoted
        if ch in "'\"":
            q = ch
            cur.append(ch)
            i += 1
            continue
        two = cmd[i : i + 2]
        if two in ("&&", "||"):
            segs.append("".join(cur))
            cur = []
            i += 2
            continue
        if ch in ";|\n":
            segs.append("".join(cur))
            cur = []
            i += 1
            continue
        if ch in "<>" or two == "&>":
            m = re.compile(r"&?>>?\s*/dev/null|>&[12]").match(cmd, i)
            if not m:
                raise _Unsafe
            if cur and cur[-1] in "12" and (len(cur) < 2 or cur[-2].isspace()):
                cur.pop()  # the fd number of 2>&1 / 2>/dev/null
            i = m.end()
            continue
        if ch in "&(){}":
            raise _Unsafe
        cur.append(ch)
        i += 1
    if q is not None:
        raise _Unsafe
    segs.append("".join(cur))
    return [s.strip() for s in segs if s.strip()], used


def _words(seg: str) -> list[str]:
    try:
        return shlex.split(re.sub(r"\$'", "'", seg))
    except ValueError:
        return seg.split()


def _inside(path: str, roots: tuple[Path, ...]) -> bool:
    path = path.replace("${VEDIT_PROJECT}", "$VEDIT_PROJECT")
    if "$" in path and not path.startswith("$VEDIT_PROJECT"):
        return False
    if path.startswith("$VEDIT_PROJECT"):
        if len(roots) < 2:
            return False
        path = str(roots[1]) + path[len("$VEDIT_PROJECT"):]
    p = Path(path).expanduser()
    p = (p if p.is_absolute() else roots[0] / p).resolve()
    return any(p == r or r in p.parents for r in roots)


def _segment_ok(seg: str, roots: tuple[Path, ...]) -> bool:
    w = _words(seg)
    if not w:
        return True
    prog = w[0]
    if prog in ("uv", "vedit", ".venv/bin/vedit") or prog.endswith("/.venv/bin/vedit"):
        if prog == "uv":
            rest = w[1:]
            if not rest or rest[0] != "run":
                return False
            rest = rest[1:]
            while rest and rest[0] in ("-q", "--quiet", "--project", "--directory", "--frozen", "--offline"):
                if rest[0] in ("--project", "--directory"):
                    if len(rest) < 2 or not _inside(rest[1], roots):
                        return False
                    rest = rest[2:]
                else:
                    rest = rest[1:]
            return bool(rest) and rest[0] == "vedit"
        return True
    if prog == "cd":
        return len(w) <= 2 and (len(w) == 1 or _inside(w[1], roots))
    if prog == "mkdir":
        args = [a for a in w[1:] if not a.startswith("-")]
        return bool(args) and all(_inside(a, roots) for a in args)
    if prog in _READ_ONLY:
        return True
    return False


def bash_check(cmd: str, roots: tuple[Path, ...] | None = None) -> tuple[bool, set[str] | None]:
    """(allowed, programs needing approval). The set is None when the command is unsafe in itself."""
    roots = roots or (REPO_ROOT,)
    roots = tuple(Path(r).resolve() for r in roots)
    try:
        segs, used = _scan(cmd)
    except _Unsafe:
        return False, None
    loop_vars: set[str] = set()
    body: list[str] = []
    for s in segs:  # for t in 1 2 3 ; do <cmd> ; done
        m = re.match(r"^for\s+([A-Za-z_]\w*)\s+in\b", s)
        if m:
            loop_vars.add(m.group(1))
            continue
        if s == "done":
            continue
        body.append(re.sub(r"^do\s+", "", s))
    if used - _SAFE_VARS - loop_vars:
        return False, None
    if not body:
        return False, None
    need = {(_words(s) or [""])[0] for s in body if not _segment_ok(s, roots)}
    return (not need), need


def _bash_allowed(cmd: str, roots: tuple[Path, ...] | None = None) -> bool:
    """vedit and a few read-only helpers, optionally chained, piped or in a simple for-loop."""
    return bash_check(cmd, roots)[0]


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
        self.chats_dir = state_dir(pdir) / "chats"
        self.chats_dir.mkdir(exist_ok=True)
        # every editor session starts with a fresh, empty chat; earlier ones are in Chat history
        self.chat_id: str | None = None
        self.chat_created: float = time.time()
        self.log: list[dict] = []
        self.session_id: str | None = None
        self.model: str = registry.normalize_key(data.get("model"))
        try:
            registry.resolve(self.model)
        except registry.RegistryError:
            self.model = registry.DEFAULT_KEY
        self.pending: dict[str, tuple[asyncio.Future, dict]] = {}  # permission requests awaiting the user
        self.always: set[str] = set(data.get("always_allow", []))  # "Always allow" answers, per project
        if data.get("log"):  # older single-chat format: keep it as a saved chat (once)
            self._write_chat(self._new_chat_id(data["log"][0].get("time")), data["log"], data.get("session_id"))
            self._save()

    # ---- plumbing --------------------------------------------------------------
    def _save(self) -> None:
        self.store.write_text(json.dumps({"model": self.model, "always_allow": sorted(self.always)}))
        if self.log:
            if self.chat_id is None:
                self.chat_id = self._new_chat_id()
            self._write_chat(self.chat_id, self.log, self.session_id, self.chat_created)

    # ---- saved chats ---------------------------------------------------------------
    def _new_chat_id(self, t: float | None = None) -> str:
        t = t or time.time()
        cid = time.strftime("%Y%m%d-%H%M%S", time.localtime(t))
        n = 1
        while (self.chats_dir / f"{cid}.json").exists() and cid != getattr(self, "chat_id", None):
            n += 1
            cid = time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + f"-{n}"
        return cid

    def _write_chat(self, cid: str, log: list[dict], session_id: str | None, created: float | None = None) -> None:
        title = next((e.get("text", "") for e in log if e.get("type") == "user"), "") or "Untitled chat"
        data = {"id": cid, "title": " ".join(title.split())[:80], "created": created or (log[0].get("time") if log else time.time()),
                "updated": time.time(), "session_id": session_id, "model": self.model, "log": log[-300:]}
        tmp = self.chats_dir / f"{cid}.json.tmp"
        tmp.write_text(json.dumps(data))
        tmp.replace(self.chats_dir / f"{cid}.json")

    def chats(self) -> list[dict]:
        out = []
        for f in self.chats_dir.glob("*.json"):
            try:
                d = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            out.append({"id": d.get("id", f.stem), "title": d.get("title") or "Untitled chat", "updated": d.get("updated", 0),
                        "messages": sum(1 for e in d.get("log", []) if e.get("type") == "user")})
        return sorted(out, key=lambda c: c["updated"], reverse=True)[:100]

    def _history_event(self) -> dict:
        return {"type": "history", "log": self.log, "busy": self.busy, "model": self.model, "models": registry.public(),
                "chats": self.chats(), "chat_id": self.chat_id}

    async def _broadcast(self, ev: dict) -> None:
        for ws in list(self.sockets):
            try:
                await ws.send_json(ev)
            except Exception:
                self.sockets.discard(ws)

    async def _stop_and_wait(self) -> None:
        """Interrupt a running reply and let it finish writing to the chat it belongs to."""
        if self.busy:
            await self.interrupt()
        try:
            await asyncio.wait_for(self.lock.acquire(), timeout=15)
            self.lock.release()
        except asyncio.TimeoutError:
            pass

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
        await ws.send_json(self._history_event())
        for _, ev in self.pending.values():  # re-show prompts still waiting for an answer
            await ws.send_json(ev)

    def _write_ok(self, file_path: str) -> bool:
        """Edits inside the project are fine, except vedit's state and the user's original media."""
        try:
            p = Path(file_path).expanduser().resolve()
            root = self.pdir.resolve()
        except OSError:
            return False
        if not (p == root or root in p.parents):
            return False
        rel = p.relative_to(root).parts
        if rel and rel[0] == ".vedit":
            return False
        if rel and rel[0] == "media" and p.exists():
            return False
        return True

    async def _permission(self, tool: str, data: dict, ctx: Any):
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

        if tool in _ALLOWED_TOOLS:
            return PermissionResultAllow()
        always_key: str | None = None
        if tool == "Bash":
            ok, need = bash_check(str(data.get("command", "")), (REPO_ROOT, self.pdir))
            if ok:
                return PermissionResultAllow()
            if need is not None and need and not (need & _NEVER_ALWAYS):
                if need <= self.always:
                    return PermissionResultAllow()
                always_key = ",".join(sorted(need))
        elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            fp = str(data.get("file_path") or data.get("notebook_path") or "")
            if self._write_ok(fp):
                return PermissionResultAllow()
        elif tool.startswith("mcp__"):
            pass  # connectors are never auto-allowed
        else:
            if tool in self.always:
                return PermissionResultAllow()
            always_key = tool
        if tool == "Bash":
            detail = str(data.get("command", ""))
        elif "file_path" in data:
            detail = str(data["file_path"])
        else:
            detail = json.dumps(data, default=str)
        rid = f"perm-{time.time_ns()}"
        ev = {"type": "permission", "id": rid, "tool": tool, "detail": detail[:2000], "time": time.time()}
        if always_key:
            ev["always"] = always_key
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[rid] = (fut, ev)
        await self.emit(ev, persist=False)
        try:
            answer = await asyncio.wait_for(fut, timeout=600)
        except asyncio.TimeoutError:
            answer = False
        finally:
            self.pending.pop(rid, None)
        if answer == "always" and always_key:
            self.always |= set(always_key.split(","))
            self._save()
        allow = bool(answer)
        verb = "Always allowing " + always_key.replace(",", ", ") if answer == "always" and always_key else ("Allowed" if allow else "Denied") + f" {tool}"
        await self.emit({"type": "notice", "text": f"{verb}: {detail[:120]}"})
        await self.emit({"type": "permission_done", "id": rid}, persist=False)
        if allow:
            return PermissionResultAllow()
        return PermissionResultDeny(message="The user denied this. Prefer a `uv run vedit ...` command, "
                                            "or ask the user how they want to proceed.")

    def answer_permission(self, rid: str, allow: bool | str) -> None:
        item = self.pending.get(rid)
        if item and not item[0].done():
            item[0].set_result("always" if allow == "always" else bool(allow))

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
        """Start a fresh chat. The current one stays in Chat history."""
        await self._stop_and_wait()
        await self._reset()
        self.chat_id, self.chat_created, self.log, self.session_id = None, time.time(), [], None
        self._save()
        await self._broadcast(self._history_event())

    async def open_chat(self, cid: str) -> None:
        """Switch to a saved chat; the AI resumes that conversation on the next message."""
        f = self.chats_dir / f"{Path(cid).name}.json"
        if not f.exists():
            await self.emit({"type": "error", "text": "That chat no longer exists."}, persist=False)
            return
        await self._stop_and_wait()
        await self._reset()
        d = json.loads(f.read_text())
        self.chat_id, self.chat_created = d.get("id", f.stem), d.get("created", time.time())
        self.log, self.session_id = d.get("log", []), d.get("session_id")
        await self._broadcast(self._history_event())

    async def delete_chat(self, cid: str) -> None:
        f = self.chats_dir / f"{Path(cid).name}.json"
        if cid == self.chat_id:
            await self._stop_and_wait()
            await self._reset()
            self.chat_id, self.chat_created, self.log, self.session_id = None, time.time(), [], None
        f.unlink(missing_ok=True)
        await self._broadcast(self._history_event())

    async def _reset(self) -> None:
        self._deny_pending()
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None

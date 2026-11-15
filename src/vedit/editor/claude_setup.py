"""First-run setup for the AI editor: is Claude Code installed, and is the user signed in?

The Claude Agent SDK ships its own Claude Code binary, so "installed" is usually true
out of the box; a system-wide `claude` on PATH is the fallback. Sign-in state is shared
with the user's normal Claude Code (~/.claude), read via `claude auth status --json`.

Signing in is an interactive browser flow, so we open it in a terminal window and the
editor polls `status()` until it flips to logged in.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
from pathlib import Path

INSTALL_CMD = {
    "Windows": "irm https://claude.ai/install.ps1 | iex",
}.get(platform.system(), "curl -fsSL https://claude.ai/install.sh | bash")

# Only what the UI needs; account identifiers (email, org) never leave this process.
_KEEP = ("loggedIn", "authMethod", "apiProvider", "subscriptionType", "planTier", "loginExpiresAt")


def cli_path() -> tuple[str | None, str | None]:
    """The CLI the SDK will use: its bundled copy first, then `claude` on PATH."""
    try:
        import claude_agent_sdk

        name = "claude.exe" if platform.system() == "Windows" else "claude"
        bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / name
        if bundled.exists():
            return str(bundled), "bundled"
    except ImportError:
        pass
    found = shutil.which("claude")
    return (found, "system") if found else (None, None)


def status() -> dict:
    cli, source = cli_path()
    out: dict = {"installed": bool(cli), "source": source, "loggedIn": False, "install_command": INSTALL_CMD}
    if not cli:
        return out
    try:
        r = subprocess.run([cli, "auth", "status", "--json"], capture_output=True, text=True, timeout=20)
        data = json.loads(r.stdout or "{}")
        out.update({k: data[k] for k in _KEEP if k in data})
        out["loggedIn"] = bool(data.get("loggedIn"))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        out["error"] = f"Couldn't read Claude Code sign-in status: {type(e).__name__}"
    if not out["loggedIn"] and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        out.update(loggedIn=True, authMethod="api key (environment)")
    return out


def _open_terminal(command: str) -> bool:
    """Run `command` in a new visible terminal window. False if we couldn't open one."""
    system = platform.system()
    try:
        if system == "Darwin":
            esc = command.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{esc}"',
                              "-e", 'tell application "Terminal" to activate'])
            return True
        if system == "Windows":
            subprocess.Popen(["cmd", "/c", "start", "powershell", "-NoExit", "-Command", command])
            return True
        for term, args in (("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("konsole", ["-e"]), ("xterm", ["-e"])):
            if shutil.which(term):
                subprocess.Popen([term, *args, "bash", "-lc", command + "; exec bash"])
                return True
    except OSError:
        pass
    return False


def login(method: str = "claudeai") -> dict:
    cli, _ = cli_path()
    if not cli:
        return {"opened": False, "command": INSTALL_CMD, "error": "Claude Code isn't installed yet."}
    flag = "--console" if method == "console" else "--claudeai"
    command = f"{shlex.quote(cli)} auth login {flag}"
    return {"opened": _open_terminal(command), "command": command}


def install() -> dict:
    return {"opened": _open_terminal(INSTALL_CMD), "command": INSTALL_CMD}

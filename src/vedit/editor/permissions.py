"""What the AI editor may do without asking, as the user declared it in Settings → Permissions.

Three layers, checked in this order by `ChatSession._permission`:

1. Built-in rules (code, not editable): reading/searching files, `vedit` commands, read-only shell
   programs and edits inside the project (never `.vedit/` or the original media) are always allowed;
   connectors (`mcp__*`) and programs in `_NEVER_ALWAYS` (rm, curl, python…) always ask.
2. "Always allow" lists: global (here, ~/.claude-cut/permissions.json) and per project (chat.json,
   filled by the "Always allow" button in the chat). Items are program names (ffmpeg) or tool names (WebFetch).
3. Everything else: `ask` the user in the chat (default), or `deny` without asking (for unattended runs).
"""

from __future__ import annotations

import json
import re

from ..library import home_dir

UNLISTED = ("ask", "deny")
_ITEM = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]{0,63}$")


class PermissionSettingsError(ValueError):
    pass


def _path():
    return home_dir() / "permissions.json"


def load() -> dict:
    try:
        data = json.loads(_path().read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    items = [str(x) for x in data.get("always_allow", []) if isinstance(x, str)]
    unlisted = data.get("unlisted") if data.get("unlisted") in UNLISTED else "ask"
    return {"always_allow": sorted(set(items)), "unlisted": unlisted}


def save(data: dict) -> dict:
    clean = {"always_allow": sorted({check_item(x) for x in data.get("always_allow", [])}),
             "unlisted": data.get("unlisted") if data.get("unlisted") in UNLISTED else "ask"}
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(clean, indent=2))
    return clean


def check_item(item: str) -> str:
    """A program or tool name that may be always-allowed. Raises for anything that can run arbitrary code."""
    from .agent import _NEVER_ALWAYS

    item = str(item or "").strip()
    if not _ITEM.match(item):
        raise PermissionSettingsError("Give one program or tool name, like ffmpeg or WebFetch.")
    if item.startswith("mcp__"):
        raise PermissionSettingsError("Connectors always ask, so they can't be always-allowed.")
    if item in _NEVER_ALWAYS:
        raise PermissionSettingsError(f"“{item}” can do anything on your computer, so it always asks.")
    return item


def global_allow() -> set[str]:
    return set(load()["always_allow"])


def unlisted() -> str:
    return load()["unlisted"]


def declaration() -> dict:
    """The built-in rules, in words, for the Settings page."""
    from .agent import _ALLOWED_TOOLS, _NEVER_ALWAYS, _READ_ONLY

    return {
        "allowed": [
            {"title": "Run vedit commands", "detail": "Every edit is an undoable vedit command on project.json."},
            {"title": "Read and search files", "detail": ", ".join(sorted(_ALLOWED_TOOLS))},
            {"title": "Read-only shell programs", "detail": ", ".join(sorted(_READ_ONLY))},
            {"title": "Edit files inside this project", "detail": "Never the .vedit/ folder or your original media."},
        ],
        "always_ask": [
            {"title": "Connectors", "detail": "Tools from connected apps (mcp__…) are never allowed automatically."},
            {"title": "Programs that can do anything", "detail": ", ".join(sorted(_NEVER_ALWAYS - {"."}))},
            {"title": "Files outside the project", "detail": "Writing anywhere else always asks."},
        ],
        "data": [
            {"title": "What the AI sees", "detail": "Your messages, the project's clip list and timings, and still frames "
                                                    "or contact sheets rendered from your footage and references. "
                                                    "Whole video files are not uploaded."},
            {"title": "Where it goes", "detail": "Only to the provider of the model you picked (Claude by default). "
                                                 "API keys stay in ~/.claude-cut/models.json on this computer."},
        ],
    }

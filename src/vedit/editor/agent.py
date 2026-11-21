"""AI chat pane: a Claude Code agent (Claude Agent SDK) that edits the open project.

Every message from the user is sent with an <editor_context> block describing what
is selected in the GUI and where the playhead is, so "make this clip slower" works.
The agent may run `vedit` (plus read-only tools) freely; anything else pauses and asks the
user to Allow or Deny it in the chat pane.

Chat modes (picked in the chat pane, saved per project):
- auto: vedit edits run straight away (the default).
- ask:  every vedit command that changes the edit waits for the user's Allow / Deny.
- plan: the agent may look (info, frame, sheet...) but every edit is blocked; it replies with a plan and the chat
        shows Approve buttons under it. Approving switches to auto or ask and the agent carries the plan out.
Auditor (also per project): off, checks (code checks when a turn changed the edit) or ai (checks + AI reviewer).
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

from .. import history
from ..project import load, state_dir
from . import models as registry
from . import permissions

REPO_ROOT = Path(__file__).resolve().parents[3]

NO_VISION_NOTE = """
The current model cannot see images. Do NOT Read .jpg/.png files (frame, sheet) — they
will fail. Verify edits from `uv run vedit --json info` output instead, and tell the user
to check the preview pane for anything visual. To mask or focus on something you can't see,
use `--object NAME` (person, car, bottle, cell phone, any...) or ask the user to draw the box
with the Mask / Focus buttons in the timeline toolbar.
""".strip()

SYSTEM_APPEND = """
You are running inside the aiklips desktop editor. The user sees four panes:
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
for privacy), never PNG / colour patches made with ffmpeg. Masks are detailed work: load the
`masking-focus` skill and follow its steps every time: `mask find` (pick the exact outline), `mask add`,
`mask check` (motion profile + flagged frames; Read its sheet), `mask fix` at the first wrong moment,
check again. A mask is not done until its check is good and you've looked at the sheet.
For object focus use `vedit focus add`; for transitions `vedit transition` (load the `transitions` skill).
The user can also box, brush or grid-paint a mask (Mask button in the timeline toolbar) or draw a focus box.

Video edits vs designs: in a normal video edit (no DESIGN line in <editor_context>) never run `vedit design build` or
`vedit scene ...` (a design build replaces the edit's clips, layers and music). "Make it for social media / TikTok"
on a video edit means editing that video: platform, safe zone, text, captions, cuts. The Design studio (design
projects, `design build`) is in development and switched off: build motion graphics in a normal project with
`vedit style set`, `motion set|draw|morph`, `counter add`, `chart add`, shapes and text instead.

Social media layouts: when the user names an app or says "social media", set `vedit platform tiktok|reels|shorts|all`
first ("social media" alone = all). Place everything that matters inside that app's safe zone (given in
<editor_context>): hook in its upper third, captions just above its bottom edge, CTAs in its lower part away from the
right-hand buttons, logos in its corners. Don't just centre everything, and size things to the zone, not the canvas.
Measure with `vedit safe check`, repair with `vedit safe fix`, and look with `vedit frame --at T --guides`.

The timeline's tracks are equal: the video track (the clips; it can hold gaps) sits among the other tracks
(0 = bottom, higher = drawn on top) at a height you set with `track video up|down|top|bottom|N`; tracks under it show
through its gaps, letterbox bars and a keyed green screen (`bg remove --mode chroma --replace none`), e.g. a colour
screen behind green-screen footage. The timeline lasts until whatever ends last, and something must stay at 0s (a
move that leaves 0s empty is refused). Everything you can drag there you can do with vedit: `place` (move a clip to a
time / a layer to a track, `--new-track --under-video`, or onto the video track itself in a gap / after the clips
with `--video-track`), `to-overlay` / `to-clip` (move between the video track and
the others), `track list|hide|show|video`, `layer style
--fit --flip-h`, `color set|look --layer`, `music set --offset --end`, and effects on part of a clip
(`fx add ... --start --end --fade-in`, `fx set fxN start= end=`). `info` shows tracks, gaps and hidden tracks.

Keep the edit consistent: one text look for all titles (`vedit text look apply <name>`, bundled
fonts via `vedit fonts`, load the `animation` skill), and for colour run `vedit color match`
then one `vedit color look <name>` on every clip (load the `color` skill). Say why you chose a look.

You only edit videos. Never change aiklips itself: its code (src/), tests, settings, .claude/ or the Python
environment are off limits, and so are running Python, tests, git or installers. Those calls are blocked
automatically. If vedit can't do something or misbehaves, say so plainly (what you ran, what happened) so the user
can report it, and offer the closest vedit alternative.

References: when the user attaches or mentions a reference video, the goal is that the result looks like it, made by
the same designer. The Style DNA is a short summary and loses most of the design, so before planning Read the
reference's contact sheets (paths in <editor_context> or `vedit ref show <id>`: the opening at 4 fps, one frame per
shot, and the whole video) and note what a motion designer would: layout and alignment (left-aligned? margins?), the
type system (headline, subhead, tiny meta text: font, size, weight, colour), the graphic elements (frames, rules,
cards, stickers, UI mockups, mascots), colour use and how things move. Then build THAT, using the DNA's `design`
block (type_roles, elements with their closest commands, rules) as the checklist. Don't trade the reference's look
for a generic one: don't apply a `style` preset that fights it, don't drop its small details, don't centre what it
left-aligns. Where vedit can't do something exactly, build the closest version rather than leaving it out. When
done, look at your frames next to the reference sheets and fix what differs. The automatic audit scores likeness to
the attached references (`vedit audit --ref` runs that check yourself; one AI call), so a plain result won't pass.

Before you say an edit is done: record why each clip you chose is there (`vedit why <clip> "..." --role ...`),
run `vedit --json audit`, fix every must-fix finding and re-run until it passes. Report the score.
""".strip()

CHAT_MODES = ("auto", "ask", "plan")
AUDITORS = ("off", "checks", "ai")
MODE_NOTES = {
    "auto": "",
    "ask": "chat mode: ask. The user approves each vedit command that changes the edit, so group related changes "
           "and say in one line what the next command does before you run it.",
    "plan": "chat mode: plan. Don't change the edit yet (edit commands are blocked). Look first if you need to (info, "
            "frame, sheet, audit), then reply with a short numbered plan: each step's vedit command, timeline seconds "
            "and why. End your turn there; the user approves the plan with a button, then you carry it out.",
}
PLAN_BLOCK = ("Plan mode: nothing may change until the user approves a plan. Only look (info, frame, sheet, audit, list "
              "commands), then reply with the numbered plan and end your turn.")
AUDIT_NOTES = {"off": "auditor: off. The user switched the auditor off: don't run `vedit audit` or record `vedit why` "
                      "unless they ask; just make the edit and summarise it.",
               "checks": "", "ai": ""}

AUDIT_TIMEOUT = 300  # seconds: a render + an AI review can take a while

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


def _chat_default() -> str | None:
    from ..ai import client as ai

    return ai.settings().get("chat")


def _record_chat_usage(model: str, msg: Any) -> None:
    """Chat turns go in the same usage log as generation calls, so Settings → Usage shows both."""
    from ..ai import client as ai

    u = getattr(msg, "usage", None) or {}
    ai._record_usage("chat", model, {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                                     "cache_read": u.get("cache_read_input_tokens"), "cost_usd": getattr(msg, "total_cost_usd", None),
                                     "seconds": round((getattr(msg, "duration_ms", 0) or 0) / 1000, 2)})


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


# vedit commands that only look (they never change project.json). Everything else counts as an edit.
_VEDIT_READ = {"info", "frame", "sheet", "audit", "history", "projects", "fonts", "preview", "help"}
_VEDIT_READ_SUB = {"layer": {"list"}, "mask": {"list", "check", "find"}, "marker": {"list"}, "track": {"list"}, "fx": {"list", "filters"},
                   "color": {"stats", "looks"}, "sound": {"report"}, "ref": {"list", "show", "recipe"},
                   "overlay": {"library"}, "transition": {"list"}, "animate": {"list"}, "template": {"list"},
                   "version": {"list"}, "proxy": {"status"}, "gen": {"list", "tags"}, "design": {"catalog"}, "update": {"status", "check"}, "safe": {"check"}, "style": {"list", "show"}, "media": {"list", "show"},
                   "text": {"look list"}, "captions": set(), "face": set(), "bg": set(), "focus": set(), "music": set()}
_VEDIT_READ_BARE = {"audit-settings", "taste", "ai-models", "platform"}  # read-only when given no options
_VEDIT_NOTES = {"why"}  # justification notes: not an edit to the picture, so "ask" mode doesn't stop for them


def _vedit_args(w: list[str]) -> list[str] | None:
    """The words after `vedit` (global options dropped), or None when this segment isn't a vedit call."""
    if not w:
        return None
    i = 0
    if w[0] == "uv":
        i = 1
        while i < len(w) and w[i] != "vedit":
            i += 1
        if i >= len(w):
            return None
    elif not (w[0] in ("vedit", ".venv/bin/vedit") or w[0].endswith("/.venv/bin/vedit")):
        return None
    rest = w[i + 1:]
    out, j = [], 0
    while j < len(rest):
        a = rest[j]
        if not out and a in ("--project", "-p"):
            j += 2
            continue
        if not out and a in ("--json", "-q"):
            j += 1
            continue
        out.append(a)
        j += 1
    return out


def vedit_kind(args: list[str]) -> str:
    """'read', 'note' or 'edit' for one vedit invocation."""
    if not args or "--help" in args or "-h" in args:
        return "read"
    cmd, opts = args[0], [a for a in args[1:] if a != "--json"]
    if cmd in _VEDIT_READ:
        return "read"
    if cmd in _VEDIT_NOTES:
        return "note"
    if cmd == "silence-cut" and "--dry-run" in opts:
        return "read"
    if cmd in _VEDIT_READ_BARE and not opts:
        return "read"
    subs = _VEDIT_READ_SUB.get(cmd)
    if subs is not None:
        if not opts:
            return "read"  # a bare group prints its help
        if opts[0] in subs or " ".join(opts[:2]) in subs:
            return "read"
    return "edit"


def vedit_edits(cmd: str) -> list[str]:
    """Short forms of the vedit commands in `cmd` that change the edit ('note' ones included, tagged)."""
    try:
        segs, _ = _scan(cmd)
    except _Unsafe:
        return []
    out = []
    for seg in segs:
        args = _vedit_args(_words(re.sub(r"^do\s+", "", seg)))
        if args is None:
            continue
        k = vedit_kind(args)
        if k != "read":
            out.append(("note:" if k == "note" else "") + " ".join(args[:3]))
    return out


# ---- hard guardrails ------------------------------------------------------------------------------
# The AI editor edits videos, never aiklips itself. These rules can't be overridden: not by clicking Allow, not by
# an always-allow list, not by Claude Code's own settings (they run in a PreToolUse hook, before any of those).
# Blocked: writing files outside the open project; programs that run code, install things or change files; shell
# tricks (redirection into files, $(...), backticks, subshells); any other program pointed at aiklips' code.
_BLOCKED = {
    # run code
    "python", "python3", "pythonw", "py", "ipython", "pytest", "node", "deno", "bun", "ruby", "perl", "php", "lua",
    "osascript", "swift", "java", "sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh", "eval", "exec", "source", ".",
    "xargs", "env", "nohup", "timeout", "watch", "time", "nice", "sudo", "su", "doas", "script", "expect", "screen", "tmux",
    # build, install, version control
    "pip", "pip3", "pipx", "uvx", "conda", "poetry", "npm", "npx", "pnpm", "yarn", "brew", "port", "git", "gh", "make",
    "cmake", "cargo", "go", "gcc", "g++", "clang", "cc", "ld", "xcodebuild", "docker",
    # change files or the system
    "sed", "awk", "gawk", "tee", "cp", "mv", "rm", "rmdir", "ln", "patch", "touch", "truncate", "install", "rsync",
    "ditto", "dd", "chmod", "chown", "chflags", "xattr", "unzip", "tar", "gzip", "gunzip", "zip", "ed", "ex", "vi",
    "vim", "nvim", "nano", "emacs", "crontab", "launchctl", "defaults", "kill", "killall", "pkill", "open", "curl",
    "wget", "ssh", "scp", "sftp", "nc", "shred", "srm", "trash", "mkfifo", "split", "csplit",
}
_VERSIONED = re.compile(r"^(python|pip|pypy|node|ruby|perl|php|lua)[\d.]*$")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
GUARD_NOTE = ("You are the video editor here, not a developer: aiklips' own code, tests and settings are off limits. "
              "Use vedit commands only. If vedit can't do something or seems broken, tell the user what you tried and "
              "what happened so they can report it; don't try to fix or work around aiklips itself.")


def _prog(w: list[str]) -> tuple[str, list[str]]:
    """(program name, its words) with leading VAR=value assignments dropped."""
    i = 0
    while i < len(w) and _ASSIGN.match(w[i]):
        i += 1
    w = w[i:]
    return (Path(w[0]).name if w else ""), w


def _blocked_prog(name: str) -> bool:
    return name in _BLOCKED or bool(_VERSIONED.match(name))


def protected_roots() -> tuple[Path, ...]:
    """aiklips' own files: the source checkout (but not its projects/ folder), the installed package, the
    Python environment, and Claude Code's settings."""
    import sys

    import vedit

    out = [Path(vedit.__file__).resolve().parent, Path(sys.prefix).resolve(), Path.home().resolve() / ".claude"]
    if (REPO_ROOT / "pyproject.toml").exists():
        out += [p.resolve() for p in REPO_ROOT.iterdir() if p.name != "projects"]
    return tuple(dict.fromkeys(out))


def _in_protected(path: str, cwd: Path, prot: tuple[Path, ...]) -> bool:
    if not path or path.startswith("-") or "://" in path:
        return False
    try:
        p = Path(path.replace("$HOME", str(Path.home()))).expanduser()
        p = (p if p.is_absolute() else cwd / p).resolve()
    except (OSError, ValueError):
        return False
    return any(p == r or r in p.parents for r in prot)


def bash_guard(cmd: str, pdir: Path | None = None) -> str | None:
    """Why this shell command is never allowed in the AI editor, or None when it may go on to the normal checks."""
    try:
        segs, _ = _scan(cmd)
    except _Unsafe:
        return ("Shell tricks (writing into files with > or heredocs, $(...), backticks, subshells, &) aren't allowed in "
                "the AI editor. Run plain vedit commands, one per step.")
    roots = (REPO_ROOT,) + ((pdir,) if pdir else ())
    prot = protected_roots()
    cwd = REPO_ROOT  # the agent's working directory; `cd` moves it
    for seg in segs:
        if re.match(r"^for\s+[A-Za-z_]\w*\s+in\b", seg) or seg == "done":
            continue
        raw = _words(re.sub(r"^do\s+", "", seg))
        name, w = _prog(raw)
        if not name:
            continue
        if len(w) != len(raw):
            return "Setting environment variables in front of a command isn't allowed in the AI editor."
        if name == "cd":
            if len(w) > 1:
                t = Path(w[1].replace("$VEDIT_PROJECT", str(pdir or "")).replace("$HOME", str(Path.home()))).expanduser()
                cwd = (t if t.is_absolute() else cwd / t).resolve()
            continue
        is_vedit = name in ("uv", "vedit") and _segment_ok(" ".join(shlex.quote(x) for x in w), roots)
        if name == "uv" and not is_vedit:
            return "Only `uv run vedit ...` is allowed: no installing, syncing, tests or Python through uv in the AI editor."
        if is_vedit:
            args = _vedit_args(w) or []
            if vedit_kind(args) != "read" and any(_in_protected(a, cwd, prot) for a in args[1:]):
                return "vedit may not write into aiklips' own files (code, tests, settings). Use a path inside the project."
            continue
        if _blocked_prog(name):
            return (f"`{name}` can run code or change files, so it's never allowed in the AI editor. "
                    "Use a vedit command instead.")
        if name not in _READ_ONLY and any(_in_protected(a, cwd, prot) for a in w[1:]):
            return f"`{name}` may not touch aiklips' own files (code, tests, settings) from the AI editor."
    return None


def file_guard(tool: str, data: dict, pdir: Path) -> str | None:
    """Edit / Write tools: only files inside the open project, never its .vedit/ state or the original media."""
    fp = str(data.get("file_path") or data.get("notebook_path") or "")
    try:
        p = Path(fp).expanduser().resolve()
        root = pdir.resolve()
    except (OSError, ValueError):
        return "That path can't be checked, so it isn't allowed."
    if not (p == root or root in p.parents):
        return (f"The AI editor may only change files inside the open project, not {fp}. aiklips' code, tests and "
                "settings are off limits; make the change with vedit commands.")
    rel = p.relative_to(root).parts
    if rel and rel[0] == ".vedit":
        return "vedit's own state (.vedit/) is never edited by hand. Use vedit commands (undo, checkout...)."
    if rel and rel[0] == "media" and p.exists():
        return "The user's original media is never overwritten."
    return None


_FILE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def guard(tool: str, data: dict, pdir: Path) -> str | None:
    """The hard rules for one tool call: a reason to block it, or None."""
    if tool == "Bash":
        return bash_guard(str(data.get("command", "")), pdir)
    if tool in _FILE_TOOLS:
        return file_guard(tool, data, pdir)
    return None


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


def _attachment(p, n: int, a: dict, pdir: Path | None = None) -> str:
    """One line of <editor_context> for something the user attached to their message (chat composer chips)."""
    kind, aid = a.get("kind"), a.get("id")
    try:
        if kind == "clip":
            starts = dict(zip([c.id for c in p.clips], p.clip_starts()))
            c = p.clip(aid)
            return (f"{n}. clip {c.id} at timeline {starts[c.id]:.2f}-{starts[c.id] + c.duration:.2f}s "
                    f"(source {c.source} {c.in_:.2f}-{c.out:.2f}s{', gap' if c.gap else ''})")
        if kind == "layer":
            l = p.layer(aid)
            label = l.text or l.image or (l.shape if l.type == "shape" else f"{l.chart.kind} chart" if l.type == "chart" and l.chart else "")
            end = l.end if l.end is not None else p.duration
            return f"{n}. {l.type} layer {l.id} {l.start:.2f}-{end:.2f}s, track {l.track} \"{str(label)[:50]}\""
        if kind == "range":
            return f"{n}. timeline range {float(a['start']):.2f}-{float(a['end']):.2f}s"
        if kind == "time":
            return f"{n}. the moment {float(a['t']):.2f}s on the timeline"
        if kind == "marker":
            m = next((m for m in p.markers if m.id == aid), None)
            return f"{n}. marker {aid} at {m.t:.2f}s \"{m.label or ''}\"" if m else f"{n}. marker {aid}"
        if kind == "ref":
            from ..references import store

            try:
                r = store.get(int(aid))
            except Exception:
                r = {}
            if r and r.get("status") != "ready":  # just made a reference from a media video: style not studied yet
                return (f"{n}. reference video {aid} \"{a.get('name') or r.get('name') or ''}\" (@ref{aid}): its style is still being "
                        f"studied ({r.get('status')}{', ' + r['progress'] if r.get('progress') else ''}). Meanwhile "
                        + (f"`vedit media show {r['media_id']}` shows what is in it; " if r.get("media_id") else "")
                        + f"run `vedit ref show {aid}` again in a minute for its Style DNA and contact sheets.")
            try:
                sheets = store.sheet_paths(int(aid))
            except Exception:
                sheets = []
            return (f"{n}. reference video {aid} \"{a.get('name') or ''}\" (@ref{aid} in the message; a studied style, not on "
                    f"the timeline). LOOK AT IT FIRST: Read its contact sheets "
                    + (", ".join(sheets) if sheets else "(listed by `vedit ref show " + str(aid) + "`)")
                    + f". `vedit ref show {aid}` = its Style DNA (edit + `design`: layout, type system, elements, motion, "
                    f"rules); `vedit ref recipe {aid}` = commands to cut footage like it. The audit compares your edit with it.")
        if kind == "source":
            from .. import media_library

            s = p.sources[aid]
            line = f"{n}. media file {aid} ({Path(s.path).name}, {s.duration:.2f}s)"
            try:
                m = media_library.project_media(pdir, p).get(aid) if pdir else None
            except Exception:
                m = None
            if m:
                line += "; study:" + media_library.media_line("", "", m).split(":", 1)[1] + f" (`vedit media show {aid}` for every shot)"
            return line
        if kind == "media":  # a file from the media library that isn't in this project yet
            from .. import media_library

            m = media_library.get(str(aid))
            dg = m.get("digest") or {}
            return (f"{n}. library media {aid} \"{m['name']}\" ({m['kind']}, {m.get('duration') or 0:.1f}s, not in this project yet)"
                    + (f": {dg.get('summary')}" if dg.get("summary") else "")
                    + f". `vedit media show {aid}` = every shot; to use it: `vedit import {m['path']}` (or `overlay add` for b-roll).")
    except Exception:
        pass
    return f"{n}. {kind} {aid or ''} (no longer in the project)"


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
        # a project remembers its own model; a new one starts on the default chosen in Settings → Models
        self.model: str = registry.normalize_key(data.get("model") or _chat_default())
        try:
            registry.resolve(self.model)
        except registry.RegistryError:
            self.model = registry.DEFAULT_KEY
        self.pending: dict[str, tuple[asyncio.Future, dict]] = {}  # permission requests awaiting the user
        self.always: set[str] = set(data.get("always_allow", []))  # "Always allow" answers, per project
        # auditor: code checks run whenever a turn changed the edit; the AI reviewer only when switched on
        # (older chat.json files only have auto_audit_ai; unticked there meant "no auditor" to the user)
        aud = data.get("auditor")
        if aud not in AUDITORS:
            aud = ("ai" if data["auto_audit_ai"] else "off") if "auto_audit_ai" in data else "checks"
        self.auditor: str = aud
        self.mode: str = data.get("mode") if data.get("mode") in CHAT_MODES else "auto"
        self.turn_allow_edits = False  # "Allow all this turn" in ask mode
        self.plan_offer: dict | None = None  # the Approve-plan card waiting under the last plan
        self.turn_head: int | None = None
        self.audited_head: int | None = None  # history step the last audit looked at
        self.audit_rounds = 0
        if data.get("log"):  # older single-chat format: keep it as a saved chat (once)
            self._write_chat(self._new_chat_id(data["log"][0].get("time")), data["log"], data.get("session_id"))
            self._save()

    # ---- plumbing --------------------------------------------------------------
    def _save(self) -> None:
        self.store.write_text(json.dumps({"model": self.model, "always_allow": sorted(self.always), "auditor": self.auditor,
                                          "mode": self.mode}))
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
                "chats": self.chats(), "chat_id": self.chat_id, "auditor": self.auditor, "mode": self.mode}

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
        if self.plan_offer:
            await ws.send_json(self.plan_offer)

    def _write_ok(self, file_path: str) -> bool:
        return file_guard("Write", {"file_path": file_path}, self.pdir) is None

    async def _blocked(self, tool: str, data: dict, why: str) -> None:
        detail = str(data.get("command") or data.get("file_path") or data.get("notebook_path") or "")
        await self.emit({"type": "notice", "text": f"Blocked {tool} (guardrail): {_short_cmd(detail)[:120]}. {why.split('. ')[0]}."})

    async def _pre_tool(self, data: Any, tool_use_id: str | None, context: Any) -> dict:
        """PreToolUse hook: runs before Claude Code's own permission rules (project settings, modes), so the hard
        guardrails hold even where those would allow the call without asking us."""
        try:
            tool, inp = str(data.get("tool_name", "")), data.get("tool_input") or {}
            why = guard(tool, inp, self.pdir)
        except Exception as e:  # a guard that can't decide blocks
            tool, inp, why = str(data.get("tool_name", "")), {}, f"guardrail check failed ({e})"
        if not why:
            return {}
        await self._blocked(tool, inp, why)
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": why + " " + GUARD_NOTE}}

    async def _permission(self, tool: str, data: dict, ctx: Any):
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

        if tool in _ALLOWED_TOOLS:
            return PermissionResultAllow()
        why = guard(tool, data, self.pdir)
        if why:
            await self._blocked(tool, data, why)
            return PermissionResultDeny(message=why + " " + GUARD_NOTE)
        if self.mode == "plan" and tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            return PermissionResultDeny(message=PLAN_BLOCK)
        if tool == "Bash" and self.mode != "auto":
            edits = vedit_edits(str(data.get("command", "")))
            real = [e for e in edits if not e.startswith("note:")]
            if self.mode == "plan" and edits:
                return PermissionResultDeny(message=PLAN_BLOCK)
            if self.mode == "ask" and real and not self.turn_allow_edits:
                answer = await self._ask_user("Bash", str(data.get("command", "")), kind="edit")
                if answer == "turn":
                    self.turn_allow_edits = True
                elif not answer:
                    return PermissionResultDeny(message="The user declined this edit. Ask what they'd like instead, "
                                                        "or suggest a different change.")
        allowed = self.always | permissions.global_allow()  # this project's + Settings → Permissions
        always_key: str | None = None
        if tool == "Bash":
            ok, need = bash_check(str(data.get("command", "")), (REPO_ROOT, self.pdir))
            if ok:
                return PermissionResultAllow()
            if need is not None and need and not (need & _NEVER_ALWAYS):
                if need <= allowed:
                    return PermissionResultAllow()
                always_key = ",".join(sorted(need))
        elif tool in _FILE_TOOLS:
            return PermissionResultAllow()  # guard() already kept it inside the project
        elif tool.startswith("mcp__"):
            pass  # connectors are never auto-allowed
        else:
            if tool in allowed:
                return PermissionResultAllow()
            always_key = tool
        if tool == "Bash":
            detail = str(data.get("command", ""))
        elif "file_path" in data:
            detail = str(data["file_path"])
        else:
            detail = json.dumps(data, default=str)
        if permissions.unlisted() == "deny":
            await self.emit({"type": "notice", "text": f"Blocked {tool} (Settings → Permissions is set to deny without asking): {detail[:120]}"})
            return PermissionResultDeny(message="Blocked by the user's permission settings. Use a `uv run vedit ...` command "
                                                "instead, or tell the user what you need and why.")
        answer = await self._ask_user(tool, detail, always=always_key)
        if answer == "always" and always_key:
            self.always |= set(always_key.split(","))
            self._save()
        allow = bool(answer)
        verb = "Always allowing " + always_key.replace(",", ", ") if answer == "always" and always_key else ("Allowed" if allow else "Denied") + f" {tool}"
        await self.emit({"type": "notice", "text": f"{verb}: {detail[:120]}"})
        if allow:
            return PermissionResultAllow()
        return PermissionResultDeny(message="The user denied this. Prefer a `uv run vedit ...` command, "
                                            "or ask the user how they want to proceed.")

    async def _ask_user(self, tool: str, detail: str, always: str | None = None, kind: str = "tool",
                        extra: dict | None = None) -> Any:
        """Show an Allow / Deny card in the chat and wait for the answer (False after 10 minutes)."""
        rid = f"perm-{time.time_ns()}"
        ev = {"type": "permission", "id": rid, "tool": tool, "kind": kind, "detail": detail[:4000], "time": time.time(), **(extra or {})}
        if always:
            ev["always"] = always
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[rid] = (fut, ev)
        await self.emit(ev, persist=False)
        try:
            answer = await asyncio.wait_for(fut, timeout=600)
        except asyncio.TimeoutError:
            answer = False
        finally:
            self.pending.pop(rid, None)
            await self.emit({"type": "permission_done", "id": rid}, persist=False)
        if kind == "edit":
            await self.emit({"type": "notice", "text": ("Allowed edits for the rest of this request" if answer == "turn"
                                                        else "Allowed edit" if answer else "Declined edit") + f": {_short_cmd(detail)[:120]}"})
        return answer

    async def set_mode(self, mode: str) -> None:
        if mode not in CHAT_MODES:
            return
        self.mode = mode
        self._save()
        if mode != "plan":
            await self._clear_plan_offer()
        await self._broadcast({"type": "mode", "mode": self.mode})

    # ---- plan mode ---------------------------------------------------------------------------------
    # Plan mode is ours, not Claude Code's: its built-in plan mode routes Bash through its own safety check
    # (skipping can_use_tool, so vedit edits could slip through) and doesn't work with other providers. Here the
    # CLI stays in its default mode, `_permission` blocks every edit, the agent answers with a plan, and the chat
    # pane shows Approve buttons under it. Approving switches to auto / ask and sends "carry it out".
    async def _offer_plan(self, text: str) -> None:
        self.plan_offer = {"type": "plan_offer", "id": f"plan-{time.time_ns()}", "text": text[:8000], "time": time.time()}
        await self.emit(self.plan_offer, persist=False)

    async def _clear_plan_offer(self) -> None:
        if self.plan_offer:
            rid, self.plan_offer = self.plan_offer["id"], None
            await self._broadcast({"type": "permission_done", "id": rid})

    async def answer_plan(self, rid: str, answer: str, ctx: dict | None = None) -> None:
        """The user's answer to a plan: 'auto' / 'ask' (approve and carry it out that way), 'revise:<notes>', 'reject'."""
        if not self.plan_offer or self.plan_offer["id"] != rid or self.lock.locked():
            return
        await self._clear_plan_offer()
        answer = str(answer or "")
        if answer.startswith("revise:"):
            note = answer[len("revise:"):].strip()
            await self.ask("Revise the plan" + (f": {note}" if note else ".") + " Don't edit yet.", ctx or {})
        elif answer in ("auto", "ask"):
            await self.set_mode(answer)
            await self.ask("Plan approved. Carry it out now"
                           + (" (I'll approve each edit)." if answer == "ask" else "."), ctx or {})
        else:
            await self.emit({"type": "notice", "text": "Plan rejected. Nothing was changed."})

    def answer_permission(self, rid: str, allow: bool | str) -> None:
        item = self.pending.get(rid)
        if item and not item[0].done():
            item[0].set_result(allow if allow in ("always", "turn") else bool(allow))

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
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher

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
            disallowed_tools=["AskUserQuestion"],  # it needs a UI the chat pane doesn't have: ask in plain text
            env=env,
            resume=self.session_id if resume else None,
            max_buffer_size=32 * 1024 * 1024,  # tool results can carry images (frames Claude looks at)
            stderr=self._cli_log,  # keep Claude Code's diagnostics out of the user's terminal
            # the auditor: every time the agent says it's done, check the edit; must-fix findings send it back
            hooks={"Stop": [HookMatcher(hooks=[self._on_stop], timeout=AUDIT_TIMEOUT)],
                   "PreToolUse": [HookMatcher(matcher=None, hooks=[self._pre_tool], timeout=30)]},
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
            lines.append(f"timeline: {p.duration:.2f}s, {len(p.clips)} clips, {len(p.layers)} layers, canvas {p.canvas.width}x{p.canvas.height}"
                         + (f", video track at height {p.main_z} (tracks 0-{p.main_z - 1} under it)" if p.main_z else ""))
            from ..ops import safezone

            z = safezone.zone(p)
            lines.append(("platform: " + (p.platform or "not set (\"all apps\" zone)") + "; " if z.key != "title" else "")
                         + "keep text, captions, stickers, buttons and PiP inside the " + z.describe(p.canvas.width, p.canvas.height))
            if p.motion_style:
                from ..design import styles

                try:
                    lines.append("motion style (follow these numbers; the audit checks them): " + styles.brief(styles.get(p.motion_style)))
                except VeditError:
                    pass
            if ctx.get("playhead") is not None:
                lines.append(f"playhead: {float(ctx['playhead']):.2f}s")
            try:  # what the media library's study found in the sources (one line each; every shot via `vedit media show sN`)
                from .. import media_library

                studied = media_library.project_media(self.pdir, p)
                if studied:
                    lines.append("media (studied once; `vedit media show sN` lists every shot with notes, quality and roles):")
                    lines += [media_library.media_line(sid, Path(p.sources[sid].path).name, m) for sid, m in list(studied.items())[:8]]
                    if len(studied) > 8:
                        lines.append(f"  ... and {len(studied) - 8} more source(s)")
                lib = media_library.library_brief(self.pdir, p)
                if lib:
                    lines.append("the user's media library, not in this project yet (uploaded and studied once; use these when "
                                 "they ask for their footage / b-roll / product shots / music; `vedit media show <id>` = every "
                                 "shot, `vedit import <id>` adds one, `vedit overlay add <stored path>` for b-roll):")
                    lines += lib
            except Exception:  # noqa: BLE001 - context is a help, never a blocker
                pass
            if p.kind == "design":
                from ..ops import scenes as scene_ops

                rows = scene_ops.summary(p)
                lines.append("DESIGN project (Design studio): scenes play back to back; edit with `vedit scene ...`, "
                             "`vedit shape add <kind> --scene ID`, `vedit text add ... --start/--end inside the scene`, "
                             "`vedit animate preset|keyframes`, `vedit layer set --sx --sy`. Load the `motion` skill.")
                for r in rows:
                    lines.append(f"  scene {r['id']} {r['start']:.2f}-{r['end']:.2f}s \"{r['name']}\" [{r['transition']}]"
                                 + (f" bg {r['background']}" if r['background'] else "") + f", layers: {', '.join(r['layers']) or 'none'}"
                                 + (f" | notes: {r['notes'][:160]}" if r['notes'] else ""))
                if ctx.get("scene"):
                    lines.append(f"current scene (where the user is): {ctx['scene']}")
            sel = ctx.get("selection") or {}
            kind, sid = sel.get("kind"), sel.get("id")
            if kind == "clip":
                starts = dict(zip([c.id for c in p.clips], p.clip_starts()))
                c = p.clip(sid)
                if c.gap:
                    lines.append(f"selected: gap {c.id} (empty space on the video track) at timeline {starts[c.id]:.2f}-{starts[c.id] + c.duration:.2f}s")
                else:
                    lines.append(f"selected: clip {c.id} at timeline {starts[c.id]:.2f}-{starts[c.id] + c.duration:.2f}s "
                                 f"(source {c.source} {c.in_:.2f}-{c.out:.2f}s, speed {c.speed:g}, effects: "
                                 f"{', '.join(str(e.params.get('id')) + ' ' + str(e.params.get('key')) if e.type == 'fx' else e.type for e in c.effects) or 'none'})")
            elif kind == "layer":
                l = p.layer(sid)
                label = l.text or l.image or ""
                extra = f", track {l.track}" + (" (hidden)" if l.hidden else "")
                if l.type == "video":
                    extra += f", fit {l.fit}" + (", flipped" if l.flip_h or l.flip_v else "")
                if l.type in ("video", "adjust") and l.effects:
                    extra += ", effects: " + ", ".join(str(e.params.get("id")) + " " + str(e.params.get("key")) for e in l.effects if e.type == "fx")
                lines.append(f"selected: {l.type} layer {l.id} {l.start:.2f}-{(l.end if l.end is not None else p.duration):.2f}s{extra} \"{label[:60]}\"")
            elif kind == "fx":
                from ..ops import fx_filters

                holder, e = fx_filters.find(p, sid)
                pr = e.params
                e_end = "end" if pr.get("end") is None else f"{float(pr['end']):g}"
                win = f"{float(pr.get('start') or 0):g}-{e_end}s"
                lines.append(f"selected: effect {sid} ({pr.get('key')}) on {holder.id}, {win} of it, fade in {pr.get('fade_in') or 0}s / out {pr.get('fade_out') or 0}s"
                             f"{'' if pr.get('on', True) else ', switched off'} (change with `vedit fx set {sid} ...`)")
            elif kind == "marker":
                m = next((m for m in p.markers if m.id == sid), None)
                lines.append(f"selected: marker {sid} at {m.t:.2f}s \"{m.label or ''}\"" if m else f"selected: marker {sid}")
            elif kind == "music":
                lines.append(f"selected: music track {sid}")
            elif kind == "source":
                s = p.sources[sid]
                lines.append(f"selected: media source {sid} ({Path(s.path).name}, {s.duration:.2f}s, not necessarily on the timeline)")
            else:
                lines.append("selected: nothing")
            atts = [x for x in (ctx.get("attachments") or []) if isinstance(x, dict)][:24]
            if atts:
                lines.append("attached by the user (\"these\", \"the first / second one\", \"like the reference\" refer to these, in this order):")
                lines += ["  " + _attachment(p, i + 1, x, self.pdir) for i, x in enumerate(atts)]
        except Exception as e:  # never block a message because context failed
            lines.append(f"(context unavailable: {e})")
        lines += [n for n in (MODE_NOTES.get(getattr(self, "mode", "auto")), AUDIT_NOTES.get(getattr(self, "auditor", "checks"))) if n]
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
                await asyncio.to_thread(self._target_refs, ctx)
                self.turn_head, self.audit_rounds, self.audited_head = self._head(), 0, None
                self.turn_allow_edits = False
                await self._clear_plan_offer()
                n0 = len(self.log)
                await self.client.query(self.context_block(ctx) + "\n\n" + text)
                await self._pump()
                said = [e["text"] for e in self.log[n0:] if e.get("type") == "assistant"]
                if self.mode == "plan" and said:
                    await self._offer_plan(said[-1])
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

    # ---- the auditor (Stop hook) -------------------------------------------------------
    def _head(self) -> int | None:
        try:
            history.sync(self.pdir)
            return history.head(self.pdir)
        except Exception:
            return None

    def _target_refs(self, ctx: dict) -> None:
        """References attached to a message become the edit's targets (Project.notes.refs): the audit compares the
        edit with them, in this turn and the next ones (e.g. after a plan is approved)."""
        try:
            ids = [int(a["id"]) for a in (ctx.get("attachments") or []) if isinstance(a, dict) and a.get("kind") == "ref"]
            if not ids:
                return
            from ..project import EditNotes

            p = load(self.pdir)
            have = list(p.notes.refs) if p.notes else []
            if set(ids) <= set(have):
                return
            with history.transaction(self.pdir, "Match references " + ", ".join(f"#{i}" for i in ids)) as tx:
                if tx.project.notes is None:
                    tx.project.notes = EditNotes()
                tx.project.notes.refs = list(dict.fromkeys(ids + have))[:4]
        except Exception:
            pass  # never block a message over this

    def _run_audit(self) -> dict:
        if self.auditor == "ai":
            from ..review import full_audit

            return full_audit(self.pdir, use_ai=True)
        from ..audit import audit
        from ..review import reference_audit

        return reference_audit(self.pdir, audit(load(self.pdir), self.pdir, render=True))

    async def _on_stop(self, data: Any, tool_use_id: str | None, context: Any) -> dict:
        """Runs when the agent ends its turn. If the turn changed the edit, audit it. Below the pass mark (or with
        must-fix findings) the stop is blocked and the findings go back to the agent, up to the round cap from the
        audit settings; then the user decides."""
        from .. import review

        try:
            if self.auditor == "off":
                return {}  # the user switched the auditor off in the chat pane
            head = self._head()
            if head is None or head == self.turn_head:
                return {}  # nothing was edited this turn (a question, a look at frames...)
            if head == self.audited_head:
                return {}  # the agent was sent back but chose not to change anything (it said why): let it stop
            cfg = review.settings()
            cap = max(1, cfg["max_rounds"])
            if self.audit_rounds >= cap:
                return {}
            self.audit_rounds += 1
            self.audited_head = head
            await self.emit({"type": "status_step", "text": "Auditing the edit…"}, persist=False)
            r = await asyncio.to_thread(self._run_audit)
            ok = review.good_enough(r, cfg["threshold"])
            last = self.audit_rounds >= cap
            await self.emit({"type": "audit", "score": r["score"], "passed": r["passed"], "counts": r["counts"], "ai": bool(r.get("ai")),
                             "threshold": cfg["threshold"], "reached": ok, "summary": r.get("summary"), "round": self.audit_rounds,
                             "final": ok or last, "reference": r.get("reference"),
                             "findings": [{k: f.get(k) for k in ("id", "severity", "category", "at", "message", "fix")}
                                          for f in r["findings"] if f["severity"] != "note"][:8]})
            if ok or last:
                return {}
            todo = [f for f in r["findings"] if f["severity"] != "note"]
            lines = "\n".join(f"- {f['id']} [{f['severity']}] {f['category']}" + (f" @{f['at']}s" if f.get("at") is not None else "")
                              + f": {f['message']}" + (f" (suggested: {f['fix']})" if f.get("fix") else "") for f in todo[:12])
            return {"decision": "block", "reason":
                    f"Automatic audit (round {self.audit_rounds}/{cap}): score {r['score']}/100, the pass mark is {cfg['threshold']}"
                    + (f", with {r['counts']['must-fix']} must-fix" if r["counts"]["must-fix"] else "") + ".\n"
                    + (f"Reviewer: {r['summary']}\n" if r.get("summary") else "")
                    + (f"Likeness to the reference: {r['reference']['score']}/5 (reference findings are what it does that "
                       "your edit doesn't; Read its sheets again before fixing them).\n" if r.get("reference") else "")
                    + f"{lines}\n"
                    "Fix these with vedit commands (must-fix first), check with `vedit frame`, then finish with a short summary. "
                    "If one can't or shouldn't be fixed, say why instead."}
        except Exception as e:  # the auditor must never break the chat
            await self.emit({"type": "notice", "text": f"Audit skipped: {e}"})
            return {}

    async def set_auditor(self, value: str) -> None:
        if value not in AUDITORS:
            return
        self.auditor = value
        self._save()
        await self._broadcast({"type": "auditor", "value": self.auditor})

    async def set_auto_audit(self, on: bool) -> None:  # older editor pages: the tick box
        await self.set_auditor("ai" if on else "off")

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
                _record_chat_usage(self.model, msg)
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

    async def set_always(self, items: list[str]) -> None:
        """Replace this project's "Always allow" list (Settings → Permissions)."""
        self.always = {permissions.check_item(x) for x in items}
        self._save()

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
        self.plan_offer = None
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
        self.plan_offer = None
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

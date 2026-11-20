"""`vedit` commands inside a generated edit: the Generate editor gets the same command set as the editor chat AI.

A plan's structured ops (clips, titles, music, captions) build the skeleton; `vedit` ops then run real CLI commands on
the built project, so the editor can add shapes, sound effects, synth music, filters, motion graphics, overlays,
signature effects, colour, adjustment layers... exactly like the chat does. One command = one undoable history step.

What stays out (and why) is listed in DENIED; every top-level command must be in ALLOWED or DENIED
(tests/test_generate_commands.py), so a new CLI command forces a decision instead of silently missing.

References the model can't know in advance are resolved here, by code, after the skeleton is built:
    @C3            the 3rd clip of the plan (its real id)        @C3.start / .end / .mid   its timeline times
    @T1            the 1st title of the plan (its layer id)      @T1.start / .end
    @name          the layer a previous vedit op made (`name`)   @name.start / .end
    @C3.start+0.2  any time reference plus / minus seconds
Batch file ids (f1, m2...) stand for their files in `overlay add`, `image add` and `music add`; no other paths.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from ..project import Project, VeditError, load

# commands the generation editor may run (the same ones the chat uses to edit)
ALLOWED = {
    "place", "to-overlay", "to-clip", "paste", "volume", "fit", "zoom", "jumpcut-zoom", "transition", "why", "platform",
    "text", "image", "layer", "animate", "captions", "music", "color", "fx", "sound", "sfx", "overlay", "shape",
    "marker", "adjust", "track", "safe", "motion", "chart", "counter", "style", "group", "iso", "3d",
}
# command -> why it is not available in a generated plan
DENIED = {
    **dict.fromkeys(["trim", "keep", "delete", "split", "move", "remove-clip", "trim-to", "speed", "silence-cut"],
                    "the clip ops decide the cuts (use clip ops: start / end / speed)"),
    **dict.fromkeys(["init", "use", "import", "remove-source", "canvas", "projects", "export", "app", "serve"],
                    "the generator owns the project, its media and its format"),
    **dict.fromkeys(["info", "fonts", "audit", "frame", "sheet", "preview", "history"],
                    "read-only: nothing to see in a one-shot plan (the auditor reviews the render)"),
    **dict.fromkeys(["undo", "redo", "checkout", "version"], "history is managed by the generator"),
    **dict.fromkeys(["audit-settings", "taste", "ai-models", "template", "update", "ref", "gen", "design", "scene", "proxy", "media"],
                    "app-wide settings or another tool, not part of one edit"),
    **dict.fromkeys(["bg", "face", "mask", "focus"], "slow AI tracking / segmentation: too slow for a batch, use it in the editor"),
}
SUB_ALLOWED = {"captions": {"style"}}  # captions themselves come from the plan's captions op (auto-transcribed)
SUB_DENIED = {"text": {"look save", "look delete"}, "iso": {"scene"}, "color": {"lut"}, "overlay": {"library"}}
FILE_CMDS = {("overlay", "add"), ("image", "add"), ("music", "add")}  # first argument is a file
TIMEOUT_S = 180

_REF = re.compile(r"@([A-Za-z_][\w-]*)(?:\.(start|end|mid))?(?:([+-])(\d+(?:\.\d+)?))?")


def parse(command: str, files: dict[str, dict]) -> list[str]:
    """The command as argv (without `vedit`), checked. Raises ValueError with the reason."""
    try:
        args = shlex.split(command.strip())
    except ValueError as e:
        raise ValueError(f"can't parse: {e}") from None
    if args[:1] in (["vedit"], ["aiklips"]):
        args = args[1:]
    if args[:2] == ["uv", "run"]:
        args = args[3:]
    args = [a for a in args if a != "--json"]
    if not args:
        raise ValueError("empty command")
    if any(a in ("--project", "-p") or a.startswith("--project=") for a in args):
        raise ValueError("--project is set by the generator")
    cmd = args[0]
    if cmd in DENIED:
        raise ValueError(f"`{cmd}` is not available here: {DENIED[cmd]}")
    if cmd not in ALLOWED:
        raise ValueError(f"unknown command `{cmd}`")
    sub = " ".join(a for a in args[1:3] if not a.startswith("-"))
    if cmd in SUB_ALLOWED and not any(sub == s or sub.startswith(s + " ") for s in SUB_ALLOWED[cmd]):
        raise ValueError(f"only `{cmd} {'|'.join(SUB_ALLOWED[cmd])}` is available here")
    for s in SUB_DENIED.get(cmd, ()):
        if sub == s or sub.startswith(s + " "):
            raise ValueError(f"`{cmd} {s}` is not available here")
    if cmd == "3d" and any(a == "--src" or a.startswith("--src=") for a in args):
        raise ValueError("3D models from files are not available here: use a 3D kind made from code (`3d add car`, `text`, ...)")
    if (cmd, args[1] if len(args) > 1 else "") in FILE_CMDS:
        args = _file_arg(args, files)
    return args


def _file_arg(args: list[str], files: dict[str, dict]) -> list[str]:
    from ..ops import overlays

    pos = next((i for i in range(2, len(args)) if not args[i].startswith("-")), None)
    if pos is None:
        raise ValueError(f"`{' '.join(args[:2])}` needs a file id")
    a = args[pos]
    if a in files:
        return [*args[:pos], str(files[a]["path"]), *args[pos + 1:]]
    if args[0] == "overlay" and a in overlays.LIBRARY:
        return args
    raise ValueError(f"{a!r} is not one of the batch's file ids ({', '.join(files)})"
                     + (" or an overlay library key" if args[0] == "overlay" else ""))


def resolve_refs(args: list[str], p: Project, refs: dict[str, str]) -> list[str]:
    """Replace @C3 / @T1 / @name (and .start / .end / .mid ± s) with real ids and timeline seconds."""
    starts = dict(zip((c.id for c in p.clips), p.clip_starts()))

    def span(key: str) -> tuple[str, float, float]:
        rid = refs.get(key) or refs.get(key.upper())
        if rid is None:
            raise ValueError(f"@{key} doesn't exist (known: {', '.join('@' + k for k in refs) or 'none'})")
        if rid in starts:
            c = p.clip(rid)
            return rid, starts[rid], starts[rid] + c.duration
        try:
            layer = p.layer(rid)
        except VeditError:
            raise ValueError(f"@{key} ({rid}) is no longer in the edit") from None
        return rid, layer.start, layer.end if layer.end is not None else p.duration

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if not m.group(2) and key not in refs and key.upper() not in refs and not re.fullmatch(r"[CTct]\d+", key):
            return m.group(0)  # plain text ("follow @brand"), not a reference
        rid, a, b = span(key)
        if not m.group(2):
            if m.group(3):
                raise ValueError(f"{m.group(0)}: add .start / .end / .mid before the offset")
            return rid
        t = {"start": a, "end": b, "mid": (a + b) / 2}[m.group(2)]
        if m.group(3):
            t += float(m.group(4)) * (1 if m.group(3) == "+" else -1)
        return f"{max(0.0, t):.3f}"

    return [_REF.sub(sub, a) if "@" in a else a for a in args]


def run(pdir: Path, args: list[str]) -> dict:
    """Run one vedit command on the project in its own process (job threads run variations side by side, and the CLI
    keeps global state). Returns its JSON output; raises VeditError with the command's error."""
    try:
        r = subprocess.run([sys.executable, "-m", "vedit", "--json", "--project", str(pdir), *args],
                           capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise VeditError(f"took longer than {TIMEOUT_S}s") from None
    out = None
    for line in reversed(r.stdout.strip().splitlines()):
        try:
            out = json.loads(line)
            break
        except ValueError:
            continue
    if r.returncode != 0 or not out or not out.get("ok", False):
        msg = (out or {}).get("error") or (r.stderr.strip().splitlines() or [r.stdout.strip() or "failed"])[-1]
        raise VeditError(msg[:300])
    return out


def new_layers(before: Project, after: Project) -> list[str]:
    old = {l.id for l in before.layers}
    return [l.id for l in after.layers if l.id not in old]


def execute(pdir: Path, ops: list, files: dict[str, dict], refs: dict[str, str], report: dict) -> None:
    """Run the plan's vedit ops in order. Each op that adds layers and has a `name` becomes @name for later ops."""
    for o in ops:
        label = f"vedit {o.command}"[:120]
        try:
            args = parse(o.command or "", files)
            before = load(pdir)
            args = resolve_refs(args, before, refs)
            out = run(pdir, args)
            if o.name:
                made = new_layers(before, load(pdir))
                if made:
                    refs[o.name] = made[0]
            report["applied"].append(label)
            for w in out.get("warnings") or []:
                report.setdefault("warnings", []).append(f"{label}: {w}")
        except (ValueError, VeditError, KeyError) as e:
            report["skipped"].append(f"{label}: {e}")


# ---- the command reference the editor sees --------------------------------------------------------------------

@lru_cache(maxsize=1)
def reference() -> str:
    """Every allowed command with its arguments and options, generated from the CLI itself (so it never drifts),
    plus the names it takes (filters, sound presets, music styles, overlays, looks)."""
    import typer

    from ..cli import app

    # typer may ship its own copy of click, so go by attributes rather than click's classes
    def line(path: str, c) -> str:
        args = " ".join(p.name.upper() for p in c.params if p.param_type_name == "argument")
        opts = " ".join(max(p.opts, key=len) for p in c.params if p.param_type_name == "option" and p.name != "help")
        about = (c.help or c.short_help or "").strip().split("\n")[0].split(". ")[0][:110]
        return f"{path} {args} [{opts}]".replace(" []", "").replace("  ", " ") + (f": {about}" if about else "")

    out: list[str] = []

    def walk(prefix: str, g) -> None:
        for name, c in g.commands.items():
            path = f"{prefix} {name}".strip()
            top = path.split()[0]
            if top not in ALLOWED:
                continue
            rest = path[len(top):].strip()
            if top in SUB_ALLOWED and rest and not any(rest.startswith(s) for s in SUB_ALLOWED[top]):
                continue
            if any(rest == s or rest.startswith(s + " ") for s in SUB_DENIED.get(top, ())):
                continue
            if hasattr(c, "commands"):
                walk(path, c)
            else:
                out.append(line(path, c))

    walk("", typer.main.get_command(app))
    return "\n".join(out) + "\n\n" + _names()


def _names() -> str:
    from .. import musicmaker, sfx
    from ..design import styles
    from ..ops import animate, color, fx_filters, items3d, moves, overlays, text_looks

    return "\n".join([
        "NAMES:",
        "- fx apply moves: " + ", ".join(m["name"] for m in moves.catalog()),
        "- fx add filters: " + ", ".join(fx_filters.FILTERS),
        "- sfx add presets: " + ", ".join(sfx.PRESETS),
        "- music make styles: " + ", ".join(musicmaker.STYLES),
        "- overlay add library keys: " + ", ".join(overlays.LIBRARY),
        "- color look / adjust --look: " + ", ".join(color.LOOKS),
        "- text look apply: " + ", ".join(text_looks.BUILTIN),
        "- animate preset / text --preset: " + ", ".join(animate.PRESETS),
        "- style set: " + ", ".join(styles.STYLES),
        "- 3d add kinds (--param settings, defaults): " + "; ".join(
            f"{r['kind']}" + (f" ({', '.join(f'{k}={v}' for k, v in r['params'].items())})" if r["params"] else "")
            for r in items3d.catalog() if r["kind"] != "model"),
        "- 3d motion presets (animate preset): spin3d (axis, period), spin3d_in (turns), sway3d (degrees, period), "
        "travel (from, to, dur, loop, face, hop, scale_from, scale_to)",
    ])

"""Taste memory (P5): what this user kept and discarded, fed back to the planner as examples.

Every Keep / Discard on a generated variation appends one line to ~/.aiklips/taste.jsonl with what made
that video what it was: angle, hook, concept, structure, opening shot and its reason, axes, audit score.
`examples()` turns the most recent ones into a short prompt block. Local to this machine, never uploaded.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .. import library

MAX_LINES = 400


def path() -> Path:
    return library.home_dir() / "taste.jsonl"


def record(verdict: str, creative: dict, plan: dict | None, axes: dict, report: dict | None) -> dict:
    plan = plan or {}
    clips = [o for o in plan.get("ops", []) if o.get("op") == "clip"]
    concept = creative.get("concept") or {}
    row = {
        "t": round(time.time()), "verdict": verdict,
        "angle": creative.get("angle", ""), "hook": creative.get("hook_text", ""),
        "concept": concept.get("title", ""), "move": concept.get("signature_move"),
        "beats": [b.get("role") for b in plan.get("beats", [])],
        "opening": (clips[0].get("why") or "") if clips else "",
        "axes": {k: v for k, v in (axes or {}).items() if v != "reference" and k != "sound"},
        "audit": ((report or {}).get("audit") or {}).get("score"),
    }
    rows = load()
    rows.append(row)
    path().write_text("\n".join(json.dumps(r) for r in rows[-MAX_LINES:]) + "\n")
    return row


def load() -> list[dict]:
    try:
        lines = path().read_text().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def clear() -> None:
    path().unlink(missing_ok=True)


def _line(r: dict) -> str:
    bits = [f'hook "{r["hook"]}"' if r.get("hook") else "", f"concept {r['concept']}" if r.get("concept") else "",
            f"move {r['move']}" if r.get("move") else "", "beats " + ">".join(r["beats"]) if r.get("beats") else "",
            f"opening: {r['opening']}" if r.get("opening") else "",
            ", ".join(f"{k}={v}" for k, v in (r.get("axes") or {}).items())]
    return "; ".join(b for b in bits if b)


def examples(n: int = 5) -> str:
    """A prompt block with the latest kept and discarded videos, or '' when there is no history yet."""
    rows = load()
    kept = [r for r in rows if r.get("verdict") == "keep"][-n:]
    gone = [r for r in rows if r.get("verdict") == "discard"][-n:]
    if not kept and not gone:
        return ""
    out = ["THIS USER'S TASTE (from videos they kept or discarded before; lean toward what they kept, away from what they discarded,"
           " but don't copy hooks):"]
    out += [f"- KEPT: {_line(r)}" for r in kept]
    out += [f"- DISCARDED: {_line(r)}" for r in gone]
    return "\n".join(out)

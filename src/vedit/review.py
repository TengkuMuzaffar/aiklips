"""The agentic auditor (P2 of docs/design/auditor-justification-creativity.md).

    full_audit(pdir, ...)   code checks (audit.py, incl. sound.py) + an independent AI reviewer that looks at the render
    settings()              the pass mark (score threshold) and the revision cap, ~/.claude-cut/audit.json

The reviewer is read-only and never sees the editor's reasoning: only the brief, the beat sheet, the stated
reasons, the measured sound per clip and the rendered frames. Its findings must point at a frame; findings
without evidence are dropped. Fixing is the editor's job: generation hands the review to the editor, which
revises its whole plan (generate/batch.py); in the desktop editor the Stop hook sends it to the chat agent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import history
from .ai import client as ai
from .ai import prompts
from .audit import SEVERITY_COST, audit
from .project import ClipMeta, Project, VeditError, load

Severity = Literal["must-fix", "should-fix", "note"]
CATEGORIES = ("hook", "story", "pacing", "typography", "colour", "audio", "creativity", "safe-zone", "footage", "other")
OVERVIEW_FRAMES = 16
HOOK_FRAMES = 12  # first 3 s at 4 fps


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Scores(_M):
    hook: int = Field(3, ge=1, le=5, description="Would the first 1-2 s stop a scroll?")
    story: int = Field(3, ge=1, le=5, description="Do the beats land; does each clip do the job its why claims?")
    pacing: int = Field(3, ge=1, le=5)
    typography: int = Field(3, ge=1, le=5)
    colour: int = Field(3, ge=1, le=5)
    audio: int = Field(3, ge=1, le=5, description="Judge from the measured numbers given; 3 if unknown")
    creativity: int = Field(3, ge=1, le=5, description="Distinctive and memorable (5) vs a generic template (1)")


class AIFinding(_M):
    severity: Severity
    category: Literal[CATEGORIES]  # type: ignore[valid-type]
    frame: int = Field(description="Evidence: the frame number on the overview sheet (1..N), or 0 for the hook strip")
    at: float = Field(description="Timeline seconds of the problem")
    message: str = Field(description="What is wrong and why it matters to a viewer, one or two sentences")
    fix: str = Field("", description="The concrete change you'd make")


class AIReview(_M):
    summary: str = Field("", description="One or two sentences: the edit's biggest strength and biggest weakness")
    scores: Scores = Field(default_factory=Scores)
    findings: list[AIFinding] = Field(default_factory=list, description="At most 8, most important first")


# ---- describing the edit (what the reviewer and the editor read) -------------------------------------

def describe_edit(p: Project, fid_of: dict[str, str] | None = None, pdir: Path | None = None) -> str:
    """The edit as text: brief, beats, clips (with reasons, colour and measured sound), overlays, music."""
    from . import sound

    fid_of = fid_of or {}
    n = p.notes
    lines = [f"Duration {p.duration:.2f}s, canvas {p.canvas.width}x{p.canvas.height}."]
    if n:
        if n.brief:
            lines.append(f"BRIEF: {n.brief}")
        if n.beats:
            lines.append("BEATS: " + " | ".join(f"{b.id} {b.role}: {b.purpose}" for b in n.beats))
        if n.rationale:
            lines.append(f"EDITOR'S RATIONALE: {n.rationale}")
        if n.style:
            lines.append(f"TARGET STYLE: {n.style}")
    lines.append("CLIPS (timeline order):")
    for c, t0 in zip(p.clips, p.clip_starts()):
        src = p.sources[c.source]
        m = c.meta or ClipMeta()
        bits = [f"{c.id} @{t0:.2f}-{t0 + c.duration:.2f}s", f"from {fid_of.get(c.source, c.source)} ({Path(src.path).name}) {c.in_:.2f}-{c.out:.2f}s"]
        if c.speed != 1:
            bits.append(f"speed {c.speed:g}")
        if m.role or m.beat:
            bits.append(f"{m.role or '?'} / beat {m.beat or '?'}")
        if m.segment:
            bits.append(f"segment {m.segment}" + (f" q{m.quality}" if m.quality else ""))
        if c.grade and (c.grade.look or c.grade.matched_to):
            bits.append(f"colour {c.grade.look or 'natural'}" + (" matched" if c.grade.matched_to else ""))
        if c.transition:
            bits.append(f"transition in {c.transition.type}")
        if pdir is not None:
            try:
                bits.append("sound: " + sound.describe(sound.clip_stats(p, pdir, c), c.volume)
                            + (f" [{m.audio}]" if m.audio else ""))
            except (VeditError, OSError):
                pass
        lines.append("  " + ", ".join(bits) + (f'  WHY: "{m.why}"' if m.why else "  WHY: (none)"))
    if p.layers:
        lines.append("OVERLAYS:")
    for l in p.layers:
        end = p.duration if l.end is None else l.end
        label = l.text or l.image or " ".join(w.text for w in l.words[:12])
        style = "" if l.type == "image" else f", font {l.style.font or 'default'}" + (f", look {l.style.look}" if l.style.look else "")
        lines.append(f"  {l.id} {l.type} {l.start:.2f}-{end:.2f}s at ({l.x}, {l.y}){style}: \"{(label or '')[:80]}\""
                     + (f'  WHY: "{l.why}"' if l.why else ""))
    for m in p.music:
        lines.append(f"  music {m.id}: volume {m.volume:g}, ducked under speech: {m.duck}, starts {m.start:g}s, song offset {m.in_:g}s")
    return "\n".join(lines)


def make_sheets(video: Path, duration: float, out_dir: Path) -> tuple[list[Path], list[float]]:
    """Overview sheet (16 numbered frames with times) + hook strip (first 3 s at 4 fps)."""
    from .references import analysis

    out_dir.mkdir(parents=True, exist_ok=True)
    n = OVERVIEW_FRAMES
    times = [round(duration * (i + 0.5) / n, 2) for i in range(n)]
    frames = analysis.sample_frames(video, times, out_dir / "frames", "ov", width=270)
    overview = out_dir / "review-overview.jpg"
    analysis._tile([(f, f"#{i + 1}  {t:.1f}s") for i, (f, t) in enumerate(zip(frames, times))], overview, cols=4, width=270,
                   title="OVERVIEW: frame number and timeline time")
    hook_t = [round(i * 0.25, 2) for i in range(HOOK_FRAMES) if i * 0.25 < duration - 0.05]
    hook = analysis.sample_frames(video, hook_t, out_dir / "frames", "hk", width=270)
    strip = out_dir / "review-hook.jpg"
    analysis._tile([(f, f"{t:.2f}s") for f, t in zip(hook, hook_t)], strip, cols=6, width=200,
                   title="HOOK STRIP (frame 0): first 3 seconds, 4 per second")
    return [overview, strip], times


# ---- AI reviewer --------------------------------------------------------------------------------

def ai_review(p: Project, code: dict, sheets: list[Path], times: list[float], quality: str = "balanced",
              fid_of: dict[str, str] | None = None, pdir: Path | None = None) -> tuple[AIReview, dict]:
    measured = [f"{f['id']} [{f['severity']}] {f['category']}" + (f" @{f['at']}s" if f["at"] is not None else "") + f": {f['message']}"
                for f in code["findings"]]
    prompt = (describe_edit(p, fid_of, pdir)
              + "\n\nCODE CHECKS ALREADY RUN (exact; don't repeat them, use them as evidence):\n"
              + ("\n".join(measured) or "(none)")
              + f"\n\nThe overview sheet has frames 1..{len(times)} at: " + ", ".join(f"#{i + 1}={t:.1f}s" for i, t in enumerate(times))
              + "\nReview this edit.")
    rev, usage = ai.call("auditor", prompt, AIReview, system=prompts.AUDITOR, images=sheets, quality=quality)
    rev.findings = verify(rev.findings, times, p.duration)
    return rev, usage


def verify(findings: list[AIFinding], times: list[float], duration: float) -> list[AIFinding]:
    """Keep findings with evidence: a real frame, and a time that matches it (or is in the hook strip)."""
    keep = []
    for f in findings[:8]:
        if f.frame == 0:
            if not 0 <= f.at <= 3.2:
                f.at = min(max(f.at, 0.0), 3.0)
        elif 1 <= f.frame <= len(times):
            half = duration / len(times) if times else 1.0
            if abs(f.at - times[f.frame - 1]) > max(1.5, half * 1.5):
                f.at = times[f.frame - 1]  # trust the frame they pointed at over a mistyped time
        else:
            continue
        if not f.message.strip():
            continue
        keep.append(f)
    return keep


def merge(code: dict, rev: AIReview | None) -> dict:
    findings = [dict(f) for f in code["findings"]]
    if rev is not None:
        for i, f in enumerate(rev.findings, 1):
            findings.append({"id": f"A{i}", "severity": f.severity, "category": f.category, "at": round(f.at, 2),
                             "message": f.message, "evidence": f"frame {'hook strip' if f.frame == 0 else '#' + str(f.frame)}",
                             "fix": f.fix or None, "source": "ai"})
    order = {"must-fix": 0, "should-fix": 1, "note": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["at"] if f.get("at") is not None else 1e9))
    deduct = max(0, 100 - sum(SEVERITY_COST[f["severity"]] for f in findings))
    out = {"findings": findings, "passed": not any(f["severity"] == "must-fix" for f in findings),
           "counts": {k: sum(1 for f in findings if f["severity"] == k) for k in SEVERITY_COST}}
    if rev is not None:
        sc = rev.scores.model_dump()
        rubric = sum(sc.values()) / len(sc) / 5 * 100
        out.update(score=round(0.5 * deduct + 0.5 * rubric), scores=sc, summary=rev.summary, ai=True)
    else:
        out.update(score=deduct, ai=False)
    return out


def full_audit(pdir: Path, use_ai: bool = True, quality: str = "balanced", fid_of: dict | None = None,
               usage_cb: Callable[[str, dict], None] | None = None) -> dict:
    from .jobs import render_lock
    from .render.runner import render_preview

    p = load(pdir)
    with render_lock:
        res = render_preview(p, pdir, progress=False)
    code = audit(p, pdir, render=True)
    rev = None
    if use_ai:
        sheets, times = make_sheets(Path(res["output"]), p.duration, pdir / ".vedit" / "review")
        try:
            rev, u = ai_review(p, code, sheets, times, quality, fid_of, pdir)
            if usage_cb:
                usage_cb("auditor", u)
        except ai.RateLimited:
            raise
        except ai.AIError as e:
            code["findings"].append({"id": "R0", "severity": "note", "category": "audit", "at": None,
                                     "message": f"AI review skipped: {e}", "evidence": "", "fix": None})
    out = merge(code, rev)
    out["step"] = history.head(pdir)
    return out


# ---- the loop ------------------------------------------------------------------------------------

def fid_map(p: Project, files: dict[str, dict]) -> dict[str, str]:
    """source id -> footage file id, by path."""
    by_path = {str(Path(m["path"]).resolve()): fid for fid, m in files.items() if m.get("path")}
    out = {}
    for sid, s in p.sources.items():
        fid = by_path.get(str(Path(s.path).resolve()))
        if fid:
            out[sid] = fid
    return out


def brief(r: dict) -> dict:
    """A round's summary for reports and the UI."""
    return {k: r.get(k) for k in ("score", "passed", "counts", "scores", "summary")} | {
        "must_fix": [f["message"] for f in r["findings"] if f["severity"] == "must-fix"][:6]}


# ---- settings: when is an edit good enough? ------------------------------------------------------

DEFAULTS = {"threshold": 80, "max_rounds": 3}


def settings() -> dict:
    """Pass mark and revision cap, shared by generation and the chat editor (~/.claude-cut/audit.json)."""
    from . import library

    try:
        data = json.loads((library.home_dir() / "audit.json").read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    out = dict(DEFAULTS)
    if isinstance(data.get("threshold"), (int, float)):
        out["threshold"] = int(max(0, min(100, data["threshold"])))
    if isinstance(data.get("max_rounds"), (int, float)):
        out["max_rounds"] = int(max(0, min(MAX_ROUNDS_CAP, data["max_rounds"])))
    return out


def save_settings(threshold: int | None = None, max_rounds: int | None = None) -> dict:
    from . import library

    cur = settings()
    if threshold is not None:
        cur["threshold"] = int(max(0, min(100, threshold)))
    if max_rounds is not None:
        cur["max_rounds"] = int(max(0, min(MAX_ROUNDS_CAP, max_rounds)))
    (library.home_dir() / "audit.json").write_text(json.dumps(cur, indent=2))
    return cur


MAX_ROUNDS_CAP = 8  # never loop forever on footage that can't reach the bar
PLATEAU = 2  # stop after this many revisions in a row that don't beat the best score


def good_enough(r: dict, threshold: int) -> bool:
    return bool(r.get("passed")) and r.get("score", 0) >= threshold


def to_json(r: dict) -> str:
    return json.dumps(r, default=str)

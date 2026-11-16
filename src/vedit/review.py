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


class IssueCheck(_M):
    id: str = Field(description="Issue id from the OPEN ISSUES list (I1, I2...)")
    status: Literal["fixed", "open", "worse"] = Field(description="In THIS version: fixed, still there, or worse than before")
    note: str = Field("", description="A few words of evidence")


class AIReview(_M):
    summary: str = Field("", description="One or two sentences: the edit's biggest strength and biggest weakness")
    scores: Scores = Field(default_factory=Scores)
    findings: list[AIFinding] = Field(default_factory=list, description="At most 8, most important first")
    verdict: Literal["better", "same", "worse"] | None = Field(
        None, description="Only when a PREVIOUS BEST version is shown: is THIS version better, the same, or worse overall?")
    issue_checks: list[IssueCheck] = Field(default_factory=list, description="Only when OPEN ISSUES are listed: one check per issue")


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
              fid_of: dict[str, str] | None = None, pdir: Path | None = None, compare: dict | None = None,
              issues: list[dict] | None = None) -> tuple[AIReview, dict]:
    """`compare` = {"sheets": [overview, hook], "score", "scores", "summary"} of the best version so far. A single review
    is noisy (the same video scored 64 and 53), so a revision is judged side by side against the best, on its scale."""
    measured = [f"{f['id']} [{f['severity']}] {f['category']}" + (f" @{f['at']}s" if f["at"] is not None else "") + f": {f['message']}"
                for f in code["findings"]]
    prompt = (describe_edit(p, fid_of, pdir)
              + "\n\nCODE CHECKS ALREADY RUN (exact; don't repeat them, use them as evidence):\n"
              + ("\n".join(measured) or "(none)")
              + f"\n\nThe overview sheet has frames 1..{len(times)} at: " + ", ".join(f"#{i + 1}={t:.1f}s" for i, t in enumerate(times))
              + "\nReview this edit.")
    images = list(sheets)
    if compare and all(Path(x).exists() for x in compare.get("sheets", [])):
        images += list(compare["sheets"])[:2]
        sc = compare.get("scores") or {}
        prompt += ("\n\nCOMPARE: images 1-2 are THIS version; images 3-4 are the PREVIOUS BEST version of the same video "
                   f"(its overview and hook strip). That version scored {compare.get('score')}/100"
                   + (" with " + ", ".join(f"{k} {v}/5" for k, v in sc.items()) if sc else "")
                   + (f". Its review said: {compare['summary']}" if compare.get("summary") else "") + ".\n"
                   "Score THIS version on that same scale: where it is about as good, give about the same rubric score; raise "
                   "only what is clearly better, lower what is clearly worse. Then set `verdict` (better / same / worse overall, "
                   "as a viewer would feel it). Findings and frame numbers refer to THIS version only.")
    open_ai = [i for i in (issues or []) if i.get("status") != "fixed" and i.get("source") == "ai"]
    if open_ai:
        prompt += ("\n\nOPEN ISSUES from earlier reviews of this video. First check EACH one against THIS version in "
                   "`issue_checks` (fixed / open / worse). Then add at most 3 NEW problems as findings; don't repeat an "
                   "open issue as a finding:\n" + "\n".join(
                       f"- {i['id']} [{i['severity']}] {i['category']}" + (f" @{i['at']}s" if i.get("at") is not None else "")
                       + f": {i['message']}" for i in open_ai))
    rev, usage = ai.call("auditor", prompt, AIReview, system=prompts.AUDITOR, images=images, quality=quality)
    if not compare:
        rev.verdict = None
    known = {i["id"] for i in open_ai}
    rev.issue_checks = [c for c in rev.issue_checks if c.id in known]
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
               usage_cb: Callable[[str, dict], None] | None = None, compare: dict | None = None,
               issues: list[dict] | None = None, round_no: int = 0) -> dict:
    """Code checks + the AI reviewer. With `issues` (the best version's tracked issues), the review checks each one,
    and the result carries the updated list (`issues`) plus what this version fixed or made worse."""
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
            rev, u = ai_review(p, code, sheets, times, quality, fid_of, pdir, compare, issues)
            if usage_cb:
                usage_cb("auditor", u)
        except ai.RateLimited:
            raise
        except ai.AIError as e:
            code["findings"].append({"id": "R0", "severity": "note", "category": "audit", "at": None,
                                     "message": f"AI review skipped: {e}", "evidence": "", "fix": None})
    out = merge(code, rev)
    out["step"] = history.head(pdir)
    out["verdict"] = rev.verdict if rev is not None else None
    out["sheets"] = [str(x) for x in sheets_of(pdir)]
    tracked, fixed, worse = track(issues or [], out["findings"], rev.issue_checks if rev is not None else [], round_no)
    out.update(issues=tracked, fixed_now=fixed, worse_now=worse)
    return out


# ---- the issue list: one problem keeps one id across rounds ----------------------------------------

def _code_key(f: dict) -> str:
    """Code findings are re-generated each round; key them by check + subject (numbers stripped)."""
    import re as _re

    return f["category"] + ":" + _re.sub(r"[-\d.]+", "#", f["message"])[:70]


def track(prev: list[dict], findings: list[dict], checks: list, round_no: int) -> tuple[list[dict], list[str], list[str]]:
    """Carry the issue list forward. Code issues are open while their check still fires; AI issues follow the
    reviewer's check (missing check = still open). New must/should findings become new issues."""
    issues = [dict(i) for i in prev]
    by_id = {i["id"]: i for i in issues}
    n = max([int(i["id"][1:]) for i in issues if i["id"][1:].isdigit()] or [0])
    fixed, worse = [], []
    code_now = {_code_key(f): f for f in findings if f.get("source") != "ai" and f["severity"] != "note"}
    for i in issues:
        if i["status"] == "fixed":
            continue
        if i["source"] == "code":
            hit = code_now.pop(i["key"], None)
            if hit is None:
                i.update(status="fixed", closed=round_no)
                fixed.append(i["id"])
            else:
                i.update(message=hit["message"], at=hit.get("at"), severity=hit["severity"])
    for c in checks:
        i = by_id.get(c.id)
        if i is None or i["status"] == "fixed":
            continue
        if c.status == "fixed":
            i.update(status="fixed", closed=round_no)
            fixed.append(i["id"])
        elif c.status == "worse":
            i["status"] = "worse"
            worse.append(i["id"])
        else:
            i["status"] = "open"
        if c.note:
            i["check_note"] = c.note
    new = list(code_now.values()) + [f for f in findings if f.get("source") == "ai" and f["severity"] != "note"]
    for f in new:
        if f.get("source") == "ai" and any(i["status"] != "fixed" and i["category"] == f["category"] and i.get("at") is not None
                                           and f.get("at") is not None and abs(i["at"] - f["at"]) < 1.0 for i in issues):
            continue  # the reviewer restated an open issue
        n += 1
        issues.append({"id": f"I{n}", "source": "ai" if f.get("source") == "ai" else "code", "key": _code_key(f),
                       "severity": f["severity"], "category": f["category"], "at": f.get("at"), "message": f["message"],
                       "fix": f.get("fix"), "status": "open", "opened": round_no})
    order = {"worse": 0, "open": 1, "fixed": 2}
    sev = {"must-fix": 0, "should-fix": 1, "note": 2}
    issues.sort(key=lambda i: (order[i["status"]], sev.get(i["severity"], 2), int(i["id"][1:])))
    return issues, fixed, worse


def open_issues(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i["status"] != "fixed"]


def sheets_of(pdir: Path) -> list[Path]:
    d = pdir / ".vedit" / "review"
    return [x for x in (d / "review-overview.jpg", d / "review-hook.jpg") if x.exists()]


SCORE_SLACK = 5  # a revision may score this much under the best (review noise), never more


def beats(new: dict, best: dict, targeted: list[str] | None = None) -> bool:
    """Is a revision better than the best so far?
    - Never if it adds must-fix problems, makes an issue worse, or scores more than SCORE_SLACK under the best.
    - With a side-by-side verdict (single scores drift ~10 points): "better" wins; "same" wins only if it fixed an
      issue the patch was aimed at (`targeted`). Fixes the patch didn't aim at don't count: e.g. a render-wide change
      fixes the same code issue in every rebuild (live run: a "same" revision scoring 29 replaced a 40).
    - Without a verdict (code-only): a fixed targeted issue or a higher score decides."""
    if new.get("counts", {}).get("must-fix", 0) > best.get("counts", {}).get("must-fix", 0):
        return False
    if new.get("worse_now"):
        return False
    if new.get("score", 0) < best.get("score", 0) - SCORE_SLACK:
        return False
    aimed = set(targeted or []) & set(new.get("fixed_now") or [])
    v = new.get("verdict")
    if v:
        return v == "better" or (v == "same" and bool(aimed))
    return bool(aimed) or new.get("score", 0) > best.get("score", 0)


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
    return {k: r.get(k) for k in ("score", "passed", "counts", "scores", "summary", "verdict")} | {
        "fixed": r.get("fixed_now") or [], "worse": r.get("worse_now") or [],
        "open": len(open_issues(r.get("issues") or [])),
        "must_fix": [f["message"] for f in r["findings"] if f["severity"] == "must-fix"][:6],
        "must_cats": sorted({f["category"] for f in r["findings"] if f["severity"] == "must-fix"})}


def recurring(rounds: list[dict], n: int = 3) -> str | None:
    """A must-fix category flagged in each of the last n rounds: the edit can't fix it, the footage probably can't."""
    if len(rounds) < n:
        return None
    common = set(rounds[-1].get("must_cats") or [])
    for r in rounds[-n:-1]:
        common &= set(r.get("must_cats") or [])
    return sorted(common)[0] if common else None


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

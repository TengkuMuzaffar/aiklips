"""Batches of generated variations: create, run (as background jobs), review, keep or discard.

    create(spec)            -> batch row; queues "batch_prepare"
    batch_prepare job       merge Style DNA, log footage (AI), assign axes, write creative (AI),
                            then queue one "variation" job per variation
    variation job           plan edit (AI) -> exact timing (code) -> build project (vedit)
                            -> render + review (AI, one round of fixes) -> export MP4

Each variation is a normal aiklips project in projects/batches/<batch>/vNN, so the
editor, undo history and export work on it unchanged.
"""

from __future__ import annotations

import json
import math
import secrets
import shutil
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from .. import history, jobs, library, media
from ..ai import client as ai
from ..project import PROJECT_FILE, VeditError, load
from ..references import store as refs
from ..references.dna import StyleDNA
from . import executor, footage, planner, timing
from .spec import AXES, BatchSpec, Creative, EditPlan

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    spec       TEXT NOT NULL,
    dna        TEXT,
    status     TEXT NOT NULL DEFAULT 'queued',  -- queued | preparing | running | done | failed | cancelled
    progress   TEXT,
    error      TEXT,
    usage      TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS variations (
    id          TEXT PRIMARY KEY,
    batch_id    TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    dir         TEXT NOT NULL,
    axes        TEXT NOT NULL,
    creative    TEXT NOT NULL,
    plan        TEXT,
    report      TEXT,
    fingerprint TEXT,
    avoid       TEXT,
    status      TEXT NOT NULL DEFAULT 'queued', -- queued | planning | building | rendering | reviewing | exporting | done | failed | cancelled
    progress    TEXT,
    error       TEXT,
    verdict     TEXT,                           -- keep | discard | NULL
    output      TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS variations_batch ON variations(batch_id, idx);
"""

TERMINAL = {"done", "failed", "cancelled"}
DEFAULT_SECONDS = {"study": 45, "footage": 40, "planner": 60, "editor": 45, "auditor": 35, "concepts": 40, "rank": 30}


def _db():
    con = library._db()
    con.executescript(SCHEMA)
    return con


def batches_root() -> Path:
    d = library.projects_root() / "batches"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _set(table: str, key: str, **kw: Any) -> None:
    kw["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in kw)
    with closing(_db()) as con, con:
        con.execute(f"UPDATE {table} SET {cols} WHERE id = ?", (*kw.values(), key))


def _batch(bid: str) -> dict:
    with closing(_db()) as con:
        r = con.execute("SELECT * FROM batches WHERE id = ?", (bid,)).fetchone()
    if not r:
        raise VeditError(f"No batch {bid}")
    return dict(r)


def _var(vid: str) -> dict:
    with closing(_db()) as con:
        r = con.execute("SELECT * FROM variations WHERE id = ?", (vid,)).fetchone()
    if not r:
        raise VeditError(f"No variation {vid}")
    return dict(r)


def _vars(bid: str) -> list[dict]:
    with closing(_db()) as con:
        return [dict(r) for r in con.execute("SELECT * FROM variations WHERE batch_id = ? ORDER BY idx", (bid,))]


def _add_usage(bid: str, task: str, usage: dict | None) -> None:
    if not usage:
        return
    b = _batch(bid)
    u = json.loads(b["usage"] or "{}")
    u["calls"] = u.get("calls", 0) + int(usage.get("calls", 1))
    for k in ("input_tokens", "output_tokens"):
        u[k] = u.get(k, 0) + int(usage.get(k) or 0)
    if usage.get("cost_usd"):
        u["cost_usd"] = round(u.get("cost_usd", 0) + float(usage["cost_usd"]), 4)
    _set("batches", bid, usage=json.dumps(u))


def _files(spec: BatchSpec) -> list[tuple[str, str]]:
    return [(f"f{i + 1}", uid) for i, uid in enumerate(spec.footage)]


def _sound(spec: BatchSpec) -> list[tuple[str, str]]:
    """Files used only for their audio (fids m1, m2...): the wizard's Sound step."""
    return [(f"m{i + 1}", uid) for i, uid in enumerate(spec.sound)]


def _metas(spec: BatchSpec) -> dict[str, dict]:
    return {**{fid: footage.meta_full(u) for fid, u in _files(spec)}, **{fid: footage.sound_meta(u) for fid, u in _sound(spec)}}


# ---- create / estimate ---------------------------------------------------------------------

def validate(spec: BatchSpec) -> None:
    for rid in spec.refs:
        refs.get(rid)  # raises for unknown ids
    kinds = [footage.get(u)["kind"] for u in spec.footage]
    if "video" not in kinds:
        raise VeditError("Add at least one video clip of your own footage")
    for u in spec.sound:
        footage.sound_meta(u)  # raises for images / silent videos
    if kinds.count("audio") + len(spec.sound) > 10:
        raise VeditError("Pick at most 10 sounds / music tracks")
    unknown = [a for a in spec.axes if a not in AXES]
    if unknown:
        raise VeditError(f"Unknown variation axes: {', '.join(unknown)}")
    try:
        spec.models = ai.check_models(spec.models)
    except ai.AIError as e:
        raise VeditError(str(e)) from None


def estimate(spec: BatchSpec) -> dict:
    validate(spec)
    secs = {**DEFAULT_SECONDS, **ai.usage_stats()}
    study = sum(1 for r in spec.refs if refs.get(r)["status"] != "ready")
    videos = [(fid, u) for fid, u in _files(spec) if footage.get(u)["kind"] == "video"]
    undescribed = 0
    for _, u in videos:
        notes = footage._json(footage._dir(u) / "notes.json", {})
        segs = footage._json(footage._dir(u) / "index.json")
        if segs is None or any(str(s["k"]) not in notes for s in segs):
            undescribed += 1
    foot = math.ceil(undescribed / ai.MAX_IMAGES)
    n = spec.count
    # typical: one revision per video (worst case: the round cap). editor = first plan + revisions
    calls = {"study": study, "footage": foot, "planner": 1, "editor": 2 * n if spec.review else n,
             "auditor": 2 * n if spec.review else 0}
    if spec.explore:
        calls.update(concepts=n, rank=1)
    ai_seconds = sum(calls[k] * secs.get(k, 40) for k in calls)
    render_seconds = n * (spec.brief.length_s * 0.9 + 6) * (3 if spec.review else 2)
    total = ai_seconds / 2 + render_seconds  # two AI calls at a time, one render at a time
    from .. import review

    rounds = spec.audit_rounds if spec.audit_rounds is not None else review.settings()["max_rounds"]
    return {"calls": sum(calls.values()), "breakdown": calls, "minutes": max(1, round(total / 60)),
            "max_calls": sum(calls.values()) + (n * 2 * max(0, rounds - 1) if spec.review else 0),
            "note": "Uses your AI subscription. If you hit its limit, the batch pauses and resumes by itself."}


def create(spec: BatchSpec) -> dict:
    validate(spec)
    bid = time.strftime("b%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    name = (spec.brief.product.strip()[:50] or "Generation") + time.strftime(" · %b %d %H:%M")
    now = time.time()
    with closing(_db()) as con, con:
        con.execute("INSERT INTO batches (id, name, spec, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (bid, name, spec.model_dump_json(), now, now))
    for rid in spec.refs:
        if refs.get(rid)["status"] in ("failed",):
            refs.restudy(rid)
    jobs.enqueue("batch_prepare", f"batch:{bid}")
    return get(bid)


# ---- jobs ----------------------------------------------------------------------------------

@jobs.handler("batch_prepare")
def _prepare(job: jobs.Job) -> dict:
    bid = job.target.split(":", 1)[1]
    spec = BatchSpec.model_validate_json(_batch(bid)["spec"])
    with ai.use_models(spec.models):  # the models picked for this generation
        return _prepare_inner(job, bid)


def _prepare_inner(job: jobs.Job, bid: str) -> dict:
    b = _batch(bid)
    spec = BatchSpec.model_validate_json(b["spec"])
    waiting = []
    for rid in spec.refs:
        r = refs.get(rid)
        if r["status"] == "failed":
            raise VeditError(f"Reference “{r['name']}” could not be studied: {r.get('error') or 'unknown error'}")
        if r["status"] != "ready":
            waiting.append(r["name"])
            jobs.enqueue("study", f"ref:{rid}")
    if waiting:
        _set("batches", bid, status="preparing", progress=f"Waiting for {len(waiting)} reference(s) to be studied")
        raise jobs.Retry(8, "waiting for references")
    _set("batches", bid, status="preparing", progress="Combining the reference styles", error=None)
    dna = refs.merged(spec.refs, spec.lead)
    try:  # the lead reference's colours, for graphics (scenes, cards) when it is motion design
        dna.color.palette = refs.palette_of(spec.lead if spec.lead in spec.refs else spec.refs[0])
    except Exception:  # noqa: BLE001 - a palette is a nice-to-have; never fail the batch over it
        dna.color.palette = {}
    _set("batches", bid, dna=dna.model_dump_json())
    files = _files(spec)
    job.check()
    u = footage.describe(files, spec.quality, lambda s: _set("batches", bid, progress=s))
    _add_usage(bid, "footage", u if u.get("calls") else None)
    log = footage.shot_log(files, _sound(spec))
    job.check()
    axes = planner.assign_axes(spec.count, spec.axes, seed=bid)
    planner.assign_sounds(axes, log)
    _set("batches", bid, progress=f"AI is planning {spec.count} variation(s)")
    creatives, usage = planner.creative(spec, dna, log, axes)
    _add_usage(bid, "planner", usage)
    job.check()
    if spec.explore:
        _set("batches", bid, progress=f"AI is exploring concepts for {spec.count} variation(s)")
        creatives = planner.explore(spec, dna, log, creatives, axes, usage_cb=lambda k, u: _add_usage(bid, k, u))
        job.check()
    now = time.time()
    with closing(_db()) as con, con:
        con.execute("DELETE FROM variations WHERE batch_id = ?", (bid,))
        for i, (a, c) in enumerate(zip(axes, creatives)):
            vid = f"{bid}-v{i + 1:02d}"
            con.execute("INSERT INTO variations (id, batch_id, idx, dir, axes, creative, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (vid, bid, i + 1, str(batches_root() / bid / f"v{i + 1:02d}"), json.dumps(a), c.model_dump_json(), now, now))
    for i in range(spec.count):
        jobs.enqueue("variation", f"var:{bid}-v{i + 1:02d}")
    _set("batches", bid, status="running", progress=None)
    return {}


@jobs.on_fail("batch_prepare")
def _prepare_failed(job: jobs.Job, msg: str) -> None:
    _set("batches", job.target.split(":", 1)[1], status="failed", progress=None, error=msg[:600])


@jobs.on_wait("batch_prepare")
def _prepare_wait(job: jobs.Job, wait: float) -> None:
    _set("batches", job.target.split(":", 1)[1], progress=f"Waiting for AI quota (~{int(wait // 60) or 1} min)")


def _context(v: dict) -> tuple[dict, BatchSpec, StyleDNA, list, dict, dict]:
    b = _batch(v["batch_id"])
    spec = BatchSpec.model_validate_json(b["spec"])
    dna = StyleDNA.model_validate_json(b["dna"]) if b["dna"] else refs.merged(spec.refs, spec.lead)
    files = _files(spec)
    return b, spec, dna, files, footage.shot_log(files, _sound(spec)), _metas(spec)


def run_variation(vid: str, job: jobs.Job | None = None) -> dict:
    v = _var(vid)
    b, spec, dna, files, log, metas = _context(v)
    def step(status: str, text: str) -> None:
        _set("variations", vid, status=status, progress=text, error=None)
        if job:
            job.check()

    axes = json.loads(v["axes"])
    c = Creative.model_validate_json(v["creative"])
    d = planner.directives(dna, spec.brief, axes, log, executor.captions_available())
    step("planning", "AI is planning the edit")
    avoid = json.loads(v["avoid"] or "[]")
    plan, u = planner.plan_edit(spec, dna, log, c, axes, d, avoid)
    _add_usage(v["batch_id"], "editor", u)
    fp = planner.fingerprint(plan, axes)
    twins = [s for s in _vars(v["batch_id"]) if s["id"] != vid and s["status"] == "done" and s["fingerprint"]
             and planner.similarity(fp, json.loads(s["fingerprint"])) >= 0.8]
    if twins:
        step("planning", "Too close to a sibling: planning again")
        other = [EditPlan.model_validate_json(s["plan"]) for s in twins if s["plan"]]
        avoid = avoid + [f"don't open with {o.ops[0].file} at {o.ops[0].start}s; don't reuse the hook '{next((x.text for x in o.ops if x.op == 'text'), '')}'"
                         for o in other if o.ops]
        plan, u = planner.plan_edit(spec, dna, log, c, axes, d, avoid)
        _add_usage(v["batch_id"], "editor", u)
        fp = planner.fingerprint(plan, axes)
    plan, notes = timing.resolve(plan, metas, spec.brief.length_s)
    _set("variations", vid, plan=plan.model_dump_json(), fingerprint=json.dumps(fp))

    # build -> audit -> (editor revises from the review -> rebuild -> audit)* until the score clears the pass mark.
    # Every attempt is its own folder; the best one becomes the variation. Code checks always run; the AI
    # reviewer and revisions only with review on.
    from .. import review

    bid = v["batch_id"]
    pdir = Path(v["dir"])
    cfg = review.settings()
    threshold = spec.audit_threshold if spec.audit_threshold is not None else cfg["threshold"]
    max_rounds = (spec.audit_rounds if spec.audit_rounds is not None else cfg["max_rounds"]) if spec.review else 0
    name = f"{b['name'].split(' · ')[0]} v{v['idx']} – {plan.title or c.angle}"[:80]
    brief = f"{spec.brief.goal}: {spec.brief.product}".strip(": ")
    usage_cb = lambda k, u: _add_usage(bid, k, u)  # noqa: E731

    def build(pl: EditPlan, where: Path) -> dict:
        if where.exists() and where.parent.parent == batches_root():
            _unregister(where)
            shutil.rmtree(where)
        return executor.execute(pl, where, metas, name, spec.brief.format, log=log, brief=brief,
                                target_shot_s=d.get("avg_shot_s"), style=dna.summary, refs=list(spec.refs))

    best, rounds, stop = _loop(vid, step, build, spec, dna, log, c, axes, d, metas, pdir, threshold, max_rounds, usage_cb,
                               first=(plan, notes), fids=dict(files),
                               refresh_log=lambda: footage.shot_log(files, _sound(spec)))
    _finish(vid, step, pdir, best, rounds, threshold, stop)
    return {}


LEARN_CATEGORIES = {"hook", "story", "footage", "colour"}  # reviewer findings about the picture itself


def _about_footage(i: dict) -> bool:
    if i.get("source") == "ai":
        return i["category"] in LEARN_CATEGORIES
    return i["category"] == "footage" and "almost black" in i["message"]  # not "reused footage": that's an edit choice


def _learn(where: Path, rev: dict, fids: dict[str, str], round_no: int) -> int:
    """Footage memory: an issue about the picture marks the segment playing at its moment as weak for that clip's
    role, ONCE, when the issue first appears (an open issue re-checked every round must not pile up marks)."""
    try:
        p = load(where)
    except VeditError:
        return 0
    n = 0
    starts = p.clip_starts()
    for f in rev.get("issues", []):
        if f.get("opened") != round_no or f["status"] == "fixed" or not _about_footage(f) or f.get("at") is None:
            continue
        for c, t0 in zip(p.clips, starts):
            if t0 - 1e-3 <= f["at"] < t0 + c.duration and c.meta and c.meta.segment:
                fid, _, k = c.meta.segment.partition(".s")
                if fid in fids and k.isdigit():
                    footage.mark(fids[fid], k, c.meta.role, good=False, note=f["message"])
                    n += 1
                break
    return n


def _introduced(old: EditPlan, new: EditPlan) -> list[tuple[str, str | None]]:
    """(segment, role) of clips the revision brought in."""
    roles = {b.id: b.role for b in new.beats}
    before = {o.segment for o in old.ops if o.op == "clip"}
    return [(o.segment, roles.get(o.beat or "")) for o in new.ops if o.op == "clip" and o.segment and o.segment not in before]


def _mark_segments(segs: list[tuple[str, str | None]], fids: dict[str, str], good: bool, note: str) -> None:
    for seg, role in segs:
        fid, _, k = seg.partition(".s")
        if fid in fids and k.isdigit():
            footage.mark(fids[fid], k, role, good=good, note=note)


def _loop(vid, step, build, spec, dna, log, c, axes, d, metas, pdir: Path, threshold: int, max_rounds: int, usage_cb,
          first: tuple | None = None, start: dict | None = None, plateau: int | None = None,
          fids: dict[str, str] | None = None, refresh_log=None) -> tuple[dict, list, dict]:
    """Build -> audit -> (editor PATCHES the best version for its top open issues -> rebuild -> audit side by side)
    until the pass mark, the round cap, or `plateau` revisions in a row that aren't better. Each review updates the
    issue list (B) and leaves marks on the footage it judged (C), so later rounds and later videos avoid repeats."""
    from .. import review

    fids = fids or {}
    rounds = list((start or {}).get("rounds") or [])
    base = len(rounds)
    best = start["best"] if start else None
    stale = 0
    todo = (first[0], first[1]) if first else None
    info: dict = {}
    for k in range(max_rounds + (1 if first else 0)):
        r = base + k
        if todo is None:
            open_now = review.open_issues(best["review"].get("issues") or [])
            step("planning", f"Editor is fixing {', '.join(i['id'] for i in open_now[:3]) or 'the review'}: audit {best['review']['score']} < {threshold}")
            try:
                plan, u, info = planner.patch_plan(spec, dna, log, c, axes, d, best["plan"], open_now, best["review"], threshold,
                                                   attempts=rounds) if open_now else (None, {}, {"note": "no open issues"})
                usage_cb("editor", u)
            except ai.RateLimited:
                raise
            except ai.AIError as e:
                plan, info = None, {"note": f"patch failed: {e}"[:120]}
            if plan is None:  # no usable patch: fall back to a full revision of the best version
                plan, u = planner.revise_plan(spec, dna, log, c, axes, d, best["plan"], best["review"], threshold,
                                              attempts=rounds, best_round=best["round"])
                usage_cb("editor", u)
                info = {"note": "full revision" + (f" ({info.get('note')})" if info.get("note") else ""), "full": True}
            todo = timing.resolve(plan, metas, spec.brief.length_s)
        cur_plan, cur_notes = todo
        todo = None
        where = pdir if r == 0 else pdir.with_name(f"{pdir.name}.r{r}")
        step("building", "Building the edit" if r == 0 else f"Rebuilding with the fixes (round {r})")
        rep_exec = build(cur_plan, where)
        rep_exec["timing"] = cur_notes
        step("reviewing", "Auditing the edit" if r == 0 else f"Re-auditing side by side (round {r})")
        compare = None
        if best is not None:  # judge the revision side by side with the best so far (single scores drift ~10 points)
            bsheets = best["review"].get("sheets") or [str(x) for x in review.sheets_of(best["dir"])]
            compare = {"sheets": bsheets, "score": best["review"].get("score"), "scores": best["review"].get("scores"),
                       "summary": best["review"].get("summary")}
        rev = review.full_audit(where, use_ai=spec.review, quality=spec.quality,
                                fid_of=review.fid_map(load(where), metas), usage_cb=usage_cb, compare=compare,
                                issues=(best["review"].get("issues") or []) if best else [], round_no=r)
        learned = _learn(where, rev, fids, r)
        accepted = best is None or review.beats(rev, best["review"], targeted=info.get("fixes"))
        if best is not None:
            intro = _introduced(best["plan"], cur_plan)
            if accepted and intro:
                _mark_segments(intro, fids, True, f"a revision using it was judged {rev.get('verdict') or 'better'}")
            elif not accepted and rev.get("verdict") == "worse" and intro:
                _mark_segments(intro, fids, False, "a revision using it was judged worse")
        rounds.append(review.brief(rev) | {"round": r, "kept": accepted, "learned": learned,
                                           **({"patch": {k2: info.get(k2) for k2 in ("note", "fixes", "full") if info.get(k2)}} if r > 0 else {})})
        if accepted:
            if best is not None and best["dir"] != where and best["dir"] != pdir and best["dir"].exists():
                shutil.rmtree(best["dir"])  # the old best is beaten
            best, stale = {"review": rev, "dir": where, "plan": cur_plan, "exec": rep_exec, "round": r}, 0
        else:
            stale += 1
            if where != pdir and where.exists():
                shutil.rmtree(where)  # not better than the best: drop it now
        if refresh_log is not None:
            log = refresh_log()  # the footage log now carries what this round learned
        if review.good_enough(rev, threshold) and accepted:
            return best, rounds, {"reason": "passed", "ran": k + (0 if first else 1), "asked": max_rounds}
        if stale >= (plateau or review.PLATEAU):
            return best, rounds, {"reason": "plateau", "ran": k + (0 if first else 1), "asked": max_rounds, "stale": stale}
    return best, rounds, {"reason": "rounds", "ran": max_rounds, "asked": max_rounds}


def _finish(vid: str, step, pdir: Path, best: dict, rounds: list, threshold: int, stop: dict | None = None) -> None:
    """The best attempt becomes the variation's project; record the audit; export."""
    from .. import review

    if best["dir"] != pdir:
        if pdir.exists():
            shutil.rmtree(pdir)
        shutil.move(str(best["dir"]), str(pdir))
        best["dir"] = pdir
    for stale_dir in pdir.parent.glob(f"{pdir.name}.r*"):
        shutil.rmtree(stale_dir, ignore_errors=True)
    plan, rev, report = best["plan"], best["review"], best["exec"]
    _set("variations", vid, plan=plan.model_dump_json())
    report["audit"] = {k: rev.get(k) for k in ("score", "passed", "counts", "scores", "summary")} | {
        "threshold": threshold, "reached": review.good_enough(rev, threshold), "rounds": rounds,
        "kept_round": best["round"], "head": history.head(pdir), "stopped": stop,
        "recurring": review.recurring(rounds),
        "issues": [{k: i.get(k) for k in ("id", "source", "key", "severity", "category", "at", "message", "fix", "status", "opened", "closed")}
                   for i in (rev.get("issues") or [])][:40],
        "findings": [{k: f.get(k) for k in ("id", "severity", "category", "at", "message", "fix")} for f in rev.get("findings", [])][:30]}
    report["review"] = {"ok": report["audit"]["reached"], "revisions": len(rounds) - 1,
                        "issues": [f["message"] for f in rev.get("findings", []) if f["severity"] != "note"][:10]}

    step("exporting", "Exporting the video")
    from ..render.runner import render

    p = load(pdir)
    dest = pdir / "exports" / f"{library.slugify(p.name)}.mp4"
    with jobs.render_lock:
        render(p, pdir, dest, 1.0, "export", progress=False)
    try:
        media.extract_frame(dest, min(1.0, p.duration / 3), pdir / ".vedit" / "poster.jpg", 360)
    except VeditError:
        pass
    _set("variations", vid, status="done", progress=None, output=str(dest), report=json.dumps(report))
    _update_batch(_var(vid)["batch_id"])




# ---- keep improving one finished video ------------------------------------------------------------

def improve(vid: str, rounds: int | None = None) -> dict:
    """Queue more editor revisions for one finished video, starting from its best version and its last review."""
    from .. import review

    v = _var(vid)
    if v["status"] != "done":
        raise VeditError("Only a finished video can be improved")
    rep = json.loads(v["report"] or "{}")
    if not v["plan"] or not rep.get("audit"):
        raise VeditError("This video has no audit to improve from; regenerate it instead")
    pdir = Path(v["dir"])
    head = rep["audit"].get("head")
    if head is not None and (pdir / PROJECT_FILE).exists():
        # compare content, not step numbers: format upgrades and no-op syncs don't count as hand edits
        if not history.same_as(pdir, head):
            raise VeditError("This video was edited by hand since it was generated. Open it in the editor and ask the AI "
                             "there: it audits every change and keeps going until the pass mark.")
    n = int(rounds if rounds is not None else review.settings()["max_rounds"]) or 1
    n = max(1, min(review.MAX_ROUNDS_CAP, n))
    _set("variations", vid, status="queued", progress=f"Waiting to improve ({n} more round{'s' if n > 1 else ''})", error=None)
    jobs.enqueue("variation_improve", f"improve:{vid}:{n}")
    _update_batch(v["batch_id"])
    return _public_var(_var(vid))


def run_improve(vid: str, rounds: int, job: jobs.Job | None = None) -> dict:
    from .. import review

    v = _var(vid)
    b, spec, dna, files, log, metas = _context(v)

    def step(status: str, text: str) -> None:
        _set("variations", vid, status=status, progress=text, error=None)
        if job:
            job.check()

    axes = json.loads(v["axes"])
    c = Creative.model_validate_json(v["creative"])
    d = planner.directives(dna, spec.brief, axes, log, executor.captions_available())
    rep = json.loads(v["report"])
    audit = rep["audit"]
    pdir = Path(v["dir"])
    bid = v["batch_id"]
    threshold = spec.audit_threshold if spec.audit_threshold is not None else review.settings()["threshold"]
    name = load(pdir).name
    brief = f"{spec.brief.goal}: {spec.brief.product}".strip(": ")
    usage_cb = lambda k, u: _add_usage(bid, k, u)  # noqa: E731

    def build(pl: EditPlan, where: Path) -> dict:
        if where.exists() and where.parent.parent == batches_root():
            shutil.rmtree(where)
        return executor.execute(pl, where, metas, name, spec.brief.format, log=log, brief=brief,
                                target_shot_s=d.get("avg_shot_s"), style=dna.summary, refs=list(spec.refs))

    exec_report = {k: x for k, x in rep.items() if k not in ("audit", "review")}
    start = {"rounds": audit.get("rounds") or [], "best": {
        "review": {k: audit.get(k) for k in ("score", "passed", "counts", "scores", "summary")} | {"findings": audit.get("findings", []),
                                                                                             "sheets": [str(x) for x in review.sheets_of(pdir)],
                                                                                             "issues": _issues_of(audit)},
        "dir": pdir, "plan": EditPlan.model_validate_json(v["plan"]), "exec": exec_report,
        "round": audit.get("kept_round", 0)}}
    with ai.use_models(spec.models):
        # you asked for these rounds: tolerate one more non-improving revision than a fresh generation does
        best, all_rounds, stop = _loop(vid, step, build, spec, dna, log, c, axes, d, metas, pdir, threshold, rounds, usage_cb,
                                       start=start, plateau=review.PLATEAU + 1, fids=dict(files),
                                       refresh_log=lambda: footage.shot_log(files, _sound(spec)))
    _finish(vid, step, pdir, best, all_rounds, threshold, stop)
    return {}


def _issues_of(audit: dict) -> list[dict]:
    """The stored issue list, or one rebuilt from the stored findings (videos made before issues were tracked)."""
    from .. import review

    if audit.get("issues"):
        return audit["issues"]
    findings = [f | {"source": "ai" if str(f.get("id", "")).startswith("A") else "code"} for f in audit.get("findings", [])]
    return review.track([], findings, [], audit.get("kept_round", 0))[0]


@jobs.handler("variation_improve")
def _improve_job(job: jobs.Job) -> dict:
    _kind, vid, n = job.target.split(":")
    return run_improve(vid, int(n), job)


@jobs.on_fail("variation_improve")
def _improve_failed(job: jobs.Job, msg: str) -> None:
    vid = job.target.split(":")[1]
    # the video is still there (the best version never moved); just report why improving stopped
    _set("variations", vid, status="done", progress=None, error=f"Improving stopped: {msg[:400]}")
    _update_batch(_var(vid)["batch_id"])


@jobs.on_wait("variation_improve")
def _improve_wait(job: jobs.Job, wait: float) -> None:
    _set("variations", job.target.split(":")[1], progress=f"Waiting for AI quota (~{int(wait // 60) or 1} min)")


@jobs.handler("variation")
def _variation_job(job: jobs.Job) -> dict:
    vid = job.target.split(":", 1)[1]
    spec = BatchSpec.model_validate_json(_batch(_var(vid)["batch_id"])["spec"])
    with ai.use_models(spec.models):  # the models picked for this generation
        return run_variation(vid, job)


@jobs.on_fail("variation")
def _variation_failed(job: jobs.Job, msg: str) -> None:
    vid = job.target.split(":", 1)[1]
    _set("variations", vid, status="failed", progress=None, error=msg[:600])
    _update_batch(_var(vid)["batch_id"])


@jobs.on_wait("variation")
def _variation_wait(job: jobs.Job, wait: float) -> None:
    _set("variations", job.target.split(":", 1)[1], status="queued", progress=f"Waiting for AI quota (~{int(wait // 60) or 1} min)")


def _update_batch(bid: str) -> None:
    vs = _vars(bid)
    if vs and all(v["status"] in TERMINAL for v in vs):
        ok = any(v["status"] == "done" for v in vs)
        b = _batch(bid)
        if b["status"] != "cancelled":
            _set("batches", bid, status="done" if ok else "failed", progress=None,
                 error=None if ok else "No variation could be made. Open one to see the error.")
    elif _batch(bid)["status"] in ("done", "failed"):
        _set("batches", bid, status="running")


# ---- listing ----------------------------------------------------------------------------------

def _public_var(v: dict) -> dict:
    c = json.loads(v["creative"])
    plan = json.loads(v["plan"]) if v["plan"] else None
    rep = json.loads(v["report"]) if v["report"] else None
    pdir = Path(v["dir"])
    row = library.find_by_path(pdir) if (pdir / PROJECT_FILE).exists() else None
    job = jobs.for_target(f"var:{v['id']}")
    return {
        "id": v["id"], "idx": v["idx"], "status": v["status"], "progress": v["progress"] or (job or {}).get("note") if v["status"] == "queued" else v["progress"],
        "error": v["error"], "verdict": v["verdict"], "axes": json.loads(v["axes"]), "angle": c.get("angle"),
        "hook_text": c.get("hook_text"), "cta_text": c.get("cta_text"), "title": (plan or {}).get("title"),
        "concept": c.get("concept"), "concept_reason": c.get("concept_reason"), "rationale": (plan or {}).get("rationale"),
        "output": v["output"], "has_poster": (pdir / ".vedit" / "poster.jpg").exists(), "project_id": row["id"] if row and not row["hidden"] else None,
        "review": (rep or {}).get("review"), "skipped": (rep or {}).get("skipped", []), "updated_at": v["updated_at"],
        "audit": None if not (rep or {}).get("audit") else {k: rep["audit"].get(k) for k in ("score", "passed", "counts", "summary", "scores",
                                                                                              "threshold", "reached", "rounds", "stopped", "recurring", "issues")}
                 | {"top": [f for f in rep["audit"].get("findings", []) if f["severity"] != "note"][:5]},
        "attention": bool(v["status"] == "done" and (rep or {}).get("audit")
                          and not rep["audit"].get("reached", rep["audit"].get("passed"))),
        "plan_problems": (rep or {}).get("plan_problems", []),
    }


def get(bid: str) -> dict:
    b = _batch(bid)
    spec = json.loads(b["spec"])
    vs = [_public_var(v) for v in _vars(bid)]
    job = jobs.for_target(f"batch:{bid}")
    progress = b["progress"]
    if b["status"] == "queued" and job and job.get("note"):
        progress = job["note"]
    names = []
    for rid in spec["refs"]:
        try:
            names.append(refs.get(rid)["name"])
        except VeditError:
            names.append(f"#{rid} (deleted)")
    return {"id": bid, "name": b["name"], "status": b["status"], "progress": progress, "error": b["error"],
            "created_at": b["created_at"], "count": spec["count"], "brief": spec["brief"], "refs": names,
            "usage": json.loads(b["usage"] or "{}"), "variations": vs,
            "models": {r["tier"]: (spec.get("models") or {}).get(r["tier"]) or r["effective"] for r in ai.roles(spec.get("quality", "balanced"))},
            "done": sum(1 for v in vs if v["status"] == "done")}


def list_batches() -> list[dict]:
    with closing(_db()) as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM batches ORDER BY created_at DESC LIMIT 100")]
    return [get(i) for i in ids]


# ---- actions -------------------------------------------------------------------------------------

def _unregister(pdir: Path) -> None:
    row = library.find_by_path(pdir)
    if row:
        from contextlib import closing as _c

        with _c(library._db()) as con, con:
            con.execute("DELETE FROM projects WHERE id = ?", (row["id"],))


def project_for(vid: str) -> dict:
    """Register the variation's project in the library (so it can open in the editor)."""
    v = _var(vid)
    pdir = Path(v["dir"])
    if not (pdir / PROJECT_FILE).exists():
        raise VeditError("This variation isn't built yet")
    history.sync(pdir)
    return library.get(library.register(pdir))


def _remember(vid: str, verdict: str) -> None:
    """Taste memory: what the user kept or discarded steers future plans."""
    from . import taste

    v = _var(vid)
    if v["verdict"] == verdict:
        return
    try:
        taste.record(verdict, json.loads(v["creative"]), json.loads(v["plan"]) if v["plan"] else None,
                     json.loads(v["axes"]), json.loads(v["report"]) if v["report"] else None)
    except (OSError, ValueError):
        pass


def keep(vid: str) -> dict:
    row = project_for(vid)
    _remember(vid, "keep")
    _set("variations", vid, verdict="keep")
    return row


def discard(vid: str) -> None:
    _remember(vid, "discard")
    v = _var(vid)
    _set("variations", vid, verdict="discard")
    row = library.find_by_path(Path(v["dir"]))
    if row:
        library.remove(row["id"], delete_files=False)


def regenerate(vid: str) -> dict:
    v = _var(vid)
    jobs.cancel(f"var:{vid}")
    avoid = json.loads(v["avoid"] or "[]")
    if v["plan"]:
        plan = EditPlan.model_validate_json(v["plan"])
        hook = next((o.text for o in plan.ops if o.op == "text"), "")
        first = next((o for o in plan.ops if o.op == "clip"), None)
        avoid.append(f"the previous attempt opened with {first.file if first else '?'} at {first.start if first else 0}s and the hook '{hook}'; do something clearly different")
    _set("variations", vid, status="queued", progress=None, error=None, verdict=None, plan=None, report=None,
         fingerprint=None, output=None, avoid=json.dumps(avoid[-4:]))
    jobs.enqueue("variation", f"var:{vid}")
    _update_batch(v["batch_id"])
    return _public_var(_var(vid))


def cancel(bid: str) -> dict:
    jobs.cancel(f"batch:{bid}")
    for v in _vars(bid):
        jobs.cancel(f"var:{v['id']}")
        if v["status"] not in TERMINAL:
            _set("variations", v["id"], status="cancelled", progress=None)
    _set("batches", bid, status="cancelled", progress=None)
    return get(bid)


def resume(bid: str) -> dict:
    _batch(bid)
    vs = _vars(bid)
    if not vs:
        _set("batches", bid, status="queued", error=None, progress=None)
        jobs.enqueue("batch_prepare", f"batch:{bid}")
    else:
        _set("batches", bid, status="running", error=None)
        for v in vs:
            if v["status"] in ("failed", "cancelled"):
                _set("variations", v["id"], status="queued", error=None, progress=None)
                jobs.enqueue("variation", f"var:{v['id']}")
    return get(bid)


def delete(bid: str) -> None:
    cancel(bid)
    d = batches_root() / bid
    for v in _vars(bid):
        _unregister(Path(v["dir"]))
    if d.exists() and d.parent == batches_root():
        shutil.rmtree(d, ignore_errors=True)
    with closing(_db()) as con, con:
        con.execute("DELETE FROM variations WHERE batch_id = ?", (bid,))
        con.execute("DELETE FROM batches WHERE id = ?", (bid,))


def poster(vid: str) -> Path:
    return Path(_var(vid)["dir"]) / ".vedit" / "poster.jpg"


def video(vid: str) -> Path:
    v = _var(vid)
    if not v["output"] or not Path(v["output"]).exists():
        raise VeditError("Not exported yet")
    return Path(v["output"])

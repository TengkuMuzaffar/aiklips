"""Batches of generated variations: create, run (as background jobs), review, keep or discard.

    create(spec)            -> batch row; queues "batch_prepare"
    batch_prepare job       merge Style DNA, log footage (AI), assign axes, write creative (AI),
                            then queue one "variation" job per variation
    variation job           plan edit (AI) -> exact timing (code) -> build project (vedit)
                            -> render + review (AI, one round of fixes) -> export MP4

Each variation is a normal claude-cut project in projects/batches/<batch>/vNN, so the
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
DEFAULT_SECONDS = {"study": 45, "footage": 40, "planner": 60, "editor": 45, "critic": 25}


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


# ---- create / estimate ---------------------------------------------------------------------

def validate(spec: BatchSpec) -> None:
    for rid in spec.refs:
        refs.get(rid)  # raises for unknown ids
    kinds = [footage.get(u)["kind"] for u in spec.footage]
    if "video" not in kinds:
        raise VeditError("Add at least one video clip of your own footage")
    if kinds.count("audio") > 1:
        raise VeditError("Pick at most one music track")
    unknown = [a for a in spec.axes if a not in AXES]
    if unknown:
        raise VeditError(f"Unknown variation axes: {', '.join(unknown)}")


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
    calls = {"study": study, "footage": foot, "planner": 1, "editor": n, "critic": n if spec.review else 0}
    ai_seconds = sum(calls[k] * secs.get(k, 40) for k in calls)
    render_seconds = n * (spec.brief.length_s * 0.9 + 6) * (2 if spec.review else 1)
    total = ai_seconds / 2 + render_seconds  # two AI calls at a time, one render at a time
    return {"calls": sum(calls.values()), "breakdown": calls, "minutes": max(1, round(total / 60)),
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
    _set("batches", bid, dna=dna.model_dump_json())
    files = _files(spec)
    job.check()
    u = footage.describe(files, spec.quality, lambda s: _set("batches", bid, progress=s))
    _add_usage(bid, "footage", u if u.get("calls") else None)
    log = footage.shot_log(files)
    job.check()
    axes = planner.assign_axes(spec.count, spec.axes, seed=bid)
    _set("batches", bid, progress=f"AI is planning {spec.count} variation(s)")
    creatives, usage = planner.creative(spec, dna, log, axes)
    _add_usage(bid, "planner", usage)
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
    metas = {fid: footage.meta_full(u) for fid, u in files}
    return b, spec, dna, files, footage.shot_log(files), metas


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

    step("building", "Building the edit")
    pdir = Path(v["dir"])
    if pdir.exists() and pdir.parent.parent == batches_root():
        _unregister(pdir)
        shutil.rmtree(pdir)
    name = f"{b['name'].split(' · ')[0]} v{v['idx']} – {plan.title or c.angle}"[:80]
    report = executor.execute(plan, pdir, metas, name, spec.brief.format)
    report["timing"] = notes

    if spec.review:
        step("rendering", "Rendering a preview")
        from ..render.runner import render_preview

        with jobs.render_lock:
            res = render_preview(load(pdir), pdir, progress=False)
        sheet = pdir / ".vedit" / "review-sheet.jpg"
        p = load(pdir)
        media.contact_sheet(Path(res["output"]), p.duration, sheet, 12, 4, 270)
        step("reviewing", "AI is reviewing the result")
        layers = [{"id": l.id, "type": l.type, "text": l.text or (" ".join(w.text for w in l.words[:10]) if l.words else l.image),
                   "start": l.start, "end": l.end, "x": l.x, "y": l.y, "size": l.style.size} for l in p.layers]
        try:
            rev, u = planner.review(sheet, layers, dna, spec.quality)
            _add_usage(v["batch_id"], "critic", u)
            report["review"] = {"ok": rev.ok, "issues": rev.issues, "fixed": executor.apply_fixes(pdir, rev.fixes) if not rev.ok else []}
        except ai.RateLimited:
            raise
        except ai.AIError as e:
            report["review"] = {"ok": None, "issues": [f"review skipped: {e}"], "fixed": []}

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
    _update_batch(v["batch_id"])
    return {}


@jobs.handler("variation")
def _variation_job(job: jobs.Job) -> dict:
    return run_variation(job.target.split(":", 1)[1], job)


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
        "output": v["output"], "has_poster": (pdir / ".vedit" / "poster.jpg").exists(), "project_id": row["id"] if row and not row["hidden"] else None,
        "review": (rep or {}).get("review"), "skipped": (rep or {}).get("skipped", []), "updated_at": v["updated_at"],
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


def keep(vid: str) -> dict:
    row = project_for(vid)
    _set("variations", vid, verdict="keep")
    return row


def discard(vid: str) -> None:
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

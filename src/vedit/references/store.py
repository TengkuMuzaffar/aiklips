"""Reference library: add videos, study them (measure + AI), keep the Style DNA.

Files: ~/.claude-cut/references/<sha16>/
    source.<ext>   copy of the uploaded video (the user's original is never touched)
    metrics.json   measured by code (references.analysis)
    thumb.jpg, sheet-1.jpg, sheet-2.jpg
    dna.json       Style DNA (AI + measured), before the user's overrides
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from .. import jobs, library
from ..ai import client as ai
from ..ai import prompts
from ..project import VeditError
from . import analysis, dna as dnamod

SCHEMA = """
CREATE TABLE IF NOT EXISTS refs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    sha            TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    dir            TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'queued',   -- queued | studying | ready | failed
    progress       TEXT,
    tags           TEXT NOT NULL DEFAULT '[]',
    overrides      TEXT NOT NULL DEFAULT '{}',
    ai             INTEGER NOT NULL DEFAULT 0,         -- 1 = visual traits came from the AI
    prompt_version INTEGER,
    error          TEXT,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
"""

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


def _db():
    con = library._db()
    con.executescript(SCHEMA)
    return con


def root() -> Path:
    d = library.home_dir() / "references"
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _set(rid: int, **kw: Any) -> None:
    kw["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in kw)
    with closing(_db()) as con, con:
        con.execute(f"UPDATE refs SET {cols} WHERE id = ?", (*kw.values(), rid))


def _row(rid: int) -> dict:
    with closing(_db()) as con:
        r = con.execute("SELECT * FROM refs WHERE id = ?", (rid,)).fetchone()
    if not r:
        raise VeditError(f"No reference #{rid}")
    return dict(r)


def source_path(row: dict) -> Path:
    d = Path(row["dir"])
    return next((p for p in d.glob("source.*")), d / "source.mp4")


# ---- add / list / get --------------------------------------------------------------------

def add(path: Path, name: str | None = None, study: bool = True, move: bool = False) -> dict:
    """Add a video to the library (deduplicated by content). Queues the study job."""
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise VeditError(f"File not found: {path}")
    if path.suffix.lower() not in VIDEO_EXT:
        raise VeditError(f"{path.name}: references must be videos ({', '.join(sorted(VIDEO_EXT))})")
    sha = file_hash(path)
    with closing(_db()) as con:
        r = con.execute("SELECT id FROM refs WHERE sha = ?", (sha,)).fetchone()
    if r:
        if move:
            path.unlink(missing_ok=True)
        return get(int(r["id"]))
    d = root() / sha[:16]
    d.mkdir(parents=True, exist_ok=True)
    dest = d / ("source" + path.suffix.lower())
    if move:
        shutil.move(str(path), dest)
    else:
        shutil.copy2(path, dest)
    now = time.time()
    with closing(_db()) as con, con:
        cur = con.execute("INSERT INTO refs (sha, name, dir, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                          (sha, (name or path.stem)[:120], str(d), now, now))
        rid = int(cur.lastrowid)
    if study:
        jobs.enqueue("study", f"ref:{rid}")
    return get(rid)


def _load_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def get(rid: int, full: bool = False) -> dict:
    row = _row(rid)
    d = Path(row["dir"])
    row["tags"] = json.loads(row["tags"] or "[]")
    row["overrides"] = json.loads(row["overrides"] or "{}")
    m = _load_json(d / "metrics.json")
    base = _load_json(d / "dna.json")
    row["duration"] = m.get("duration") if m else None
    row["has_dna"] = base is not None
    row["stale"] = bool(base) and row["ai"] == 1 and (row["prompt_version"] or 0) < prompts.PROMPT_VERSION
    if base:
        style = dnamod.apply_overrides(base, row["overrides"])
        row["dna"] = style.model_dump()
        row["summary"] = style.summary
        row["auto_tags"] = style.tags
        if full:
            row["card"] = dnamod.card(style)
            row["base_dna"] = base
    if full and m:
        a = m.get("audio") or {}
        row["metrics"] = {
            "duration": m["duration"], "width": m.get("width"), "height": m.get("height"), "fps": m.get("fps"),
            "cuts": m["cuts"], "pacing": m["pacing"], "motion": m.get("motion"), "first_cut_s": m.get("first_cut_s"),
            "cuts_on_beat": m.get("cuts_on_beat"), "cuts_on_beat_chance": m.get("cuts_on_beat_chance"),
            "audio": None if not a else {k: a.get(k) for k in ("bpm", "tempo_confidence", "beats", "onsets", "rms", "rms_step", "silence_ratio")},
            "transcript": m.get("transcript"), "sheets": m.get("sheets", []),
        }
    job = jobs.for_target(f"ref:{rid}")
    row["job"] = None if not job else {"status": job["status"], "note": job["note"]}
    return row


def list_refs() -> list[dict]:
    with closing(_db()) as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM refs ORDER BY created_at DESC")]
    return [get(i) for i in ids]


def update(rid: int, name: str | None = None, tags: list[str] | None = None, overrides: dict | None = None) -> dict:
    row = _row(rid)
    kw: dict[str, Any] = {}
    if name is not None:
        if not name.strip():
            raise VeditError("Name can't be empty")
        kw["name"] = name.strip()[:120]
    if tags is not None:
        kw["tags"] = json.dumps([str(t).strip()[:30] for t in tags if str(t).strip()][:12])
    if overrides is not None:
        base = _load_json(Path(row["dir"]) / "dna.json")
        if base is None:
            raise VeditError("This reference hasn't been studied yet")
        clean = {k: v for k, v in overrides.items() if v is not None}
        try:
            dnamod.apply_overrides(base, clean)
        except Exception as e:
            raise VeditError(f"Invalid trait value: {e}") from None
        kw["overrides"] = json.dumps(clean)
    if kw:
        _set(rid, **kw)
    return get(rid, full=True)


def delete(rid: int) -> None:
    row = _row(rid)
    jobs.cancel(f"ref:{rid}")
    d = Path(row["dir"])
    if d.exists() and d.parent == root():
        shutil.rmtree(d, ignore_errors=True)
    with closing(_db()) as con, con:
        con.execute("DELETE FROM refs WHERE id = ?", (rid,))


def restudy(rid: int) -> dict:
    _row(rid)
    _set(rid, status="queued", progress=None, error=None)
    jobs.enqueue("study", f"ref:{rid}")
    return get(rid)


def style(rid: int) -> dnamod.StyleDNA:
    row = _row(rid)
    base = _load_json(Path(row["dir"]) / "dna.json")
    if base is None:
        raise VeditError(f"Reference “{row['name']}” isn't studied yet")
    return dnamod.apply_overrides(base, json.loads(row["overrides"] or "{}"))


def merged(ids: list[int], lead: int | None = None) -> dnamod.StyleDNA:
    styles = [style(i) for i in ids]
    li = ids.index(lead) if lead in ids else None
    return dnamod.merge(styles, li)


# ---- studying ---------------------------------------------------------------------------------

def study(rid: int, job: jobs.Job | None = None, quality: str = "balanced") -> dict:
    """Measure the video, then ask the AI for the visual traits. Always leaves a usable DNA."""
    row = _row(rid)
    d = Path(row["dir"])
    src = source_path(row)
    _set(rid, status="studying", progress="Starting", error=None)

    def say(text: str) -> None:
        _set(rid, progress=text)
        if job:
            job.check()

    metrics = _load_json(d / "metrics.json")
    if metrics is None or not (d / "sheet-1.jpg").exists():
        metrics = analysis.analyze(src, d, say)
    say("AI is studying the style")
    usage: dict = {}
    ai_used = 0
    try:
        prompt = ("MEASURED METRICS (exact):\n" + json.dumps(analysis.summary_for_ai(metrics), indent=1)
                  + "\n\nAllowed values are in the JSON schema. Study the style and return the Style DNA.")
        sheets = [d / s for s in metrics.get("sheets", [])]
        result, usage = ai.call("study", prompt, dnamod.StyleDNA, system=prompts.STUDY, images=sheets, quality=quality)
        style_ = dnamod.apply_measured(result, metrics)
        ai_used = 1
        note = None
    except ai.RateLimited:
        _set(rid, status="queued", progress="Waiting for AI quota")
        raise
    except ai.AIError as e:  # no login / bad answer: keep the measured traits, say why
        style_ = dnamod.from_metrics(metrics)
        note = f"Studied without AI ({e}). Visual traits are defaults; you can edit them or re-study."
    (d / "dna.json").write_text(json.dumps(style_.model_dump(), indent=1))
    _set(rid, status="ready", progress=None, ai=ai_used, prompt_version=prompts.PROMPT_VERSION, error=note)
    return usage


@jobs.handler("study")
def _study_job(job: jobs.Job) -> dict:
    return study(int(job.target.split(":")[1]), job)


@jobs.on_fail("study")
def _study_failed(job: jobs.Job, msg: str) -> None:
    _set(int(job.target.split(":")[1]), status="failed", progress=None, error=msg[:500])


@jobs.on_wait("study")
def _study_wait(job: jobs.Job, wait: float) -> None:
    _set(int(job.target.split(":")[1]), status="queued", progress=f"Waiting for AI quota (~{int(wait // 60) or 1} min)")

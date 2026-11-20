"""Media library: every file the user brings in (footage, sound, images) is studied once and reused everywhere.

The files themselves stay where generate/footage.py keeps them (~/.aiklips/uploads/<sha16>/, the id is the start
of the content hash, so the same file added twice is one entry). This module adds what that folder lacks:

- a row per file in library.db (status, versions, tags, user overrides, where it is used),
- a background study job at import ("media_study"): code measures first (cuts, motion, light, sound, speech),
  then the AI logs each segment, and code writes a small digest.json (what the planner reads first),
- versioning: a better measure or a new footage prompt re-studies only what is stale (`restudy_stale`, app start).

Status: queued -> measuring -> studying -> ready | partial (measured, but the AI notes are missing) | failed.
Only `ready` and `partial` are usable; Generate waits for the rest.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

from . import jobs, library, library_store
from .ai import client as ai
from .ai import prompts
from .project import VeditError

MEASURE_VERSION = 1  # bump when footage.measure / segments / audio stats change meaningfully
TRANSCRIPT_MAX_S = 600
MAX_AI_SEGMENTS = 90  # a long recording is logged by evenly spread samples: one AI call stays small
BEST_N = 5
SETTLED = ("ready", "partial", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id              TEXT PRIMARY KEY,                 -- upload id = first 16 hex chars of the content hash
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL,                    -- video | audio | image
    status          TEXT NOT NULL DEFAULT 'queued',   -- queued | measuring | studying | ready | partial | failed
    progress        TEXT,
    measure_version INTEGER NOT NULL DEFAULT 0,
    study_version   INTEGER NOT NULL DEFAULT 0,
    tags            TEXT NOT NULL DEFAULT '[]',
    overrides       TEXT NOT NULL DEFAULT '{}',       -- {segment k: {description, quality, tags, good_for...}}
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    last_used_at    REAL
);
CREATE TABLE IF NOT EXISTS media_usage (
    media_id TEXT NOT NULL,
    kind     TEXT NOT NULL,                           -- project | batch
    target   TEXT NOT NULL,
    used_at  REAL NOT NULL,
    PRIMARY KEY (media_id, kind, target)
);
"""


def _db():
    con = library._db()
    con.executescript(SCHEMA)
    return con


def _footage():
    from .generate import footage  # imported late: footage.add registers files here

    return footage


def _row(uid: str) -> dict | None:
    with closing(_db()) as con:
        r = con.execute("SELECT * FROM media WHERE id = ?", (uid,)).fetchone()
    return dict(r) if r else None


def _set(uid: str, **kw: Any) -> None:
    kw["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in kw)
    with closing(_db()) as con, con:
        con.execute(f"UPDATE media SET {cols} WHERE id = ?", (*kw.values(), uid))


_load = library_store.load_json


# ---- registering ---------------------------------------------------------------------------

def register(uid: str, study: bool = True) -> dict:
    """Make sure an upload folder has a library row (and a study queued when it needs one). Idempotent."""
    fm = _footage()
    meta = fm.meta_full(uid)
    row = _row(uid)
    if row is None:
        path = Path(meta["path"])
        now = time.time()
        with closing(_db()) as con, con:
            con.execute("INSERT OR IGNORE INTO media (id, name, kind, size_bytes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (uid, meta["name"], meta["kind"], path.stat().st_size if path.exists() else 0, now, now))
        row = _row(uid)
    if study and needs_study(row):
        queue(uid)
    return row


def needs_study(row: dict) -> bool:
    if row["status"] in ("queued", "measuring", "studying"):
        j = jobs.for_target(f"media:{row['id']}")
        return not (j and j["status"] in ("queued", "running"))  # on its way, unless its job was lost
    if row["status"] == "failed":
        return False  # the user re-studies by hand; don't loop on a broken file
    return row["measure_version"] < MEASURE_VERSION or (row["kind"] == "video" and row["study_version"] < prompts.FOOTAGE_VERSION)


def queue(uid: str) -> None:
    _set(uid, status="queued", progress=None, error=None)
    jobs.enqueue("media_study", f"media:{uid}", start=jobs.IN_APP)  # the app's workers poll the queue every few s


def backfill() -> int:
    """Register upload folders made before the library existed (or by an older app). Called at app start."""
    root = _footage().root()
    n = 0
    with closing(_db()) as con:
        known = {r["id"] for r in con.execute("SELECT id FROM media").fetchall()}
    for d in root.iterdir() if root.exists() else []:
        if d.is_dir() and d.name not in known and (d / "meta.json").exists():
            try:
                register(d.name)
                n += 1
            except (VeditError, OSError):
                continue
    return n


def restudy_stale() -> list[str]:
    """Queue every settled file whose measure or AI notes are older than this app's (called at app start)."""
    with closing(_db()) as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM media WHERE status IN ('ready', 'partial')").fetchall()]
    out = []
    for r in rows:
        if needs_study(r):
            queue(r["id"])
            out.append(r["id"])
    return out


def add(path: Path, name: str | None = None, move: bool = False) -> dict:
    """Add a file to the library (copied in, deduplicated by content) and queue its study."""
    meta = _footage().add(path, name, move=move)  # footage.add registers it
    return get(meta["id"])


def wait_list(uids: list[str]) -> list[str]:
    """Names of files that are still being studied (registering any that slipped in without a row)."""
    out = []
    for uid in dict.fromkeys(uids):
        row = _row(uid) or register(uid)
        if row["status"] not in SETTLED:
            out.append(row["name"])
    return out


# ---- studying -------------------------------------------------------------------------------

def study(uid: str, job: jobs.Job | None = None) -> dict:
    fm = _footage()
    row = _row(uid) or register(uid, study=False)
    meta = fm.meta_full(uid)
    d = fm._dir(uid)

    def say(text: str, status: str | None = None) -> None:
        _set(uid, progress=text, **({"status": status} if status else {}))
        if job:
            job.check()

    stale_measure = row["measure_version"] < MEASURE_VERSION
    if stale_measure:  # index.json (the segments) stays: notes, marks and overrides are keyed by segment number
        (d / "measure.json").unlink(missing_ok=True)
    say("Measuring", "measuring")
    if meta["kind"] == "video":
        segs = fm.segments(uid)
        try:
            fm.measure(uid)
        except Exception:  # noqa: BLE001 - measuring is a bonus; the AI notes still work without it
            pass
        if meta.get("has_audio"):
            fm._audio_stats(uid)
    else:
        segs = []
    _set(uid, measure_version=MEASURE_VERSION)

    usage: dict = {"calls": 0}
    note = None
    if meta["kind"] == "video":
        if row["study_version"] < prompts.FOOTAGE_VERSION and row["study_version"] > 0:
            (d / "notes.json").unlink(missing_ok=True)  # notes from an older prompt: log again (marks are kept)
        say("AI is logging the footage", "studying")
        try:
            usage = fm.describe([("f1", uid)], "balanced", limit=MAX_AI_SEGMENTS)
        except ai.RateLimited:
            _set(uid, status="queued", progress="Waiting for AI quota")
            raise
        if meta.get("has_audio"):
            try:
                fm.classify([uid])  # footage or sound? cached on the file
            except ai.RateLimited:
                raise
            except Exception:  # noqa: BLE001 - a role guess is optional
                pass
            _transcript(uid, say)
        notes = _load(d / "notes.json", {}) or {}
        missing = [s for s in fm.sample(segs, MAX_AI_SEGMENTS) if str(s["k"]) not in notes]
        if missing:
            note = f"The AI could not log {len(missing)} of {len(segs)} segment(s); planning uses the measurements for those."
    write_digest(uid)
    status = "partial" if note else "ready"
    # stamped even when partial, so a file the AI can't log isn't re-sent on every start (Restudy tries again)
    _set(uid, status=status, progress=None, error=note, study_version=prompts.FOOTAGE_VERSION)
    return usage


def _transcript(uid: str, say) -> None:
    """Words spoken in the clip, when there is speech and the captions extra is installed (never installed here)."""
    fm = _footage()
    d = fm._dir(uid)
    if (d / "transcript.json").exists():
        return
    measured = _load(d / "measure.json", {}) or {}
    from .sound import SPEECH

    if not any((m.get("voice") or 0) >= SPEECH for m in measured.values()):
        return
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return
    meta = fm.meta_full(uid)
    if float(meta.get("duration") or 0) > TRANSCRIPT_MAX_S:
        return
    say("Transcribing speech")
    try:
        from .ops import captions

        words = captions.transcribe(Path(meta["path"]))
        (d / "transcript.json").write_text(json.dumps([{"text": w.text, "start": w.start, "end": w.end} for w in words]))
    except Exception:  # noqa: BLE001 - a transcript helps; never fail the study over it
        pass


def study_now(uid: str) -> str | None:
    """Study a file in this process if it is still waiting (the CLI, with no app running its job queue).
    Returns a note when the file can't be studied here (a worker is already on it)."""
    row = _row(uid) or register(uid, study=False)
    if row["status"] in ("ready", "partial", "failed") and not needs_study(row):
        return None
    j = jobs.for_target(f"media:{uid}")
    if j and j["status"] == "running" and time.time() - j["updated_at"] < 600:
        return "Being studied right now by the app; run this again in a minute for the full notes."
    jobs.cancel(f"media:{uid}")
    study(uid)
    return None


def write_digest(uid: str) -> dict:
    """The file in a few facts, made by code from the measurements + AI notes: the planner's first look at it."""
    fm = _footage()
    d = fm._dir(uid)
    meta = fm.meta_full(uid)
    row = _row(uid) or {}
    dg: dict[str, Any] = {"kind": meta["kind"], "name": meta["name"], "duration": meta.get("duration")}
    w, h = meta.get("width"), meta.get("height")
    if w and h:
        dg["orientation"] = "portrait" if h > w * 1.1 else "landscape" if w > h * 1.1 else "square"
    if meta["kind"] == "video":
        segs = fm.segments(uid)
        notes = apply_overrides(_load(d / "notes.json", {}) or {}, json.loads(row.get("overrides") or "{}"))
        measured = _load(d / "measure.json", {}) or {}
        from .sound import SPEECH

        usable = [s for s in segs if (notes.get(str(s["k"])) or {}).get("quality", 3) >= 3
                  and (measured.get(str(s["k"])) or {}).get("luma", 0.5) >= 0.08]
        dg["segments"] = len(segs)
        dg["usable_s"] = round(sum(s["to"] - s["from"] for s in usable), 1)
        ranked = sorted(segs, key=lambda s: -(notes.get(str(s["k"])) or {}).get("quality", 0))
        dg["best"] = [s["k"] for s in ranked[:BEST_N] if str(s["k"]) in notes]
        subjects = Counter(n.get("subject") for n in notes.values() if n.get("subject"))
        dg["subjects"] = [s for s, _ in subjects.most_common(3)]
        roles = Counter(r for n in notes.values() for r in n.get("good_for") or [])
        dg["roles"] = [r for r, _ in roles.most_common()]
        dg["summary"] = "; ".join(dict.fromkeys((notes.get(str(s["k"])) or {}).get("description", "")
                                                for s in ranked[:3] if str(s["k"]) in notes))[:240]
        motions = [m["motion"] for m in measured.values() if "motion" in m]
        if motions:
            avg = sum(motions) / len(motions)
            dg["energy"] = "static" if avg < 0.01 else "high" if avg > 0.06 else "medium"
        voice = [m for m in measured.values() if (m.get("voice") or 0) >= SPEECH]
        talking = sum(1 for n in notes.values() if n.get("talking"))
        dg["speech"] = bool(voice) or talking > 0
        tr = _load(d / "transcript.json")
        if tr:
            dg["transcript"] = " ".join(x["text"].strip() for x in tr)[:400]
        if meta.get("has_audio"):
            dg["has_audio"] = True
            if meta.get("role_hint"):
                dg["role"] = meta["role_hint"].get("role")
    elif meta["kind"] == "audio":
        dg["bpm"] = meta.get("bpm")
        dg["beats"] = len(meta.get("beats") or [])
    if row.get("tags"):
        dg["tags"] = json.loads(row["tags"])
    (d / "digest.json").write_text(json.dumps(dg))
    return dg


def digest(uid: str) -> dict:
    d = _footage()._dir(uid)
    return _load(d / "digest.json") or write_digest(uid)


def apply_overrides(notes: dict, overrides: dict) -> dict:
    """The user's corrections win over the AI's notes, segment by segment, and survive a re-study."""
    if not overrides:
        return notes
    out = {k: dict(v) for k, v in notes.items()}
    for k, ov in overrides.items():
        out[str(k)] = {**out.get(str(k), {"description": "", "quality": 3}), **ov}
    return out


def overrides(uid: str) -> dict:
    row = _row(uid)
    return json.loads(row["overrides"] or "{}") if row else {}


library_store.study_job("media_study", lambda t: t.split(":", 1)[1], lambda uid, job: study(uid, job), _set)


# ---- browsing / editing ------------------------------------------------------------------------

def _public(row: dict) -> dict:
    out = dict(row)
    out["tags"] = json.loads(row["tags"] or "[]")
    out["overrides"] = json.loads(row["overrides"] or "{}")
    return out


def get(uid: str, full: bool = False) -> dict:
    row = _row(uid) or register(uid)
    out = _footage().get(uid) | _public(row)  # the library's name wins (the user may have renamed it)
    out["digest"] = _load(_footage()._dir(uid) / "digest.json")
    with closing(_db()) as con:
        out["used_in"] = [dict(r) for r in con.execute(
            "SELECT kind, target, used_at FROM media_usage WHERE media_id = ? ORDER BY used_at DESC", (uid,)).fetchall()]
    if full and out["kind"] == "video":
        out["segments"] = _footage().shot_log([("f1", uid)])["f1"]["segments"]
    return out


def list_media(kind: str | None = None, q: str | None = None, tag: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM media", []
    if kind:
        sql += " WHERE kind = ?"
        args.append(kind)
    sql += " ORDER BY COALESCE(last_used_at, created_at) DESC"
    with closing(_db()) as con:
        rows = [_public(dict(r)) for r in con.execute(sql, args).fetchall()]
        used = Counter(r["media_id"] for r in con.execute("SELECT media_id FROM media_usage").fetchall())
    out = []
    for r in rows:
        try:
            r = _footage().get(r["id"]) | r
        except VeditError:  # folder removed by hand: list it so it can be deleted
            r["missing"] = True
        r["digest"] = _load(_footage()._dir(r["id"]) / "digest.json")
        r["uses"] = used.get(r["id"], 0)
        r["search"] = _search_text(r)
        if tag and tag not in r["tags"]:
            continue
        if q and q.lower() not in " ".join([r["name"], " ".join(r["tags"]), r["search"]]).lower():
            continue
        out.append(r)
    return out


def _search_text(row: dict) -> str:
    """Everything findable about a file: every shot's note (with the user's edits), subjects, roles, transcript."""
    d = _footage()._dir(row["id"])
    notes = apply_overrides(_load(d / "notes.json", {}) or {}, row["overrides"] if isinstance(row["overrides"], dict) else json.loads(row["overrides"] or "{}"))
    dg = row.get("digest") or {}
    bits = [n.get("description", "") for n in notes.values()] + [n.get("subject", "") for n in notes.values()]
    bits += [" ".join(n.get("tags") or []) for n in notes.values()] + dg.get("roles", []) + [dg.get("transcript", "")]
    return " ".join(dict.fromkeys(b for b in bits if b))


def update(uid: str, name: str | None = None, tags: list[str] | None = None, segment_overrides: dict | None = None) -> dict:
    row = _row(uid) or register(uid)
    kw: dict[str, Any] = {}
    if name is not None:
        kw["name"] = name.strip()[:120] or row["name"]
    if tags is not None:
        kw["tags"] = json.dumps(sorted({t.strip().lower() for t in tags if t.strip()}))
    if segment_overrides is not None:
        merged = json.loads(row["overrides"] or "{}")
        for k, v in segment_overrides.items():
            if v is None:
                merged.pop(str(k), None)
            else:
                merged[str(k)] = {**merged.get(str(k), {}), **v}
        kw["overrides"] = json.dumps(merged)
    if kw:
        _set(uid, **kw)
        write_digest(uid)
    return get(uid)


def restudy(uid: str) -> dict:
    """Study the file again from scratch (measure + AI notes). Reviewer marks and user overrides are kept."""
    _row(uid) or register(uid, study=False)
    _set(uid, measure_version=0, study_version=0)
    d = _footage()._dir(uid)
    for f in ("notes.json", "digest.json", "transcript.json"):
        (d / f).unlink(missing_ok=True)
    queue(uid)
    return get(uid)


def record_use(uids: list[str], kind: str, target: str) -> None:
    now = time.time()
    with closing(_db()) as con, con:
        for uid in dict.fromkeys(uids):
            con.execute("INSERT OR REPLACE INTO media_usage (media_id, kind, target, used_at) VALUES (?, ?, ?, ?)", (uid, kind, target, now))
            con.execute("UPDATE media SET last_used_at = ? WHERE id = ?", (now, uid))


def delete(uid: str, force: bool = False) -> None:
    """Remove a file from the library. Refused while projects or batches use it, unless forced."""
    import shutil

    row = _row(uid)
    if row is None:
        raise VeditError(f"No media {uid}")
    with closing(_db()) as con:
        uses = con.execute("SELECT COUNT(*) FROM media_usage WHERE media_id = ?", (uid,)).fetchone()[0]
    if uses and not force:
        raise VeditError(f"“{row['name']}” is used in {uses} project(s) or batch(es). Remove it anyway with force.")
    jobs.cancel(f"media:{uid}")
    d = _footage()._dir(uid)
    if d.exists() and d.parent == _footage().root():
        shutil.rmtree(d, ignore_errors=True)
    with closing(_db()) as con, con:
        con.execute("DELETE FROM media WHERE id = ?", (uid,))
        con.execute("DELETE FROM media_usage WHERE media_id = ?", (uid,))


def disk_usage() -> int:
    with closing(_db()) as con:
        return int(con.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM media").fetchone()[0])


# ---- projects (vedit import, the editor) ------------------------------------------------------------

def id_for_path(path: str | Path) -> str | None:
    """The library id of a stored copy (~/.aiklips/uploads/<id>/file.ext), None for any other file."""
    p = Path(path)
    try:
        if p.parent.parent.resolve() == _footage().root().resolve() and (p.parent / "meta.json").exists():
            return p.parent.name
    except OSError:
        return None
    return None


def store_for_project(pdir: Path, path: Path, name: str | None = None, move: bool = False) -> Path:
    """Bring a file a project imports into the library (studied once, in the background) and return the stored
    copy to point the project at. A type the library doesn't take stays where it is."""
    fm = _footage()
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() not in fm.VIDEO | fm.AUDIO | fm.IMAGE:
        return path
    meta = fm.add(path, name or path.name, move=move)
    record_use([meta["id"]], "project", str(Path(pdir).resolve()))
    return Path(meta["path"])


def project_media(pdir: Path, project) -> dict[str, dict]:
    """{source id: short digest + status} for the project's sources that are in the library."""
    from .project import resolve_media

    out: dict[str, dict] = {}
    for sid, s in project.sources.items():
        try:
            uid = id_for_path(resolve_media(Path(pdir), s.path))
        except Exception:  # noqa: BLE001 - a missing file is the editor's problem, not this summary's
            uid = None
        if not uid:
            continue
        row = _row(uid) or {}
        dg = _load(_footage()._dir(uid) / "digest.json") or {}
        out[sid] = {"media": uid, "status": row.get("status", "unknown"), "tags": json.loads(row.get("tags") or "[]"),
                    **{k: dg[k] for k in ("usable_s", "energy", "speech", "subjects", "summary", "transcript", "bpm", "orientation") if k in dg}}
    return out


def media_line(sid: str, name: str, m: dict) -> str:
    """One line for the chat AI's context: what is in a source, from its study."""
    if m["status"] not in ("ready", "partial"):
        return f"  {sid}{' ' + name if name else ''}: being studied ({m['status']})"
    facts = [f"{m['usable_s']}s usable" if "usable_s" in m else "", f"energy {m['energy']}" if m.get("energy") else "",
             "speech" if m.get("speech") else "", f"{m['bpm']:.0f} BPM" if m.get("bpm") else "",
             "subjects: " + ", ".join(m["subjects"]) if m.get("subjects") else "", "tags: " + ", ".join(m["tags"]) if m.get("tags") else ""]
    line = f"  {sid}{' ' + name if name else ''}: " + "; ".join(x for x in facts if x)
    if m.get("summary"):
        line += f"; best shots: {m['summary'][:160]}"
    if m.get("transcript"):
        line += f"; says: “{m['transcript'][:120]}”"
    return line

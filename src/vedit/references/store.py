"""References: a role a VIDEO in the media library can have. The file lives once, in the media library
(~/.aiklips/uploads/<media id>/file.<ext>); making it a reference adds a style study next to it.

Files: ~/.aiklips/uploads/<media id>/style/
    metrics.json   measured by code (references.analysis)
    thumb.jpg, sheet-1.jpg, sheet-2.jpg, sheet-3.jpg, frames/, words.json
    dna.json       Style DNA (AI + measured), before the user's overrides

The `refs` row keeps its integer id (projects, batches and chat messages point at it) plus `media_id`.
Older references (their own copy in ~/.aiklips/references/<sha16>/) are moved into the media library once by
`migrate_to_media()`, keeping their DNA.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from .. import jobs, library, library_store
from ..ai import client as ai
from ..ai import prompts
from ..project import VeditError
from . import analysis, dna as dnamod, recipe as recipemod

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

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}  # the same set as generate.footage.VIDEO
_SCHEMA_OK: set[str] = set()


def _db():
    con = library._db()
    con.executescript(SCHEMA)
    key = str(library.home_dir())
    if key not in _SCHEMA_OK:  # media_id came later: add it to an older library.db
        cols = {r[1] for r in con.execute("PRAGMA table_info(refs)").fetchall()}
        if "media_id" not in cols:
            con.execute("ALTER TABLE refs ADD COLUMN media_id TEXT")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS refs_media ON refs(media_id)")
        con.commit()
        _SCHEMA_OK.add(key)
    return con


def _footage():
    from ..generate import footage  # imported late: footage imports this module

    return footage


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
    """The video itself: the media library's copy (older, not yet migrated references: their own copy)."""
    if row.get("media_id"):
        try:
            return Path(_footage().meta_full(row["media_id"])["path"])
        except VeditError:
            pass
    d = Path(row["dir"])
    return next((p for p in d.glob("source.*")), d / "source.mp4")


# ---- add / list / get --------------------------------------------------------------------

def add(path: Path, name: str | None = None, study: bool = True, move: bool = False) -> dict:
    """Add a video to the media library (deduplicated by content) and make it a reference (queues the style study)."""
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise VeditError(f"File not found: {path}")
    if path.suffix.lower() not in VIDEO_EXT:
        raise VeditError(f"{path.name}: references must be videos ({', '.join(sorted(VIDEO_EXT))})")
    meta = _footage().add(path, path.name if name is None else name, move=move)
    return promote(meta["id"], name=name, study=study)


def for_media(media_id: str) -> int | None:
    """The reference id of a media item, None when it isn't a reference."""
    with closing(_db()) as con:
        r = con.execute("SELECT id FROM refs WHERE media_id = ?", (media_id,)).fetchone()
    return int(r["id"]) if r else None


def promote(media_id: str, name: str | None = None, study: bool = True) -> dict:
    """Make a video in the media library a reference: no copy, a style study next to the file. Idempotent."""
    fm = _footage()
    meta = fm.meta_full(media_id)
    if meta.get("kind") != "video":
        raise VeditError(f"“{meta.get('name', media_id)}” is {meta.get('kind') or 'not a video'}: only videos can be references")
    rid = for_media(media_id)
    if rid is not None:
        return get(rid)
    migrate_to_media()  # an older reference of the same file: adopt it (keeps its id and DNA) instead of a second row
    rid = for_media(media_id)
    if rid is not None:
        return get(rid)
    from .. import media_library

    mrow = media_library._row(media_id) or {}
    label = (name or Path(mrow.get("name") or meta["name"]).stem)[:120]
    d = fm._dir(media_id) / "style"
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    with closing(_db()) as con, con:
        cur = con.execute("INSERT INTO refs (sha, name, dir, media_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                          (f"media:{media_id}", label, str(d), media_id, now, now))
        rid = int(cur.lastrowid)
    if study:
        jobs.enqueue("study", f"ref:{rid}")
    return get(rid)


def resolve(token: Any, pdir: Path | None = None) -> int:
    """A reference id from what a user may type or pick: a reference id (12 / "12" / "#12"), a media id, a project
    source id (sN, with pdir), or a video file (added to the media library). Media becomes a reference on the way."""
    t = str(token).strip().lstrip("#")
    if t.isdigit():
        _row(int(t))
        return int(t)
    if len(t) == 16 and all(c in "0123456789abcdef" for c in t):
        return int(promote(t)["id"])
    if pdir is not None and t[:1] == "s" and t[1:].isdigit():
        from .. import media_library
        from ..project import load, resolve_media

        proj = load(Path(pdir))
        if t not in proj.sources:
            raise VeditError(f"No source {t} in this project")
        path = resolve_media(Path(pdir), proj.sources[t].path)
        uid = media_library.id_for_path(path)
        if uid is None:  # imported with --no-library / --copy: bring it in now
            return int(add(path)["id"])
        return int(promote(uid)["id"])
    p = Path(t).expanduser()
    if p.exists():
        return int(add(p)["id"])
    raise VeditError(f"No reference, media or file called {token!r}")


_load_json = library_store.load_json


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
            row["recipe"] = recipemod.build(style, m, rid)
            row["gaps"] = recipemod.gaps(style)
            row["sheets"] = sheet_paths(rid)
    if full and m:
        a = m.get("audio") or {}
        row["metrics"] = {
            "duration": m["duration"], "width": m.get("width"), "height": m.get("height"), "fps": m.get("fps"),
            "cuts": m["cuts"], "pacing": m["pacing"], "motion": m.get("motion"), "first_cut_s": m.get("first_cut_s"),
            "cuts_on_beat": m.get("cuts_on_beat"), "cuts_on_beat_chance": m.get("cuts_on_beat_chance"),
            "audio": None if not a else {k: a.get(k) for k in ("bpm", "tempo_confidence", "beats", "onsets", "rms", "rms_step", "silence_ratio")},
            "transcript": m.get("transcript"), "sheets": m.get("sheets", []), "color": m.get("color"),
        }
    if full:
        row["source"] = str(source_path(row))
    job = jobs.for_target(f"ref:{rid}")
    row["job"] = None if not job else {"status": job["status"], "note": job["note"]}
    return row


def color_of(rid: int) -> dict:
    """The reference's measured colour (analysis.color_profile). Older references are measured
    now and saved, so they work without a re-study."""
    row = _row(rid)
    d = Path(row["dir"])
    m = _load_json(d / "metrics.json") or {}
    if not m.get("color"):
        m["color"] = analysis.color_profile(source_path(row), float(m.get("duration") or 0) or None)
        if (d / "metrics.json").exists():
            (d / "metrics.json").write_text(json.dumps(m))
    return m["color"]


def palette_of(rid: int) -> dict:
    """The reference's palette (analysis.palette), measured once and saved with its metrics."""
    row = _row(rid)
    d = Path(row["dir"])
    m = _load_json(d / "metrics.json") or {}
    if "palette" not in m:
        try:
            m["palette"] = analysis.palette(source_path(row), float(m.get("duration") or 0) or None)
        except (VeditError, OSError):
            m["palette"] = None
        if (d / "metrics.json").exists():
            (d / "metrics.json").write_text(json.dumps(m))
    return m["palette"] or {}


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
    """Stop using a video as a reference: its style study goes, the file stays in the media library."""
    row = _row(rid)
    jobs.cancel(f"ref:{rid}")
    d = Path(row["dir"])
    fm = _footage()
    ours = d.parent == root() or (row.get("media_id") and d == fm.root() / row["media_id"] / "style")
    if d.exists() and ours:
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


# ---- migration: references used to keep their own copy ---------------------------------------------

STYLE_FILES = ("metrics.json", "dna.json", "thumb.jpg", "words.json")


def migrate_to_media() -> int:
    """Move every older reference's video into the media library (deduplicated: a file already there is reused)
    and its style study into uploads/<media id>/style/. The DNA is kept, so nothing is studied again. Idempotent."""
    with closing(_db()) as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM refs WHERE media_id IS NULL").fetchall()]
    fm = _footage()
    n = 0
    for row in rows:
        old = Path(row["dir"])
        src = next((p for p in old.glob("source.*")), None) if old.exists() else None
        if src is None:
            continue  # nothing to move (folder gone): leave the row for the user to remove
        try:
            meta = fm.add(src, row["name"] + src.suffix.lower(), move=False)
        except VeditError:
            continue
        mid = meta["id"]
        if for_media(mid) is not None:  # two old references of one file: keep the first, this one stays as it was
            continue
        new = fm._dir(mid) / "style"
        new.mkdir(parents=True, exist_ok=True)
        for f in old.iterdir():
            if f.name.startswith("source."):
                continue
            dest = new / f.name
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True) if dest.is_dir() else dest.unlink()
            shutil.move(str(f), dest)
        with closing(_db()) as con, con:
            con.execute("UPDATE refs SET media_id = ?, dir = ?, updated_at = ? WHERE id = ?", (mid, str(new), time.time(), row["id"]))
        if old.parent == root():
            shutil.rmtree(old, ignore_errors=True)
        n += 1
    return n


def _shared_inputs(row: dict) -> tuple[list[float] | None, list[dict] | None]:
    """What the media study already measured on the same file: its raw cuts and its transcript (a better whisper
    model than the style study's), so they aren't measured twice."""
    mid = row.get("media_id")
    if not mid:
        return None, None
    d = _footage()._dir(mid)
    cuts = _load_json(d / "cuts.json")
    tr = _load_json(d / "transcript.json")
    words = [{"t": round(float(w["start"]), 2), "w": w["text"]} for w in tr] if isinstance(tr, list) and tr else None
    return (cuts if isinstance(cuts, list) else None), words


# ---- studying ---------------------------------------------------------------------------------

def backfill(d: Path, src: Path, metrics: dict, say=lambda _s: None) -> dict:
    """Measurements added since a reference was first analysed (colour, the whole-video sheet, the palette), made
    now and saved, so an old reference doesn't need a full re-analysis."""
    changed = False
    dur = float(metrics.get("duration") or 0)
    if not metrics.get("color"):
        say("Measuring colour")
        metrics["color"] = analysis.color_profile(src, dur or None)
        changed = True
    if "sheet-3.jpg" not in metrics.get("sheets", []) and dur > 3.5:
        say("Making the whole-video sheet")
        tl = analysis.make_timeline_sheet(src, dur, d, metrics.get("cuts"), metrics.get("motion_energy"),
                                          (metrics.get("motion") or {}).get("fps") or 10)
        if tl:
            metrics["sheets"] = [*metrics.get("sheets", []), tl]
            changed = True
    if "palette" not in metrics:
        say("Measuring the palette")
        try:
            metrics["palette"] = analysis.palette(src, dur or None)
        except (VeditError, OSError):
            metrics["palette"] = None
        changed = True
    if changed:
        (d / "metrics.json").write_text(json.dumps(metrics))
    return metrics


def sheet_paths(rid: int) -> list[str]:
    """The reference's contact sheets (absolute paths), made if missing: what it actually looks like."""
    row = _row(rid)
    d = Path(row["dir"])
    m = _load_json(d / "metrics.json")
    if m is None:
        return []
    try:
        m = backfill(d, source_path(row), m)
    except (VeditError, OSError):
        pass
    return [str(d / s) for s in m.get("sheets", []) if (d / s).exists()]


def restudy_stale() -> list[int]:
    """Queue a re-study of every AI-studied reference made with an older study prompt (called when the app starts)."""
    out = []
    with closing(_db()) as con:
        rows = con.execute("SELECT id FROM refs WHERE status = 'ready' AND ai = 1 AND COALESCE(prompt_version, 0) < ?",
                           (prompts.PROMPT_VERSION,)).fetchall()
    for r in rows:
        restudy(r["id"])  # enqueue skips one already queued
        out.append(r["id"])
    return out

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
        cuts, words = _shared_inputs(row)
        metrics = analysis.analyze(src, d, say, cuts=cuts, words=words)
    else:
        backfill(d, src, metrics, say)
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


library_store.study_job("study", lambda t: int(t.split(":")[1]), lambda rid, job: study(rid, job), _set)

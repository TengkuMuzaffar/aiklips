"""Durable background jobs (studying references, generating variations).

Jobs live in the library's SQLite file, so a batch survives closing the app: on the next
start, anything that was running is put back in the queue. Two worker threads run jobs;
the AI client separately limits concurrent LLM calls, and renders take a global lock.

When the AI is rate-limited past its own retries, the job is re-queued with a delay
("Waiting for AI quota") instead of failing.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from contextlib import closing
from typing import Callable

from . import library

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    target     TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | failed | cancelled
    attempts   INTEGER NOT NULL DEFAULT 0,
    not_before REAL NOT NULL DEFAULT 0,
    note       TEXT,
    error      TEXT,
    usage      TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, not_before);
"""

WORKERS = 2
AUTOSTART = True  # tests turn this off and call drain() instead
MAX_RATE_WAITS = 12
render_lock = threading.Lock()  # one render / export at a time across all jobs

_handlers: dict[str, Callable[["Job"], dict | None]] = {}
_threads: list[threading.Thread] = []
_wake = threading.Event()
_started = threading.Lock()
_recovered = False


class Cancelled(Exception):
    pass


class Retry(Exception):
    """Not ready yet (e.g. waiting for references to be studied): run this job again later."""

    def __init__(self, delay: float = 10.0, note: str | None = None):
        super().__init__(note or "retry")
        self.delay, self.note = delay, note


def _db():
    con = library._db()
    con.executescript(SCHEMA)
    return con


def handler(kind: str):
    def deco(fn):
        _handlers[kind] = fn
        return fn
    return deco


class Job:
    def __init__(self, row: dict):
        self.id, self.kind, self.target = row["id"], row["kind"], row["target"]
        self.attempts = row["attempts"]

    def cancelled(self) -> bool:
        with closing(_db()) as con:
            r = con.execute("SELECT status FROM jobs WHERE id = ?", (self.id,)).fetchone()
        return bool(r) and r["status"] == "cancelled"

    def check(self) -> None:
        if self.cancelled():
            raise Cancelled()

    def note(self, text: str) -> None:
        with closing(_db()) as con, con:
            con.execute("UPDATE jobs SET note = ?, updated_at = ? WHERE id = ?", (text, time.time(), self.id))


def enqueue(kind: str, target: str, delay: float = 0.0, start: bool = True) -> int:
    now = time.time()
    with closing(_db()) as con, con:
        row = con.execute("SELECT id FROM jobs WHERE kind = ? AND target = ? AND status IN ('queued','running')", (kind, target)).fetchone()
        if row:
            return int(row["id"])
        cur = con.execute("INSERT INTO jobs (kind, target, not_before, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                          (kind, target, now + delay, now, now))
        jid = int(cur.lastrowid)
    if start and AUTOSTART:
        ensure_workers()
    _wake.set()
    return jid


def cancel(target_prefix: str) -> int:
    with closing(_db()) as con, con:
        cur = con.execute("UPDATE jobs SET status = 'cancelled', updated_at = ? WHERE target LIKE ? AND status IN ('queued','running')",
                          (time.time(), target_prefix + "%"))
        return cur.rowcount


def for_target(target: str) -> dict | None:
    with closing(_db()) as con:
        r = con.execute("SELECT * FROM jobs WHERE target = ? ORDER BY id DESC LIMIT 1", (target,)).fetchone()
    return dict(r) if r else None


def pending() -> int:
    with closing(_db()) as con:
        return int(con.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0])


def recover() -> None:
    """After a restart nothing is really running: put interrupted jobs back in the queue."""
    with closing(_db()) as con, con:
        con.execute("UPDATE jobs SET status = 'queued', updated_at = ? WHERE status = 'running'", (time.time(),))


def _claim() -> dict | None:
    now = time.time()
    with closing(_db()) as con, con:
        row = con.execute("SELECT * FROM jobs WHERE status = 'queued' AND not_before <= ? ORDER BY not_before, id LIMIT 1", (now,)).fetchone()
        if not row:
            return None
        cur = con.execute("UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_at = ? WHERE id = ? AND status = 'queued'",
                          (now, row["id"]))
        if cur.rowcount != 1:
            return None
        return dict(row) | {"attempts": row["attempts"] + 1}


def _finish(jid: int, status: str, error: str | None = None, usage: dict | None = None, delay: float | None = None, note: str | None = None) -> None:
    now = time.time()
    with closing(_db()) as con, con:
        cur = con.execute("SELECT status FROM jobs WHERE id = ?", (jid,)).fetchone()
        if cur and cur["status"] == "cancelled":
            return
        con.execute("UPDATE jobs SET status = ?, error = ?, usage = COALESCE(?, usage), not_before = COALESCE(?, not_before), "
                    "note = COALESCE(?, note), updated_at = ? WHERE id = ?",
                    (status, error, json.dumps(usage) if usage else None, None if delay is None else now + delay, note, now, jid))


def run_one(row: dict) -> None:
    from .ai.client import AIError, RateLimited

    job = Job(row)
    fn = _handlers.get(job.kind)
    if fn is None:
        _finish(job.id, "failed", f"No handler for job kind {job.kind}")
        return
    try:
        usage = fn(job)
        _finish(job.id, "done", usage=usage or None)
    except Cancelled:
        _finish(job.id, "cancelled")
    except Retry as e:
        with closing(_db()) as con, con:  # waiting isn't an attempt
            con.execute("UPDATE jobs SET attempts = attempts - 1 WHERE id = ?", (job.id,))
        _finish(job.id, "queued", delay=e.delay, note=e.note)
    except RateLimited as e:
        if job.attempts >= MAX_RATE_WAITS:
            _finish(job.id, "failed", f"AI quota still exhausted after {job.attempts} tries: {e}")
            _on_fail(job, str(e))
        else:
            wait = min(1800.0, e.retry_after * job.attempts)
            _finish(job.id, "queued", delay=wait, note=f"Waiting for AI quota (retry in {int(wait // 60) or 1} min)")
            _on_wait(job, wait)
    except AIError as e:
        _finish(job.id, "failed", str(e))
        _on_fail(job, str(e))
    except Exception as e:
        tb = traceback.format_exc(limit=6)
        _finish(job.id, "failed", f"{type(e).__name__}: {e}\n{tb}"[:4000])
        _on_fail(job, f"{type(e).__name__}: {e}")


_fail_hooks: dict[str, Callable[[Job, str], None]] = {}
_wait_hooks: dict[str, Callable[[Job, float], None]] = {}


def on_fail(kind: str):
    def deco(fn):
        _fail_hooks[kind] = fn
        return fn
    return deco


def on_wait(kind: str):
    def deco(fn):
        _wait_hooks[kind] = fn
        return fn
    return deco


def _on_fail(job: Job, msg: str) -> None:
    try:
        if job.kind in _fail_hooks:
            _fail_hooks[job.kind](job, msg)
    except Exception:
        pass


def _on_wait(job: Job, wait: float) -> None:
    try:
        if job.kind in _wait_hooks:
            _wait_hooks[job.kind](job, wait)
    except Exception:
        pass


def _worker() -> None:
    while True:
        row = _claim()
        if row is None:
            _wake.wait(timeout=5)
            _wake.clear()
            continue
        run_one(row)


def ensure_workers() -> None:
    global _recovered
    _load_handlers()
    with _started:
        if not _recovered:
            recover()
            _recovered = True
        alive = [t for t in _threads if t.is_alive()]
        _threads[:] = alive
        for i in range(WORKERS - len(alive)):
            t = threading.Thread(target=_worker, name=f"vedit-job-{i}", daemon=True)
            t.start()
            _threads.append(t)


def drain(timeout: float = 600, wait: bool = False) -> None:
    """Run queued jobs in this thread until none are due (tests), or with wait=True until none are left (CLI)."""
    _load_handlers()
    end = time.time() + timeout
    while time.time() < end:
        row = _claim()
        if row is not None:
            run_one(row)
            continue
        if not wait:
            return
        with closing(_db()) as con:
            nxt = con.execute("SELECT MIN(not_before) FROM jobs WHERE status = 'queued'").fetchone()[0]
        if nxt is None:
            return
        time.sleep(max(0.2, min(nxt - time.time(), end - time.time(), 60)))


def _load_handlers() -> None:
    from .generate import batch  # noqa: F401  (registers handlers)
    from .references import store  # noqa: F401

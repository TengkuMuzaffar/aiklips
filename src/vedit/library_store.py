"""Plumbing shared by the two studies kept on library files: the media study (media_library, what is in each shot)
and the style study (references.store, how the video is made). Both are rows in library.db with a status, run as
background jobs on targets like "media:<id>" / "ref:<id>", and write JSON next to the file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from . import jobs


def load_json(p: Path, default: Any = None) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def study_job(kind: str, parse: Callable[[str], Any], run: Callable[[Any, jobs.Job], dict],
              set_status: Callable[..., None]) -> None:
    """Register a study job kind: `run(id, job)` does the work; a failure or an AI-quota wait shows on the row
    through `set_status(id, status=..., progress=..., error=...)`. `parse` turns the job target into the id."""

    @jobs.handler(kind)
    def _run(job: jobs.Job) -> dict:
        return run(parse(job.target), job)

    @jobs.on_fail(kind)
    def _failed(job: jobs.Job, msg: str) -> None:
        set_status(parse(job.target), status="failed", progress=None, error=msg[:500])

    @jobs.on_wait(kind)
    def _wait(job: jobs.Job, wait: float) -> None:
        set_status(parse(job.target), status="queued", progress=f"Waiting for AI quota (~{int(wait // 60) or 1} min)")

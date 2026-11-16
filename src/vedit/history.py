"""Undo / redo history.

Every edit writes a full snapshot to .vedit/history/NNNN.json and moves HEAD.
Undo/redo/checkout move HEAD and restore project.json from that snapshot.
A new edit after an undo discards the redo branch (like most editors).
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .project import PROJECT_FILE, Project, VeditError, dump_json, load, save, state_dir

MAX_SNAPSHOTS = 500
COALESCE_SECONDS = 1.5  # repeated edits of the same field this close together become one undo step


def _hdir(pdir: Path) -> Path:
    d = state_dir(pdir) / "history"
    d.mkdir(exist_ok=True)
    return d


def _snap_path(pdir: Path, n: int) -> Path:
    return _hdir(pdir) / f"{n:04d}.json"


def _indices(pdir: Path) -> list[int]:
    return sorted(int(p.stem) for p in _hdir(pdir).glob("[0-9][0-9][0-9][0-9].json"))


def head(pdir: Path) -> int:
    f = _hdir(pdir) / "HEAD"
    if f.exists():
        return int(f.read_text().strip())
    idx = _indices(pdir)
    return idx[-1] if idx else -1


def _set_head(pdir: Path, n: int) -> None:
    (_hdir(pdir) / "HEAD").write_text(str(n))


def _read(pdir: Path, n: int) -> dict:
    p = _snap_path(pdir, n)
    if not p.exists():
        raise VeditError(f"History step {n} does not exist")
    return json.loads(p.read_text())


def commit(pdir: Path, project: Project, message: str, coalesce: str | None = None) -> int:
    """`coalesce` is a key naming the field being edited (e.g. "layer_set:t1:style.size"). When the newest step has
    the same key and was written moments ago (a slider drag, typing in a number box), it is replaced instead of
    adding a step, so one undo reverts the whole gesture."""
    h = head(pdir)
    idx = _indices(pdir)
    now = time.time()
    if coalesce and h >= 1 and h == (idx[-1] if idx else -1):
        try:
            prev = _read(pdir, h)
        except (OSError, ValueError, VeditError):
            prev = {}
        if prev.get("coalesce") == coalesce and now - float(prev.get("time", 0)) < COALESCE_SECONDS:
            _snap_path(pdir, h).write_text(json.dumps(
                {"message": message, "time": now, "coalesce": coalesce, "project": project.to_dict()}, indent=1))
            return h
    for i in idx:
        if i > h:
            _snap_path(pdir, i).unlink()  # drop redo branch
    n = h + 1
    snap = {"message": message, "time": now, "project": project.to_dict()}
    if coalesce:
        snap["coalesce"] = coalesce
    _snap_path(pdir, n).write_text(json.dumps(snap, indent=1))
    _set_head(pdir, n)
    # trim very old history
    idx = _indices(pdir)
    for i in idx[: max(0, len(idx) - MAX_SNAPSHOTS)]:
        _snap_path(pdir, i).unlink()
    return n


def sync(pdir: Path) -> None:
    """If project.json was edited by hand, record it so undo can return to it."""
    h = head(pdir)
    current = load(pdir)
    if h < 0:
        commit(pdir, current, "initial state")
        return
    if not same_as(pdir, h, current):
        commit(pdir, current, "manual edit of project.json")


def _normal(data: dict) -> dict | None:
    """A snapshot as the CURRENT project format sees it: fields added since it was taken get their defaults,
    so a new setting (e.g. canvas.loudness) never looks like a hand edit."""
    try:
        return Project.model_validate(data).to_dict()
    except Exception:
        return None


def same_as(pdir: Path, n: int, current: Project | None = None) -> bool:
    """Is the project's content the same as history step n (ignoring fields added to the format since)?"""
    try:
        snap = _read(pdir, n)["project"]
    except (OSError, KeyError, ValueError):
        return False
    cur = (current or load(pdir)).to_dict()
    return snap == cur or _normal(snap) == cur


def _restore(pdir: Path, n: int) -> dict:
    snap = _read(pdir, n)
    project = Project.model_validate(snap["project"])
    (pdir / PROJECT_FILE).write_text(dump_json(project))
    _set_head(pdir, n)
    return snap


def undo(pdir: Path, steps: int = 1) -> dict:
    sync(pdir)
    h = head(pdir)
    idx = _indices(pdir)
    target = h - steps
    if not idx or target < idx[0]:
        raise VeditError("Nothing to undo")
    undone = _read(pdir, h)["message"]
    snap = _restore(pdir, target)
    return {"head": target, "undone": undone, "now_at": snap["message"]}


def redo(pdir: Path, steps: int = 1) -> dict:
    sync(pdir)
    h = head(pdir)
    target = h + steps
    if target not in _indices(pdir):
        raise VeditError("Nothing to redo")
    snap = _restore(pdir, target)
    return {"head": target, "redone": snap["message"]}


def checkout(pdir: Path, n: int) -> dict:
    sync(pdir)
    snap = _restore(pdir, n)
    return {"head": n, "now_at": snap["message"]}


def log(pdir: Path, limit: int | None = None) -> list[dict]:
    h = head(pdir)
    out = []
    idx = _indices(pdir)
    for i in (idx[-limit:] if limit else idx):
        s = _read(pdir, i)
        out.append({"step": i, "message": s["message"], "time": s["time"], "current": i == h})
    return out


class Tx:
    def __init__(self, project: Project, message: str):
        self.project = project
        self.message = message
        self.cancelled = False
        self.coalesce: str | None = None


@contextmanager
def transaction(pdir: Path, message: str) -> Iterator[Tx]:
    """Load, edit, validate, save and snapshot in one step.

    On any exception nothing is written.
    """
    sync(pdir)
    tx = Tx(load(pdir), message)
    yield tx
    if tx.cancelled:
        return
    save(pdir, tx.project)
    commit(pdir, tx.project, tx.message, tx.coalesce)

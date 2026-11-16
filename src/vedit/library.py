"""Project library: a small SQLite registry of all projects on this machine.

project.json (inside each project folder) stays the source of truth for the edit;
the library only remembers *where* projects are and when they were used, so the
home screen can list, open, rename, duplicate and remove them.

Database: ~/.claude-cut/library.db   (override with $VEDIT_HOME)
New projects: <repo>/projects/<slug>  (override with $VEDIT_PROJECTS_DIR)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .project import PROJECT_FILE, STATE_DIR, Project, VeditError, load, save

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    path       TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    opened_at  REAL,
    hidden     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

REPO_ROOT = Path(__file__).resolve().parents[2]
FORMATS = {"reels": (1080, 1920), "portrait45": (1080, 1350), "square": (1080, 1080), "landscape": (1920, 1080)}


def home_dir() -> Path:
    d = Path(os.environ.get("VEDIT_HOME") or Path.home() / ".claude-cut")
    d.mkdir(parents=True, exist_ok=True)
    return d


def projects_root() -> Path:
    if os.environ.get("VEDIT_PROJECTS_DIR"):
        d = Path(os.environ["VEDIT_PROJECTS_DIR"])
    elif (REPO_ROOT / "pyproject.toml").exists():
        d = REPO_ROOT / "projects"
    else:
        d = Path.home() / "claude-cut-projects"
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(home_dir() / "library.db", timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return s[:60] or "project"


def _unique_dir(root: Path, name: str) -> Path:
    base = root / slugify(name)
    d, n = base, 2
    while d.exists():
        d = base.with_name(f"{base.name}-{n}")
        n += 1
    return d


# ---- registry -------------------------------------------------------------------

def register(pdir: Path, name: str | None = None) -> int:
    """Add an existing project folder to the library (no-op if already there)."""
    pdir = pdir.resolve()
    if not (pdir / PROJECT_FILE).exists():
        raise VeditError(f"{pdir} is not a claude-cut project (no project.json)")
    if name is None:
        try:
            name = load(pdir).name
        except VeditError:
            name = pdir.name
    with closing(_db()) as con, con:
        row = con.execute("SELECT id FROM projects WHERE path = ?", (str(pdir),)).fetchone()
        if row:
            con.execute("UPDATE projects SET hidden = 0 WHERE id = ?", (row["id"],))  # explicitly opened again
            return row["id"]
        cur = con.execute("INSERT INTO projects (name, path, created_at) VALUES (?, ?, ?)", (name, str(pdir), time.time()))
        return int(cur.lastrowid)


def get(pid: int) -> dict:
    with closing(_db()) as con:
        row = con.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    if not row:
        raise VeditError(f"No project #{pid} in the library")
    return dict(row)


def find_by_path(pdir: Path) -> dict | None:
    with closing(_db()) as con:
        row = con.execute("SELECT * FROM projects WHERE path = ?", (str(pdir.resolve()),)).fetchone()
    return dict(row) if row else None


def touch(pdir: Path) -> None:
    """Mark a project as just opened (registering it if needed)."""
    pid = register(pdir)
    with closing(_db()) as con, con:
        con.execute("UPDATE projects SET opened_at = ? WHERE id = ?", (time.time(), pid))


def scan(root: Path | None = None) -> int:
    """Register any project folders under the projects root that aren't in the library yet."""
    root = root or projects_root()
    added = 0
    for pj in root.glob(f"*/{PROJECT_FILE}"):
        if not find_by_path(pj.parent):
            register(pj.parent)
            added += 1
    return added


def _stats(pdir: Path) -> dict:
    try:
        data = json.loads((pdir / PROJECT_FILE).read_text())
        p = Project.model_validate(data)
    except Exception:
        return {"missing": not (pdir / PROJECT_FILE).exists(), "broken": True}
    first = p.clips[0].source if p.clips else None
    return {
        "missing": False,
        "broken": False,
        "project_name": p.name,
        "duration": round(p.duration, 2),
        "clips": len(p.clips),
        "layers": len(p.layers),
        "width": p.canvas.width,
        "height": p.canvas.height,
        "fps": p.canvas.fps,
        "thumb_source": first,
        "updated_at": (pdir / PROJECT_FILE).stat().st_mtime,
    }


def list_projects(do_scan: bool = True) -> list[dict]:
    if do_scan:
        scan()
    with closing(_db()) as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM projects WHERE hidden = 0")]
    out = []
    for r in rows:
        st = _stats(Path(r["path"]))
        if st.get("project_name") and st["project_name"] != r["name"]:
            r["name"] = st["project_name"]  # project.json wins
            with closing(_db()) as con, con:
                con.execute("UPDATE projects SET name = ? WHERE id = ?", (r["name"], r["id"]))
        st.pop("project_name", None)
        out.append(r | st)
    out.sort(key=lambda r: max(r.get("opened_at") or 0, r.get("updated_at") or 0, r["created_at"]), reverse=True)
    return out


# ---- actions -----------------------------------------------------------------------

def create(name: str, fmt: str = "reels", fps: float = 30, root: Path | None = None) -> dict:
    from . import history

    name = name.strip() or "Untitled project"
    if fmt not in FORMATS:
        raise VeditError(f"format must be one of {', '.join(FORMATS)}")
    d = _unique_dir(root or projects_root(), name)
    d.mkdir(parents=True)
    (d / "media").mkdir()
    (d / "exports").mkdir()
    p = Project(name=name)
    p.canvas.width, p.canvas.height = FORMATS[fmt]
    p.canvas.fps = fps
    save(d, p)
    history.commit(d, p, "create project")
    pid = register(d, name)
    return get(pid)


def rename(pid: int, new_name: str) -> dict:
    from . import history

    new_name = new_name.strip()
    if not new_name:
        raise VeditError("Name can't be empty")
    row = get(pid)
    pdir = Path(row["path"])
    if (pdir / PROJECT_FILE).exists():
        with history.transaction(pdir, f"rename project to {new_name}") as tx:
            tx.project.name = new_name
    with closing(_db()) as con, con:
        con.execute("UPDATE projects SET name = ? WHERE id = ?", (new_name, pid))
    return get(pid)


def duplicate(pid: int) -> dict:
    from . import history

    row = get(pid)
    src = Path(row["path"])
    if not (src / PROJECT_FILE).exists():
        raise VeditError("Project folder is missing")
    name = f"{row['name']} copy"
    dest = _unique_dir(src.parent, name)
    # copy the edit + media, skip caches / previews / history (fresh history for the copy)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(STATE_DIR, "exports"))
    (dest / "exports").mkdir(exist_ok=True)
    p = load(dest)
    p.name = name
    save(dest, p)
    history.commit(dest, p, f"duplicated from {row['name']}")
    return get(register(dest, name))


def _rmtree_project(pdir: Path) -> None:
    """Delete a project folder, first letting an in-flight preview render finish.

    On Windows a file still open by ffmpeg can't be unlinked, so we wait on the
    preview lock and retry briefly for handles that close a moment later.
    """
    from .render.runner import _file_lock

    lock = pdir / STATE_DIR / "preview" / ".lock"
    if lock.exists():
        with _file_lock(lock):
            pass
    for attempt in range(20):
        try:
            shutil.rmtree(pdir)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def remove(pid: int, delete_files: bool = False) -> None:
    row = get(pid)
    pdir = Path(row["path"])
    if delete_files and pdir.exists():
        if not (pdir / PROJECT_FILE).exists():
            raise VeditError(f"Refusing to delete {pdir}: it doesn't look like a claude-cut project")
        _rmtree_project(pdir)
    with closing(_db()) as con, con:
        if delete_files:
            con.execute("DELETE FROM projects WHERE id = ?", (pid,))
        else:  # keep the row hidden so scanning projects/ doesn't bring it back
            con.execute("UPDATE projects SET hidden = 1 WHERE id = ?", (pid,))

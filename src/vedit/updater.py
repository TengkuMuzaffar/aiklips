"""In-app updates for the desktop install (the `aiklips` launcher, see launcher/).

Split of work, so the trusted part stays small and rarely changes:
  this module  checks GitHub Releases for a newer version and *stages* it (downloads the release manifest, its
               signature and the payload into <app>/pending/, checking the payload's sha256 on the way).
  launcher     on the next start, verifies the manifest's ed25519 signature with the public key built into it,
               installs the payload into <app>/versions/<v>/ (`uv sync --frozen`), smoke-tests it, switches
               <app>/current, and rolls back automatically if the new version crashes on start.

Only active when started by the launcher (VEDIT_LAUNCHER=1, VEDIT_APP_ROOT=<app>). In a git checkout updates are
`git pull` and every function here reports `enabled: False`.

Release assets (made by .github/workflows/release.yml):
  manifest.json   {"version", "payload", "sha256", "size", "schema", "min_launcher", "notes", "published"}
  manifest.sig    base64 ed25519 signature of manifest.json's exact bytes
  aiklips-<version>.tar.gz   the repo at that tag (git archive)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from .library import home_dir
from .project import SCHEMA_VERSION, VeditError

REPO = os.environ.get("VEDIT_UPDATE_REPO", "TengkuMuzaffar/aiklips")
# the release index on the project's own site (written by .github/workflows/site.yml): same shape as
# latest_release(). It keeps hosting movable and avoids GitHub's 60-requests-an-hour limit; the GitHub API is the fallback.
INDEX_URL = os.environ.get("VEDIT_UPDATE_URL", "https://aiklips.muzdev.online/latest.json")
CHECK_EVERY = 6 * 3600  # seconds between automatic checks
RESTART_CODE = 75  # exit code that tells the launcher "restart me (and apply the staged update)"
TIMEOUT = 20


def enabled() -> bool:
    return bool(os.environ.get("VEDIT_LAUNCHER") and os.environ.get("VEDIT_APP_ROOT"))


def app_root() -> Path:
    return Path(os.environ["VEDIT_APP_ROOT"])


def parse_version(v: str) -> tuple:
    """'v0.4.2' -> (0, 4, 2). Pre-release / local suffixes sort before the plain release."""
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)(.*)", v.strip())
    if not m:
        return (0, 0, 0, 0)
    return (int(m[1]), int(m[2]), int(m[3]), 0 if m[4] else 1)


def newer(a: str, b: str) -> bool:
    """Is version a newer than b?"""
    return parse_version(a) > parse_version(b)


# ---- settings + cache (~/.aiklips/update.json) ------------------------------------------------------

def _state_file() -> Path:
    return home_dir() / "update.json"


def _state() -> dict:
    try:
        return json.loads(_state_file().read_text())
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> None:
    _state_file().write_text(json.dumps(st, indent=2))


def set_auto(on: bool) -> None:
    st = _state()
    st["auto"] = bool(on)
    _save_state(st)


def auto() -> bool:
    return _state().get("auto", True)


# ---- GitHub ---------------------------------------------------------------------------------------------

def _get(url: str, accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"aiklips/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise VeditError("No releases published yet.") from None
        if e.code == 403:
            raise VeditError("GitHub rate limit reached; try again in an hour.") from None
        raise VeditError(f"Update check failed: HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise VeditError(f"Update check failed (offline?): {e}") from None


def latest_release() -> dict:
    """The newest published (non-pre-release) release: {version, notes, url, published, assets: {name: url}}.
    Nothing here is trusted: the launcher installs only what the signed manifest describes."""
    try:
        idx = json.loads(_get(INDEX_URL, "application/json"))
        if idx.get("version") and isinstance(idx.get("assets"), dict):
            return {"version": str(idx["version"]).lstrip("v"), "notes": idx.get("notes") or "",
                    "url": idx.get("url") or f"https://github.com/{REPO}/releases", "published": idx.get("published"),
                    "assets": idx["assets"]}
    except (VeditError, ValueError):
        pass
    rel = json.loads(_get(f"https://api.github.com/repos/{REPO}/releases/latest"))
    return {
        "version": str(rel.get("tag_name", "")).lstrip("v"),
        "notes": rel.get("body") or "",
        "url": rel.get("html_url") or f"https://github.com/{REPO}/releases",
        "published": rel.get("published_at"),
        "assets": {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])},
    }


# ---- check / stage / rollback -----------------------------------------------------------------------

def status() -> dict:
    st = _state()
    out = {"enabled": enabled(), "current": __version__, "auto": st.get("auto", True), "checked_at": st.get("checked_at"),
           "latest": (st.get("latest") or {}).get("version"), "notes": (st.get("latest") or {}).get("notes", ""),
           "url": (st.get("latest") or {}).get("url"), "staged": None, "previous": None}
    skipped = (app_root() / "skip").read_text().strip() if enabled() and (app_root() / "skip").exists() else ""
    out["available"] = bool(out["latest"] and newer(out["latest"], __version__) and out["latest"] != skipped)
    if enabled():
        man = app_root() / "pending" / "manifest.json"
        if man.exists():
            try:
                out["staged"] = json.loads(man.read_text()).get("version")
            except ValueError:
                pass
        prev = app_root() / "previous"
        if prev.exists():
            out["previous"] = prev.read_text().strip() or None
    return out


def check(force: bool = False) -> dict:
    """Look for a newer release (at most every CHECK_EVERY seconds unless forced). Never raises when not forced:
    an offline machine just reports what it knew."""
    if not enabled():
        return status()
    st = _state()
    if not force and (not st.get("auto", True) or time.time() - st.get("checked_at", 0) < CHECK_EVERY):
        return status()
    try:
        rel = latest_release()
    except VeditError as e:
        if force:
            raise
        return status() | {"error": str(e)}
    st.update(checked_at=time.time(), latest=rel)
    _save_state(st)
    return status()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stage(on_progress=None) -> dict:
    """Download the latest release into <app>/pending/ for the launcher to verify and install on the next start."""
    if not enabled():
        raise VeditError("Updates are managed by the aiklips launcher. In a source checkout, run `git pull` instead.")
    rel = check(force=True)
    latest = _state().get("latest") or {}
    if not rel["available"]:
        raise VeditError(f"Already up to date ({__version__}).")
    assets = latest.get("assets", {})
    if "manifest.json" not in assets or "manifest.sig" not in assets:
        raise VeditError("This release has no update manifest; download it from the releases page instead.")
    root = app_root()
    tmp = root / "pending.part"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    raw = _get(assets["manifest.json"], "application/octet-stream")
    man = json.loads(raw)
    if int(man.get("schema", SCHEMA_VERSION)) < SCHEMA_VERSION:
        raise VeditError("That release is older than this one's project format; not installing it.")
    (tmp / "manifest.json").write_bytes(raw)  # exact bytes: the signature covers them
    (tmp / "manifest.sig").write_bytes(_get(assets["manifest.sig"], "application/octet-stream"))
    name = man["payload"]
    if name not in assets or "/" in name or "\\" in name:
        raise VeditError(f"Release is missing its payload {name!r}")
    dest = tmp / name
    req = urllib.request.Request(assets[name], headers={"User-Agent": f"aiklips/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r, dest.open("wb") as f:
            total, done = int(man.get("size") or r.headers.get("Content-Length") or 0), 0
            for chunk in iter(lambda: r.read(1 << 16), b""):
                f.write(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(done / total)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise VeditError(f"Download failed: {e}") from None
    if _sha256(dest) != man["sha256"]:
        shutil.rmtree(tmp, ignore_errors=True)
        raise VeditError("Downloaded update is corrupt (checksum mismatch); try again.")
    pending = root / "pending"
    shutil.rmtree(pending, ignore_errors=True)
    tmp.rename(pending)
    return status() | {"staged": man["version"]}


def rollback() -> dict:
    """Go back to the previously installed version on the next start (the launcher keeps the last two)."""
    if not enabled():
        raise VeditError("Rollback is only available in the installed app.")
    root = app_root()
    prev = (root / "previous").read_text().strip() if (root / "previous").exists() else ""
    if not prev or not (root / "versions" / prev).exists():
        raise VeditError("No previous version to go back to.")
    cur = (root / "current").read_text().strip()
    (root / "current").write_text(prev)
    (root / "previous").write_text(cur)
    shutil.rmtree(root / "pending", ignore_errors=True)
    (root / "skip").write_text(cur)  # don't offer the version we just left again
    return status() | {"current_after_restart": prev}


# ---- optional features (extras), installed on first use -----------------------------------------------
# <app>/extras.txt lists the extras this install has. `uv sync` installs exactly what it's told, so the launcher
# passes the same list on every update and this module adds to it; otherwise an update would remove them.
EXTRAS = ("ai", "bg-quality", "captions")
_EXTRA_LOCK = threading.Lock()


def installed_extras() -> list[str]:
    f = app_root() / "extras.txt"
    return [x for x in f.read_text().split() if x in EXTRAS] if f.exists() else []


def install_extra(extra: str) -> None:
    """Install an optional feature into the running app's environment (one time; can take a minute or two)."""
    import importlib
    import sys

    if extra not in EXTRAS:
        raise VeditError(f"Unknown extra {extra}")
    with _EXTRA_LOCK:
        have = installed_extras()
        if extra in have:
            importlib.invalidate_caches()
            return
        want = sorted({*have, extra})
        print(f"Installing the '{extra}' feature (one time, may take a minute or two)...", file=sys.stderr, flush=True)
        cmd = [shutil.which("uv") or "uv", "sync", "--frozen", "--no-dev", *[a for x in want for a in ("--extra", x)]]
        proc = subprocess.run(cmd, cwd=os.environ.get("VEDIT_APP_DIR") or None, capture_output=True, text=True)
        if proc.returncode != 0:
            raise VeditError(f"Couldn't install the '{extra}' feature (are you online?):\n{proc.stderr[-1500:]}")
        (app_root() / "extras.txt").write_text("\n".join(want) + "\n")
        importlib.invalidate_caches()

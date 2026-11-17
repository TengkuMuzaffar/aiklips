"""In-app updates (updater.py): check, stage (download + checksum) and rollback, against a local fake release server.
The launcher (launcher/) does signature verification and install; here we test the app's half of the contract."""

import functools
import hashlib
import http.server
import json
import threading

import pytest

from vedit import updater
from vedit.project import SCHEMA_VERSION, VeditError, migrate


def test_version_compare():
    assert updater.newer("0.2.0", "0.1.9")
    assert updater.newer("v1.0.0", "0.9.9")
    assert not updater.newer("0.1.0", "0.1.0")
    assert updater.newer("0.2.0", "0.2.0-rc1")  # a release beats its release candidate
    assert not updater.newer("garbage", "0.1.0")


def test_disabled_in_a_source_checkout(monkeypatch):
    monkeypatch.delenv("VEDIT_LAUNCHER", raising=False)
    st = updater.check(force=True)
    assert st["enabled"] is False and st["available"] is False
    with pytest.raises(VeditError, match="git pull"):
        updater.stage()


def test_newer_project_format_is_refused():
    assert migrate({"version": SCHEMA_VERSION})["version"] == SCHEMA_VERSION
    with pytest.raises(VeditError, match="newer version"):
        migrate({"version": SCHEMA_VERSION + 1})


@pytest.fixture
def release(tmp_path, monkeypatch):
    """A fake GitHub release served over HTTP, and the launcher environment pointing at an app folder."""
    files = tmp_path / "srv"
    files.mkdir()
    payload = files / "aiklips-9.9.9.tar.gz"
    payload.write_bytes(b"pretend this is a tarball" * 1000)
    man = {"version": "9.9.9", "payload": payload.name, "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
           "size": payload.stat().st_size, "schema": SCHEMA_VERSION, "min_launcher": 1, "notes": "faster"}
    (files / "manifest.json").write_text(json.dumps(man))
    (files / "manifest.sig").write_text("c2lnbmF0dXJl")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(files))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    app = tmp_path / "app"
    (app / "versions" / "0.1.0").mkdir(parents=True)
    (app / "versions" / "0.0.9").mkdir(parents=True)
    (app / "current").write_text("0.1.0")
    (app / "previous").write_text("0.0.9")
    monkeypatch.setenv("VEDIT_LAUNCHER", "1")
    monkeypatch.setenv("VEDIT_APP_ROOT", str(app))
    monkeypatch.setattr(updater, "__version__", "0.1.0")
    rel = {"version": "9.9.9", "notes": "faster", "url": base, "published": None,
           "assets": {n: f"{base}/{n}" for n in ("manifest.json", "manifest.sig", payload.name)}}
    monkeypatch.setattr(updater, "latest_release", lambda: rel)
    yield {"app": app, "files": files, "payload": payload, "rel": rel}
    srv.shutdown()


def test_check_then_stage_downloads_and_verifies(release):
    st = updater.check(force=True)
    assert st["enabled"] and st["available"] and st["latest"] == "9.9.9"
    seen = []
    st = updater.stage(on_progress=seen.append)
    assert st["staged"] == "9.9.9" and seen and seen[-1] == pytest.approx(1.0)
    pending = release["app"] / "pending"
    assert (pending / "manifest.json").read_bytes() == (release["files"] / "manifest.json").read_bytes()  # exact bytes
    assert (pending / "manifest.sig").exists() and (pending / release["payload"].name).exists()
    assert not (release["app"] / "pending.part").exists()
    assert updater.status()["staged"] == "9.9.9"


def test_corrupt_download_is_rejected(release):
    release["payload"].write_bytes(b"tampered")
    with pytest.raises(VeditError, match="checksum"):
        updater.stage()
    assert not (release["app"] / "pending").exists()


def test_auto_check_is_rate_limited_and_can_be_off(release, monkeypatch):
    calls = []
    monkeypatch.setattr(updater, "latest_release", lambda: calls.append(1) or release["rel"])
    updater.check()
    updater.check()  # within 6 hours: cached
    assert len(calls) == 1
    updater.set_auto(False)
    monkeypatch.setattr(updater.time, "time", lambda: 1e12)
    updater.check()
    assert len(calls) == 1 and updater.status()["auto"] is False


def test_rollback_swaps_versions_and_skips_the_one_left(release):
    updater.check(force=True)
    st = updater.rollback()
    app = release["app"]
    assert (app / "current").read_text() == "0.0.9" and (app / "previous").read_text() == "0.1.0"
    assert st["current_after_restart"] == "0.0.9"
    (app / "previous").write_text("0.0.1")  # not installed
    (app / "current").write_text("0.1.0")
    with pytest.raises(VeditError, match="No previous version"):
        updater.rollback()


def test_update_api(release, monkeypatch):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    c = TestClient(create_app(None))
    assert c.get("/api/update").json()["enabled"] is True
    r = c.post("/api/update/apply")
    assert r.status_code == 200 and r.json()["staged"] == "9.9.9"
    exits = []
    monkeypatch.setattr(updater.os, "_exit", exits.append)  # the restart timer would otherwise end the test run
    monkeypatch.setattr("threading.Timer", lambda t, fn: type("T", (), {"start": lambda self: fn()})())
    assert c.post("/api/update/restart").status_code == 200
    assert exits == [updater.RESTART_CODE]


def test_release_index_on_the_site_first_then_github(monkeypatch):
    calls = []
    index = {"version": "v1.2.0", "notes": "n", "url": "u", "assets": {"manifest.json": "https://x/m.json"}}
    gh = {"tag_name": "v1.1.0", "body": "", "html_url": "h", "assets": [{"name": "manifest.json", "browser_download_url": "g"}]}

    def fake_get(url, accept=None):
        calls.append(url)
        if url == updater.INDEX_URL:
            if index is None:
                raise VeditError("offline")
            return json.dumps(index).encode()
        return json.dumps(gh).encode()

    monkeypatch.setattr(updater, "_get", fake_get)
    rel = updater.latest_release()
    assert rel["version"] == "1.2.0" and rel["assets"]["manifest.json"] == "https://x/m.json" and len(calls) == 1
    index = None  # site down: GitHub answers
    assert updater.latest_release()["version"] == "1.1.0"
    assert "api.github.com" in calls[-1]

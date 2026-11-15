"""Project library (SQLite) and the home-screen API."""

import json
from pathlib import Path

import pytest

from vedit import library
from vedit.project import VeditError, load

from .conftest import needs_ffmpeg


def test_create_list_rename_duplicate_remove():
    a = library.create("My First Reel", "reels", 30)
    b = library.create("My First Reel", "square", 25)  # same name -> unique folder
    assert Path(a["path"]).name == "my-first-reel" and Path(b["path"]).name == "my-first-reel-2"
    assert load(Path(b["path"])).canvas.width == 1080 and load(Path(b["path"])).canvas.fps == 25

    names = {p["name"] for p in library.list_projects()}
    assert names == {"My First Reel"}
    library.rename(a["id"], "Promo")
    assert load(Path(a["path"])).name == "Promo"  # project.json is updated too

    c = library.duplicate(a["id"])
    assert c["name"] == "Promo copy" and Path(c["path"]).exists()
    assert not (Path(c["path"]) / ".vedit" / "cache").exists()

    library.remove(c["id"], delete_files=False)  # hide, keep files
    assert Path(c["path"]).exists()
    assert c["id"] not in [p["id"] for p in library.list_projects()]  # scan doesn't bring it back
    library.touch(Path(c["path"]))  # opening it explicitly un-hides it
    assert c["id"] in [p["id"] for p in library.list_projects()]

    library.remove(b["id"], delete_files=True)
    assert not Path(b["path"]).exists()


def test_scan_registers_projects_made_elsewhere_and_flags_missing():
    a = library.create("Alpha")
    pj = Path(a["path"]) / "project.json"
    other = library.projects_root() / "made-by-cli"
    other.mkdir()
    (other / "project.json").write_text(pj.read_text().replace('"Alpha"', '"From CLI"'))
    names = [p["name"] for p in library.list_projects()]
    assert "From CLI" in names
    import shutil
    shutil.rmtree(a["path"])
    alpha = next(p for p in library.list_projects() if p["id"] == a["id"])
    assert alpha["missing"] is True


def test_bad_inputs():
    with pytest.raises(VeditError):
        library.create("x", "cinema")
    with pytest.raises(VeditError):
        library.register(Path("/definitely/not/here"))


@needs_ffmpeg
def test_home_api_flow(test_clip):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    c = TestClient(create_app(None, watch=False, chat=False))
    assert c.get("/").status_code == 200
    assert c.get("/editor", follow_redirects=False).status_code in (302, 307)  # nothing open yet
    assert c.get("/api/state").status_code == 409

    r = c.post("/api/projects", json={"name": "Launch video", "format": "landscape"}).json()
    st = c.get("/api/state").json()  # creating opens it
    assert st["project"]["name"] == "Launch video" and st["project"]["canvas"]["width"] == 1920
    assert c.get("/editor").status_code == 200

    with open(test_clip, "rb") as f:
        assert c.post("/api/import", files={"file": ("a.mp4", f, "video/mp4")}, data={"append": "true"}).status_code == 200
    assert c.get(f"/api/projects/{r['id']}/thumb.jpg").status_code == 200

    other = c.post("/api/projects", json={"name": "Second"}).json()
    assert c.post(f"/api/projects/{r['id']}/open").status_code == 200
    assert c.get("/api/state").json()["project"]["name"] == "Launch video"

    lst = c.get("/api/projects").json()
    assert {p["name"] for p in lst["projects"]} == {"Launch video", "Second"}
    assert lst["open"] == r["path"]

    assert c.delete(f"/api/projects/{r['id']}?delete_files=true").status_code == 200
    assert c.get("/api/state").status_code == 409  # deleting the open project closes it
    assert [p["name"] for p in c.get("/api/projects").json()["projects"]] == ["Second"]

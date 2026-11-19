"""A video edit is never rebuilt as a design by accident; designs can still be opened in the timeline editor."""

import json


def test_design_build_refuses_a_video_edit(cli, test_clip, tmp_path):
    from vedit.project import load

    cli("import", str(test_clip))
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"title": "x", "length": 3, "scenes": [{"id": "s1", "dur": 3, "purpose": "hook"}]}))
    cli("design", "build", str(plan), ok=False)
    p = load(cli.project)
    assert p.kind == "edit" and p.clips  # untouched


def test_designs_can_open_in_the_timeline_editor(tmp_path):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app
    from vedit.project import Project, save

    p = Project(kind="design")
    save(tmp_path, p)
    c = TestClient(create_app(tmp_path))
    r = c.get("/editor", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/studio"
    assert c.get("/editor?view=timeline", follow_redirects=False).status_code == 200


def test_design_is_switched_off_for_users(cli, monkeypatch, tmp_path):
    """The Design studio is in development: no new designs, the studio page says so, designs open on the timeline."""
    from fastapi.testclient import TestClient

    from vedit import features
    from vedit.preview_server.app import create_app

    monkeypatch.delenv("AIKLIPS_DESIGN", raising=False)
    assert not features.design_enabled()
    cli("design", "new", str(tmp_path / "d"), ok=False)
    c = TestClient(create_app(cli.project, watch=False, chat=False))
    r = c.get("/studio")
    assert r.status_code == 200 and "In development" in r.text
    assert c.post("/api/design/new", json={"format": "reels"}).status_code == 403
    assert c.get("/api/app").json()["features"]["design"] is False
    assert c.get("/editor", follow_redirects=False).status_code == 200  # never sent to the studio
    for k in ("bar", "kinetic", "morph"):
        assert c.get(f"/api/motion-thumb/{k}.png").headers["content-type"] == "image/png"
    assert c.get("/api/motion-thumb/nope.png").status_code == 404

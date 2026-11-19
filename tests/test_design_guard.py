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

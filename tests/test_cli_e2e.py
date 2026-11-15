"""End-to-end tests through the CLI with real FFmpeg renders."""

import json
from pathlib import Path

import pytest

from vedit import media
from vedit.project import load

from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


def test_import_info(cli, test_clip):
    r = cli("import", str(test_clip))
    assert r["added"][0]["duration"] == pytest.approx(6.0, abs=0.05)
    info = cli("info")
    assert info["duration"] == pytest.approx(6.0, abs=0.05)
    assert info["clips"][0]["id"] == "c1"


def test_undo_redo_restores_identical_project(cli, test_clip):
    cli("import", str(test_clip))
    before = (cli.project / "project.json").read_text()
    cli("trim", "--head", "1")
    after = (cli.project / "project.json").read_text()
    cli("undo")
    assert (cli.project / "project.json").read_text() == before
    cli("redo")
    assert (cli.project / "project.json").read_text() == after
    cli("undo")
    cli("trim", "--tail", "1")  # new edit drops the redo branch
    cli("redo", ok=False)
    steps = cli("history")["history"]
    assert steps[-1]["message"].startswith("trim head 0")


def test_manual_edit_is_recorded(cli, test_clip):
    cli("import", str(test_clip))
    pj = cli.project / "project.json"
    data = json.loads(pj.read_text())
    data["name"] = "hand-edited"
    pj.write_text(json.dumps(data))
    cli("trim", "--head", "1")
    cli("undo")
    assert load(cli.project).name == "hand-edited"


def test_silence_cut_removes_the_gap(cli, test_clip):
    cli("import", str(test_clip))
    r = cli("silence-cut", "--pad", "0")
    assert r["cuts"] == 1
    a, b = r["ranges"][0]
    assert a == pytest.approx(2.0, abs=0.15) and b == pytest.approx(3.2, abs=0.15)
    assert r["after"] == pytest.approx(4.8, abs=0.2)


def test_export_full_pipeline(cli, test_clip, tmp_path):
    cli("import", str(test_clip))
    cli("silence-cut")
    cli("jumpcut-zoom")
    cli("speed", "1.25", "--clip", "c3")
    cli("text", "add", "Hook text", "--preset", "pop_in", "--preset", "fade_out")
    cli("animate", "keyframes", "t1", '[{"t":0,"rotation":-8,"ease":"back_out"},{"t":0.6,"rotation":0}]')
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,100 --> 00:00:02,000\nhello from the test\n")
    cli("captions", "srt", str(srt), "--style", "boxed")
    cli("fit", "blur", "--clip", "c1")
    out = tmp_path / "out.mp4"
    r = cli("export", str(out), "--scale", "0.25")
    info = media.probe(out)
    duration = load(cli.project).duration
    assert info["duration"] == pytest.approx(duration, abs=0.12)
    assert (info["width"], info["height"]) == (270, 480)
    assert info["has_audio"]
    frame = cli("frame", "--at", "0.5")
    assert Path(frame["image"]).exists() and frame["image"].endswith(".jpg")
    many = cli("frame", "--at", "0.2,1,2.5")
    assert len(many["images"]) == 3 and all(Path(i).stat().st_size < 200_000 for i in many["images"])
    cli("frame", "--at", "999", ok=False)


def test_errors_are_reported_as_json(cli):
    cli("delete", "--from", "0", "--to", "1", ok=False)  # empty timeline


def test_preview_server_state(cli, test_clip):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    cli("import", str(test_clip))
    client = TestClient(create_app(cli.project, watch=False, chat=False))
    s = client.get("/api/state").json()
    assert s["project"]["clips"][0]["id"] == "c1"
    cli("trim", "--head", "1")
    assert client.post("/api/undo").status_code == 200
    assert client.get("/api/state").json()["duration"] == pytest.approx(6.0, abs=0.05)

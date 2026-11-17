import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vedit.cli import app
from vedit.project import Clip, Project, Source

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def isolated_library(tmp_path_factory, monkeypatch):
    """Never touch the real ~/.aiklips library or repo projects/ folder in tests."""
    root = tmp_path_factory.mktemp("lib")
    monkeypatch.setenv("VEDIT_HOME", str(root / "home"))
    monkeypatch.setenv("VEDIT_PROJECTS_DIR", str(root / "projects"))
    return root


@pytest.fixture
def make_project():
    """A pure in-memory project with one 10s source (no media needed)."""

    def _make(n_clips: int = 1) -> Project:
        p = Project()
        p.sources["s1"] = Source(path="fake.mp4", duration=10.0, width=1920, height=1080, fps=30)
        for i in range(n_clips):
            span = 10.0 / n_clips
            p.clips.append(Clip(id=f"c{i + 1}", source="s1", in_=round(i * span, 3), out=round((i + 1) * span, 3)))
        return p

    return _make


@pytest.fixture(scope="session")
def test_clip(tmp_path_factory) -> Path:
    """6s 1280x720 test video; audio is silent between 2.0s and 3.2s."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("media") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=6",
         "-f", "lavfi", "-i", "aevalsrc='if(between(t,2,3.2),0,0.4*sin(2*PI*440*t))':s=48000:d=6",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
        check=True,
    )
    return out


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Run vedit commands against a fresh project; returns parsed --json output."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    project = tmp_path / "proj"

    def run(*args: str, ok: bool = True) -> dict:
        res = runner.invoke(app, ["--json", "-p", str(project), *args])
        if ok:
            assert res.exit_code == 0, f"{args} failed: {res.output}\n{res.exception!r}"
            lines = [ln for ln in res.output.strip().splitlines() if ln.startswith("{")]
            assert lines, f"{args}: no JSON in output: {res.output!r}"
            return json.loads(lines[-1])
        assert res.exit_code != 0 or res.exception is not None
        return {}

    run.project = project  # type: ignore[attr-defined]
    runner.invoke(app, ["--json", "init", str(project)])
    return run

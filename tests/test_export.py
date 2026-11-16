"""Custom exports: file type, size, frame rate, quality, folder; background job with progress and cancel."""

import json
import subprocess
import time
from pathlib import Path

import pytest

from vedit import media
from vedit.project import load
from vedit.render import export as ex

from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


def _streams(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)], capture_output=True, text=True).stdout
    st = json.loads(out)["streams"]
    v = next(s for s in st if s["codec_type"] == "video")
    a = next((s for s in st if s["codec_type"] == "audio"), None)
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    return {"codec": v["codec_name"], "w": v["width"], "h": v["height"], "fps": num / den, "audio": a["codec_name"] if a else None}


@pytest.fixture
def short(cli, test_clip):
    cli("import", str(test_clip))
    cli("trim", "--tail", "4.5")  # 1.5 s is plenty to check codecs
    return cli


@pytest.mark.parametrize("fmt,codec,audio", [("mp4", "h264", "aac"), ("hevc", "hevc", "aac"), ("prores", "prores", "pcm_s16le"),
                                             ("webm", "vp9", "opus")])
def test_each_format(short, tmp_path, fmt, codec, audio):
    if not ex.available()[fmt]:
        pytest.skip(f"ffmpeg here can't write {fmt}")
    d = short.project
    seen = []
    s = ex.ExportSettings(format=fmt, fps=24, scale=0.5, quality="small", folder=str(tmp_path / "out"), filename="clip")
    r = ex.run(load(d), d, s, on_progress=lambda f, stage: seen.append(f))
    info = _streams(Path(r["output"]))
    assert Path(r["output"]).name == f"clip{ex.FORMATS[fmt]['ext']}" and Path(r["output"]).parent == tmp_path / "out"
    assert (info["codec"], info["w"], info["h"], info["audio"]) == (codec, 540, 960, audio)
    assert info["fps"] == pytest.approx(24, abs=0.01) and r["bytes"] > 0
    assert seen and seen[-1] == 1.0


def test_gif_is_silent_small_and_never_overwrites(short, tmp_path):
    d = short.project
    s = ex.ExportSettings(format="gif", gif_fps=10, gif_width=360, folder=str(tmp_path))
    r1 = ex.run(load(d), d, s)
    r2 = ex.run(load(d), d, s)
    info = _streams(Path(r1["output"]))
    assert (info["codec"], info["w"], info["audio"]) == ("gif", 360, None)
    assert Path(r2["output"]).name.endswith("-2.gif") and Path(r1["output"]).exists()  # second export: a new name


def test_no_audio_and_cancel(short, tmp_path):
    d = short.project
    r = ex.run(load(d), d, ex.ExportSettings(format="mp4", audio=False, folder=str(tmp_path), scale=0.5))
    assert _streams(Path(r["output"]))["audio"] is None
    with pytest.raises(media.Cancelled):
        ex.run(load(d), d, ex.ExportSettings(format="mp4", folder=str(tmp_path / "c"), filename="x"), should_stop=lambda: True)
    assert not list((tmp_path / "c").glob("x*"))  # no half-written file left behind


def test_settings_are_remembered_and_cli_uses_them(short, tmp_path):
    d = short.project
    assert ex.load_settings(d) == ex.ExportSettings()
    r = short("export", "--format", "hevc", "--fps", "25", "--scale", "0.5", "--folder", str(tmp_path), "--name", "Final cut")
    assert r["output"] == str(tmp_path / "Final cut.mp4") and r["fps"] == 25
    assert ex.load_settings(d).format == "hevc"
    r2 = short("export")  # remembered: hevc, 25 fps, half size, same folder, new name
    assert _streams(Path(r2["output"]))["codec"] == "hevc" and r2["output"].endswith("Final cut-2.mp4")
    r3 = short("export", str(tmp_path / "loop.gif"))  # the extension picks the type
    assert r3["format"] == "gif" and r3["output"].endswith("loop.gif")
    short("export", "--format", "avi", ok=False)


def test_safe_names_and_preview(short):
    d = short.project
    assert ex.safe_name('a/b:c*?"d') == "a-b-c---d" and ex.safe_name("...") == "video"
    p = load(d)
    info = ex.describe(p, d, ex.ExportSettings(format="mp4", scale=2.0, fps=60))
    assert (info["width"], info["height"], info["fps"], info["audio"]) == (2160, 3840, 60, True)
    assert ex.describe(p, d, ex.ExportSettings(format="gif", gif_width=540))["audio"] is False


def test_export_api_job(short):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    c = TestClient(create_app(short.project, watch=False, chat=False))
    o = c.get("/api/exports/options").json()
    assert [f["id"] for f in o["formats"]] == list(ex.FORMATS) and o["canvas"]["width"] == 1080
    assert c.post("/api/exports", json={"format": "nope"}).status_code == 400
    eid = c.post("/api/exports", json={"format": "mp4", "scale": 0.5, "fps": 24}).json()["id"]
    for _ in range(300):
        j = c.get(f"/api/exports/{eid}").json()
        if j["status"] != "running":
            break
        time.sleep(0.1)
    assert j["status"] == "done" and j["progress"] == 1.0 and Path(j["result"]["output"]).exists(), j
    assert j["result"]["width"] == 540
    assert c.post("/api/exports/reveal", json={"path": "/etc/hosts"}).status_code == 403  # only exported files
    assert c.get("/api/exports/nope").status_code == 404
    assert c.get("/api/exports/options").json()["settings"]["fps"] == 24  # remembered

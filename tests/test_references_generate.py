"""References (study) and Generate (variations), with the LLM replaced by a fake."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from vedit import jobs
from vedit.ai import client as ai
from vedit.references import analysis
from vedit.references.dna import StyleDNA, apply_overrides, merge

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _ff(*args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


@pytest.fixture(scope="session")
def beat_ref(tmp_path_factory) -> Path:
    """8 s video, a hard cut every 1 s, a click every 0.5 s (120 BPM)."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("ref")
    colors = ["red", "blue", "green", "white"] * 2
    ins = [x for c in colors for x in ("-f", "lavfi", "-i", f"color=c={c}:s=360x640:d=1:r=30")]
    _ff(*ins, "-f", "lavfi", "-i", "aevalsrc='0.8*sin(2*PI*880*t)*exp(-40*mod(t,0.5))':s=44100:d=8",
        "-filter_complex", "".join(f"[{i}:v]" for i in range(8)) + "concat=n=8:v=1:a=0,format=yuv420p[v]",
        "-map", "[v]", "-map", "8:a", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(d / "ref.mp4"))
    return d / "ref.mp4"


@pytest.fixture(scope="session")
def song(tmp_path_factory) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("song") / "song.wav"
    _ff("-f", "lavfi", "-i", "aevalsrc='0.8*sin(2*PI*660*t)*exp(-40*mod(t,0.5))':s=44100:d=20", str(out))
    return out


@pytest.fixture
def fake_ai(monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "AUTOSTART", False)

    def backend(task, system, prompt, schema, images, model):
        calls.append(task)
        if task == "study":
            return StyleDNA(summary="Fast beat-cut promo", tags=["fast-cut"]).model_dump() | {
                "text": {"color": "#FFE600", "animation_in": ["pop_in"], "position_y": "22%"}}
        if task == "footage":
            ids = re.findall(r"^(f\d+\.s\d+):", prompt, re.M)
            return {"segments": [{"id": i, "description": f"shot {i}", "quality": 4} for i in ids]}
        if task == "planner":
            n = int(re.search(r"VARIATIONS \((\d+)\)", prompt).group(1))
            return {"variations": [{"index": i + 1, "angle": f"angle {i + 1}", "hook_text": f"Hook number {i + 1}",
                                    "cta_text": "Try it"} for i in range(n)]}
        if task == "editor":
            segs = re.findall(r"(f\d+)\.s\d+ \(([\d.]+)-([\d.]+)s", prompt)
            hook = re.search(r"hook line: (.+)", prompt).group(1)
            ops = [{"op": "clip", "file": f, "start": float(a), "end": float(b)} for f, a, b in segs[:4]]
            ops.append({"op": "text", "text": hook, "start": 0, "end": 1.5})
            ops.append({"op": "transition", "type": "fade", "into": 1, "duration": 0.3})
            return {"title": hook, "ops": ops}
        if task == "critic":
            return {"ok": False, "issues": ["hook too low"], "fixes": [{"layer": "t1", "y": "20%"}]}
        raise AssertionError(task)

    ai.set_backend(backend)
    yield calls
    ai.set_backend(None)


# ---- measuring (no AI) --------------------------------------------------------------------

@needs_ffmpeg
def test_analysis_measures_cuts_tempo_and_beat_sync(beat_ref, tmp_path):
    m = analysis.analyze(beat_ref, tmp_path, transcript=False)
    assert m["cuts"] == pytest.approx([1, 2, 3, 4, 5, 6, 7], abs=0.05)
    assert m["pacing"]["avg_shot_s"] == pytest.approx(1.0, abs=0.05)
    assert m["audio"]["bpm"] == pytest.approx(120, abs=2)
    assert m["cuts_on_beat"] >= 0.85
    assert (tmp_path / "sheet-1.jpg").exists() and (tmp_path / "sheet-2.jpg").exists()


def test_dna_merge_majority_average_and_lead():
    a = StyleDNA(pacing={"avg_shot_s": 1.0}, text={"color": "#FFFFFF", "uppercase": True}, captions={"style": "bold"})
    b = StyleDNA(pacing={"avg_shot_s": 2.0}, text={"color": "#FFE600", "uppercase": False}, captions={"style": "neon"})
    m = merge([a, b], lead=1)
    assert m.pacing.avg_shot_s == 1.5
    assert m.text.color == "#FFE600" and m.text.uppercase is False  # ties go to the lead
    assert m.captions.style == "neon"
    o = apply_overrides(a.model_dump(), {"text.color": "#00FF00", "captions.words_at_once": 2})
    assert o.text.color == "#00FF00" and o.captions.words_at_once == 2


def test_axes_spread_keeps_variation_one_faithful():
    from vedit.generate.planner import assign_axes

    axes = assign_axes(4, ["hook", "caption_style", "pacing"], seed="x")
    assert axes[0]["hook"] == "reference" and axes[0]["pacing"] == "reference"
    assert len({a["hook"] for a in axes}) == 4
    assert all(a["order"] == "best_first" for a in axes)  # not ticked: fixed


def test_timing_snaps_cuts_to_beats_and_fits_length():
    from vedit.generate.spec import EditPlan, Op
    from vedit.generate.timing import resolve

    beats = [round(0.25 + 0.5 * i, 3) for i in range(80)]
    files = {"f1": {"kind": "video", "duration": 30.0}, "f2": {"kind": "audio", "duration": 40.0, "beats": beats}}
    plan = EditPlan(beat_sync=True, ops=[Op(op="clip", file="f1", start=0, end=1.3), Op(op="clip", file="f1", start=5, end=6.1),
                                         Op(op="clip", file="f1", start=10, end=30), Op(op="music", file="f2", offset=3.1),
                                         Op(op="clip", file="nope", start=0, end=1)])
    plan, notes = resolve(plan, files, target_s=6)
    clips = [o for o in plan.ops if o.op == "clip"]
    music = next(o for o in plan.ops if o.op == "music")
    assert music.offset in beats  # timeline 0 is on a beat
    t = 0
    for c in clips:
        t += c.end - c.start
        assert abs(((t + 1e-6) % 0.5)) < 0.01 or abs((t % 0.5) - 0.5) < 0.01  # every cut on the grid
    assert t <= 6.6
    assert any("unknown file" in n for n in notes)


# ---- studying -----------------------------------------------------------------------------------

@needs_ffmpeg
def test_study_reference_with_ai_and_overrides(beat_ref, fake_ai):
    from vedit.references import store

    r = store.add(beat_ref, "Beat promo")
    assert store.add(beat_ref)["id"] == r["id"]  # same content: deduplicated
    jobs.drain()
    r = store.get(r["id"], full=True)
    assert r["status"] == "ready" and r["ai"] == 1
    assert r["dna"]["text"]["color"] == "#FFE600"           # from the AI
    assert r["dna"]["sound"]["bpm"] == pytest.approx(120, abs=2)  # measured wins
    assert r["dna"]["sound"]["cut_on_beat"] is True
    r = store.update(r["id"], overrides={"text.color": "#123456"})
    assert r["dna"]["text"]["color"] == "#123456"
    assert any(row["label"] == "Sound" for row in r["card"])


@needs_ffmpeg
def test_study_without_ai_falls_back_to_measured(beat_ref, monkeypatch):
    from vedit.references import store

    monkeypatch.setattr(jobs, "AUTOSTART", False)

    def offline(**kw):
        raise ai.AIUnavailable("not signed in")

    ai.set_backend(offline)
    try:
        r = store.add(beat_ref)
        jobs.drain()
        r = store.get(r["id"])
        assert r["status"] == "ready" and r["ai"] == 0 and "without AI" in r["error"]
        assert r["dna"]["sound"]["cut_on_beat"] is True
    finally:
        ai.set_backend(None)


@needs_ffmpeg
def test_rate_limit_pauses_job_instead_of_failing(beat_ref, monkeypatch):
    from vedit.references import store

    monkeypatch.setattr(jobs, "AUTOSTART", False)
    monkeypatch.setattr(ai.time, "sleep", lambda s: None)

    def limited(**kw):
        raise ai.RateLimited("usage limit reached", retry_after=60)

    ai.set_backend(limited)
    try:
        r = store.add(beat_ref)
        jobs.drain()
        job = jobs.for_target(f"ref:{r['id']}")
        assert job["status"] == "queued" and "AI quota" in job["note"]
        assert store.get(r["id"])["status"] == "queued"
    finally:
        ai.set_backend(None)


# ---- generating -------------------------------------------------------------------------------------

@needs_ffmpeg
def test_generate_batch_end_to_end(beat_ref, test_clip, song, fake_ai):
    from vedit import history
    from vedit.generate import batch, footage
    from vedit.generate.spec import BatchSpec
    from vedit.project import load
    from vedit.references import store

    r = store.add(beat_ref)
    ups = [footage.add(test_clip)["id"], footage.add(song)["id"]]
    spec = BatchSpec(refs=[r["id"]], footage=ups, count=2, brief={"product": "Test app", "length_s": 5, "cta": "Try it"})
    est = batch.estimate(spec)
    assert est["breakdown"] == {"study": 1, "footage": 1, "planner": 1, "editor": 2, "critic": 2}
    b = batch.create(spec)
    jobs.drain()
    b = batch.get(b["id"])
    assert b["status"] == "done", b
    assert [v["status"] for v in b["variations"]] == ["done", "done"]
    hooks = {v["hook_text"] for v in b["variations"]}
    assert len(hooks) == 2  # different variations
    v = b["variations"][0]
    assert Path(v["output"]).exists()
    vdir = Path(batch._var(v["id"])["dir"])
    p = load(vdir)
    assert p.music and p.layers[0].y == "20%"          # music added, AI review fix applied
    assert 3.5 <= p.duration <= 6.5
    assert len(history.log(vdir)) > 4                    # one undoable step per operation
    assert fake_ai.count("planner") == 1 and fake_ai.count("editor") == 2

    row = batch.keep(v["id"])
    assert row["path"] == str(vdir)
    batch.discard(b["variations"][1]["id"])
    assert batch.get(b["id"])["variations"][1]["verdict"] == "discard"
    batch.regenerate(b["variations"][1]["id"])
    jobs.drain()
    assert batch.get(b["id"])["variations"][1]["status"] == "done"
    batch.delete(b["id"])
    assert not vdir.exists()


@needs_ffmpeg
def test_home_api_references_uploads_and_batches(beat_ref, test_clip, fake_ai):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    c = TestClient(create_app(None, watch=False, chat=False))
    with beat_ref.open("rb") as f:
        r = c.post("/api/references", files=[("files", ("ref.mp4", f, "video/mp4"))])
    assert r.status_code == 200, r.text
    rid = r.json()["added"][0]["id"]
    jobs.drain()
    ref = c.get(f"/api/references/{rid}").json()
    assert ref["status"] == "ready" and ref["card"]
    assert c.get(f"/api/references/{rid}/sheet/1.jpg").status_code == 200
    assert c.get(f"/api/references/{rid}/thumb.jpg").status_code == 200
    assert c.patch(f"/api/references/{rid}", json={"name": "Renamed"}).json()["name"] == "Renamed"
    assert c.post("/api/references/merge", json={"ids": [rid]}).json()["card"]

    with test_clip.open("rb") as f:
        up = c.post("/api/uploads", files=[("files", ("clip.mp4", f, "video/mp4"))]).json()["added"][0]
    body = {"refs": [rid], "footage": [up["id"]], "count": 1, "review": False, "brief": {"length_s": 5}}
    assert c.post("/api/batches/estimate", json=body).json()["calls"] >= 2
    bad = c.post("/api/batches", json={**body, "footage": []})
    assert bad.status_code == 400
    bid = c.post("/api/batches", json=body).json()["id"]
    jobs.drain()
    b = c.get(f"/api/batches/{bid}").json()
    assert b["status"] == "done"
    vid = b["variations"][0]["id"]
    assert c.get(f"/api/variations/{vid}/poster.jpg").status_code == 200
    assert c.get(f"/api/variations/{vid}/video.mp4").status_code == 200
    assert c.post(f"/api/variations/{vid}/open").status_code == 200
    assert c.get("/api/state").status_code == 200  # the variation is now open in the editor
    assert c.delete(f"/api/batches/{bid}").status_code == 200
    assert c.delete(f"/api/references/{rid}").status_code == 200
    assert c.get("/api/references").json()["references"] == []


@needs_ffmpeg
def test_cli_ref_and_gen(beat_ref, test_clip, fake_ai, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from vedit.cli import app

    monkeypatch.chdir(tmp_path)
    run = lambda *a: json.loads(CliRunner().invoke(app, ["--json", *a], catch_exceptions=False).stdout)
    r = run("ref", "add", str(beat_ref))["references"][0]
    assert r["status"] == "ready"
    assert run("ref", "set", str(r["id"]), "text.color", '"#00FF00"')["overrides"] == {"text.color": "#00FF00"}
    assert run("ref", "show", str(r["id"]))["reference"]["dna"]["text"]["color"] == "#00FF00"
    b = run("gen", "run", "--ref", str(r["id"]), "-f", str(test_clip), "--count", "1", "--length", "5", "--no-review")["batch"]
    assert b["status"] == "done" and Path(b["variations"][0]["output"]).exists()
    assert run("gen", "list")["batches"][0]["id"] == b["id"]

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


MODELS_USED: list = []
PROMPTS: list = []
NEVER_PASS = False


@pytest.fixture
def fake_ai(monkeypatch):
    MODELS_USED.clear()
    PROMPTS.clear()
    calls = []
    monkeypatch.setattr(jobs, "AUTOSTART", False)

    def backend(task, system, prompt, schema, images, model):
        calls.append(task)
        MODELS_USED.append((task, model))
        PROMPTS.append((task, prompt))
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
            segs = re.findall(r"((f\d+)\.s\d+) \(([\d.]+)-([\d.]+)s", prompt)
            hook = re.search(r"hook line: (.+)", prompt).group(1)
            beats = [{"id": "b1", "role": "hook", "purpose": "stop the scroll"}, {"id": "b2", "role": "demo", "purpose": "show it"}]
            ops = [{"op": "clip", "file": f, "segment": sid, "start": float(a), "end": float(b), "beat": "b1" if i == 0 else "b2",
                    "why": f"shot {sid} shows the product"} for i, (sid, f, a, b) in enumerate(segs[:4])]
            revising = "INDEPENDENT REVIEW" in prompt  # the auditor's review came back to the editor
            if revising:
                assert "hook too low" in prompt and "no evidence: dropped" not in prompt
            ops.append({"op": "text", "text": hook, "start": 0, "end": 1.5, "beat": "b1", "why": "hook line",
                        **({"y": "20%"} if revising else {})})
            ops.append({"op": "transition", "type": "fade", "into": 1, "duration": 0.3})
            ops.append({"op": "move", "type": "flash_cut", "into": 1, "why": "the concept's signature move"})
            return {"title": hook, "beats": beats, "ops": ops, "rationale": "open on the product"}
        if task == "classify":
            ids = re.findall(r"^(m\d+): '([^']*)'", prompt, re.M)
            return {"files": [{"id": i, "role": "sound" if "sound" in n else "footage", "reason": "fake"} for i, n in ids]}
        if task == "concepts":
            n = re.search(r"THIS VARIATION \(#(\d+)\)", prompt).group(1)
            return {"concepts": [{"title": f"Idea {n}.{j}", "arc": "doubt -> delight", "opening": "product close-up",
                                  "signature_move": ["punch_in", "flash_cut", "zoom_out_reveal"][j - 1]} for j in (1, 2, 3)]}
        if task == "rank":
            vs = [int(x) for x in re.findall(r"^VARIATION (\d+)", prompt, re.M)]
            return {"picks": [{"variation": v, "concept": 3 if v == 1 else 2, "reason": "boldest"} for v in vs]}
        if task == "auditor":
            if ("(20%" in prompt or ", 20%)" in prompt) and not NEVER_PASS:  # already fixed: clean bill
                return {"summary": "fine", "scores": {k: 5 for k in ("hook", "story", "pacing", "typography", "colour", "audio", "creativity")},
                        "findings": []}
            return {"summary": "hook title sits low", "scores": {k: 3 for k in ("hook", "story", "pacing", "typography", "colour", "audio", "creativity")},
                    "findings": [{"severity": "must-fix", "category": "hook", "frame": 0, "at": 0.5, "message": "hook too low", "fix": "move t1 up"},
                                 {"severity": "should-fix", "category": "story", "frame": 99, "at": 1, "message": "no evidence: dropped"}]}
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
    spec = BatchSpec(refs=[r["id"]], footage=ups, count=2, brief={"product": "Test app", "length_s": 5, "cta": "Try it"},
                     audit_threshold=70, audit_rounds=3, models={"auditor": "anthropic/claude-opus-5-5"})
    est = batch.estimate(spec)
    assert est["breakdown"] == {"study": 1, "footage": 1, "planner": 1, "editor": 4, "auditor": 4, "concepts": 2, "rank": 1}
    assert est["max_calls"] > est["calls"]
    b = batch.create(spec)
    jobs.drain()
    b = batch.get(b["id"])
    assert b["status"] == "done", (b["error"], [x["error"] for x in b["variations"]])
    assert [v["status"] for v in b["variations"]] == ["done", "done"]
    hooks = {v["hook_text"] for v in b["variations"]}
    assert len(hooks) == 2  # different variations
    v = b["variations"][0]
    assert Path(v["output"]).exists()
    vdir = Path(batch._var(v["id"])["dir"])
    p = load(vdir)
    assert p.music and p.layers[0].y == "20%"          # music added; the editor revised the edit from the review
    a = v["audit"]
    assert a and a["scores"]["hook"] == 5 and a["reached"] and a["threshold"] == 70 and not v["attention"]
    assert [x["round"] for x in a["rounds"]] == [0, 1] and a["rounds"][1]["score"] > a["rounds"][0]["score"]
    assert not any(f.get("message") == "no evidence: dropped" for f in a["top"])
    assert fake_ai.count("auditor") >= 4 and "fixer" not in fake_ai
    assert not list(vdir.parent.glob(f"{vdir.name}.r*"))  # the best attempt became the variation; others removed
    assert fake_ai.count("concepts") == 2 and fake_ai.count("rank") == 1
    # the models picked for this generation: auditor = Opus; every other role keeps the balanced default
    assert {m for t, m in MODELS_USED if t == "auditor"} == {"anthropic/claude-opus-5-5"}
    assert {m for t, m in MODELS_USED if t == "editor"} == {"anthropic/claude-sonnet-5-5"}
    assert b["models"]["auditor"] == "anthropic/claude-opus-5-5" and b["models"]["editor"] == "anthropic/claude-sonnet-5-5"
    assert v["concept"]["title"] == "Idea 1.3" and v["concept"]["signature_move"] == "zoom_out_reveal"
    assert b["variations"][1]["concept"]["title"] == "Idea 2.2"
    assert p.clips and all(c.grade and c.grade.matched_to == "reference" for c in p.clips)  # graded to the reference's colour
    assert p.clips[1].transition and p.clips[1].transition.type == "fadewhite"  # signature move applied after the plain transition
    assert p.notes and [b.role for b in p.notes.beats] == ["hook", "demo"]   # beat sheet stored in the project
    assert p.clips[0].meta and p.clips[0].meta.why and p.clips[0].meta.role == "hook" and p.clips[0].meta.quality == 4
    from vedit.ops.fonts import FONTS
    assert {l.style.font for l in p.layers if l.type == "text"} <= set(FONTS)  # titles use a bundled font
    assert 3.5 <= p.duration <= 6.5
    assert len(history.log(vdir)) > 4                    # one undoable step per operation
    assert fake_ai.count("planner") == 1 and fake_ai.count("editor") >= 4  # 2 plans + 2 revisions

    row = batch.keep(v["id"])
    assert row["path"] == str(vdir)
    batch.discard(b["variations"][1]["id"])
    assert batch.get(b["id"])["variations"][1]["verdict"] == "discard"
    batch.regenerate(b["variations"][1]["id"])
    jobs.drain()
    assert batch.get(b["id"])["variations"][1]["status"] == "done"
    batch.delete(b["id"])
    assert not vdir.exists()


def test_video_used_only_for_its_sound(beat_ref, test_clip, fake_ai, tmp_path):
    """The Sound step: a video's audio becomes the music track (beats measured), its pictures are never cut in."""
    from vedit.generate import batch, footage
    from vedit.generate.spec import BatchSpec
    from vedit.project import load
    from vedit.references import store

    snd = tmp_path / "trending sound.mp4"
    _ff("-i", str(beat_ref), "-vf", "hue=s=0", "-c:a", "copy", str(snd))  # different hash from the reference
    r = store.add(beat_ref)
    clip, sv = footage.add(test_clip)["id"], footage.add(snd)["id"]

    roles = footage.classify([clip, sv])
    assert roles[sv] == {"role": "sound", "reason": "fake", "by": "ai"} and roles[clip]["role"] == "footage"
    n = fake_ai.count("classify")
    footage.classify([clip, sv])
    assert fake_ai.count("classify") == n  # cached per file

    m = footage.sound_meta(sv)
    assert m["kind"] == "audio" and m["from_video"] and m["bpm"] == pytest.approx(120, abs=2)
    with pytest.raises(Exception):
        batch.validate(BatchSpec(refs=[r["id"]], footage=[clip], sound=[sv] * 11))

    # two sounds, three videos: they take turns (m1, m2, m1)
    song2 = tmp_path / "song2.wav"
    _ff("-f", "lavfi", "-i", "aevalsrc='0.8*sin(2*PI*440*t)*exp(-40*mod(t,0.6))':s=44100:d=12", str(song2))
    s2 = footage.add(song2)["id"]
    spec = BatchSpec(refs=[r["id"]], footage=[clip], sound=[sv, s2], count=3, review=False, axes=["hook"], brief={"length_s": 5})
    b = batch.create(spec)
    jobs.drain()
    b = batch.get(b["id"])
    assert b["status"] == "done", (b["error"], [x["error"] for x in b["variations"]])
    used = []
    for v in b["variations"]:
        p = load(Path(batch._var(v["id"])["dir"]))
        assert len(p.music) == 1
        music_src = p.sources[p.music[0].source]
        assert not music_src.has_video
        assert all(p.sources[c.source].path != music_src.path for c in p.clips)
        used.append(Path(music_src.path).parent.name)
    assert used == [sv, s2, sv]


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
    roles = c.post("/api/uploads/classify", json={"ids": [up["id"]]}).json()["roles"]
    assert roles[up["id"]]["role"] == "footage"
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


@needs_ffmpeg
def test_keep_improving_one_video(beat_ref, test_clip, song, fake_ai):
    from vedit import history
    from vedit.generate import batch, footage
    from vedit.generate.spec import BatchSpec
    from vedit.project import load, save
    from vedit.references import store

    r = store.add(beat_ref)
    ups = [footage.add(test_clip)["id"], footage.add(song)["id"]]
    # an unreachable pass mark and no revisions: the video ends below the mark, ready to be improved
    spec = BatchSpec(refs=[r["id"]], footage=ups, count=1, brief={"product": "Test app", "length_s": 5}, explore=False,
                     audit_threshold=100, audit_rounds=0)
    b = batch.create(spec)
    jobs.drain()
    v = batch.get(b["id"])["variations"][0]
    assert v["status"] == "done" and not v["audit"]["reached"] and len(v["audit"]["rounds"]) == 1 and v["attention"]
    PROMPTS.clear()
    global NEVER_PASS
    NEVER_PASS = True  # the reviewer stays unhappy, so both extra rounds run
    try:
        out = batch.improve(v["id"], rounds=2)
        assert out["status"] == "queued"
        jobs.drain()
    finally:
        NEVER_PASS = False
    v2 = batch.get(b["id"])["variations"][0]
    assert v2["status"] == "done" and len(v2["audit"]["rounds"]) == 3  # 2 more rounds after the first
    revise = [pr for t, pr in PROMPTS if t == "editor" and "INDEPENDENT REVIEW" in pr]
    assert revise and "YOUR BEST PLAN SO FAR" in revise[0]
    assert "EARLIER ATTEMPTS" in revise[-1] and "best so far" in revise[-1]  # the 2nd revision knows how the 1st did
    vdir = Path(batch._var(v["id"])["dir"])
    assert not list(vdir.parent.glob(f"{vdir.name}.r*"))
    # once a person edits it, improving from the AI's plan would throw their work away: refuse
    p = load(vdir)
    p.layers[0].y = "33%"
    save(vdir, p)
    history.commit(vdir, p, "hand edit")
    with pytest.raises(Exception, match="edited by hand"):
        batch.improve(v["id"], rounds=1)

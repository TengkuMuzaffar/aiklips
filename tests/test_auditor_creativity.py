"""P2-P5 of docs/design/auditor-justification-creativity.md: AI reviewer + fix loop, chat Stop hook,
signature moves + concepts, taste memory."""

import asyncio
import subprocess
from pathlib import Path

import numpy as np
import pytest

from vedit import history, review, sound
from vedit.ai import client as ai
from vedit.generate import planner
from vedit.ops import moves
from vedit.project import Layer, VeditError, load, save

from .conftest import needs_ffmpeg


# ---- signature moves (pure timeline maths) --------------------------------------------------------

def test_every_move_applies(make_project):
    for name, (needs, _about) in moves.MOVES.items():
        p = make_project(3)  # 3 clips of 3.33 s
        before = p.duration
        kw = {"clip": "c2"} if needs == "clip" else {"at": 4.0}
        if "+text" in needs:
            kw["text"] = "wow"
        msg = moves.apply(p, name, **kw)
        assert msg, name
        if name == "freeze_title":
            assert p.duration == pytest.approx(before + 0.72, abs=0.02)  # 0.08 s held at 0.1x = 0.8 s
            assert any(c.speed == pytest.approx(0.1) for c in p.clips) and p.layers
        if name == "speed_ramp":
            assert p.duration < before and any(c.speed == pytest.approx(2.5) for c in p.clips)
        if name == "punch_in":
            assert len(p.clips) == 4 and p.clips[2].zoom == pytest.approx(1.18)
        if name in ("flash_cut", "whip", "before_after"):
            assert p.clip("c2").transition is not None
        if name == "before_after":
            assert [l.text for l in p.layers] == ["BEFORE", "AFTER"]
        if name == "keyword_pop":
            assert p.layers[0].text == "WOW" and p.layers[0].style.look == "comic-pop"


def test_move_errors(make_project):
    p = make_project(2)
    with pytest.raises(VeditError, match="Unknown move"):
        moves.apply(p, "explode")
    with pytest.raises(VeditError, match="first clip"):
        moves.apply(p, "whip", clip="c1")
    with pytest.raises(VeditError, match="needs --text"):
        moves.apply(p, "keyword_pop", at=1.0)


def test_study_moves_are_confirmed_by_measurements():
    from vedit.references.dna import SignatureMove, StyleDNA, confirm_moves

    energy = [0.01] * 100
    energy[30] = 0.2  # a spike at 3.0 s with no cut nearby: a punch-in
    m = {"cuts": [5.0], "motion": {"fps": 10, "flashes": [7.0]}, "motion_energy": energy}
    dna = StyleDNA(signature_moves=[SignatureMove(move="punch_in", at=3.0), SignatureMove(move="flash_cut", at=7.1),
                                    SignatureMove(move="punch_in", at=5.0), SignatureMove(move="keyword_pop")])
    confirm_moves(dna, m)
    assert [s.confirmed for s in dna.signature_moves] == [True, True, False, False]


def test_validate_plan_checks_moves():
    from vedit.generate import planner
    from vedit.generate.spec import EditPlan, Op

    plan = EditPlan(ops=[Op(op="move", type="explode"), Op(op="move", type="keyword_pop", start=1.0),
                         Op(op="move", type="whip"), Op(op="move", type="flash_cut", into=1)])
    errs = "\n".join(planner.validate_plan(plan, {}))
    assert "4 moves" in errs and "'explode' is not in the move library" in errs
    assert "keyword_pop needs `text`" in errs and "whip needs `into`" in errs


# ---- reviewer: evidence filter, merge, fixes -------------------------------------------------------

def test_verify_drops_findings_without_evidence():
    F = review.AIFinding
    times = [1.0, 3.0, 5.0, 7.0]
    out = review.verify([F(severity="must-fix", category="hook", frame=0, at=0.4, message="weak opening"),
                         F(severity="should-fix", category="story", frame=9, at=2, message="no such frame"),
                         F(severity="note", category="pacing", frame=2, at=30, message="time far from frame"),
                         F(severity="note", category="other", frame=1, at=1, message=" ")], times, 8.0)
    assert [f.message for f in out] == ["weak opening", "time far from frame"]
    assert out[1].at == 3.0  # snapped to the frame it pointed at


def test_merge_scores_code_and_ai():
    code = {"findings": [{"id": "F1", "severity": "should-fix", "category": "colour", "at": None, "message": "x", "evidence": "", "fix": None}]}
    rev = review.AIReview(summary="ok", scores=review.Scores(hook=5, story=5, pacing=5, typography=5, colour=5, audio=5, creativity=5),
                          findings=[review.AIFinding(severity="must-fix", category="hook", frame=1, at=1, message="y")])
    r = review.merge(code, rev)
    assert not r["passed"] and r["counts"] == {"must-fix": 1, "should-fix": 1, "note": 0}
    assert r["score"] == round(0.5 * (100 - 15 - 6) + 0.5 * 100)
    assert r["findings"][0]["id"] == "A1" and r["findings"][0]["evidence"] == "frame #1"  # must-fix sorts first
    assert review.merge(code, None)["score"] == 94


# ---- settings ------------------------------------------------------------------------------------

def test_audit_settings_round_trip_and_clamp():
    assert review.settings() == review.DEFAULTS
    assert review.save_settings(threshold=150, max_rounds=99) == {"threshold": 100, "max_rounds": review.MAX_ROUNDS_CAP}
    assert review.settings()["threshold"] == 100
    review.save_settings(threshold=75, max_rounds=2)
    assert review.good_enough({"passed": True, "score": 75}, 75) and not review.good_enough({"passed": False, "score": 99}, 75)


@pytest.fixture
def fake(monkeypatch):
    calls, answers = [], {"auditor": [], "editor": []}

    def backend(task, system, prompt, schema, images, model):
        calls.append((task, prompt))
        return answers[task].pop(0)

    ai.set_backend(backend)
    yield calls, answers
    ai.set_backend(None)


def _review(score, must=False):
    return {"summary": "s", "scores": {k: score for k in ("hook", "story", "pacing", "typography", "colour", "audio", "creativity")},
            "findings": [{"severity": "must-fix", "category": "hook", "frame": 1, "at": 0.2, "message": "fix me"}] if must else []}


def test_revise_plan_hands_the_review_to_the_editor(fake):
    from tests.test_audit_justify import _good, _plan_args

    calls, answers = fake
    answers["editor"].append(_good().model_dump())
    spec, dna, log, c, axes, d = _plan_args()
    rev = {"score": 55, "summary": "weak hook", "scores": {"hook": 2}, "findings": [
        {"id": "A1", "severity": "must-fix", "category": "hook", "at": 0.3, "message": "opening is static", "fix": "swap c1"},
        {"id": "F2", "severity": "note", "category": "pacing", "at": None, "message": "a note", "fix": None}]}
    plan, _ = planner.revise_plan(spec, dna, log, c, axes, d, _good(), rev, threshold=80)
    prompt = calls[0][1]
    assert "INDEPENDENT REVIEW" in prompt and "score 55/100, needs 80" in prompt and "weak hook" in prompt
    assert "A1 [must-fix] hook @0.3s: opening is static -> swap c1" in prompt and "a note" not in prompt
    assert '"segment":"f1.s1"' in prompt  # the editor sees its own current plan
    assert plan.beats and all(o.audio for o in plan.ops if o.op == "clip")  # audio modes filled in


@needs_ffmpeg
def test_cli_audit_with_ai(cli, test_clip, fake):
    _calls, answers = fake
    cli("import", str(test_clip))
    answers["auditor"] += [_review(4)]
    r = cli("audit", "--ai")
    assert r["ai"] and r["scores"]["hook"] == 4


# ---- chat Stop hook ------------------------------------------------------------------------------

@needs_ffmpeg
def test_stop_hook_sends_the_edit_back_until_the_pass_mark(cli, test_clip):
    from vedit.editor.agent import ChatSession

    review.save_settings(threshold=80, max_rounds=2)
    cli("import", str(test_clip))
    s = ChatSession(cli.project)
    events = []

    async def emit(ev, persist=True):
        events.append(ev)

    s.emit = emit
    run = lambda: asyncio.run(s._on_stop({"hook_event_name": "Stop", "stop_hook_active": False}, None, {}))  # noqa: E731
    s.turn_head, s.audit_rounds, s.audited_head = s._head(), 0, None
    assert run() == {}  # nothing edited this turn: no audit
    cli("text", "add", "Way down", "--y", "97%", "--end", "3")
    out = run()
    assert out["decision"] == "block" and "safe-zone" in out["reason"] and "pass mark is 80" in out["reason"]
    ev = [e for e in events if e["type"] == "audit"][-1]
    assert ev["round"] == 1 and not ev["reached"] and not ev["final"] and ev["threshold"] == 80
    assert run() == {}  # sent back, but nothing changed since that audit (the agent explained): no second audit
    cli("layer", "set", "t1", "--start", "0.1")  # edits again without fixing
    assert run() == {}  # round 2 = the cap: report, then the user decides
    assert [e for e in events if e["type"] == "audit"][-1]["final"]
    # a fixed edit that clears the mark lets the agent stop on the first audit
    cli("layer", "set", "t1", "--y", "30%")
    s.turn_head, s.audit_rounds, s.audited_head = s._head() - 1, 0, None
    assert run() == {} and [e for e in events if e["type"] == "audit"][-1]["reached"]
    # a stricter mark keeps sending it back even without must-fix findings (here: a title too brief to read)
    review.save_settings(threshold=100)
    cli("layer", "set", "t1", "--end", "0.4")
    s.audit_rounds, s.audited_head = 0, None
    assert run().get("decision") == "block"
    asyncio.run(s.set_auto_audit(True))  # the chat-pane toggle, persisted per project
    assert ChatSession(cli.project).auto_audit_ai


# ---- sound -----------------------------------------------------------------------------------------

def _speechlike(seconds=3.0, sr=16000):
    """Voice-band noise in ~150 ms bursts, 3 a second, with pauses: words and syllables, not a tone."""
    rng = np.random.default_rng(1)
    t = np.arange(int(sr * seconds)) / sr
    x = rng.standard_normal(t.size)
    F = np.fft.rfft(x)
    f = np.fft.rfftfreq(t.size, 1 / sr)
    F[(f < 300) | (f > 3400)] = 0
    x = np.fft.irfft(F, n=t.size)
    gate = ((t * 3) % 1.0) < 0.45
    return (0.3 * x / np.abs(x).max() * gate).astype(np.float32)


def test_speech_score_separates_talking_from_tones_and_noise():
    sr = 16000
    t = np.arange(sr * 3) / sr
    tone_gap = (0.4 * np.sin(2 * np.pi * 440 * t) * ~((t > 1.0) & (t < 1.6))).astype(np.float32)
    rng = np.random.default_rng(2)
    hiss = (0.05 * rng.standard_normal(t.size)).astype(np.float32)
    assert sound.analyse(_speechlike())["voice"] >= 0.8
    assert sound.analyse(tone_gap)["voice"] == 0.0
    assert sound.analyse(hiss)["voice"] == 0.0
    assert sound.analyse(np.zeros(sr, dtype=np.float32))["db"] == -90.0


def _clip_with(audio: np.ndarray, path: Path, sr=16000) -> Path:
    raw = path.with_suffix(".f32")
    raw.write_bytes(audio.astype(np.float32).tobytes())
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=gray:s=320x568:r=30:d={audio.size / sr}",
                    "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", str(raw), "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(path)], check=True)
    return path


@needs_ffmpeg
def test_sound_checks_and_auto(cli, tmp_path):
    rng = np.random.default_rng(3)
    noise = (0.2 * rng.standard_normal(16000 * 3)).astype(np.float32)
    broll = _clip_with(noise, tmp_path / "broll.mp4")
    talk = _clip_with(_speechlike(), tmp_path / "talk.mp4")
    song = tmp_path / "song.wav"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=330:d=3", str(song)], check=True)
    cli("import", str(broll), str(talk))
    cli("music", "add", str(song), "--volume", "0.8", "--no-duck")
    r = cli("sound", "report")
    by = {x["clip"]: x for x in r["clips"]}
    assert not by["c1"]["speech"] and by["c2"]["speech"]
    msgs = " | ".join(f["message"] for f in r["findings"])
    assert "c1 plays its own background sound" in msgs and "fights the speech in c2" in msgs
    assert any(f["category"] == "sound" for f in cli("audit", "--no-render")["findings"])  # part of the audit
    cli("sound", "auto")
    from vedit.project import load as _load
    p = _load(cli.project)
    assert p.clip("c1").volume == 0 and p.clip("c1").meta.audio == "mute"
    assert p.clip("c2").volume == 1 and p.music[0].duck
    left = " | ".join(f["message"] for f in cli("sound", "report")["findings"])
    assert "background sound" not in left and "fights the speech" not in left
    # an explicit mute is a volume choice: the clip still has speech, and the audit says so (live-run regression)
    p = _load(cli.project)
    p.clip("c2").volume = 0.0
    p.clip("c2").meta = p.clip("c2").meta.model_copy(update={"audio": "mute"})
    from vedit.project import save as _save
    _save(cli.project, p)
    assert any("c2 has speech but is muted" in f["message"] for f in cli("sound", "report")["findings"])


# ---- taste memory --------------------------------------------------------------------------------

def test_taste_records_and_prompts():
    from vedit.generate import taste

    assert taste.examples() == ""
    taste.record("keep", {"angle": "a", "hook_text": "Stop scrolling", "concept": {"title": "Tiny hands", "signature_move": "punch_in"}},
                 {"beats": [{"role": "hook"}, {"role": "demo"}], "ops": [{"op": "clip", "why": "macro shot of the drop"}]},
                 {"hook": "question", "pacing": "reference"}, {"audit": {"score": 88}})
    taste.record("discard", {"hook_text": "Meh"}, None, {}, None)
    ex = taste.examples()
    assert "KEPT: hook \"Stop scrolling\"; concept Tiny hands; move punch_in; beats hook>demo; opening: macro shot" in ex
    assert "hook=question" in ex and "pacing" not in ex and "DISCARDED: hook \"Meh\"" in ex
    taste.clear()
    assert taste.load() == []


# ---- which model does which job ------------------------------------------------------------------

def test_model_precedence_and_isolation(monkeypatch):
    import threading

    monkeypatch.delenv("VEDIT_AI_MODEL", raising=False)
    assert ai.model_for("auditor", "best") == ai.QUALITY["best"]["auditor"]
    ai.save_settings({"editor": "anthropic/claude-haiku-5-5"})
    assert ai.model_for("editor") == "anthropic/claude-haiku-5-5"  # saved default beats the tier
    seen = {}

    def job(name, models):
        with ai.use_models(models):
            seen[name] = (ai.model_for("editor"), ai.model_for("auditor"))

    t1 = threading.Thread(target=job, args=("a", {"editor": "anthropic/claude-opus-5-5"}))
    t2 = threading.Thread(target=job, args=("b", {}))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert seen["a"][0] == "anthropic/claude-opus-5-5"  # this generation's pick wins
    assert seen["b"][0] == "anthropic/claude-haiku-5-5"  # the other job is unaffected
    assert ai.model_for("editor") == "anthropic/claude-haiku-5-5"  # restored after the job
    roles = {r["tier"]: r for r in ai.roles()}
    assert roles["editor"]["saved"] == "anthropic/claude-haiku-5-5" and roles["auditor"]["vision"]


def test_vision_roles_reject_blind_models():
    from vedit.editor import models as registry

    registry.upsert_provider({"name": "Blind", "base_url": "https://blind.example/anthropic", "api_key": "k"})
    registry.add_model({"provider": "blind", "id": "text-only", "label": "Text only", "vision": False})
    assert ai.check_models({"editor": "blind/text-only"}) == {"editor": "blind/text-only"}  # editing needs no eyes
    with pytest.raises(ai.AIError, match="Auditor looks at video frames"):
        ai.check_models({"auditor": "blind/text-only"})
    with pytest.raises(ai.AIError, match="Unknown role"):
        ai.check_models({"colorist": "anthropic/claude-opus-5-5"})


def test_roles_api_and_cli(cli):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    c = TestClient(create_app(None, watch=False, chat=False))
    d = c.get("/api/ai/roles?quality=economy").json()
    assert [r["label"] for r in d["roles"]] == ["Watcher", "Creative director", "Editor", "Auditor"]
    assert any(m["key"] == "anthropic/claude-opus-5-5" and m["ready"] for m in d["models"])
    r = c.post("/api/ai/roles", json={"auditor": "anthropic/claude-opus-5-5", "_quality": "economy"}).json()
    assert next(x for x in r["roles"] if x["tier"] == "auditor")["effective"] == "anthropic/claude-opus-5-5"
    assert c.post("/api/ai/roles", json={"auditor": "nope/missing"}).status_code == 400
    c.post("/api/ai/roles", json={"auditor": ""})
    assert ai.settings().get("auditor") is None
    assert [r["label"] for r in cli("ai-models")["roles"]][-1] == "Auditor"


def test_new_format_fields_are_not_hand_edits(cli, test_clip):
    """A setting added to the project format (e.g. canvas.loudness) must not look like a hand edit (live bug)."""
    import json as _json

    cli("import", str(test_clip))
    pdir = cli.project
    h = history.head(pdir)
    snap_file = history._snap_path(pdir, h)
    snap = _json.loads(snap_file.read_text())
    del snap["project"]["canvas"]["loudness"]  # as written before the field existed
    snap_file.write_text(_json.dumps(snap))
    history.sync(pdir)
    assert history.head(pdir) == h  # no "manual edit of project.json" step
    assert history.same_as(pdir, h)
    p = load(pdir)
    p.canvas.background = "#112233"  # a real hand edit still counts
    from vedit.project import save as _save
    _save(pdir, p)
    assert not history.same_as(pdir, h)
    history.sync(pdir)
    assert history.head(pdir) == h + 1

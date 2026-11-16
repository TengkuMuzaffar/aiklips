"""P1 of docs/design/auditor-justification-creativity.md: beat sheets, reasons, plan validation, `vedit audit`."""

import pytest

from vedit import audit as audit_mod
from vedit.ai import client as ai
from vedit.generate import planner
from vedit.generate.spec import BatchSpec, Brief, Creative, EditPlan, Op, PlanBeat
from vedit.project import Beat, ClipMeta, EditNotes, Layer, TextStyle

from .conftest import needs_ffmpeg

LOG = {
    "f1": {"kind": "video", "name": "a.mp4", "duration": 10.0,
           "segments": [{"id": "f1.s1", "from": 0.0, "to": 3.0, "description": "close-up", "quality": 5},
                        {"id": "f1.s2", "from": 3.0, "to": 6.0, "description": "wide", "quality": 3}]},
    "f2": {"kind": "video", "name": "b.mp4", "duration": 4.0,
           "segments": [{"id": "f2.s1", "from": 0.0, "to": 4.0, "description": "hands", "quality": 4}]},
}
BEATS = [PlanBeat(id="b1", role="hook", purpose="stop the scroll"), PlanBeat(id="b2", role="demo", purpose="show it")]


def _good() -> EditPlan:
    return EditPlan(beats=BEATS, ops=[
        Op(op="clip", file="f1", segment="f1.s1", start=0.5, end=2.5, beat="b1", why="serum drop close-up proves the texture"),
        Op(op="clip", file="f2", segment="f2.s1", start=0.0, end=3.0, beat="b2", why="hands apply it: the routine"),
    ])


# ---- plan validation ---------------------------------------------------------------------------

def test_valid_plan_has_no_errors():
    assert planner.validate_plan(_good(), LOG) == []


def test_validation_finds_each_problem():
    plan = _good()
    plan.beats = [PlanBeat(id="b1", role="demo"), PlanBeat(id="b2", role="hook"), PlanBeat(id="b3", role="cta")]
    plan.ops[0].why = " "
    plan.ops[0].start = 2.0
    plan.ops[0].end = 4.0  # outside f1.s1 (0-3)
    plan.ops[1].file = "f1"  # segment belongs to f2
    plan.ops.append(Op(op="clip", file="f1", segment="f9.s9", start=0, end=1, beat="b7", why="x"))
    errs = "\n".join(planner.validate_plan(plan, LOG))
    for needle in ("must have role hook", "missing why", "outside segment f1.s1", "belongs to file f2", "unknown segment f9.s9",
                   "beat 'b7' is not one of", "beat b3 (cta) has no clips"):
        assert needle in errs, needle
    assert "no beats" in "\n".join(planner.validate_plan(EditPlan(ops=_good().ops), LOG))


def test_fill_ranges_takes_the_segment():
    plan = EditPlan(beats=BEATS, ops=[Op(op="clip", segment="f1.s2", beat="b1", why="w")])
    planner.fill_ranges(plan, LOG)
    o = plan.ops[0]
    assert (o.file, o.start, o.end) == ("f1", 3.0, 6.0)


@pytest.fixture
def editor_backend():
    prompts, answers = [], []

    def backend(task, system, prompt, schema, images, model):
        assert task == "editor"
        prompts.append(prompt)
        return answers.pop(0)

    ai.set_backend(backend)
    yield prompts, answers
    ai.set_backend(None)


def _plan_args():
    from vedit.references.dna import StyleDNA

    spec = BatchSpec(refs=[1], footage=["x"], brief=Brief(product="serum", length_s=6))
    dna = StyleDNA()
    axes = {"hook": "reference", "order": "story", "pacing": "reference", "caption_style": "reference",
            "text_animation": "reference", "transitions": "reference", "music_section": "start"}
    d = planner.directives(dna, spec.brief, axes, LOG, False)
    return spec, dna, LOG, Creative(index=1, angle="a", hook_text="Hook"), axes, d


def test_plan_edit_retries_once_with_the_errors(editor_backend):
    prompts, answers = editor_backend
    bad = {"ops": [{"op": "clip", "file": "f1", "start": 0, "end": 2}]}
    answers += [bad, _good().model_dump()]
    plan, _ = planner.plan_edit(*_plan_args())
    assert len(prompts) == 2 and "PROBLEMS" in prompts[1] and "missing why" in prompts[1]
    assert plan.problems == [] and plan.beats[0].role == "hook"


def test_plan_edit_keeps_going_when_still_invalid(editor_backend):
    prompts, answers = editor_backend
    bad = {"ops": [{"op": "clip", "file": "f1", "start": 0, "end": 2}]}
    answers += [bad, bad]
    plan, _ = planner.plan_edit(*_plan_args())
    assert len(prompts) == 2 and plan.problems  # built anyway, problems reported
    assert any(o.op == "clip" for o in plan.ops)


# ---- audit checks (no media) ------------------------------------------------------------------

def _f(report, category, severity=None):
    return [f for f in report if f["category"] == category and (severity is None or f["severity"] == severity)]


def test_hook_check(make_project):
    p = make_project(1)  # one 10 s clip, no title
    assert _f(audit_mod.check_hook(p, None), "hook", "must-fix")
    p.layers.append(Layer(id="t1", type="text", text="Hook", start=0.2, end=2))
    assert not audit_mod.check_hook(p, None)
    p.clips[0].meta = ClipMeta(quality=2, segment="f1.s4")
    assert _f(audit_mod.check_hook(p, None), "hook", "should-fix")


def test_readability_layout_and_overlap(make_project):
    p = make_project(1)
    p.layers += [
        Layer(id="t1", type="text", text="Five words that need time", start=0, end=1.5, y="30%"),
        Layer(id="t2", type="text", text="Bottom", start=0, end=3, y="95%"),
        Layer(id="t3", type="text", text="CLASH", start=1, end=3, y="31%", style=TextStyle(size=90)),
        Layer(id="t4", type="text", text="ok", start=5, end=7, y="30%"),
    ]
    r = audit_mod.check_readability(p, None)
    assert [f["message"][:2] for f in r] == ["t1"]
    lay = audit_mod.check_layout(p, None)
    safe = _f(lay, "safe-zone", "must-fix")
    assert len(safe) == 1 and "t2" in safe[0]["message"] and "bottom" in safe[0]["message"]
    over = _f(lay, "overlap", "must-fix")
    assert len(over) == 1 and "t1 and t3" in over[0]["message"]  # t4 is at the same place but later


def test_caption_zone_is_lower_than_titles(make_project):
    from vedit.project import Word

    p = make_project(1)
    p.layers.append(Layer(id="cap1", type="captions", y="74%", words=[Word(text="hello", start=0, end=0.5), Word(text="there", start=0.5, end=1)]))
    assert not _f(audit_mod.check_layout(p, None), "safe-zone")


def test_typography_footage_pacing_and_justification(make_project):
    p = make_project(2)  # c1 0-5, c2 5-10 of s1
    for i, font in enumerate(["anton", "bangers", "dm-serif"]):
        p.layers.append(Layer(id=f"t{i + 1}", type="text", text="x", start=i, end=i + 1, style=TextStyle(font=font)))
    assert _f(audit_mod.check_typography(p, None), "typography", "should-fix")
    p.clips[1].in_, p.clips[1].out = 1.0, 4.0  # reuses 1-4 s of c1
    assert _f(audit_mod.check_footage(p, None), "footage", "should-fix")
    p.notes = EditNotes(target_shot_s=1.0, beats=[Beat(id="b1", role="hook"), Beat(id="b2", role="cta", purpose="buy")])
    assert len(audit_mod.check_pacing(p, None)) == 1  # c1 is 5 s > 3 x 1 s
    p.clips[0].meta = ClipMeta(why="opening product shot", beat="b1")
    j = audit_mod.check_justification(p, None)
    assert any("c2 has no stated reason" in f["message"] for f in j)
    assert any("Beat b2" in f["message"] and f["severity"] == "should-fix" for f in j)


def test_audit_scores_and_survives_a_broken_check(make_project, monkeypatch):
    p = make_project(1)
    monkeypatch.setattr(audit_mod, "CHECKS", audit_mod.CHECKS + [("boom", lambda p, d: 1 / 0)])
    r = audit_mod.audit(p, None, render=False)
    assert not r["passed"] and r["counts"]["must-fix"] >= 1
    assert any("could not run" in f["message"] for f in r["findings"])
    assert r["score"] == 100 - sum(audit_mod.SEVERITY_COST[f["severity"]] for f in r["findings"])
    assert [f["id"] for f in r["findings"]] == [f"F{i}" for i in range(1, len(r["findings"]) + 1)]


# ---- with media -------------------------------------------------------------------------------

@needs_ffmpeg
def test_audit_and_why_cli(cli, test_clip):
    cli("import", str(test_clip))
    cli("text", "add", "Way down here", "--y", "96%", "--end", "3")
    r = cli("audit")
    cats = {f["category"] for f in r["findings"] if f["severity"] == "must-fix"}
    assert "safe-zone" in cats and not r["passed"]
    assert any(f["category"] == "audio" for f in r["findings"]) or r["score"] <= 100  # loudness was measured
    lufs, peak = audit_mod.loudness(cli.project / ".vedit" / "preview" / "preview.mp4")
    assert lufs is not None and -60 < lufs < 0 and peak is not None
    cli("why", "c1", "Opening shot: the test pattern shows every colour", "--role", "hook")
    from vedit.project import load
    m = load(cli.project).clips[0].meta
    assert (m.why, m.role) == ("Opening shot: the test pattern shows every colour", "hook")
    cli("why", "t1", "states the promise")
    assert load(cli.project).layers[0].why == "states the promise"
    cli("why", "c1", "x", "--role", "villain", ok=False)
    cli("layer", "set", "t1", "--y", "30%")
    assert cli("audit", "--no-render")["passed"]


@needs_ffmpeg
def test_footage_measure_per_segment(test_clip):
    from vedit.generate import footage

    uid = footage.add(test_clip)["id"]
    m = footage.measure(uid)
    segs = footage.segments(uid)
    assert set(m) == {str(s["k"]) for s in segs}
    first = m[str(segs[0]["k"])]
    assert first["motion"] > 0.001 and 0 < first["luma"] < 1  # testsrc2 moves
    assert footage.measure(uid) == m  # cached
    log = footage.shot_log([("f1", uid)])
    assert "measured" in log["f1"]["segments"][0]


@needs_ffmpeg
def test_editor_why_and_audit_api(cli, test_clip):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    cli("import", str(test_clip))
    client = TestClient(create_app(cli.project, watch=False, chat=False))
    r = client.post("/api/op", json={"op": "clip_why", "args": {"clip": "c1", "why": "shows the colours", "role": "demo"}})
    assert r.status_code == 200, r.text
    meta = client.get("/api/state").json()["project"]["clips"][0]["meta"]
    assert (meta["why"], meta["role"]) == ("shows the colours", "demo")
    assert client.post("/api/op", json={"op": "clip_why", "args": {"clip": "c1", "role": "nope"}}).status_code == 400
    a = client.post("/api/audit").json()
    assert {"score", "passed", "findings", "counts"} <= set(a)

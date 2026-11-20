"""References studied as design (layout, type system, elements), seen whole, shown to the editor, and checked for likeness."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from vedit import jobs, review
from vedit.ai import client as ai
from vedit.ai import prompts
from vedit.references import analysis, dna as dnamod
from vedit.references.dna import DesignDNA, StyleDNA

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

DESIGN = {"kind": "motion_graphics", "background": "flat near-black", "layout": "left_aligned", "margins": "8% left edge",
          "type_roles": [{"role": "headline", "font": "space-mono", "size_px": 58, "align": "left", "x": "8%", "y": "30%"},
                         {"role": "meta", "font": "space-mono", "size_px": 14, "color": "#E8552B", "align": "left", "case": "upper"}],
          "elements": [{"kind": "frame", "look": "thin orange rule on the left edge", "color": "#E8552B", "closest": "shape add line"}],
          "motion": {"feel": "smooth", "ease": "smooth"}, "scenes": [{"start": 0, "end": 4, "what": "poster frame"}],
          "rules": ["one accent colour", "mono type, left aligned"]}


@pytest.fixture(scope="module")
def poster(tmp_path_factory) -> Path:
    """10 s, no cuts: a black poster whose centre card changes colour at 6 s (a change without a cut)."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("poster") / "poster.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x050505:s=360x640:d=10:r=10",
                    "-vf", "drawbox=x=20:y=250:w=320:h=140:color=0xFAF2EA:t=fill:enable='lt(t,6)',"
                           "drawbox=x=20:y=250:w=320:h=140:color=0x1AAA4B:t=fill:enable='gte(t,6)',"
                           "drawbox=x=20:y=120:w=200:h=6:color=0xE8552B:t=fill",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


@pytest.fixture
def fake(monkeypatch):
    calls = []
    answers = {"study": StyleDNA(summary="Dark poster motion design", tags=["motion-graphics"]).model_dump() | {"design": DESIGN},
               "auditor": {"score": 1, "carried": ["dark background"], "misses": [
                   {"severity": "must-fix", "frame": 2, "at": 3.0, "message": "Text is centred; the reference is left-aligned on one edge.",
                    "fix": "layer set t1 --x 8%"}]}}
    monkeypatch.setattr(jobs, "AUTOSTART", False)

    def backend(task, system, prompt, schema, images, model):
        calls.append((task, system, prompt, [Path(i).name for i in images]))
        return answers[task]

    ai.set_backend(backend)
    yield calls
    ai.set_backend(None)


def test_timeline_covers_the_whole_video_and_changes_without_cuts():
    energy = [0.01] * 100
    energy[59] = 0.2  # the picture changes at 6 s with no cut
    marks = analysis.timeline_times(10.0, cuts=[], energy=energy, fps=10)
    times = [t for t, _ in marks]
    assert times[0] > 3.0 and times[-1] > 9.0 and len(marks) <= analysis.TIMELINE_FRAMES
    assert any(label.endswith("change") and 6.0 <= t <= 6.6 for t, label in marks)
    assert analysis.timeline_times(3.0) == []  # the hook sheet already covers a short video


def test_design_dna_parses_merges_and_shows_on_the_card():
    a = StyleDNA.model_validate({"summary": "a", "design": DESIGN})
    assert a.design.layout == "left_aligned" and a.design.type_roles[1].case == "upper"
    old = StyleDNA(summary="old study")  # studied before the design pass
    assert old.design is None
    merged = dnamod.merge([old, a], lead=1)
    assert merged.design and merged.design.type_roles[0].font == "space-mono"
    assert any(r["label"] == "Design" and "left aligned" in r["value"] for r in dnamod.card(a))
    assert dnamod.apply_measured(a, {"pacing": {}, "palette": {"background": "#050505", "accents": ["#E8552B"]}}).color.palette["accents"] == ["#E8552B"]


def test_design_kind_decides_motion_mode():
    from vedit.generate import planner

    d = StyleDNA(summary="motion graphics promo", tags=["motion-graphics"])
    d.design = DesignDNA(kind="footage")
    assert not planner.is_motion(d)  # the studied kind wins over words in the summary
    d.design = DesignDNA(kind="hybrid")
    assert planner.is_motion(d)


def test_study_prompt_asks_for_design_and_uses_the_whole_video_sheet():
    assert prompts.PROMPT_VERSION >= 5
    for word in ("type_roles", "left_aligned", "Sheet 3", "elements", "rules"):
        assert word in prompts.STUDY
    assert "design" in StyleDNA.model_json_schema()["properties"]


@needs_ffmpeg
def test_study_makes_the_whole_video_sheet_and_palette(poster, fake):
    from vedit.references import store

    r = store.add(poster, "poster")
    jobs.drain()
    r = store.get(r["id"], full=True)
    assert r["status"] == "ready" and r["dna"]["design"]["layout"] == "left_aligned"
    assert [Path(s).name for s in r["sheets"]] == ["sheet-1.jpg", "sheet-2.jpg", "sheet-3.jpg"]
    task, _system, prompt, images = fake[0]
    assert task == "study" and "sheet-3.jpg" in images and '"palette"' in prompt
    assert r["dna"]["color"]["palette"]["background"]


@needs_ffmpeg
def test_old_reference_is_backfilled_and_restudied(poster, fake):
    from vedit.references import store

    r = store.add(poster, "poster")
    jobs.drain()
    d = Path(store.get(r["id"])["dir"])
    m = json.loads((d / "metrics.json").read_text())  # make it look like a v4 study
    m["sheets"] = ["sheet-1.jpg", "sheet-2.jpg"]
    m.pop("palette")
    (d / "sheet-3.jpg").unlink()
    (d / "metrics.json").write_text(json.dumps(m))
    store._set(r["id"], prompt_version=4)
    assert store.get(r["id"])["stale"]
    assert [Path(x).name for x in store.sheet_paths(r["id"])][-1] == "sheet-3.jpg"   # made on demand
    assert "palette" in json.loads((d / "metrics.json").read_text())
    assert store.restudy_stale() == [r["id"]]
    jobs.drain()
    assert not store.get(r["id"])["stale"]


def test_likeness_is_part_of_the_score():
    code = {"findings": [], "passed": True, "counts": {"must-fix": 0, "should-fix": 0, "note": 0}, "score": 100}
    rm = review.RefMatch(score=1, misses=[review.RefMiss(severity="must-fix", frame=2, at=3.0, message="centred, the reference is left-aligned")])
    out = review.with_reference(dict(code, findings=[]), rm, [1.0, 3.0], 4.0)
    assert out["score"] < 80 and not out["passed"] and out["reference"]["score"] == 1
    assert out["findings"][0]["category"] == "reference" and out["findings"][0]["id"] == "L1"
    generic = review.with_reference(dict(code, findings=[]), review.RefMatch(score=2), [1.0], 2.0)
    assert generic["findings"] and generic["findings"][0]["severity"] == "should-fix"  # generic never passes silently
    same = review.with_reference(dict(code, findings=[]), review.RefMatch(score=5), [1.0], 2.0)
    assert same["score"] == 100 and not same["findings"]
    assert review.with_reference(dict(code), None, [1.0], 2.0)["score"] == 100


@needs_ffmpeg
def test_chat_targets_attached_references_and_the_audit_compares(poster, fake, tmp_path):
    from vedit.audit import audit
    from vedit.editor.agent import SYSTEM_APPEND, ChatSession, _attachment
    from vedit.generate.executor import create_project
    from vedit.history import transaction
    from vedit.ops import overlays
    from vedit.project import load
    from vedit.references import store

    rid = store.add(poster, "poster")["id"]
    jobs.drain()
    pdir = tmp_path / "proj"
    create_project(pdir, "t", "reels")
    with transaction(pdir, "a title") as tx:
        overlays.add_shape(tx.project, "rect", 0, 4, width=600, height=300, x="50%", y="50%")
    # the editor is told to look at the reference itself, with the sheet paths
    line = _attachment(load(pdir), 1, {"kind": "ref", "id": rid, "name": "poster"})
    assert "LOOK AT IT FIRST" in line and "sheet-3.jpg" in line and f"@ref{rid}" in line
    assert "Read the\nreference's contact sheets" in SYSTEM_APPEND or "contact sheets" in SYSTEM_APPEND
    # attaching it makes it the edit's target (kept for later turns, e.g. after a plan is approved)
    chat = ChatSession(pdir)
    chat._target_refs({"attachments": [{"kind": "ref", "id": rid, "name": "poster"}]})
    assert load(pdir).notes.refs == [rid]
    chat._target_refs({"attachments": [{"kind": "ref", "id": rid}]})  # already targeted: no new history step
    # "checks" auditor: code checks + one likeness call; a generic edit can't score 100 any more
    code = audit(load(pdir), pdir, render=True)
    out = review.reference_audit(pdir, dict(code))
    task, system, prompt, images = fake[-1]
    assert task == "auditor" and system == prompts.REF_MATCH and images[-1] == "review-reference.jpg"
    assert "left_aligned" in prompt and "REFERENCE #" in prompt
    assert out["reference"]["score"] == 1 and out["score"] < code["score"] and not out["passed"]
    assert any(f["category"] == "reference" and "left-aligned" in f["message"] for f in out["findings"])
    # no target: nothing extra runs
    n = len(fake)
    with transaction(pdir, "clear") as tx:
        tx.project.notes.refs = []
    assert review.reference_audit(pdir, dict(code))["score"] == code["score"] and len(fake) == n

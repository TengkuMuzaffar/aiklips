"""References are a role a video in the media library has: one file, two studies (content + style).
Plus the brief tags (funnel, audience, pace, tone, must-do) that Generate's prompts and code use."""

import json
import shutil
import time

import pytest

from vedit import jobs
from vedit.generate import brief_tags, planner
from vedit.generate.spec import Brief, EditPlan, Op
from vedit.project import VeditError
from vedit.references.dna import StyleDNA
from tests.test_references_generate import PROMPTS, beat_ref, fake_ai, song  # noqa: F401 - fixtures

AXES = {"hook": "reference", "order": "story", "pacing": "reference", "caption_style": "reference", "text_animation": "reference",
        "transitions": "reference", "music_section": "start", "color_look": "reference", "font": "reference", "structure": "reference"}


def test_ref_add_puts_the_file_in_media_once(beat_ref, fake_ai):
    from vedit import media_library as ml
    from vedit.references import store

    r = store.add(beat_ref, "Beat promo")
    mid = r["media_id"]
    assert mid and store.source_path(store._row(r["id"])).parent.name == mid
    assert not any(store.root().iterdir())  # no second copy under references/
    jobs.drain()
    r = store.get(r["id"], full=True)
    assert r["status"] == "ready" and r["dna"]["summary"] == "Fast beat-cut promo"
    assert (ml._footage()._dir(mid) / "style" / "dna.json").exists()
    m = ml.get(mid)
    assert m["ref_id"] == r["id"] and m["ref_status"] == "ready" and m["status"] == "ready"  # both studies done
    assert [x["id"] for x in ml.list_media(role="reference")] == [mid]
    assert store.add(beat_ref)["id"] == r["id"]  # same content: same reference
    assert store.promote(mid)["id"] == r["id"]


def test_only_videos_can_be_references(song):
    from vedit.generate import footage
    from vedit.references import store

    uid = footage.add(song)["id"]
    with pytest.raises(VeditError, match="only videos"):
        store.promote(uid)


def test_style_study_reuses_the_media_study(beat_ref, fake_ai, monkeypatch):
    from vedit import media_library as ml
    from vedit.ops import captions
    from vedit.references import analysis, store

    mid = ml.add(beat_ref)["id"]
    jobs.drain()
    d = ml._footage()._dir(mid)
    assert json.loads((d / "cuts.json").read_text())  # the media study saved its raw cuts
    (d / "transcript.json").write_text(json.dumps([{"text": "hello", "start": 0.2, "end": 0.5}, {"text": "world", "start": 0.6, "end": 0.9}]))

    def boom(*_a, **_k):
        raise AssertionError("measured twice")

    monkeypatch.setattr(analysis, "detect_cuts", boom)
    monkeypatch.setattr(captions, "transcribe", boom)
    rid = store.promote(mid)["id"]
    jobs.drain()
    r = store.get(rid, full=True)
    assert r["status"] == "ready"
    assert r["metrics"]["transcript"] == "hello world" and len(r["metrics"]["cuts"]) >= 5


def test_old_references_move_into_media_and_keep_their_dna(beat_ref, fake_ai):
    from vedit import media_library as ml
    from vedit.references import store

    sha = store.file_hash(beat_ref)
    old = store.root() / sha[:16]
    old.mkdir(parents=True)
    shutil.copy2(beat_ref, old / "source.mp4")
    dna = StyleDNA(summary="Old study").model_dump()
    (old / "dna.json").write_text(json.dumps(dna))
    (old / "metrics.json").write_text(json.dumps({"duration": 8.0, "sheets": []}))
    now = time.time()
    with store._db() as con:
        rid = con.execute("INSERT INTO refs (sha, name, dir, status, ai, prompt_version, created_at, updated_at) VALUES (?, ?, ?, 'ready', 1, 999, ?, ?)",
                          (sha, "Old one", str(old), now, now)).lastrowid
        con.commit()

    assert store.migrate_to_media() == 1
    assert store.migrate_to_media() == 0  # idempotent
    row = store._row(rid)
    assert row["media_id"] and not old.exists()
    assert store.style(rid).summary == "Old study"  # DNA kept, nothing re-studied
    assert jobs.for_target(f"ref:{rid}") is None
    assert ml.get(row["media_id"])["ref_id"] == rid
    assert store.source_path(row).exists()


def test_removing_the_role_keeps_the_file_and_deleting_the_file_drops_the_role(beat_ref, fake_ai):
    from vedit import media_library as ml
    from vedit.references import store

    r = store.add(beat_ref)
    jobs.drain()
    mid = r["media_id"]
    store.delete(r["id"])
    assert ml.get(mid)["ref_id"] is None and not (ml._footage()._dir(mid) / "style").exists()
    assert ml._footage().meta_full(mid)  # the file is still there
    rid = store.promote(mid)["id"]
    ml.delete(mid, force=True)
    with pytest.raises(VeditError):
        store.get(rid)


def test_resolve_takes_ids_media_and_files(beat_ref, fake_ai):
    from vedit import media_library as ml
    from vedit.references import store

    mid = ml.add(beat_ref)["id"]
    rid = store.resolve(mid)
    assert store.resolve(str(rid)) == rid == store.resolve(f"#{rid}") == store.resolve(beat_ref)
    with pytest.raises(VeditError):
        store.resolve("nope")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_generate_with_a_media_video_as_style_and_brief_tags(beat_ref, test_clip, fake_ai):
    from vedit import media_library as ml
    from vedit.generate import batch, footage
    from vedit.generate.spec import BatchSpec
    from vedit.project import load
    from pathlib import Path

    mid = ml.add(beat_ref)["id"]  # in the media library, never added as a reference
    jobs.drain()
    spec = BatchSpec(refs=[mid], footage=[footage.add(test_clip)["id"]], count=1, review=False, explore=False,
                     brief={"product": "Test app", "length_s": 5, "funnel": "conversion", "pace": "fast",
                            "audience": {"age": ["gen_z"], "region": ["malaysia"]}, "rules": ["no_music"]})
    est = batch.estimate(spec)
    assert est["breakdown"]["study"] == 1  # its style is studied on create
    b = batch.create(spec)
    jobs.drain()
    b = batch.get(b["id"])
    assert b["status"] == "done", (b["error"], [x["error"] for x in b["variations"]])
    saved = BatchSpec.model_validate_json(batch._batch(b["id"])["spec"])
    assert saved.refs == [ml.get(mid)["ref_id"]]
    planner_prompt = next(p for t, p in PROMPTS if t == "planner")
    assert "FUNNEL: Conversion" in planner_prompt and "Gen Z 18-24" in planner_prompt and "PACE: Fast" in planner_prompt
    editor_prompt = next(p for t, p in PROMPTS if t == "editor")
    assert "MUST list" in editor_prompt
    p = load(Path(batch._var(b["variations"][0]["id"])["dir"]))
    assert not p.music  # "no music" rule
    assert "funnel conversion" in p.notes.brief  # the auditor reads the tags too


# ---- brief tags (no media) --------------------------------------------------------------------

def test_brief_rejects_unknown_tags_and_old_briefs_load():
    with pytest.raises(ValueError):
        Brief(pace="warp")
    with pytest.raises(ValueError):
        Brief(audience={"age": ["toddlers"]})
    with pytest.raises(ValueError):
        Brief(tone=["playful", "premium", "funny", "bold"])
    b = Brief.model_validate_json('{"product": "old batch", "notes": "x"}')
    assert b.pace == "reference" and b.funnel is None and brief_tags.describe(b) == ""


def test_pace_sets_shot_length_and_curve():
    dna = StyleDNA(pacing={"avg_shot_s": 2.0})
    assert planner.directives(dna, Brief(), AXES, {}, False)["avg_shot_s"] == 2.0
    assert planner.directives(dna, Brief(pace="fast"), AXES, {}, False)["avg_shot_s"] == pytest.approx(1.1)
    faster = planner.directives(dna, Brief(pace="fast"), {**AXES, "pacing": "faster"}, {}, False)
    assert faster["avg_shot_s"] < 1.1  # the pacing axis varies around the brief's pace
    d = planner.directives(dna, Brief(pace="pulse"), AXES, {}, False)
    assert d["pace_curve"] == [0.7, 1.8, 0.9]
    even = EditPlan(ops=[Op(op="clip", start=0, end=1.8) for _ in range(8)])
    assert any("hook" in e for e in planner._brief_problems(even, d))
    shaped = EditPlan(ops=[Op(op="clip", start=0, end=x) for x in (0.7, 0.7, 1.8, 1.8, 1.8, 1.8, 0.9, 0.9)])
    assert not planner._brief_problems(shaped, d)


def test_rules_change_the_directives():
    dna = StyleDNA()
    assert planner.directives(dna, Brief(), AXES, {}, False)["music_make"]
    d = planner.directives(dna, Brief(rules=["no_music", "no_text_first_1s"]), AXES, {}, False)
    assert d["music_make"] is None and d["music"] is None
    plan = EditPlan(ops=[Op(op="text", text="Hi", start=0.2, end=1)])
    assert any("first second" in e for e in planner._brief_problems(plan, d))


def test_funnel_biases_hooks():
    axes = planner.assign_axes(3, ["hook"], seed="s", prefer={"hook": brief_tags.funnel_bias(Brief(funnel="conversion"))["hooks"]})
    assert axes[0]["hook"] == "reference" and {axes[1]["hook"], axes[2]["hook"]} == {"bold_claim", "stat"}

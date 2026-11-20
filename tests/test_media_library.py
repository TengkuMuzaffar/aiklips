"""Media library: files are studied once in the background and reused; the planner sees layered context."""

import json
import re

import pytest

from vedit import jobs
from vedit.ai import client as ai


@pytest.fixture
def fake_ai(monkeypatch):
    class Calls(list):
        fail: dict

    calls = Calls()
    monkeypatch.setattr(jobs, "AUTOSTART", False)
    fail = {"footage": False}

    def backend(task, system, prompt, schema, images, model):
        calls.append(task)
        if task == "footage":
            if fail["footage"]:
                raise ai.AIError("no login")
            ids = re.findall(r"^(f\d+\.s\d+):", prompt, re.M)
            return {"segments": [{"id": i, "description": f"shot {i}", "quality": 4, "subject": "test card",
                                  "good_for": ["hook"]} for i in ids]}
        if task == "classify":
            ids = re.findall(r"^(m\d+): ", prompt, re.M)
            return {"files": [{"id": i, "role": "footage", "reason": "fake"} for i in ids]}
        raise AssertionError(task)

    ai.set_backend(backend)
    calls.fail = fail
    yield calls
    ai.set_backend(None)


def test_import_studies_once_and_reuses(test_clip, fake_ai):
    from vedit import media_library as ml

    row = ml.add(test_clip)
    assert row["status"] == "queued"
    jobs.drain()
    row = ml.get(row["id"], full=True)
    assert row["status"] == "ready" and row["study_version"] >= 1
    assert fake_ai.count("footage") == 1
    assert row["digest"]["usable_s"] > 0 and row["digest"]["subjects"] == ["test card"]
    assert all(s["description"].startswith("shot") for s in row["segments"])

    again = ml.add(test_clip)  # the same file again (another project / batch): nothing re-studied
    jobs.drain()
    assert again["id"] == row["id"] and fake_ai.count("footage") == 1
    assert len(ml.list_media()) == 1


def test_new_footage_prompt_restudies_only_the_ai_part(test_clip, fake_ai, monkeypatch):
    from vedit import media_library as ml
    from vedit.ai import prompts

    uid = ml.add(test_clip)["id"]
    jobs.drain()
    measured = (ml._footage()._dir(uid) / "measure.json").stat().st_mtime_ns
    assert ml.restudy_stale() == []
    monkeypatch.setattr(prompts, "FOOTAGE_VERSION", prompts.FOOTAGE_VERSION + 1)
    assert ml.restudy_stale() == [uid]
    jobs.drain()
    assert fake_ai.count("footage") == 2
    assert (ml._footage()._dir(uid) / "measure.json").stat().st_mtime_ns == measured  # code measurements kept
    assert ml.get(uid)["status"] == "ready"


def test_ai_failure_leaves_a_usable_partial_file(test_clip, fake_ai):
    from vedit import media_library as ml

    fake_ai.fail["footage"] = True
    uid = ml.add(test_clip)["id"]
    jobs.drain()
    row = ml.get(uid)
    assert row["status"] == "partial" and "could not log" in row["error"]
    assert ml.wait_list([uid]) == []  # Generate doesn't wait on it
    assert ml.restudy_stale() == []    # and it isn't re-sent on every start


def test_user_overrides_win_and_survive_restudy(test_clip, fake_ai):
    from vedit import media_library as ml
    from vedit.generate import footage

    uid = ml.add(test_clip)["id"]
    jobs.drain()
    ml.update(uid, tags=["Product", "b-roll"], segment_overrides={"1": {"description": "hero shot of the box", "quality": 5}})
    ml.restudy(uid)
    jobs.drain()
    seg = footage.shot_log([("f1", uid)])["f1"]["segments"][0]
    assert seg["description"] == "hero shot of the box" and seg["quality"] == 5
    assert ml.get(uid)["tags"] == ["b-roll", "product"]
    assert ml.list_media(q="hero shot") and not ml.list_media(q="nothing like it")


def test_delete_refused_while_in_use(test_clip, fake_ai):
    from vedit import media_library as ml
    from vedit.project import VeditError

    uid = ml.add(test_clip)["id"]
    ml.record_use([uid], "batch", "b1")
    with pytest.raises(VeditError):
        ml.delete(uid)
    ml.delete(uid, force=True)
    assert ml.list_media() == []


def test_backfill_registers_old_uploads(test_clip, fake_ai):
    from contextlib import closing

    from vedit import media_library as ml

    uid = ml.add(test_clip)["id"]
    with closing(ml._db()) as con, con:  # as if made by an app version without the library
        con.execute("DELETE FROM media")
    assert ml.backfill() == 1 and ml.get(uid)["kind"] == "video"


# ---- layered context ------------------------------------------------------------------------

def _seg(fid, k, q=3, talking=False, voice=0.0, good_for=()):
    return {"id": f"{fid}.s{k}", "from": float(k - 1), "to": float(k), "description": f"d{k}", "quality": q,
            "talking": talking, "good_for": list(good_for), "measured": {"motion": 0.02, "luma": 0.5, "voice": voice, "audio_db": -20}}


def _video(fid, n, **kw):
    return {"name": f"{fid}.mp4", "kind": "video", "duration": float(n), "has_audio": True, "digest": {},
            "segments": [_seg(fid, k, **kw) for k in range(1, n + 1)]}


@pytest.mark.parametrize("log,length,recipe,sound", [
    ({"f1": {"name": "a.png", "kind": "image", "duration": None, "size": "10x10"}}, 20, "image_motion", "make_music"),
    ({"f1": _video("f1", 6, voice=0.9)}, 20, "speech_led", "clip_sound"),
    ({"f1": _video("f1", 6)}, 20, "scarce_footage", "make_music"),
    ({"f1": _video("f1", 100)}, 20, "abundant_pick_best", "make_music"),
    ({"f1": _video("f1", 25), "m1": {"name": "s.wav", "kind": "audio", "duration": 20, "bpm": 120, "beats": 40},
      "m2": {"name": "t.wav", "kind": "audio", "duration": 20, "bpm": 90, "beats": 30}}, 20, "balanced", "music_each"),
])
def test_situation_recipes(log, length, recipe, sound):
    from vedit.generate import context

    sit = context.situation(log, length)
    assert (sit["recipe"], sit["sound"]) == (recipe, sound)


def test_context_stays_within_budget_and_keeps_every_file():
    from vedit.generate import context

    log = {f"f{i}": _video(f"f{i}", 60) for i in range(1, 13)}  # 12 files x 60 segments
    log["f3"]["segments"][10]["quality"] = 5
    log["f3"]["segments"][10]["good_for"] = ["hook"]
    text = context.footage_block(log, 20, budget=120, roles=["hook"])
    lines = re.findall(r"^  (f\d+\.s\d+) \(", text, re.M)
    assert len(lines) == 120
    assert {x.split(".")[0] for x in lines} == set(log)  # no file disappears
    assert "f3.s11" in lines                               # the strong hook shot makes the cut
    assert "RECIPE abundant_pick_best" in text


def test_media_api(test_clip, fake_ai):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    c = TestClient(create_app(None, watch=False, chat=False))
    with test_clip.open("rb") as f:
        up = c.post("/api/media", files=[("files", ("clip.mp4", f, "video/mp4"))]).json()["added"][0]
    jobs.drain()
    lst = c.get("/api/media").json()
    assert lst["bytes"] > 0 and lst["media"][0]["status"] == "ready" and lst["media"][0]["duration"]
    m = c.get(f"/api/media/{up['id']}").json()
    assert m["segments"] and m["digest"]["subjects"] == ["test card"]
    m = c.patch(f"/api/media/{up['id']}", json={"name": "Hero clip", "tags": ["promo"], "overrides": {"1": {"quality": 5}}}).json()
    assert m["name"] == "Hero clip" and m["tags"] == ["promo"] and m["overrides"]["1"]["quality"] == 5
    assert c.get(f"/api/media/{up['id']}/thumb.jpg").status_code == 200
    assert c.get(f"/api/media/{up['id']}/file").status_code == 200
    assert c.post(f"/api/media/{up['id']}/restudy").json()["status"] == "queued"
    assert c.delete(f"/api/media/{up['id']}").status_code == 200
    assert c.get("/api/media").json()["media"] == []


# ---- editor / CLI ---------------------------------------------------------------------------------

def test_cli_import_goes_through_the_library(cli, test_clip, fake_ai):
    from vedit import media_library as ml

    cli("import", str(test_clip))
    info = cli("info")
    src = info["sources"]["s1"]
    uid = ml.id_for_path(src["path"])
    assert uid and src["path"] != str(test_clip)        # points at the library copy, not the original
    assert src["study"]["status"] == "queued"           # the app's worker would study it in the background

    shown = cli("media", "show", "s1")                 # no app running: studied right here, once
    assert shown["status"] == "ready" and shown["segments"][0]["description"].startswith("shot")
    assert fake_ai.count("footage") == 1
    assert cli("info")["sources"]["s1"]["study"]["subjects"] == ["test card"]

    cli("media", "note", "s1", "1", "--text", "logo reveal", "--quality", "5")
    assert cli("media", "show", "s1")["segments"][0]["description"] == "logo reveal"
    assert cli("media", "tag", "s1", "promo", "Brand")["tags"] == ["brand", "promo"]
    assert [m["id"] for m in cli("media", "list", "--search", "logo reveal")["media"]] == [uid]
    assert ml.get(uid)["used_in"][0]["kind"] == "project"

    cli("import", str(test_clip), "--no-library")       # the old way: the original file, not studied
    assert cli("info")["sources"]["s2"]["path"] == str(test_clip)
    cli("media", "show", "s2", ok=False)
    assert fake_ai.count("footage") == 1                # same file again in the library would cost nothing either


def test_editor_drop_import_uses_the_library(cli, test_clip, fake_ai, tmp_path):
    import shutil

    from vedit import media_library as ml
    from vedit.editor import actions

    tmp = tmp_path / "upload-tmp"
    shutil.copy(test_clip, tmp)
    r = actions.import_file(cli.project, "My Clip.MP4", tmp, True, None)
    src = cli("info")["sources"][r["source"]]
    uid = ml.id_for_path(src["path"])
    assert uid and ml.get(uid)["name"] == "My Clip.MP4" and not tmp.exists()
    assert not (cli.project / "media" / "My Clip.MP4").exists()  # moved into the library, not copied twice


def test_long_recordings_are_logged_by_samples(test_clip, fake_ai, monkeypatch):
    from vedit import media_library as ml
    from vedit.generate import footage

    monkeypatch.setattr(ml, "MAX_AI_SEGMENTS", 1)
    uid = ml.add(test_clip)["id"]
    jobs.drain()
    assert len(footage.segments(uid)) > 1
    assert ml.get(uid)["status"] == "ready"  # the sampled segments were logged: complete, not partial
    assert footage.sample(list(range(100)), 10) == list(range(0, 100, 10))


def test_library_files_reach_the_editor_and_the_ai(cli, test_clip, fake_ai):
    """Files uploaded elsewhere (Home → Media, Generate) can be used in any project: listed for the chat AI,
    imported by library id (CLI) or from the editor's library, never copied twice or added twice."""
    from vedit import media_library as ml
    from vedit.editor import actions
    from vedit.project import load, resolve_media

    row = ml.add(test_clip)
    jobs.drain()
    p = load(cli.project)
    brief = ml.library_brief(cli.project, p)
    assert brief and brief[0].startswith(f"  {row['id']} ") and "test card" in brief[0]

    first = actions.import_media(cli.project, row["id"], append=True)  # the editor's library drag / + button
    assert first["clip"] and not first.get("existing")
    p = load(cli.project)
    assert row["id"] in ml.project_ids(cli.project, p)
    assert ml.library_brief(cli.project, p) == []  # in the project now: no longer offered as "not in this project"
    again = actions.import_media(cli.project, row["id"])  # dragged again: the same source, not a second one
    assert again["source"] == first["source"] and again.get("existing")

    out = cli("import", row["id"], "--no-append")  # `vedit import <library id>`: the library's copy, not a new one
    sid = out["added"][0]["source"]
    p = load(cli.project)
    assert ml.id_for_path(resolve_media(cli.project, p.sources[sid].path)) == row["id"]
    assert len(ml.list_media()) == 1

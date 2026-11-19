"""Music loops from the built-in sequencer + synth: every style renders a seamless loop exactly `bars` long, the
harmony helpers read keys and progressions, the CLI and the editor action put a looping ducked track on the timeline,
`--from` changes a loop in place, and the AI only fills the numbers."""

import io
import time
import wave

import numpy as np
import pytest

from vedit import musicmaker as mm
from vedit.ai import client as ai
from vedit.editor import actions
from vedit.project import load


@pytest.mark.parametrize("key", list(mm.STYLES))
def test_every_style_renders_a_seamless_loop_quickly(key):
    p = mm.style(key)
    t = time.time()
    wav = mm.render(p)
    assert time.time() - t < 8, key
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, mm.SR)
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2) / 32768
    assert len(x) == round(mm.loop_seconds(p) * mm.SR)  # exactly the loop
    assert np.isfinite(x).all() and 0.5 < np.abs(x).max() <= p.volume + 1e-3
    assert np.sqrt(np.mean(x ** 2)) > 0.05  # not silent
    steps = np.abs(np.diff(x, axis=0)).max(axis=1)
    assert np.abs(x[0] - x[-1]).max() <= np.percentile(steps, 99.9), key  # the wrap is an ordinary step


def test_seed_changes_the_melody_not_the_chords():
    a, b = mm.style("pop"), mm.style("pop", seed=99)
    prog = mm._progression(a)
    assert prog == mm._progression(b)
    la = mm._lead_notes(a, prog, np.random.default_rng(a.seed + 3))
    lb = mm._lead_notes(b, prog, np.random.default_rng(b.seed + 3))
    assert la != lb
    assert mm.render(a) == mm.render(mm.style("pop"))  # reproducible


def test_melody_stays_in_key_and_lands_on_chord_tones():
    p = mm.style("pop")
    prog = mm._progression(p)
    notes = mm._lead_notes(p, prog, np.random.default_rng(1))
    scale = {(mm.key_pc(p.key) + s) % 12 for s in mm.SCALES[p.scale]}
    assert notes and all(m % 12 in scale for _, _, m, _ in notes)
    strong = [(s, m) for s, _, m, _ in notes if int(s) % 8 == 0]
    assert all(m % 12 in mm._chord_at(prog, int(s)) for s, m in strong)


def test_chords_and_bass_follow_the_progression():
    p = mm.style("synthwave", key="C", scale="minor", chords=[1, 6])
    prog = mm._progression(p)
    assert prog[0][2] == [0, 3, 7] and prog[1][2] == [8, 0, 3]  # Cm then Ab
    bass = mm._bass_notes(p, prog, [])
    assert {m % 12 for s, _, m, _ in bass if s < 16} == {0} and {m % 12 for s, _, m, _ in bass if 16 <= s < 32} == {8}
    assert mm._chord_pcs(mm.style("lofi", key="C", scale="major"), 2) == [2, 5, 9, 0]  # Dm7


def test_keys_chords_and_lengths_parse():
    assert mm.parse_key("Am") == ("A", "minor")
    assert mm.parse_key("F# minor") == ("F#", "minor")
    assert mm.parse_key("Eb") == ("D#", None)
    assert mm.parse_key("Bb major") == ("A#", "major")
    assert mm.parse_key("C") == ("C", None)
    with pytest.raises(ValueError):
        mm.parse_key("H")
    assert mm.parse_chords("1,5,6,4") == [1, 5, 6, 4]
    assert mm.parse_chords("i VI III VII") == [1, 6, 3, 7]
    assert mm.parse_chords("ii7-V7-I") == [2, 5, 1]
    with pytest.raises(ValueError):
        mm.parse_chords("1, X")
    assert mm.bars_for(15, 128) == 8 and mm.bars_for(5, 120) == 2 and mm.bars_for(200, 60) == 16
    with pytest.raises(ValueError):
        mm.MusicParams(bpm=60, bars=32)  # 128 s: too long
    with pytest.raises(ValueError):
        mm.MusicParams(kick="x..y")
    with pytest.raises(ValueError):
        mm.style("polka")
    assert mm.style("lo-fi").style == "lofi" and mm.style("80s").style == "synthwave"


def test_moods_nudge_the_numbers():
    p = mm.style("pop")
    sad = mm.apply_mood(p, "sad")
    assert sad.scale == "minor" and sad.bpm < p.bpm and sad.brightness < p.brightness
    assert mm.apply_mood(p, "energetic").bpm > p.bpm
    with pytest.raises(ValueError):
        mm.apply_mood(p, "grumpy")


def test_pump_ducks_the_synths_on_the_kick():
    on = mm.synth(mm.style("house", lead="none", drums_level=0, pump=1.0, room=0))
    step = int(60 / 124 / 4 * mm.SR)
    near = np.abs(on[int(0.02 * mm.SR):int(0.06 * mm.SR)]).mean()  # just after the first kick
    late = np.abs(on[3 * step:4 * step]).mean()  # before the next one
    assert near < 0.6 * late


def test_cli_makes_a_looping_ducked_track(cli, tmp_path):
    r = cli("music", "make", "trap", "--key", "Am", "--bpm", "150", "--chords", "i VI VII", "--length", "6",
            "--set", "lead=none", "--volume", "0.3", "--for", "20")
    prm = r["params"]
    assert (prm["key"], prm["scale"], prm["bpm"], prm["chords"], prm["lead"]) == ("A", "minor", 150, [1, 6, 7], "none")
    assert prm["bars"] == 4 and abs(r["loop_seconds"] - 6.4) < 0.01
    p = load(tmp_path / "proj")
    m = next(x for x in p.music if x.id == r["music"])
    assert (m.loop, m.duck, m.volume, m.start, m.end) == (True, True, 0.3, 0.0, 20.0)
    path = tmp_path / "proj" / p.sources[m.source].path
    assert path.exists() and "music" in path.parts and path.with_suffix(".json").exists()
    assert abs(p.sources[m.source].duration - 6.4) < 0.01
    cli("music", "make", "lofi", "--set", "nope=1", ok=False)
    cli("music", "make", "polka", ok=False)
    assert "lofi" in str(cli("music", "styles")["styles"])


def test_cli_from_changes_a_loop_in_place(cli, tmp_path):
    r = cli("music", "make", "pop", "--bars", "4", "--volume", "0.2")
    r2 = cli("music", "make", "--from", r["music"], "--bpm", "128", "--mood", "dark")
    assert r2["music"] == r["music"]
    prm = r2["params"]
    assert prm["style"] == "pop" and prm["scale"] == "phrygian" and prm["bars"] == 4 and prm["bpm"] == 128
    p = load(tmp_path / "proj")
    assert len(p.music) == 1 and p.music[0].volume == 0.2
    r3 = cli("music", "make", "--from", r["music"], "--vary", "1")
    assert r3["params"]["seed"] != prm["seed"] and r3["params"]["chords"] == prm["chords"]
    cli("sfx", "add", "pop")
    sfx_id = load(tmp_path / "proj").music[-1].id
    cli("music", "make", "--from", sfx_id, ok=False)  # not a generated loop


def test_editor_action_makes_a_loop(cli, tmp_path):
    actions.apply(tmp_path / "proj", "music_make", {"style": "chiptune", "start": 1, "volume": 0.3})
    p = load(tmp_path / "proj")
    m = p.music[-1]
    assert (m.start, m.volume, m.loop, m.duck) == (1.0, 0.3, True, True) and "loop-chiptune" in p.sources[m.source].path


def test_ai_design_fills_the_numbers():
    calls = []

    def backend(task, system, prompt, schema, images, model):
        calls.append((task, prompt))
        return {"params": {**mm.style("ambient").model_dump(), "name": "calm morning", "bpm": 64}, "note": "soft"}

    ai.set_backend(backend)
    try:
        d = mm.design("calm music for a sunrise timelapse", mm.style("pop"))
    finally:
        ai.set_backend(None)
    assert calls[0][0] == "music" and "sunrise timelapse" in calls[0][1] and '"pop"' in calls[0][1]
    assert d["params"]["bpm"] == 64 and d["params"]["name"] == "calm morning" and d["note"] == "soft"
    with pytest.raises(ValueError):
        mm.design(" ")


def test_editor_api_previews_moods_and_lists_loops(cli, tmp_path):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    client = TestClient(create_app(cli.project, watch=False, chat=False))
    st = client.get("/api/music/styles").json()
    assert {s["key"] for s in st["styles"]} == set(mm.STYLES) and "dark" in st["moods"] and st["scale_steps"]["minor"][2] == 3
    prm = mm.style("chiptune", bars=2).model_dump()
    r = client.post("/api/music/preview", json={"params": prm})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert client.post("/api/music/preview", json={"params": {**prm, "kick": "zz"}}).status_code == 400
    assert client.post("/api/music/mood", json={"params": prm, "mood": "sad"}).json()["params"]["scale"] == "minor"
    assert client.get("/api/music/loops").json()["loops"] == []
    actions.apply(cli.project, "music_make", {"params": prm, "start": 2})
    loops = client.get("/api/music/loops").json()["loops"]
    assert len(loops) == 1 and loops[0]["params"]["style"] == "chiptune" and loops[0]["start"] == 2
    actions.apply(cli.project, "music_make", {"params": {**prm, "bpm": 100}, "replace": loops[0]["id"]})
    loops2 = client.get("/api/music/loops").json()["loops"]
    assert [l["id"] for l in loops2] == [loops[0]["id"]] and loops2[0]["params"]["bpm"] == 100 and loops2[0]["start"] == 2

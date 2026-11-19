"""Sound effects from the built-in synth: presets render, knobs move the numbers, the CLI and the editor action add a
music track that plays the sound once, and the AI only fills the synth's numbers."""

import io
import math
import wave

import numpy as np
import pytest

from vedit import sfx
from vedit.ai import client as ai
from vedit.editor import actions
from vedit.project import load


@pytest.mark.parametrize("key", list(sfx.PRESETS))
def test_every_preset_renders_a_clean_wav(key):
    p = sfx.preset(key)
    with wave.open(io.BytesIO(sfx.render(p))) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, sfx.SR)
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2") / 32768
    assert np.isfinite(x).all() and 0.05 < np.abs(x).max() <= p.volume + 1e-3  # audible, never clipped
    if p.loop:
        assert len(x) // 2 == int(p.duration * sfx.SR)  # exactly one loop long
    else:
        assert len(x) // 2 >= int(p.duration * sfx.SR) - 2
        assert abs(x[-2:]).max() < 0.01  # ends without a click


def test_pitch_and_length_knobs():
    base = sfx.preset("riser")
    up = sfx.preset("riser", pitch=12, length=1.0)
    assert up.pitch_start == pytest.approx(base.pitch_start * 2) and up.pitch_end == pytest.approx(base.pitch_end * 2)
    assert up.duration == 1.0 and up.attack == pytest.approx(base.attack * 1.0 / base.duration)
    click = sfx.preset("mouse click", pitch=-12, length=0.06)
    assert click.body_tune == -12 and click.hits[0].at == pytest.approx(0.085 * 2) and click.body_ring == pytest.approx(2)
    assert sfx.preset("pop", pitch=60).pitch_end == 12000  # clamped
    with pytest.raises(ValueError):
        sfx.preset("trumpet")


def test_variations_are_distinct_takes_of_the_same_sound():
    a = sfx.preset("mouse click")
    takes = sfx.variations(a, 6, 0.25, seed=1)
    assert len({t.seed for t in takes}) == 6 and all(t.name == "mouse click" for t in takes)
    for t in takes:
        assert len(t.modes) == len(a.modes) and len(t.hits) == 1  # same kind of sound
        assert t.modes[0].freq != a.modes[0].freq and abs(math.log2(t.modes[0].freq / a.modes[0].freq)) < 1
    assert sfx.vary(a, seed=3) == sfx.vary(a, seed=3)  # reproducible
    wavs = {sfx.render(t) for t in takes}
    assert len(wavs) == 6


def _env(x, ms):
    s = int(ms * sfx.SR / 1000)
    seg = np.abs(x[s:s + int(0.005 * sfx.SR), 0])
    return float(seg.max()) if len(seg) else 0.0


def test_clicks_are_clicks():
    """A click = a sharp start that dies within ~20 ms; a mouse click has a quieter release ~85 ms later."""
    for key in ("mouse click", "soft click", "ui tick", "trackpad tap", "pen click", "keyboard key"):
        x = sfx.synth(sfx.preset(key))
        peak_at = int(np.argmax(np.abs(x[:, 0])))
        assert peak_at < 0.004 * sfx.SR, key  # loudest in the first 4 ms
        assert _env(x, 25) < 0.25 * _env(x, 0), key  # and gone fast
    x = sfx.synth(sfx.preset("mouse click"))
    assert _env(x, 50) < 0.05 and 0.2 * _env(x, 0) < _env(x, 85) < _env(x, 0)


def test_cli_set_takes_json_lists_and_vary(cli, tmp_path):
    r = cli("sfx", "add", "click", "--set", 'modes=[{"freq": 3000, "decay": 0.004, "gain": 1}]', "--vary", "2")
    assert len(r["params"]["modes"]) == 1 and r["params"]["name"] == "mouse click"


def test_cli_adds_a_one_shot_track(cli, tmp_path):
    r = cli("sfx", "add", "impact", "--at", "1.5", "--pitch", "-3", "--set", "echo=0.2", "--volume", "0.6")
    p = load(tmp_path / "proj")
    m = next(x for x in p.music if x.id == r["music"])
    assert (m.start, m.volume, m.loop, m.duck) == (1.5, 0.6, False, False)
    path = tmp_path / "proj" / p.sources[m.source].path
    assert path.exists() and path.suffix == ".wav" and "sfx" in path.parts
    assert r["params"]["echo"] == 0.2
    cli("sfx", "add", "pop", "--set", "nope=1", ok=False)
    assert "mouse click" in str(cli("sfx", "list")["presets"])


def test_editor_action_adds_custom_params(cli, tmp_path):
    prm = sfx.preset("ding", name="my ding").model_dump()
    actions.apply(tmp_path / "proj", "sfx_add", {"params": prm, "start": 2, "volume": 0.5})
    p = load(tmp_path / "proj")
    assert p.music[-1].start == 2 and "my-ding" in p.sources[p.music[-1].source].path


def test_ai_design_fills_the_numbers():
    calls = []

    def backend(task, system, prompt, schema, images, model):
        calls.append((task, prompt))
        return {"params": {**sfx.preset("whoosh").model_dump(), "name": "soft whoosh", "filter_end": 3000}, "note": "airy"}

    ai.set_backend(backend)
    try:
        d = sfx.design("soft whoosh for a slide", sfx.preset("pop"))
    finally:
        ai.set_backend(None)
    assert calls[0][0] == "sfx" and "soft whoosh for a slide" in calls[0][1] and "pop" in calls[0][1]
    assert d["params"]["filter_end"] == 3000 and d["note"] == "airy"
    with pytest.raises(ValueError):
        sfx.design("  ")


def test_loops_are_seamless_and_quick():
    import time

    for key in ("rain", "heavy rain", "wind", "fire", "applause"):
        t = time.time()
        x = sfx.synth(sfx.preset(key))
        assert time.time() - t < 3, key  # previews must stay interactive
        steps = np.abs(np.diff(x, axis=0)).max(axis=1)
        seam = np.abs(x[0] - x[-1]).max()
        assert seam <= np.percentile(steps, 99.5), key  # the jump from the end back to the start is an ordinary step


def test_drizzle_is_separate_drops_and_rain_is_a_dense_wash():
    def crest(key):  # peak / rms of the 2 ms loudness envelope: high = separate drops, low = continuous
        x = np.abs(sfx.synth(sfx.preset(key, tone=0))).max(axis=1)
        win = int(0.002 * sfx.SR)
        env = x[: len(x) // win * win].reshape(-1, win).max(axis=1)
        return float(env.max() / np.sqrt(np.mean(env ** 2)))
    assert crest("drizzle") > 1.5 * crest("rain") and crest("rain") > crest("heavy rain")


def test_vowels_put_the_first_formant_where_it_belongs():
    def f1(v):
        x = sfx.synth(sfx.SfxParams(wave="voice", vowel=v, pitch_start=130, pitch_end=130, duration=0.8, filter_start=19500, filter_end=19500))[:, 0]
        X = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        f = np.fft.rfftfreq(len(x), 1 / sfx.SR)
        S = np.convolve(X, np.ones(200) / 200, "same")  # the spectral envelope
        band = (f > 150) & (f < 1000)
        return float(f[band][np.argmax(S[band])])
    assert f1("a") == pytest.approx(730, rel=0.15) and f1("i") == pytest.approx(300, rel=0.2)


def test_loop_plays_for_hold_or_to_the_end(cli, tmp_path):
    r = cli("sfx", "add", "rain", "--at", "1", "--for", "5")
    p = load(tmp_path / "proj")
    m = next(x for x in p.music if x.id == r["music"])
    assert m.loop and m.end == 6 and m.fade_in > 0
    r = cli("sfx", "add", "wind")
    m = next(x for x in load(tmp_path / "proj").music if x.id == r["music"])
    assert m.loop and m.end is None
    mid = cli("sfx", "add", "scream", "--at", "2")["music"]
    m = next(x for x in load(tmp_path / "proj").music if x.id == mid)
    assert not m.loop  # one-shots play once

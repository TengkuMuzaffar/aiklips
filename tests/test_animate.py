import pytest

from vedit.ops.animate import EASINGS, PRESETS, ease, props_at
from vedit.project import Canvas, Keyframe, Layer, Preset, VeditError

C = Canvas()


@pytest.mark.parametrize("name", list(EASINGS))
def test_easings_hit_endpoints(name):
    assert ease(name, 0.0) == pytest.approx(0.0, abs=1e-6)
    assert ease(name, 1.0) == pytest.approx(1.0, abs=1e-6)


def test_unknown_easing():
    with pytest.raises(VeditError):
        ease("wobbly", 0.5)


def test_keyframes_interpolate_with_percent_positions():
    l = Layer(id="t", type="text", text="x", keyframes=[Keyframe(t=0, x="0%", ease="linear"), Keyframe(t=1, x="100%")])
    assert props_at(l, 0.5, 2.0, C)["x"] == pytest.approx(540)
    assert props_at(l, 5.0, 6.0, C)["x"] == pytest.approx(1080)


def test_pop_in_and_fade_out():
    l = Layer(id="t", type="text", text="x", presets=[Preset(name="pop_in"), Preset(name="fade_out")])
    assert props_at(l, 0.0, 3.0, C)["scale"] == pytest.approx(0.0, abs=1e-6)
    assert props_at(l, 1.0, 3.0, C)["scale"] == pytest.approx(1.0)
    assert props_at(l, 2.99, 3.0, C)["opacity"] < 0.2


def test_typewriter_reveal():
    l = Layer(id="t", type="text", text="hello world", presets=[Preset(name="typewriter", duration=1.0)])
    assert props_at(l, 0.5, 3.0, C)["reveal"] == pytest.approx(0.5, abs=0.15)
    assert props_at(l, 2.0, 3.0, C)["reveal"] == 1.0


def test_all_presets_run():
    for name in PRESETS:
        l = Layer(id="t", type="text", text="abc", presets=[Preset(name=name)])
        for t in (0.0, 0.2, 1.0, 1.9):
            pr = props_at(l, t, 2.0, C)
            assert 0.0 <= pr["opacity"] <= 1.0

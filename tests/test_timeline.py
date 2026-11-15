import pytest

from vedit.ops import cut
from vedit.ops.captions import parse_srt
from vedit.project import Layer, VeditError, Word


def test_split_creates_two_clips(make_project):
    p = make_project()
    i = cut.split_at(p, 4.0)
    assert i == 1
    assert [(c.in_, c.out) for c in p.clips] == [(0.0, 4.0), (4.0, 10.0)]
    assert len({c.id for c in p.clips}) == 2


def test_split_on_boundary_is_noop(make_project):
    p = make_project(2)
    assert cut.split_at(p, 5.0) == 1
    assert len(p.clips) == 2


def test_delete_range_and_layers_follow(make_project):
    p = make_project()
    p.layers.append(Layer(id="t1", type="text", text="late", start=7.0, end=9.0))
    p.layers.append(Layer(id="t2", type="text", text="inside", start=3.2, end=3.8))
    r = cut.delete_range(p, 3.0, 5.0)
    assert r["removed_seconds"] == 2.0
    assert p.duration == pytest.approx(8.0)
    assert [(c.in_, c.out) for c in p.clips] == [(0.0, 3.0), (5.0, 10.0)]
    assert p.layer("t1").start == 5.0 and p.layer("t1").end == 7.0
    assert "t2" in r["dropped_layers"]


def test_keep_and_trim(make_project):
    p = make_project()
    cut.trim(p, head=1.0, tail=2.0)
    assert p.duration == pytest.approx(7.0)
    assert (p.clips[0].in_, p.clips[-1].out) == (1.0, 8.0)
    with pytest.raises(VeditError):
        cut.trim(p, head=5, tail=5)


def test_speed_range_changes_duration_and_remaps(make_project):
    p = make_project()
    p.layers.append(Layer(id="t1", type="text", text="x", start=8.0, end=9.0))
    cut.set_speed_range(p, 2.0, 6.0, 2.0)
    assert p.duration == pytest.approx(8.0)
    assert p.layer("t1").start == pytest.approx(6.0)


def test_captions_shift_after_delete(make_project):
    p = make_project()
    p.layers.append(Layer(id="cap1", type="captions", start=0.5, end=6.0,
                          words=[Word(text="a", start=0.5, end=1.0), Word(text="b", start=2.1, end=2.5), Word(text="c", start=5.0, end=5.5)]))
    cut.delete_range(p, 2.0, 3.0)
    words = p.layer("cap1").words
    assert [w.text for w in words] == ["a", "c"]
    assert words[1].start == pytest.approx(4.0)


def test_move_and_jumpcut_zoom(make_project):
    p = make_project(3)
    cut.move_clip(p, "c3", 0)
    assert [c.id for c in p.clips] == ["c3", "c1", "c2"]
    assert cut.jumpcut_zoom(p, 1.2) == ["c1"]


def test_parse_srt_spreads_words():
    words = parse_srt("1\n00:00:01,000 --> 00:00:03,000\nhello big world\n")
    assert [w.text for w in words] == ["hello", "big", "world"]
    assert words[0].start == 1.0 and words[-1].end == pytest.approx(3.0, abs=0.01)

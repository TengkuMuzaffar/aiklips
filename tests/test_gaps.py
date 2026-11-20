"""Main-track gaps: a clip dragged later stays there; the space before it plays the background colour."""

import json
import subprocess

import pytest

from vedit import media
from vedit.editor import actions
from vedit.ops import cut
from vedit.project import Clip, Project, Source, VeditError, load, normalize_gaps, save


@pytest.fixture
def proj(tmp_path, test_clip):
    p = Project()
    p.sources["s1"] = Source(path=str(test_clip), **media.probe(test_clip))
    p.clips += [Clip(id="c1", source="s1", in_=0, out=2), Clip(id="c2", source="s1", in_=2, out=5)]
    save(tmp_path, p)
    return tmp_path


def layout(p):
    return [("gap" if c.gap else c.id, round(s, 2), round(s + c.duration, 2)) for c, s in zip(p.clips, p.clip_starts())]


def test_clip_dropped_later_stays_there(proj):
    actions.apply(proj, "layer_add_shape", {"shape": "rect", "full": True, "start": 0, "end": 1})  # something stays at 0s
    actions.apply(proj, "clip_place", {"clip": "c1", "start": 4.0})
    p = load(proj)
    # c2 kept its time (2-5); c1 overlapped it in its second half, so it goes after c2
    assert layout(p) == [("gap", 0, 2), ("c2", 2, 5), ("c1", 5, 7)]
    actions.apply(proj, "clip_place", {"clip": "c1", "start": 8.0})
    assert layout(load(proj)) == [("gap", 0, 2), ("c2", 2, 5), ("gap", 5, 8), ("c1", 8, 10)]


def test_single_clip_moved_right_leaves_a_leading_gap(proj):
    actions.apply(proj, "remove_clip", {"clip": "c2"})
    with pytest.raises(VeditError, match="0s"):  # alone at 0s: it can't leave the start empty
        actions.apply(proj, "clip_place", {"clip": "c1", "start": 1.5})
    actions.apply(proj, "layer_add_shape", {"shape": "rect", "full": True, "start": 0, "end": 1})  # something stays at 0s
    actions.apply(proj, "clip_place", {"clip": "c1", "start": 1.5})
    p = load(proj)
    assert layout(p) == [("gap", 0, 1.5), ("c1", 1.5, 3.5)]
    gap = p.sources[p.clips[0].source]
    assert not gap.has_audio and ".gap-000000" in gap.path


def test_drop_on_first_half_of_a_clip_inserts_before_it(proj):
    actions.apply(proj, "clip_place", {"clip": "c2", "start": 0.3})
    assert layout(load(proj)) == [("c2", 0, 3), ("c1", 3, 5)]


def test_gaps_merge_and_trailing_gap_is_dropped(make_project):
    p = make_project(1)
    p.sources["g"] = Source(path="media/.gap-000000.mp4", duration=600, has_audio=False)
    p.clips = [Clip(id="g1", source="g", in_=0, out=1, gap=True), Clip(id="g2", source="g", in_=0, out=2, gap=True),
               p.clips[0], Clip(id="g3", source="g", in_=0, out=1, gap=True)]
    normalize_gaps(p)
    assert [(c.id, c.out) for c in p.clips] == [("g1", 3), ("c1", 10)]


def test_gap_renders_and_is_ignored_by_the_audit(proj):
    from vedit import audit
    from vedit.render import graph

    actions.apply(proj, "remove_clip", {"clip": "c2"})
    actions.apply(proj, "layer_add_shape", {"shape": "rect", "full": True, "start": 0, "end": 1})  # something stays at 0s
    actions.apply(proj, "clip_place", {"clip": "c1", "start": 1.0})
    p = load(proj)
    out = proj / "o.mp4"
    args, _ = graph.build(p, proj, out, scale=0.25)
    subprocess.run(args, check=True, capture_output=True)
    assert abs(media.probe(out)["duration"] - 3.0) < 0.15
    gap_id = p.clips[0].id
    findings = audit.audit(p, proj, render=False)["findings"]
    assert findings is not None
    assert not any(gap_id in json.dumps(f) or "almost black" in json.dumps(f) for f in findings)

"""Captions are text: one text layer per phrase in a captions group (ops/captions.py)."""

import pytest

from vedit.editor import actions
from vedit.ops import captions, groups
from vedit.project import SCHEMA_VERSION, Project, Word, migrate

WORDS = [Word(text=t, start=s, end=s + 0.3) for t, s in
         [("one", 0.5), ("two", 0.9), ("three.", 1.3), ("four", 1.7), ("five", 3.0), ("six", 3.4), ("seven", 3.8)]]


def test_add_captions_makes_text_phrases_in_one_group_on_one_track(make_project):
    p = make_project()
    g = captions.add_captions(p, WORDS, "bold", "72%", 3)
    ls = captions.phrases(p, g.id)
    # a phrase breaks after 3 words, at a sentence end and at a pause over 0.5 s
    assert [l.text for l in ls] == ["one two three.", "four", "five six seven"]
    assert all(l.type == "text" and l.highlight_words and l.group == g.id for l in ls)
    assert g.kind == "captions" and g.per_group == 3 and not g.collapsed
    assert len({l.track for l in ls}) == 1  # one "Captions" row
    assert ls[0].start == 0.5 and ls[0].end == ls[1].start  # a phrase stays until the next one
    assert [w.start for w in ls[2].words] == [0.0, 0.4, pytest.approx(0.8)]  # seconds from the phrase's start
    assert ls[0].presets[0].name == "pop_in" and ls[0].presets[0].params == {"from": 0.86}


def test_word_at_follows_the_phrase_when_it_moves(make_project):
    p = make_project()
    g = captions.add_captions(p, WORDS, "bold", "72%", 3)
    first = captions.phrases(p, g.id)[0]
    assert captions.word_at(first, 0.45) == 1  # "two" (0.9 s on the timeline)
    groups.move(p, g.id, delta=2.0)
    assert first.start == 2.5 and captions.word_at(first, 0.45) == 1  # moved together, still in step


def test_regroup_keeps_every_word_and_the_group(make_project):
    p = make_project()
    g = captions.add_captions(p, WORDS, "neon", "70%", 3)
    ls = captions.regroup(p, g.id, 2)
    assert [l.text for l in ls] == ["one two", "three.", "four", "five six", "seven"]
    assert [w.text for l in ls for w in l.words] == [w.text for w in WORDS]
    assert all(l.group == g.id and l.y == "70%" and l.highlight_words for l in ls)
    assert p.groups[0].per_group == 2 and len(p.groups) == 1
    assert ls[0].style.stroke_color == "#7C3AED"  # the neon style carried over


def test_retext_keeps_or_spreads_word_times(make_project):
    p = make_project()
    l = captions.phrases(p, captions.add_captions(p, WORDS, "bold", "72%", 3).id)[0]
    before = [(w.start, w.end) for w in l.words]
    captions.retext(l, "satu dua tiga")  # same count: the times stay
    assert [w.text for w in l.words] == ["satu", "dua", "tiga"] and [(w.start, w.end) for w in l.words] == before
    captions.retext(l, "hello world")  # another count: spread over the phrase
    assert [w.text for w in l.words] == ["hello", "world"] and l.text == "hello world"
    assert l.words[0].start == before[0][0] and l.words[-1].end == pytest.approx(before[-1][1], abs=0.01)


def test_style_group_restyles_every_phrase(make_project):
    p = make_project()
    g = captions.add_captions(p, WORDS, "bold", "72%", 3)
    captions.style_group(p, None, "karaoke")
    assert {l.style.color for l in captions.phrases(p, g.id)} == {"#9CA3AF"}
    with pytest.raises(Exception):
        captions.style_group(p, None, "nope")


def test_editor_actions_edit_regroup_and_restyle(make_project):
    p = make_project()
    g = captions.add_captions(p, WORDS, "bold", "72%", 3)
    first = captions.phrases(p, g.id)[0]
    actions._OPS["layer_set"](p, {"layer": first.id, "text": "ONE TWO THREE"})
    assert [w.text for w in first.words] == ["ONE", "TWO", "THREE"]
    actions._OPS["layer_set"](p, {"layer": first.id, "highlight_words": False})
    assert first.highlight_words is False
    actions._OPS["captions_words"](p, {"group": g.id, "n": 1})
    assert len(captions.phrases(p, g.id)) == 7
    actions._OPS["captions_style"](p, {"group": g.id, "style": "boxed"})
    assert all(l.style.bg_color for l in captions.phrases(p, g.id))


def test_is_caption_tells_phrases_from_titles(make_project):
    from vedit.project import Layer

    p = make_project()
    g = captions.add_captions(p, WORDS, "bold", "72%", 3)
    title = Layer(id="t1", type="text", text="Big title")
    p.layers.append(title)
    assert captions.is_caption(p, captions.phrases(p, g.id)[0]) and not captions.is_caption(p, title)


def test_old_captions_layer_migrates_to_text_phrases():
    old = {"version": 5, "sources": {}, "clips": [], "groups": [],
           "layers": [
               {"id": "t1", "type": "text", "text": "Title", "words": [], "words_per_group": 3, "track": 0},
               {"id": "cap1", "type": "captions", "start": 0.5, "end": 4.4, "y": "64%", "track": 1, "words_per_group": 3,
                "style": {"size": 70, "color": "#FFFFFF", "highlight_color": "#FFE600"},
                "words": [w.model_dump() for w in WORDS]},
           ]}
    p = Project.model_validate(migrate(old))
    assert p.version == SCHEMA_VERSION
    ls = [l for l in p.layers if l.id.startswith("cap1_")]
    assert [l.text for l in ls] == ["one two three.", "four", "five six seven"]
    assert all(l.type == "text" and l.highlight_words and l.y == "64%" and l.style.size == 70 for l in ls)
    assert ls[-1].end == 4.4  # the old layer's end
    assert [ls[2].start + w.start for w in ls[2].words] == [3.0, 3.4, 3.8]  # same spoken moments
    (g,) = p.groups
    assert g.kind == "captions" and g.per_group == 3 and all(l.group == g.id for l in ls)
    assert p.layer("t1").group is None  # titles untouched

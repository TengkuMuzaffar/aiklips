import json
from pathlib import Path

from vedit import media_library, sound
from vedit.project import Clip


def test_studied_speech_counts_when_the_whole_clip_score_is_just_under(monkeypatch, tmp_path):
    """A talking head whose pauses pull the whole-clip score under SPEECH is still speech when the media study
    heard a voice (or saw talking) in a shot inside the clip."""
    d = tmp_path / "u1"
    d.mkdir()
    (d / "index.json").write_text(json.dumps([{"k": 0, "from": 0, "to": 3}, {"k": 1, "from": 3, "to": 6}]))
    (d / "measure.json").write_text(json.dumps({"0": {"voice": 0.1}, "1": {"voice": 0.6}}))
    (d / "notes.json").write_text(json.dumps({"0": {"talking": False}}))
    fake = type("F", (), {"_dir": staticmethod(lambda uid: d),
                          "segments": staticmethod(lambda uid: json.loads((d / "index.json").read_text()))})
    monkeypatch.setattr(media_library, "id_for_path", lambda p: "u1")
    monkeypatch.setattr(media_library, "_footage", lambda: fake)
    assert sound.studied_speech(Path("x.mp4"), 2.0, 5.0)  # overlaps the voiced shot 1
    assert not sound.studied_speech(Path("x.mp4"), 0.0, 2.9)  # only the quiet shot 0
    (d / "notes.json").write_text(json.dumps({"0": {"talking": True}}))
    assert sound.studied_speech(Path("x.mp4"), 0.0, 2.9)  # the AI saw her talking there
    c = Clip(id="c1", source="s1", in_=0, out=3)
    assert sound.is_speech(c, {"voice": 0.36, "studied_speech": True})
    assert not sound.is_speech(c, {"voice": 0.36})
    monkeypatch.setattr(media_library, "id_for_path", lambda p: None)
    assert not sound.studied_speech(Path("x.mp4"), 0.0, 6.0)  # not in the library: the measurement decides

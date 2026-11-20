"""Text contrast guardrail: light text on a white card must be caught (ops/contrast.py, audit, CLI warnings)."""

import numpy as np
import pytest

from vedit import audit as audit_mod
from vedit.ops import contrast
from vedit.project import Layer, Project, TextStyle

FLAT = dict(stroke_width=0, shadow=False)


def card_project() -> Project:
    """The bug report: a white card on a dark canvas, near-white heading + lime accent on it, dark pills with white
    text that are fine."""
    p = Project()
    p.canvas.background = "#111111"
    p.layers = [
        Layer(id="card", type="shape", shape="rect", width=900, height=1500, radius=40, fill="#FFFFFF", track=0, end=5),
        Layer(id="pill", type="shape", shape="rect", width=760, height=110, y="55%", fill="#1A1D1A", radius=16, track=1, end=5),
        Layer(id="head", type="text", text="You paid for AI.", y="25%", track=2, end=5,
              style=TextStyle(color="#E8ECE8", size=90, **FLAT)),
        Layer(id="accent", type="text", text="again.", y="35%", track=3, end=5,
              style=TextStyle(color="#D4F27A", size=120, **FLAT)),
        Layer(id="row", type="text", text="AI chat plan", y="55%", track=4, end=5,
              style=TextStyle(color="#FFFFFF", size=44, **FLAT)),
        Layer(id="free", type="text", text="On the canvas", y="95%", track=5, end=5,
              style=TextStyle(color="#FFFFFF", size=60, **FLAT)),
    ]
    return p


def test_ratio_matches_wcag():
    assert float(contrast.ratio(np.array([1, 1, 1.0]), np.array([0, 0, 0.0]))) == pytest.approx(21.0)
    assert float(contrast.ratio(np.array([1, 1, 1.0]), np.array([1, 1, 1.0]))) == pytest.approx(1.0)
    # #767676 on white is the classic 4.54:1
    assert float(contrast.ratio(np.array(contrast.rgba("#767676")[:3]), np.array([1, 1, 1.0]))) == pytest.approx(4.54, abs=0.01)


def test_light_text_on_white_card_is_caught():
    p = card_project()
    res = {r.layer: r for r in contrast.issues(p, None)}
    assert res["head"].severity == "must-fix" and res["head"].ratio < 1.3
    assert "shape card" in res["head"].behind
    assert res["accent"].severity == "must-fix"  # lime on white
    assert "--color #111111" in res["head"].suggest  # dark text fixes a white card
    assert res["row"].severity is None  # white on the dark pill: the pill is what's behind it, not the card
    assert res["free"].severity is None and "canvas" in res["free"].behind  # off the card: on the dark canvas


def test_outline_and_own_box_rescue():
    p = card_project()
    head = p.layer("head")
    head.style.stroke_width, head.style.stroke_color = 8, "#000000"
    assert {r.layer: r for r in contrast.issues(p, None)}["head"].severity is None  # a strong dark outline reads
    head.style.stroke_width = 0
    head.style.bg_color = "#000000E0"
    assert {r.layer: r for r in contrast.issues(p, None)}["head"].severity is None  # its own dark box


def test_translucent_shape_composites_and_footage_is_unknown(make_project):
    p = make_project()  # a real footage clip under everything
    p.layers = [Layer(id="t", type="text", text="Hello", style=TextStyle(color="#FFFFFF", **FLAT), end=5)]
    assert contrast.issues(p, None) == []  # footage: only pixels can tell, nothing claimed
    p.layers.insert(0, Layer(id="dim", type="shape", shape="rect", width=1080, height=1920, fill="#000000", opacity=0.5, end=5))
    assert contrast.issues(p, None) == []  # half-black over footage: still unknown underneath
    p.layers[0].opacity = 1.0
    assert contrast.issues(p, None)[0].severity is None  # fully black: known, fine


def test_audit_reports_contrast(tmp_path):
    rep = audit_mod.audit(card_project(), tmp_path, render=False)
    found = [f for f in rep["findings"] if f["category"] == "contrast"]
    assert {f["message"].split()[0] for f in found} == {"head", "accent"}
    assert all(f["severity"] == "must-fix" for f in found) and not rep["passed"]
    assert "1.1" in found[0]["evidence"] or "1.2" in found[0]["evidence"] or "1.3" in found[0]["evidence"]


def test_pixels_catch_light_text_over_footage(cli, test_clip, tmp_path):
    """Over footage the structure can't tell: the rendered pixels around the letters decide."""
    from PIL import Image

    from vedit.project import load

    white = tmp_path / "white.png"
    Image.new("RGB", (400, 400), "#FFFFFF").save(white)
    cli("import", str(test_clip))
    cli("image", "add", str(white), "--width", "1000", "--y", "30%")
    cli("text", "add", "Invisible", "--y", "30%", "--color", "#F2F2F2", "--stroke-width", "0", "--size", "90")
    p = load(cli.project)
    rep = audit_mod.audit(p, cli.project, render=True)
    found = [f for f in rep["findings"] if f["category"] == "contrast"]
    assert found and found[0]["severity"] == "must-fix", rep["findings"]
    assert "on screen" in found[0]["message"]


def test_cli_warns_right_after_the_edit(cli):
    cli("shape", "add", "rect", "--width", "900", "--height", "1500", "--fill", "#FFFFFF", "--end", "5")
    r = cli("text", "add", "Hello there", "--color", "#EEEEEE", "--stroke-width", "0", "--end", "5")
    assert r["warnings"] and "hard to read" in r["warnings"][0] and "--color #111111" in r["warnings"][0]
    lid = r["layer"]
    assert "warnings" not in cli("layer", "set", lid, "--x", "52%")  # an unrelated edit doesn't repeat it
    assert "warnings" not in cli("text", "edit", lid, "--color", "#111111")  # fixed


def test_outline_counts_per_pixel_on_busy_backgrounds():
    """White text with a black outline over footage that is partly white, partly blue: wherever the fill fails the
    outline carries it, so it reads. Without the outline the white patches make it unreadable."""
    p = card_project()
    l = p.layer("free")
    mixed = np.array([[1.0, 1.0, 1.0]] * 30 + [[0.0, 0.0, 1.0]] * 70)
    bh = contrast.Behind(mixed, "footage", pixels=True)
    l.style.stroke_width, l.style.stroke_color = 6, "#000000"
    assert contrast.evaluate(p, l, bh, 1.0).severity is None
    l.style.stroke_width = 0
    assert contrast.evaluate(p, l, bh, 1.0).severity == "must-fix"

"""Colour grading (measure, match, looks) and the font library / text looks."""

import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from vedit.ops import color, fonts, text_looks
from vedit.ops.text import find_font, render_text
from vedit.project import Grade, Layer, TextStyle, VeditError

from .conftest import needs_ffmpeg


# ---- fonts ----------------------------------------------------------------------------------

def test_every_bundled_font_exists_and_loads():
    for key, (file, *_rest) in fonts.FONTS.items():
        path = fonts.FONT_DIR / file
        assert path.exists(), key
        assert find_font(key) == str(path)
        assert render_text("Hello 50%", TextStyle(font=key, size=40)).getbbox(), key
    assert (fonts.FONT_DIR / "licenses").is_dir()


def test_family_names_and_feels_resolve():
    assert find_font("Bebas Neue").endswith("BebasNeue-Regular.ttf")
    assert fonts.feel_of("bold condensed sans") == "condensed"
    assert fonts.feel_of("elegant serif") == "serif"
    assert fonts.feel_of("something nobody wrote") == "bold_sans"
    assert fonts.for_feel("handwritten") in fonts.BY_FEEL["handwritten"]
    assert all(fonts.BY_FEEL[f] for f in fonts.FEELS)  # every feel has at least one font
    with pytest.raises(VeditError, match="Bundled fonts"):
        find_font("No Such Font Anywhere")


def test_letter_spacing_glow_and_gradient_render():
    base = TextStyle(font="anton", size=60, stroke_width=0, shadow=False)
    plain = render_text("SPACING", base)
    wide = render_text("SPACING", base.model_copy(update={"letter_spacing": 12}))
    assert wide.width == pytest.approx(plain.width + 12 * 6, abs=3)
    glow = render_text("SPACING", base.model_copy(update={"glow_color": "#FF00FF"}))
    assert glow.width > plain.width
    px = np.array(glow)
    assert (px[..., 0].astype(int) - px[..., 1] > 100).any()  # magenta glow pixels exist
    grad = np.array(render_text("I", base.model_copy(update={"gradient": ["#FF0000", "#0000FF"], "size": 200})))
    solid = grad[grad[..., 3] == 255]
    assert solid[:, 0].max() > 200 and solid[:, 2].max() > 200  # both ends of the gradient are present


def test_typewriter_reveal_still_works_with_spacing():
    st = TextStyle(font="poppins", size=50, letter_spacing=4, shadow=False, stroke_width=0)
    half = render_text("ABCDEFGH", st, reveal=0.5)
    full = render_text("ABCDEFGH", st)
    assert half.size == full.size
    assert np.array(half)[..., 3].sum() < np.array(full)[..., 3].sum() * 0.7


# ---- text looks -----------------------------------------------------------------------------

def test_builtin_looks_are_valid():
    for name in text_looks.BUILTIN:
        layer = Layer(id="t1", type="text", text="Look test")
        text_looks.apply(layer, name)
        assert layer.style.look == name
        assert layer.style.font in fonts.FONTS
        assert layer.presets, name
        render_text("Look test", layer.style)
    assert set(text_looks.FOR_FEEL) == set(fonts.FEELS)


def test_save_apply_and_delete_a_custom_look():
    src = Layer(id="t1", type="text", text="Brand", rotation=3, style=TextStyle(font="bungee", color="#123456", letter_spacing=5))
    src.presets = [text_looks._preset("pop_in")]
    text_looks.save("my-brand", src, about="our titles")
    assert any(r["name"] == "my-brand" and r["saved"] for r in text_looks.catalog())
    dst = Layer(id="t2", type="text", text="Other")
    text_looks.apply(dst, "my-brand")
    assert (dst.style.font, dst.style.color, dst.style.letter_spacing, dst.rotation) == ("bungee", "#123456", 5, 3)
    assert [p.name for p in dst.presets] == ["pop_in"]
    with pytest.raises(VeditError):
        text_looks.save("viral-hook", src)  # built-in names are reserved
    text_looks.delete("my-brand")
    with pytest.raises(VeditError):
        text_looks.get("my-brand")


def test_captions_keep_their_size_when_restyled():
    from vedit.project import Word

    cap = Layer(id="cap1", type="text", text="hi", style=TextStyle(size=70), words=[Word(text="hi", start=0, end=0.4)])
    text_looks.apply(cap, "neon")
    assert cap.style.size == 70 and cap.style.glow_color and not cap.presets


# ---- colour maths ---------------------------------------------------------------------------

def _st(luma=0.5, spread=0.6, sat=0.3, warmth=0.0, tint=0.0):
    return {"luma": luma, "p05": luma - spread / 2, "p95": luma + spread / 2, "sat": sat, "warmth": warmth, "tint": tint}


def test_match_moves_toward_target_and_is_neutral_on_itself():
    same = color.match(_st(), _st())
    assert same == {"brightness": 0, "contrast": 1, "saturation": 1, "temperature": 0, "tint": 0}
    m = color.match(_st(luma=0.3, sat=0.15, warmth=-0.05), _st(luma=0.5, sat=0.3, warmth=0.05), strength=1)
    assert m["brightness"] > 0.1 and m["saturation"] > 1.5 and m["temperature"] > 0.5
    extreme = color.match(_st(luma=0.02, spread=0.01, sat=0.001), _st(luma=0.98, spread=0.9, sat=0.9), strength=1)
    assert abs(extreme["brightness"]) <= 0.2 and extreme["contrast"] <= 1.35 and extreme["saturation"] == 1.0  # clamped


def test_filter_chain_from_grade():
    assert color.filter_chain(None) is None
    assert color.filter_chain(Grade()) is None
    chain = color.filter_chain(Grade(brightness=0.05, temperature=0.5, look="teal-orange"))
    assert chain.startswith("eq=") and "colorchannelmixer" in chain and "colorbalance" in chain
    film = color.filter_chain(Grade(look="warm-film"))
    assert "curves=" in film and "vignette=" in film
    half = color.resolved(Grade(look="vibrant", intensity=0.5))
    assert half["saturation"] == pytest.approx(1.16)
    with pytest.raises(VeditError, match="LUT"):
        color.filter_chain(Grade(lut="/no/such.cube"))
    with pytest.raises(VeditError, match="Unknown colour look"):
        color.look_name("instagram-filter-9")


def test_frame_stats_reads_warm_and_cool():
    warm = color.frame_stats(np.full((4, 4, 3), [0.8, 0.5, 0.3]))
    cool = color.frame_stats(np.full((4, 4, 3), [0.3, 0.5, 0.8]))
    assert warm["warmth"] > 0.4 and cool["warmth"] < -0.4
    assert warm["sat"] == pytest.approx(0.5)


# ---- end to end -----------------------------------------------------------------------------

def _clip(path: Path, vf: str) -> Path:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2",
                    "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


@needs_ffmpeg
def test_color_match_makes_clips_consistent_and_renders(cli, tmp_path):
    dark_cool = _clip(tmp_path / "dark.mp4", "eq=brightness=-0.25:saturation=0.5,colorchannelmixer=rr=0.8:bb=1.2")
    bright_warm = _clip(tmp_path / "bright.mp4", "eq=brightness=0.15,colorchannelmixer=rr=1.2:bb=0.8")
    cli("import", str(dark_cool), str(bright_warm))
    before = cli("color", "stats")
    r = cli("color", "match")
    assert r["matched_to"] == "clips" and set(r["clips"]) == {"c1", "c2"}
    assert r["clips"]["c1"]["brightness"] > 0 > r["clips"]["c2"]["brightness"]
    assert r["clips"]["c1"]["temperature"] > 0 > r["clips"]["c2"]["temperature"]
    assert r["spread_after"] < r["spread_before"] * 0.6

    # the rendered frames really are closer than the source frames
    def grab(at):
        img = Path(cli("frame", "--at", str(at))["image"])
        return color.frame_stats(np.asarray(Image.open(img).convert("RGB"), dtype=np.float32) / 255.0)

    a, b = grab(1.0), grab(3.0)
    src = before["per_clip"]
    assert abs(a["luma"] - b["luma"]) < abs(src["c1"]["luma"] - src["c2"]["luma"]) * 0.6
    assert abs(a["warmth"] - b["warmth"]) < abs(src["c1"]["warmth"] - src["c2"]["warmth"]) * 0.6

    hero = cli("color", "match", "--to", "c2")
    assert hero["clips"]["c2"]["brightness"] == 0 and hero["matched_to"] == "c2"
    cli("color", "look", "warm-film", "--intensity", "0.6")
    cli("frame", "--at", "1.0")  # renders with eq + mixer + balance + curves + vignette
    stats = cli("color", "stats")
    assert "look warm-film @0.6" in stats["grades"]["c1"]
    cli("color", "look", "nope", ok=False)
    cli("undo")
    cli("color", "clear")
    assert all(g == "none" for g in cli("color", "stats")["grades"].values())


@needs_ffmpeg
def test_text_look_cli_and_fonts(cli, test_clip):
    cli("import", str(test_clip))
    t1 = cli("text", "add", "HOOK", "--look", "viral-hook", "--size", "90")["layer"]
    t2 = cli("text", "add", "second", "--font", "dm-serif", "--letter-spacing", "4", "--gradient", "#FFE600,#FF6A00")["layer"]
    from vedit.project import load
    p = load(cli.project)
    l1, l2 = p.layer(t1), p.layer(t2)
    assert (l1.style.font, l1.style.size, l1.style.look) == ("anton", 90, "viral-hook")
    assert [x.name for x in l1.presets] == ["pop_in", "fade_out"]
    assert l2.style.gradient == ["#FFE600", "#FF6A00"] and l2.style.letter_spacing == 4
    cli("text", "look", "apply", "editorial")  # every text layer, for consistency
    p = load(cli.project)
    assert {l.style.font for l in p.layers} == {"dm-serif"}
    cli("text", "look", "save", "house", "--from", t1)
    assert any(r["name"] == "house" for r in cli("text", "look", "list")["looks"])
    cli("text", "edit", t2, "--gradient", "none", "--glow", "#00FFFF")
    assert load(cli.project).layer(t2).style.gradient is None
    cli("text", "edit", t2, "--font", "not-a-font", ok=False)
    out = cli("fonts", "--feel", "serif", "--sheet")
    assert {r["feel"] for r in out["fonts"]} == {"serif"} and Path(out["sheet"]).exists()
    cli("frame", "--at", "0.5")


# ---- generation -----------------------------------------------------------------------------

def test_planner_keeps_one_font_and_passes_the_grade():
    from vedit.generate import planner
    from vedit.generate.spec import Creative, EditPlan, Op
    from vedit.references.dna import StyleDNA

    dna = StyleDNA()
    dna.text.font_feel = "elegant serif"
    dna.color.look, dna.color.measured = "moody", {"luma": 0.3, "p05": 0.05, "p95": 0.7, "sat": 0.1, "warmth": -0.02, "tint": 0.0}
    old_axes = {"hook": "reference", "order": "story", "pacing": "reference", "caption_style": "reference",
                "text_animation": "reference", "transitions": "reference", "music_section": "start"}  # saved before the new axes
    from vedit.generate.spec import Brief
    d = planner.directives(dna, Brief(), old_axes, {}, False)
    assert d["text"]["font"] in fonts.BY_FEEL["serif"]
    assert d["grade"]["look"] == "moody" and d["grade"]["target"]["luma"] == 0.3
    plan = EditPlan(ops=[Op(op="text", text="a", font="bangers"), Op(op="text", text="b", font=d["text"]["accent_font"]),
                         Op(op="text", text="c", font=d["text"]["accent_font"])])
    out = planner.enforce(plan, d, Creative(index=1, angle="x", hook_text="a"))
    used = [o.font for o in out.ops if o.op == "text"]
    # the model may pick one second font for emphasis (its first choice), never a third
    assert used == ["bangers", d["text"]["font"], d["text"]["font"]]
    plan = EditPlan(ops=[Op(op="text", text="a"), Op(op="text", text="b", font=d["text"]["accent_font"]),
                         Op(op="text", text="c", font=d["text"]["accent_font"])])
    used = [o.font for o in planner.enforce(plan, d, Creative(index=1, angle="x", hook_text="a")).ops if o.op == "text"]
    assert used == [d["text"]["font"], d["text"]["accent_font"], d["text"]["accent_font"]]
    assert out.grade["look"] == "moody"
    d2 = planner.directives(dna, Brief(), {**old_axes, "font": "rounded", "color_look": "vibrant"}, {}, False)
    assert d2["text"]["font"] in fonts.BY_FEEL["rounded"] and d2["grade"]["look"] == "vibrant"


def test_dna_takes_measured_colour():
    from vedit.references.dna import StyleDNA, apply_measured, card, merge

    m = {"pacing": {}, "color": {"luma": 0.6, "p05": 0.1, "p95": 0.9, "sat": 0.3, "warmth": 0.08, "tint": 0.0}}
    a = apply_measured(StyleDNA(), m)
    assert a.color.measured["warmth"] == 0.08
    b = StyleDNA()
    b.color.measured = {**m["color"], "luma": 0.4}
    assert merge([a, b]).color.measured["luma"] == pytest.approx(0.5)
    row = next(r for r in card(a) if r["label"] == "Colour")
    assert "warm" in row["value"]


# ---- desktop editor inspector ---------------------------------------------------------------

@needs_ffmpeg
def test_editor_font_dropdown_api_and_look_ops(cli, test_clip):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    cli("import", str(test_clip))
    client = TestClient(create_app(cli.project, watch=False, chat=False))
    t = client.get("/api/typography").json()
    assert {f["key"] for f in t["fonts"]} == set(fonts.FONTS) and t["default"] in fonts.FONTS
    assert set(t["feels"]) == set(fonts.FEELS) and any(k["name"] == "neon" for k in t["looks"])
    r = client.get("/api/fonts/anton")
    assert r.status_code == 200 and r.content[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO")
    assert client.get("/api/fonts/..%2F..%2Fpyproject.toml").status_code == 404
    assert client.get("/api/fonts/nope").status_code == 404

    def op(op_name, ok=True, **args):
        r = client.post("/api/op", json={"op": op_name, "args": args})
        assert (r.status_code == 200) is ok, r.text
        return client.get("/api/state").json()["project"]["layers"]

    op("layer_add_text", text="One")
    op("layer_add_text", text="Two")
    layers = op("text_look", layer="t1", name="neon")
    assert layers[0]["style"]["font"] == "righteous" and layers[0]["style"]["look"] == "neon"
    layers = op("text_look", name="editorial", all=True)
    assert {l["style"]["font"] for l in layers} == {"dm-serif"}
    layers = op("layer_set", layer="t2", style={"font": "bangers", "letter_spacing": "4", "gradient": "#FFE259,#FF4E50", "glow_color": ""})
    s = layers[1]["style"]
    assert (s["font"], s["letter_spacing"], s["gradient"], s["look"]) == ("bangers", 4.0, ["#FFE259", "#FF4E50"], None)
    layers = op("layer_set", layer="t2", style={"font": "", "gradient": ""})
    assert layers[1]["style"]["font"] is None and layers[1]["style"]["gradient"] is None
    op("layer_set", ok=False, layer="t2", style={"font": "Definitely Not A Font"})
    op("text_look", ok=False, layer="t2", name="nope")


@needs_ffmpeg
def test_editor_clip_colour_ops(cli, tmp_path):
    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    cli("import", str(_clip(tmp_path / "a.mp4", "eq=brightness=-0.2")), str(_clip(tmp_path / "b.mp4", "eq=brightness=0.15")))
    client = TestClient(create_app(cli.project, watch=False, chat=False))
    assert any(l["name"] == "warm-film" for l in client.get("/api/typography").json()["color_looks"])

    def op(op_name, ok=True, **args):
        r = client.post("/api/op", json={"op": op_name, "args": args})
        assert (r.status_code == 200) is ok, r.text
        return {c["id"]: c.get("grade") for c in client.get("/api/state").json()["project"]["clips"]}

    g = op("color_match")
    assert g["c1"]["brightness"] > 0 > g["c2"]["brightness"] and g["c1"]["matched_to"] == "clips"
    g = op("color_match", hero="c2")
    assert g["c2"]["brightness"] == 0 and g["c1"]["matched_to"] == "c2"
    g = op("color_look", clip="c1", name="moody", intensity="0.5")
    assert (g["c1"]["look"], g["c1"]["intensity"]) == ("moody", 0.5) and g["c2"]["look"] is None
    g = op("color_look", all=True, name="moody", intensity=0.5)
    assert g["c2"]["look"] == "moody" and g["c1"]["matched_to"] == "c2"  # look keeps the match
    g = op("color_set", clip="c2", saturation="1.3", temperature="0.2")
    assert (g["c2"]["saturation"], g["c2"]["temperature"]) == (1.3, 0.2)
    op("color_look", ok=False, clip="c1", name="sepia-ultra")
    op("color_set", ok=False, clip="c1")
    g = op("color_clear", clip="c1")
    assert g["c1"] is None and g["c2"] is not None
    assert all(v is None for v in op("color_clear", all=True).values())
    assert client.post("/api/undo").status_code == 200
    assert load_grade(client, "c2")["look"] == "moody"


def load_grade(client, cid):
    return next(c["grade"] for c in client.get("/api/state").json()["project"]["clips"] if c["id"] == cid)


def test_linux_fontconfig_fallback_is_not_a_match(monkeypatch, tmp_path):
    """fc-match always answers (falls back to DejaVu…): an unknown name must still be an error, a real match not."""
    import subprocess
    import types

    from vedit.ops import text as text_mod

    real = tmp_path / "Montserrat-Bold.ttf"
    real.write_bytes(b"x")
    fallback = tmp_path / "DejaVuSans.ttf"
    fallback.write_bytes(b"x")
    monkeypatch.setattr(text_mod.sys, "platform", "linux")
    monkeypatch.setattr(text_mod, "_font_dirs", lambda: [])

    def fake_run(cmd, **kw):
        fam, path = ("Montserrat", real) if "Montserrat" in cmd[-1] else ("DejaVu Sans", fallback)
        return types.SimpleNamespace(stdout=f"{fam}\n{path}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert find_font("Montserrat Bold") == str(real)
    with pytest.raises(VeditError, match="Bundled fonts"):
        find_font("No Such Font Anywhere")

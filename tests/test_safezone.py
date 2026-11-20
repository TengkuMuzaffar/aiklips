"""Platform safe zones: measured layer boxes, check / fix, the audit, the CLI, and the editor's guides table."""

import re
from pathlib import Path

import pytest

from vedit import audit as audit_mod
from vedit.ops import safezone
from vedit.project import Layer, TextStyle, VeditError

INDEX = Path(__file__).parents[1] / "src/vedit/preview_server/static/index.html"


def test_zone_per_platform(make_project):
    p = make_project()  # 1080x1920
    z = safezone.zone(p, "tiktok")
    assert (round(z.x0), round(z.y0), round(z.x1), round(z.y1)) == (59, 159, 940, 1440)
    assert safezone.zone(p).key == "all"  # portrait with no platform: the strictest
    safezone.set_platform(p, "Instagram")
    assert p.platform == "reels" and safezone.zone(p).name == "Instagram Reels"
    with pytest.raises(VeditError):
        safezone.set_platform(p, "myspace")
    p.canvas.width, p.canvas.height = 1920, 1080
    safezone.set_platform(p, None)
    assert safezone.zone(p).key == "title"  # landscape: plain 5% margins


def test_editor_guides_match_the_python_table():
    js = INDEX.read_text(encoding="utf-8")  # Windows defaults to cp1252, which can't read the page's arrows
    for key, z in safezone.PLATFORMS.items():
        jkey = "audit" if key == "all" else key
        m = re.search(jkey + r": \{ name: \"[^\"]+\", top: ([\d.]+), bottom: ([\d.]+), left: ([\d.]+), right: ([\d.]+)", js)
        assert m, key
        assert tuple(float(v) for v in m.groups()) == (z["top"], z["bottom"], z["left"], z["right"]), key


def test_boxes_check_and_fit(make_project):
    p = make_project()
    p.platform = "tiktok"
    p.layers += [
        Layer(id="btn", type="shape", shape="rect", width=300, height=120, x=1000, y="50%", start=0, end=2),  # under the buttons
        Layer(id="big", type="text", text="A VERY LONG HEADLINE THAT GOES ON", style=TextStyle(size=140, max_width=2000),
              x="50%", y="30%", start=0, end=2),
        Layer(id="bg", type="shape", shape="rect", width=1080, height=1920, radius=0, start=0, end=2),  # a colour screen
        Layer(id="ok", type="shape", shape="ellipse", width=200, height=200, x="40%", y="40%", start=0, end=2),
    ]
    b = safezone.layer_box(p, None, p.layer("btn"))
    assert [round(v) for v in b] == [850, 900, 1150, 1020]
    r = safezone.check(p, None)
    bad = {x["layer"] for x in r["layers"] if not x["ok"]}
    assert bad == {"btn", "big"} and "bg" not in {x["layer"] for x in r["layers"]}  # backgrounds are exempt
    assert "like / comment / share" in next(x for x in r["layers"] if x["layer"] == "btn")["problems"][0]
    assert safezone.fit(p, None, p.layer("btn")) == {"x": 778.0, "y": 960.0}  # moved left, same size
    assert safezone.fit(p, None, p.layer("big"))["scale"] < 1  # too wide for the zone: shrunk
    assert safezone.check(p, None)["ok"] and safezone.fit(p, None, p.layer("ok")) is None


def test_fit_moves_an_animated_path_along(make_project):
    from vedit.project import Keyframe

    p = make_project()
    l = Layer(id="s", type="shape", shape="rect", width=200, height=100, x=1000, y=960, start=0, end=2,
              keyframes=[Keyframe(t=0, x=1000), Keyframe(t=1, x=900)])
    p.layers.append(l)
    safezone.fit(p, None, l)
    assert l.x == 838 and [k.x for k in l.keyframes] == [838, 738]


def test_audit_uses_the_platform_and_every_layer_type(make_project):
    p = make_project()
    p.platform = "reels"
    p.layers.append(Layer(id="sticker", type="shape", shape="star", width=300, height=300, x="50%", y=1800, start=0, end=2))
    f = [x for x in audit_mod.check_layout(p, None) if x["category"] == "safe-zone"]
    assert len(f) == 1 and "Instagram Reels" in f[0]["message"] and f[0]["fix"] == "vedit safe fix sticker"


def test_cli_platform_safe_and_guides(cli, test_clip):
    from vedit.project import load

    cli("import", str(test_clip))
    assert cli("platform", "tiktok")["platform"] == "tiktok"
    assert "TikTok safe area" in cli("platform")["describe"]
    t = cli("text", "add", "FOLLOW", "--x", "96%", "--y", "50%", "--end", "2")["layer"]
    assert not cli("safe", "check")["ok"]
    assert t in cli("safe", "fix")["changed"]
    assert cli("safe", "check")["ok"]
    img = cli("frame", "--at", "1", "--guides")["image"]
    from PIL import Image

    im = Image.open(img).convert("RGB")
    r, g, b = im.getpixel((im.width // 2, 2))  # the top bar is tinted red
    assert r > g + 20 and r > b + 10
    assert load(cli.project).platform == "tiktok"

"""Overlays: video layers (picture-in-picture, green screen, sound), shapes, blend modes, stacking order, frame
styling and the built-in library. Pixels are checked on real FFmpeg renders."""

import re
import subprocess

import numpy as np
import pytest
from PIL import Image

from vedit import media
from vedit.ops import overlays
from vedit.ops.overlay_style import chroma_alpha, render_shape, style_frame
from vedit.project import Layer, VeditError, load, validate
from vedit.render.runner import PREVIEW_SCALE, base_hash, render

from .conftest import needs_ffmpeg


def _px(img: Image.Image, fx: float, fy: float) -> tuple[int, int, int]:
    return img.getpixel((int(fx * (img.width - 1)), int(fy * (img.height - 1))))[:3]


# ---- pure ------------------------------------------------------------------------------------------------------

def test_shapes_and_frame_styling():
    rect = render_shape(Layer(id="a", type="shape", shape="rect", width=200, height=100, radius=30, fill="#FF0000"))
    assert rect.size == (200, 100)
    assert rect.getpixel((100, 50))[3] == 255 and rect.getpixel((1, 1))[3] < 40  # rounded corner is cut away
    ring = render_shape(Layer(id="b", type="shape", shape="ellipse", width=200, height=200, fill=None, border_width=10, border_color="#00FF00"))
    assert ring.getpixel((100, 100))[3] == 0 and ring.getpixel((100, 4))[1] > 200  # hollow, green edge
    arrow = render_shape(Layer(id="c", type="shape", shape="arrow", width=300, height=90, fill="#FFE600"))
    assert arrow.getpixel((290, 45))[3] > 200 and arrow.getpixel((10, 5))[3] == 0

    pic = Image.new("RGB", (300, 200), (255, 0, 0))
    circ = style_frame(pic, Layer(id="d", type="video", mask="circle", border_width=8, border_color="#FFFFFF"), 1.0)
    assert circ.size == (200, 200)  # squared for the circle
    assert circ.getpixel((2, 2))[3] == 0 and circ.getpixel((100, 100))[:3] == (255, 0, 0)
    assert circ.getpixel((100, 3))[:3] == (255, 255, 255)  # border inside the edge

    green = np.zeros((10, 20, 3), np.uint8)
    green[:, :10] = (0, 255, 0)
    green[:, 10:] = (200, 50, 50)
    a = chroma_alpha(green, "#00FF00", 0.25)
    assert a[:, :10].max() == 0 and a[:, 10:].min() == 255


def test_set_fields_and_order(make_project):
    p = make_project(1)
    p.layers += [Layer(id="a", type="text", text="A"), Layer(id="b", type="text", text="B"), Layer(id="c", type="text", text="C")]
    assert overlays.move_layer(p, "a", "front") == 2 and [l.id for l in p.layers] == ["b", "c", "a"]
    overlays.move_layer(p, "a", "down")
    assert [l.id for l in p.layers] == ["b", "a", "c"]
    overlays.move_layer(p, "c", 0)
    assert [l.id for l in p.layers] == ["c", "b", "a"]
    with pytest.raises(VeditError):
        overlays.move_layer(p, "a", "sideways")
    L = p.layers[0]
    assert set(overlays.set_fields(L, {"blend": "screen", "radius": "12", "chroma": "none", "loop": "true"})) == {"blend", "radius", "chroma", "loop"}
    assert L.blend == "screen" and L.radius == 12 and L.chroma is None and L.loop is True
    with pytest.raises(VeditError):
        overlays.set_fields(L, {"blend": "dodge"})
    with pytest.raises(VeditError):
        overlays.set_fields(L, {"border_color": "reddish"})


def test_video_layer_needs_a_real_source(make_project):
    p = make_project(1)
    p.layers.append(Layer(id="v1", type="video", source="nope"))
    with pytest.raises(VeditError):
        validate(p)
    with pytest.raises(VeditError):
        overlays.add_video(p, "nope")


def test_base_preview_ignores_overlay_only_sources(make_project):
    from vedit.project import Source

    p = make_project(1)
    h = base_hash(p)
    p.sources["s9"] = Source(path="leak.mp4", duration=6)
    p.layers.append(Layer(id="v1", type="video", source="s9", blend="screen"))
    assert base_hash(p) == h


# ---- rendered ----------------------------------------------------------------------------------------------------

@pytest.fixture
def two_sources(cli, test_clip, tmp_path):
    """Project: the test clip on the timeline (canvas reels) + a solid red 3s clip imported as a second source."""
    red = tmp_path / "red.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=640x360:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=f=660:d=3", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(red)],
                   check=True)
    cli("import", str(test_clip))
    cli("canvas", "--preset", "reels")
    r = cli("overlay", "add", str(red), "--start", "1", "--x", "70%", "--y", "70%", "--width", "400", "--circle",
            "--border", "12", "--border-color", "#00FF00")
    return cli.project, r["layer"]


def _frames(d, t_list, layers=True):
    p = load(d)
    out = d / "o.mp4"
    render(p, d, out, PREVIEW_SCALE, "preview", progress=False, layers=layers)
    imgs = []
    for t in t_list:
        f = d / f"f{t}.png"
        media.extract_frame(out, t, f)
        imgs.append(Image.open(f).convert("RGB"))
    return out, imgs


@needs_ffmpeg
def test_picture_in_picture_renders_in_place_and_time(two_sources):
    d, lid = two_sources
    _, (before, during, after) = _frames(d, [0.5, 2.0, 4.5])
    assert _px(during, 0.70, 0.70)[0] > 200 and _px(during, 0.70, 0.70)[1] < 60  # red, at the layer's centre
    assert _px(before, 0.70, 0.70) != _px(during, 0.70, 0.70)
    assert _px(after, 0.70, 0.70)[0] < 200 or _px(after, 0.70, 0.70)[1] > 60  # the 3s source ended at 4s
    # circle: the square's corner around the centre is not red
    r = 200 / 1080  # radius as a fraction of the width
    cx, cy = 0.70, 0.70
    corner = _px(during, cx + r * 0.9, cy + r * 0.9 * 1080 / 1920)
    assert not (corner[0] > 200 and corner[1] < 60)


@needs_ffmpeg
def test_overlay_sound_mixes_in(two_sources, cli):
    d, lid = two_sources
    cli("volume", "0")  # silence the main clip

    def level(path) -> float:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        return float(re.search(r"max_volume: (-?[\d.]+|-inf) dB", out).group(1).replace("-inf", "-200"))

    out, _ = _frames(d, [])
    assert level(out) < -60  # overlays are muted by default
    cli("layer", "style", lid, "--volume", "1")
    out, _ = _frames(d, [])
    assert level(out) > -30
    out, _ = _frames(d, [], layers=False)  # the live preview's base video plays overlay sound in the browser instead
    assert level(out) < -60


@needs_ffmpeg
def test_blend_screen_and_stacking(cli, test_clip):
    cli("import", str(test_clip))
    cli("canvas", "--preset", "reels")
    a = cli("shape", "add", "rect", "--width", "1080", "--height", "1920", "--fill", "#FF0000", "--end", "5", "--blend", "screen")["layer"]
    d = cli.project
    _, (img,) = _frames(d, [1.0])
    # screen with pure red: red channel saturates, green / blue are what the video had
    base = _frames(d, [1.0], layers=False)[1][0]
    for fx in (0.1, 0.3, 0.5, 0.7, 0.9):
        got, under = _px(img, fx, 0.5), _px(base, fx, 0.5)
        assert got[0] > 235
        assert abs(got[1] - under[1]) < 25 and abs(got[2] - under[2]) < 25
    # stacking: two opaque boxes at the same place; the one later in the list is on top
    b = cli("shape", "add", "rect", "--width", "300", "--height", "300", "--fill", "#0000FF", "--end", "5")["layer"]
    c = cli("shape", "add", "rect", "--width", "300", "--height", "300", "--fill", "#00FF00", "--end", "5")["layer"]
    _, (img,) = _frames(d, [1.0])
    assert _px(img, 0.5, 0.5)[1] > 200  # green (added last) on top
    cli("layer", "order", c, "back")
    _, (img,) = _frames(d, [1.0])
    assert _px(img, 0.5, 0.5)[2] > 200  # now blue is on top
    assert [l.id for l in load(d).layers] == [c, a, b]


@needs_ffmpeg
@pytest.mark.parametrize("key", list(overlays.LIBRARY))
def test_every_library_overlay_adds_and_renders(cli, test_clip, key):
    cli("import", str(test_clip))
    r = cli("overlay", "add", key, "--start", "0.5")
    d = cli.project
    p = load(d)
    layer = p.layer(r["layer"])
    assert layer.type == overlays.LIBRARY[key].kind
    if layer.type == "video":
        assert (d / p.sources[layer.source].path).exists() or p.sources[layer.source].path.startswith("/")
    _, (plain,) = _frames(d, [1.5], layers=False)
    _, (img,) = _frames(d, [1.5])
    diff = np.abs(np.asarray(img, float) - np.asarray(plain, float)).mean()
    assert diff > 0.5, f"{key} left no mark on the picture ({diff:.2f})"


@needs_ffmpeg
def test_green_screen_video(cli, test_clip, tmp_path):
    gs = tmp_path / "gs.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=0x00FF00:s=640x360:r=30:d=3,drawbox=x=270:y=130:w=100:h=100:c=red:t=fill",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(gs)], check=True)
    cli("import", str(test_clip))
    cli("canvas", "--preset", "reels")
    cli("overlay", "add", str(gs), "--x", "50%", "--y", "50%", "--width", "1080", "--chroma", "#00FF00")
    _, (img,) = _frames(cli.project, [1.0])
    assert _px(img, 0.5, 0.5)[0] > 200 and _px(img, 0.5, 0.5)[1] < 80  # the red box stays
    g = _px(img, 0.1, 0.5)
    assert not (g[1] > 200 and g[0] < 60 and g[2] < 60)  # the green is gone


def test_sprite_api_draws_shapes_and_styled_images(cli, test_clip, tmp_path):
    import io

    from fastapi.testclient import TestClient

    from vedit.preview_server.app import create_app

    cli("import", str(test_clip))
    client = TestClient(create_app(cli.project, watch=False, chat=False))
    shape = Layer(id="s", type="shape", shape="ellipse", width=200, height=200, fill="#FF0000").model_dump(mode="json")
    img = Image.open(io.BytesIO(client.post("/api/sprite", json={"layer": shape, "scale": 1}).content))
    assert img.size == (200, 200) and img.getpixel((2, 2))[3] == 0
    pic = tmp_path / "p.png"
    Image.new("RGB", (100, 100), (0, 0, 255)).save(pic)
    il = Layer(id="i", type="image", image=str(pic), mask="circle").model_dump(mode="json")
    img = Image.open(io.BytesIO(client.post("/api/sprite", json={"layer": il, "scale": 1}).content))
    assert img.getpixel((1, 1))[3] == 0 and img.getpixel((50, 50))[2] == 255
    st = client.get("/api/state").json()
    assert client.get(f"/api/source/{next(iter(st['project']['sources']))}/media").status_code == 200
    assert client.get("/api/source/nope/media").status_code == 404
    assert len(client.get("/api/overlays").json()["library"]) == len(overlays.LIBRARY)

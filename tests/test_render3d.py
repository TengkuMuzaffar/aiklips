"""3D object layers: recipes, 3D text, the renderer, the travel / spin motion and the `vedit 3d` commands."""

from pathlib import Path

import numpy as np
import pytest

from vedit.ops import items3d
from vedit.ops.animate import props_at, travel_at
from vedit.ops.mesh3d import newell, text_mesh
from vedit.ops.render3d import render_obj3d
from vedit.project import Canvas, Layer, Obj3D, Preset, load, migrate

from .conftest import needs_ffmpeg


def _ink(img) -> int:
    return int((np.asarray(img.getchannel("A")) > 20).sum())


def test_every_kind_renders_at_any_turn_without_changing_size():
    for kind in ["text", *items3d.ITEMS3D]:
        L = Layer(id="o", type="obj3d", width=160, obj3d=Obj3D(kind=kind, text="Hi"))
        sizes = set()
        for yaw in (-25, 90, 200):
            img = render_obj3d(L, 1.0, yaw, 18, 0, t=0.3)
            sizes.add(img.size)
            assert _ink(img) > 200, (kind, yaw)
        assert sizes == {(160, 160)}, kind  # same box at every turn: no jitter while spinning


def test_faces_point_outwards():
    """Convex recipes keep their faces' normals pointing away from the centre (back faces are culled by this)."""
    from vedit.ops.mesh3d import box, ellipsoid, frustum

    for m in (box(1, 2, 3, "#fff"), ellipsoid(1, 0.5, 0.7, "#fff"), frustum(1, 0.4, 2, "#fff")):
        v = m.array()
        c = v.mean(0)
        for f in m.faces:
            p = v[f.loop]
            assert np.dot(newell(p), p.mean(0) - c) > 0


def test_3d_text_keeps_letter_holes():
    m = text_mesh("Ag8", None, 0.3, "#FFD447", "#FF6B3D")
    caps = [f for f in m.faces if f.holes]
    assert len(caps) >= 4  # A (1 hole) + g (1) + 8 (2), front and back: at least 4 capped faces with holes
    v = m.array()
    assert v[:, 2].min() == pytest.approx(-0.15) and v[:, 2].max() == pytest.approx(0.15)


def test_recipe_settings_are_checked():
    with pytest.raises(Exception, match="no setting"):
        items3d.build("car", {"wings": 2}, None, None, 0)
    a = render_obj3d(Layer(id="o", type="obj3d", width=120, obj3d=Obj3D(kind="car", params={"style": "van"})))
    b = render_obj3d(Layer(id="o", type="obj3d", width=120, obj3d=Obj3D(kind="car", params={"style": "sport"})))
    assert np.asarray(a).tobytes() != np.asarray(b).tobytes()


def test_legs_and_wheels_move_over_time():
    L = Layer(id="o", type="obj3d", width=140, obj3d=Obj3D(kind="dog"))
    assert np.asarray(render_obj3d(L, t=0.0)).tobytes() != np.asarray(render_obj3d(L, t=0.12)).tobytes()
    L.obj3d.gait = False
    assert np.asarray(render_obj3d(L, t=0.0)).tobytes() == np.asarray(render_obj3d(L, t=0.12)).tobytes()


def test_travel_once_pingpong_continue():
    c = Canvas()
    k = {"from": "10%,50%", "to": "90%,50%", "dur": 2}
    x0, y0, face, _ = travel_at(0, k, c)
    assert (x0, y0, face) == (pytest.approx(108), pytest.approx(960), 0.0)
    assert travel_at(5, k, c)[0] == pytest.approx(972)  # once: stays at B
    pp = {**k, "loop": "pingpong"}
    assert travel_at(3, pp, c)[0] == pytest.approx(540)  # half way back
    assert travel_at(3, pp, c)[2] == 1.0  # facing the other way on the way back
    ct = {**k, "loop": "continue"}
    xs = [travel_at(t, ct, c)[0] for t in np.arange(0, 8, 0.25)]
    assert max(xs) <= 1080 * 1.25 + 1e-6 and min(xs) >= -1080 * 0.25 - 1e-6  # wraps round the canvas
    assert any(b < a for a, b in zip(xs, xs[1:]))  # came back in from the left


def test_spin_and_face_change_yaw():
    c = Canvas()
    L = Layer(id="o", type="obj3d", obj3d=Obj3D(kind="car", yaw=10), presets=[Preset(name="spin3d", params={"period": 2})])
    assert props_at(L, 0, 4, c)["yaw"] == pytest.approx(10)
    assert props_at(L, 1, 4, c)["yaw"] == pytest.approx(190)
    L.presets = [Preset(name="travel", params={"from": "90%,50%", "to": "10%,50%"})]  # heading left: turns round
    assert props_at(L, 1, 4, c)["yaw"] == pytest.approx(190)
    assert props_at(Layer(id="t", type="text", text="x"), 1, 4, c)["yaw"] == 0  # other layers don't turn


def test_old_projects_migrate():
    assert migrate({"version": 4, "name": "x"})["version"] == 5


def test_3d_commands(cli):
    cli("shape", "add", "rect", "--full", "--fill", "#111827", "--end", "3")
    a = cli("3d", "add", "text", "--text", "WOW", "--font", "anton", "--y", "25%", "--end", "3")
    lid = a["layer"]
    cli("3d", "spin", lid, "--axis", "x", "--period", "1.5")
    cli("3d", "set", lid, "--yaw", "30", "--fov", "60", "--color", "#FF0000", "--outline", "none")
    car = cli("3d", "add", "car", "--param", "style=sport", "--y", "70%", "--end", "3", "--no-intro")["layer"]
    cli("3d", "move", car, "--from", "-10%,70%", "--to", "110%,70%", "--loop", "continue", "--dur", "1.5", "--hop", "4")
    p = load(cli.project)
    t, c = p.layer(lid), p.layer(car)
    assert t.obj3d.yaw == 30 and t.obj3d.fov == 60 and t.obj3d.outline is None and t.obj3d.color == "#FF0000"
    assert [x.name for x in t.presets] == ["spin3d_in", "spin3d"] and t.presets[1].params["axis"] == "x"
    tr = next(x for x in c.presets if x.name == "travel")
    assert tr.params["loop"] == "continue" and tr.params["from"] == "-10%,70%"
    assert c.obj3d.params == {"style": "sport"}
    info = cli("3d", "show", car)
    assert info["kind"] == "car" and any(m["name"] == "travel" for m in info["motion"])
    cli("3d", "move", car, "--off")
    assert not any(x.name == "travel" for x in load(cli.project).layer(car).presets)
    cli("3d", "add", "dog", "--param", "wings=2", ok=False)
    cli("3d", "add", "text", ok=False)
    cli("3d", "spin", lid, "--axis", "w", ok=False)
    assert {r["kind"] for r in cli("3d", "kinds")["kinds"]} >= {"text", "car", "dog", "model"}


@needs_ffmpeg
def test_3d_renders_in_the_export(cli):
    cli("shape", "add", "rect", "--full", "--fill", "#111827", "--end", "2")
    lid = cli("3d", "add", "gem", "--end", "2", "--width", "400")["layer"]
    cli("3d", "spin", lid, "--period", "1")
    imgs = cli("frame", "--at", "0.9,1.2", "--width", "300")["images"]
    assert all(Path(i).exists() for i in imgs)
    from PIL import Image

    a, b = (np.asarray(Image.open(i).convert("L"), float) for i in imgs)
    assert np.abs(a - b).mean() > 0.1  # it turned between the two moments

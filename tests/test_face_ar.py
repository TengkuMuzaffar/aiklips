"""Face AR painters with synthetic landmarks / pose (no camera or real face needed)."""

import numpy as np
import pytest
from PIL import Image

from vedit.ops import face_ar


def _fake_face(w=400, h=600):
    lm = np.zeros((face_ar.N_LM, 3), np.float32)
    lm[:, 0], lm[:, 1] = w / 2, h / 2
    lm[face_ar.L_EYE_OUT] = (150, 250, 0)
    lm[face_ar.R_EYE_OUT] = (250, 250, 0)
    lm[face_ar.L_CHEEK] = (120, 300, 0)
    lm[face_ar.R_CHEEK] = (280, 300, 0)
    lm[face_ar.FOREHEAD] = (200, 180, 0)
    lm[face_ar.CHIN] = (200, 420, 0)
    return lm


def test_sticker_follows_eyes(tmp_path):
    Image.new("RGBA", (100, 40), (255, 0, 0, 255)).save(tmp_path / "glasses.png")
    paint = face_ar._sticker_painter(tmp_path, {"image": "glasses.png", "anchor": "eyes"}, 400, 600)
    out = paint(Image.new("RGBA", (400, 600), (0, 0, 0, 0)), [_fake_face()], [])
    alpha = np.asarray(out)[:, :, 3]
    ys, xs = np.nonzero(alpha)
    assert abs(xs.mean() - 200) < 3 and abs(ys.mean() - 250) < 3  # centred between the eyes


def test_prop_projects_in_front_of_camera(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    trimesh.creation.box(extents=(1, 1, 1)).export(tmp_path / "box.obj")
    paint = face_ar._prop_painter(tmp_path, {"model": "box.obj", "size_cm": 10, "y_cm": 0}, 400, 600, 30)
    pose = np.eye(4, dtype=np.float32)
    pose[2, 3] = -60  # face 60cm in front of the camera
    out = paint(Image.new("RGBA", (400, 600), (0, 0, 0, 0)), [_fake_face()], [pose])
    alpha = np.asarray(out)[:, :, 3]
    ys, xs = np.nonzero(alpha)
    assert len(xs) > 500
    assert abs(xs.mean() - 200) < 10 and abs(ys.mean() - 300) < 10  # centred in the image


def test_hull_is_convex():
    pts = np.array([[0, 0], [2, 0], [2, 2], [0, 2], [1, 1]], float)
    assert len(face_ar._hull(pts)) == 4

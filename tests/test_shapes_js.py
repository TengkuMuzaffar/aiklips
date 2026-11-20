"""static/shapes.js is a port of ops/shapes.py: the live preview's SVG shapes must have the export's outlines."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from vedit.ops import shapes

SHAPES_JS = Path(__file__).parents[1] / "src/vedit/preview_server/static/shapes.js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

CASES = [
    ("triangle", 300, 200, {}), ("diamond", 120, 333, {}), ("polygon", 400, 300, {"sides": 6}),
    ("polygon", 250, 250, {"sides": 5}), ("star", 380, 380, {"points": 5, "inner": 0.48}),
    ("star", 500, 200, {"points": 9, "inner": 0.8}), ("heart", 360, 330, {}), ("blob", 420, 400, {}),
    ("bubble", 560, 300, {"radius": 48}), ("bubble", 100, 400, {"radius": 900}),
]


def _py(kind, w, h, o):
    return shapes.outline(kind, w, h, o.get("sides", 6), o.get("points", 5), o.get("inner", 0.5), o.get("radius", 0))


@needs_node
def test_js_outlines_match_python():
    script = (f"const S = require({json.dumps(str(SHAPES_JS))});"
              "let d='';process.stdin.on('data',c=>d+=c).on('end',()=>{const cs=JSON.parse(d);"
              "process.stdout.write(JSON.stringify(cs.map(([k,w,h,o])=>S.outline(k,w,h,o.sides??6,o.points??5,o.inner??0.5,o.radius??0))));});")
    out = subprocess.run(["node", "-e", script], input=json.dumps(CASES), capture_output=True, text=True, check=True)
    for (kind, w, h, o), js in zip(CASES, json.loads(out.stdout)):
        py = _py(kind, w, h, o)
        assert len(js) == len(py), kind
        for a, b in zip(py, js):
            assert b == pytest.approx(list(a), abs=1e-6), kind


@needs_node
def test_js_svg_draws_every_kind():
    from vedit.ops.overlays import SHAPE_DEFAULTS

    layers = [{"shape": k, **SHAPE_DEFAULTS[k], "fill_gradient": ["#FF0000", "#0000FF"] if k == "star" else None}
              for k in SHAPE_DEFAULTS]
    script = (f"const S = require({json.dumps(str(SHAPES_JS))});"
              "let d='';process.stdin.on('data',c=>d+=c).on('end',()=>{const ls=JSON.parse(d);"
              "process.stdout.write(JSON.stringify(ls.map(l=>S.svg(l,l.width/2,l.height/2,undefined,0.5))));});")
    out = json.loads(subprocess.run(["node", "-e", script], input=json.dumps(layers), capture_output=True, text=True,
                                    check=True).stdout)
    for l, s in zip(layers, out):
        assert s.startswith("<svg") and "<path" in s and "NaN" not in s, l["shape"]
    assert "linearGradient" in out[[l["shape"] for l in layers].index("star")]

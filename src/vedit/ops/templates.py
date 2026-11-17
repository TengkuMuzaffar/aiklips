"""Templates: a group of overlay layers (a lower third, an end card, a hook title with its arrow and box...) saved
once and dropped into any project at the playhead, timing kept relative to the group's start.

Stored in ~/.claude-cut/templates/<name>.json. Image and video layers keep the absolute path of their media, so the
template works in other projects as long as the file is still there.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..project import Layer, Project, VeditError, resolve_media


def templates_dir() -> Path:
    from ..library import home_dir

    d = home_dir() / "templates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not s:
        raise VeditError("Give the template a name")
    return s[:60]


def save(p: Project, pdir: Path, name: str, layer_ids: list[str], about: str = "") -> dict:
    if not layer_ids:
        raise VeditError("Pick the layers to save (e.g. --layer t1 --layer sh1)")
    layers = [p.layer(i) for i in layer_ids]
    layers.sort(key=lambda l: p.layers.index(l))  # keep their stacking order
    t0 = min(l.start for l in layers)
    out = []
    for l in layers:
        if l.type == "captions":
            raise VeditError(f"{l.id} is captions: those belong to one video, not a template")
        d = l.model_dump(mode="json", by_alias=True)
        d["start"] = round(l.start - t0, 3)
        d["end"] = None if l.end is None else round(l.end - t0, 3)
        if l.type == "image" and l.image:
            d["image"] = str(resolve_media(pdir, l.image))
        if l.type == "video" and l.source in p.sources:
            d["media"] = str(resolve_media(pdir, p.sources[l.source].path))
            d["source"] = None
        out.append(d)
    data = {"name": name.strip(), "about": about, "layers": out, "length": max((x["end"] or 0) for x in out) or None}
    (templates_dir() / f"{_slug(name)}.json").write_text(json.dumps(data, indent=1))
    return data


def catalog() -> list[dict]:
    rows = []
    for f in sorted(templates_dir().glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        rows.append({"key": f.stem, "name": d.get("name", f.stem), "about": d.get("about", ""), "layers": len(d.get("layers", [])),
                     "types": sorted({x.get("type") for x in d.get("layers", [])}), "length": d.get("length")})
    return rows


def load_template(key: str) -> dict:
    f = templates_dir() / f"{_slug(key)}.json"
    if not f.exists():
        raise VeditError(f"No template '{key}'. Templates: {', '.join(r['key'] for r in catalog()) or 'none'}")
    return json.loads(f.read_text())


def apply(p: Project, pdir: Path, key: str, at: float = 0.0) -> list[str]:
    """Add the template's layers starting at `at` (timeline seconds). Returns the new layer ids."""
    from .fx_filters import _new_id
    from .overlays import add_source

    data = load_template(key)
    ids = []
    for d in data["layers"]:
        d = dict(d)
        media = d.pop("media", None)
        if d.get("type") == "video":
            if not media or not Path(media).exists():
                raise VeditError(f"The template's video is missing: {media}")
            d["source"] = add_source(p, pdir, Path(media))
        if d.get("type") == "image" and d.get("image") and not Path(d["image"]).exists():
            raise VeditError(f"The template's image is missing: {d['image']}")
        prefix = {"text": "t", "image": "img", "video": "v", "shape": "sh", "adjust": "adj"}.get(d["type"], "l")
        d["id"] = p.new_id(prefix)
        d["start"] = round(at + d["start"], 3)
        d["end"] = None if d.get("end") is None else round(at + d["end"], 3)
        if p.duration and d["start"] >= p.duration:
            continue
        if d["end"] is not None and p.duration:
            d["end"] = min(d["end"], round(p.duration, 3))
        layer = Layer.model_validate(d)
        p.layers.append(layer)
        for e in layer.effects:  # filter ids must stay unique in this project
            e.params["id"] = _new_id(p)
        ids.append(layer.id)
    if not ids:
        raise VeditError("Nothing to add here: the playhead is at the end of the timeline")
    return ids


def delete(key: str) -> None:
    f = templates_dir() / f"{_slug(key)}.json"
    if not f.exists():
        raise VeditError(f"No template '{key}'")
    f.unlink()

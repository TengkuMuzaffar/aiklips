"""The user's own material for generation: uploads, and a shot log the AI can plan with.

Files: ~/.claude-cut/uploads/<id>/   (id = first 16 hex chars of the content hash)
    file.<ext>   the uploaded file
    meta.json    kind (video / audio / image), duration, size; beats for audio
    index.json   video segments (cuts, long takes split into ≤3.5 s pieces)
    notes.json   AI descriptions per segment (cached, so a file is only described once)
    sheet.jpg    one labelled frame per segment

Each upload is used in a batch either as FOOTAGE (pictures; a video keeps its own audio) or as
SOUND (only its audio, e.g. a trending sound saved as a video). classify() suggests the role:
code measures the audio, the AI looks at a contact sheet and judges.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

from .. import library, media
from ..ai import client as ai
from ..ai import prompts
from ..project import VeditError
from ..references import analysis
from ..references.store import file_hash
from .spec import FootageNotes, MediaRoles

VIDEO = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
IMAGE = {".png", ".jpg", ".jpeg", ".webp"}
MAX_SEG = 3.5
MAX_SHEET_SEGMENTS = 16


def root() -> Path:
    d = library.home_dir() / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dir(uid: str) -> Path:
    if not uid or not all(c in "0123456789abcdef" for c in uid):
        raise VeditError(f"Bad upload id {uid!r}")
    return root() / uid


def _json(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def add(path: Path, name: str | None = None, move: bool = False) -> dict:
    """Store a footage / music / image file (deduplicated) and measure it."""
    path = Path(path).expanduser().resolve()
    ext = path.suffix.lower()
    if ext not in VIDEO | AUDIO | IMAGE:
        raise VeditError(f"{name or path.name}: unsupported file type {ext or '(none)'}")
    uid = file_hash(path)[:16]
    d = _dir(uid)
    if (d / "meta.json").exists():
        if move:
            path.unlink(missing_ok=True)
        return get(uid)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / ("file" + ext)
    (shutil.move if move else shutil.copy2)(str(path), dest)
    meta: dict = {"id": uid, "name": (name or path.name)[:120], "path": str(dest)}
    try:
        if ext in IMAGE:
            from PIL import Image

            with Image.open(dest) as im:
                meta.update(kind="image", width=im.width, height=im.height, duration=None, has_audio=False)
                im.convert("RGB").resize((360, max(1, int(im.height * 360 / im.width)))).save(d / "thumb.jpg", "JPEG", quality=85)
        else:
            info = media.probe(dest)
            kind = "video" if info["has_video"] and ext in VIDEO else "audio"
            if kind == "audio" and not info["has_audio"]:
                raise VeditError(f"{meta['name']} has no audio")
            meta.update(kind=kind, **info)
            if kind == "video":
                media.extract_frame(dest, min(1.0, info["duration"] / 3), d / "thumb.jpg", 360)
            else:
                a = analysis.analyze_audio(dest, min(info["duration"], 600))
                meta.update(bpm=(a or {}).get("bpm"), beats=(a or {}).get("beats", []), rms=(a or {}).get("rms", []))
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    (d / "meta.json").write_text(json.dumps(meta))
    return get(uid)


def get(uid: str) -> dict:
    meta = _json(_dir(uid) / "meta.json")
    if meta is None:
        raise VeditError(f"Upload {uid} not found (it may have been cleaned up). Add the file again.")
    meta = {k: v for k, v in meta.items() if k not in ("beats", "rms")} | {"beat_count": len(meta.get("beats") or [])}
    return meta


def meta_full(uid: str) -> dict:
    meta = _json(_dir(uid) / "meta.json")
    if meta is None:
        raise VeditError(f"Upload {uid} not found")
    return meta


def thumb(uid: str) -> Path:
    return _dir(uid) / "thumb.jpg"


# ---- shot log -----------------------------------------------------------------------------

def segments(uid: str) -> list[dict]:
    """[{k, from, to}] — cuts found in the file, long takes split into ≤3.5 s pieces."""
    d = _dir(uid)
    idx = _json(d / "index.json")
    if idx is not None:
        return idx
    meta = meta_full(uid)
    if meta["kind"] != "video":
        return []
    dur = float(meta["duration"])
    cuts = analysis.detect_cuts(Path(meta["path"]))
    edges = [0.0] + [c for c in cuts if 0.3 < c < dur - 0.3] + [dur]
    out: list[dict] = []
    for a, b in zip(edges, edges[1:]):
        n = max(1, round((b - a) / MAX_SEG))
        step = (b - a) / n
        for i in range(n):
            out.append({"k": len(out) + 1, "from": round(a + i * step, 2), "to": round(a + (i + 1) * step, 2)})
    (d / "index.json").write_text(json.dumps(out))
    return out


def _sheet(uid: str, fid: str, segs: list[dict]) -> Path:
    d = _dir(uid)
    meta = meta_full(uid)
    shown = segs if len(segs) <= MAX_SHEET_SEGMENTS else [segs[int(i * len(segs) / MAX_SHEET_SEGMENTS)] for i in range(MAX_SHEET_SEGMENTS)]
    times = [round((s["from"] + s["to"]) / 2, 2) for s in shown]
    frames = analysis.sample_frames(Path(meta["path"]), times, d / "frames", "seg")
    dest = d / f"sheet-{fid}.jpg"
    analysis._tile([(p, f"{fid}.s{s['k']}  {s['from']:.1f}-{s['to']:.1f}s") for p, s in zip(frames, shown)], dest, cols=4,
                   title=f"{fid}: {meta['name']}")
    return dest


def describe(files: list[tuple[str, str]], quality: str = "balanced", progress: Callable[[str], None] | None = None) -> dict:
    """Make sure every video in [(fid, upload id)] has AI notes per segment. Returns usage."""
    todo = []
    for fid, uid in files:
        if meta_full(uid)["kind"] != "video":
            continue
        segs = segments(uid)
        notes = _json(_dir(uid) / "notes.json", {})
        if any(str(s["k"]) not in notes for s in segs):
            todo.append((fid, uid, segs))
    usage: dict = {"calls": 0}
    for i in range(0, len(todo), ai.MAX_IMAGES):
        chunk = todo[i: i + ai.MAX_IMAGES]
        if progress:
            progress(f"AI is logging your footage ({min(i + ai.MAX_IMAGES, len(todo))}/{len(todo)} files)")
        sheets = [_sheet(uid, fid, segs) for fid, uid, segs in chunk]
        listing = "\n".join(f"{fid}.s{s['k']}: {s['from']:.1f}-{s['to']:.1f}s of {meta_full(uid)['name']}"
                            for fid, uid, segs in chunk for s in segs)
        try:
            res, u = ai.call("footage", "Segments to log (one entry each):\n" + listing, FootageNotes,
                             system=prompts.FOOTAGE, images=sheets, quality=quality)
            usage["calls"] += 1
            by_id = {n.id.strip(): n for n in res.segments}
        except ai.RateLimited:
            raise
        except ai.AIError:
            by_id = {}
        for fid, uid, segs in chunk:
            notes = _json(_dir(uid) / "notes.json", {})
            for s in segs:
                n = by_id.get(f"{fid}.s{s['k']}")
                if n is not None:
                    notes[str(s["k"])] = n.model_dump(exclude={"id"})
            (_dir(uid) / "notes.json").write_text(json.dumps(notes))
    return usage


def shot_log(files: list[tuple[str, str]], sound: list[tuple[str, str]] = ()) -> dict:
    """What the planner sees: {fid: {name, kind, duration, segments:[{id, from, to, description, quality}]}}.
    `sound` files appear as kind 'audio' whatever they are on disk."""
    out = {}
    for fid, uid in sound:
        meta = sound_meta(uid)
        out[fid] = {"name": meta["name"], "kind": "audio", "duration": meta.get("duration"), "bpm": meta.get("bpm"),
                    "beats": len(meta.get("beats") or []), **({"taken_from": "video"} if meta["from_video"] else {})}
    for fid, uid in files:
        meta = meta_full(uid)
        entry = {"name": meta["name"], "kind": meta["kind"], "duration": meta.get("duration")}
        if meta["kind"] == "video":
            notes = _json(_dir(uid) / "notes.json", {})
            entry["has_audio"] = meta.get("has_audio")
            entry["segments"] = [{"id": f"{fid}.s{s['k']}", "from": s["from"], "to": s["to"],
                                  **(notes.get(str(s["k"])) or {"description": "(not described)", "quality": 3})}
                                 for s in segments(uid)]
        elif meta["kind"] == "audio":
            entry["bpm"] = meta.get("bpm")
            entry["beats"] = len(meta.get("beats") or [])
        else:
            entry["size"] = f"{meta.get('width')}x{meta.get('height')}"
        out[fid] = entry
    return out


# ---- sound -------------------------------------------------------------------------------------

def _audio_stats(uid: str) -> dict:
    """Beats / tempo / loudness of the file's audio, measured once and cached in meta.json."""
    d = _dir(uid)
    meta = meta_full(uid)
    if "beats" not in meta and meta["kind"] == "video" and meta.get("has_audio"):
        a = analysis.analyze_audio(Path(meta["path"]), min(float(meta["duration"]), 600)) or {}
        meta.update(bpm=a.get("bpm"), beats=a.get("beats", []), rms=a.get("rms", []),
                    silence_ratio=a.get("silence_ratio"), level_db=a.get("level_db"))
        (d / "meta.json").write_text(json.dumps(meta))
    return meta


def sound_meta(uid: str) -> dict:
    """The upload seen as a sound track: kind 'audio' (even for a video), with beats for timing."""
    meta = meta_full(uid)
    if meta["kind"] == "image":
        raise VeditError(f"{meta['name']} is an image and has no sound")
    if meta["kind"] == "video" and not meta.get("has_audio"):
        raise VeditError(f"{meta['name']} has no sound to use")
    meta = _audio_stats(uid)
    return {**meta, "kind": "audio", "from_video": meta["kind"] == "video"}


def _role_sheet(uid: str) -> Path:
    meta = meta_full(uid)
    dest = _dir(uid) / "roles.jpg"
    if not dest.exists():
        media.contact_sheet(Path(meta["path"]), float(meta["duration"]), dest, count=8, cols=4, width=200)
    return dest


def classify(uids: list[str], quality: str = "balanced") -> dict[str, dict]:
    """{upload id: {role: footage|sound, reason, by: ai|rule}}. The AI is asked only about videos with
    sound (the only real question); its answer is cached per file, so each file costs one look at most."""
    out: dict[str, dict] = {}
    ask: list[str] = []
    for uid in dict.fromkeys(uids):
        meta = meta_full(uid)
        if meta.get("role_hint"):
            out[uid] = meta["role_hint"]
        elif meta["kind"] == "image":
            out[uid] = {"role": "footage", "reason": "image", "by": "rule"}
        elif meta["kind"] == "audio":
            out[uid] = {"role": "sound", "reason": "audio file", "by": "rule"}
        elif not meta.get("has_audio"):
            out[uid] = {"role": "footage", "reason": "video without sound", "by": "rule"}
        else:
            ask.append(uid)
    for i in range(0, len(ask), ai.MAX_IMAGES):
        chunk = ask[i: i + ai.MAX_IMAGES]
        rows = []
        for n, uid in enumerate(chunk):
            m = _audio_stats(uid)
            rows.append(f"m{n + 1}: {m['name']!r}, {float(m['duration']):.1f}s, cuts: {len(segments(uid))} segments, "
                        f"audio: bpm={m.get('bpm')}, beats={len(m.get('beats') or [])}, silence_ratio={m.get('silence_ratio')}, "
                        f"level_db={m.get('level_db')}")
        try:
            res, _ = ai.call("classify", "Files (contact sheet i belongs to file mi):\n" + "\n".join(rows), MediaRoles,
                             system=prompts.CLASSIFY, images=[_role_sheet(u) for u in chunk], quality=quality)
            by_id = {r.id.strip(): r for r in res.files}
        except ai.AIError:
            by_id = {}
        for n, uid in enumerate(chunk):
            r = by_id.get(f"m{n + 1}")
            if r is None:  # no AI answer: guess, but don't cache so the AI can look next time
                out[uid] = {"role": "footage", "reason": "guess (AI unavailable)", "by": "rule"}
                continue
            out[uid] = {"role": r.role, "reason": r.reason[:140], "by": "ai"}
            meta = meta_full(uid)
            meta["role_hint"] = out[uid]
            (_dir(uid) / "meta.json").write_text(json.dumps(meta))
    return out

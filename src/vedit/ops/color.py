"""Colour: measure footage, match clips to each other or to a reference, and grade with looks.

Code does all of it, no AI. The AI only picks a look by name (and can say why).

    stats(path, a, b)      brightness / contrast / saturation / warmth / tint of a time range,
                           from a few small sampled frames (cached)
    match(src, target)     the Grade correction that moves `src` stats onto `target` stats
    match_clips(project)   correct every clip toward one target: the average of the clips (shot
                           matching), one hero clip, or a reference video's measured colour
    filter_chain(grade)    FFmpeg filters for a Grade (eq, colorchannelmixer, colorbalance,
                           curves, lut3d, vignette), applied per clip at canvas size

Units: all stats are 0..1 on sRGB values. `warmth` = mean(R - B), `tint` = mean(G - (R+B)/2).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

from .. import media
from ..project import Clip, Grade, Project, VeditError, resolve_media, state_dir

# looks: offsets applied on top of the correction, scaled by Grade.intensity
#   brightness/temperature/tint add, contrast/saturation multiply,
#   shadows/highlights: (r, g, b) colour balance shifts, fade: lifted blacks, vignette 0..1
LOOKS: dict[str, dict] = {
    "natural": {"about": "No creative grade, just the correction"},
    "clean-bright": {"about": "Bright, airy, slightly warm. Lifestyle, beauty, UGC",
                     "brightness": 0.04, "contrast": 1.04, "saturation": 1.08, "temperature": 0.12},
    "vibrant": {"about": "Punchy saturated colour. Food, travel, products",
                "contrast": 1.08, "saturation": 1.32},
    "warm-film": {"about": "Soft warm film: faded blacks, gentle contrast, light vignette",
                  "contrast": 0.94, "saturation": 0.9, "temperature": 0.4, "fade": 0.06,
                  "shadows": (0.03, 0.0, -0.03), "highlights": (0.04, 0.02, -0.04), "vignette": 0.3},
    "golden-hour": {"about": "Sunset gold highlights, warm and glowing",
                    "brightness": 0.02, "saturation": 1.1, "temperature": 0.6, "highlights": (0.08, 0.04, -0.06)},
    "teal-orange": {"about": "Blockbuster: teal shadows, orange skin and highlights",
                    "contrast": 1.1, "saturation": 1.1, "shadows": (-0.08, 0.0, 0.1), "highlights": (0.1, 0.03, -0.08)},
    "cool-crisp": {"about": "Cool, clean and sharp. Tech, corporate, winter",
                   "contrast": 1.08, "saturation": 1.02, "temperature": -0.35},
    "moody": {"about": "Dark, desaturated, cool shadows, heavy vignette. Drama, night, fashion",
              "brightness": -0.05, "contrast": 1.15, "saturation": 0.75, "temperature": -0.15,
              "shadows": (-0.04, 0.0, 0.06), "vignette": 0.55},
    "vintage": {"about": "Washed-out retro: low saturation, faded, warm highlights, purple shadows",
                "contrast": 0.88, "saturation": 0.72, "temperature": 0.25, "fade": 0.1,
                "shadows": (0.04, 0.0, 0.06), "highlights": (0.05, 0.04, -0.03)},
    "pastel": {"about": "Soft and light, low contrast, gentle pink. Kids, cafes, aesthetic",
               "brightness": 0.05, "contrast": 0.86, "saturation": 0.88, "tint": 0.12, "fade": 0.07},
    "bw": {"about": "Contrasty black and white", "saturation": 0.0, "contrast": 1.2},
    "bw-soft": {"about": "Soft matte black and white", "saturation": 0.0, "contrast": 0.95, "fade": 0.06},
}

K_TEMP = 0.10  # colorchannelmixer gain per unit of temperature (R up, B down)
K_TINT = 0.08  # green gain per unit of tint (down = magenta)
SAMPLES = 6
STAT_KEYS = ("luma", "p05", "p95", "sat", "warmth", "tint")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _r(v: float, n: int = 4) -> float:
    return round(float(v), n)


# ---- measuring --------------------------------------------------------------------------------

def frame_stats(rgb: np.ndarray) -> dict:
    """rgb: (..., 3) float array in 0..1."""
    px = rgb.reshape(-1, 3)
    r, g, b = px[:, 0], px[:, 1], px[:, 2]
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return {
        "luma": _r(luma.mean()), "p05": _r(np.percentile(luma, 5)), "p95": _r(np.percentile(luma, 95)),
        "sat": _r((px.max(axis=1) - px.min(axis=1)).mean()),
        "warmth": _r((r - b).mean()), "tint": _r((g - (r + b) / 2).mean()),
    }


def _grab(path: Path, t: float, size: int = 64) -> np.ndarray | None:
    proc = subprocess.run(
        [media.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
         "-frames:v", "1", "-an", "-vf", f"scale={size}:{size},format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or len(proc.stdout) < size * size * 3:
        return None
    return np.frombuffer(proc.stdout[: size * size * 3], dtype=np.uint8).reshape(size, size, 3).astype(np.float32) / 255.0


def stats(path: Path, start: float, end: float, samples: int = SAMPLES, cache: Path | None = None) -> dict:
    """Colour stats of path between start and end (seconds in the file)."""
    path = Path(path)
    key = None
    if cache is not None:
        st = path.stat()
        key = hashlib.sha1(f"{path.resolve()}|{st.st_mtime}|{st.st_size}|{start:.2f}|{end:.2f}|{samples}".encode()).hexdigest()[:16]
        f = cache / f"{key}.json"
        if f.exists():
            try:
                return json.loads(f.read_text())
            except json.JSONDecodeError:
                pass
    span = max(0.0, end - start)
    times = [start + span * (i + 0.5) / samples for i in range(samples)] if span > 0.05 else [start]
    frames = [f for f in (_grab(path, t) for t in times) if f is not None]
    if not frames:
        raise VeditError(f"Could not read frames from {path.name} for colour measurement")
    out = frame_stats(np.stack(frames))
    if key is not None:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{key}.json").write_text(json.dumps(out))
    return out


def average(items: list[tuple[dict, float]]) -> dict:
    """Duration-weighted mean of several stats dicts."""
    tot = sum(w for _, w in items) or 1.0
    return {k: _r(sum(s[k] * w for s, w in items) / tot) for k in STAT_KEYS}


def _cache(pdir: Path) -> Path:
    return state_dir(pdir) / "cache" / "color"


def clip_stats(project: Project, pdir: Path, clip: Clip) -> dict | None:
    src = project.sources[clip.source]
    if not src.has_video:
        return None
    return stats(resolve_media(pdir, src.path), clip.in_, clip.out, cache=_cache(pdir))


# ---- matching ---------------------------------------------------------------------------------

def match(src: dict, tgt: dict, strength: float = 0.8) -> dict:
    """Grade correction fields that move `src` onto `tgt`. Clamped so bad input can't wreck a shot."""
    k = _clamp(strength, 0.0, 1.0)
    c = _clamp((tgt["p95"] - tgt["p05"]) / max(0.05, src["p95"] - src["p05"]), 0.75, 1.35)
    c = 1 + (c - 1) * k
    b = _clamp(tgt["luma"] - ((src["luma"] - 0.5) * c + 0.5), -0.2, 0.2) * k
    sat = 1.0 if src["sat"] < 0.02 else 1 + (_clamp(tgt["sat"] / src["sat"], 0.6, 1.6) - 1) * k
    L = max(0.15, src["luma"])
    temp = _clamp((tgt["warmth"] - src["warmth"]) / (2 * K_TEMP * L), -1.0, 1.0) * k
    tint = _clamp(-(tgt["tint"] - src["tint"]) / (K_TINT * L), -1.0, 1.0) * k
    return {"brightness": _r(b, 3), "contrast": _r(c, 3), "saturation": _r(sat, 3), "temperature": _r(temp, 3), "tint": _r(tint, 3)}


def match_clips(project: Project, pdir: Path, target: dict | None = None, hero: str | None = None, strength: float = 0.8,
                clip_ids: list[str] | None = None, label: str | None = None) -> dict:
    """Correct clips toward one target. No target and no hero = the duration-weighted average of
    all clips, so the whole edit reads as one shoot. Keeps each clip's look. Returns a report."""
    measured = {c.id: clip_stats(project, pdir, c) for c in project.clips}
    measured = {k: v for k, v in measured.items() if v}
    if not measured:
        raise VeditError("No video clips to match")
    if hero:
        project.clip(hero)
        if hero not in measured:
            raise VeditError(f"Clip {hero} has no picture to match to")
        target, label = measured[hero], hero
    elif target is None:
        target = average([(measured[c.id], c.duration) for c in project.clips if c.id in measured])
        label = "clips"
    targets = [project.clip(i) for i in clip_ids] if clip_ids else project.clips
    changed = {}
    for c in targets:
        s = measured.get(c.id)
        if not s:
            continue
        corr = {k: 0.0 if k in ("brightness", "temperature", "tint") else 1.0 for k in ("brightness", "contrast", "saturation", "temperature", "tint")} \
            if c.id == hero else match(s, target, strength)
        g = c.grade or Grade()
        c.grade = g.model_copy(update={**corr, "matched_to": label})
        changed[c.id] = corr
    return {"target": target, "matched_to": label, "clips": changed}


def set_look(project: Project, name: str, intensity: float = 1.0, clip_ids: list[str] | None = None) -> list[str]:
    key = look_name(name)
    targets = [project.clip(i) for i in clip_ids] if clip_ids else project.clips
    for c in targets:
        g = c.grade or Grade()
        c.grade = g.model_copy(update={"look": None if key == "natural" else key, "intensity": _clamp(intensity, 0.0, 1.0)})
    return [c.id for c in targets]


def look_name(name: str) -> str:
    key = name.strip().lower().replace("_", "-").replace(" ", "-")
    if key not in LOOKS:
        raise VeditError(f"Unknown colour look '{name}'. Looks: {', '.join(LOOKS)}")
    return key


# ---- rendering ---------------------------------------------------------------------------------

def _f(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def _escape_path(p: str) -> str:
    return "'" + p.replace("\\", "/").replace("'", r"'\''") + "'"


def resolved(g: Grade) -> dict:
    """Correction + look combined into final filter numbers."""
    look = LOOKS.get(g.look or "natural", {})
    i = _clamp(g.intensity, 0.0, 1.0)
    return {
        "brightness": _clamp(g.brightness + look.get("brightness", 0.0) * i, -0.4, 0.4),
        "contrast": _clamp(g.contrast * (1 + (look.get("contrast", 1.0) - 1) * i), 0.4, 2.0),
        "saturation": _clamp(g.saturation * (1 + (look.get("saturation", 1.0) - 1) * i), 0.0, 3.0),
        "temperature": _clamp(g.temperature + look.get("temperature", 0.0) * i, -1.5, 1.5),
        "tint": _clamp(g.tint + look.get("tint", 0.0) * i, -1.5, 1.5),
        "shadows": tuple(v * i for v in look.get("shadows", (0, 0, 0))),
        "highlights": tuple(v * i for v in look.get("highlights", (0, 0, 0))),
        "fade": look.get("fade", 0.0) * i,
        "vignette": look.get("vignette", 0.0) * i,
    }


def filter_chain(g: Grade | None, pdir: Path | None = None) -> str | None:
    """Comma-joined FFmpeg filters for a grade, or None when it changes nothing."""
    if g is None:
        return None
    v = resolved(g)
    parts = []
    if abs(v["brightness"]) > 1e-3 or abs(v["contrast"] - 1) > 1e-3 or abs(v["saturation"] - 1) > 1e-3:
        parts.append(f"eq=brightness={_f(v['brightness'])}:contrast={_f(v['contrast'])}:saturation={_f(v['saturation'])}")
    if abs(v["temperature"]) > 1e-3 or abs(v["tint"]) > 1e-3:
        t, n = v["temperature"], v["tint"]
        parts.append(f"colorchannelmixer=rr={_f(1 + K_TEMP * t)}:gg={_f(1 - K_TINT * n)}:bb={_f(1 - K_TEMP * t)}")
    sh, hi = v["shadows"], v["highlights"]
    if any(abs(x) > 1e-3 for x in (*sh, *hi)):
        parts.append("colorbalance=" + ":".join(f"{n}={_f(x)}" for n, x in zip(("rs", "gs", "bs", "rh", "gh", "bh"), (*sh, *hi))))
    if v["fade"] > 1e-3:
        parts.append(f"curves=all='0/{_f(v['fade'])} 1/{_f(1 - v['fade'] / 3)}'")
    if g.lut:
        lut = resolve_media(pdir, g.lut) if pdir else Path(g.lut)
        if not lut.exists():
            raise VeditError(f"LUT file not found: {lut}")
        parts.append(f"lut3d=file={_escape_path(str(lut))}")
    if v["vignette"] > 1e-3:
        parts.append(f"vignette=angle={_f(0.25 + 0.5 * v['vignette'])}")
    return ",".join(parts) or None


def describe(g: Grade | None) -> str:
    if g is None:
        return "none"
    bits = []
    if g.matched_to:
        bits.append(f"matched to {g.matched_to}")
    adj = [f"{k} {getattr(g, k):+g}" for k in ("brightness", "temperature", "tint") if abs(getattr(g, k)) > 1e-3]
    adj += [f"{k} x{getattr(g, k):g}" for k in ("contrast", "saturation") if abs(getattr(g, k) - 1) > 1e-3]
    if adj:
        bits.append(", ".join(adj))
    if g.look:
        bits.append(f"look {g.look}" + (f" @{g.intensity:g}" if g.intensity < 1 else ""))
    if g.lut:
        bits.append(f"LUT {Path(g.lut).name}")
    return "; ".join(bits) or "neutral"


def consistency(project: Project, pdir: Path) -> dict:
    """How far apart the clips are in colour (lower = more consistent), before and after grading.
    Graded stats are estimated by applying the correction maths to the measured stats."""
    rows = []
    for c in project.clips:
        s = clip_stats(project, pdir, c)
        if s:
            rows.append((c, s))
    if len(rows) < 2:
        return {"clips": len(rows), "spread_before": 0.0, "spread_after": 0.0, "per_clip": {c.id: s for c, s in rows}}

    def spread(items):
        keys = ("luma", "sat", "warmth")
        arr = np.array([[s[k] for k in keys] for s in items])
        return _r(float(arr.std(axis=0).mean()), 4)

    graded = []
    for c, s in rows:
        g = c.grade
        if not g:
            graded.append(s)
            continue
        L = max(0.15, s["luma"])
        graded.append({"luma": (s["luma"] - 0.5) * g.contrast + 0.5 + g.brightness, "sat": s["sat"] * g.saturation,
                       "warmth": s["warmth"] + 2 * K_TEMP * L * g.temperature})
    return {"clips": len(rows), "spread_before": spread([s for _, s in rows]), "spread_after": spread(graded),
            "per_clip": {c.id: s for c, s in rows}}

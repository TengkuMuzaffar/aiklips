"""Audit an edit with code: hook, readability, safe zones, overlaps, typography, colour, pacing,
footage use, audio loudness and justification. No AI; every finding carries its evidence.

    report = audit(project, pdir)          # {"score", "passed", "counts", "findings": [...]}

A finding: {id, severity: must-fix | should-fix | note, category, at (timeline s or None),
            message, evidence, fix (a suggested vedit command or None)}.
`passed` means no must-fix findings. The AI reviewer (P2) and the chat Stop hook (P3) build on this.
See docs/design/auditor-justification-creativity.md.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable

from . import media
from .project import Layer, Project, VeditError, parse_pos

SEVERITY_COST = {"must-fix": 15, "should-fix": 6, "note": 1}
LOUDNESS = (-18.0, -10.0)
MAX_TRUE_PEAK = -0.5


def _finding(sev: str, cat: str, msg: str, at: float | None = None, evidence: str = "", fix: str | None = None) -> dict:
    return {"severity": sev, "category": cat, "at": None if at is None else round(at, 2), "message": msg,
            "evidence": evidence, "fix": fix}


def _end(layer: Layer, total: float) -> float:
    return total if layer.end is None else min(layer.end, total)


def _words(layer: Layer) -> list[str]:
    if layer.type == "captions":
        return [w.text for w in layer.words]
    return (layer.text or "").split()


# ---- text geometry ----------------------------------------------------------------------------

def text_box(p: Project, layer: Layer) -> tuple[float, float, float, float] | None:
    """Ink bounds of a text/captions layer at rest, as canvas fractions (x0, y0, x1, y1)."""
    from .ops.text import render_text

    if layer.type == "text":
        sample = layer.text or ""
    elif layer.type == "captions" and layer.words:
        n = max(1, layer.words_per_group)
        groups = [" ".join(w.text for w in layer.words[i:i + n]) for i in range(0, len(layer.words), n)]
        sample = max(groups, key=len)
    else:
        return None
    if not sample.strip():
        return None
    img = render_text(sample, layer.style)
    bb = img.getbbox()
    if not bb:
        return None
    W, H = p.canvas.width, p.canvas.height
    s = layer.scale
    cx, cy = parse_pos(layer.x, W), parse_pos(layer.y, H)
    x0 = cx + (bb[0] - img.width / 2) * s
    x1 = cx + (bb[2] - img.width / 2) * s
    y0 = cy + (bb[1] - img.height / 2) * s
    y1 = cy + (bb[3] - img.height / 2) * s
    return x0 / W, y0 / H, x1 / W, y1 / H


# ---- checks -----------------------------------------------------------------------------------

def check_hook(p: Project, pdir: Path) -> list[dict]:
    out = []
    if not p.clips:
        return out
    titles = [l.start for l in p.layers if l.type in ("text", "image") and l.start <= 1.0]
    first = p.clips[0].duration
    if not titles and first > 2.5:
        out.append(_finding("must-fix", "hook", "Nothing grabs attention in the first second: no title by 1.0 s and the "
                            "first shot runs for a long time.", 0.0, f"first title: none before 1.0 s; first shot {first:.1f}s",
                            'vedit text add "<hook line>" --start 0 --end 2.5 --y 22% --look viral-hook'))
    q = p.clips[0].meta.quality if p.clips[0].meta else None
    if q is not None and q <= 3:
        out.append(_finding("should-fix", "hook", f"The opening shot is only quality {q}/5. Open on the most striking shot.",
                            0.0, f"segment {p.clips[0].meta.segment}: quality {q}", f"vedit move <best clip> --to 0"))
    return out


def check_readability(p: Project, pdir: Path) -> list[dict]:
    out = []
    total = p.duration
    for l in p.layers:
        if l.type != "text":
            continue
        n = len(_words(l))
        shown = _end(l, total) - l.start
        need = 0.6 + 0.25 * n
        if n and shown < need:
            out.append(_finding("should-fix", "readability", f"{l.id} \"{(l.text or '')[:40]}\" is on screen {shown:.1f}s; "
                                f"{n} words need about {need:.1f}s to read.", l.start, f"{n} words / {shown:.2f}s",
                                f"vedit layer set {l.id} --end {l.start + need:.1f}"))
    return out


def check_layout(p: Project, pdir: Path) -> list[dict]:
    """Safe zone of the project's platform for every visible layer (text, captions, stickers, shapes, picture-in-
    picture; not full-frame backgrounds), measured by code (ops/safezone.py), and overlaps between text/captions on
    screen at the same time."""
    from .ops import safezone

    out = []
    total = p.duration
    z = safezone.zone(p)
    where = "the " + (z.name if z.key != "title" else "title-safe area") + (" safe zone" if z.key != "title" else "")
    for l in safezone.checked_layers(p):
        try:
            probs, worst, b = safezone.problems(p, pdir, l, z)
        except VeditError:
            continue
        if not probs:
            continue
        W, H = p.canvas.width, p.canvas.height
        off = b[0] < 0 or b[1] < 0 or b[2] > W or b[3] > H
        out.append(_finding("must-fix" if off or worst > 0.05 else "should-fix", "safe-zone",
                            f"{l.id} ({l.type}) is outside {where}{' and cut off' if off else ''}: " + "; ".join(probs) + ".",
                            l.start, f"box x {b[0]:.0f}-{b[2]:.0f}px, y {b[1]:.0f}-{b[3]:.0f}px; " + z.describe(W, H),
                            f"vedit safe fix {l.id}"))
    boxes: list[tuple[Layer, tuple]] = []
    for l in p.layers:
        if l.type not in ("text", "captions") or l.hidden:
            continue
        try:
            box = text_box(p, l)
        except VeditError:
            continue
        if box is not None:
            boxes.append((l, box))
    for i, (a, ba) in enumerate(boxes):
        for b, bb in boxes[i + 1:]:
            t0, t1 = max(a.start, b.start), min(_end(a, total), _end(b, total))
            if t1 - t0 < 0.1:
                continue
            if ba[0] < bb[2] and bb[0] < ba[2] and ba[1] < bb[3] and bb[1] < ba[3]:
                out.append(_finding("must-fix", "overlap", f"{a.id} and {b.id} overlap on screen from {t0:.1f}s to {t1:.1f}s.",
                                    t0, f"boxes intersect: {a.id} y {ba[1]:.0%}-{ba[3]:.0%}, {b.id} y {bb[1]:.0%}-{bb[3]:.0%}",
                                    f"vedit layer set {b.id} --y {'22%' if bb[1] > 0.5 else '62%'}"))
    return out


def check_typography(p: Project, pdir: Path) -> list[dict]:
    from .ops import fonts

    out = []
    texts = [l for l in p.layers if l.type == "text"]
    used = sorted({l.style.font or fonts.DEFAULT_FONT for l in texts})
    if len(used) > 2:
        out.append(_finding("should-fix", "typography", f"{len(used)} fonts across titles ({', '.join(used)}). Use one title "
                            "font plus at most one accent.", None, ", ".join(f"{l.id}: {l.style.font or 'default'}" for l in texts),
                            "vedit text look apply <look>"))
    looks = sorted({l.style.look for l in texts if l.style.look})
    if len(looks) > 1:
        out.append(_finding("note", "typography", f"Titles mix text looks: {', '.join(looks)}.", None,
                            ", ".join(f"{l.id}: {l.style.look}" for l in texts if l.style.look), f"vedit text look apply {looks[0]}"))
    return out


def check_color(p: Project, pdir: Path) -> list[dict]:
    from .ops import color

    out = []
    video = [c for c in p.clips if p.sources[c.source].has_video and not _plain(p, c)]
    if len(video) < 2:
        return out
    looks = {(c.grade.look if c.grade else None) for c in video}
    if len(looks) > 1:
        out.append(_finding("should-fix", "colour", "Clips have different colour looks: "
                            + ", ".join(sorted(str(x or "none") for x in looks)) + ". One look reads as one video.", None,
                            ", ".join(f"{c.id}: {c.grade.look if c.grade and c.grade.look else 'none'}" for c in video),
                            "vedit color look <name>"))
    try:
        cons = color.consistency(p, pdir)
    except (VeditError, OSError):
        return out
    matched = any(c.grade and c.grade.matched_to for c in video)
    spread = cons.get("spread_after") if matched else cons.get("spread_before")
    if spread is not None and spread > 0.05:
        out.append(_finding("should-fix", "colour", f"Shots don't match in colour (spread {spread:.3f}; under 0.05 reads as "
                            "one shoot).", None, f"measured spread {cons.get('spread_before')} -> {cons.get('spread_after')}",
                            "vedit color match" if not matched else "vedit color match --strength 1"))
    for cid, s in (cons.get("per_clip") or {}).items():
        if s["luma"] < 0.04:
            at = p.clip_starts()[[c.id for c in p.clips].index(cid)]
            out.append(_finding("must-fix", "footage", f"Clip {cid} is almost black.", at, f"mean brightness {s['luma']:.3f}",
                                f"vedit remove-clip {cid}"))
    return out


def _plain(p: Project, c) -> bool:
    """A gap, or a Design background clip (the canvas-colour file played as a clip): no footage to judge."""
    return c.gap or Path(p.sources[c.source].path).name.startswith(".gap-")


def check_pacing(p: Project, pdir: Path) -> list[dict]:
    out = []
    target = p.notes.target_shot_s if p.notes else None
    limit = max(1.5, 3 * target) if target else 6.0
    for c, start in zip(p.clips, p.clip_starts()):
        if c.duration > limit and not _plain(p, c):
            out.append(_finding("note", "pacing", f"Clip {c.id} holds for {c.duration:.1f}s"
                                + (f", over 3x the {target:g}s target shot." if target else "."), start,
                                f"{c.duration:.2f}s > {limit:.1f}s", f"vedit split --at {start + c.duration / 2:.2f}"))
    return out


def check_footage(p: Project, pdir: Path) -> list[dict]:
    out = []
    starts = p.clip_starts()
    for i, a in enumerate(p.clips):
        for j in range(i + 1, len(p.clips)):
            b = p.clips[j]
            if a.source != b.source or _plain(p, a):
                continue
            ov = min(a.out, b.out) - max(a.in_, b.in_)
            if ov > 0.3:
                out.append(_finding("should-fix", "footage", f"Clips {a.id} and {b.id} reuse the same footage "
                                    f"({max(a.in_, b.in_):.1f}-{min(a.out, b.out):.1f}s of {a.source}).", starts[j],
                                    f"{ov:.1f}s shared", f"vedit remove-clip {b.id}"))
    return out


def check_masks(p: Project, pdir: Path) -> list[dict]:
    """Every tracked mask, checked like `vedit mask check` (motion jumps, SAM re-outline on sample frames). Results are
    cached per mask setup, so an unchanged mask costs nothing on the next audit."""
    import hashlib
    import json

    from .ops import maskcheck, sam
    from .ops.effects import cache_dir
    from .render.runner import preview_dir

    out = []
    starts = p.clip_starts()
    for c, s0 in zip(p.clips, starts):
        for e in c.effects:
            if e.type != "mask" or not e.params.get("track", True):
                continue
            mid = e.params.get("id")
            verify = sam.installed()
            key = hashlib.sha1(json.dumps([c.source, c.in_, c.out, c.speed, s0, e.params, verify, maskcheck.VERSION],
                                          sort_keys=True, default=str).encode()).hexdigest()[:16]
            cache = cache_dir(pdir) / f"maskcheck-{key}.json"
            if cache.exists():
                r = json.loads(cache.read_text())
            else:
                r = maskcheck.check(pdir, p, c, e, preview_dir(pdir) / f"maskcheck-{mid}.jpg", lambda m: None, verify)
                cache.write_text(json.dumps(r))
            if r["verdict"] == "good":
                continue
            times = ", ".join(f"{it['t']:.2f}s" for it in r["issues"][:4])
            first = r["issues"][0]["t"] if r["issues"] else None
            out.append(_finding("must-fix" if r["verdict"] == "poor" else "should-fix", "mask",
                                f"Mask {mid} doesn't hold on the object" + (f" at {times}" if times else "")
                                + (f" ({r['issues'][0]['detail']})" if r["issues"] else "") + ".", first,
                                f"verdict {r['verdict']}; covers the object {r['agreement'].get('cover_min')} at worst",
                                f"vedit mask check {mid} (Read its sheet), then vedit mask fix {mid} --at <first wrong time> "
                                f"--space source --box <where it really is>"))
    return out


_I = re.compile(r"^\s*I:\s*(-?[\d.]+|-inf)\s*LUFS", re.M)
_PEAK = re.compile(r"^\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS", re.M)


def loudness(path: Path) -> tuple[float | None, float | None]:
    """(integrated LUFS, true peak dBTP) of a rendered file, via ffmpeg ebur128."""
    proc = subprocess.run([media.ffmpeg_bin(), "-hide_banner", "-nostats", "-nostdin", "-i", str(path), "-vn",
                           "-filter_complex", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    summary = proc.stderr[proc.stderr.rfind("Summary:"):] if "Summary:" in proc.stderr else proc.stderr

    def num(rx):
        m = rx.search(summary)
        if not m:
            return None
        return float("-inf") if m.group(1) == "-inf" else float(m.group(1))

    return num(_I), num(_PEAK)


def check_audio(p: Project, pdir: Path, render: bool = True) -> list[dict]:
    if not render or not p.clips:
        return []
    from .render.runner import render_preview

    try:
        res = render_preview(p, pdir, progress=False)
    except VeditError as e:
        return [_finding("must-fix", "render", f"The edit does not render: {e}")]
    lufs, peak = loudness(Path(res["output"]))
    out = []
    if lufs is None:
        return out
    if lufs < -40:
        out.append(_finding("note", "audio", "The edit is (almost) silent.", None, f"{lufs:.1f} LUFS",
                            "vedit music add <song> --volume 0.3"))
    elif not LOUDNESS[0] <= lufs <= LOUDNESS[1]:
        loud = lufs > LOUDNESS[1]
        out.append(_finding("should-fix", "audio", f"Overall loudness {lufs:.1f} LUFS is too {'loud' if loud else 'quiet'} "
                            "for social apps (aim for about -14).", None, f"integrated {lufs:.1f} LUFS",
                            "vedit volume 0.7" if loud else "vedit volume 1.4"))
    if peak is not None and peak > MAX_TRUE_PEAK and lufs > -40:
        out.append(_finding("should-fix", "audio", f"Audio peaks at {peak:.1f} dBFS and will distort on phones.", None,
                            f"true peak {peak:.1f} dBTP", "vedit volume 0.8"))
    return out


def check_justification(p: Project, pdir: Path) -> list[dict]:
    out = []
    beats = p.notes.beats if p.notes else []
    starts = dict(zip([c.id for c in p.clips], p.clip_starts()))
    if not beats:
        return out
    for c in p.clips:
        if not _plain(p, c) and not (c.meta and c.meta.why):
            out.append(_finding("note", "justification", f"Clip {c.id} has no stated reason.", starts[c.id], "",
                                f'vedit why {c.id} "<what it shows and which beat it serves>" --beat <id>'))
    used = {c.meta.beat for c in p.clips if c.meta and c.meta.beat}
    for b in beats:
        if b.id not in used:
            out.append(_finding("should-fix", "justification", f"Beat {b.id} ({b.role}: {b.purpose}) has no clip on the timeline.",
                                None, "", f"vedit why <clip> \"...\" --beat {b.id}"))
    return out


def check_sound(p: Project, pdir: Path) -> list[dict]:
    """Footage sound vs music: see sound.py."""
    from . import sound

    return sound.check_sound(p, pdir)


def check_motion(p: Project, pdir: Path) -> list[dict]:
    """Design videos (motion graphics on a plain background): a stretch of 3s+ where nothing moves looks frozen."""
    from .ops import animate as anim

    if not p.clips or not all(_plain(p, c) for c in p.clips):
        return []

    def moving(t: float) -> bool:
        for l in p.layers:
            end = p.duration if l.end is None else l.end
            if l.hidden or not (l.start <= t < end):
                continue
            if l.type == "video" or l.keyframes:
                return True
            for pr in l.presets:
                phase = anim.PRESETS.get(pr.name, ("loop",))[0]
                d = anim._preset_duration(l, pr) if pr.name in anim.PRESETS else 0
                if phase == "loop" or (phase == "in" and t - l.start < d) or (phase == "out" and end - t < d):
                    return True
        return False

    out, run_start, t, step = [], None, 0.0, 0.25
    while t <= p.duration:
        if moving(t) or t >= p.duration - step:
            if run_start is not None and t - run_start >= 3.0:
                out.append(_finding("should-fix", "motion", f"Nothing moves from {run_start:.1f}s to {t:.1f}s.", run_start,
                                    f"{t - run_start:.1f}s with no animated layer",
                                    "give a layer there a loop preset (bob, pulse, float) or a keyframe path"))
            run_start = None
        elif run_start is None:
            run_start = t
        t = round(t + step, 3)
    return out


CHECKS: list[tuple[str, Callable]] = [
    ("hook", check_hook), ("readability", check_readability), ("layout", check_layout), ("typography", check_typography),
    ("colour", check_color), ("pacing", check_pacing), ("footage", check_footage), ("justification", check_justification),
    ("sound", check_sound), ("motion", check_motion),
]


def audit(p: Project, pdir: Path, render: bool = True) -> dict:
    findings: list[dict] = []
    for name, fn in CHECKS:
        try:
            findings += fn(p, pdir)
        except Exception as e:  # one broken check must not hide the others
            findings.append(_finding("note", "audit", f"Check '{name}' could not run: {e}"))
    if render:  # masks are tracked by the preview render; without it there's nothing to check yet
        try:
            findings += check_masks(p, pdir)
        except Exception as e:
            findings.append(_finding("note", "audit", f"Check 'masks' could not run: {e}"))
    try:
        findings += check_audio(p, pdir, render)
    except Exception as e:
        findings.append(_finding("note", "audit", f"Check 'audio' could not run: {e}"))
    order = {"must-fix": 0, "should-fix": 1, "note": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["at"] if f["at"] is not None else 1e9))
    for i, f in enumerate(findings, 1):
        f["id"] = f"F{i}"
    counts = {k: sum(1 for f in findings if f["severity"] == k) for k in order}
    score = max(0, 100 - sum(SEVERITY_COST[f["severity"]] for f in findings))
    return {"score": score, "passed": counts["must-fix"] == 0, "counts": counts, "findings": findings}

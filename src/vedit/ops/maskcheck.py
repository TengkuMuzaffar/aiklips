"""Check a tracked mask: how the object moves, where the mask looks wrong, and a picture to see it.

`vedit mask check` runs this after a mask is tracked (it tracks it first if needed). The AI editor reads the result
before saying a mask is done, and repairs bad moments with `vedit mask fix` instead of redoing the whole mask.

1. Motion profile (from the tracked outline, every frame): how much the object grows / shrinks, turns, changes
   shape (an object turning shows another side), how fast it moves, when it touches the frame edge, and when it
   isn't visible (hidden, or gone: left the shot / the scene cut).
2. Suspicious frames: the outline suddenly jumping in size or place, and, when SAM is installed, an independent
   re-outline of the object on sample frames that doesn't agree with the tracked one (a mask drifting onto the
   background or shrinking to part of the object).
3. A check sheet (JPG): up to 12 source frames with the tracked outline drawn on (green = fine, red = flagged,
   yellow = SAM's own outline where it disagrees) and a 10% grid, so boxes for fixes can be read off it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from ..project import Clip, Effect, Project, resolve_media
from . import sam
from .effects import proc_size, require, source_dims

VERSION = "maskcheck-2"  # part of the audit's cache key
SHEET_COLS = 4
SHEET_MAX = 12
CHECKS = 10  # frames re-outlined by SAM to verify the track
AGREE = 0.72  # IoU below which SAM's outline "disagrees" with the tracked one
COVER = 0.88  # share of SAM's outline of the object the mask must cover (less: part of it shows)
LOOSE = 0.45  # IoU below which a mask that does cover the object is flagged as loose (minor)
LOOKALIKE = 0.2  # colour similarity to the marked frame below which the mask is probably on something else


def _mask_of(cv2, polys, w: int, h: int) -> np.ndarray:
    from .outline import poly_mask

    if not polys:
        return np.zeros((h, w), bool)
    return poly_mask(cv2, [np.asarray(p, np.float32) * [w, h] for p in polys], w, h) > 127


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 1.0


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    out, k = [], 0
    while k < len(flags):
        if flags[k]:
            j = k
            while j + 1 < len(flags) and flags[j + 1]:
                j += 1
            out.append((k, j))
            k = j + 1
        else:
            k += 1
    return out


def check(pdir: Path, project: Project, clip: Clip, eff: Effect, out: Path, log: Callable[[str], None],
          verify: bool = True) -> dict:
    from .track import _frames, track

    cv2 = require("cv2", "ai")
    p = eff.params
    data = track(pdir, project, clip.source, p, log)
    src = project.sources[clip.source]
    path = resolve_media(pdir, src.path)
    w, h, fps = source_dims(src)
    start_tl = project.clip_starts()[project.clips.index(clip)]
    a, b = p.get("range") or [clip.in_, clip.out]
    a, b = max(a, clip.in_), min(b, clip.out)
    fa, fb = int(a * fps), max(int(a * fps) + 1, int(b * fps))
    tl = lambda i: round(start_tl + (i / fps - clip.in_) / max(clip.speed, 1e-6), 2)  # noqa: E731
    ol = data.get("outline")
    how = "segment" if data.get("tracker") == "segment" else ("outline" if ol else "box")

    def polys_at(i: int):
        if not ol:  # box tracker: the box is the region
            bx, by, bw, bh = data["boxes"][min(i, len(data["boxes"]) - 1)]
            return [np.float32([[bx, by], [bx + bw, by], [bx + bw, by + bh], [bx, by + bh]])]
        k = i - ol["start"]
        return ol["polys"][k] if 0 <= k < len(ol["polys"]) else None

    # ---- 1. motion profile from the outline, every frame (small raster) ----
    aw, ah = proc_size(w, h, 320)
    idx = list(range(fa, fb))
    vis, area, cen, ang, asp, edge = [], [], [], [], [], []
    for i in idx:
        pl = polys_at(i)
        m = _mask_of(cv2, pl, aw, ah) if pl else None
        if m is None or m.sum() < 3:
            vis.append(False)
            area.append(0.0)
            cen.append(None)
            ang.append(None)
            asp.append(None)
            edge.append(False)
            continue
        ys, xs = np.nonzero(m)
        vis.append(True)
        area.append(float(m.sum()) / (aw * ah))
        cen.append(np.array([xs.mean() / aw, ys.mean() / ah]))
        (_, _), (rw, rh), an = cv2.minAreaRect(np.column_stack([xs, ys]).astype(np.float32))
        if rw < rh:
            an, rw, rh = an + 90, rh, rw
        ang.append(float(an % 180))
        asp.append(float(rw / max(rh, 1e-6)))
        edge.append(bool(m[:, :2].any() or m[:, -2:].any() or m[:2].any() or m[-2:].any()))
    seen = [k for k, v in enumerate(vis) if v]
    f0 = min(max(int(round(float(p.get("at", a)) * fps)), fa), fb - 1) - fa
    ref = f0 if vis[f0] else (seen[0] if seen else 0)
    issues: list[dict] = []
    motion: dict = {}
    if seen:
        sc = np.array([area[k] / max(area[ref], 1e-9) for k in seen])
        # turning in the picture plane: only measurable on elongated shapes (a near-square's angle flips by 90)
        a0 = ang[ref]
        turns = [abs(((ang[k] - a0) + 90) % 180 - 90) for k in seen if asp[k] > 1.3 and asp[ref] > 1.3] or [0.0]
        aspc = [abs(np.log(asp[k] / max(asp[ref], 1e-6))) for k in seen]
        steps = [float(np.linalg.norm(cen[seen[j]] - cen[seen[j - 1]])) * fps / max(1, seen[j] - seen[j - 1])
                 for j in range(1, len(seen))]
        motion = {"scale_min": round(float(sc.min()), 2), "scale_max": round(float(sc.max()), 2),
                  "rotation_deg": round(float(max(turns)), 1), "shape_change": round(float(np.exp(max(aspc)) - 1), 2),
                  "max_speed": round(max(steps, default=0.0), 2), "edge_share": round(sum(edge) / len(seen), 2),
                  "visible_share": round(len(seen) / len(idx), 3)}
        # sudden jumps (a mask snapping to something else): size or position, well beyond its normal frame-to-frame motion
        med = float(np.median(steps)) if steps else 0.0
        for j in range(1, len(seen)):
            k0, k1 = seen[j - 1], seen[j]
            if k1 - k0 > 2 or edge[k0] or edge[k1]:  # at the frame edge, size and place change as it slides in / out
                continue
            r = area[k1] / max(area[k0], 1e-9)
            mv = float(np.linalg.norm(cen[k1] - cen[k0]))
            if r > 1.45 or r < 0.69:
                issues.append({"frame": idx[k1], "kind": "size jump", "detail": f"area x{r:.2f} in one frame"})
            elif mv * fps > max(4 * med, 0.6) and mv > 0.04:
                issues.append({"frame": idx[k1], "kind": "position jump", "detail": f"moved {mv:.0%} of the frame in one frame"})
    # where it isn't visible
    hidden = []
    for k0, k1 in _runs([not v for v in vis]):
        why = ("not in the shot yet / tracking starts later" if k0 == 0 else
               "gone: left the shot, scene cut or lost" if k1 == len(vis) - 1 else "hidden (something in front) or lost")
        hidden.append({"from": tl(idx[k0]), "to": tl(idx[k1] + 1), "why": why})

    # ---- 2. independent check by SAM on sample frames ----
    checked: list[dict] = []
    frames: dict[int, np.ndarray] = {}
    vw, vh = proc_size(w, h, 640)

    def frame(i: int) -> np.ndarray | None:
        if i not in frames:
            frames[i] = next(_frames(path, vw, vh, fps, i / fps, 1.5 / fps), None)
        return frames[i]

    def look(f: np.ndarray, m: np.ndarray) -> np.ndarray | None:
        """Colour fingerprint of what's under the mask (hue x saturation), to compare with the marked frame."""
        if m.sum() < 30:
            return None
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], m.astype(np.uint8), [18, 8], [0, 180, 0, 256])
        return cv2.normalize(hist, hist).flatten()

    picks = sorted({seen[int(round(x))] for x in np.linspace(0, len(seen) - 1, min(CHECKS, len(seen)))} |
                   {i - fa for i in (x["frame"] for x in issues[:4])}) if seen else []
    f_ref = frame(idx[ref]) if seen else None
    ref_look = look(f_ref, _mask_of(cv2, polys_at(idx[ref]), vw, vh)) if f_ref is not None else None
    use_sam = verify and sam.installed() and p.get("shape") != "paint"
    for k in picks:
        if not vis[k]:
            continue
        f = frame(idx[k])
        if f is None:
            continue
        tm = _mask_of(cv2, polys_at(idx[k]), vw, vh)
        ys, xs = np.nonzero(tm)
        if not len(xs):
            continue
        # does it still look like the thing that was marked? (a mask left on an empty background, or on another thing)
        lk = look(f, tm)
        if ref_look is not None and lk is not None and abs(k - ref) > 2:
            sim = float(cv2.compareHist(ref_look, lk, cv2.HISTCMP_CORREL))
            if sim < LOOKALIKE:
                issues.append({"frame": idx[k], "kind": "looks different",
                               "detail": f"what's under the mask looks unlike the marked object (similarity {sim:.2f}): "
                                         "it may be on the background or another thing, or the object has left"})
        if not use_sam:
            continue
        px, py = 0.08 * (xs.max() - xs.min()) + 2, 0.08 * (ys.max() - ys.min()) + 2
        box = (max(0, xs.min() - px), max(0, ys.min() - py), min(vw - 1, xs.max() + px), min(vh - 1, ys.max() + py))
        try:
            sm, score = sam.segment(f, box)
        except Exception as e:  # noqa: BLE001
            log(f"  SAM check skipped: {e}")
            use_sam = False
            continue
        iou = _iou(tm, sm)
        cover = float((tm & sm).sum() / max(1, sm.sum()))  # how much of the object the mask covers: what hiding needs
        checked.append({"frame": idx[k], "iou": round(iou, 3), "cover": round(cover, 3), "sam": sm})
        if score <= 0.7:
            continue
        if cover < COVER and iou < AGREE:
            issues.append({"frame": idx[k], "kind": "misses part",
                           "detail": f"{1 - cover:.0%} of the object (SAM's own outline) is outside the mask: it can show"})
        elif iou < LOOSE:
            issues.append({"frame": idx[k], "kind": "loose",
                           "detail": f"covers the object, but {1 - iou:.0%} of the mask is around it (a 'remove' fill "
                                     "samples less of the real surface)"})
    # merge issues on nearby frames, keep the worst few
    issues.sort(key=lambda x: x["frame"])
    merged: list[dict] = []
    for it in issues:
        if merged and it["frame"] - merged[-1]["frame"] < fps * 0.3:
            if it["kind"] in ("misses part", "looks different") and merged[-1]["kind"] not in ("misses part", "looks different"):
                merged[-1] = it
            continue
        merged.append(it)
    issues = merged
    for it in issues:
        i = it["frame"]
        it["t"] = tl(i)
        bx = data["boxes"][min(i, len(data["boxes"]) - 1)]
        it["box_source"] = f"{bx[0] * 100:.1f}%,{bx[1] * 100:.1f}%,{bx[2] * 100:.1f}%,{bx[3] * 100:.1f}%"
    agree = [c["iou"] for c in checked]
    cover = [c["cover"] for c in checked]

    # ---- 3. the check sheet ----
    must = {it["frame"] for it in issues[:6]} | {idx[ref]}
    even = {idx[int(round(x))] for x in np.linspace(0, len(idx) - 1, SHEET_MAX)}
    show = sorted(must | {e for e in even if all(abs(e - m) > 0.25 * fps for m in must)})
    while len(show) > SHEET_MAX:  # thin the evenly spaced ones, never the flagged / marked frames
        keep = {it["frame"] for it in issues[:6]} | {idx[ref]}
        cand = [s for s in show if s not in keep]
        if not cand:
            break
        show.remove(cand[len(cand) // 2])
    flagged = {it["frame"]: it for it in issues}
    sam_at = {c["frame"]: c for c in checked}
    tw = 300
    th = int(round(tw * vh / vw))
    tiles = []
    for i in show:
        f = frame(i)
        if f is None:
            continue
        img = cv2.resize(f, (tw, th), interpolation=cv2.INTER_AREA)
        for g in range(1, 10):  # a 10% grid to read boxes off
            col = (90, 90, 90) if g != 5 else (140, 140, 140)
            cv2.line(img, (int(tw * g / 10), 0), (int(tw * g / 10), th), col, 1)
            cv2.line(img, (0, int(th * g / 10)), (tw, int(th * g / 10)), col, 1)
        bad = i in flagged and flagged[i]["kind"] != "loose"
        pl = polys_at(i)
        if pl:
            pts = [np.round(np.asarray(q, np.float32) * [tw, th]).astype(np.int32) for q in pl]
            over = img.copy()
            cv2.fillPoly(over, pts, (40, 40, 220) if bad else (60, 200, 60))
            img = cv2.addWeighted(over, 0.28, img, 0.72, 0)
            cv2.polylines(img, pts, True, (40, 40, 255) if bad else (80, 255, 80), 2)
        c = sam_at.get(i)
        if c is not None and (c["iou"] < AGREE or c["cover"] < COVER):
            sm = cv2.resize(c["sam"].astype(np.uint8), (tw, th), interpolation=cv2.INTER_NEAREST)
            cs, _ = cv2.findContours(sm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.polylines(img, cs, True, (0, 220, 255), 1)
        cap = f"{tl(i):.2f}s" + ("  MARKED" if i == idx[ref] else "") + (f"  {flagged[i]['kind']}" if i in flagged else "")
        if not pl:
            cap += "  (no mask)"
        bar = np.full((22, tw, 3), 24, np.uint8)
        cv2.putText(bar, cap, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (90, 90, 255) if bad else (230, 230, 230), 1, cv2.LINE_AA)
        tiles.append(np.vstack([bar, img]))
    if tiles:
        rows = []
        for r in range(0, len(tiles), SHEET_COLS):
            row = tiles[r:r + SHEET_COLS]
            row += [np.zeros_like(tiles[0])] * (SHEET_COLS - len(row))
            rows.append(np.hstack([np.pad(t, ((2, 2), (2, 2), (0, 0))) for t in row]))
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])

    # ---- verdict and advice ----
    mid = p.get("id")
    advice = []
    worst = min(cover, default=1.0)
    serious = [it for it in issues if it["kind"] != "loose"]
    verdict = ("good" if not serious and worst >= 0.9 else
               "check" if len(serious) <= 1 and worst >= 0.75 else "poor")
    if how == "outline" and motion and (motion["rotation_deg"] > 25 or motion["shape_change"] > 0.35 or motion["edge_share"] > 0.15):
        advice.append(f"The object turns / changes shape / enters from the edge: re-track it with SAM 2 video: "
                      f"vedit mask set {mid} --tracker segment")
    if how in ("outline", "box") and serious and sam.installed():
        advice.append(f"Try the segment tracker first (vedit mask set {mid} --tracker segment), then check again")
    for it in [i for i in issues if i["kind"] != "loose"][:5]:
        advice.append(f"At {it['t']:.2f}s ({it['kind']}): look at that tile. If the outline is wrong there, draw the object's "
                      f"box on the sheet grid (source %, x,y,w,h) and run: vedit mask fix {mid} --at {it['t']:.2f} "
                      f"--space source --box x,y,w,h   (tracked box there: {it['box_source']})")
    for hd in hidden:
        if "hidden" in hd["why"]:
            advice.append(f"No mask {hd['from']:.2f}-{hd['to']:.2f}s ({hd['why']}). If the object is still visible there, "
                          f"fix it: vedit mask fix {mid} --at <a time in that span> --space source --box x,y,w,h")
    if any(it["kind"] == "loose" for it in issues) and not serious:
        advice.append(f"The mask covers the object but is loose in places: fine for blur / fill; for 'remove', a tighter "
                      f"start helps (mask find, then re-add with the exact candidate box)")
    if not advice:
        advice.append("Looks right. Still look at the sheet once: green outlines should hug the object in every tile.")
    return {"mask": mid, "clip": clip.id, "tracker": how, "range": [tl(fa), tl(fb)], "frames": len(idx),
            "marked_at": tl(idx[ref]), "motion": motion, "hidden": hidden,
            "issues": [{k: v for k, v in it.items() if k != "frame"} for it in issues],
            "agreement": {"checked": len(agree), "mean": round(float(np.mean(agree)), 3) if agree else None,
                          "min": round(min(agree), 3) if agree else None,
                          "cover_mean": round(float(np.mean(cover)), 3) if cover else None,
                          "cover_min": round(worst, 3) if cover else None},
            "fixes": len(p.get("fixes") or []), "verdict": verdict, "advice": advice, "sheet": str(out) if tiles else None}

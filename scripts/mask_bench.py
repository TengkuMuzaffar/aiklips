"""Mask tracking benchmark with exact ground truth: a textured label moves over a busy background with rotation,
scale, perspective, motion blur and an occluder. Scores the box, outline and segment (SAM 2 video) trackers as mean IoU.

    uv run python scripts/mask_bench.py [--label image.png] [--seconds 6] [--hard]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

W, H, FPS = 540, 960, 30
TRACKS = [("box", "off"), ("outline", "off"), ("outline", "on"), ("segment", "on")]  # (tracker, sam)


def make_clip(out: Path, label: np.ndarray, seconds: float, hard: bool, seed: int = 3) -> list[np.ndarray]:
    """Write the clip; returns the ground-truth label quad (4x2 px) per frame."""
    rng = np.random.default_rng(seed)
    bg = (rng.integers(0, 255, (H // 8, W // 8, 3))).astype(np.uint8)
    bg = cv2.resize(bg, (W * 2, H * 2), interpolation=cv2.INTER_CUBIC)
    bg = cv2.GaussianBlur(bg, (0, 0), 2)
    lh, lw = label.shape[:2]
    src = np.float32([[0, 0], [lw, 0], [lw, lh], [0, lh]])
    n = int(seconds * FPS)
    quads = []
    proc = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out)],
                            stdin=subprocess.PIPE)
    prev = None
    for i in range(n):
        t = i / n
        ang = (35 if hard else 15) * np.sin(2 * np.pi * t)
        sc = 1.0 + (0.35 if hard else 0.15) * np.sin(2 * np.pi * t * 1.5)
        cx = W / 2 + (150 if hard else 80) * np.sin(2 * np.pi * t * 0.8)
        cy = H / 2 + (220 if hard else 100) * np.sin(2 * np.pi * t * 1.1)
        tilt = (0.35 if hard else 0.15) * np.sin(2 * np.pi * t * 0.7)  # perspective: one side shrinks
        hw, hh = 170 * sc, 170 * sc * lh / lw
        q = np.float32([[-hw, -hh * (1 - tilt)], [hw, -hh * (1 + tilt)], [hw, hh * (1 + tilt)], [-hw, hh * (1 - tilt)]])
        R = np.float32([[np.cos(np.radians(ang)), -np.sin(np.radians(ang))], [np.sin(np.radians(ang)), np.cos(np.radians(ang))]])
        q = (q @ R.T + [cx, cy]).astype(np.float32)
        # the background pans too (a handheld camera)
        ox, oy = int(W / 2 + 120 * np.sin(2 * np.pi * t * 0.5)), int(H / 2 + 80 * np.cos(2 * np.pi * t * 0.6))
        frame = bg[oy:oy + H, ox:ox + W].copy()
        M = cv2.getPerspectiveTransform(src, q)
        warped = cv2.warpPerspective(label, M, (W, H))
        m = cv2.warpPerspective(np.full((lh, lw), 255, np.uint8), M, (W, H))
        frame[m > 127] = warped[m > 127]
        if hard and 0.55 < t < 0.7:  # a hand passes over part of the label
            x = int(cx - 220 + (t - 0.55) / 0.15 * 300)
            cv2.rectangle(frame, (x, int(cy - 60)), (x + 90, H), (60, 90, 160), -1)
        if prev is not None and hard:  # motion blur on fast moves
            frame = cv2.addWeighted(frame, 0.65, prev, 0.35, 0)
        prev = frame
        proc.stdin.write(frame.tobytes())
        quads.append(q)
    proc.stdin.close()
    proc.wait()
    return quads


def make_scene(out: Path, label: np.ndarray, seconds: float, scene: str, seed: int = 3) -> list[list[np.ndarray]]:
    """Harder real-life scenes; returns the ground-truth quads (one per label) per frame.

    corner: the camera orbits a box corner. One label on each face; the faces turn in opposite directions.
    edge:   the label starts half outside the picture and the camera pans until it's fully in view."""
    rng = np.random.default_rng(seed)
    bg = cv2.GaussianBlur(cv2.resize(rng.integers(0, 255, (H // 8, W // 8, 3)).astype(np.uint8), (W * 2, H * 2),
                                     interpolation=cv2.INTER_CUBIC), (0, 0), 2)
    lh, lw = label.shape[:2]
    src = np.float32([[0, 0], [lw, 0], [lw, lh], [0, lh]])
    n = int(seconds * FPS)
    proc = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out)],
                            stdin=subprocess.PIPE)
    truth = []
    for i in range(n):
        t = i / max(1, n - 1)
        frame = bg[H // 2:H // 2 + H, W // 2:W // 2 + W].copy()
        quads = []
        if scene == "corner":
            cx, cy, ch = W / 2, H * 0.62, 150
            turn = 0.15 + 0.7 * t  # 0..1: how much of the left face we see vs the right face
            lw_, rw_ = 260 * (1 - turn) + 30, 260 * turn + 30
            # left face: its far edge recedes (shorter) as it turns away; right face the other way
            left = np.float32([[cx - lw_, cy - ch * (0.75 + 0.25 * turn)], [cx, cy - ch], [cx, cy + ch], [cx - lw_, cy + ch * (0.75 + 0.25 * turn)]])
            right = np.float32([[cx, cy - ch], [cx + rw_, cy - ch * (1 - 0.25 * turn)], [cx + rw_, cy + ch * (1 - 0.25 * turn)], [cx, cy + ch]])
            cv2.fillPoly(frame, [left.astype(np.int32)], (90, 150, 190))
            cv2.fillPoly(frame, [right.astype(np.int32)], (110, 170, 205))
            for face in (left, right):  # a label in the middle of each face
                c = face.mean(0)
                q = (c + (face - c) * 0.6).astype(np.float32)
                M = cv2.getPerspectiveTransform(src, q)
                warped = cv2.warpPerspective(label, M, (W, H))
                m = cv2.warpPerspective(np.full((lh, lw), 255, np.uint8), M, (W, H))
                frame[m > 127] = warped[m > 127]
                quads.append(q)
        else:  # edge
            x = -150 + 250 * min(1.0, t * 1.4)
            q = np.float32([[x, 300], [x + 300, 300], [x + 300, 470], [x, 470]])
            M = cv2.getPerspectiveTransform(src, q)
            warped = cv2.warpPerspective(label, M, (W, H))
            m = cv2.warpPerspective(np.full((lh, lw), 255, np.uint8), M, (W, H))
            frame[m > 127] = warped[m > 127]
            quads.append(q)
        proc.stdin.write(frame.tobytes())
        truth.append(quads)
    proc.stdin.close()
    proc.wait()
    return truth


def score_scene(cli, project: Path, truth: list[list[np.ndarray]], tracker: str, sam: str, split: bool = True) -> dict:
    """Mask each label with its own box joined by ';' (as the AI does), score the union against the truth."""
    from vedit.ops import track as tr
    from vedit.project import load

    boxes = []
    for q in truth[0]:
        x0, y0 = np.clip(q.min(0), 0, [W - 1, H - 1])
        x1, y1 = np.clip(q.max(0), 0, [W - 1, H - 1])
        boxes.append(f"{x0 / W * 100:.3f}%,{y0 / H * 100:.3f}%,{(x1 - x0) / W * 100:.3f}%,{(y1 - y0) / H * 100:.3f}%")
    cli("mask", "remove", "all")
    cli("mask", "add", "--at", "0", "--box", ";".join(boxes), "--style", "fill", "--feather", "0", "--expand", "1.0",
        "--tracker", tracker, "--sam", sam)
    p = load(project)
    e = next(e for c in p.clips for e in c.effects if e.type == "mask")
    c = next(c for c in p.clips if e in c.effects)
    data = tr.track(project, p, c.source, e.params, lambda s: None)
    ious = []
    for i, qs in enumerate(truth):
        m = tr.mask_at(cv2, {**e.params, "feather": 0, "expand": 1.0}, data, i, W, H, "mask") > 127
        gt = np.zeros((H, W), np.uint8)
        cv2.fillPoly(gt, [np.round(q).astype(np.int32) for q in qs], 255)
        gt = gt > 127
        u = (m | gt).sum()
        ious.append((m & gt).sum() / u if u else 1.0)
    a = np.array(ious)
    return {"tracker": tracker, "sam": sam, "mean_iou": round(float(a.mean()), 3), "p10_iou": round(float(np.percentile(a, 10)), 3),
            "frames_over_0.8": round(float((a > 0.8).mean()), 3), "how": data.get("how"), "sam_calls": data.get("sam_calls"),
            "iou_by_second": [round(float(a[k:k + FPS].mean()), 3) for k in range(0, len(a), FPS)]}


def score(cli, project: Path, quads: list[np.ndarray], tracker: str, sam: str, loose: float = 0.0) -> dict:
    """Add a mask from the first frame's ground-truth box, track it, and compare with the truth on every frame."""
    from vedit.ops import track as tr
    from vedit.project import load

    x0, y0 = quads[0].min(0)
    x1, y1 = quads[0].max(0)
    px, py = loose * (x1 - x0), loose * (y1 - y0)  # a box drawn roughly around the object, as people do
    x0, y0, x1, y1 = max(0, x0 - px), max(0, y0 - py), min(W - 1, x1 + px), min(H - 1, y1 + py)
    box = f"{x0 / W * 100:.3f}%,{y0 / H * 100:.3f}%,{(x1 - x0) / W * 100:.3f}%,{(y1 - y0) / H * 100:.3f}%"
    cli("mask", "remove", "all")
    cli("mask", "add", "--at", "0", "--box", box, "--space", "source", "--style", "fill", "--feather", "0", "--expand", "1.0",
        "--tracker", tracker, "--sam", sam)
    p = load(project)
    e = next(e for c in p.clips for e in c.effects if e.type == "mask")
    c = next(c for c in p.clips if e in c.effects)
    data = tr.track(project, p, c.source, e.params, lambda s: None)
    ious = []
    for i, q in enumerate(quads):
        if i >= len(data["boxes"]):
            break
        m = tr.mask_at(cv2, {**e.params, "feather": 0, "expand": 1.0}, data, i, W, H, "mask") > 127
        gt = np.zeros((H, W), np.uint8)
        cv2.fillPoly(gt, [np.round(q).astype(np.int32)], 255)
        gt = gt > 127
        u = (m | gt).sum()
        ious.append((m & gt).sum() / u if u else 1.0)
    a = np.array(ious)
    return {"tracker": tracker, "sam": sam, "loose": loose, "mean_iou": round(float(a.mean()), 3), "p10_iou": round(float(np.percentile(a, 10)), 3),
            "frames_over_0.8": round(float((a > 0.8).mean()), 3), "how": data.get("how"), "sam_calls": data.get("sam_calls"),
            "anchors": data.get("anchors"), "iou_first": round(float(a[0]), 3),
            "iou_by_second": [round(float(a[k:k + FPS].mean()), 3) for k in range(0, len(a), FPS)]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", type=Path)
    ap.add_argument("--seconds", type=float, default=6)
    ap.add_argument("--hard", action="store_true")
    ap.add_argument("--scene", choices=["single", "corner", "edge"], default="single")
    ap.add_argument("--only", nargs="*", help="trackers to score (box outline segment); default all")
    ap.add_argument("--loose", type=float, default=0.0, help="draw the starting box this much too big on each side (0.12)")
    a = ap.parse_args()
    if a.label:
        label = cv2.imread(str(a.label))
    else:
        rng = np.random.default_rng(1)
        label = np.full((180, 320, 3), 230, np.uint8)
        for _ in range(40):
            cv2.putText(label, "".join(rng.choice(list("HONDA0123456789")) for _ in range(5)),
                        (int(rng.integers(0, 260)), int(rng.integers(20, 180))), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 200), 2)
    d = Path(tempfile.mkdtemp(prefix="mask-bench-"))
    # a throwaway library: the benchmark's projects must never show up in the user's Recent projects
    os.environ["VEDIT_HOME"] = str(d / "home")
    os.environ["VEDIT_PROJECTS_DIR"] = str(d / "projects")
    clip = d / "bench.mp4"
    if a.scene != "single":
        truth = make_scene(clip, label, a.seconds, a.scene)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    project = d / "proj"

    def cli(*args):
        r = subprocess.run([sys.executable, "-m", "vedit", "--json", "-p", str(project), *args], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(r.stdout + r.stderr)
        return json.loads(r.stdout or "{}")

    subprocess.run([sys.executable, "-m", "vedit", "init", str(project)], check=True, capture_output=True)
    if a.scene != "single":
        cli("import", str(clip))
        from vedit.ops import sam as _sam

        for tracker, sm in TRACKS:
            if (sm == "off" or _sam.installed()) and (not a.only or tracker in a.only):
                print(json.dumps(score_scene(cli, project, truth, tracker, sm)), flush=True)
        return
    quads = make_clip(clip, label, a.seconds, a.hard)
    cli("import", str(clip))
    from vedit.ops import sam

    for tracker, sm in TRACKS:
        if (sm == "off" or sam.installed()) and (not a.only or tracker in a.only):
            print(json.dumps(score(cli, project, quads, tracker, sm, a.loose)), flush=True)


if __name__ == "__main__":
    main()

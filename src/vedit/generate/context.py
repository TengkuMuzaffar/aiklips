"""What the AI is told about the user's media, in layers, within a budget.

L0  situation: the whole set summed up by code (how much usable footage vs the target length, where the sound
    comes from, speech or not, images only...) and the recipe that follows from it. Code decides the situation, so
    no prompt has to guess it.
L1  one line per file from the media library's digest (what it is, usable seconds, energy, speech, transcript).
L2  a shortlist of segments, ranked by code (AI quality score, fit to the roles this variation needs, reviewer marks)
    within a line budget across all files, instead of every segment of every file.

The full shot log is still what validate_plan / fill_ranges check against; this only shapes the prompt.
"""

from __future__ import annotations

from ..sound import SPEECH

PLAN_LINES = 120     # segment lines for the editor (plan_edit / revise / patch)
CREATIVE_LINES = 36  # the creative director only needs the strongest shots
MIN_PER_FILE = 3     # every file keeps a few lines, so nothing the user added is invisible

RECIPES = {
    "image_motion": "Only still images: build a motion-led edit (moves on the images, kinetic text, scene cards); "
                    "never hold a still without movement.",
    "speech_led": "Someone speaks in the footage: the speech carries the story. Cut on sentence ends, never mid-word, "
                  "keep the speaking clips' sound, put b-roll over the talking, music low under it.",
    "scarce_footage": "Not enough usable footage for the length: reuse the best shots with a different crop / speed / "
                      "zoom rather than padding with weak ones, and lean on text and graphic moments.",
    "abundant_pick_best": "Far more footage than needed: be picky. Use only the strongest segments, no two similar "
                          "shots in a row.",
    "balanced": "Enough footage for the length: follow the reference's pacing and use the best segment for each beat.",
}
SOUND_PLANS = {
    "music_each": "Several sound files: each variation has its own (named in its directives).",
    "music_file": "Cut to the uploaded sound's beats.",
    "clip_sound": "The footage's own sound is the soundtrack; any music sits under it.",
    "make_music": "No sound uploaded: a music loop is made for the edit.",
}


def _speech(seg: dict) -> bool:
    m = seg.get("measured") or {}
    return bool(seg.get("talking")) or (m.get("voice") or 0) >= SPEECH


def _usable(seg: dict) -> bool:
    m = seg.get("measured") or {}
    return seg.get("quality", 3) >= 3 and m.get("luma", 0.5) >= 0.08


def situation(log: dict, length_s: float) -> dict:
    """L0, by code: the set of media summed up, and the recipe it calls for."""
    videos = {fid: f for fid, f in log.items() if f["kind"] == "video"}
    images = [fid for fid, f in log.items() if f["kind"] == "image"]
    sounds = [fid for fid, f in log.items() if f["kind"] == "audio"]
    segs = [s for f in videos.values() for s in f.get("segments") or []]
    usable_s = round(sum(s["to"] - s["from"] for s in segs if _usable(s)), 1)
    speech_files = [fid for fid, f in videos.items() if any(_speech(s) for s in f.get("segments") or [])]
    if not videos:
        recipe = "image_motion"
    elif speech_files:
        recipe = "speech_led"
    elif usable_s < length_s * 0.8:
        recipe = "scarce_footage"
    elif usable_s > length_s * 4:
        recipe = "abundant_pick_best"
    else:
        recipe = "balanced"
    sound = ("music_each" if len(sounds) > 1 else "music_file" if sounds
             else "clip_sound" if speech_files else "make_music")
    return {"videos": len(videos), "images": len(images), "sounds": len(sounds), "segments": len(segs),
            "usable_s": usable_s, "target_s": length_s, "speech_files": speech_files, "recipe": recipe, "sound": sound}


def situation_text(sit: dict) -> str:
    bits = [f"{sit['videos']} video(s)"]
    if sit["images"]:
        bits.append(f"{sit['images']} image(s)")
    if sit["sounds"]:
        bits.append(f"{sit['sounds']} sound file(s)")
    head = (f"SITUATION (measured by code): {', '.join(bits)}; {sit['usable_s']}s of usable footage for a "
            f"{sit['target_s']:.0f}s video" + (f"; speech in {', '.join(sit['speech_files'])}" if sit["speech_files"] else "") + ".")
    return f"{head}\nRECIPE {sit['recipe']}: {RECIPES[sit['recipe']]}\nSOUND: {SOUND_PLANS[sit['sound']]}"


def _file_line(fid: str, f: dict) -> str:
    dur = f"{float(f['duration']):.1f}s" if f.get("duration") else ""
    if f["kind"] == "audio":
        extra = ", ".join(x for x in [f"{f['bpm']:.0f} bpm" if f.get("bpm") else "", f"{f.get('beats', 0)} beats",
                                      "taken from a video" if f.get("taken_from") else ""] if x)
        return f"{fid} {f['name']!r}: sound, {dur}, {extra}"
    if f["kind"] == "image":
        return f"{fid} {f['name']!r}: image {f.get('size', '')}"
    dg = f.get("digest") or {}
    facts = [dur, dg.get("orientation", ""), f"{dg['usable_s']}s usable" if "usable_s" in dg else "",
             f"energy {dg['energy']}" if dg.get("energy") else "", "speech" if dg.get("speech") else "",
             "own sound" if f.get("has_audio") else "silent"]
    line = f"{fid} {f['name']!r}: video, {', '.join(x for x in facts if x)}"
    if dg.get("subjects"):
        line += f"; subjects: {', '.join(dg['subjects'])}"
    if dg.get("summary"):
        line += f"; best: {dg['summary']}"
    if dg.get("transcript"):
        line += f"\n  says: “{dg['transcript'][:240]}”"
    return line


def _score(seg: dict, roles: set[str]) -> float:
    s = 2.0 * seg.get("quality", 3)
    if roles and roles & set(seg.get("good_for") or []):
        s += 2.5
    m = seg.get("marks") or {}
    s -= 1.5 * sum((m.get("weak") or {}).values())
    s += 0.5 * sum((m.get("good") or {}).values())
    if not _usable(seg):
        s -= 4
    return s


def shortlist(log: dict, budget: int, roles: list[str] | None = None) -> dict[str, list[dict]]:
    """L2: the best segments per file within `budget` lines in total, each file's picks in time order."""
    want = {r.lower() for r in roles or []}
    files = {fid: f.get("segments") or [] for fid, f in log.items() if f["kind"] == "video"}
    picked: dict[str, list[dict]] = {fid: [] for fid in files}
    ranked = {fid: sorted(segs, key=lambda s: -_score(s, want)) for fid, segs in files.items()}
    left = budget
    for fid, segs in ranked.items():  # a floor for every file
        take = segs[:MIN_PER_FILE]
        picked[fid] += take
        left -= len(take)
    rest = sorted(((s, fid) for fid, segs in ranked.items() for s in segs[MIN_PER_FILE:]), key=lambda x: -_score(x[0], want))
    for s, fid in rest[:max(0, left)]:
        picked[fid].append(s)
    return {fid: sorted(v, key=lambda s: s["from"]) for fid, v in picked.items()}


def footage_block(log: dict, length_s: float, budget: int = PLAN_LINES, roles: list[str] | None = None) -> str:
    """L0 + L1 + L2 as prompt text. Segment lines keep the `f1.s3 (0.0-2.1s, q4): ...` form the editor cites."""
    from .planner import _shot_line

    sit = situation(log, length_s)
    picks = shortlist(log, budget, roles)
    out = [situation_text(sit), "", "FILES:"]
    for fid, f in log.items():
        out.append(_file_line(fid, f))
        if f["kind"] == "video":
            segs = f.get("segments") or []
            shown = picks.get(fid) or []
            if len(shown) < len(segs):
                out.append(f"  segments (best {len(shown)} of {len(segs)}; other ids exist but are weaker):")
            out += [f"  {_shot_line(s)}" for s in shown]
    return "\n".join(out)

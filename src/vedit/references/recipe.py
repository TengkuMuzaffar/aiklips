"""Recipe: how to rebuild a reference's edit with vedit, as an ordered list of real commands.

Built by code from the Style DNA (plus the measured metrics), so it costs no AI call and every
command is one vedit actually has. Values the user must fill in are in <angle brackets>
(<clip>, <song>, "<HOOK>"). What the reference does that vedit can't do exactly comes from the
AI study (`StyleDNA.gaps`), each with the closest command.

    steps = build(dna, metrics, ref_id=3)
    [{"area": "Colour", "command": "vedit color match --ref 3", "why": "..."}, ...]
"""

from __future__ import annotations

import shlex

from .dna import StyleDNA

TEXT_SIZE = {"small": 56, "medium": 72, "large": 92, "huge": 118}  # same scale as generate/planner.py
OUT_DUR = 0.3


def _q(s: str) -> str:
    return s if s.startswith("<") and s.endswith(">") else shlex.quote(s)


def _canvas(m: dict) -> str | None:
    w, h = m.get("width"), m.get("height")
    if not w or not h:
        return None
    r = w / h
    if r < 0.66:
        return "reels"
    if r < 0.9:
        return "portrait45"
    if r <= 1.1:
        return "square"
    return "landscape"


def build(dna: StyleDNA, m: dict | None = None, ref_id: int | None = None) -> list[dict]:
    m = m or {}
    d = dna
    steps: list[dict] = []
    ref = str(ref_id) if ref_id is not None else "<ref id>"

    def add(area: str, cmd: str, why: str, *, placeholder: bool = False) -> None:
        steps.append({"area": area, "command": cmd, "why": why, "fill_in": placeholder or "<" in cmd})

    # 1. canvas
    preset = _canvas(m)
    if preset:
        add("Canvas", f"vedit canvas --preset {preset}", f"The reference is {m['width']}×{m['height']}.")

    # 2. cutting / pacing
    p = d.pacing
    if d.sound.voiceover:
        add("Cutting", "vedit silence-cut --dry-run", "It's a talking video: check what silence-cut would remove first.")
        add("Cutting", "vedit silence-cut", "Tight speech with no pauses, like the reference.")
    beat = (60.0 / d.sound.bpm) if d.sound.bpm else None
    if d.sound.cut_on_beat and beat:
        n = max(1, round(p.avg_shot_s / beat))
        add("Cutting", "vedit split --at <beat time>",
            f"Cuts land on the beat ({d.sound.bpm:g} BPM): cut every {n} beat{'s' if n > 1 else ''} (~{n * beat:.2f}s per shot). "
            f"`vedit gen run --ref {ref}` snaps cuts to the beat for you.", placeholder=True)
    else:
        add("Cutting", "vedit keep --from <A> --to <B>",
            f"Keep each shot around {p.avg_shot_s:g}s ({p.cuts_per_10s:g} cuts per 10s, {p.rhythm} rhythm); "
            f"first cut at {d.hook.first_cut_s:g}s." if d.hook.first_cut_s else
            f"Keep each shot around {p.avg_shot_s:g}s ({p.cuts_per_10s:g} cuts per 10s, {p.rhythm} rhythm).", placeholder=True)
    if p.speed_ramps:
        add("Cutting", "vedit fx apply speed_ramp --clip <clip>", "The reference speeds into some shots.")

    # 3. framing / motion
    mo = d.motion
    if mo.jumpcut_zoom:
        add("Motion", f"vedit jumpcut-zoom --zoom {mo.jumpcut_zoom:g}", "Alternate shots are punched in, so jump cuts look intentional.")
    if mo.ken_burns:
        add("Motion", "vedit zoom 1.0 --to 1.08", "Slow push-in on the shots (Ken Burns).")
    if mo.zoom_punch_on_beat:
        add("Motion", "vedit fx add zoom_pulse", "The picture pulses in on the beat.")
    if mo.shake:
        add("Motion", "vedit fx add shake --clip <clip> --start <S> --end <E>", "Camera shake on the high-energy moments.")

    # 4. colour
    add("Colour", f"vedit color match --ref {ref} --strength 0.8", "Move your footage toward the reference's measured colour.")
    if d.color.look != "natural":
        add("Colour", f"vedit color look {d.color.look} --intensity {d.color.intensity:g}",
            f"The grade: {d.color_feel or d.color.look}.")

    # 5. titles (one look for all of them)
    t = d.text
    if t.present:
        y = t.position_y or "25%"
        parts = [f'vedit text add "<HOOK>" --start 0 --end {d.hook.duration_s:g} --y {_q(y)} --size {TEXT_SIZE[t.size]}',
                 f"--color {_q(t.color)}"]
        if t.font:
            parts.append(f"--font {t.font}")
        if t.stroke:
            parts.append("--stroke '#000000'")
        if t.bg_box:
            parts.append(f"--bg {_q(t.bg_color or '#000000B0')}")
        if t.uppercase:
            parts.append("--uppercase")
        if t.letter_spacing:
            parts.append(f"--letter-spacing {t.letter_spacing:g}")
        parts += [f"--preset {x}" for x in t.animation_in] + [f"--preset {x}" for x in t.loop]
        parts += [f"--preset {x}:{OUT_DUR:g}" for x in t.animation_out]
        hook = f"{d.hook.pattern.replace('_', ' ')} hook, {d.hook.duration_s:g}s" + (f" (theirs: “{d.hook.example}”)" if d.hook.example else "")
        add("Titles", " ".join(parts), f"{hook}. Write your own line, about {t.words_per_title} words.")
        add("Titles", f"vedit text look save ref-{ref} --from <text layer id>",
            f"Save it as a look, then `vedit text look apply ref-{ref} --layer <id>` keeps every title the same.")
        if t.on_beat and beat:
            add("Titles", "vedit layer set <text layer id> --start <beat time>", "Titles appear exactly on beats.")

    # 6. signature moves
    for sm in d.signature_moves[:2]:
        from ..ops.moves import MOVES

        needs = MOVES[sm.move][0]
        if needs.startswith("at"):
            cmd = f"vedit fx apply {sm.move} --at {sm.at:g}" if sm.at is not None else f"vedit fx apply {sm.move} --at <T>"
        else:
            cmd = f"vedit fx apply {sm.move} --clip <clip>"
        if "+text" in needs:
            cmd += ' --text "<WORD>"'
        seen = f" (seen at {sm.at:g}s in the reference)" if sm.at is not None else ""
        add("Moves", cmd, (sm.note or sm.move.replace("_", " ")) + seen + (" ✓ measured" if sm.confirmed else ""))

    # 7. transitions
    tr = d.transitions
    if tr.default != "none" and tr.frequency in ("often", "every_cut"):
        add("Transitions", f"vedit transition {tr.default} --duration {tr.duration_s:g}", f"{tr.default} on {'every cut' if tr.frequency == 'every_cut' else 'most cuts'}.")
    elif tr.default != "none" and tr.frequency == "rare":
        add("Transitions", f"vedit transition {tr.default} --clip <clip> --duration {tr.duration_s:g}", "Only on a few cuts; the rest are hard cuts.")
    for a in tr.accents[:2]:
        add("Transitions", f"vedit transition {a} --clip <clip> --duration {tr.duration_s:g}", "An accent transition at a key change.")

    # 8. sound
    s = d.sound
    if s.music:
        vol = 0.2 if s.voiceover else {"low": 0.5, "medium": 0.7, "high": 0.85}[s.music_energy]
        duck = "--duck" if (s.ducking or s.voiceover) else "--no-duck"
        add("Sound", f"vedit music add <song> --volume {vol:g} {duck}",
            f"{s.music_energy}-energy music" + (f" around {s.bpm:g} BPM" if s.bpm else "") + (", ducked under the voice" if s.voiceover else "") + ".")
    if s.voiceover:
        add("Sound", "vedit sound voice clean", "Clear voice-over, like the reference.")
    else:
        add("Sound", "vedit sound auto", "Mute b-roll noise so the music carries it.")

    # 9. captions last
    c = d.captions
    if c.present:
        add("Captions", f"vedit captions auto --style {c.style} --y {_q(c.y)} --words {c.words_at_once}",
            "Captions go last, after every cut and speed change.")

    # 10. check
    add("Check", "vedit audit", "Fix the must-fix findings before calling it done.")
    return steps


def gaps(dna: StyleDNA) -> list[dict]:
    return [g.model_dump() for g in dna.gaps]


def as_text(steps: list[dict], gap_list: list[dict]) -> str:
    lines, area = [], None
    for i, s in enumerate(steps, 1):
        if s["area"] != area:
            area = s["area"]
            lines.append(f"\n{area}")
        lines.append(f"  {i:>2}. {s['command']}\n      {s['why']}")
    if gap_list:
        lines.append("\nNot exactly possible in vedit (closest instead)")
        for g in gap_list:
            lines.append(f"  - {g['what']}" + (f"\n      closest: {g['closest']}" if g.get("closest") else ""))
    return "\n".join(lines).lstrip("\n")

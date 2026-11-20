"""Brief tags: what the AI should know about a generation, as clickable choices instead of free text.

One registry for everything that uses them:
  - the wizard's Brief step (chips; served by /api/generate/options),
  - the CLI (`vedit gen run --funnel --audience --pace --tone --rule`),
  - the prompts (`describe()` turns the picks into plain lines every planner / editor / auditor prompt gets),
  - code (`pace_target()` sets the shot length, `funnel_bias()` the hook types, `rules` the hard directives).

Each option has a `label` (the chip), a `hint` (the chip's tooltip) and a `prompt` (what the AI is told).
"""

from __future__ import annotations

from typing import Any

# ---- funnel stage ----------------------------------------------------------------------------

FUNNEL: dict[str, dict[str, Any]] = {
    "awareness": {
        "label": "Awareness", "hint": "Top of funnel: people who don't know you yet",
        "prompt": "Top of funnel (cold audience who doesn't know the brand). Stop the scroll and entertain or teach; "
                  "no hard sell, the brand is visible but light. Soft CTA (follow / save / share).",
        "hooks": ["pov", "question", "pain_point"], "cta": "Follow for more",
    },
    "consideration": {
        "label": "Consideration", "hint": "They know you and are comparing options",
        "prompt": "Middle of funnel (they know the brand and are comparing). Demo it, prove it, show before / after, "
                  "answer the main objection, show how it works. CTA: learn more / see it in action.",
        "hooks": ["before_after", "pain_point", "stat"], "structures": ["before_after", "problem_solution"], "cta": "See how it works",
    },
    "conversion": {
        "label": "Conversion", "hint": "Ready to buy: offer, price, urgency",
        "prompt": "Bottom of funnel (ready to buy). Lead with the offer, show the price / deal and the product on screen "
                  "within the first 3 seconds, add social proof and urgency, end on ONE clear hard CTA.",
        "hooks": ["bold_claim", "stat"], "cta": "Shop now", "needs_cta": True,
    },
    "retention": {
        "label": "Retention", "hint": "Existing customers: tips, community, upsell",
        "prompt": "Existing customers. Tips and how-to-use, new features, community and loyalty, a gentle upsell; "
                  "warm tone, they already trust the brand.",
        "hooks": ["question", "pov"], "structures": ["text_led"], "cta": "Save this for later",
    },
}

# ---- target audience ---------------------------------------------------------------------------

AUDIENCE: dict[str, dict[str, Any]] = {
    "age": {"label": "Age", "multi": True, "options": {
        "teens": "Teens 13-17", "gen_z": "Gen Z 18-24", "young_adults": "Young adults 25-34",
        "age_35_44": "35-44", "age_45_54": "45-54", "age_55": "55+"}},
    "who": {"label": "Who they are", "multi": True, "options": {
        "students": "Students", "parents": "Parents", "professionals": "Working professionals",
        "business_owners": "Business owners / SMEs", "creators": "Creators", "gamers": "Gamers", "fitness": "Fitness",
        "beauty": "Beauty & skincare", "foodies": "Foodies", "travellers": "Travellers", "tech": "Tech enthusiasts",
        "shoppers": "Shoppers / deal hunters"}},
    "region": {"label": "Region", "multi": True, "options": {
        "malaysia": "Malaysia", "indonesia": "Indonesia", "singapore": "Singapore", "global": "Global"}},
    "awareness": {"label": "What they know", "multi": False, "options": {
        "unaware": "Doesn't know the problem", "problem_aware": "Knows the problem",
        "solution_aware": "Knows the solutions", "product_aware": "Knows your product"}},
}
AWARENESS_PROMPT = {
    "unaware": "they don't know they have the problem yet: show the problem first, make it relatable",
    "problem_aware": "they feel the problem but don't know the fix: agitate it, then reveal the fix",
    "solution_aware": "they know fixes exist: show why this one is better",
    "product_aware": "they know the product: skip explaining it, give them a reason to act now",
}
REGION_PROMPT = {
    "malaysia": "Malaysian viewers (local slang and RM prices where natural)",
    "indonesia": "Indonesian viewers (local slang and Rp prices where natural)",
    "singapore": "Singaporean viewers (S$ prices where natural)",
    "global": "an international audience (no local slang)",
}

# ---- pace ----------------------------------------------------------------------------------------
# avg = target average shot length (s); curve = hook / middle / end shot lengths for shaped paces

PACE: dict[str, dict[str, Any]] = {
    "reference": {"label": "Follow the reference", "hint": "The shot length the reference uses", "avg": None},
    "very_slow": {"label": "Very slow", "hint": "~3.5 s shots: calm, ASMR, luxury, cinematic", "avg": 3.5},
    "slow": {"label": "Slow", "hint": "~2.4 s shots: storytelling, tutorials", "avg": 2.4},
    "medium": {"label": "Medium", "hint": "~1.6 s shots: most promos", "avg": 1.6},
    "fast": {"label": "Fast", "hint": "~1.1 s shots: TikTok energy", "avg": 1.1},
    "very_fast": {"label": "Very fast", "hint": "~0.6 s shots: hype montage, drops, sales", "avg": 0.6},
    "build": {"label": "Build up", "hint": "Slow start that speeds up to the payoff", "avg": 1.4, "curve": [2.4, 1.4, 0.7]},
    "pulse": {"label": "Fast hook, calm middle, fast end", "hint": "Retention curve: 0.7 s / 1.8 s / 0.9 s", "avg": 1.3,
              "curve": [0.7, 1.8, 0.9]},
}
CURVE_PARTS = ("hook (first ~20%)", "middle", "end (last ~20%)")

# ---- tone, must do / avoid ---------------------------------------------------------------------

TONE: dict[str, str] = {
    "playful": "Playful", "premium": "Premium", "emotional": "Emotional", "funny": "Funny", "urgent": "Urgent",
    "calm": "Calm", "educational": "Educational", "bold": "Bold", "wholesome": "Wholesome", "raw": "Raw / authentic (UGC)",
}
MAX_TONE = 3

RULES: dict[str, dict[str, str]] = {
    "price_early": {"label": "Show price early", "prompt": "show the price / offer on screen within the first 3 seconds"},
    "logo_end": {"label": "Logo at the end", "prompt": "end on the brand logo (an image the user gave, or the brand name as text)"},
    "product_first_2s": {"label": "Product in first 2 s", "prompt": "the product must be clearly visible within the first 2 seconds"},
    "no_faces": {"label": "No faces", "prompt": "don't use shots where a face is the subject"},
    "captions_always": {"label": "Captions always on", "prompt": "keep captions on for all speech"},
    "no_music": {"label": "No music", "prompt": "no music track: voice and natural sound only"},
    "voiceover_room": {"label": "Room for voiceover", "prompt": "leave room for a voiceover: little on-screen text, calmer music, mute clip sound"},
    "no_text_first_1s": {"label": "No text in first 1 s", "prompt": "no on-screen text in the first second: let the picture hook"},
    "brand_colours": {"label": "Keep brand colours", "prompt": "titles and cards use the brand's colours (from the reference palette or the user's images)"},
}


def options() -> dict:
    """The registry as the wizard draws it: groups of chips."""
    return {
        "funnel": [{"id": k, "label": v["label"], "hint": v["hint"]} for k, v in FUNNEL.items()],
        "audience": [{"id": g, "label": v["label"], "multi": v["multi"],
                      "options": [{"id": k, "label": lab} for k, lab in v["options"].items()]} for g, v in AUDIENCE.items()],
        "pace": [{"id": k, "label": v["label"], "hint": v["hint"]} for k, v in PACE.items()],
        "tone": [{"id": k, "label": v} for k, v in TONE.items()],
        "max_tone": MAX_TONE,
        "rules": [{"id": k, "label": v["label"], "hint": v["prompt"]} for k, v in RULES.items()],
    }


def audience_ids() -> dict[str, str]:
    """{option id: group} for every audience chip (the CLI takes them as one flat list)."""
    return {k: g for g, v in AUDIENCE.items() for k in v["options"]}


def check(brief) -> None:
    """Raise ValueError for an id that isn't in the registry (Brief's validator)."""
    bad: list[str] = []
    if brief.funnel and brief.funnel not in FUNNEL:
        bad.append(f"funnel {brief.funnel!r}")
    if brief.pace not in PACE:
        bad.append(f"pace {brief.pace!r}")
    for g in ("age", "who", "region"):
        bad += [f"{g} {x!r}" for x in getattr(brief.audience, g) if x not in AUDIENCE[g]["options"]]
    if brief.audience.awareness and brief.audience.awareness not in AUDIENCE["awareness"]["options"]:
        bad.append(f"awareness {brief.audience.awareness!r}")
    bad += [f"tone {x!r}" for x in brief.tone if x not in TONE]
    bad += [f"rule {x!r}" for x in brief.rules if x not in RULES]
    if bad:
        raise ValueError("unknown " + ", ".join(bad))
    if len(brief.tone) > MAX_TONE:
        raise ValueError(f"pick at most {MAX_TONE} tones")


def pace_target(brief, reference_avg: float) -> tuple[float, list[float] | None]:
    """(average shot length, per-section curve or None) the brief asks for; the reference's when it says so."""
    p = PACE.get(brief.pace) or PACE["reference"]
    if p["avg"] is None:
        return reference_avg, None
    return float(p["avg"]), (list(p["curve"]) if p.get("curve") else None)


def funnel_bias(brief) -> dict:
    """What the funnel stage changes in code: preferred hook types / structures and a default CTA."""
    f = FUNNEL.get(brief.funnel or "")
    return {"hooks": f.get("hooks", []), "structures": f.get("structures", []), "cta": f.get("cta", "")} if f else {}


def describe(brief) -> str:
    """The picks as plain lines for the prompts (empty when nothing is picked)."""
    lines = []
    f = FUNNEL.get(brief.funnel or "")
    if f:
        lines.append(f"FUNNEL: {f['label']}: {f['prompt']}")
    a = brief.audience
    who = [AUDIENCE["age"]["options"][x] for x in a.age] + [AUDIENCE["who"]["options"][x] for x in a.who] + list(a.custom)
    where = [REGION_PROMPT[x] for x in a.region]
    if who or where or a.awareness:
        parts = [", ".join(who)] if who else []
        parts += where
        if a.awareness:
            parts.append(AWARENESS_PROMPT[a.awareness])
        lines.append("AUDIENCE: " + "; ".join(parts) + ". Speak to them: their words, their problems, their references.")
    p = PACE.get(brief.pace)
    if p and p["avg"] is not None:
        if p.get("curve"):
            lines.append(f"PACE: {p['label']}: shots of " + ", ".join(f"~{s}s in the {n}" for s, n in zip(p["curve"], CURVE_PARTS)) + ".")
        else:
            lines.append(f"PACE: {p['label']}: shots of ~{p['avg']}s on average ({p['hint'].split(': ', 1)[-1]}).")
    if brief.tone:
        lines.append("TONE: " + ", ".join(TONE[t] for t in brief.tone) + ".")
    if brief.rules:
        lines.append("MUST: " + "; ".join(RULES[r]["prompt"] for r in brief.rules) + ".")
    return "\n".join(lines)


def short(brief) -> str:
    """One line for the edit's notes (what the auditor reads)."""
    bits = []
    if brief.funnel:
        bits.append(f"funnel {FUNNEL[brief.funnel]['label'].lower()}")
    a = brief.audience
    who = [AUDIENCE["age"]["options"][x] for x in a.age] + [AUDIENCE["who"]["options"][x] for x in a.who] \
        + list(a.custom) + [AUDIENCE["region"]["options"][x] for x in a.region]
    if who:
        bits.append("for " + ", ".join(who))
    if brief.pace != "reference":
        bits.append(f"pace {PACE[brief.pace]['label'].lower()}")
    if brief.tone:
        bits.append("tone " + ", ".join(TONE[t].lower() for t in brief.tone))
    if brief.rules:
        bits.append("must: " + "; ".join(RULES[r]["prompt"] for r in brief.rules))
    return " | ".join(bits)


def prompt_block(brief) -> str:
    """BRIEF section for a prompt: the brief's fields as JSON plus the tag lines."""
    raw = brief.model_dump(exclude={"funnel", "audience", "pace", "tone", "rules"})
    import json

    text = describe(brief)
    return json.dumps(raw, indent=1) + (f"\n{text}" if text else "")

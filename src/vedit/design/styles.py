"""Motion styles: a look written as numbers (easing curves, timing, palette, type, what is allowed and what never
happens), so the AI can hit it on purpose and the audit can check it (`vedit style`, `audit.check_style`).

Everything here is made with our own tracks: shapes (incl. paths, draw-on, morph), text (incl. kinetic split and
counters), charts, presets, keyframes, filters and textures. Styles that need real 3D or painted textures
(isometric worlds, toy 3D, watercolour, hand-drawn line boil) are listed in PLANNED until their Python renderers exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..project import VeditError


@dataclass(frozen=True)
class Style:
    key: str
    name: str
    use_for: str
    ease: str  # the one easing everything uses (keyframes, kinetic text pieces, draw-on)
    enter: tuple[str, ...]  # entrance presets that belong to the style
    forbid: tuple[str, ...]  # presets that break it
    transitions: tuple[str, ...]  # allowed scene transitions
    split: str  # default kinetic text: none | word | letter | line
    split_anim: str
    stagger: float  # seconds between kinetic text pieces
    enter_s: float  # how long entrances take
    hold_per_3_words: float  # minimum seconds on screen per 3 words of text
    scene_s: tuple[float, float]  # scene length range
    background: str
    text: str
    accents: tuple[str, ...]
    max_colors: int  # distinct colours in shapes / text / charts (background excluded)
    fonts: tuple[str, ...]  # bundled font keys (`vedit fonts`); the first is for titles
    filters: tuple[str, ...] = ()  # look filters for the whole video (`fx add <f> --layer adj` on an adjustment layer)
    rules: tuple[str, ...] = field(default_factory=tuple)  # the numbers in words, for the AI

    def card(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


STYLES: dict[str, Style] = {s.key: s for s in [
    Style("swiss", "Swiss grid & kinetic type", "brand identity, event openers, conference titles, bold statements",
          "snap", ("wipe_in", "slide_in", "grow_in", "stretch_in"), ("fade_in", "fade_out", "pop_in", "bob", "float", "wiggle", "drop_in"),
          ("cut", "wipe", "slide"), "letter", "mask_up", 0.03, 0.45, 0.9, (1.2, 2.5), "#F2F0EA", "#111111", ("#FF3B1F",), 2,
          ("archivo-black", "space-mono"), (),
          ("12-column grid: x positions on multiples of 1/12 of the canvas, left-aligned text, nothing centred by default",
           "one signal colour (#FF3B1F) used on at most one element per scene; everything else black on off-white",
           "everything snaps: cubic-bezier(.7,0,.2,1) ('snap') for every move, 0.35-0.5 s, wipes and masks, never fades",
           "titles split by letter with mask_up, 0.03 s stagger; rules / bars grow_in on the same curve",
           "hard cuts or wipes between scenes on the beat; 1.2-2.5 s per scene")),
    Style("flat-explainer", "Flat vector explainer", "SaaS promos, product explainers, science and how-it-works videos",
          "overshoot", ("pop_in", "slide_in", "rise_in", "stretch_in"), ("shake", "fade_in"), ("slide", "pop", "cut"),
          "word", "rise", 0.08, 0.5, 1.0, (2.5, 4.5), "#1E1B4B", "#FFFFFF", ("#22D3EE", "#F472B6", "#FBBF24"), 4,
          ("poppins-black", "poppins"), (),
          ("flat shapes, no outlines or 6 px outlines in one colour; rounded corners 24-48 px",
           "entrances overshoot ('overshoot' / back_out) 0.4-0.55 s, groups stagger 0.08 s left to right",
           "one idea per scene, 2.5-4.5 s; something keeps moving (bob / float on one element)",
           "icons from shapes (circle, ring, star, arrow, bubble); 3 accent colours on a dark navy")),
    Style("shape-morph", "Shape morph & liquid", "logo stings, abstract openers, transitions between ideas",
          "smooth", ("pop_in", "grow_in"), ("shake", "typewriter"), ("cut", "zoom"),
          "word", "fade", 0.1, 0.8, 1.2, (2.0, 4.0), "#0F172A", "#F8FAFC", ("#A78BFA", "#22D3EE"), 3,
          ("poppins-bold",), (),
          ("one hero shape on screen that keeps turning into the next (`motion morph`, 0.8-1.2 s, 'smooth')",
           "shapes 40-60 % of the canvas width, centred, gradient fills of 2 accent colours",
           "text small and calm under the shape; the motion is the message",
           "never two morphs at once; hold 0.4 s between morphs")),
    Style("single-line", "Single line & live drawing", "storytelling, journeys, process explainers, signatures",
          "ease_in_out", ("draw_in",), ("pop_in", "drop_in", "shake", "bob"), ("cut", "slide"),
          "word", "fade", 0.12, 1.4, 1.2, (3.0, 5.0), "#FFFDF7", "#1F2937", ("#1F2937", "#EF4444"), 2,
          ("kalam", "poppins"), (),
          ("everything is a line: path shapes 6-10 px stroke in ink colour, drawn on with draw_in 1.2-2 s",
           "one continuous idea per scene; the next line starts where the last ended (match path endpoints)",
           "the only colour is one red accent line per scene; handwritten titles fade in word by word")),
    Style("data-story", "Data story", "results, reports, growth numbers, comparisons, 'by the numbers'",
          "expo_out", ("rise_in", "grow_in", "wipe_in", "count_up", "draw_in"), ("shake", "bob", "wiggle", "spin"), ("cut", "slide"),
          "word", "rise", 0.06, 0.66, 1.0, (2.5, 4.0), "#0B1020", "#FFFFFF", ("#22D3EE", "#A3E635", "#F472B6"), 4,
          ("archivo-black", "space-mono"), (),
          ("one number per scene, 140-220 px, counting up with expo_out over 1.0-1.4 s (`vedit counter add`)",
           "charts grow in 20 frames per bar (draw_in about 0.66 s per bar, stagger 0.5); values roll with the bars",
           "label every number with what it means, under it, 40-56 px; source / footnote in space-mono 28 px",
           "accent colour only on the bar or number that matters; the rest in 40 % white")),
    Style("dark-keynote", "Dark tech keynote", "launches, announcements, feature reveals, premium tech",
          "expo_out", ("rise_in", "fade_in", "zoom_in", "wipe_in"), ("bob", "wiggle", "drop_in", "shake", "pop_in"), ("fade", "cut", "zoom"),
          "word", "rise", 0.09, 0.8, 1.3, (2.0, 3.5), "#050505", "#FFFFFF", ("#7C5CFF",), 2,
          ("poppins-bold", "poppins"), ("vignette", "glow"),
          ("near-black stage, one giant word or number (180-260 px) per scene, centred, lots of empty space",
           "slow and expensive: expo_out 0.7-0.9 s rises of 60-80 px, never bouncy",
           "one violet glow accent; a slow push (rate 0.02) on the hero element",
           "soft fades between scenes, 2-3.5 s each")),
    Style("ui-screencast", "Living UI screencast", "app demos, onboarding, feature walkthroughs",
          "smooth", ("rise_in", "fade_in", "pop_in", "slide_in"), ("shake", "wiggle", "spin", "drop_in"), ("slide", "zoom", "cut"),
          "none", "fade", 0.0, 0.4, 1.0, (2.5, 5.0), "#F4F4F5", "#18181B", ("#2563EB", "#16A34A"), 3,
          ("poppins-bold", "poppins"), (),
          ("rebuild the UI from rect shapes (radius 16-24, white cards, 1-2 px borders) and text, not screenshots",
           "a cursor (small dark ellipse or arrow) moves with keyframes ('smooth') and clicks with a punch",
           "zoom into the part being explained (layer scale 1 -> 1.6 over 0.6 s), back out after",
           "one action per scene; highlight the clicked control in the accent blue")),
    Style("hud", "HUD & terminal", "cyber, security, gaming, hacking, sci-fi data",
          "step", ("typewriter", "wipe_in", "draw_in"), ("bob", "float", "pop_in", "drop_in", "fade_in"), ("cut",),
          "letter", "type", 0.025, 0.2, 0.8, (1.5, 3.0), "#030A06", "#39FF88", ("#39FF88", "#FFB000"), 2,
          ("space-mono",), ("rgb_split", "grain"),
          ("monospace only, uppercase, 32-64 px; text types in letter by letter (split letter / type, 0.025 s)",
           "corner brackets drawn with path shapes (draw_in 0.3 s) lock onto the subject",
           "green on black, amber only for warnings; hard cuts, 1.5-3 s scenes, a faint rgb_split")),
    Style("risograph", "Risograph & halftone", "zines, music, indie brands, posters that move",
          "snap", ("slide_in", "stretch_in", "wipe_in", "pop_in"), ("glow", "fade_in"), ("cut", "slide"),
          "word", "slide", 0.07, 0.4, 1.0, (1.5, 3.0), "#F5EFE0", "#1D3557", ("#FF4F79", "#1D3557", "#FFD23F"), 3,
          ("bungee", "space-mono"), ("posterize", "grain"),
          ("2-3 flat ink colours (pink, navy, yellow) on paper; layers overlap with multiply blend",
           "posterize + grain over everything; shapes slightly misregistered (a copy offset 6 px in another ink)",
           "snappy slides, no soft fades; 1.5-3 s scenes")),
    Style("pixel", "Pixel art", "games, retro tech, playful apps",
          "step", ("drop_in", "pop_in", "slide_in"), ("fade_in", "fade_out", "glow"), ("cut",),
          "letter", "type", 0.04, 0.3, 1.0, (1.5, 3.0), "#1B1B2F", "#FFFFFF", ("#E43F5A", "#FFD460", "#52D681"), 4,
          ("rubik-mono", "space-mono"), ("pixelate",),
          ("pixelate filter over everything (size 6-8); shapes only rect, diamond, star; no gradients, no rotation",
           "motion in steps: step easing, positions on a 12 px grid, bobs of 12 px",
           "hard cuts; 1.5-3 s scenes; 3-4 bright colours on deep navy")),
    Style("vhs", "VHS / film / CRT", "nostalgia, music videos, throwback promos",
          "ease_out", ("fade_in", "rise_in", "zoom_in"), ("pop_in", "drop_in", "bob"), ("cut", "fade"),
          "line", "fade", 0.2, 0.6, 1.2, (2.0, 4.0), "#14110F", "#F5E6C8", ("#FF6B35", "#F5E6C8"), 3,
          ("righteous", "bebas"), ("vhs", "film", "grain"),
          ("vhs or film filter + grain over the whole video; warm off-white text with a soft glow",
           "slow eased moves, nothing bouncy; occasional shake 4 px for a glitch beat",
           "fades and cuts, 2-4 s scenes; a REC / timestamp in the corner in mono")),
    Style("paper-cutout", "Paper cut-out", "kids, crafts, food, friendly brands",
          "back_out", ("drop_in", "pop_in", "slide_in"), ("glow", "rgb_split"), ("cut", "slide", "pop"),
          "word", "pop", 0.09, 0.45, 1.1, (2.5, 4.0), "#FFF3E0", "#3E2723", ("#E76F51", "#2A9D8F", "#E9C46A"), 4,
          ("titan", "kalam"), ("grain",),
          ("shapes are paper: flat fills, a dark shadow copy offset 8 px down-right at 25 % opacity",
           "things land with a little bounce (back_out / drop_in), then sway 2-4 deg",
           "warm earthy palette, grain texture, 2.5-4 s scenes")),
    Style("vertical-captions", "Vertical social captions", "talking heads, tips, Reels / TikTok / Shorts",
          "back_out", ("pop_in", "punch", "rise_in"), ("drop_in", "spin_in", "typewriter"), ("cut",),
          "word", "pop", 0.05, 0.25, 0.8, (1.0, 3.0), "#000000", "#FFFFFF", ("#FFE600",), 2,
          ("poppins-black", "anton"), (),
          ("captions 2-4 words, 72-96 px, in the platform safe zone (`vedit platform`), highlighted word in yellow",
           "hook title in the top third pops in on frame 1 (pop_in 0.25 s)",
           "cuts every 1-3 s; never let text sit under the app's buttons")),
]}

# styles from the motion-graphics library we can't draw well yet: they need a Python 3D / paint renderer
PLANNED: dict[str, str] = {
    "isometric": "Isometric miniature world (needs the 3D renderer)",
    "toy-3d": "Toy & material 3D (needs the 3D renderer)",
    "watercolour": "Watercolour & ink (needs the paint renderer)",
    "line-boil": "Hand-drawn line boil (needs the paint renderer)",
}


def get(key: str) -> Style:
    k = (key or "").strip().lower()
    if k in STYLES:
        return STYLES[k]
    if k in PLANNED:
        raise VeditError(f"'{k}' isn't available yet: {PLANNED[k]}. Styles: {', '.join(STYLES)}")
    raise VeditError(f"Unknown motion style '{key}'. Styles: {', '.join(STYLES)}")


def brief(s: Style) -> str:
    """The style as instructions for the AI (one paragraph of numbers)."""
    return (f"{s.name} ({s.key}): easing {s.ease}; entrances {', '.join(s.enter)} in {s.enter_s:g}s; never "
            f"{', '.join(s.forbid)}; scene transitions {', '.join(s.transitions)}; text split {s.split}/{s.split_anim} "
            f"stagger {s.stagger:g}s; scenes {s.scene_s[0]:g}-{s.scene_s[1]:g}s; hold {s.hold_per_3_words:g}s per 3 words; "
            f"background {s.background}, text {s.text}, accents {', '.join(s.accents)} (max {s.max_colors} colours); fonts "
            f"{', '.join(s.fonts)}" + (f"; filters {', '.join(s.filters)}" if s.filters else "") + ". " + " ".join(
                r.rstrip(".") + "." for r in s.rules))

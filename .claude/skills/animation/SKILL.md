---
name: animation
description: Text, logo and image overlays with custom animation (presets and keyframes) in vedit. Use when the user asks for titles, hooks, CTAs, lower thirds, logos, stickers, motion, bounce, pop, slide, typewriter, shake or any custom movement.
---

# Overlays & animation

Layers are drawn on top of the video.
- **Text:** `text add`
- **Images:** `image add` (PNG with transparency works best)

Position `x`/`y` is the **centre** of the layer, in canvas px or `"NN%"`. The canvas is 1080×1920 unless `info` says otherwise.

## Quick recipes

- **Hook title, pops in, fades out at 3s:**
  ```
  text add "3 mistakes killing your sales" --y 22% --size 92 --uppercase --end 3 --preset pop_in --preset fade_out
  ```
- **Typewriter subtitle:**
  ```
  text add "Watch till the end" --y 30% --size 60 --preset typewriter
  ```
- **CTA box at the end** (use `info` to get the duration D):
  ```
  text add "Follow for part 2" --start <D-3> --y 62% --bg "#000000B0" --preset slide_in --preset pulse
  ```
- **Logo in the corner, gently floating:**
  ```
  image add logo.png --width 180 --x 86% --y 8% --preset fade_in --preset bob
  ```
- **Emoji/sticker image that shakes:**
  ```
  image add fire.png --width 220 --x 75% --y 40% --start 2 --end 4 --preset pop_in --preset shake
  ```

`--preset NAME:0.6` overrides a preset's duration. Run `uv run vedit --json animate list` for every preset and easing.

## Preset types

- **In** (start of the layer): `fade_in`, `pop_in`, `slide_in`, `zoom_in`, `punch`, `spin_in`, `drop_in`, `typewriter`
- **Out** (end of the layer): `fade_out`, `pop_out`, `slide_out`
- **Loop** (the whole time): `shake`, `bob`, `pulse`, `wiggle`, `spin`

Presets stack. Add one later with:
```
animate preset <id> slide_in --params '{"direction":"left","distance":300}'
```

## Custom motion with keyframes

When the user describes motion that no preset covers, write keyframes.
- `t` is seconds from the **layer start**.
- Animatable props: `x`, `y`, `scale`, `rotation` (degrees), `opacity` (0–1), `reveal` (0–1 text typed).
- `ease` controls the motion **from that keyframe to the next**: `linear`, `ease_in`, `ease_out`, `ease_in_out`, `back_out`, `back_in`, `bounce_out`, `elastic_out`, `step`.

**Example: fly in from the left, overshoot, settle, then drift up and fade:**
```
uv run vedit --json animate keyframes t1 '[
 {"t":0,"x":"-20%","rotation":-15,"ease":"back_out"},
 {"t":0.6,"x":"50%","rotation":0,"ease":"linear"},
 {"t":2.4,"y":"30%","opacity":1,"ease":"ease_in"},
 {"t":3.0,"y":"24%","opacity":0}
]'
```
- Props missing from a keyframe keep their base value.
- Keyframes and presets combine: keyframes first, then the presets modify them.
- After animating, check a few moments with `frame --at T` and Read the images.

## Styling text

- **Start from a look.** `text add "HOOK" --look viral-hook` sets font, colours, outline and animation in one go.
  `text look list` shows them all (viral-hook, bold-yellow, clean-white, boxed-label, dark-box, editorial, luxury,
  cinematic, marker-note, neon, retro-sunset, comic-pop, playful, tech, minimal-caption, plus the user's saved ones).
  Any explicit option (`--size 90`, `--preset ...`) overrides the look.
- **Keep it consistent.** One look for every title in an edit: `text look apply <name>` restyles every text layer
  (`--layer t2` for one, `--captions` to include captions). At most two fonts per video: the title font plus one
  accent (e.g. `dm-serif-italic` for a single emphasis word).
- **Reuse.** `text look save my-brand --from t1 --about "..."` saves a layer's style + animation for every future
  project. Offer this when the user likes a style.
- **Options:** `--size`, `--color`, `--stroke` (outline colour), `--stroke-width`, `--bg "#RRGGBBAA"` (rounded box),
  `--font`, `--uppercase`, `--max-width`, `--letter-spacing 8` (wide cinematic caps), `--glow "#FF2BD6"` (neon),
  `--gradient "#FFE259,#FF4E50"` (top → bottom fill). `text edit` takes the same, plus `--glow none` / `--gradient none`.
- **Fonts:** 24 fonts are bundled and look the same on every machine. `vedit fonts` lists them with their feel and
  use (`--feel serif` to filter, `--sheet` renders a sample image to Read). Pick by feel:
  condensed (anton, bebas) for hooks, clean (poppins-bold, kanit) for tutorials, serif (dm-serif, abril) for premium,
  handwritten (marker, kalam) for personal, rounded (titan, lilita) for playful, display (bangers, bungee) for punch
  words. A font file path or system font name also works.
- **Changing existing text:** `text edit <id> --text "new" --color "#FFE600"`
- **Timing and position:** `layer set <id> --start 1 --end 4 --y 40%`

## Readability rules

- **Size:** at least 56px for body text and 80px or more for hooks.
- **Contrast:** always keep contrast, via a stroke (default) or a `--bg` box.
- **Safe zones:** stay inside y 12–75% and x 8–88%.

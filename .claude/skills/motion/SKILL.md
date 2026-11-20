---
name: motion
description: Design projects (the Design studio) and motion graphics - motion-design videos made of scenes of animated shapes, text, counters, charts, drawn-on lines and morphs, in a named style (swiss, data-story, dark-keynote, flat-explainer, hud ...). Use when the user asks for an animated / motion-graphics / explainer / kinetic-type / data / launch / promo video, wants shapes or text that move, stretch or change colour, wants a storyboard turned into scenes, or is working in a design project (`<editor_context>` says DESIGN project). NOT for editing the user's existing video (a social media / TikTok version, text or captions on footage, a promo cut from their clips): that is the normal video editor, never `design build` on it.
---

# Motion design (Design studio)

> **The Design studio is in development and switched off** (`design new` / `design build` refuse unless
> `AIKLIPS_DESIGN=1`). Until it's back, make motion graphics in a normal project: the style and the building blocks
> below (style, motion, counter, chart, shapes, text, keyframes) all work there. Scene / MotionPlan sections apply
> only when the studio is on.

There is no video generator. Everything on screen is a shape, text, the user's image / clip or a piece cut from
their sheet. The motion comes from animating those layers: presets (enter / during / exit) and keyframes.

A **design project** (`kind: design`) is made of **scenes** (like Canva pages) that play back to back. Every element
belongs to the scene its times fall inside; changing a scene's length, order or deleting it moves its elements
along. The user edits the same project by hand in the Design studio (canvas with stretch / rotate handles, scene
strip, Animate panel), so re-run `info` / `scene list` before acting on old assumptions.

## Start with a style (looks as numbers)

Before building anything, pick the style that fits the brief and set it: `vedit style list`, `vedit style show
data-story`, `vedit style set data-story` (or `"style": "data-story"` in a MotionPlan). A style is numbers: one easing
curve for every move, entrance length, stagger, scene length, palette, fonts, what it never does (e.g. swiss = snap
`cubic-bezier(.7,0,.2,1)`, wipes and masks, never fades). Follow them exactly; `<editor_context>` repeats them and the
audit flags anything outside them. Not available yet (say so, offer the closest): isometric / toy 3D, watercolour,
hand-drawn line boil.

Then work like a motion designer: **storyboard** (scenes with seconds and what moves in each), **build**, **look**
(`vedit frame --at T1,T2,...` at the moments things move, and `vedit sheet`; Read the images: clipped text, reads too
short, dead time), **fix**, **audit**.

## Motion building blocks

```
uv run vedit --json motion set t1 --split letter --anim mask_up --stagger 0.03 --ease snap   # kinetic type
uv run vedit --json motion set t2 --split word --anim rise --stagger 0.08                    # word by word
uv run vedit --json counter add 1250000 --prefix '$' --suffix + --y 30% --size 180 --dur 1.2  # counts up (expo_out)
uv run vedit --json chart add bar --data "Mon=12, Tue=30, Wed=18" --suffix k --y 62%        # bars rise one by one
uv run vedit --json chart add line|hbar|donut --data "..." ; chart set ch1 --colors "#22D3EE,#A3E635"
uv run vedit --json shape add path --path "0,1 0.3,0.2 0.6,0.8 1,0" --width 700 --height 200 --border 12 --border-color "#F472B6"
uv run vedit --json motion draw sh3 --dur 1.2 [--out]      # outline / path / chart draws itself on (preset draw_in)
uv run vedit --json motion morph sh2 --to star --at 0.5 --dur 0.8   # or --loop: there and back (morph_loop)
```

- **Kinetic text** pieces: word / letter / line; `--anim` rise, mask_up (rises out of its own row), drop, slide, pop,
  fade, type; each piece starts `--stagger` s after the last and takes `--dur` s on `--ease`.
- **Easing** anywhere (keyframes, kinetic text, draw-on): names (`snap`, `smooth`, `expo_out`, `circ_out`,
  `overshoot`, `anticipate`, `ease_out`, `back_out` ...) or `cubic-bezier(x1,y1,x2,y2)`.
- **Progress keyframes**: `draw` (outline / chart drawn, 0..1), `morph` (0 = own shape, 1 = `morph_to`), `count`
  (0 = `from`, 1 = `to`): `animate keyframes sh1 '[{"t":0,"draw":0},{"t":1.2,"draw":1,"ease":"snap"}]'`.
- Paths: points are fractions of the shape's box; open = a stroke (`--border` thickness / colour), `--closed` = filled.
  Brackets, underlines, arrows' trails, a signature, a journey across the frame.

## Small edits: one command each

```
uv run vedit --json scene list                                   # scenes: times, background, entrance, notes, layers
uv run vedit --json scene add --dur 4 --name "Proof" --background "#0B1F3A,#7C5CFF" --transition pop --notes "3 stars"
uv run vedit --json scene set sc2 --dur 5 --transition slide      # later scenes + their elements move along
uv run vedit --json scene move sc3 0 | scene dup sc1 | scene remove sc2
uv run vedit --json shape add star --scene sc1 --gradient "#FFC23B,#FF3B6B" --points 6 --x 50% --y 40% --preset stretch_in --preset breathe
uv run vedit --json text add "FAST" --look bold-yellow --start <scene start> --end <scene end> --y 70%
uv run vedit --json layer set sh1 --width 600 --height 200 --sx 1.3 --rotation -8   # size, stretch, turn
uv run vedit --json animate keyframes sh1 '[{"t":0,"w":120,"fill":"#FF3B6B"},{"t":0.8,"w":600,"fill":"#22D3EE","ease":"back_out"}]'
uv run vedit --json layer dup sh1 --dx 0 --dy 160
```

- **Shapes:** rect (`--radius`), ellipse, triangle, diamond, polygon (`--sides`), star (`--points --inner`), ring
  (`--inner` = hole), heart, bubble (speech bubble), blob, line, arrow (points right: aim it with `--rotation`).
  Fill with `--fill` or `--gradient` (2-6 colours, `--gradient-angle`); `--fill none --border 10` = outline.
- **Stretch:** shapes have their own `width` / `height` (they stay crisp); anything can be stretched with `sx` / `sy`.
- **Keyframes** (`t` = seconds from the element's start): `x`, `y` (`"NN%"` or px), `scale`, `rotation`, `opacity`,
  `w`, `h` (shape size), `sx`, `sy` (stretch), `fill` (shape colour, blended between keys), `reveal`, plus `ease`
  (`animate list` shows the easings).
- **Presets** (`animate list`): enter `fade_in pop_in slide_in zoom_in punch spin_in drop_in stretch_in grow_in
  rise_in wipe_in typewriter`, exit `fade_out pop_out slide_out shrink_out wipe_out`, during `shake bob pulse wiggle
  spin float sway orbit drift push breathe squash_stretch color_cycle`. One of each phase per element.
- **Scene entrance** (`--transition`): elements without their own enter animation get it (fade / slide / zoom / pop
  / wipe; cut = they just appear).
- **Scene background:** `--background "#hex"` or `"#top,#bottom"` for a gradient.

## A whole video at once: one plan

For "make a motion-graphics video from this brief / these clips", write ONE MotionPlan and build it (one undoable
step per scene). It becomes a design project with scenes, so the user can keep editing it in the studio.

```
uv run vedit --json design catalog                       # frames, presets, looks, textures, fonts
uv run vedit --json design split sheet.png --cutout      # pieces p1..pN + an overview image (Read it to see who is who)
uv run vedit --json design build plan.json --new projects/<name> --format reels
uv run vedit --json sheet                                # Read the image: check every scene
uv run vedit --json audit                                # fix must-fix findings, re-run
```

```json
{
  "title": "Kiko's bakery", "background": "#14141c", "texture": "grain",
  "palette": ["#FFFFFF", "#C5F467", "#FF7A59"],
  "files": {"f1": "/path/shop.mp4", "p1": "projects/x/media/pieces/sheet-p01.png", "m1": "/path/song.mp3"},
  "scenes": [
    {"id": "S1", "dur": 3.5, "purpose": "hook: meet Kiko", "transition": "pop", "elements": [
      {"kind": "shape", "shape": "blob", "gradient": ["#C5F467", "#22D3EE"], "width": 700, "height": 680, "y": "58%", "loop": "breathe"},
      {"kind": "image", "file": "p1", "width": 560, "y": "58%", "enter": "drop_in", "loop": "sway"},
      {"kind": "text", "text": "Meet Kiko", "look": "bold-yellow", "y": "22%", "at": 0.3, "enter": "pop_in"}]},
    {"id": "S2", "dur": 5, "purpose": "the shop", "transition": "slide", "background": "#22303a", "elements": [
      {"kind": "clip", "file": "f1", "src_in": 2.0, "frame": "card", "y": "44%", "loop": "push"},
      {"kind": "shape", "shape": "rect", "width": 40, "height": 12, "fill": "#FF7A59", "y": "70%",
       "path": [{"t": 0, "w": 40}, {"t": 0.6, "w": 620, "ease": "back_out"}]},
      {"kind": "text", "text": "Fresh every morning", "look": "bold-yellow", "y": "74%", "at": 0.8}]}
  ],
  "music": {"file": "m1", "offset": 0, "volume": 0.6}
}
```

- Scenes play in order; give only `dur`. Element `at` / `dur` are relative to the scene, `path` `t` to the element.
- Elements: `clip` / `image` (`file`, `frame` full|card|circle|half|pip), `text` (`look`, `size`, `color`, `split`
  word|letter|line, `split_anim`, `stagger`, `split_ease`), `shape` (`shape` incl. `path` with `points_xy`
  [[x,y],...] and `closed`, `fill` or `gradient`, `sides`, `points`, `inner`, `width`, `height`, `radius`, `border`,
  `morph_to`), `counter` (`count_to`, `count_from`, `prefix`, `suffix`, `decimals`, `size`), `chart` (`chart`
  bar|hbar|line|donut, `data` "A=3, B=7", `colors`, `prefix`, `suffix`). Any: `x`, `y`, `scale`, `rotation`,
  `opacity`, `sx`, `sy`, `enter` (e.g. `draw_in` for a path), `exit`, `loop` (e.g. `morph_loop`), `path`
  (keyframes, which may animate `draw` / `morph` / `count`), `why`. Top level: `"style": "<key>"`.

## Taste

- First scene = the hook: something moves within 0.5 s.
- One idea per scene, few elements, each moving with purpose. Stagger entrances by 0.15-0.4 s; don't animate
  everything at once. Every scene needs something moving (the audit flags 3 s+ with nothing animated).
- Shapes carry the design: a growing underline (`w` keyframes), a bar that stretches in (`stretch_in` / `grow_in`), a
  badge star with `breathe`, a speech bubble behind a quote, a colour change with `fill` keyframes.
- Text: at most about 7 words per line, 1.2 s+ on screen per 3 words, inside the platform's safe zone (`vedit safe check`; x 8-88% / y 12-75% without one), one look for all
  titles, at most two fonts.
- Colour: 3-4 colours for the whole video; mark a new section with a scene background change. At most one texture.
- With music: put scene changes on the beat.

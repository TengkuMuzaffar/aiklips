---
name: motion
description: Design projects (the Design studio) - motion-design videos made of scenes of animated shapes, text and the user's media. Use when the user asks for an animated / motion-graphics / explainer / kinetic-type / promo video, wants shapes or text that move, stretch or change colour, wants a storyboard turned into scenes, or is working in a design project (`<editor_context>` says DESIGN project).
---

# Motion design (Design studio)

There is no video generator. Everything on screen is a shape, text, the user's image / clip or a piece cut from
their sheet. The motion comes from animating those layers: presets (enter / during / exit) and keyframes.

A **design project** (`kind: design`) is made of **scenes** (like Canva pages) that play back to back. Every element
belongs to the scene its times fall inside; changing a scene's length, order or deleting it moves its elements
along. The user edits the same project by hand in the Design studio (canvas with stretch / rotate handles, scene
strip, Animate panel), so re-run `info` / `scene list` before acting on old assumptions.

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
- Elements: `clip` / `image` (`file`, `frame` full|card|circle|half|pip), `text` (`look`, `size`, `color`), `shape`
  (`shape`, `fill` or `gradient`, `sides`, `points`, `inner`, `width`, `height`, `radius`, `border`). Any: `x`, `y`,
  `scale`, `rotation`, `opacity`, `sx`, `sy`, `enter`, `exit`, `loop`, `path`, `why`.

## Taste

- First scene = the hook: something moves within 0.5 s.
- One idea per scene, few elements, each moving with purpose. Stagger entrances by 0.15-0.4 s; don't animate
  everything at once. Every scene needs something moving (the audit flags 3 s+ with nothing animated).
- Shapes carry the design: a growing underline (`w` keyframes), a bar that stretches in (`stretch_in` / `grow_in`), a
  badge star with `breathe`, a speech bubble behind a quote, a colour change with `fill` keyframes.
- Text: at most about 7 words per line, 1.2 s+ on screen per 3 words, inside x 8-88% / y 12-75%, one look for all
  titles, at most two fonts.
- Colour: 3-4 colours for the whole video; mark a new section with a scene background change. At most one texture.
- With music: put scene changes on the beat.

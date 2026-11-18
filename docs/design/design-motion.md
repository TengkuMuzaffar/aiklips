# Design studio: motion-design videos (replaces Design beta)

Status: **implemented** · Date: 2026-11-18

## Goal

A Canva-style editor for motion design: the user makes short videos out of **scenes** (pages) of animated
**elements** (shapes, text, their own images and clips) on a canvas, by hand or with the AI. Menu: **Elements, Text,
Media, Storyboard, AI**. Every element can be moved, stretched, rotated and animated (presets + keyframes). The AI is
whichever model the user connected (Claude login, OpenAI, DeepSeek, OpenRouter, Gemini's OpenAI-compatible endpoint,
Ollama / LM Studio ...): there is no video-generation model and no provider lock-in.

The old Design (beta) was a 5-step wizard that ran a background pipeline and handed back finished versions the user
could only change in the footage editor. It is gone; its useful parts (MotionPlan, builder, sheet splitting,
validation) stay and feed the studio.

## Principles

1. **A design is still a `project.json`.** `kind: "design"` + `scenes`. Undo, `/api/op`, the CLI, export and the
   audit all work unchanged; the AI edits designs with the same `vedit` commands.
2. **Scenes are timeline ranges, not a render tree.** Scenes play back to back (start = sum of the durations before).
   Layers keep absolute times and point at their scene (`Layer.scene`). Scene ops (`ops/scenes.py`) ripple the
   times of a scene's layers and of every later scene; `reconcile` (run on every save) re-derives `Layer.scene` from
   the times, so a layer dragged into another scene on the timeline joins it. The main track is background clips as
   long as the scenes; a scene background is a full-canvas rect on the bottom track (`Scene.bg_layer`, gradient with
   `"#top,#bottom"`). The renderer and export did not change.
3. **One motion engine, two runtimes.** `ops/animate.py` (export) and `static/animate.js` (live) stay mirrored and
   `tests/test_live_preview.py` compares them for every prop and preset.
4. **Shapes are geometry.** `ops/shapes.py` defines every kind; the export rasterises it with PIL
   (`overlay_style.render_shape`, supersampled, borders kept inside the outline), the studio draws the same outline
   as SVG (`static/shapes.js`, `tests/test_shapes_js.py`), so stretching or animating size is crisp at 60 fps without a
   server round trip. The original rect / ellipse / line / arrow pixels are unchanged.
5. **The AI writes JSON through `ai.call`, never provider SDKs.** One structured call per action, validated, one
   repair round; any model that returns JSON works. The chat (tool use) is an extra for models that support tools.

## Data

- `Scene{id, name, dur, background, bg_layer, transition, notes, why}`; `transition` (cut / fade / slide / zoom / pop
  / wipe) = the enter preset its elements get (marked `params.auto`, never overriding the user's own).
- `Layer`: `scene`, stretch `sx` / `sy`, shape `fill_gradient` + `gradient_angle`, `sides`, `points`, `inner`.
- 12 shape kinds: rect, ellipse, triangle, diamond, polygon, star, ring, heart, bubble, blob, line, arrow.
- `Keyframe`: + `w`, `h` (shape size, redrawn crisp), `sx`, `sy`, `fill` (hex, RGB blend), `ease` per key.
- New presets: `stretch_in`, `grow_in`, `rise_in`, `wipe_in` / `wipe_out` (reveal from the left; text keeps its
  typewriter meaning), `shrink_out`, `breathe`, `squash_stretch`, `color_cycle`.
- `SCHEMA_VERSION` 2 (migration 1 → 2 is a no-op: every new field has a default).

## Studio (`static/studio.html` + `studio.js`, `/studio`)

- Top bar: home, File, Resize (canvas presets), undo / redo, title, Check (audit), Preview (full screen), Export.
- Left rail + 344 px drawer: **Elements** (shapes, lines, scene backgrounds, textures, templates; click or drag onto
  the canvas), **Text** (heading / subheading / body, text looks), **Media** (upload, project media, music),
  **Storyboard** (scene cards: name, length, entrance, notes; reorder / duplicate / delete; "Write the storyboard
  with AI"), **AI** (chat with the connected model + quick actions). Contextual views: **Animate** (enter / during /
  exit presets with live hover preview on the canvas, duration, keyframes at the playhead with easing, Record
  motion, Animate with AI) and **Position** (arrange, align, exact size / time, save as template).
- Canvas: everything drawn live in the browser (SVG shapes, export-rendered text / image sprites, `<video>`). Selection
  box with 8 handles + rotate knob; shapes stretch freely (Shift keeps the ratio), media / text keep it (Shift
  stretches); snapping to canvas centre / edges / other elements; marquee and Shift multi-select; inline text
  editing; arrow-key nudge; motion path of x / y keyframes. A drag commits once: `layer_set`, or `keyframe_set` at
  the playhead when the property is already animated or Record is on; several changes go in one `batch` (one undo
  step).
- **Properties** (rail): nothing selected = the project (name, format, size, canvas colour, fps, length, music) and
  the current scene; a scene block clicked in the timeline = that scene; an element (canvas or timeline bar) = all of
  its settings (text content and style, shape kind / fill / gradient / border / corners, media frame, position, size,
  stretch, timing, blend, animation summary); several elements = shared opacity / blend / align.
- **Floating toolbar** (Canva-style pill, per element type, nothing at root): text = timing, font, size −/+, colour,
  bold, italic, underline, strikethrough, case, align, list, spacing, transparency; shape = timing, fill (design
  colours first, gradient, none), border, corners, shape options, kind; image / video = timing, corners / circle,
  border, flip, sound. Then Effects (text: shadow, outline, glow, box, gradient; any: blend), Animate, Position.
  A mini bar above the selection (duplicate, delete, more) and rotate + move handles below it.
- Text styles `bold` (fill-colour stroke + spacing), `italic` (shear), `underline`, `strike`, `list_style` (bullet /
  number per line) are rendered by `ops/text.py`, so the export matches.
- Timeline: transport + scene strip (proportional blocks; drag the edge to change a length, drag to reorder, ⋯ menu,
  +), bars for the current scene's elements (move / trim with snapping, keyframe diamonds), scrubbing.
- Live updates: `/api/events` refreshes the studio when the CLI or the AI changes the project.

## AI (`design/studio_ai.py`, `preview_server/design_routes.py`)

| Action | Call | Applied |
|---|---|---|
| Storyboard | `design_storyboard` (planner tier) brief → scenes; code scales durations to the asked length | shown first; "Replace" / "Add after" = one undo step |
| Design a scene | `design_scene` (planner) notes + other scenes' look + existing elements → MotionPlan elements | built by `builder._element` inside the scene, one undo step; bad elements skipped |
| Animate selection | `design_animate` (editor) elements + intent → presets / keyframes | one undo step |

The model is the one picked in the studio (`body.model` → `ai.use_models`), else the saved role default, else the
quality tier. Prompts carry the vocabulary generated from code (shape kinds, presets with help, easings, looks,
fonts), so they can't drift from the renderer. Provider errors map to 429 / 503 / 502 with a clear message.

## Migration

- Old Design (beta) versions (a saved `.vedit/design_plan.json`, a main track of background clips) become designs when
  opened (`scenes.adopt_legacy`, one history step). `vedit design build` now makes design projects directly.
- The wizard, version cards and the `design_prepare` / `design_var` jobs are removed; leftover queued jobs of those
  kinds fail harmlessly ("no handler"). The old `designs` / `design_vars` tables are left unread.

## Later

- Shape morphing between kinds, line draw-on (path trim), masks / wipes in any direction, parallax groups.
- Element groups (move / animate together), copy-paste between designs, brand kit (palette + fonts).
- AI: "design the whole video" from the storyboard in one go (one `design_scene` call per scene) with the auditor
  loop of the old pipeline.

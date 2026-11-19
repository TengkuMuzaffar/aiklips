# aiklips: you are the video editor

People clone this repo and ask you to edit their videos in plain language. You edit **only** through the `vedit` CLI.
Every command is a non-destructive, undoable change to `project.json`. Original media is never modified.

Run commands as `uv run vedit --json <command>` (the JSON output is easier to read reliably).

## Workflow (follow every time)

1. **Find or create the project.**
   - Run `uv run vedit --json info`.
   - If no project exists: `uv run vedit init projects/<name>`, then `uv run vedit import <video files...>`.
   - Projects live in `projects/` (git-ignored).
2. **Understand before cutting.** Read the clip list, durations and layers from `info`. For talking-head videos, run `silence-cut --dry-run` first.
3. **One intent per command.** Chain several small commands rather than hand-editing JSON.
4. **Look at your work.**
   - After visual changes, run `uv run vedit --json frame --at <sec>` or `uv run vedit --json sheet`.
   - Then **Read the image** it returns to check the result yourself (text placement, crop, background).
5. **Justify and audit before saying done.**
   - Record why each clip you chose or kept is there: `vedit why c3 "close-up proves the texture claim" --role proof`.
   - Run `uv run vedit --json audit`. Fix every **must-fix** finding, and should-fix ones where they help. Re-run until it passes.
   - In the desktop editor this is enforced: when you finish a turn that changed the edit, the auditor runs and, while
     the score is below the pass mark (`vedit audit-settings`, default 80) or must-fix findings remain, sends the
     findings back to you (up to the round cap). Fix them, or say clearly why one shouldn't be fixed. If the
     `<editor_context>` says `auditor: off`, the user switched it off: skip `why` / `audit` unless they ask.
   - Don't call an edit finished while the audit has must-fix findings; if one can't be fixed, say so and why.
6. **Tell the user what changed**, in timeline seconds, the audit score, and mention they can say "undo".
7. **Export only when asked**, or when the user is happy: `uv run vedit --json export`. Default: H.264 MP4 at the project's size and fps in `projects/<name>/exports/`; unset options reuse the project's last export settings, and a file is never overwritten (name-2…). Ask before ProRes or 2× (huge files).

**The desktop editor:** the user may have it open (`uv run vedit app`, or `vedit serve` in a browser).
- It re-renders automatically after every command, so you never need to start it. Suggest it once.
- The user can also edit by hand in it (drag-and-drop timeline, inspector). Re-run `info` before acting on old assumptions.
- Inside the editor's AI pane, every message starts with an `<editor_context>` block giving the playhead and the selected clip or layer. Words like "this" and "here" refer to that selection.
- The chat has three modes, named in `<editor_context>`: auto (edit straight away), ask (the user approves each
  edit command), plan (edits are blocked: look, reply with a numbered plan, end your turn; they approve with a button).

## Rules

- **Times are timeline seconds.** Times are what the viewer sees after earlier cuts. Always re-check `info` after cutting, because times shift.
- **Captions go last.** Add them after all cuts and speed changes. Layers and captions are remapped by cuts, but auto-captions transcribe the current edit, so do them last.
- **Ask before slow effects.** Before `bg remove --mode quality`, `face prop`, or effects on long videos (over 60s), give the user a time warning and ask. The `fast` modes are fine to run directly.
- **Use undo for mistakes.** Don't try to rebuild state by hand. Use `undo`, `redo`, `history` and `checkout <n>`.
- **Don't touch user media files.** Never delete or overwrite them. Never hand-edit `.vedit/`.
- **Hand-editing `project.json` is a last resort.** It's acceptable for complex keyframes, but keep it valid. The next command records it in history automatically.
- **Report errors.** If a command errors, read the message and fix the inputs. Don't retry blindly.
- **Sound.** Speech clips keep their sound and the music ducks under them; b-roll noise (wind, traffic, hum) is
  muted so the music carries those parts. `vedit sound report` shows each clip's sound, `vedit sound auto` applies
  those defaults, `vedit volume 0 --clip cX` mutes one clip. Never cut a speaking clip mid-word.
- **Signature moves.** One memorable move beats five generic ones: `vedit fx apply <move>` (at most 2 per video).
- **Green screen footage:** `bg remove --mode chroma` (instant, keyed in the render); the AI modes are for footage without a screen.
- **Overlays.** Picture-in-picture: `overlay add <video> --circle --border 10` (face cam), green screen: `--chroma #00FF00`.
  Textures and light leaks from `overlay library` use `screen` / `overlay` blend; keep them subtle (opacity 0.5-0.85)
  and use at most one texture for the whole edit. Shapes are for callouts (arrow, ring) and boxes behind titles.
  A blank / colour screen (title card, fade-to-colour) = `shape add rect --full --fill #hex` (or `--gradient`);
  the editor calls these the Elements tab.
- **Sound polish.** Talking clips: `sound voice clean` (or `studio` for a podcast sound). Use `sound fade` on clips
  whose sound starts or ends abruptly (0.1-0.3 s), not on speech cut mid-sentence.
- **Tracks are equal.** The video track holds the clips (and gaps) and sits among the other tracks (0 = bottom,
  higher = drawn on top) at a height set with `track video up|down|top|bottom|N` (default: the bottom). Tracks under
  it show through its gaps, letterbox bars and a keyed green screen: a colour screen behind green-screen footage =
  `shape add rect --full --fill #hex --track 0 --new-track`, `track video up`, `bg remove --mode chroma --replace none`.
  Several layers share a track when their times don't overlap; an element can also sit on the video track itself, in
  a gap or after the clips (`place <layer> --video-track --at T`; never over a clip). The timeline lasts until
  whatever ends last.
  **Something must stay at 0s**: a move that leaves 0s empty is refused, so put another clip / element at 0s first.
  B-roll over a talking head: `to-overlay <clip> --at T` (keeps sound, colour, filters; leaves a gap where the clip
  was, `--close-gap` to ripple), or `overlay add`; back again with `to-clip [--at T]`. `place <clip> --at T` past the
  previous clip leaves a gap. `info` shows each layer's `track`, `hidden`, gap clips and `video_track_height`; a hidden
  track (the user's eye toggle) is not in the export, so check `track list` before saying something is missing.
- **Effects on part of a clip.** `fx add <f> --clip c2 --start 1 --end 2.5 --fade-in 0.3` (seconds from the clip's own
  start) instead of splitting the clip just for an effect. Video overlays take filters and colour too
  (`fx add --layer`, `color set|look --layer`).
- **Stretches, not clips.** For a look on a section (flashback, ending), use one `adjust add` layer over it instead of
  the same filter on each clip. Before a big change, `version save "before X"` so the user can compare.
- **Filters** (`vedit fx add blur|sharpen|denoise|glow|vignette|grain|rgb_split|edges|posterize|invert|mirror|pixelate|letterbox|film|vhs|shake|zoom_pulse|trails`):
  use them to serve the look, not to decorate. Prefer one look across the whole edit (`fx add film` with no `--clip`
  adds it to every clip) over different filters per clip; for colour itself use `color look`, not filters.
- **Consistency.** One text look for all titles (`text look apply`), at most two fonts, one colour look on every
  clip, and run `color match` whenever the edit mixes footage from different cameras or lighting.
- **Hiding things = `vedit mask`.** To hide a label, logo, brand or plate, use a tracked `mask add`. The default style `remove` rebuilds the background so the thing looks like it was never there; use `--style blur|pixelate` only for privacy censoring (faces, plates) or when the user asks for blur. Never build PNG or colour patches with ffmpeg as overlay layers, because they look stuck on and slide off when the camera moves.
- **Stay inside vedit.** Don't run raw ffmpeg / python for edits. If vedit can't do something, say so and suggest the closest vedit command.

## Canvas & safe zones (default 1080×1920, Reels/TikTok)

- **Positions:** `x` and `y` are the layer's centre point, in px or `"NN%"`.
- **Know the platform.** When the user names an app ("for TikTok", "Reels", "Shorts") or says "social media", set it
  first: `vedit platform tiktok|reels|shorts|all` ("social media" without an app = `all`, the strictest). Its safe
  zone (the part its top bar, caption bar and like / comment / share buttons leave visible) is in the
  `<editor_context>` and in `vedit platform`. Everything that matters (text, captions, stickers, buttons, CTAs,
  picture-in-picture, logos) goes inside it; only full-frame backgrounds may cover the rest.
- **Lay out for the app, don't centre everything.** Hook / title in the upper third of the safe zone, captions just
  above its bottom edge, CTAs and "follow" / "subscribe" elements in its lower part (never under the right-hand
  buttons), a logo in a corner *of the safe zone*, faces / the subject clear of the text. Size elements to the zone's
  width (text `--max-width` about the zone width minus 60px), not the canvas width.
- **Measure, don't guess.** `vedit safe check` gives every layer's real on-screen box and what pokes out;
  `vedit safe fix [ids]` moves (and if needed shrinks) them back inside. Check stills with `vedit frame --at T --guides`
  (the covered parts are red). The audit flags any layer outside the zone with the fix command.
- **Default text positions (all apps, 9:16):**
  - Hook or title: y=`18%`–`30%`
  - Captions: y=`70%`–`75%`
  - Logo: a corner inside the zone, e.g. x=`80%` y=`16%`

## Command cheat-sheet

```
projects | init <dir> | use <dir> | import <files...> [--at N --start S --end E] | info
canvas --preset reels|square|landscape|portrait45
trim --head S --tail S | keep --from A --to B | delete --from A --to B | split --at T
move <clip> --to N | place <clip> --at T (gap before it) | place <layer> [--at --end --track N --new-track --under-video --video-track --src-in]
to-overlay <clip> --at T [--track N --share-track --close-gap] | to-clip <layer> [--index N | --at T] | track list | track hide|show <N|video> | track video up|down|top|bottom|N
trim-to --at T --side start|end | paste --from ID --to ID [--to ID2] --group colour|effects|framing|sound|speed|style|frame|transform|animation
remove-clip <clip> | speed X [--clip ID | --from A --to B] | volume X [--clip ID]
fit cover|contain|blur [--clip ID --focus-x 0..1] | zoom Z [--clip ID --to Z2] | jumpcut-zoom [--zoom 1.12]
silence-cut [--threshold -35 --min 0.5 --pad 0.12 --dry-run]
overlay library | overlay add <video|source id|library key> [--start --end --x --y --width --src-in --volume --loop --blend --circle --radius --border --border-color --chroma #00FF00 --similarity]
shape add rect|ellipse|triangle|diamond|polygon|star|ring|heart|bubble|blob|line|arrow [--full (whole canvas) --track N --new-track --start --end --x --y --width --height --fill --gradient '#a,#b' --gradient-angle --sides --points --inner --border --border-color --radius --rotation --blend --preset --scene ID]
layer order <id> up|down|front|back|N | layer style <id> [--blend --radius --border --border-color --circle/--rect --chroma --volume --loop --fill --fit free|cover|contain --flip-h --flip-v]
text add "TEXT" [--look NAME --start --end --x --y --size --color --stroke --bg --font --uppercase --letter-spacing --glow --gradient --bold --italic --underline --strike --list bullet|number --align --line-spacing --preset NAME[:dur]]
text edit <id> [...] | image add <png> [--width --x --y --preset]
text look list | text look apply NAME [--layer ID --captions] | text look save NAME --from ID | text look delete NAME
fonts [--feel serif --sheet]
color stats | color match [--to CLIP | --ref ID | --video FILE --strength 0.8] | color look NAME [--intensity 0.8 --clip ID | --layer ID]
color set [--clip | --layer ID --brightness --contrast --saturation --temperature --tint] | color lut FILE.cube | color looks | color clear [--clip | --layer]
layer list | layer set <id> [--start --end --x --y --scale --rotation --opacity --width --height --sx --sy (stretch)] | layer dup <id> [--dx --dy] | layer remove <id>
animate list | animate preset <id> <name> [--duration --params JSON] | animate keyframes <id> 'JSON' | animate clear <id>
captions srt <file> [--style] | captions auto [--language en|ms|...] | captions style <id> bold|boxed|minimal|neon|karaoke
bg remove [--clip --mode chroma|fast|quality --replace color:#hex|blur|image:path|video:path --key auto|#hex] | bg tune [--similarity --blend --spill --shrink --key] | bg replace <spec> | bg clear
face mesh [--color --glow --dots] | face sticker <png> --anchor eyes|head|nose|mouth|face | face prop <model.obj> | face blur [--style pixelate] | face clear
transition fade|fadeblack|slideleft|zoomin|hblur|wipeleft|circleopen|none [--clip ID --duration 0.5] | transition list
mask find --at T [--near x,y,w,h] | mask add --at T --box x,y,w,h[;x,y,w,h...] [--space source] | --paint JSON | --object NAME [--style remove|blur|pixelate|fill|dim --keep-box x,y,w,h --no-track --shape ellipse --tracker auto|segment|outline|box --sam auto|on|off] | mask check <id|all> | mask fix <id> --at T --box x,y,w,h [--space source | --remove-at T | --clear] | mask list | mask set <id> [--tracker] | mask remove <id|all>
focus add --at T --box x,y,w,h | --paint JSON | --object NAME [--mode spotlight|follow|both --style spotlight|colorpop|brighten|glow|outline --color --width --zoom 1.3] | focus set [--style --color --width --strength] | focus clear
music add <file> [--volume 0.2 --duck/--no-duck --offset S] | music set <id> [--volume --start --offset S --end T|none --duck/--no-duck --loop/--no-loop] | music remove <id>
audit [--no-render --ai] | why <clip|layer> "reason" [--role hook|problem|setup|demo|proof|benefit|payoff|cta --beat b2]
audit-settings [--threshold 80 --max-rounds 3] | sound report | sound auto
fx list | fx apply punch_in|speed_ramp|freeze_title|flash_cut|whip|keyword_pop|before_after|zoom_out_reveal [--clip ID --at T --text W]
fx filters | fx add <filter> [--clip ID | --layer ID --set amount=20 ... --start S --end S --fade-in S --fade-out S]
fx set <fxN> amount=30 on=false start=1 end=2.5 fade_in=0.3 | fx replace <fxN> <filter> | fx move <fxN> <index> | fx copy --from ID [--to ID] | fx remove <fxN|all>
taste [--clear] | ai-models [--vision --planner --editor --auditor <provider/model> --clear]   (saved default model per role)
undo [--steps N] | redo | history | checkout <step> | version save "NAME" | version list
update status | update check | update apply | update rollback | update auto true|false   (installed app only)
marker add --at T [--label L --color #hex] | marker list | marker set <id> | marker remove <id|all> | marker chapters
sound fade [--clip ID] --in S --out S | sound voice clean|studio|off [--clip ID]
platform [tiktok|reels|shorts|all|none] | safe check [--platform P] | safe fix [ids...] [--margin 12] | frame --at T --guides
adjust add [--start --end --look NAME --intensity --saturation --brightness --contrast --temperature --fx vignette ...]  (then `fx add <f> --layer adjN`)
template save NAME --layer ID [--layer ID2 ...] | template list | template apply NAME --at T | template delete NAME
proxy make | proxy status | proxy clean
preview [--open] | frame --at T[,T2,...] | sheet | serve
export [path.mp4|.mov|.webm|.gif] [--format mp4|hevc|prores|webm|gif --fps 30 --scale 1 --quality high|standard|small --folder DIR --name N --no-audio]
ref add <videos...> | ref list | ref show <id> | ref recipe <id> | ref set <id> <trait.path> <json> | ref remove <id>
design new <dir> [--format reels|square|landscape|portrait45 --dur 5 --background #hex] | design build plan.json [--new DIR --format reels] | design split <sheet.png> [--grid 2x3 --cutout --out DIR] | design catalog
scene list | scene add [--dur 5 --after ID --name --background #hex --transition cut|fade|slide|zoom|pop|wipe --notes] | scene set <id> [--dur --name --background --transition --notes --why] | scene move <id> N | scene dup <id> | scene remove <id>
gen run --ref ID [--ref ID2] -f <footage/music/image>... [--model watcher|director|editor|auditor=<provider/model>] [--sound <audio or video: only its sound is used; repeatable, videos take turns> --count 3 --length 20 --about "..." --hook --cta --format reels --vary hook --vary pacing ...] | gen list
```

**References / Generate:**
- To edit the user's own footage like a reference by hand, run `vedit --json ref recipe <id>`: ordered vedit commands
  built from its Style DNA (fill in the `<...>` parts) plus `gaps`, what vedit can't do exactly, with the closest option.
- `vedit gen run` makes several complete projects (in `projects/batches/<id>/vNN`) and uses the user's AI quota:
  about 5 calls per video with the defaults (concepts + editor + auditor, + one editor revision and re-audit when the
  score is below the pass mark), plus 1 planner + 1 rank call per batch. Each extra revision round adds 2 calls (cap:
  `vedit audit-settings --max-rounds`). `--no-review` / `--no-explore` cut that to about 1-2.
- Give the estimate and ask before running it with `--count` above 3.
- Four AI roles: Watcher (studies references, logs footage; needs vision), Creative director (angles, hooks,
  concepts), Editor (builds and revises edits), Auditor (watches renders; needs vision). Precedence: this
  generation's `--model` > the user's saved default (`vedit ai-models`) > the quality tier. Say which models you used.
- To change one generated video, open its project folder with `vedit use` and edit it like any other project.
- To keep the AI improving one generated video that's below the pass mark (and not edited by hand):
  `vedit gen improve <variation id> [--rounds 3]`. It revises from that video's best version.

**Design: motion-design videos (the Design studio) - IN DEVELOPMENT, switched off for users:** `design new` /
`design build` and the studio refuse unless `AIKLIPS_DESIGN=1`. For any motion-graphics request, build it in a normal
project with the motion commands below (`vedit init`, `style set`, `motion`, `counter`, `chart`, shapes, text);
tell the user the Design studio is coming later. The notes below describe it for when it is switched on.
- **Never turn a video edit into a design.** `design build` replaces a project's clips, layers and music, so on a
  video-edit project use `--new projects/<name>` (a separate design); it refuses otherwise. "Make it for social media"
  on a video edit is an edit task (platform, safe zone, text, captions), not a design.
- A design project (`vedit design new`, or the studio) is made of **scenes** played back to back (`vedit scene
  list|add|set|move|dup|remove`); each element belongs to the scene its times fall in and moves with it. Load the
  `motion` skill for any design or motion-graphics work.
- Elements: shapes (`shape add star --scene sc1 --gradient ...`; 12 kinds), text, the user's images / clips. Animate
  with presets (enter / during / exit) and keyframes, including shape size `w` / `h`, stretch `sx` / `sy` and `fill`.
- A whole video at once: ONE MotionPlan JSON with `vedit design build plan.json` (one undoable step per scene; it
  becomes a design project) instead of dozens of commands; then check with `vedit sheet` and fix with normal commands.
- `vedit design split sheet.png --cutout` cuts a character / storyboard sheet into PNG pieces (background removed).
- `vedit design catalog` lists what a plan may use (frames, presets, looks, textures, fonts).
- In the studio the user can also ask the AI (any connected model) for a storyboard, a scene from its notes, or
  motion for the selection: 1 call each.

## Skills

Detailed recipes live in `.claude/skills/`:
- `cutting`
- `animation`
- `captions`
- `background-removal`
- `face-ar`
- `masking-focus`: remove / hide / censor regions with tracking (box, multi-box or painted shape), and spotlight or follow an object
- `transitions`
- `color`: match clips to each other or a reference, grade with looks, LUTs
- `motion`: design projects (Design studio): scenes, shapes, stretch / size / colour motion, and whole videos in one MotionPlan

Load the relevant one before doing that kind of edit.

## Developing aiklips itself

- **Code:** `src/vedit/`
  - Pure timeline ops: `ops/cut.py`
  - FFmpeg graph: `render/graph.py`
  - Overlay rendering: `render/layers.py`
  - Effects: `ops/effects.py`, `ops/bg_remove.py`, `ops/face_ar.py`, `ops/track.py` (masks and object focus;
    `ops/segment.py` = SAM 2 video tracker (default via `auto` when SAM is installed; 512 px input, chunks, growth
    for objects entering the frame, `fixes` as references), `ops/outline.py` = point/outline tracker (fallback),
    `ops/sam.py` = SAM 2.1 tiny image outlines, `mask-ai` extra; `ops/maskfind.py` (`mask find`: numbered SAM
    candidates) and `ops/maskcheck.py` (`mask check`: motion profile, flagged frames, SAM re-check, check sheet).
    Measure tracking changes with `scripts/mask_bench.py` (exact ground truth, IoU; `--only segment`) before and after)
  - Licences: every dependency / model with its licence in `THIRD_PARTY_NOTICES.md`; commercial use must be allowed
  - Filters: `ops/fx_filters.py` registry (one entry = UI card + CLI + FFmpeg chain), stored as `Effect(type="fx")`,
    applied after fit + grade at canvas size in `render/graph.py`. A new filter needs only a registry entry + `STRONG` test value.
  - Tracks (equal): `Layer.track` (`normalize_tracks` in project.py, which also keeps `Project.main_z` = the video
    track's height, i.e. how many tracks are under it), video-track gaps (`Clip.gap`), `Layer.hidden` /
    `Project.main_hidden`. `Project.duration` = whatever ends last (clips or a layer's end); a project can have no
    clips. Render: `graph.build` draws canvas -> layers under the video -> the video track with alpha (`keep_alpha`
    around grade / filters, transparent gaps / bars, `replace none` keys) -> layers over it; with nothing under the
    video it keeps the old opaque path. The editor's base.mp4 bakes in the layers under the video (`base_hash`), the
    browser draws only those over it. The 0s rule is `tracks.zero_guard` (editor `ZERO_GUARDED` ops + CLI moves),
    which also refuses a clip over an element on the video track (`Layer.on_video`, `tracks.put_on_video_track`;
    such layers stay out of the track rows and are drawn right after the video). Preview transform box:
    `drawSelBox` / `transformDrag` in index.html (corners = scale, sides = width / height for shapes else sx / sy,
    knob = rotation; one `batch` op per drag). Timeline ops shared by the editor and the CLI live in `ops/tracks.py`; **every editor action
    (`editor/actions.py` `@op`) needs a CLI command too, so the AI can do it** (`tests/test_ai_parity.py` checks this).
  - Overlays: `Layer` types video / shape + frame fields (blend, radius, border, mask, chroma) in `project.py`;
    `ops/overlays.py` (add / order / style + the generated library, cached in ~/.aiklips/overlays), pixels in
    `ops/overlay_style.py` (shared by export and live sprites), video frames decoded per layer in `render/layers.py`,
    blend modes in `render/graph.py` `_blend_layer`. Live preview plays video layers in `<video>` elements (keyed on a
    canvas for green screen) with CSS mix-blend-mode, so #pv-hold / #pv-layers must not get a z-index.
  - Editor tools: `ops/editing.py` (markers + chapters, keyframes, trim to playhead, paste attributes), clip sound
    fades / voice chains in `render/graph.py` (`clip_audio_tail`, `VOICE_CHAINS`), scopes `render/scopes.py`, proxies
    `proxies.py` (previews only; built in the editor's background thread), adjustment layers (`Layer.type == "adjust"`,
    `_adjust_layer` in graph.py; part of the base preview and the instant frame), named versions in `history.py`
    (a `version` key on a step), templates `ops/templates.py` (~/.aiklips/templates).
  - Transitions: `Clip.transition` with `ops/cut.py` `transition_lengths`, rendered as `xfade` in `render/graph.py`
  - Colour: `Clip.grade` + `ops/color.py` (measure / match / looks -> FFmpeg filters, applied after fit in `render/graph.py`)
  - Fonts & text looks: `ops/fonts.py` (bundled font registry in `assets/fonts/`, feel -> font), `ops/text_looks.py`
    (built-in + saved looks in ~/.aiklips/text-looks.json), rendering in `ops/text.py`
  - Editor live preview: the editor plays `base.mp4` (rendered with `layers=False`) and draws overlay layers in the
    browser from `/api/sprite` (`preview_server/sprites.py`, same `render_text`). Layer motion runs in
    `preview_server/static/animate.js`, a port of `ops/animate.py`: **change both together**
    (`tests/test_live_preview.py` checks they agree). The "Final" toggle and `vedit frame` / audit use the full render.
    Video edits show at once via `render/frame.py` (`POST /api/frame`): the clip under the playhead rendered for two
    frames, with dragged-but-unsaved values; it sets `effects.CACHED_ONLY`, so it never starts mask / tracking work.
  - Design studio: `Project.kind == "design"` + `Project.scenes` (`Scene`), scene ops `ops/scenes.py` (ripple layer
    times, scene backgrounds = bottom-track rect layers, main track = background clips; `reconcile` on every save keeps
    `Layer.scene` true to the layer's times), shape geometry `ops/shapes.py` **mirrored in `static/shapes.js`** (SVG in
    the studio; `tests/test_shapes_js.py`), stretch `Layer.sx/sy` + keyframed `w/h/sx/sy/fill` in `ops/animate.py` /
    `static/animate.js` and `render/layers.py`. UI: `static/studio.html` + `static/studio.js` at `/studio` (`/editor`
    redirects designs there); every change is an editor action (`batch` groups a drag into one undo step). AI actions
    (storyboard / scene / animate, one structured `ai.call` each, the model picked in the studio):
    `design/studio_ai.py`, API `preview_server/design_routes.py`. MotionPlans: `design/` (spec, planner = validate +
    fit + patches, builder = plan -> design project, sheet = panel splitting). Old Design (beta) versions become
    designs when opened (`scenes.adopt_legacy`). Note: `docs/design/design-motion.md`.
  - References / Generate: `references/` (analysis = measured by code, dna = Style DNA schema + merge, store), `generate/` (footage log, planner = axes + AI plans, timing = beat snapping, executor = plan → vedit ops, batch = jobs), `ai/client.py` (every LLM call: routing, JSON schema, retries, rate-limit pause), `jobs.py` (SQLite job queue). Design: `docs/design/references-and-generation.md`
  - Distribution: `launcher/` (Go: install, signed updates, rollback), `updater.py` (check + stage + on-demand extras,
    `vedit update`), `.github/workflows/release.yml` (a new `pyproject.toml` version on main = a release). Read
    `docs/design/distribution.md` before changing any of them; a `project.json` format change needs `SCHEMA_VERSION`
    + a `MIGRATIONS` step in project.py.
  - App shell (all three pages): `static/ui.css` (brand tokens, motion, shared components; loaded after each page's
    own `<style>`) and `static/shell.js` (`AK.*`: icons, toasts, the first-run welcome tour, "what's new", the native
    menu bar's `aiklipsMenu(cmd)` from `editor/window.py`). State in `~/.aiklips/ui.json` via `/api/app` + `/api/app/seen`
    (onboarded, last seen version, prefs: default format / fps / calm motion). Release highlights: `whats_new.py` (add
    an entry when a release has something users would notice). Home (`home.html`): sidebar + quick-start cards + grid /
    list of projects; dropping files makes a project whose canvas follows the first video (`import_file(fit_canvas)`).
    App icons: `assets/brand/` (window icon set in `editor/window.py`).
  - Settings page: `preview_server/static/settings.html` at `/settings` (home links to it; the editor opens it in an
    iframe overlay with `?embed=1`, closed/refreshed via postMessage). Tabs: General (default format / fps, calm motion, tour, about), Models (chat default = `ai.json` "chat",
    roles, audit pass mark), Providers & keys (`editor/models.py` registry + add-provider wizard), Permissions
    (`editor/permissions.py`: global always-allow + "ask"/"deny" for anything unlisted, checked in `ChatSession._permission`),
    Usage (`ai.client.usage_summary` over `ai-usage.jsonl`; chat turns are logged there too as task "chat").
  - Chat modes + auditor picker: `ChatSession.mode` (auto / ask / plan) and `.auditor` (off / checks / ai), saved in
    `.vedit/chat.json`. `vedit_kind` in `editor/agent.py` sorts vedit commands into read / note / edit (a new
    read-only command belongs in `_VEDIT_READ*`). Plan mode is ours, not Claude Code's (its plan mode skips
    `can_use_tool` for Bash): edits are denied, the reply becomes a `plan_offer` card, `answer_plan` carries it out.
  - OpenAI / ChatGPT: a provider with `"api": "openai"` goes through `editor/openai_bridge.py`, a 127.0.0.1
    Anthropic-Messages ⇄ Chat-Completions translator (streaming + tool calls) that `models.env_for` points Claude
    Code at; the real key stays in the registry. Covers chat, Generate and the auditor.
  - Audit & justification: `audit.py` (code checks -> findings with evidence + suggested command), `Clip.meta` /
    `Layer.why` / `Project.notes` (beats, rationale), plan validation in `generate/planner.py` (`validate_plan`).
    Design + roadmap (AI reviewer, chat Stop hook, creativity): `docs/design/auditor-justification-creativity.md`
  - Rule of thumb there: **code measures and times, the AI judges and writes**. Never ask the LLM for exact timestamps.
- **Tests:** `uv run pytest`. They use FFmpeg-generated clips. AI calls are faked with `ai.client.set_backend()`.
- **Before committing:** keep the tests green. Add a test for each new op.

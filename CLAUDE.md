# claude-cut: you are the video editor

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
     findings back to you (up to the round cap). Fix them, or say clearly why one shouldn't be fixed.
   - Don't call an edit finished while the audit has must-fix findings; if one can't be fixed, say so and why.
6. **Tell the user what changed**, in timeline seconds, the audit score, and mention they can say "undo".
7. **Export only when asked**, or when the user is happy: `uv run vedit --json export`. The file goes to `projects/<name>/exports/`.

**The desktop editor:** the user may have it open (`uv run vedit app`, or `vedit serve` in a browser).
- It re-renders automatically after every command, so you never need to start it. Suggest it once.
- The user can also edit by hand in it (drag-and-drop timeline, inspector). Re-run `info` before acting on old assumptions.
- Inside the editor's AI pane, every message starts with an `<editor_context>` block giving the playhead and the selected clip or layer. Words like "this" and "here" refer to that selection.

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
- **Consistency.** One text look for all titles (`text look apply`), at most two fonts, one colour look on every
  clip, and run `color match` whenever the edit mixes footage from different cameras or lighting.
- **Hiding things = `vedit mask`.** To hide a label, logo, brand or plate, use a tracked `mask add`. The default style `remove` rebuilds the background so the thing looks like it was never there; use `--style blur|pixelate` only for privacy censoring (faces, plates) or when the user asks for blur. Never build PNG or colour patches with ffmpeg as overlay layers, because they look stuck on and slide off when the camera moves.
- **Stay inside vedit.** Don't run raw ffmpeg / python for edits. If vedit can't do something, say so and suggest the closest vedit command.

## Canvas & safe zones (default 1080×1920, Reels/TikTok)

- **Positions:** `x` and `y` are the layer's centre point, in px or `"NN%"`.
- **Keep important text inside x 8–88% and y 12–75%.** TikTok and Instagram cover the right edge (buttons) and the bottom 25% (caption, music).
- **Default text positions:**
  - Hook or title: y=`18%`–`30%`
  - Captions: y=`70%`–`75%`
  - Logo: a corner, e.g. x=`85%` y=`8%`

## Command cheat-sheet

```
projects | init <dir> | use <dir> | import <files...> [--at N --start S --end E] | info
canvas --preset reels|square|landscape|portrait45
trim --head S --tail S | keep --from A --to B | delete --from A --to B | split --at T
move <clip> --to N | remove-clip <clip> | speed X [--clip ID | --from A --to B] | volume X [--clip ID]
fit cover|contain|blur [--clip ID --focus-x 0..1] | zoom Z [--clip ID --to Z2] | jumpcut-zoom [--zoom 1.12]
silence-cut [--threshold -35 --min 0.5 --pad 0.12 --dry-run]
text add "TEXT" [--look NAME --start --end --x --y --size --color --stroke --bg --font --uppercase --letter-spacing --glow --gradient --preset NAME[:dur]]
text edit <id> [...] | image add <png> [--width --x --y --preset]
text look list | text look apply NAME [--layer ID --captions] | text look save NAME --from ID | text look delete NAME
fonts [--feel serif --sheet]
color stats | color match [--to CLIP | --ref ID | --video FILE --strength 0.8] | color look NAME [--intensity 0.8 --clip ID]
color set [--clip --brightness --contrast --saturation --temperature --tint] | color lut FILE.cube | color looks | color clear
layer list | layer set <id> [--start --end --x --y --scale --rotation --opacity] | layer remove <id>
animate list | animate preset <id> <name> [--duration --params JSON] | animate keyframes <id> 'JSON' | animate clear <id>
captions srt <file> [--style] | captions auto [--language en|ms|...] | captions style <id> bold|boxed|minimal|neon|karaoke
bg remove [--clip --mode fast|quality --replace color:#hex|blur|image:path|video:path] | bg replace <spec> | bg clear
face mesh [--color --glow --dots] | face sticker <png> --anchor eyes|head|nose|mouth|face | face prop <model.obj> | face blur [--style pixelate] | face clear
transition fade|fadeblack|slideleft|zoomin|hblur|wipeleft|circleopen|none [--clip ID --duration 0.5] | transition list
mask add --at T --box x,y,w,h[;x,y,w,h...] | --paint JSON | --object NAME [--style remove|blur|pixelate|fill|dim --keep-box x,y,w,h --no-track --shape ellipse] | mask list | mask set <id> | mask remove <id|all>
focus add --at T --box x,y,w,h | --object NAME [--mode spotlight|follow|both --zoom 1.3] | focus clear
music add <file> [--volume 0.2 --duck/--no-duck --offset S] | music set <id> | music remove <id>
audit [--no-render --ai] | why <clip|layer> "reason" [--role hook|problem|setup|demo|proof|benefit|payoff|cta --beat b2]
audit-settings [--threshold 80 --max-rounds 3] | sound report | sound auto
fx list | fx apply punch_in|speed_ramp|freeze_title|flash_cut|whip|keyword_pop|before_after|zoom_out_reveal [--clip ID --at T --text W]
taste [--clear] | ai-models [--vision --planner --editor --auditor <provider/model> --clear]   (saved default model per role)
undo [--steps N] | redo | history | checkout <step>
preview [--open] | frame --at T[,T2,...] | sheet | export [out.mp4] [--scale 1] | serve
ref add <videos...> | ref list | ref show <id> | ref set <id> <trait.path> <json> | ref remove <id>
gen run --ref ID [--ref ID2] -f <footage/music/image>... [--model watcher|director|editor|auditor=<provider/model>] [--sound <audio or video: only its sound is used; repeatable, videos take turns> --count 3 --length 20 --about "..." --hook --cta --format reels --vary hook --vary pacing ...] | gen list
```

**References / Generate:**
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

Load the relevant one before doing that kind of edit.

## Developing claude-cut itself

- **Code:** `src/vedit/`
  - Pure timeline ops: `ops/cut.py`
  - FFmpeg graph: `render/graph.py`
  - Overlay rendering: `render/layers.py`
  - Effects: `ops/effects.py`, `ops/bg_remove.py`, `ops/face_ar.py`, `ops/track.py` (masks and object focus)
  - Transitions: `Clip.transition` with `ops/cut.py` `transition_lengths`, rendered as `xfade` in `render/graph.py`
  - Colour: `Clip.grade` + `ops/color.py` (measure / match / looks -> FFmpeg filters, applied after fit in `render/graph.py`)
  - Fonts & text looks: `ops/fonts.py` (bundled font registry in `assets/fonts/`, feel -> font), `ops/text_looks.py`
    (built-in + saved looks in ~/.claude-cut/text-looks.json), rendering in `ops/text.py`
  - References / Generate: `references/` (analysis = measured by code, dna = Style DNA schema + merge, store), `generate/` (footage log, planner = axes + AI plans, timing = beat snapping, executor = plan → vedit ops, batch = jobs), `ai/client.py` (every LLM call: routing, JSON schema, retries, rate-limit pause), `jobs.py` (SQLite job queue). Design: `docs/design/references-and-generation.md`
  - Audit & justification: `audit.py` (code checks -> findings with evidence + suggested command), `Clip.meta` /
    `Layer.why` / `Project.notes` (beats, rationale), plan validation in `generate/planner.py` (`validate_plan`).
    Design + roadmap (AI reviewer, chat Stop hook, creativity): `docs/design/auditor-justification-creativity.md`
  - Rule of thumb there: **code measures and times, the AI judges and writes**. Never ask the LLM for exact timestamps.
- **Tests:** `uv run pytest`. They use FFmpeg-generated clips. AI calls are faked with `ai.client.set_backend()`.
- **Before committing:** keep the tests green. Add a test for each new op.

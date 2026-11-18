# aiklips

**An open-source video editor you drive with Claude Code.** Clone it, open it in Claude Code, and describe what you want:

> "Remove the pauses, add a hook title that pops in, put TikTok captions on it, blur my background, and export."

Claude turns that into precise, undoable editing commands. It checks its own work by looking at rendered frames, and a live preview updates as it edits. It's built for short-form video (Reels / TikTok / Shorts, 1080×1920), and works for any aspect ratio.

- ✂️ **Cutting:** trim, split, delete ranges, reorder, speed / slow-mo, auto silence removal, jump-cut zoom, reframe (cover / contain / blurred fill), Ken Burns zoom
- ↩️ **Undo / redo history:** every edit is a snapshot. You can undo, redo, or jump to any step.
- 🎬 **Custom animation:** text and image layers with stackable presets (pop, slide, typewriter, bounce, shake…) or free-form keyframes with easing
- 🔤 **Fonts & text looks:** 24 bundled open-licence fonts (condensed, serif, handwritten, rounded, display…) and 15 ready-made text looks (font + colours + outline / glow / gradient + animation). Save your own look once and reuse it in every project.
- 🎨 **Colour grading:** match clips shot on different cameras so they look like one shoot, or match a reference video's colour. Then apply a look (warm film, teal & orange, moody, vibrant, B&W…) or a `.cube` LUT. All measured by code, no guessing.
- 🧑‍⚖️ **Auditor:** every edit is checked before it's called done: hook, readability, safe zones, overlaps, fonts, colour, pacing, reused footage, loudness and the stated reason for each shot. An independent AI reviewer can watch the render too, and below your pass mark (default 80/100) its review goes back to the AI editor, which revises and rebuilds the edit until it passes or hits the round cap.
- 🔊 **Sound auditor:** measures each clip's own sound (speech vs background noise), keeps speech with the music ducked under it, mutes b-roll wind/hum so the music carries those parts, and flags cut-off words, volume jumps and music that ends early.
- 💡 **Creative generation:** 3 concepts per video with a judge picking the strongest, a library of signature moves (punch-in, speed ramp, freeze + title, flash, whip, keyword pop, before/after, zoom-out reveal) learned from your references, and taste memory from the videos you keep or discard.
- 💬 **Captions:** word-by-word highlighted captions from SRT, or offline auto-transcription (faster-whisper)
- 🎞️ **Transitions:** crossfade, dip to black / white, slide, push, wipe, zoom, whip, circle and more. They're centred on the cut, so timing never shifts.
- 🫥 **Tracked masks:** hide labels, logos or plates with a feathered blur / pixelate / fill that follows the object as the camera moves. Draw the box on the preview, or let the AI find it.
- 🎯 **Object focus:** spotlight an object (blur and dim the rest), or let the camera follow it (auto-reframe wide footage to vertical)
- 🧍 **Background removal:** replace with a colour, blur, image or video (MediaPipe fast mode, or BiRefNet quality mode)
- 🎭 **Face mesh / AR:** 478-point tracked face mesh, stickers that follow the face, 3D props on the head, and face blur / pixelate
- 🎵 **Music:** background tracks with automatic ducking under speech
- 📚 **References:** add videos whose style you like. The AI studies their pacing, hook, text and caption style, animation, transitions and sound (tempo, cuts on the beat), and you can correct any trait
- 🪄 **Generate:** a wizard makes N clearly different videos from your own footage in the style of chosen references. Each one is a normal, undoable project
- 🖥️ **Desktop editor:** a 4-pane window
  - **Project:** inspector with drill-down settings
  - **Preview**
  - **AI chat:** the AI sees your selection and playhead
  - **Timeline:** drag and drop

Rendering, background removal and face tracking all run locally on macOS, Windows and Linux. Your original files are never modified.

The AI sends your instructions to Claude, along with the project details and any preview frames it looks at. Your video files themselves are never uploaded. References and Generate work the same way: measurements, transcripts and small contact sheets are sent, never the videos.

## Download the app

**[aiklips.muzdev.online](https://aiklips.muzdev.online)**, or get the newest version from the [releases page](https://github.com/TengkuMuzaffar/aiklips/releases/latest):

| System | Download | First start |
|---|---|---|
| macOS 12+ | `aiklips-mac.zip` | Unzip, move **aiklips.app** to Applications, then **right-click → Open** the first time (not notarised yet). |
| Windows 10/11 | `aiklips-windows-x64.exe` | Run it; on SmartScreen choose **More info → Run anyway**. It adds Start-menu and desktop shortcuts. |
| Linux | `aiklips-linux-x86_64` / `-arm64` | `chmod +x` and run it once; it adds itself to your app menu. |

The first start sets everything up (Python, libraries, FFmpeg: about 150 MB, a few minutes). After that the app
updates itself: when a new version is out it says so (**Settings → Updates**) and installs it on restart. Updates are
signed and verified, a version that fails to start is rolled back automatically, and your projects
(`~/aiklips-projects`) are never touched. Optional features (AI background removal, face tracking, auto-captions)
download the first time you use them. How it works: [docs/design/distribution.md](docs/design/distribution.md).

## Quick start (from source, with Claude Code)

```bash
git clone https://github.com/<you>/aiklips && cd aiklips
scripts/setup.sh --ai          # macOS / Linux  (Windows: scripts\setup.ps1 -AI)
claude                         # open Claude Code here
```

Then, inside Claude Code:

```
/new myreel ~/Movies/raw-take.mov
Remove the pauses and add a bold hook title for the first 3 seconds
```

### Or use the desktop app

```bash
cd aiklips
uv run vedit app                      # home screen: your projects
uv run vedit app projects/myreel      # or jump straight into one project (created if needed)
```

**The home screen** lists every project, with a thumbnail of the edit, its length and when it was last edited.
- Create a project by picking a name and a format: 9:16, 4:5, 1:1 or 16:9.
- Projects can be opened, renamed, duplicated, removed from the list, or deleted.
- "Open folder…" adds an existing project from anywhere on disk.
- The project list is a small SQLite library at `~/.aiklips/library.db`. Each project's edit stays in its own `project.json`.
- Projects created with the CLI show up automatically. `uv run vedit projects` prints the list.

**The References tab** is a library of videos you want to learn from.
- Drop in TikToks, Reels or ads (as files). Each one is studied once:
  - **Code measures** what needs to be exact: cuts and shot lengths, motion, tempo, the beat grid, and how many cuts land on a beat.
  - **The AI looks at** contact sheets (the first 3 seconds at 4 fps, plus one frame per shot) and describes the hook, text, captions, animation and transitions.
- Click a reference to see its **Style card**: a cut / beat / loudness timeline, every trait with the AI's confidence, and the frames it looked at. **Edit traits** corrects anything it got wrong. Your corrections survive a re-study.
- Without an AI login, a reference still gets its measured traits.

**The Generate tab** makes several videos at once:
1. **References:** tick one or more. With several, the styles are blended and the ★ lead reference breaks ties.
2. **Footage:** your clips, plus optionally one music track and some images. Only these files are used, never the reference's footage or music.
3. **Brief:** what it's about, an optional hook and CTA, length, format and language.
4. **Variations:** how many (1-10) and what may change between them: hook, clip order, pacing, caption style, text animation, transitions, music section. Video 1 stays closest to the reference.
5. **Review:** shows the number of AI calls and the expected time, then you click Generate.

It runs in the background (closing the app is fine). For each video:
- The AI plans the edit.
- Code snaps the cuts to the beat and fits the length.
- vedit builds it as a normal project.
- The AI reviews a contact sheet and fixes text placement once.
- The video is exported.

You can play each result, **Edit** it in the editor (with full undo history), keep it (it moves into Projects), discard it, or regenerate it. If your AI subscription hits its limit, the batch pauses and resumes by itself. The same works from the CLI: `vedit ref add`, `vedit gen run`.

```
┌──────────────────────┬─────────────────────────────┐
│ 1. Project           │ 2. Preview                  │
│  settings, media,    │  player, frame stepping     │
│  clips, layers →     │                             │
│  click to drill down │                             │
├──────────────────────┼─────────────────────────────┤
│ 3. AI editor (chat)  │ 4. Timeline                 │
│  knows your selection│  drag clips to reorder,     │
│  + playhead          │  drag edges to trim, drag   │
│                      │  layers/music to retime     │
└──────────────────────┴─────────────────────────────┘
```

**Importing media:** drop files anywhere on the window, or drag media from the Project pane onto the timeline.

**Using the AI pane:**
- Select something, then type e.g. *"make this 2x faster"* or *"animate this title in from the left"*. The AI uses the Claude Code login you already have, through the Claude Agent SDK.
- It can only run `vedit` commands, so it can't touch anything else on your machine.
- Pick the model in the chat header: Opus is best, Sonnet and Haiku are cheaper.

**Shared history:** the AI's edits, your own edits in the window, and any CLI commands all go into one undo history.

**Shortcuts:**

| Key | Action |
|---|---|
| <kbd>Space</kbd> | Play / pause |
| <kbd>←</kbd> <kbd>→</kbd> | Step one frame (<kbd>⇧</kbd> for 1 second) |
| <kbd>S</kbd> | Split at the playhead |
| <kbd>T</kbd> | Add text |
| <kbd>⌫</kbd> | Delete the selection |
| <kbd>⌘Z</kbd> / <kbd>⇧⌘Z</kbd> | Undo / redo |
| <kbd>+</kbd> <kbd>-</kbd> | Zoom the timeline |

Use `uv run vedit app --browser` (or `vedit serve`) to open the same editor in a browser tab instead.

**Requirements:**
- [Claude Code](https://claude.com/claude-code)
- FFmpeg 6+ and [uv](https://docs.astral.sh/uv/). The setup script installs both.
- Python 3.11–3.12. uv handles this.

**Optional extras:**

| Extra | Enables | Install |
|---|---|---|
| `ai` | Fast background removal, face mesh / AR | `uv sync --extra ai` |
| `captions` | Offline auto-captions (faster-whisper) | `uv sync --extra captions` |
| `bg-quality` | High-quality background removal (BiRefNet) | `uv sync --extra bg-quality` |
| `mask-ai` | Exact object outlines for masks / focus (SAM 2.1 tiny + PyTorch; ~156 MB model downloaded on first use) | `uv sync --extra mask-ai` |

See [examples/prompts.md](examples/prompts.md) for more things to ask.

## How it works

```
You ──► Claude Code ──► vedit CLI ──► project.json ──► FFmpeg render ──► preview / export
          ▲   reads CLAUDE.md + skills       │ snapshot per edit (.vedit/history)
          └── looks at rendered frames ◄─────┘
```

- **`project.json` is the edit.** It's a small, human-readable edit decision list: sources, clips with in/out points, speed, zoom and effects, plus overlay layers, captions and music. Every `vedit` command loads it, applies one change, validates it, saves it, and snapshots it for undo.
- **Rendering** compiles the project into a single FFmpeg filter graph.
  - Text, captions and animated images are rendered frame by frame with Pillow into transparent overlay clips.
  - Background masks and face tracking are computed once per source and cached, so later cuts never redo the work.
- **Claude** reads `CLAUDE.md`, the workflow and safe-zone rules, and loads `.claude/skills/*` for detailed recipes.
  - After an edit it runs `vedit frame` / `vedit sheet` and *looks at the image* before reporting back.
  - Slash commands: `/new`, `/edit`, `/review`, `/undo`, `/export`.

## Using `vedit` without Claude

It's a normal CLI, so you can script it yourself:

```bash
uv run vedit init projects/demo
uv run vedit import raw.mp4
uv run vedit silence-cut --threshold -35 --min 0.5
uv run vedit jumpcut-zoom
uv run vedit text add "Stop scrolling" --y 22% --size 96 --preset pop_in --preset fade_out --end 3
uv run vedit captions auto --style bold
uv run vedit bg remove --replace blur:30
uv run vedit undo
uv run vedit export
```

Run `uv run vedit --help` or `uv run vedit <command> --help` for every option. Add `--json` for machine-readable output.

## Project layout

```
src/vedit/
  cli.py              all commands
  project.py          project.json schema
  history.py          undo / redo snapshots
  media.py            ffprobe / ffmpeg helpers
  ops/                cut, silence, animate, text, captions, effects, bg_remove, face_ar
  render/             filter-graph builder, overlay renderer, runner
  editor/             desktop app: GUI actions, AI chat agent (Claude Agent SDK), native window
  preview_server/     FastAPI server + the 4-pane editor UI + home tabs (gen_routes.py)
  references/         reference library: measuring (analysis), Style DNA (dna), store
  generate/           footage log, variation planner, beat timing, plan executor, batches
  ai/                 one LLM client for references + generate (routing, JSON, retries), prompts
  jobs.py             durable background job queue (SQLite)
.claude/              skills, slash commands, permissions for Claude Code
tests/                pytest suite (uses FFmpeg-generated clips)
```

## Development

```bash
uv sync --extra ai
uv run pytest
```

PRs are welcome. Ideas:
- More animation presets
- Transitions between clips
- Beat detection for music sync
- Multi-track picture-in-picture
- GPU-accelerated segmentation

## Licence & credits

MIT. Built on FFmpeg, Pillow, OpenCV and MediaPipe (Apache-2.0), SAM 2.1 (Meta, Apache-2.0), PyTorch (BSD), rembg / BiRefNet (MIT) and faster-whisper (MIT). Model files are downloaded from their official sources on first use. Every component and its licence: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Check the licences of any stickers, fonts, music or 3D models you add.

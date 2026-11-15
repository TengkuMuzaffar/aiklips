# claude-cut

**An open-source video editor you drive with Claude Code.** Clone it, open it in Claude Code, and describe what you want:

> "Remove the pauses, add a hook title that pops in, put TikTok captions on it, blur my background, and export."

Claude turns that into precise, undoable editing commands. It checks its own work by looking at rendered frames, and a live preview updates as it edits. It's built for short-form video (Reels / TikTok / Shorts, 1080×1920), and works for any aspect ratio.

- ✂️ **Cutting:** trim, split, delete ranges, reorder, speed / slow-mo, auto silence removal, jump-cut zoom, reframe (cover / contain / blurred fill), Ken Burns zoom
- ↩️ **Undo / redo history:** every edit is a snapshot. You can undo, redo, or jump to any step.
- 🎬 **Custom animation:** text and image layers with stackable presets (pop, slide, typewriter, bounce, shake…) or free-form keyframes with easing
- 💬 **Captions:** word-by-word highlighted captions from SRT, or offline auto-transcription (faster-whisper)
- 🎞️ **Transitions:** crossfade, dip to black / white, slide, push, wipe, zoom, whip, circle and more. They're centred on the cut, so timing never shifts.
- 🫥 **Tracked masks:** hide labels, logos or plates with a feathered blur / pixelate / fill that follows the object as the camera moves. Draw the box on the preview, or let the AI find it.
- 🎯 **Object focus:** spotlight an object (blur and dim the rest), or let the camera follow it (auto-reframe wide footage to vertical)
- 🧍 **Background removal:** replace with a colour, blur, image or video (MediaPipe fast mode, or BiRefNet quality mode)
- 🎭 **Face mesh / AR:** 478-point tracked face mesh, stickers that follow the face, 3D props on the head, and face blur / pixelate
- 🎵 **Music:** background tracks with automatic ducking under speech
- 🖥️ **Desktop editor:** a 4-pane window
  - **Project:** inspector with drill-down settings
  - **Preview**
  - **AI chat:** the AI sees your selection and playhead
  - **Timeline:** drag and drop

Rendering, background removal and face tracking all run locally on macOS, Windows and Linux. Your original files are never modified.

The AI sends your instructions to Claude, along with the project details and any preview frames it looks at. Your video files themselves are never uploaded.

## Quick start

```bash
git clone https://github.com/<you>/claude-cut && cd claude-cut
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
cd claude-cut
uv run vedit app                      # home screen: your projects
uv run vedit app projects/myreel      # or jump straight into one project (created if needed)
```

**The home screen** lists every project, with a thumbnail of the edit, its length and when it was last edited.
- Create a project by picking a name and a format: 9:16, 4:5, 1:1 or 16:9.
- Projects can be opened, renamed, duplicated, removed from the list, or deleted.
- "Open folder…" adds an existing project from anywhere on disk.
- The project list is a small SQLite library at `~/.claude-cut/library.db`. Each project's edit stays in its own `project.json`.
- Projects created with the CLI show up automatically. `uv run vedit projects` prints the list.

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
  preview_server/     FastAPI server + the 4-pane editor UI
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

MIT. Built on FFmpeg, Pillow, MediaPipe (Apache-2.0), rembg / BiRefNet (MIT) and faster-whisper (MIT). Model files are downloaded from their official sources on first use. Check the licences of any stickers, fonts, music or 3D models you add.

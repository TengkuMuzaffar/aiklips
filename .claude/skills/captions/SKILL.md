---
name: captions
description: Add animated word-by-word captions/subtitles to a video with vedit, from an SRT file or offline auto-transcription. Use when the user asks for captions, subtitles, transcript text on screen, or TikTok-style highlighted words.
---

# Captions

Add captions **after** all cutting and speed changes.

## From an SRT file (no AI)

```
uv run vedit --json captions srt subs.srt --style bold
```
SRT times must match the **current timeline**.

## Auto-captions (offline, faster-whisper)

1. Needs the extra (one time):
   ```
   uv sync --extra captions
   ```
2. Run:
   ```
   uv run vedit --json captions auto --language en --style bold
   ```
   - It transcribes the current edit and returns the text. Show the transcript to the user and fix obvious mistakes.
   - Language codes: `en`, `ms` (Malay), `id`, `zh`, `ta`, etc. Leave it empty to auto-detect.
   - `--model small` is the default balance. Use `medium` or `large-v3` for accuracy, `tiny` for speed.

## Styles

| Style | Look |
|---|---|
| `bold` | White, uppercase, thick black outline, yellow active word (classic TikTok) |
| `boxed` | White text on a dark rounded box, teal active word |
| `minimal` | Clean white with a soft shadow |
| `neon` | Purple outline, cyan active word |
| `karaoke` | Grey words turn white as they're spoken |

- **Switch style:** `captions style cap1 neon`
- **Fine-tune:** `text edit cap1 --size 70 --highlight "#22C55E" --color "#FFFFFF"`
- **Move:** `layer set cap1 --y 68%`
- **Words per group:** set `--words 2` (punchier) or `4` (calmer) when adding.

## Fixing a word

1. Edit `project.json` → `layers[].words[].text` for that word (keep the times).
2. Run any `vedit` command (e.g. `info`). It records the manual edit in history.

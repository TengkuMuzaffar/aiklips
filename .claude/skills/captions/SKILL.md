---
name: captions
description: Add animated word-by-word captions/subtitles to a video with vedit, from an SRT file or offline auto-transcription, and edit them. Use when the user asks for captions, subtitles, transcript text on screen, TikTok-style highlighted words, or to fix / restyle / move captions.
---

# Captions

Captions are **ordinary text layers, one per phrase** (a few words: `cap1`, `cap2`...), kept in one **captions group**
(`[gN]` in `info`, one "Captions" row on the timeline). Each phrase knows when its words are spoken, so the word
being said is drawn in the highlight colour. Add captions **after** all cutting and speed changes.

## From an SRT file (no AI)

```
uv run vedit --json captions srt subs.srt --style bold
```
SRT times must match the **current timeline**.

## Auto-captions (offline, faster-whisper)

```
uv run vedit --json captions auto --language en --style bold
```
- The speech-to-text add-on installs itself the first time (a minute or two).
- It transcribes the current edit and returns the group id, the phrase ids and the text. Show the transcript to the
  user and fix obvious mistakes.
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

- **All phrases:** `captions style g1 neon`, or a text look: `text look apply clean --group g1` (keeps size and the highlight).
- **Words per phrase:** `captions words g1 2` (punchier) or `4` (calmer). This rebuilds the phrases, so do it before
  hand edits to single phrases.
- **One phrase:** it's text: `text edit cap3 --size 70 --highlight "#22C55E"`, `layer set cap3 --y 68%`,
  `animate preset cap3 punch`.
- **Move them all:** `group move g1 --by 0.2` (timing), or `layer set` each phrase's `--y` (position).
- **No highlight on a phrase:** `text edit cap3 --no-highlight-words` (back on: `--highlight-words`).

## Fixing a word

```
uv run vedit --json text edit cap3 --text "jubah konvo"
```
The same number of words keeps their times; a different number spreads the phrase's time over the new words.

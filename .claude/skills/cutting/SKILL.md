---
name: cutting
description: Recipes for cutting, trimming, removing pauses, reordering, speed changes and reframing videos with vedit. Use when the user asks to cut, trim, shorten, remove silence/pauses, tighten, speed up, slow down, reorder or reframe a video.
---

# Cutting with vedit

All times are **timeline seconds** after previous edits. Re-run `uv run vedit --json info` after each cut, because times shift.

## Talking-head cleanup (the most common request)

1. Preview what would be cut:
   ```
   uv run vedit --json silence-cut --dry-run
   ```
2. Remove the pauses:
   ```
   uv run vedit --json silence-cut
   ```
   - Too choppy: use `--min 0.8` or `--pad 0.2`.
   - Pauses still left: use `--threshold -30`. Noisy rooms need a higher (less negative) threshold.
3. Optional, so jump cuts feel intentional:
   ```
   uv run vedit --json jumpcut-zoom --zoom 1.12
   ```
4. Check the result:
   ```
   uv run vedit --json sheet
   ```
   Then Read the image.

## Mapping user language to commands

| User says | Command |
|---|---|
| "cut the first 3 seconds" | `trim --head 3` |
| "cut the last 2 seconds" | `trim --tail 2` |
| "remove 12s to 15s" | `delete --from 12 --to 15` |
| "only keep 5s–20s" | `keep --from 5 --to 20` |
| "put the second clip first" | `move c2 --to 0` (get ids from `info`) |
| "speed up the middle part 2x" | `speed 2 --from A --to B` |
| "slow-mo this clip" | `speed 0.5 --clip c3` |
| "mute the clip" | `volume 0 --clip c2` |
| "make it fit / no black bars / landscape footage" | `fit cover` (fills and crops). Use `--focus-x 0.3` if the subject is left of centre. |
| "show the whole frame" | `fit blur` (blurred background behind it) or `fit contain` (letterbox) |
| "slow zoom in" | `zoom 1.0 --to 1.25 --clip c1` (Ken Burns, always fills the frame) |

## Finding the right moment

- **Visually:** `sheet --count 16` shows the whole edit in one image. Use `frame --at T` for detail.
- **By speech:** auto-captions (`captions auto`) give word timings. If the user says "cut where I say 'um'", transcribe first, read the word times from `info`/`layer list`, cut, then remove and re-add the captions.

## Multiple files

- `import a.mp4 b.mp4 c.mp4` appends them in order.
- `import b-roll.mp4 --at 1 --start 2 --end 5` inserts part of a file as the second clip.

## Things to know

- Splits never re-encode the source. Cuts are frame-accurate at render time.
- Overlays (text, captions, music start) shift automatically with cuts. A layer that lands entirely inside a deleted range is removed (reported as `dropped_layers`).

---
name: background-removal
description: Remove or replace the background behind people in a video with vedit (solid colour, blur, image or video). Use when the user asks to remove the background, green screen, blur the background, put them in front of another image/video, or isolate the speaker.
---

# Background removal

Needs the extra (one time):
```
uv sync --extra ai
```
Models download automatically on first use.

## Commands

- **Solid colour:**
  ```
  uv run vedit --json bg remove --replace "color:#0F172A"
  ```
- **Blurred real background** (portrait mode):
  ```
  uv run vedit --json bg remove --replace blur:30
  ```
- **Image background:**
  ```
  uv run vedit --json bg remove --replace image:/path/office.jpg
  ```
- **Video background:**
  ```
  uv run vedit --json bg remove --replace video:/path/beach.mp4
  ```
- **Change only the background** (the mask is reused, no recompute):
  ```
  uv run vedit --json bg replace "color:#FDE68A"
  ```
- **Turn it off:**
  ```
  uv run vedit --json bg clear
  ```
- **Only one clip:** add `--clip c2`.

## Modes

| Mode | Speed | Quality | When |
|---|---|---|---|
| `fast` (default, MediaPipe) | about real time | good for a single person, softer hair edges | Most requests. Run it directly. |
| `quality` (BiRefNet via rembg) | ~0.5–2 s per frame on CPU | clean hair and edges | Final exports. **Ask first** and give a time estimate (frames = seconds × fps). Needs `uv sync --extra bg-quality`. |

- **Flicker:** raise `--smooth 0.7`.
- **Haloes or cut-off edges:** edit `params.low` / `params.high` in project.json (defaults 0.3 / 0.7). Lower `low` keeps more edge.

## How it works (for explaining to users)

1. The mask is computed once for the **whole source** and cached in `.vedit/cache/effects`, so later cuts never recompute it.
2. The render step merges the mask as alpha, then composites the person over the new background.
3. The first `preview`/`frame` after adding it does the computation. Tell the user that this first render is slower.

## Check the result

1. Run `frame --at <a moment with motion>` and Read the image.
2. Look for missing hands or hair. If the fast mode isn't good enough, suggest quality mode.

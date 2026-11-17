---
name: background-removal
description: Remove or replace the background behind people in a video with vedit (solid colour, blur, image or video). Use when the user asks to remove the background, green screen, blur the background, put them in front of another image/video, or isolate the speaker.
---

# Background removal

**Shot on a green or blue screen? Use `--mode chroma`.** It keys the screen colour inside the render itself: no extra
install, no model, no one-time computation, real time, and every setting can be tuned instantly. Only use the AI
modes (`fast` / `quality`) for footage that was **not** shot on a screen.

## Green / blue screen (chroma)

```
uv run vedit --json bg remove --mode chroma --replace image:/path/office.jpg     # key colour sampled from each clip
uv run vedit --json bg remove --mode chroma --key "#2BC24F" --replace blur:30     # give the colour yourself
uv run vedit --json bg tune --similarity 0.16          # screen still showing through
uv run vedit --json bg tune --similarity 0.09          # edges of the person eaten away / green clothing vanishing
uv run vedit --json bg tune --spill 0.9 --shrink 2     # green rim / green cast on hair and skin
uv run vedit --json bg tune --key auto                 # re-sample the screen colour
```

- `--key auto` (default) takes the median of the clearly green (or blue) pixels in each clip, half a second in. The
  JSON output lists the key per clip; if it errors ("Couldn't find a green or blue screen"), it isn't screen footage.
- Defaults: similarity 0.12, blend (edge softness) 0.08, spill 0.6, shrink 1 px. Tune in small steps (±0.02-0.04).
- `bg replace` and `bg clear` work the same as for the AI modes. In the editor: clip inspector → **Background**
  (Pick = eyedropper on the original footage).
- Check edges with `frame --at` on a moment with hair or fast motion, and Read the image.

## AI cut-out (no screen)

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

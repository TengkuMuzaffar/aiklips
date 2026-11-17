---
name: color
description: Colour correction and grading. Make clips from different cameras or lighting match, copy a reference video's colour, and apply looks (warm film, teal & orange, moody, vibrant, black & white...) or a .cube LUT. Use when the user mentions colour, grading, "make it look cinematic/warm/moody", mismatched or inconsistent shots, too dark/bright/washed out footage, or a LUT.
---

# Colour

Code measures the colour of every clip (brightness, contrast, saturation, warmth, tint) and computes the
correction. You pick the look and explain why. Every command is one undoable step.

```
uv run vedit --json color stats                      # per-clip numbers + "spread" (how inconsistent the edit is)
uv run vedit --json color match                      # all clips -> their average: looks like one shoot
uv run vedit --json color match --to c2              # all clips -> the hero clip c2
uv run vedit --json color match --ref 3              # all clips -> reference 3's colour (`ref list`)
uv run vedit --json color match --video look.mp4     # all clips -> any video's colour
uv run vedit --json color look warm-film --intensity 0.7
uv run vedit --json color looks                      # list looks
uv run vedit --json color set --clip c3 --brightness 0.05 --temperature 0.3
uv run vedit --json color lut path/to/film.cube [--clip c1]
uv run vedit --json color clear [--clip c1]
```

## Order of work

1. **Match first, then look.** `color match` fixes the differences between shots. `color look` is the creative
   grade on top. Matching keeps the look, and a new look keeps the match.
2. **Apply the look to every clip** (the default). A different look per clip is what makes edits feel random.
3. Check with `frame --at` on two different clips and Read both images. `color stats` should show
   `spread_after` well below `spread_before`.

## Picking a look

| Content | Look |
|---|---|
| UGC, lifestyle, beauty, talking head | `clean-bright` or `natural` (0.5–0.7) |
| Food, travel, product hero shots | `vibrant` |
| Story, nostalgia, cafe, wedding | `warm-film`, `golden-hour` |
| Cinematic / trailer / action | `teal-orange` |
| Drama, night, fashion, serious topic | `moody` |
| Tech, corporate, clean product | `cool-crisp` |
| Retro / Y2K / thrift | `vintage` |
| Kids, soft aesthetic | `pastel` |
| Timeless / emotional moment | `bw`, `bw-soft` |

- Faces: keep `--intensity` at 0.8 or below for `teal-orange`, `golden-hour` and `vibrant`, or skin goes orange.
- Tell the user which look you chose and **why**, in one line (e.g. "warm-film: matches the cozy cafe footage and the
  reference's faded blacks").
- `--strength` on `match` (default 0.8) controls how far shots are pulled. Lower it if a clip looks over-corrected.
- Log footage (flat, grey): apply the camera's LUT first with `color lut`, then `match` and `look`.

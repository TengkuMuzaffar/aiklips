---
name: masking-focus
description: Hide or censor part of a video (brand names, labels, logos, number plates, screens, faces of bystanders) with a tracked, feathered blur / pixelate mask, or put the focus on one object (spotlight it, or make the camera follow it / auto-reframe). Use when the user says hide, censor, blur this, cover the logo/label/brand, mask, focus on, spotlight, follow the product/person, keep it centred, or auto-reframe.
---

# Masks and object focus

Needs the extra once: `uv sync --extra ai` (OpenCV tracker + MediaPipe detector).

**Never fake a mask** by generating PNG / colour patches with ffmpeg and laying them on top as image layers.
That looks like a sticker stuck on the video and slides off when the camera moves. Use `vedit mask`.

## Pick the region

Boxes are `x,y,w,h` = **top-left corner + size**, on the **canvas** (what `frame` shows), in px or %.

1. Go to a moment where the thing is clearly visible: `uv run vedit --json frame --at T`.
2. Read the frame and estimate the box in % of the frame (e.g. `20%,55%,60%,12%`).
   Make it generous: the mask already adds 10% margin (`--expand 1.1`), but a box that's too tight leaks letters.
3. If you can't see images (no-vision model) use `--object NAME` (COCO names: person, car, truck, bottle,
   cup, cell phone, laptop, tv, book, handbag, ...; `any` = most prominent object),
   or ask the user to draw it: **Mask** / **Focus** buttons under the preview, drag a box.

## Hide something (mask)

```
uv run vedit --json mask add --at 3.2 --box 18%,52%,64%,14%                 # tracked blur (default)
uv run vedit --json mask add --at 3.2 --box 18%,52%,64%,14% --style pixelate
uv run vedit --json mask add --at 3.2 --box ... --style fill --color "#F2C200"  # solid, feathered
uv run vedit --json mask add --at 3.2 --box ... --no-track                  # static (tripod shot, burned-in text)
uv run vedit --json mask list | mask set mk1 --style pixelate --strength 36 | mask remove mk1 | mask remove all
```

- `--at` is the timeline second where the box is right; tracking runs forwards **and** backwards from there
  through the whole clip. It applies to the clip under `--at` (or `--clip c3`).
- One `mask add` per clip: the same label in 4 shots = 4 masks (one `--at` inside each shot).
- Styles: `blur` (default, reads as professional censoring), `pixelate`, `fill` (brand-colour block), `dim`.
- Looks: `--feather 0.04` (soft edge; 0 = hard), `--expand 1.1`, `--shape ellipse` for faces / round logos.
- `--invert` = apply to everything except the region.
- Style changes (`mask set`) are instant; a new box or `--at` re-tracks (one-time, ~real time).

## Focus on an object

```
uv run vedit --json focus add --at 1 --box 30%,35%,40%,40%                    # spotlight: blur + dim around it
uv run vedit --json focus add --at 1 --object person --mode follow           # camera pans with it (auto-reframe)
uv run vedit --json focus add --at 1 --box ... --mode both --zoom 1.3        # follow + spotlight, tighter
uv run vedit --json focus clear [--clip c2]
```

- `follow` on wide (16:9) footage on a 9:16 canvas reframes at `--zoom 1.0` (full height, pans sideways).
  On footage that already matches the canvas it zooms in to 1.4 so there is room to pan.
- Spotlight: `--blur 14 --dim 0.35 --expand 1.35 --feather 0.12`.
- One focus per clip (adding replaces it).

## Check it

Masks: `frame --at` at the **start, middle and end** of the clip. Look at the edges: no letters may peek out.
If they do: `mask remove`, then add again with a bigger box, or at the moment where the label is largest.
If tracking drifts on a fast whip pan, split the clip there and mask each part.

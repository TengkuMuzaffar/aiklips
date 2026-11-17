---
name: masking-focus
description: Remove, hide or censor part of a video (brand names, labels, logos, stickers, number plates, screens, faces of bystanders) with a tracked mask that rebuilds the background (remove) or blurs / pixelates, or put the focus on one object (spotlight it, or make the camera follow it / auto-reframe). Use when the user says remove, erase, hide, censor, blur this, cover the logo/label/brand, mask, focus on, spotlight, follow the product/person, keep it centred, or auto-reframe.
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
3. **Odd shapes:** don't cover a lot of extra background with one big box. Join several boxes with `;`
   (`--box "20%,10%,30%,8%;25%,18%,20%,6%"`), which makes one region shaped like their union. That keeps the
   `remove` fill from sampling the object next to the label.
4. If you can't see images (no-vision model) use `--object NAME` (COCO names: person, car, truck, bottle,
   cup, cell phone, laptop, tv, book, handbag, ...; `any` = most prominent object),
   or ask the user to mark it: **Mask** button under the preview → **Brush** (paint over it), **Grid** (tick squares)
   or **Box**. Painted masks are stored as `shape: paint` and follow the tracked object like boxes do.

## Hide something (mask)

```
uv run vedit --json mask add --at 3.2 --box 18%,52%,64%,14%                 # tracked remove (default): looks like it was never there
uv run vedit --json mask add --at 3.2 --box 18%,52%,64%,14% --style blur     # censor look
uv run vedit --json mask add --at 3.2 --box 18%,52%,64%,14% --style pixelate
uv run vedit --json mask add --at 3.2 --paint '[{"pts":[[200,400],[600,420]],"r":40},{"rect":[150,380,500,90]},{"rect":[...],"erase":true}]'  # canvas px
uv run vedit --json mask add --at 3.2 --box ... --style fill --color "#F2C200"  # solid, feathered
uv run vedit --json mask add --at 3.2 --box ... --no-track                  # static (tripod shot, burned-in text)
uv run vedit --json mask list | mask set mk1 --style pixelate --strength 36 | mask remove mk1 | mask remove all
```

- `--at` is the timeline second where the box is right; tracking runs forwards **and** backwards from there
  through the whole clip. It applies to the clip under `--at` (or `--clip c3`).
- One `mask add` per clip: the same label in 4 shots = 4 masks (one `--at` inside each shot).
- Styles: `remove` (default; rebuilds the surface behind it from the pixels around the region, with matching
  grain, every frame, so it follows lighting changes), `blur` (reads as deliberate censoring: use for faces / plates),
  `pixelate`, `fill` (brand-colour block), `dim`.
- **Product in front of the label** (the label's box overlaps the thing being sold): add `--keep-box x,y,w,h[;...]`
  (canvas, at `--at`) over the product, or `mask set mk2 --keep-box ... --at T` on an existing mask. That
  region is tracked on its own and the mask is never applied over it (any style). In the editor: **Keep** brush.
  `remove` also locks onto the label's texture each frame and leaves alone anything that passes in front of it
  (a hand), and stops painting once the label has left the shot.
- `remove` works best on fairly plain surfaces (cardboard, walls, tables, sky, a shirt). Over detailed texture
  or another object it gives a soft smooth patch. Mask as tightly as you can (multi-box / painted) so the fill
  only samples the surface around the label. It runs once per mask (~real time) and is cached.
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

### Highlight styles (`--style`, changed instantly with `focus set`, no re-tracking)

```
uv run vedit --json focus add --at 2 --paint '[{"pts":[[400,900],[700,950]],"r":60}]' --style glow --color "#FFD400"
uv run vedit --json focus add --at 2 --box 30%,40%,25%,20% --style colorpop      # rest of the shot black & white
uv run vedit --json focus set --style outline --color "#FF3B6B" --width 8
uv run vedit --json focus set --style brighten --strength 0.6
```

| Style | Look | Good for |
|---|---|---|
| `spotlight` (default) | blur + dim around it | talking about one thing in a busy shot |
| `colorpop` | everything else black & white | product reveals, "this one" moments |
| `brighten` | lifts the object itself | subtle emphasis that keeps the shot natural |
| `glow` | soft coloured halo (`--width` 30, `--color`) | product shots, social hooks |
| `outline` | crisp coloured stroke (`--width` 6) | tutorials, pointing out a UI element or part |

- `--paint` (same JSON as `mask add`) hugs the object's real shape: use it for glow / outline so the ring follows the
  object instead of an ellipse. In the editor: **Focus** button → Brush / Grid / Box, then pick the style.
- One signature highlight per video is plenty; keep glow / outline colours on brand.

## Check it

Masks: `frame --at` at the **start, middle and end** of the clip. Look at the edges: no letters may peek out.
For `remove`, also check that the fill matches the surface and hasn't picked up a nearby object's colour (tighten the region if it has).
If they do: `mask remove`, then add again with a bigger box, or at the moment where the label is largest.
If tracking drifts on a fast whip pan, split the clip there and mask each part.

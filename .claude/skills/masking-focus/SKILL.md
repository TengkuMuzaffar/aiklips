---
name: masking-focus
description: Remove, hide or censor part of a video (brand names, labels, logos, stickers, number plates, screens, faces of bystanders) with a tracked mask that rebuilds the background (remove) or blurs / pixelates, or put the focus on one object (spotlight it, or make the camera follow it / auto-reframe). Use when the user says remove, erase, hide, censor, blur this, cover the logo/label/brand, mask, focus on, spotlight, follow the product/person, keep it centred, or auto-reframe.
---

# Masks and object focus

Needs the extra once: `uv sync --extra ai` (OpenCV tracker + MediaPipe detector).

**Never fake a mask** by generating PNG / colour patches with ffmpeg and laying them on top as image layers.
That looks like a sticker stuck on the video and slides off when the camera moves. Use `vedit mask`.

## The procedure (follow every step, in order)

A mask is detailed work: find the exact thing, know how it moves, check every stretch, fix what's wrong. A mask
you haven't checked is not done. Never answer "done" after `mask add` alone.

**1. Find it exactly.** Go to the moment where the thing is largest and clearest.
```
uv run vedit --json mask find --at 12.4                       # SAM outlines every object, numbered, on one frame
uv run vedit --json mask find --at 12.4 --near 20%,50%,30%,15%  # look closer at one area (small labels, logos)
```
Read the `image`. Pick the number whose outline is exactly the thing to hide (the label, not the whole bottle;
the logo, not the shirt). Each candidate has a ready `add` command with its exact box (source space). Several
pieces of one brand mark (logo + wordmark) = one mask per piece, or one with `;`-joined canvas boxes.
No SAM (`mask find` says so) or a no-vision model: see "Without SAM" below.

**2. Add it** with the candidate's command, plus the style:
`uv run vedit --json mask add --at 12.4 --space source --box 31.2%,40.1%,12.0%,8.0% --style remove`.
The default tracker `auto` uses SAM 2 video ("segment") when SAM is installed: it finds the object again in
every frame, so it follows turning (another side shows), resizing, perspective, shape changes, things passing in
front, and objects coming into view from the frame edge.

**3. Check it** (tracks it first if needed; ~1-2 s per second of video, one-time):
```
uv run vedit --json mask check mk1        # or: mask check all
```
It returns:
- `motion`: how the object moves (size range, turning, shape change, speed, time at the frame edge, visible share).
  Use it to judge risk: big size change, shape change > 30% or edge time = watch those stretches closely.
- `hidden`: stretches with no mask, and why (not in the shot, gone / cut, hidden behind something).
- `issues`: frames that look wrong (size / position jumps, SAM's own outline disagreeing with the tracked one).
- `verdict` good / check / poor, `advice` with exact commands, and `sheet`: a picture of up to 12 frames with the
  tracked outline drawn (green ok, red flagged, yellow = SAM's own outline where it disagrees) and a 10% grid.

**Always Read the sheet**, even when the verdict is good: the outline must hug the thing in every tile (no letters
outside it, not covering the product next to it). A tile with no outline while the thing is still visible is a miss.

**4. Fix what's wrong**, at the moment it's wrong, then check again:
```
uv run vedit --json mask fix mk1 --at 14.2 --space source --box 33%,41%,11%,9%   # where the object really is then
uv run vedit --json mask check mk1
```
- Read the box off the sheet's grid (source %, top-left + size), or run `mask find --at 14.2` and use the
  matching candidate's box. One fix guides the tracker from that moment on, so fix the **first** bad moment of a
  bad stretch, re-check, and repeat. Usually 0-2 fixes.
- `mask fix mk1 --remove-at 14.2` drops one fix; `--clear` drops them all.
- A mask on `outline` / `box` that the check calls poor: `mask set mk1 --tracker segment` first, then check.
- Stop after 3 rounds of fixes on one mask and tell the user what's still off (with the time).

**5. Look at the result** in the edit: `frame --at` on 2-3 of the risky moments from the check (largest, fastest,
nearest the edge). For `remove`, the rebuilt surface must match its surroundings.

### Without SAM

`mask find` / the SAM re-check need the `mask-ai` extra (~156 MB model): ask before installing (`--sam on`
installs it). Without it: go to the clearest moment with `frame --at T`, read the box off the frame (canvas %),
make it generous (the mask adds 10% margin), use `;`-joined boxes for odd shapes, and still run `mask check`
(motion profile, jumps, sheet). No vision at all: `--object NAME` (person, car, bottle, cell phone, laptop, tv,
book, `any`) or ask the user to mark it (**Mask** button in the timeline toolbar: Brush / Grid / Box).

### Trackers (`--tracker`)

| Tracker | How | Use |
|---|---|---|
| `auto` (default) | `segment` when SAM is installed, else `outline` | always, unless there's a reason below |
| `segment` | SAM 2 video: re-finds the object in every frame, from a memory of how it looked | turning, resizing, shape changes, entering from the edge, things in front |
| `outline` | follows the outline point by point (optical flow + one perspective fit) | flat things on a steady surface when SAM isn't installed; faster |
| `box` | older box tracker | old masks only |

Painted masks (`--paint`) keep the painted shape as the start; `segment` tracks it like any other.

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

Masks: the procedure above (`mask check`, Read the sheet, `mask fix`, check again). Focus: `frame --at` at the
start, middle and end. If a mask still drifts on a fast whip pan after fixes, split the clip there and mask each part.

---
name: 3d
description: 3D objects that turn in perspective and move - 3D text / titles, cars, trucks, buses, planes, rockets, dogs, cats, birds, fish, cubes, spheres, gems, coins, hearts, stars, or the user's own .glb / .obj model. Spin them 360, flip them, move them from point A to B (once, repeat, back and forth, or keep going). Use when the user asks for anything "3D", a spinning / rotating title or logo, a car driving by, an animal walking / running across, a plane / bird flying through, a coin flip, a rocket launch, or when an iso world should become a real turning object.
---

# 3D objects

`uv run vedit --json 3d add <kind> ...` adds one 3D object as a layer. It is generated from code (no download),
drawn low-poly with flat light, in perspective, by the same renderer for the preview and the export. Then give it
motion with `3d spin` (round and round) and `3d move` (A -> B). `vedit 3d kinds` lists every kind with its settings.

## Pick the kind

| Moment | Command |
|---|---|
| Hook / title that spins in | `3d add text --text "NEW DROP" --font anton --y 25% --color '#FFD447' --color2 '#FF6B3D'` (spins in by default) |
| Logo-like word turning slowly | `3d add text --text BRAND --no-intro` + `3d spin <id> --period 6` or `--sway 25` |
| "Fast", delivery, travel, cars | `3d add car --param style=sport --y 70% --width 520` + `3d move <id> --from -10%,70% --to 110%,70% --loop continue --dur 2.5` |
| Big move / logistics | `truck` or `bus`, same `move` |
| Travel / flights / "taking off" | `plane` + `move --from -15%,30% --to 115%,20% --loop continue` |
| Launch / growth / "to the moon" | `rocket` (points up) + `move --from 50%,110% --to 50%,-10% --no-face --loop repeat --dur 2.5` |
| Pets, fun, kids, cute | `dog` / `cat` + `move ... --hop 8 --loop continue` (legs walk, tail wags) |
| Freedom, nature, light | `bird` + `move ... --hop 25` (wings flap) |
| Water / food / playful | `fish` + `move --loop pingpong` (turns round at each end) |
| Money, rewards, prices | `coin` + `3d spin <id> --axis x --period 1.2` (flip) |
| Premium, value, diamonds | `gem` + `3d spin --period 4` |
| Likes, love, ratings | `heart` / `star` + `3d spin --period 2` or `--sway 30` |
| Their own product / mascot | `3d add model --src product.glb` (needs the `ai` extra; ask first, it installs once) |

## One-step signature moves

- `fx apply title_3d --at T --text "NEW DROP"`: 3D title spins in, sways, pops out (3 s).
- `fx apply drive_by_3d --at T --text 'car #E11D48'`: crosses the frame left to right, facing its way (2.4 s; car,
  truck, bus, plane, rocket (bottom to top), dog, cat, bird, fish).
- `fx apply spin_3d --at T --text 'gem #5EEAD4'`: hero object turning in the middle (coin flips on x).
Use these for a fast result, then refine with `3d set` / `3d move` / `3d spin` on the layer they made.

## Motion

- `3d spin <id> --axis y|x|z|yx|xyz --period S`: 360 every S seconds (negative = the other way). `y` = turntable,
  `x` = flip (coins), `z` = roll, `xyz` = tumble (falling, floating in space). `--sway DEG` turns back and forth instead.
- `3d move <id> --from x,y --to x,y`: px or `NN%` of the canvas. `--loop once` (default; eases in and out over the
  layer's length, then stays at B), `repeat` (A -> B again and again), `pingpong` (there and back, turning round),
  `continue` (keeps going past B at the same speed and comes back in from the other side: drive-bys, runners).
  `--dur` = seconds A -> B. Vehicles and animals face the way they go (`--no-face` for rockets, text).
  `--hop 8` = steps / bounces (px), `--scale-from 0.6 --scale-to 1.3` = comes nearer (or goes away).
- Resting turn: `--yaw` (round its up axis; 0 = side on, facing right; default -25 = three-quarter view),
  `--pitch` (tip towards the camera, default 18; 60+ = from above), `--roll`.
- Anything else is keyframes on `yaw` / `pitch` / `roll` (degrees) plus x / y / scale, e.g.
  `animate keyframes <id> '[{"t":0,"yaw":-90,"ease":"back_out"},{"t":1.2,"yaw":0}]'`.
- `spin3d_in` (on by default, `--no-intro` to skip) spins it in while it grows.

## Make it yours (creativity = settings, not new files)

- Colours: `--color` (main) / `--color2` (second: windows, belly, letter sides, fins). Pick from the edit's palette.
- `--param`: car `style=sedan|sport|van spoiler=true length=4.5`; dog `ears=pointy tail=0.9`; bird `flap=0.2`;
  gem `facets=6`; star `points=6 inner=0.5`; sphere `segments=6 rings=4` (chunkier = more low-poly)...
- Text: `--font` (any `vedit fonts` key; chunky fonts read best: anton, archivo-black, poppins-black, titan, bungee),
  `--depth 0.2-0.8`, `--param smooth=2` (faceted letters) or `smooth=8` (round).
- Look: `3d set <id> --fov 15` (flat, product shot) to `--fov 70` (dramatic close perspective), `--light 0.2` (flat,
  cartoon) to `0.9` (deep shadows), `--outline none` (no edge lines).
- Size: `--width` is the box it turns in (px). Text fills the width; a car ~45-60% of the canvas width reads well.

## Rules

- One 3D hero moment per 10-15 s; don't stack several moving 3D objects on talking-head footage.
- Keep it inside the safe zone while it matters (`safe check`); `continue` paths may start / end off-canvas on purpose.
- Text on top of a busy 3D object needs contrast like any text (the audit checks it).
- After adding, look: `vedit frame --at T` at a few moments of the move (start, middle, end) and Read them.
- Iso worlds (`iso add`) are for whole miniature scenes; 3D objects are single things that turn and move. A 3D car
  can drive over an iso city: put it on a track above the iso layer.

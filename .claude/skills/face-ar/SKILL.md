---
name: face-ar
description: Face mesh and AR effects in vedit - tracked 3D face mesh wireframe, stickers that follow the face (glasses, hats, masks), 3D props attached to the head, and face blur/pixelate for privacy. Use when the user asks for face filters, AR, face mesh, tracking, stickers on the face, 3D objects on the head, or hiding faces.
---

# Face mesh / AR

Needs the extra (one time):
```
uv sync --extra ai
```
- Uses MediaPipe Face Landmarker: 478 3D landmarks plus head pose for each frame.
- Tracking runs once per source and is cached. All face effects reuse it.

## Effects

- **Wireframe mesh** (sci-fi / tech look):
  ```
  uv run vedit --json face mesh --color "#00E5FF" --glow 6
  ```
  - `--dots` draws the landmark points.
  - `--opacity 0.5` makes it subtle.
- **Sticker that follows the face:**
  ```
  uv run vedit --json face sticker glasses.png --anchor eyes
  uv run vedit --json face sticker hat.png --anchor head --size 1.1 --offset-y -0.1
  ```
  - Anchors: `eyes`, `head`, `nose`, `mouth`, `face`.
  - `--size` scales the sticker. `--offset-x/--offset-y` are fractions of face width and rotate with the head.
  - Stickers stack: run the command again with another PNG.
- **3D prop on the head** (software-rendered, flat shaded):
  ```
  uv run vedit --json face prop crown.obj --size-cm 14 --y-cm 12 --spin 45 --color "#FACC15"
  ```
  - Face space units are cm. The origin is near the face centre, `y` is up and `z` points toward the camera. `--y-cm 12` sits above the head.
  - `--rot-x/y/z` orient the model. `--spin` is degrees per second.
  - Simple, low-poly models (under 5k triangles) render fastest. **Ask before** using large models on long clips.
- **Privacy blur:**
  ```
  uv run vedit --json face blur
  uv run vedit --json face blur --style pixelate --strength 24 --faces 5
  ```
- **Remove all face effects:**
  ```
  uv run vedit --json face clear
  ```
  Use `--clip ID` to target one clip.

## Tips

- `--faces N` tracks up to N people. The default is 1, and 3 for blur.
- Landmarks are smoothed over time (`params.smooth`, default 0.4). Raise it if stickers jitter, lower it if they lag.
- If no face is found in a frame, nothing is drawn on that frame.
- Always check with `frame --at T` and Read the image. Stickers often need a `--size` or `--offset-y` adjustment.
- Find free stickers (PNG with transparency) and low-poly models (.obj/.glb) that the user has rights to use. Don't download copyrighted assets.

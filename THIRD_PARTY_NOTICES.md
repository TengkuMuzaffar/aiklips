# Third-party notices

aiklips itself is released under the [MIT License](LICENSE). It uses the open-source components below. Apart from
the fonts in `assets/fonts/`, none of them is copied into this repository: Python packages are installed from PyPI (each ships its own licence file),
and model weights are downloaded from their publishers on first use into `~/.cache/aiklips/models`. All of them
allow commercial use. If you redistribute aiklips together with these components (for example a bundled
installer), include their licence texts and notices as their licences require.

## Optional AI features (extras)

| Component | Used for | Licence | Source |
|---|---|---|---|
| **SAM 2.1 tiny** (Segment Anything Model 2, Meta) — code (`sam2` package) and the `sam2.1_hiera_tiny.pt` checkpoint | Exact object outlines for masks and focus (`mask-ai` extra) | Apache-2.0. The package also bundles a connected-components helper under BSD-3-Clause (`LICENSE_cctorch`). The only non-Apache parts of the SAM 2 project are fonts in Meta's web demo, which aiklips doesn't use. | https://github.com/facebookresearch/sam2 |
| PyTorch (`torch`) | Runs SAM (`mask-ai`) | BSD-3-Clause style (plus bundled third-party components under Apache-2.0, MIT, BSD-2, BSL-1.0, ISC, Zlib) | https://github.com/pytorch/pytorch |
| torchvision | SAM dependency | BSD-3-Clause | https://github.com/pytorch/vision |
| hydra-core, iopath | SAM configuration / file loading | MIT | https://github.com/facebookresearch/hydra, https://github.com/facebookresearch/iopath |
| omegaconf, antlr4-python3-runtime, portalocker | SAM dependencies | BSD-3-Clause | PyPI |
| MediaPipe + EfficientDet-Lite0 detector, selfie segmentation | Object detection by name, fast background removal, face mesh (`ai` extra) | Apache-2.0 | https://github.com/google-ai-edge/mediapipe |
| OpenCV (`opencv-contrib-python`) | Tracking, optical flow, image processing (`ai` extra) | Apache-2.0 | https://github.com/opencv/opencv |
| trimesh | 3D face props and importing your own 3D models into 3D object layers (`ai` extra) | MIT | https://github.com/mikedh/trimesh |
| rembg + BiRefNet | High-quality background removal (`bg-quality` extra) | MIT | https://github.com/danielgatis/rembg |
| faster-whisper + Whisper models | Offline captions (`captions` extra) | MIT | https://github.com/SYSTRAN/faster-whisper |

## Core

| Component | Licence |
|---|---|
| Claude Agent SDK (`claude-agent-sdk`) | MIT. It runs Claude Code, whose use is governed by Anthropic's own terms; models from other providers are governed by those providers' terms. |
| fontTools (glyph outlines for 3D text) | MIT |
| FastAPI, uvicorn, Typer, pydantic, Pillow (MIT-CMU), NumPy (BSD-3-Clause), pywebview (BSD-3-Clause), watchfiles, websockets | Permissive (MIT / BSD) |
| FFmpeg | aiklips runs the `ffmpeg` / `ffprobe` programs and does not link to them. The installer uses the system's FFmpeg 6+ or else downloads the static 6.1.1 builds from github.com/eugeneware/ffmpeg-static, which are **GPL**: they are separate programs fetched from their publisher, and anyone redistributing them (for example inside a bundled installer) must follow the GPL, including offering the source. |
| Fonts in `assets/fonts/` (these *are* in the repository) | SIL Open Font License 1.1, except Luckiest Guy and Permanent Marker (Apache-2.0). Each font's licence text is in `assets/fonts/licenses/`. |

When adding a dependency or a model, check its licence allows commercial use and add it here. Not allowed for
aiklips: non-commercial licences (for example CC BY-NC, the S-Lab licence used by ProPainter, or CoTracker's
CC BY-NC).

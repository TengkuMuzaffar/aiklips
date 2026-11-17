---
description: Start a new video project from one or more files
argument-hint: <name> <video files...>
---
Create a new aiklips project from: $ARGUMENTS

1. The first word is the project name. The rest are file paths. If no files are given, ask for them.
2. Run `uv run vedit init projects/<name>`, then `uv run vedit --json import <files...>`.
3. Run `uv run vedit --json info`. Summarise the clips, their total length, and whether each source is vertical or landscape.
4. Suggest a first pass: silence-cut, a hook title, captions. Also suggest `uv run vedit serve` for the live preview.

---
description: Undo the last edit (or N edits)
argument-hint: [steps]
---
Run `uv run vedit --json undo --steps N`, where N is "$ARGUMENTS" (or 1 if it is empty). Then run `uv run vedit --json history --limit 8` and tell the user where they are now.

---
description: Edit the active video project from a plain-language request
argument-hint: <what you want, e.g. "remove pauses, add a hook title and captions">
---
Edit the active claude-cut project: $ARGUMENTS

Follow the workflow in CLAUDE.md:
1. `uv run vedit --json info`
2. Load the relevant skill(s).
3. Run small vedit commands.
4. Check the result with `frame` or `sheet` and Read the image.
5. Summarise the changes. Mention that they can say "undo".

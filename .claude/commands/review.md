---
description: Visually review the current edit and suggest improvements
---
1. Run `uv run vedit --json sheet --count 12`. Read the image.
2. Run `uv run vedit --json info`.
3. Review the edit like a short-form video editor:
   - Is the hook in the first 2 seconds?
   - Is the text readable and inside the safe zones?
   - Is the pacing tight (any leftover pauses)?
   - Are the captions present?
   - Is there an ending CTA?
4. List concrete fixes as vedit commands, and ask which to apply.

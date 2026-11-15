---
name: transitions
description: Add animated transitions between clips (crossfade, dip to black/white, slide, push, wipe, zoom, whip, circle, pixelize...). Use when the user asks for transitions, smoother cuts, fades between shots, swipe/slide effects, or "make the cuts less harsh".
---

# Transitions

```
uv run vedit --json transition fade                       # every cut, 0.5s
uv run vedit --json transition slideleft --clip c3 --duration 0.4   # only the cut INTO c3
uv run vedit --json transition none [--clip c3]           # back to hard cuts
uv run vedit --json transition list
```

- A transition belongs to the clip it leads **into**. The first clip can't have one.
- The blend is **centred on the cut** and uses spare footage before/after each clip (or holds a frame).
  So the timeline length, captions and overlay times **don't change**. You can add transitions at any point.
- Splitting a clip never adds a transition inside it.

## Which one

| Feel | Transition | Duration |
|---|---|---|
| Talking head, same scene | none (hard cut) or `fade` | 0.2–0.3 |
| Calm / lifestyle / product | `fade`, `dissolve` | 0.5–0.8 |
| Scene / topic change | `fadeblack` (dip) | 0.6–1.0 |
| Energetic / TikTok | `slideleft`, `smoothleft`, `hblur` (whip), `zoomin` | 0.25–0.4 |
| Reveal / before-after | `wipeleft`, `circleopen`, `revealleft` | 0.4–0.6 |
| Flash beat drop | `fadewhite` | 0.2–0.3 |

Aliases: crossfade, dip, flash, whip, push, slide, wipe, zoom, iris, glitch.

Don't mix many styles in one video: one main transition, maybe one accent at the hook or ending.
Check with `frame --at <cut time>` (mid-transition).

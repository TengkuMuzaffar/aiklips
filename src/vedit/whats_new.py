"""Release highlights shown once after an update ("What's new"), newest first.

Add an entry when a release has something a user would notice. Keep it short: title + one sentence.
"""

from __future__ import annotations

from . import __version__
from .updater import parse_version

NOTES: list[dict] = [
    {"version": "0.2.2", "items": [
        {"icon": "spark", "title": "Design studio", "body": "Make motion-design videos on a canvas: scenes of shapes, text and media you stretch, rotate and animate with presets or keyframes."},
        {"icon": "wand", "title": "Storyboard with any AI", "body": "Turn a brief into scenes, design a scene from its notes, or animate a selection, with whichever AI model you've connected."},
        {"icon": "film", "title": "Beta labels", "body": "References and Generate are marked beta while they keep improving."},
        {"icon": "film", "title": "References live in your Media", "body": "Any video in your Media can be a style reference: no second upload. Your existing references were moved there, with their style kept."},
        {"icon": "list", "title": "Tell Generate who it's for", "body": "Pick the funnel stage, the audience, a detailed pace, the tone and must-dos with one click each."},
    ]},
    {"version": "0.2.1", "items": [
        {"icon": "home", "title": "A new home", "body": "Start a project from a format or by dropping a video, and find recent work in a grid or list."},
        {"icon": "spark", "title": "Welcome tour", "body": "First-run setup that connects your AI and walks you through the editor. Replay it from Help."},
        {"icon": "download", "title": "Friendlier installer", "body": "A setup window with real progress, and a faster first install."},
        {"icon": "gear", "title": "Settings, reorganised", "body": "General, AI, permissions, usage and updates, all in one place."},
        {"icon": "list", "title": "Plan, ask or auto", "body": "Have the AI plan an edit for your approval, approve each change, or let it edit straight away."},
        {"icon": "key", "title": "ChatGPT models", "body": "Use OpenAI (or any OpenAI-compatible server) for the AI editor with your own API key."},
        {"icon": "brush", "title": "Masks that hug the object", "body": "Masks follow the object's own outline point by point, turn with it, and stop when it leaves the shot. Add SAM for exact outlines from a rough box."},
        {"icon": "brush", "title": "Masks that keep up", "body": "With SAM installed, masks find the object again in every frame: they follow it as it turns, grows or comes into view. The AI picks the exact object, checks the mask frame by frame and fixes the moments it slips."},
        {"icon": "play", "title": "Scrub the preview", "body": "Drag along the bar under the preview to watch any second, even while the AI is editing."},
        {"icon": "spark", "title": "See what the AI changed", "body": "What the AI edits glows on the timeline, which scrolls to it. Step through its changes with the chip in the timeline header. While playing, the timeline follows the playhead."},
    ]},
    {"version": "0.2.0", "items": [
        {"icon": "key", "title": "Green screen in one step", "body": "Keys out the screen colour, cleans the fringe and drops in any background."},
        {"icon": "brush", "title": "Brush to highlight", "body": "Paint over an object and it's tracked with a glow, outline, colour pop or spotlight."},
        {"icon": "refresh", "title": "Updates by themselves", "body": "New versions download in the background and install when you restart."},
    ]},
]


def since(seen: str | None, current: str = __version__) -> list[dict]:
    """Highlights of the versions after `seen`, up to the running one (all of them for a fresh install: none)."""
    if not seen:
        return []
    lo, hi = parse_version(seen), parse_version(current)
    return [n for n in NOTES if lo < parse_version(n["version"]) <= hi]

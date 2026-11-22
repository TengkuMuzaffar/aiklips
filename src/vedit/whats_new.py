"""Release highlights shown once after an update ("What's new"), newest first.

Add an entry when a release has something a user would notice. Keep it short: title + one sentence.
"""

from __future__ import annotations

from . import __version__
from .updater import parse_version

NOTES: list[dict] = [
    {"version": "0.2.6", "items": [
        {"icon": "list", "title": "Captions you can edit", "body": "Captions show in the live preview, can be dragged into place, and each phrase is its own text layer in a captions group."},
        {"icon": "sliders", "title": "Pick what to install", "body": "Choose the features you want when installing, and remove aiklips cleanly with the new uninstaller."},
    ]},
    {"version": "0.2.5", "items": [
        {"icon": "film", "title": "Your media library in the editor", "body": "The Media tab shows everything you uploaded, already studied. Drag it in, and the AI editor can use it too."},
        {"icon": "spark", "title": "Generate uses your AI", "body": "With DeepSeek or another provider set up, Generate now uses it instead of asking for a Claude login, and shows the real error if a call fails."},
        {"icon": "monitor", "title": "Dark on Windows", "body": "A dark title bar, no grey menu strip and dark dialogs: aiklips looks the same on Windows as on Mac."},
    ]},
    {"version": "0.2.4", "items": [
        {"icon": "monitor", "title": "The window shows on Windows", "body": "aiklips started but its window stayed invisible on Windows. It now opens in front."},
    ]},
    {"version": "0.2.3", "items": [
        {"icon": "monitor", "title": "Windows opens again", "body": "aiklips 0.2.2 closed right after starting on Windows. Fixed."},
    ]},
    {"version": "0.2.2", "items": [
        {"icon": "film", "title": "Media library", "body": "Every file you add is studied once (shots, motion, light, speech) and ready in any project or generation."},
        {"icon": "spark", "title": "References live in your Media", "body": "Any video in your Media can be a style reference, with no second upload. Your existing references were moved there, their style kept."},
        {"icon": "list", "title": "Tell Generate who it's for", "body": "Pick the funnel stage, the audience, a detailed pace, the tone and must-dos with one click each."},
        {"icon": "wand", "title": "Signature effects", "body": "Snap zooms, checklists, liquid glass, orbiting words, dolly zoom and more, each one a single move grouped on the timeline."},
        {"icon": "spark", "title": "3D objects", "body": "3D titles, cars, animals and shapes that turn in perspective, spin and travel across the frame."},
        {"icon": "sliders", "title": "Motion graphics", "body": "Kinetic text, counters, charts that grow, lines that draw themselves and shapes that morph."},
        {"icon": "play", "title": "Sound effects and music loops", "body": "Make whooshes, hits and ambiences, or an original music loop in a style you pick, right in the editor."},
        {"icon": "layers", "title": "Tracks and elements", "body": "The video track can sit among the other tracks, with colour screens and shapes from the new Elements tab."},
        {"icon": "monitor", "title": "Safe zones", "body": "Set TikTok, Reels or Shorts and see what their buttons cover; text is kept out of the way."},
        {"icon": "brush", "title": "Resize in the preview", "body": "Drag to resize, stretch and rotate anything right on the video, and style text with bold, lists and alignment."},
        {"icon": "film", "title": "Beta labels", "body": "References and Generate are marked beta while they keep improving."},
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

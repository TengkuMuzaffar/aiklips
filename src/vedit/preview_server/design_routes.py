"""HTTP API for Design: the Design studio (static/studio.html) and the home screen's Design tab.

  /api/design/options              styles + formats for the studio
  POST /api/design/new             a new blank design project (opened at once)
  GET  /api/design/projects        the user's designs (home screen)
  POST /api/design/storyboard      AI: brief -> proposed scenes (nothing changes yet)
  POST /api/design/storyboard/apply   make the proposed scenes real (one undo step)
  POST /api/design/scene           AI: design one scene from its notes (one undo step)
  POST /api/design/animate         AI: animate the selected elements (one undo step)

The AI calls use whichever model the body names (the studio's model picker: any connected provider), else the
user's saved default for that role (`vedit ai-models`), else the quality tier. Every edit itself goes through the
editor actions (/api/op), like the rest of the app.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .. import library
from ..ai.client import AIError, AIUnavailable, RateLimited
from ..project import PROJECT_FILE, VeditError


def mount(app: FastAPI, host, bad) -> None:
    from ..design import studio_ai
    from ..design.spec import STYLES

    def ai_error(e: Exception) -> HTTPException:
        if isinstance(e, RateLimited):
            return HTTPException(429, f"The AI provider is rate-limiting: try again in a minute ({e})")
        if isinstance(e, AIUnavailable):
            return HTTPException(503, f"No AI model is available: connect one in Settings → Providers ({e})")
        return HTTPException(502, str(e))

    async def run_ai(fn, *a):
        try:
            return await asyncio.to_thread(fn, *a)
        except VeditError as e:
            raise bad(e)
        except (AIError, AIUnavailable, RateLimited) as e:
            raise ai_error(e)

    @app.get("/api/design/options")
    def design_options() -> dict:
        return {"styles": [{"id": k, "about": v} for k, v in STYLES.items()], "formats": list(library.FORMATS)}

    @app.post("/api/design/new")
    async def design_new(body: dict) -> dict:
        """A new design project: one empty scene on a canvas of the chosen format, opened in the studio."""
        from .. import history
        from ..ops import scenes
        from ..project import Project, save

        fmt = body.get("format") or "reels"
        if fmt not in scenes.FORMATS:
            raise HTTPException(400, f"format must be one of {', '.join(scenes.FORMATS)}")
        name = str(body.get("name") or "").strip() or "Untitled design"
        try:
            row = library.create(name, fmt, float(body.get("fps") or 30))
            d = Path(row["path"])
            p = Project(name=name)
            scenes.new_design(p, d, fmt, name, float(body.get("dur") or 5), body.get("background") or "#101014")
            save(d, p)
            history.commit(d, p, f"new design ({fmt})")
            await host.open(d)
        except VeditError as e:
            raise bad(e)
        return library.get(row["id"]) | {"kind": "design"}

    @app.get("/api/design/projects")
    def design_projects() -> dict:
        out = []
        for row in library.list_projects():
            try:
                data = json.loads((Path(row["path"]) / PROJECT_FILE).read_text())
            except (OSError, ValueError):
                continue
            if data.get("kind") == "design":
                out.append(row | {"scenes": len(data.get("scenes") or [])})
        return {"projects": out}

    @app.post("/api/design/storyboard")
    async def design_storyboard(body: dict) -> dict:
        return await run_ai(studio_ai.storyboard, host.need(), str(body.get("brief") or ""), float(body.get("length") or 20),
                            body.get("style"), body.get("model"))

    @app.post("/api/design/storyboard/apply")
    async def design_storyboard_apply(body: dict) -> dict:
        try:
            return studio_ai.apply_storyboard(host.need(), list(body.get("scenes") or []), body.get("mode") or "replace")
        except VeditError as e:
            raise bad(e)

    @app.post("/api/design/scene")
    async def design_scene(body: dict) -> dict:
        return await run_ai(studio_ai.design_scene, host.need(), str(body.get("scene") or ""), body.get("model"),
                            str(body.get("extra") or ""))

    @app.post("/api/design/animate")
    async def design_animate(body: dict) -> dict:
        return await run_ai(studio_ai.animate, host.need(), list(body.get("layers") or []), str(body.get("intent") or ""),
                            body.get("model"))

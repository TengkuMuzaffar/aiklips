"""claude-cut app server: home menu (project library) + 4-pane editor.

  /            home: list / create / open / rename / duplicate / remove projects
  /editor      the editor for the currently open project
  /api/projects...   project library (SQLite, see vedit.library)
  /api/state, /api/op, /api/import, /api/export ...   editor API for the open project
  /ws/chat     AI chat pane (Claude Agent SDK)

The server watches the open project's project.json and re-renders the preview after
every change, whether it came from the GUI, the AI or the CLI.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse

from .. import history, library, media
from ..project import PROJECT_FILE, VeditError, load, resolve_media, state_dir
from ..render.runner import preview_dir, render_preview

STATIC = Path(__file__).parent / "static"


class Renderer:
    """Re-renders the preview whenever project.json changes."""

    def __init__(self, pdir: Path, watch: bool = True):
        self.pdir = pdir
        self.version = 0
        self.status = "idle"
        self.error: str | None = None
        self.pending = threading.Event()
        self.stopped = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()
        if watch:
            threading.Thread(target=self._watch, daemon=True).start()
        self.pending.set()

    def stop(self) -> None:
        self.stopped.set()
        self.pending.set()

    def _loop(self) -> None:
        while not self.stopped.is_set():
            self.pending.wait()
            if self.stopped.is_set():
                return
            time.sleep(0.2)  # debounce bursts of edits
            self.pending.clear()
            self.status = "rendering"
            self.version += 1
            try:
                p = load(self.pdir)
                if p.clips:
                    render_preview(p, self.pdir, progress=False)
                self.error = None
            except VeditError as e:
                self.error = str(e)
            except Exception as e:  # keep the server alive
                self.error = f"{type(e).__name__}: {e}"
            self.status = "idle"
            self.version += 1

    def _watch(self) -> None:
        from watchfiles import watch

        for changes in watch(self.pdir, recursive=False, stop_event=self.stopped):
            if any(Path(p).name == PROJECT_FILE for _, p in changes):
                self.pending.set()


class Host:
    """Holds the currently open project (one at a time) and its renderer + AI chat."""

    def __init__(self, watch: bool, chat: bool):
        self.watch, self.chat = watch, chat
        self.pdir: Path | None = None
        self.renderer: Renderer | None = None
        self.session = None

    async def open(self, pdir: Path) -> None:
        pdir = pdir.resolve()
        if not (pdir / PROJECT_FILE).exists():
            raise VeditError(f"No project at {pdir}")
        if self.pdir == pdir:
            library.touch(pdir)
            return
        await self.close()
        self.open_now(pdir)

    def open_now(self, pdir: Path) -> None:
        """Open a project when nothing else is open (no async cleanup needed)."""
        pdir = pdir.resolve()
        library.touch(pdir)
        self.pdir = pdir
        self.renderer = Renderer(pdir, self.watch)
        if self.chat:
            from ..editor.agent import ChatSession

            self.session = ChatSession(pdir)

    async def close(self) -> None:
        if self.renderer:
            self.renderer.stop()
        if self.session:
            await self.session._reset()
        self.pdir, self.renderer, self.session = None, None, None

    def need(self) -> Path:
        if self.pdir is None:
            raise HTTPException(409, "No project is open. Open one from the home screen.")
        return self.pdir


def _thumb(pdir: Path, sid: str) -> Path:
    p = load(pdir)
    if sid not in p.sources:
        raise HTTPException(404)
    src = p.sources[sid]
    if not src.has_video:
        raise HTTPException(404)
    d = state_dir(pdir) / "cache" / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    path = resolve_media(pdir, src.path)
    if not path.exists():
        raise HTTPException(404)
    out = d / f"{sid}-{int(path.stat().st_mtime)}.jpg"
    if not out.exists():
        media.extract_frame(path, min(1.0, src.duration / 3), out, 360)
    return out


def create_app(pdir: Path | None = None, watch: bool = True, chat: bool = True) -> FastAPI:
    app = FastAPI(title="claude-cut")
    host = Host(watch, chat)

    if pdir is not None:
        host.open_now(pdir)

    def bad(e: Exception) -> HTTPException:
        return HTTPException(400, str(e))

    # ---- pages -----------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return (STATIC / "home.html").read_text(encoding="utf-8")

    @app.get("/editor", response_class=HTMLResponse)
    def editor():
        if host.pdir is None:
            return RedirectResponse("/")
        return (STATIC / "index.html").read_text(encoding="utf-8")

    # ---- project library ------------------------------------------------------------
    @app.get("/api/projects")
    def projects() -> dict:
        return {"projects": library.list_projects(), "root": str(library.projects_root()),
                "open": str(host.pdir) if host.pdir else None, "formats": list(library.FORMATS)}

    @app.post("/api/projects")
    async def create_project(body: dict) -> dict:
        try:
            row = library.create(str(body.get("name") or ""), body.get("format") or "reels", float(body.get("fps") or 30))
        except (VeditError, ValueError) as e:
            raise bad(e)
        await host.open(Path(row["path"]))
        return row

    @app.post("/api/projects/register")
    async def register_project(body: dict) -> dict:
        try:
            pid = library.register(Path(str(body.get("path", ""))).expanduser())
        except VeditError as e:
            raise bad(e)
        return library.get(pid)

    @app.post("/api/projects/{pid}/open")
    async def open_project(pid: int) -> dict:
        try:
            row = library.get(pid)
            await host.open(Path(row["path"]))
        except VeditError as e:
            raise bad(e)
        return row

    @app.post("/api/projects/{pid}/rename")
    def rename_project(pid: int, body: dict) -> dict:
        try:
            return library.rename(pid, str(body.get("name", "")))
        except VeditError as e:
            raise bad(e)

    @app.post("/api/projects/{pid}/duplicate")
    def duplicate_project(pid: int) -> dict:
        try:
            return library.duplicate(pid)
        except VeditError as e:
            raise bad(e)

    @app.delete("/api/projects/{pid}")
    async def remove_project(pid: int, delete_files: bool = False) -> dict:
        try:
            row = library.get(pid)
            if host.pdir and Path(row["path"]).resolve() == host.pdir:
                await host.close()
            library.remove(pid, delete_files)
        except VeditError as e:
            raise bad(e)
        return {"ok": True}

    @app.get("/api/projects/{pid}/thumb.jpg")
    def project_thumb(pid: int):
        try:
            row = library.get(pid)
            d = Path(row["path"])
            p = load(d)
        except VeditError:
            raise HTTPException(404)
        if not p.clips:
            raise HTTPException(404)
        prev = preview_dir(d) / "preview.mp4"
        if prev.exists():  # poster of the actual edit (canvas, overlays and all)
            poster = state_dir(d) / "cache" / f"poster-{int(prev.stat().st_mtime)}.jpg"
            if not poster.exists():
                poster.parent.mkdir(parents=True, exist_ok=True)
                for old in poster.parent.glob("poster-*.jpg"):
                    old.unlink(missing_ok=True)
                try:
                    media.extract_frame(prev, min(1.0, p.duration / 3), poster, 360)
                except VeditError:
                    poster = None
            if poster:
                return FileResponse(poster, headers={"Cache-Control": "no-store"})
        return FileResponse(_thumb(d, p.clips[0].source), headers={"Cache-Control": "no-store"})

    @app.post("/api/close")
    async def close_project() -> dict:
        await host.close()
        return {"ok": True}

    # ---- editor (open project) ---------------------------------------------------------
    @app.get("/api/state")
    def api_state() -> dict:
        d = host.need()
        r = host.renderer
        p = load(d)
        return {
            "project_dir": str(d),
            "project": p.to_dict(),
            "duration": p.duration,
            "starts": p.clip_starts(),
            "history": history.log(d, 80),
            "render": {"status": r.status, "error": r.error, "version": r.version} if r else {"status": "idle", "error": None, "version": 0},
        }

    def _hist(fn, *a):
        try:
            return fn(host.need(), *a)
        except VeditError as e:
            raise bad(e)

    @app.post("/api/undo")
    def api_undo() -> dict:
        return _hist(history.undo)

    @app.post("/api/redo")
    def api_redo() -> dict:
        return _hist(history.redo)

    @app.post("/api/checkout/{step}")
    def api_checkout(step: int) -> dict:
        return _hist(history.checkout, step)

    @app.post("/api/op")
    def api_op(body: dict) -> dict:
        from ..editor import actions

        try:
            return actions.apply(host.need(), body.get("op", ""), body.get("args") or {})
        except VeditError as e:
            raise bad(e)
        except (KeyError, ValueError, TypeError) as e:
            raise HTTPException(400, f"Bad request: {e}")

    @app.post("/api/import")
    async def api_import(file: UploadFile = File(...), append: bool = Form(True), index: int | None = Form(None)) -> dict:
        from ..editor import actions

        d = host.need()
        suffix = Path(file.filename or "").suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=state_dir(d)) as tmp:
            while chunk := await file.read(1 << 20):
                tmp.write(chunk)
        try:
            return actions.import_file(d, file.filename or "upload" + suffix, Path(tmp.name), append, index)
        except VeditError as e:
            Path(tmp.name).unlink(missing_ok=True)
            raise bad(e)

    @app.post("/api/export")
    def api_export() -> dict:
        from ..render.runner import render

        d = host.need()
        p = load(d)
        dest = d / "exports" / f"{library.slugify(p.name)}.mp4"
        try:
            res = render(p, d, dest, 1.0, "export", progress=False)
        except VeditError as e:
            raise bad(e)
        media.open_file(dest.parent)
        return res

    @app.get("/media/{name}")
    def media_file(name: str):
        if name not in ("preview.mp4", "thumbs.jpg", "wave.png"):
            raise HTTPException(404)
        f = preview_dir(host.need()) / name
        if not f.exists():
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "no-store"})

    @app.get("/api/source/{sid}/thumb.jpg")
    def source_thumb(sid: str):
        return FileResponse(_thumb(host.need(), sid))

    @app.get("/api/events")
    async def events():
        async def gen():
            last = None
            while True:
                r, d = host.renderer, host.pdir
                if r is None or d is None:
                    yield f"data: {json.dumps({'closed': True})}\n\n"
                    return
                try:
                    mtime = (d / PROJECT_FILE).stat().st_mtime
                except FileNotFoundError:
                    mtime = 0
                key = (id(r), r.version, r.status, mtime)
                if key != last:
                    last = key
                    yield f"data: {json.dumps({'version': r.version, 'status': r.status, 'error': r.error})}\n\n"
                await asyncio.sleep(0.3)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket):
        await ws.accept()
        session = host.session
        if session is None:
            await ws.send_json({"type": "error", "text": "AI chat is unavailable (no project open, or chat disabled)."})
            await ws.close()
            return
        await session.attach(ws)
        try:
            while True:
                msg = await ws.receive_json()
                kind = msg.get("type")
                if kind == "ask" and str(msg.get("text", "")).strip():
                    asyncio.create_task(session.ask(str(msg["text"]).strip(), msg.get("context") or {}))
                elif kind == "interrupt":
                    await session.interrupt()
                elif kind == "model":
                    await session.set_model(str(msg.get("model", "")))
                elif kind == "reset":
                    await session.new_conversation()
        except WebSocketDisconnect:
            session.sockets.discard(ws)

    return app


def run(pdir: Path | None, host: str = "127.0.0.1", port: int = 5173, browser: bool = True) -> None:
    import uvicorn

    url = f"http://{host}:{port}" + ("/editor" if pdir else "")
    print(f"claude-cut  {url}\n(Ctrl+C to stop)")
    if browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(pdir), host=host, port=port, log_level="warning")

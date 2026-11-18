"""aiklips app server: home menu (project library) + 4-pane editor.

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
import os
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse

from .. import history, library, media
from ..project import PROJECT_FILE, VeditError, load, resolve_media, state_dir
from ..render.runner import preview_dir, render_preview

STATIC = Path(__file__).parent / "static"
SRC = Path(__file__).resolve().parents[1]  # src/vedit, watched by `vedit serve --dev`
# `--dev`: remembers the open project so a code reload drops you back into the same editor
DEV_OPEN = Path(tempfile.gettempdir()) / "aiklips-dev-open"
BOOT = str(time.time())  # changes every time the server (re)starts

# Injected into pages in dev mode: reloads the page when the server restarts or a static file changes.
DEV_SCRIPT = """<script>(() => {
  let last = null;
  setInterval(async () => {
    try {
      const s = await (await fetch("/api/dev/stamp", {cache: "no-store"})).text();
      if (last !== null && s !== last) location.reload();
      last = s;
    } catch (e) { /* server restarting: try again next tick */ }
  }, 700);
})();</script>"""


def _dev_stamp() -> str:
    return BOOT + ":" + str(max((p.stat().st_mtime for p in STATIC.iterdir()), default=0))


def _rev(pdir: Path) -> float:
    """Changes whenever project.json is written (by the GUI, the AI or the CLI)."""
    try:
        return (pdir / PROJECT_FILE).stat().st_mtime_ns / 1e9
    except FileNotFoundError:
        return 0.0


class Renderer:
    """Re-renders the preview whenever project.json changes."""

    def __init__(self, pdir: Path, watch: bool = True):
        self.pdir = pdir
        self.version = 0
        # live mode (default): render base.mp4 without overlay layers; the browser draws them from sprites.
        # final mode: render the full preview.mp4 exactly as it will export.
        self.final = False
        self.passes = 0  # finished render passes (cache hits too): tells the browser the edit has been handled
        self._shown_final: bool | None = None
        self.status = "idle"
        self.error: str | None = None
        self.pending = threading.Event()
        self.stopped = threading.Event()
        self.proxy_kick = threading.Event()
        self.proxy_status = ""  # e.g. "proxy 1/3" while building
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._proxies, daemon=True).start()
        if watch:
            threading.Thread(target=self._watch, daemon=True).start()
        self.pending.set()
        self.proxy_kick.set()

    def stop(self) -> None:
        self.stopped.set()
        self.pending.set()
        self.proxy_kick.set()

    def _proxies(self) -> None:
        """Build small proxies of big sources in the background, so previews of 4K footage decode fast."""
        from .. import proxies

        while not self.stopped.is_set():
            self.proxy_kick.wait()
            self.proxy_kick.clear()
            if self.stopped.is_set():
                return
            try:
                p = load(self.pdir)
                todo = [s for s in p.sources.values() if proxies.needs_proxy(s) and not proxies.available(self.pdir, s)]
                for i, src in enumerate(todo):
                    if self.stopped.is_set():
                        return
                    self.proxy_status = f"proxy {i + 1}/{len(todo)}"
                    proxies.make(self.pdir, src, should_stop=self.stopped.is_set)
            except Exception:  # a broken file must not stop the editor; previews just use the original
                pass
            self.proxy_status = ""

    def _loop(self) -> None:
        while not self.stopped.is_set():
            self.pending.wait()
            if self.stopped.is_set():
                return
            self.proxy_kick.set()  # new sources (imports) get proxies too
            time.sleep(0.15)  # debounce bursts of edits
            self.pending.clear()
            self.status = "rendering"
            fresh, final = False, self.final
            try:
                p = load(self.pdir)
                if p.clips:
                    # a newer edit makes this render stale: stop FFmpeg now and start over
                    res = render_preview(p, self.pdir, progress=False, should_stop=self.pending.is_set, base=not final)
                    fresh = not res.get("cached")
                self.error = None
            except media.Cancelled:
                continue  # `pending` is set: the loop renders the newer edit straight away
            except VeditError as e:
                self.error = str(e)
            except Exception as e:  # keep the server alive
                self.error = f"{type(e).__name__}: {e}"
            self.status = "idle"
            # the version only moves when there is a new file to load (or on the first pass), so the
            # browser never re-downloads an unchanged preview
            if fresh or self.version == 0 or self._shown_final != final:
                self.version += 1
                self._shown_final = final
            self.passes += 1

    def _watch(self) -> None:
        from watchfiles import watch

        for changes in watch(self.pdir, recursive=False, stop_event=self.stopped):
            if any(Path(p).name == PROJECT_FILE for _, p in changes):
                self.pending.set()


class Host:
    """Holds the currently open project (one at a time) and its renderer + AI chat."""

    def __init__(self, watch: bool, chat: bool, dev: bool = False):
        self.watch, self.chat, self.dev = watch, chat, dev
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
        if self.dev:
            DEV_OPEN.write_text(str(pdir), encoding="utf-8")
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
        if self.dev:
            DEV_OPEN.unlink(missing_ok=True)

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


def create_app(pdir: Path | None = None, watch: bool = True, chat: bool = True, dev: bool = False) -> FastAPI:
    app = FastAPI(title="aiklips")
    host = Host(watch, chat, dev)

    if pdir is not None:
        host.open_now(pdir)

    def bad(e: Exception) -> HTTPException:
        return HTTPException(400, str(e))

    # ---- pages -----------------------------------------------------------------
    def page(name: str) -> str:
        html = (STATIC / name).read_text(encoding="utf-8")
        return html.replace("</body>", DEV_SCRIPT + "</body>", 1) if dev else html

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return page("home.html")

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page() -> str:
        return page("settings.html")

    @app.get("/editor", response_class=HTMLResponse)
    def editor():
        if host.pdir is None:
            return RedirectResponse("/")
        return page("index.html")

    if dev:
        @app.get("/api/dev/stamp", response_class=Response)
        def dev_stamp() -> Response:
            return Response(_dev_stamp(), media_type="text/plain", headers={"Cache-Control": "no-store"})

    STATIC_TYPES = {"animate.js": "text/javascript", "shell.js": "text/javascript", "ui.css": "text/css",
                    "logo.svg": "image/svg+xml"}

    @app.get("/static/{name}")
    def static_file(name: str):
        if name == "display.ttf":  # the UI's italic display face (bundled for text layers too)
            from ..ops import fonts as font_lib

            return FileResponse(font_lib.FONT_DIR / "DMSerifDisplay-Italic.ttf", media_type="font/ttf",
                                headers={"Cache-Control": "max-age=86400"})
        if name not in STATIC_TYPES:
            raise HTTPException(404)
        return FileResponse(STATIC / name, media_type=STATIC_TYPES[name], headers={"Cache-Control": "no-cache"})

    # ---- app shell: version, first-run onboarding, "what's new" after an update -------------
    def _ui_file() -> Path:
        return library.home_dir() / "ui.json"

    def _ui() -> dict:
        try:
            return json.loads(_ui_file().read_text())
        except (OSError, ValueError):
            return {}

    @app.get("/api/app")
    def api_app() -> dict:
        from .. import __version__, updater, whats_new

        ui = _ui()
        return {"version": __version__, "installed": updater.enabled(), "onboarded": bool(ui.get("onboarded")),
                "seen_version": ui.get("seen_version"), "whats_new": whats_new.since(ui.get("seen_version")),
                "all_notes": whats_new.NOTES, "prefs": ui.get("prefs", {}), "projects_root": str(library.projects_root())}

    @app.post("/api/app/seen")
    def api_app_seen(body: dict) -> dict:
        """Remember that the welcome tour / this version's highlights were shown, and UI preferences."""
        from .. import __version__

        ui = _ui()
        if body.get("onboarded"):
            ui["onboarded"] = True
        if body.get("version", True):
            ui["seen_version"] = __version__
        if isinstance(body.get("prefs"), dict):
            ui["prefs"] = {**ui.get("prefs", {}), **body["prefs"]}
        _ui_file().write_text(json.dumps(ui, indent=2))
        return {"ok": True}

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

    @app.post("/api/projects/bulk-remove")
    async def bulk_remove_projects(body: dict) -> dict:
        """Remove several projects from the list (files stay) or, with delete_files, delete their folders too.
        Each one is tried on its own: one failure doesn't stop the rest, and the result says which failed."""
        ids = [int(x) for x in (body.get("ids") or []) if str(x).lstrip("-").isdigit()]
        delete_files = bool(body.get("delete_files"))
        if not ids:
            raise HTTPException(400, "No projects selected")
        done, failed = [], []
        for pid in dict.fromkeys(ids):
            try:
                row = library.get(pid)
                if host.pdir and Path(row["path"]).resolve() == host.pdir:
                    await host.close()
                library.remove(pid, delete_files and Path(row["path"]).exists())
                done.append(pid)
            except (VeditError, OSError) as e:
                failed.append({"id": pid, "error": str(e)})
        return {"ok": not failed, "removed": done, "failed": failed}

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

    # ---- app updates (desktop install via the launcher; see updater.py) ------------------
    async def _update(fn, *a) -> dict:
        try:
            return await asyncio.to_thread(fn, *a)
        except VeditError as e:
            raise bad(e)

    @app.get("/api/update")
    async def api_update() -> dict:
        """Status, refreshed from GitHub at most every 6 hours (never errors when offline)."""
        from .. import updater

        return await _update(updater.check)

    @app.post("/api/update/check")
    async def api_update_check() -> dict:
        from .. import updater

        return await _update(updater.check, True)

    @app.post("/api/update/apply")
    async def api_update_apply() -> dict:
        from .. import updater

        return await _update(updater.stage)

    @app.post("/api/update/rollback")
    async def api_update_rollback() -> dict:
        from .. import updater

        return await _update(updater.rollback)

    @app.post("/api/update/auto")
    async def api_update_auto(body: dict) -> dict:
        from .. import updater

        updater.set_auto(bool(body.get("on", True)))
        return updater.status()

    @app.post("/api/update/restart")
    def api_update_restart() -> dict:
        """Quit with the launcher's restart code; it installs the staged update (or rollback) and starts again."""
        from .. import updater

        if not updater.enabled():
            raise HTTPException(400, "Restart to update only works in the installed app")
        if any(j["status"] == "running" for j in EXPORTS.values()):
            raise HTTPException(409, "An export is running. Restart when it has finished.")
        threading.Timer(0.4, lambda: os._exit(updater.RESTART_CODE)).start()  # let this response reach the page first
        return {"ok": True}

    # ---- references + generation (home tabs) ---------------------------------------------
    from . import gen_routes

    gen_routes.mount(app, host, bad)

    # ---- AI models: any model from any Anthropic-compatible provider -----------------
    async def _models(fn, *a) -> dict:
        from ..editor import models as registry

        try:
            out = await asyncio.to_thread(fn, *a)
        except registry.RegistryError as e:
            raise HTTPException(400, str(e))
        if host.session is not None:
            await host.session.models_changed()
        return {"ok": True, "result": out, "models": registry.public()}

    @app.get("/api/models")
    def api_models() -> dict:
        from ..editor import models as registry

        return registry.public()

    @app.post("/api/models/provider")
    async def api_provider_save(body: dict) -> dict:
        from ..editor import models as registry

        return await _models(registry.upsert_provider, body)

    @app.post("/api/models/provider/{pid}/remove")
    async def api_provider_remove(pid: str) -> dict:
        from ..editor import models as registry

        return await _models(registry.remove_provider, pid)

    @app.post("/api/models/model")
    async def api_model_add(body: dict) -> dict:
        from ..editor import models as registry

        return await _models(registry.add_model, body)

    @app.post("/api/models/model/remove")
    async def api_model_remove(body: dict) -> dict:
        from ..editor import models as registry

        return await _models(registry.remove_model, str(body.get("key", "")))

    @app.post("/api/models/test")
    async def api_model_test(body: dict) -> dict:
        from ..editor import models as registry

        try:
            if body.get("provider"):
                text = await asyncio.to_thread(registry.test_model, str(body["provider"]), str(body.get("id", "")).strip())
            else:
                text = await asyncio.to_thread(registry.test, str(body.get("key", "")))
            return {"ok": True, "text": text}
        except registry.RegistryError as e:
            return {"ok": False, "text": str(e)}

    @app.post("/api/models/discover")
    async def api_model_discover(body: dict) -> dict:
        from ..editor import models as registry

        try:
            return {"ok": True, "models": await asyncio.to_thread(registry.list_models, str(body.get("provider", "")))}
        except registry.RegistryError as e:
            return {"ok": False, "text": str(e), "models": []}

    # ---- AI setup: Claude Code installed + signed in? ---------------------------------
    @app.get("/api/ai/status")
    async def api_ai_status() -> dict:
        from ..editor import claude_setup
        from ..editor import models as registry

        claude = await asyncio.to_thread(claude_setup.status)
        key = host.session.model if host.session else registry.DEFAULT_KEY
        try:
            prov, model = registry.resolve(key)
            pub = next(p for p in registry.public()["providers"] if p["id"] == prov["id"])
            label, builtin, prov_ready = f"{model['label']} ({prov['name']})", prov["id"] == registry.BUILTIN, pub["ready"]
        except (registry.RegistryError, StopIteration):
            label, builtin, prov_ready = key, True, True
        step = "install" if not claude["installed"] else "login" if builtin and not claude["loggedIn"] else "key" if not prov_ready else "ready"
        return {"claude": claude, "model": key, "model_label": label, "step": step}

    @app.post("/api/ai/login")
    async def api_ai_login(body: dict) -> dict:
        from ..editor import claude_setup

        return await asyncio.to_thread(claude_setup.login, str(body.get("method", "claudeai")))

    @app.post("/api/ai/install")
    async def api_ai_install() -> dict:
        from ..editor import claude_setup

        return await asyncio.to_thread(claude_setup.install)

    # ---- which model does which job (generation + the auditor) ------------------------------
    @app.get("/api/ai/roles")
    def api_ai_roles(quality: str = "balanced") -> dict:
        from ..ai import client as ai
        from ..editor import models as registry

        pub = registry.public()
        ready = {pv["id"]: pv["ready"] for pv in pub["providers"]}
        models = [{"key": m["key"], "label": m.get("label") or m["id"], "vision": m.get("vision"),
                   "provider": next((pv["name"] for pv in pub["providers"] if pv["id"] == m["provider"]), m["provider"]),
                   "ready": ready.get(m["provider"], False)} for m in pub["models"]]
        return {"roles": ai.roles(quality), "models": models}

    @app.post("/api/ai/roles")
    def api_ai_roles_save(body: dict) -> dict:
        """Save defaults: {tier: model key, or "" to go back to the quality tier}."""
        from ..ai import client as ai

        cur = ai.settings()
        for tier, k in (body or {}).items():
            if tier in ai.TIER_NAMES:
                if k:
                    cur[tier] = k
                else:
                    cur.pop(tier, None)
        try:
            ai.save_settings(cur)
        except (ai.AIError, ValueError) as e:
            raise HTTPException(400, str(e))
        return {"roles": ai.roles(str((body or {}).get("_quality") or "balanced"))}

    # ---- Settings page: AI editor model, permissions, usage ----------------------------------
    @app.get("/api/settings")
    def api_settings() -> dict:
        from ..ai import client as ai
        from ..editor import models as registry

        default = ai.settings().get("chat") or registry.DEFAULT_KEY
        return {"project": host.pdir.name if host.pdir else None, "chat_default": default,
                "chat_model": host.session.model if host.session else None}

    @app.post("/api/settings/chat-model")
    async def api_settings_chat_model(body: dict) -> dict:
        """The AI editor's model: saved as the default for new projects, and used now in the open project."""
        from ..ai import client as ai
        from ..editor import models as registry

        key = str((body or {}).get("model") or "").strip()
        try:
            registry.resolve(key)
            ai.save_settings({**ai.settings(), "chat": key})
        except (registry.RegistryError, ai.AIError) as e:
            raise HTTPException(400, str(e))
        if host.session is not None and (body or {}).get("apply", True):
            await host.session.set_model(key)
        return api_settings()

    @app.get("/api/settings/permissions")
    def api_settings_permissions() -> dict:
        from ..editor import permissions

        return {**permissions.load(), "rules": permissions.declaration(), "project": host.pdir.name if host.pdir else None,
                "project_allow": sorted(host.session.always) if host.session else None}

    @app.post("/api/settings/permissions")
    async def api_settings_permissions_save(body: dict) -> dict:
        """{unlisted: ask|deny} and/or {scope: global|project, add: name} / {scope, remove: name}."""
        from ..editor import permissions

        body = body or {}
        try:
            cur = permissions.load()
            if "unlisted" in body:
                if body["unlisted"] not in permissions.UNLISTED:
                    raise permissions.PermissionSettingsError("unlisted must be ask or deny")
                cur["unlisted"] = body["unlisted"]
            scope = body.get("scope", "global")
            if scope == "project":
                if host.session is None:
                    raise permissions.PermissionSettingsError("Open a project to change its own list.")
                items = set(host.session.always)
                if body.get("add"):
                    items.add(permissions.check_item(body["add"]))
                if body.get("remove"):
                    items.discard(str(body["remove"]))
                await host.session.set_always(sorted(items))
            else:
                if body.get("add"):
                    cur["always_allow"] = [*cur["always_allow"], permissions.check_item(body["add"])]
                if body.get("remove"):
                    cur["always_allow"] = [x for x in cur["always_allow"] if x != body["remove"]]
            permissions.save(cur)
        except permissions.PermissionSettingsError as e:
            raise HTTPException(400, str(e))
        return api_settings_permissions()

    @app.get("/api/settings/usage")
    def api_settings_usage(days: float = 30) -> dict:
        from ..ai import client as ai
        from ..editor import models as registry

        out = ai.usage_summary(max(1.0, min(365.0, days)))
        labels = {m["key"]: f"{m.get('label') or m['id']}" for m in registry.public()["models"]}
        for m in out["models"]:
            m["label"] = labels.get(m["key"], m["key"])
        return out

    # ---- audit settings (pass mark + revision cap; shared by generation and the chat editor) ----
    @app.get("/api/audit/settings")
    def api_audit_settings() -> dict:
        from ..review import settings

        return settings()

    @app.post("/api/audit/settings")
    def api_audit_settings_save(body: dict) -> dict:
        from ..review import save_settings

        try:
            return save_settings(body.get("threshold"), body.get("max_rounds"))
        except (TypeError, ValueError) as e:
            raise HTTPException(400, str(e))

    # ---- typography (bundled fonts + text looks, for the inspector) -----------------------
    @app.get("/api/typography")
    def api_typography() -> dict:
        from ..ops import fonts, text_looks

        from ..ops import color

        return {"fonts": fonts.catalog(), "feels": list(fonts.FEELS), "default": fonts.DEFAULT_FONT, "looks": text_looks.catalog(),
                "color_looks": [{"name": k, "about": v["about"]} for k, v in color.LOOKS.items()]}

    @app.get("/api/fonts/{key}")
    def api_font_file(key: str):
        from ..ops import fonts

        if key not in fonts.FONTS:  # only bundled fonts, by key: no paths from the client
            raise HTTPException(404, "Unknown font")
        return FileResponse(fonts.FONT_DIR / fonts.FONTS[key][0], media_type="font/ttf",
                            headers={"Cache-Control": "max-age=86400"})

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
            "rev": _rev(d),
            "render": {"status": r.status, "error": r.error, "version": r.version, "final": r.final, "passes": r.passes} if r
            else {"status": "idle", "error": None, "version": 0, "final": False, "passes": 0},
        }

    def _hist(fn, *a):
        try:
            return fn(host.need(), *a)
        except VeditError as e:
            raise bad(e)

    @app.post("/api/audit")
    async def api_audit(body: dict | None = None) -> dict:
        from ..audit import audit

        d = host.need()
        try:
            if (body or {}).get("ai"):
                from ..review import full_audit

                return await asyncio.to_thread(full_audit, d, True)
            return await asyncio.to_thread(audit, load(d), d, True)
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
    async def api_import(file: UploadFile = File(...), append: bool = Form(True), index: int | None = Form(None),
                         fit_canvas: bool = Form(False)) -> dict:
        from ..editor import actions

        d = host.need()
        suffix = Path(file.filename or "").suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=state_dir(d)) as tmp:
            while chunk := await file.read(1 << 20):
                tmp.write(chunk)
        try:
            return actions.import_file(d, file.filename or "upload" + suffix, Path(tmp.name), append, index, fit_canvas)
        except VeditError as e:
            Path(tmp.name).unlink(missing_ok=True)
            raise bad(e)

    # ---- exports: settings dialog, background job with progress, cancel, reveal --------------------
    EXPORTS: dict[str, dict] = {}

    @app.get("/api/exports/options")
    def api_export_options() -> dict:
        from ..render import export as ex

        d = host.need()
        run = next((j for j in EXPORTS.values() if j["status"] == "running"), None)
        return ex.options(load(d), d) | {"running": {"id": run["id"], "status": "running", "progress": run["progress"],
                                                     "stage": run["stage"]} if run else None}

    @app.post("/api/exports/preview")
    def api_export_preview(body: dict) -> dict:
        """What these settings would produce (size, fps, file name) without exporting."""
        from ..render import export as ex

        d = host.need()
        try:
            return ex.describe(load(d), d, ex.ExportSettings.model_validate(body or {}))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/exports")
    def api_export_start(body: dict) -> dict:
        from ..render import export as ex

        d = host.need()
        try:
            st = ex.ExportSettings.model_validate(body or {})
        except ValueError as e:
            raise HTTPException(400, str(e))
        if any(j["status"] == "running" for j in EXPORTS.values()):
            raise HTTPException(409, "An export is already running")
        ex.save_settings(d, st)
        eid = f"e{int(time.time() * 1000)}"
        job = {"id": eid, "status": "running", "progress": 0.0, "stage": "Starting", "started": time.time(), "result": None,
               "error": None, "stop": False, "format": st.format}
        EXPORTS[eid] = job

        def work():
            from ..jobs import render_lock

            try:
                with render_lock:
                    job["result"] = ex.run(load(d), d, st, on_progress=lambda f, stage: job.update(progress=round(f, 4), stage=stage),
                                           should_stop=lambda: job["stop"])
                job["status"] = "done"
            except media.Cancelled:
                job["status"] = "cancelled"
            except VeditError as e:
                job.update(status="failed", error=str(e))
            except Exception as e:  # noqa: BLE001 - surface anything to the dialog
                job.update(status="failed", error=f"{type(e).__name__}: {e}")

        threading.Thread(target=work, daemon=True).start()
        return {"id": eid}

    @app.get("/api/exports/{eid}")
    def api_export_status(eid: str) -> dict:
        job = EXPORTS.get(eid)
        if not job:
            raise HTTPException(404, "No such export")
        el = time.time() - job["started"]
        eta = (el / job["progress"] - el) if 0.03 < job["progress"] < 1 else None
        return {k: v for k, v in job.items() if k != "stop"} | {"elapsed": round(el, 1), "eta": None if eta is None else round(eta, 1)}

    @app.post("/api/exports/{eid}/cancel")
    def api_export_cancel(eid: str) -> dict:
        job = EXPORTS.get(eid)
        if not job:
            raise HTTPException(404, "No such export")
        job["stop"] = True
        return {"ok": True}

    @app.post("/api/exports/pick-folder")
    async def api_pick_folder(body: dict | None = None) -> dict:
        from ..render import export as ex

        folder = await asyncio.to_thread(ex.pick_folder, (body or {}).get("start"))
        return {"folder": folder}

    @app.post("/api/exports/reveal")
    def api_reveal(body: dict) -> dict:
        from ..render import export as ex

        path = Path(str((body or {}).get("path") or "")).expanduser()
        made = {Path(j["result"]["output"]) for j in EXPORTS.values() if j.get("result")}
        if path not in made:  # only files this server exported: never open arbitrary paths
            raise HTTPException(403, "Only exported files can be opened from here")
        if not path.exists():
            raise HTTPException(404, "File not found")
        if body.get("open"):
            media.open_file(path)
        else:
            ex.reveal(path)
        return {"ok": True}

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
        if name not in ("preview.mp4", "base.mp4", "thumbs.jpg", "wave.png"):
            raise HTTPException(404)
        f = preview_dir(host.need()) / name
        if not f.exists():
            raise HTTPException(404)
        # FileResponse answers Range requests and sends ETag/Last-Modified; "no-cache" makes the
        # browser revalidate instead of re-downloading an unchanged file
        return FileResponse(f, headers={"Cache-Control": "no-cache"})

    @app.get("/api/source/{sid}/media")
    def source_media(sid: str):
        """A source's media file (Range requests work), for video layers the live preview plays itself."""
        d = host.need()
        p = load(d)
        if sid not in p.sources:
            raise HTTPException(404)
        from .. import proxies

        f = proxies.available(d, p.sources[sid]) or resolve_media(d, p.sources[sid].path)  # the proxy plays smoother
        if not f.exists():
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "no-cache"})

    LOOK_LOCK = threading.Lock()

    @app.get("/api/layer/{lid}/media")
    def layer_media(lid: str):
        """A video overlay's whole source with its colour grade + filters applied (H.264, preview size), for the live
        preview: the browser can't run FFmpeg filters, so it plays this instead of the raw source. Cached per look."""
        import hashlib

        from ..render.layers import video_look

        d = host.need()
        p = load(d)
        l = next((x for x in p.layers if x.id == lid and x.type == "video"), None)
        if l is None or l.source not in p.sources:
            raise HTTPException(404)
        src = p.sources[l.source]
        from .. import proxies

        f = proxies.available(d, src) or resolve_media(d, src.path)
        w = 540 if (src.width or 1920) >= (src.height or 1080) else 360
        h = int(round(w * (src.height or 1080) / (src.width or 1920) / 2)) * 2
        vf = f"scale={w}:{h},setsar=1" + video_look(p, d, l, w, h, w / max(1, p.canvas.width), offset=l.src_in)
        key = hashlib.sha1(f"{f}|{f.stat().st_mtime}|{vf}".encode()).hexdigest()[:16]
        out = state_dir(d) / "cache" / "looks" / f"{lid}-{key}.mp4"
        with LOOK_LOCK:
            if not out.exists():
                out.parent.mkdir(parents=True, exist_ok=True)
                tmp = out.with_suffix(".part.mp4")
                media.run([media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(f), "-vf", vf,
                           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)], "preview look")
                tmp.replace(out)
        return FileResponse(out, headers={"Cache-Control": "no-cache"})

    @app.post("/api/version")
    def save_version(body: dict) -> dict:
        """Name the current state (History tab: jump back to it any time)."""
        return _hist(history.save_version, str(body.get("name") or ""))

    @app.get("/api/templates")
    def list_templates() -> dict:
        from ..ops import templates

        return {"templates": templates.catalog()}

    @app.post("/api/templates")
    def save_template(body: dict) -> dict:
        from ..ops import templates

        d = host.need()
        try:
            return templates.save(load(d), d, str(body.get("name") or ""), list(body.get("layers") or []), str(body.get("about") or ""))
        except VeditError as e:
            raise bad(e)

    @app.delete("/api/templates/{key}")
    def delete_template(key: str) -> dict:
        from ..ops import templates

        try:
            templates.delete(key)
        except VeditError as e:
            raise bad(e)
        return {"ok": True}

    @app.get("/api/overlays")
    def overlays_catalog() -> dict:
        from ..ops import overlays

        return {"library": overlays.catalog()}

    @app.get("/api/overlays/{key}/thumb.jpg")
    async def overlay_thumb(key: str):
        """A still of a library overlay (generating the overlay on first use; cached afterwards)."""
        from ..ops import overlays

        if key.startswith("shape-"):  # the plain shapes: shape-rect, shape-ellipse, shape-line, shape-arrow
            try:
                png = overlays.shape_thumb(key[6:])
            except VeditError:
                raise HTTPException(404)
            return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})
        if key not in overlays.LIBRARY:
            raise HTTPException(404)
        it = overlays.LIBRARY[key]
        if it.kind == "shape":
            extra = dict(it.layer)
            png = overlays.shape_thumb(extra.pop("shape"), extra)
            return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})
        p = load(host.need())

        def make() -> Path:
            f = overlays.library_file(key, p.canvas.width, p.canvas.height)
            thumb = f.with_name(f"thumb-{f.stem}.jpg")
            if not thumb.exists():
                if f.suffix == ".png":  # stickers / frames: on a dark checker so transparent parts read as such
                    from PIL import Image

                    img = Image.open(f).convert("RGBA")
                    img.thumbnail((360, 360))
                    bg = Image.new("RGBA", img.size, (40, 44, 52, 255))
                    Image.alpha_composite(bg, img).convert("RGB").save(thumb, quality=85)
                else:
                    media.extract_frame(f, 1.5, thumb, width=240)
            return thumb

        try:
            return FileResponse(await asyncio.to_thread(make), headers={"Cache-Control": "max-age=3600"})
        except VeditError as e:
            raise bad(e)

    @app.get("/api/effects")
    def effects_catalog() -> dict:
        """Everything the Effects tab can apply: filters (ops/fx_filters.py) and signature moves (ops/moves.py)."""
        from ..ops import fx_filters, moves

        return {"filters": fx_filters.catalog(), "categories": list(fx_filters.CATEGORIES), "moves": moves.catalog()}

    @app.post("/api/preview-mode")
    def preview_mode(body: dict) -> dict:
        """Live (base video + browser-drawn overlays) or final (the full FFmpeg render, as exported)."""
        host.need()
        r = host.renderer
        if r is None:
            raise HTTPException(409, "No renderer")
        final = bool(body.get("final"))
        if final != r.final:
            r.final = final
            r.pending.set()
        return {"final": r.final}

    @app.post("/api/frame")
    async def frame(body: dict):
        """One preview frame at `t` (JPEG, no overlays), in ~0.1-0.3 s. `clip` (optional) replaces a clip's settings
        for this frame only, so dragging a slider shows its effect before it is saved. 409 = an effect of that clip
        is still being computed (the background render will show it)."""
        from ..ops.effects import NotReady
        from ..render.frame import render_frame

        d = host.need()
        try:
            jpg = await asyncio.to_thread(render_frame, load(d), d, float(body.get("t", 0)), body.get("clip") or None)
        except NotReady as e:
            raise HTTPException(409, str(e))
        except VeditError as e:
            raise bad(e)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, f"Bad request: {e}")
        return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/scope")
    async def scope(body: dict):
        """Waveform or vectorscope (PNG) of the frame at `t`, with optional unsaved clip settings like /api/frame."""
        from ..ops.effects import NotReady
        from ..render.frame import render_frame
        from ..render.scopes import scope_png

        d = host.need()

        def make() -> bytes:
            jpg = render_frame(load(d), d, float(body.get("t", 0)), body.get("clip") or None)
            return scope_png(jpg, str(body.get("kind", "waveform")))

        try:
            png = await asyncio.to_thread(make)
        except NotReady as e:
            raise HTTPException(409, str(e))
        except VeditError as e:
            raise bad(e)
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/sprite")
    async def sprite(body: dict):
        """A layer's overlay sprite as PNG, drawn by the export's own text/image renderer."""
        from .sprites import sprite_png

        d = host.need()
        opt = lambda k: None if body.get(k) is None else int(body[k])  # noqa: E731
        try:
            png = await asyncio.to_thread(sprite_png, d, body.get("layer") or {}, float(body.get("scale", 0.5)),
                                          opt("reveal"), opt("group"), opt("hi"))
        except VeditError as e:
            raise bad(e)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, f"Bad layer: {e}")
        return Response(png, media_type="image/png")

    @app.get("/api/source/{sid}/thumb.jpg")
    def source_thumb(sid: str):
        # revalidate every time: ids like s1 exist in every project, so a cached copy may be another project's
        return FileResponse(_thumb(host.need(), sid), headers={"Cache-Control": "no-cache"})

    @app.get("/api/events")
    async def events():
        async def gen():
            last = None
            while True:
                r, d = host.renderer, host.pdir
                if r is None or d is None:
                    yield f"data: {json.dumps({'closed': True})}\n\n"
                    return
                rev = _rev(d)
                key = (id(r), r.version, r.status, r.error, rev, r.final, r.passes)
                if key != last:
                    last = key
                    msg = {'version': r.version, 'status': r.status, 'error': r.error, 'rev': rev, 'final': r.final,
                           'passes': r.passes}
                    yield f"data: {json.dumps(msg)}\n\n"
                await asyncio.sleep(0.15)

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
                elif kind == "permission":
                    allow = msg.get("allow")
                    session.answer_permission(str(msg.get("id", "")), allow if allow in ("always", "turn") else bool(allow))
                elif kind == "interrupt":
                    await session.interrupt()
                elif kind == "model":
                    await session.set_model(str(msg.get("model", "")))
                elif kind == "reset":
                    asyncio.create_task(session.new_conversation())
                elif kind == "open_chat":
                    asyncio.create_task(session.open_chat(str(msg.get("id", ""))))
                elif kind == "delete_chat":
                    asyncio.create_task(session.delete_chat(str(msg.get("id", ""))))
                elif kind == "auto_audit":
                    await session.set_auto_audit(bool(msg.get("on")))
                elif kind == "auditor":
                    await session.set_auditor(str(msg.get("value", "")))
                elif kind == "mode":
                    await session.set_mode(str(msg.get("mode", "")))
                elif kind == "plan_answer":
                    asyncio.create_task(session.answer_plan(str(msg.get("id", "")), str(msg.get("answer", ""))[:2000],
                                                            msg.get("context") or {}))
        except WebSocketDisconnect:
            session.sockets.discard(ws)

    return app


def dev_app() -> FastAPI:
    """App factory for `vedit serve --dev`: uvicorn re-imports this in a fresh process after each code change."""
    d = DEV_OPEN.read_text(encoding="utf-8").strip() if DEV_OPEN.exists() else ""
    pdir = Path(d) if d and (Path(d) / PROJECT_FILE).exists() else None
    return create_app(pdir, dev=True)


def run(pdir: Path | None, host: str = "127.0.0.1", port: int = 5173, browser: bool = True, dev: bool = False) -> None:
    # timeout_graceful_shutdown: the open editor keeps /api/events streaming forever, and uvicorn would otherwise wait
    # for it to close on Ctrl+C (and on every --dev reload), so the server never stops
    import uvicorn

    url = f"http://{host}:{port}" + ("/editor" if pdir else "")
    print(f"aiklips  {url}\n(Ctrl+C to stop)")
    if browser:
        threading.Timer(1.5 if dev else 1.0, lambda: webbrowser.open(url)).start()
    if not dev:
        uvicorn.run(create_app(pdir), host=host, port=port, log_level="warning", timeout_graceful_shutdown=1)
        return
    print("Dev mode: Python changes restart the server, HTML/JS changes refresh the page.")
    if pdir:
        DEV_OPEN.write_text(str(pdir.resolve()), encoding="utf-8")
    else:
        DEV_OPEN.unlink(missing_ok=True)
    uvicorn.run("vedit.preview_server.app:dev_app", factory=True, host=host, port=port, log_level="warning",
                reload=True, reload_dirs=[str(SRC)], reload_includes=["*.py"], timeout_graceful_shutdown=1)

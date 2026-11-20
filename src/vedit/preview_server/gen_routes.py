"""HTTP API for the References and Generate tabs of the home screen.

  /api/references ...       library of studied videos (upload, Style card, overrides, re-study)
  /api/uploads              footage / music / images for generation
  /api/batches ...          estimate, create, list, cancel, resume, delete
  /api/variations/{id}/...  poster, video, open in editor, keep, discard, regenerate
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError

from .. import jobs, library
from ..project import VeditError


async def _save_uploads(files: list[UploadFile], add) -> dict:
    added, errors = [], []
    tmpdir = library.home_dir() / "incoming"
    tmpdir.mkdir(exist_ok=True)
    for f in files:
        name = Path(f.filename or "upload").name
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(name).suffix.lower(), dir=tmpdir) as tmp:
            while chunk := await f.read(1 << 20):
                tmp.write(chunk)
        try:
            added.append(await asyncio.to_thread(add, Path(tmp.name), name))
        except VeditError as e:
            errors.append(f"{name}: {e}")
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    if not added and errors:
        raise HTTPException(400, "; ".join(errors))
    return {"added": added, "errors": errors}


def mount(app: FastAPI, host, bad) -> None:
    from ..generate import batch, footage
    from ..generate.spec import AXES, AXIS_LABELS, BatchSpec
    from ..ops.captions import CAPTION_STYLES
    from ..references import dna as dnamod
    from ..references import store
    from .. import media_library

    def spec_from(body: dict) -> BatchSpec:
        try:
            return BatchSpec.model_validate(body)
        except ValidationError as e:
            first = e.errors()[0]
            where = ".".join(str(x) for x in first["loc"])
            msg = {"refs": "Pick at least one reference", "footage": "Add at least one video of your own footage"}.get(where, f"{where}: {first['msg']}")
            raise HTTPException(400, msg) from None

    jobs.IN_APP = True  # this process runs the job queue (media studies queued by uploads start workers here)
    if jobs.AUTOSTART and jobs.pending():  # a batch was still running when the app closed: carry on
        jobs.ensure_workers()
    if jobs.AUTOSTART:  # references studied with an older study prompt: study them again (design pass, whole-video sheet)
        try:
            store.restudy_stale()
        except Exception:
            pass
    if jobs.AUTOSTART:  # media library: register older uploads, re-study files measured / logged by an older version
        try:
            media_library.backfill()
            media_library.restudy_stale()
        except Exception:
            pass

    # ---- references ------------------------------------------------------------------
    @app.get("/api/references")
    def list_refs() -> dict:
        return {"references": store.list_refs()}

    @app.post("/api/references")
    async def add_refs(files: list[UploadFile] = File(...)) -> dict:
        return await _save_uploads(files, lambda p, n: store.add(p, Path(n).stem, move=True))

    @app.get("/api/references/{rid}")
    def get_ref(rid: int) -> dict:
        try:
            return store.get(rid, full=True)
        except VeditError as e:
            raise HTTPException(404, str(e))

    @app.patch("/api/references/{rid}")
    def patch_ref(rid: int, body: dict) -> dict:
        try:
            return store.update(rid, body.get("name"), body.get("tags"), body.get("overrides"))
        except VeditError as e:
            raise bad(e)

    @app.post("/api/references/{rid}/restudy")
    def restudy(rid: int) -> dict:
        try:
            return store.restudy(rid)
        except VeditError as e:
            raise bad(e)

    @app.delete("/api/references/{rid}")
    def delete_ref(rid: int) -> dict:
        try:
            store.delete(rid)
        except VeditError as e:
            raise bad(e)
        return {"ok": True}

    def _ref_file(rid: int, name: str) -> FileResponse:
        try:
            row = store.get(rid)
        except VeditError:
            raise HTTPException(404)
        f = Path(row["dir"]) / name
        if not f.exists():
            raise HTTPException(404)
        return FileResponse(f)

    @app.get("/api/references/{rid}/thumb.jpg")
    def ref_thumb(rid: int):
        return _ref_file(rid, "thumb.jpg")

    @app.get("/api/references/{rid}/sheet/{n}.jpg")
    def ref_sheet(rid: int, n: int):
        return _ref_file(rid, f"sheet-{n}.jpg")

    @app.get("/api/references/{rid}/video")
    def ref_video(rid: int):
        try:
            return FileResponse(store.source_path(store.get(rid)))
        except VeditError:
            raise HTTPException(404)

    @app.post("/api/references/merge")
    def merge_refs(body: dict) -> dict:
        ids = [int(i) for i in body.get("ids") or []]
        ready = [i for i in ids if store.get(i)["status"] == "ready"]
        if not ready:
            return {"card": [], "dna": None, "pending": len(ids)}
        lead = body.get("lead")
        m = store.merged(ready, int(lead) if lead is not None else None)
        return {"card": dnamod.card(m), "dna": m.model_dump(), "pending": len(ids) - len(ready)}

    # ---- uploads (footage / music / images) ------------------------------------------------
    @app.post("/api/uploads")
    async def add_uploads(files: list[UploadFile] = File(...)) -> dict:
        return await _save_uploads(files, lambda p, n: footage.add(p, n, move=True))

    @app.post("/api/uploads/classify")
    def classify_uploads(body: dict) -> dict:
        """AI suggests, per upload, whether it is footage (pictures) or sound (audio only)."""
        try:
            return {"roles": footage.classify([str(u) for u in body.get("ids") or []], body.get("quality") or "balanced")}
        except VeditError as e:
            raise bad(e)

    # ---- media library: every file studied once, reused in any project or batch ---------------------
    @app.get("/api/media")
    def media_list(kind: str | None = None, q: str | None = None, tag: str | None = None) -> dict:
        return {"media": media_library.list_media(kind, q, tag), "bytes": media_library.disk_usage()}

    @app.post("/api/media")
    async def media_add(files: list[UploadFile] = File(...)) -> dict:
        return await _save_uploads(files, lambda p, n: media_library.add(p, n, move=True))

    @app.get("/api/media/{uid}")
    def media_get(uid: str) -> dict:
        try:
            return media_library.get(uid, full=True)
        except VeditError as e:
            raise HTTPException(404, str(e))

    @app.patch("/api/media/{uid}")
    def media_update(uid: str, body: dict) -> dict:
        try:
            return media_library.update(uid, body.get("name"), body.get("tags"), body.get("overrides"))
        except VeditError as e:
            raise bad(e)

    @app.post("/api/media/{uid}/restudy")
    def media_restudy(uid: str) -> dict:
        try:
            return media_library.restudy(uid)
        except VeditError as e:
            raise bad(e)

    @app.delete("/api/media/{uid}")
    def media_delete(uid: str, force: bool = False) -> dict:
        try:
            media_library.delete(uid, force)
        except VeditError as e:
            raise bad(e)
        return {"ok": True}

    @app.get("/api/media/{uid}/thumb.jpg")
    def media_thumb(uid: str):
        return upload_thumb(uid)

    @app.get("/api/media/{uid}/file")
    def media_file(uid: str):
        """The stored copy itself, for the media manager's player (seeks to a segment)."""
        try:
            p = Path(footage.meta_full(uid)["path"])
        except VeditError:
            raise HTTPException(404)
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p)

    @app.get("/api/uploads/{uid}/thumb.jpg")
    def upload_thumb(uid: str):
        try:
            f = footage.thumb(uid)
        except VeditError:
            raise HTTPException(404)
        if not f.exists():
            raise HTTPException(404)
        return FileResponse(f)

    # ---- batches -----------------------------------------------------------------------------
    @app.get("/api/generate/options")
    def gen_options() -> dict:
        from ..generate.executor import captions_available

        return {"axes": [{"id": k, "label": AXIS_LABELS[k], "values": v} for k, v in AXES.items()],
                "caption_styles": list(CAPTION_STYLES), "formats": list(library.FORMATS), "captions": captions_available(),
                "traits": dnamod.editable(),
                "goals": ["promo", "tutorial", "story", "ugc", "announcement", "other"]}

    @app.post("/api/batches/estimate")
    def estimate(body: dict) -> dict:
        try:
            return batch.estimate(spec_from(body))
        except VeditError as e:
            raise bad(e)

    @app.post("/api/batches")
    def create_batch(body: dict) -> dict:
        try:
            return batch.create(spec_from(body))
        except VeditError as e:
            raise bad(e)

    @app.get("/api/batches")
    def list_batches() -> dict:
        return {"batches": batch.list_batches()}

    @app.get("/api/batches/{bid}")
    def get_batch(bid: str) -> dict:
        try:
            return batch.get(bid)
        except VeditError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/batches/{bid}/{action}")
    def batch_action(bid: str, action: str) -> dict:
        fn = {"cancel": batch.cancel, "resume": batch.resume}.get(action)
        if fn is None:
            raise HTTPException(404)
        try:
            return fn(bid)
        except VeditError as e:
            raise bad(e)

    @app.delete("/api/batches/{bid}")
    def delete_batch(bid: str) -> dict:
        try:
            batch.delete(bid)
        except VeditError as e:
            raise bad(e)
        return {"ok": True}

    # ---- variations ------------------------------------------------------------------------------
    @app.get("/api/variations/{vid}/poster.jpg")
    def var_poster(vid: str):
        try:
            f = batch.poster(vid)
        except VeditError:
            raise HTTPException(404)
        if not f.exists():
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "no-store"})

    @app.get("/api/variations/{vid}/video.mp4")
    def var_video(vid: str):
        try:
            return FileResponse(batch.video(vid), media_type="video/mp4")
        except VeditError:
            raise HTTPException(404)

    @app.post("/api/variations/{vid}/open")
    async def var_open(vid: str) -> dict:
        try:
            row = batch.project_for(vid)
            await host.open(Path(row["path"]))
        except VeditError as e:
            raise bad(e)
        return row

    @app.post("/api/variations/{vid}/improve")
    def var_improve(vid: str, body: dict | None = None) -> dict:
        """More editor revisions for one video, from its best version, until the pass mark (or the rounds run out)."""
        try:
            return batch.improve(vid, (body or {}).get("rounds"))
        except VeditError as e:
            raise bad(e)

    @app.post("/api/variations/{vid}/{action}")
    def var_action(vid: str, action: str) -> dict:
        try:
            if action == "keep":
                return {"ok": True, "project": batch.keep(vid)}
            if action == "discard":
                batch.discard(vid)
                return {"ok": True}
            if action == "regenerate":
                return batch.regenerate(vid)
        except VeditError as e:
            raise bad(e)
        raise HTTPException(404)

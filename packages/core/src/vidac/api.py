from __future__ import annotations

import json
import os
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from vidac import __version__
from vidac.config import Settings, Workspace
from vidac.db import Database, uid
from vidac.editing import validate_plan
from vidac.jobs import Worker
from vidac.media import VIDEO_EXTENSIONS, files_at
from vidac.models import ClipPatch, CreateEdit, EditPlan, SaveEdit
from vidac.relink import relink_file


class ImportRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)


class RelinkRequest(BaseModel):
    path: str


class ExportRequest(BaseModel):
    format: str = "otio"
    revision: int | None = Field(default=None, ge=1)


def create_app(workspace: Workspace | None = None, run_worker: bool = True) -> FastAPI:
    workspace = workspace or Workspace()
    db = Database(workspace.db_path)
    worker = Worker(workspace, db)

    @asynccontextmanager
    async def lifespan(app):
        if run_worker:
            worker.start()
        yield
        if run_worker:
            worker.stop()

    app = FastAPI(title="Vidac", version=__version__, lifespan=lifespan)
    extra_hosts = [
        host.strip()
        for host in os.environ.get("VIDAC_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    ]
    if any(
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host) for host in extra_hosts
    ):
        raise ValueError(
            "VIDAC_ALLOWED_HOSTS requires exact hostnames or IPv4 addresses, without schemes, ports or wildcards."
        )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver", *extra_hosts],
    )
    app.state.workspace, app.state.db, app.state.worker = workspace, db, worker
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PATCH", "PUT"],
        allow_headers=["Content-Type", "X-Vidac-Client"],
    )

    @app.middleware("http")
    async def local_writes(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("x-vidac-client") != "workspace":
                return JSONResponse(
                    {"detail": "Missing X-Vidac-Client: workspace header."}, status_code=403
                )
            origin = request.headers.get("origin")
            if (
                origin
                and urlsplit(origin).netloc != request.headers.get("host")
                and origin not in {"http://localhost:5173", "http://127.0.0.1:5173"}
            ):
                return JSONResponse(
                    {"detail": "This origin cannot modify the local workspace."}, status_code=403
                )
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc).strip("'")}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "version": __version__,
            "workspace": str(workspace.root),
            "ffmpeg": bool(shutil.which(os.environ.get("VIDAC_FFMPEG", "ffmpeg"))),
            "ffprobe": bool(shutil.which(os.environ.get("VIDAC_FFPROBE", "ffprobe"))),
            "worker": worker.active,
            "mode": "local",
        }

    @app.get("/api/stats")
    def stats():
        return {
            "assets": db.one("SELECT count(*) AS n FROM assets WHERE status='ready'")["n"],
            "clips": db.one("SELECT count(*) AS n FROM clips")["n"],
            "favorites": db.one("SELECT count(*) AS n FROM clips WHERE favorite=1")["n"],
            "duration": db.one(
                "SELECT coalesce(sum(duration),0) AS n FROM assets WHERE status='ready'"
            )["n"],
            "edits": db.one("SELECT count(*) AS n FROM edits")["n"],
            "active_jobs": db.one(
                "SELECT count(*) AS n FROM jobs WHERE status IN ('queued','running')"
            )["n"],
        }

    @app.get("/api/assets")
    def assets():
        return [
            db.asset(row["id"]) for row in db.all("SELECT id FROM assets ORDER BY created_at DESC")
        ]

    @app.get("/api/assets/{asset_id}")
    def asset(asset_id: str):
        return db.asset(asset_id)

    @app.post("/api/ingest", status_code=202)
    def enqueue_ingest(request: ImportRequest):
        # Validate the complete request before enqueueing any work.
        paths = list(dict.fromkeys(p for value in request.paths for p in files_at(Path(value))))
        if len(paths) > 500:
            raise ValueError("Import at most 500 files at once.")
        return {"jobs": [db.enqueue("ingest", {"path": str(path)}) for path in paths]}

    @app.post("/api/uploads", status_code=202)
    def upload(files: list[UploadFile] = File()):
        if len(files) > 50:
            raise ValueError("Upload at most 50 files at once.")
        for file in files:
            if Path(file.filename or "").suffix.lower() not in VIDEO_EXTENSIONS:
                raise ValueError(f"Unsupported video: {file.filename}")
        jobs = []
        for file in files:
            name = Path(file.filename or "video.mp4").name
            directory = workspace.root / "uploads" / uid("upload")
            directory.mkdir()
            target = directory / name
            with target.open("wb") as out:
                shutil.copyfileobj(file.file, out, length=8 * 1024 * 1024)
            file.file.close()
            jobs.append(db.enqueue("ingest", {"path": str(target)}))
        return {"jobs": jobs}

    @app.post("/api/demo", status_code=202)
    def demo():
        return db.enqueue("demo", {})

    @app.get("/api/clips")
    def clips(q: str = "", asset_id: str = "", favorite: bool = False):
        return db.clips(query=q, asset_id=asset_id, favorite=favorite)

    @app.get("/api/clips/{clip_id}")
    def clip(clip_id: str):
        return db.clip(clip_id)

    @app.patch("/api/clips/{clip_id}")
    def patch_clip(clip_id: str, patch: ClipPatch):
        old = db.clip(clip_id)
        updates = patch.model_dump(exclude_none=True)
        start = updates.get("start_frame", old["start_frame"])
        end = updates.get("end_frame", old["end_frame"])
        if start < old["shot_start"] or end > old["shot_end"] or start >= end:
            raise ValueError(
                "Trim must stay inside this camera shot and contain at least one frame."
            )
        if "tags" in updates:
            updates["tags"] = json.dumps(
                list(dict.fromkeys(t.strip()[:100] for t in updates["tags"] if t.strip()))
            )
        if "favorite" in updates:
            updates["favorite"] = int(updates["favorite"])
        if updates:
            db.execute(
                "UPDATE clips SET " + ",".join(f"{key}=?" for key in updates) + " WHERE id=?",
                (*updates.values(), clip_id),
            )
        return db.clip(clip_id)

    @app.post("/api/clips/{clip_id}/analyze", status_code=202)
    def analyze(clip_id: str):
        db.clip(clip_id)
        if not workspace.settings().vision_model:
            raise ValueError("Choose a local vision model in Settings first.")
        return db.enqueue("annotate", {"clip_id": clip_id})

    @app.post("/api/assets/{asset_id}/relink")
    def relink(asset_id: str, request: RelinkRequest):
        return relink_file(db, asset_id, Path(request.path))

    @app.post("/api/relink", status_code=202)
    def enqueue_relink(request: RelinkRequest):
        path = Path(request.path).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Search folder is unavailable: {path}")
        return db.enqueue("relink", {"path": str(path)})

    @app.get("/api/media/{asset_id}/{kind}")
    def media(asset_id: str, kind: str):
        asset = db.asset(asset_id)
        paths = {
            "proxy": asset["proxy_path"],
            "poster": asset["thumb_path"],
            "waveform": str(workspace.cache(asset_id) / "waveform.png"),
            "transcript": str(workspace.cache(asset_id) / "transcript.json"),
        }
        selected = paths.get(kind)
        if not selected or not Path(selected).is_file():
            raise HTTPException(404, "Media derivative is not available.")
        return FileResponse(selected)

    @app.get("/api/thumbnails/{clip_id}")
    def thumb(clip_id: str):
        path = db.clip(clip_id)["thumb_path"]
        if not path or not Path(path).is_file():
            raise HTTPException(404, "Thumbnail not available.")
        return FileResponse(path)

    @app.get("/api/jobs")
    def jobs():
        return db.jobs()

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return db.job(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        db.job(job_id)
        db.execute(
            "UPDATE jobs SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END "
            "WHERE id=? AND status IN ('queued','running')",
            (job_id,),
        )
        return db.job(job_id)

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str):
        old = db.job(job_id)
        if old["status"] not in {"failed", "cancelled"}:
            raise ValueError("Only failed or cancelled jobs can be retried.")
        db.execute(
            "UPDATE jobs SET status='queued',progress=0,error=NULL,cancel_requested=0,stage='Waiting to retry' WHERE id=?",
            (job_id,),
        )
        return db.job(job_id)

    @app.get("/api/edits")
    def edits():
        return [
            db.edit(row["id"]) for row in db.all("SELECT id FROM edits ORDER BY updated_at DESC")
        ]

    @app.post("/api/edits", status_code=202)
    def create_edit(request: CreateEdit):
        if not db.clips(limit=1):
            raise ValueError("Import footage before creating an edit.")
        if request.use_ai and not workspace.settings().planning_model:
            raise ValueError("Choose a planning model in Settings first.")
        return db.enqueue("edit", request.model_dump())

    @app.get("/api/edits/{edit_id}")
    def get_edit(edit_id: str, revision: int | None = None):
        return db.edit(edit_id, revision)

    @app.put("/api/edits/{edit_id}")
    def save_edit(edit_id: str, request: SaveEdit):
        db.edit(edit_id)
        plan = validate_plan(db, request.plan)
        try:
            return db.save_edit(plan.model_dump(), edit_id, request.revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/edits/{edit_id}/export", status_code=202)
    def export(edit_id: str, request: ExportRequest):
        edit = db.edit(edit_id, request.revision)
        if request.format not in {"otio", "fcpxml", "resolve", "preview"}:
            raise ValueError("Unknown export format.")
        if not edit["plan"]["items"]:
            raise ValueError("Add clips before exporting.")
        return db.enqueue(
            "export", {"edit_id": edit_id, "revision": edit["revision"], "format": request.format}
        )

    @app.get("/api/artifacts/{artifact_id}")
    def artifact(artifact_id: str, inline: bool = False):
        row = db.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
        if not row or not Path(row["path"]).is_file():
            raise HTTPException(404, "Export not available.")
        return FileResponse(row["path"], filename=None if inline else Path(row["path"]).name)

    @app.get("/api/settings")
    def settings():
        return workspace.settings()

    @app.put("/api/settings")
    def save_settings(settings: Settings):
        for value in settings.watch_folders:
            path = Path(value).expanduser().resolve()
            if (
                not path.is_dir()
                or path.is_relative_to(workspace.root)
                or workspace.root.is_relative_to(path)
            ):
                raise ValueError(
                    "Watch a footage folder outside the workspace and its parent folders."
                )
        workspace.save_settings(settings)
        return settings

    @app.get("/api/models")
    def models():
        try:
            response = httpx.get(f"{workspace.settings().ollama_url}/api/tags", timeout=3)
            response.raise_for_status()
            return {"connected": True, "models": [m["name"] for m in response.json()["models"]]}
        except (httpx.HTTPError, KeyError, ValueError):
            return {"connected": False, "models": []}

    @app.get("/api/schema/edit-plan")
    def schema():
        return EditPlan.model_json_schema()

    # Mount after all API routes. StaticFiles handles traversal and HTTP range requests.
    web_dist = Path(
        os.environ.get("VIDAC_WEB_DIST", Path(__file__).resolve().parents[4] / "apps/web/dist")
    )
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="workspace")
    else:

        @app.get("/")
        def no_build():
            return {
                "message": "Run npm run dev for development, or npm run build before vidac serve.",
                "docs": "/docs",
            }

    return app

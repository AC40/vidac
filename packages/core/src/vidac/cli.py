from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated

import typer

from vidac.config import Workspace
from vidac.db import Database
from vidac.editing import validate_plan
from vidac.jobs import Worker
from vidac.media import files_at
from vidac.models import CreateEdit, EditPlan

app = typer.Typer(
    no_args_is_help=True, help="Footage in. Possibilities out. A local creative workspace."
)


def context():
    workspace = Workspace()
    return workspace, Database(workspace.db_path)


def print_json(value):
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False))


def wait_for(workspace, db, jobs):
    worker = Worker(workspace, db)
    worker.start()
    try:
        for job in jobs:
            last = ""
            while True:
                current = db.job(job["id"])
                stage = current["stage"]
                if stage != last:
                    typer.echo(f"{current['id']}: {stage}", err=True)
                    last = stage
                if current["status"] in {"succeeded", "failed", "cancelled"}:
                    print_json(current)
                    if current["status"] != "succeeded":
                        raise typer.Exit(1)
                    break
                time.sleep(0.4)
    finally:
        worker.stop()


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8741):
    """Run the API, durable worker and built web app. One process per workspace."""
    import uvicorn

    uvicorn.run("vidac.api:create_app", factory=True, host=host, port=port)


@app.command()
def worker():
    """Consume jobs without running the web server."""
    workspace, db = context()
    try:
        Worker(workspace, db).run()
    except KeyboardInterrupt:
        pass


@app.command()
def ingest(path: Path, wait: bool = True):
    """Reference a local video or recursively ingest a folder. Originals stay untouched."""
    workspace, db = context()
    jobs = [db.enqueue("ingest", {"path": str(p)}) for p in files_at(path)]
    wait_for(workspace, db, jobs) if wait else print_json(jobs)


@app.command()
def relink(path: Path, asset_id: str | None = None, wait: bool = True):
    """Search a folder recursively for missing originals, or relink --asset-id to a file."""
    from vidac.relink import relink_file

    workspace, db = context()
    path = path.expanduser().resolve()
    if asset_id:
        print_json(relink_file(db, asset_id, path))
        return
    if not path.is_dir():
        raise typer.BadParameter(f"Search folder is unavailable: {path}")
    job = db.enqueue("relink", {"path": str(path)})
    wait_for(workspace, db, [job]) if wait else print_json(job)


@app.command()
def demo(wait: bool = True):
    """Generate and ingest a small synthetic color-study video."""
    workspace, db = context()
    job = db.enqueue("demo", {})
    wait_for(workspace, db, [job]) if wait else print_json(job)


@app.command()
def search(query: str = "", favorites: bool = False):
    """Search titles, descriptions, tags and transcripts. Outputs agent-readable JSON."""
    _, db = context()
    print_json(db.clips(query=query, favorite=favorites))


@app.command()
def assets():
    """List source assets and their online status."""
    _, db = context()
    print_json([db.asset(row["id"]) for row in db.all("SELECT id FROM assets")])


@app.command()
def jobs():
    """Inspect persistent jobs, errors, and output artifacts."""
    _, db = context()
    print_json(db.jobs())


@app.command()
def edit(
    brief: str,
    name: str = "Rough cut",
    duration: float = 30,
    pace: str = "balanced",
    look: str = "neutral",
    aspect: str = "16:9",
    ai: bool = False,
    wait: bool = True,
):
    """Assemble a rough cut; --ai uses the configured local planning model."""
    workspace, db = context()
    request = CreateEdit(
        name=name, brief=brief, duration=duration, pace=pace, look=look, aspect=aspect, use_ai=ai
    )
    job = db.enqueue("edit", request.model_dump())
    wait_for(workspace, db, [job]) if wait else print_json(job)


@app.command("import-plan")
def import_plan(path: Path, edit_id: str | None = None, revision: int | None = None):
    """Validate and save an external AI/Codex edit plan. Existing edits require a revision."""
    _, db = context()
    if edit_id:
        db.edit(edit_id)
    plan = validate_plan(db, EditPlan.model_validate_json(path.read_text()))
    print_json(db.save_edit(plan.model_dump(), edit_id, revision))


@app.command()
def schema():
    """Print the strict edit-plan JSON schema for an AI tool or external application."""
    print_json(EditPlan.model_json_schema())


@app.command()
def edits(edit_id: str | None = None):
    _, db = context()
    print_json(
        db.edit(edit_id) if edit_id else [db.edit(r["id"]) for r in db.all("SELECT id FROM edits")]
    )


@app.command()
def export(
    edit_id: str, format: Annotated[str, typer.Option("--format")] = "otio", wait: bool = True
):
    """Export otio, fcpxml, resolve, or preview using the current saved revision."""
    workspace, db = context()
    edit = db.edit(edit_id)
    if format not in {"otio", "fcpxml", "resolve", "preview"}:
        raise typer.BadParameter("Choose otio, fcpxml, resolve, or preview.")
    job = db.enqueue("export", {"edit_id": edit_id, "revision": edit["revision"], "format": format})
    wait_for(workspace, db, [job]) if wait else print_json(job)


@app.command()
def doctor():
    """Report prerequisites and workspace configuration."""
    import shutil

    workspace, _ = context()
    print_json(
        {
            "workspace": str(workspace.root),
            "ffmpeg": shutil.which("ffmpeg"),
            "ffprobe": shutil.which("ffprobe"),
            "settings": workspace.settings().model_dump(),
        }
    )

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from filelock import FileLock, Timeout

from vidac.config import Workspace
from vidac.db import Database, now
from vidac.editing import build_plan
from vidac.exporting import export_edit
from vidac.ingest import annotate_clip, generate_demo, ingest
from vidac.media import VIDEO_EXTENSIONS
from vidac.models import CreateEdit

log = logging.getLogger(__name__)


class Cancelled(Exception):
    pass


class Worker:
    """One durable consumer per workspace, shared by server and CLI."""

    def __init__(self, workspace: Workspace, db: Database):
        self.workspace, self.db = workspace, db
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.observed: dict[str, tuple[int, int, float]] = {}
        self.last_watch = 0.0
        self.active = False

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="vidac-worker")
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=8)

    def run(self):
        try:
            with FileLock(self.workspace.root / "worker.lock", timeout=0):
                self.active = True
                self.db.execute(
                    "UPDATE jobs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'queued' END, "
                    "stage='Recovered after interruption',updated_at=? WHERE status='running'",
                    (now(),),
                )
                while not self.stopping.is_set():
                    try:
                        if time.monotonic() - self.last_watch > 5:
                            self.watch()
                            self.last_watch = time.monotonic()
                        if not self.run_one():
                            self.stopping.wait(0.5)
                    except Exception:
                        log.exception("Worker iteration failed")
                        self.stopping.wait(1)
        except Timeout:
            log.info("Another process is consuming this workspace's queue.")
        finally:
            self.active = False

    def run_one(self) -> bool:
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM jobs WHERE status='queued' AND cancel_requested=0 ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                return False
            job = dict(row)
            conn.execute(
                "UPDATE jobs SET status='running',attempts=attempts+1,updated_at=? WHERE id=?",
                (now(), job["id"]),
            )
        job_id = job["id"]

        def check():
            if self.stopping.is_set() or self.db.job(job_id)["cancel_requested"]:
                raise Cancelled()

        def progress(value: float, stage: str):
            check()
            self.db.execute(
                "UPDATE jobs SET progress=?,stage=?,updated_at=? WHERE id=?",
                (min(1, max(0, value)), stage, now(), job_id),
            )

        try:
            payload = json.loads(job["payload"])
            check()
            if job["kind"] in {"ingest", "demo"}:
                path = (
                    generate_demo(self.workspace, check)
                    if job["kind"] == "demo"
                    else Path(payload["path"])
                )
                result = ingest(self.workspace, self.db, path, progress, check)
            elif job["kind"] == "annotate":
                progress(0.1, "Understanding clip with local vision model")
                result = annotate_clip(self.workspace, self.db, payload["clip_id"], check)
            elif job["kind"] == "edit":
                progress(0.1, "Planning your rough cut")
                plan = build_plan(self.workspace, self.db, CreateEdit.model_validate(payload))
                check()
                # Stable ID makes retries safe if a crash happens after the edit commit.
                edit_id = "edit_" + job_id.removeprefix("job_")
                existing = self.db.one("SELECT id FROM edits WHERE id=?", (edit_id,))
                edit = (
                    self.db.edit(edit_id)
                    if existing
                    else self.db.save_edit(plan.model_dump(), edit_id)
                )
                result = {"edit_id": edit["id"]}
            elif job["kind"] == "export":
                result = export_edit(
                    self.workspace,
                    self.db,
                    payload["edit_id"],
                    payload["revision"],
                    payload["format"],
                    progress,
                    check,
                )
            else:
                raise ValueError(f"Unknown job type: {job['kind']}")
            self.db.execute(
                "UPDATE jobs SET status='succeeded',progress=1,stage='Complete',result=?,error=NULL,updated_at=? WHERE id=?",
                (json.dumps(result), now(), job_id),
            )
        except Cancelled:
            state = (
                "queued"
                if self.stopping.is_set() and not self.db.job(job_id)["cancel_requested"]
                else "cancelled"
            )
            self.db.execute(
                "UPDATE jobs SET status=?,stage=?,updated_at=? WHERE id=?",
                (state, "Interrupted" if state == "queued" else "Cancelled", now(), job_id),
            )
        except Exception as exc:
            log.exception("Job %s failed", job_id)
            self.db.execute(
                "UPDATE jobs SET status='failed',stage='Needs attention',error=?,updated_at=? WHERE id=?",
                (str(exc)[:4000], now(), job_id),
            )
        return True

    def watch(self):
        for folder in self.workspace.settings().watch_folders:
            root = Path(folder).expanduser()
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if self.stopping.is_set():
                    return
                if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                path = path.resolve()
                if path.is_relative_to(self.workspace.root):
                    continue
                stat = path.stat()
                key = str(path)
                old = self.observed.get(key)
                if old is None or old[:2] != (stat.st_size, stat.st_mtime_ns):
                    self.observed[key] = (stat.st_size, stat.st_mtime_ns, time.monotonic())
                    continue
                if time.monotonic() - old[2] < 15 or time.time() - stat.st_mtime < 15:
                    continue
                seen = self.db.one("SELECT * FROM watched_files WHERE path=?", (key,))
                if seen and (seen["size"], seen["mtime"]) == (stat.st_size, stat.st_mtime):
                    continue
                job = self.db.enqueue("ingest", {"path": key})
                self.db.execute(
                    "INSERT OR REPLACE INTO watched_files VALUES(?,?,?,?)",
                    (key, stat.st_size, stat.st_mtime, job["id"]),
                )

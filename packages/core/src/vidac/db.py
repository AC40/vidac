from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
 id TEXT PRIMARY KEY, path TEXT NOT NULL, name TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
 size INTEGER NOT NULL, duration REAL NOT NULL, fps TEXT NOT NULL, frames INTEGER NOT NULL,
 width INTEGER NOT NULL, height INTEGER NOT NULL, has_audio INTEGER NOT NULL,
 metadata TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'processing',
 proxy_path TEXT, thumb_path TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
 id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
 start_frame INTEGER NOT NULL, end_frame INTEGER NOT NULL,
 shot_start INTEGER NOT NULL, shot_end INTEGER NOT NULL,
 title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',
 favorite INTEGER NOT NULL DEFAULT 0, annotation TEXT NOT NULL DEFAULT '{}',
 provenance TEXT NOT NULL, thumb_path TEXT, created_at TEXT NOT NULL,
 CHECK(start_frame >= 0 AND end_frame > start_frame)
);
CREATE INDEX IF NOT EXISTS clips_asset ON clips(asset_id);
CREATE VIRTUAL TABLE IF NOT EXISTS clips_fts USING fts5(id UNINDEXED, title, description, tags);
CREATE TRIGGER IF NOT EXISTS clips_ai AFTER INSERT ON clips BEGIN
 INSERT INTO clips_fts(id,title,description,tags) VALUES(new.id,new.title,new.description,
   (SELECT group_concat(value,' ') FROM json_each(new.tags)));
END;
CREATE TRIGGER IF NOT EXISTS clips_au AFTER UPDATE ON clips BEGIN
 DELETE FROM clips_fts WHERE id=old.id;
 INSERT INTO clips_fts(id,title,description,tags) VALUES(new.id,new.title,new.description,
   (SELECT group_concat(value,' ') FROM json_each(new.tags)));
END;
CREATE TRIGGER IF NOT EXISTS clips_ad AFTER DELETE ON clips BEGIN
 DELETE FROM clips_fts WHERE id=old.id;
END;
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
 payload TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0, stage TEXT NOT NULL DEFAULT 'Waiting',
 error TEXT, result TEXT, attempts INTEGER NOT NULL DEFAULT 0,
 cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created_at);
CREATE TABLE IF NOT EXISTS edits (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL, plan TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edit_versions (
 edit_id TEXT NOT NULL REFERENCES edits(id), revision INTEGER NOT NULL,
 plan TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(edit_id, revision)
);
CREATE TABLE IF NOT EXISTS artifacts (
 id TEXT PRIMARY KEY, edit_id TEXT NOT NULL REFERENCES edits(id), revision INTEGER NOT NULL,
 kind TEXT NOT NULL, path TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watched_files (
 path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL, job_id TEXT NOT NULL
);
PRAGMA user_version = 2;
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise RuntimeError("This library was created by a newer version of Vidac.")
            # Schema, triggers, derived search index and version commit together.
            before = (
                "DROP TRIGGER IF EXISTS clips_ai; DROP TRIGGER IF EXISTS clips_au;"
                if version == 1
                else ""
            )
            after = (
                (
                    "DELETE FROM clips_fts; "
                    "INSERT INTO clips_fts(id,title,description,tags) SELECT id,title,description, "
                    "(SELECT group_concat(value,' ') FROM json_each(clips.tags)) FROM clips;"
                )
                if version == 1
                else ""
            )
            conn.executescript("BEGIN IMMEDIATE;" + before + SCHEMA + after + "COMMIT;")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def one(self, sql: str, params=()) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql: str, params=()) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def execute(self, sql: str, params=()) -> None:
        with self.connect() as conn:
            conn.execute(sql, params)

    def asset(self, asset_id: str) -> dict:
        row = self.one("SELECT * FROM assets WHERE id=?", (asset_id,))
        if row is None:
            raise KeyError("Asset not found")
        row["metadata"] = json.loads(row["metadata"])
        row["online"] = Path(row["path"]).is_file()
        return row

    def clip(self, clip_id: str) -> dict:
        rows = self.clips(clip_id=clip_id)
        if not rows:
            raise KeyError("Clip not found")
        return rows[0]

    def clips(self, query="", asset_id="", favorite=False, clip_id="", limit=1000) -> list[dict]:
        conditions, params = [], []
        terms = re.findall(r"[^\W_]+", query, flags=re.UNICODE)
        if terms:
            # Quote tokens instead of exposing FTS operators to user queries.
            match = " AND ".join(f'"{word}"*' for word in terms[:30])
            conditions.append("c.id IN (SELECT id FROM clips_fts WHERE clips_fts MATCH ?)")
            params.append(match)
        if asset_id:
            conditions.append("c.asset_id=?")
            params.append(asset_id)
        if favorite:
            conditions.append("c.favorite=1")
        if clip_id:
            conditions.append("c.id=?")
            params.append(clip_id)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self.all(
            "SELECT c.*, a.name AS asset_name, a.fps, a.frames AS asset_frames, "
            "a.duration AS asset_duration, a.width, a.height, a.has_audio, a.status AS asset_status "
            "FROM clips c JOIN assets a ON a.id=c.asset_id"
            + where
            + " ORDER BY a.created_at DESC, c.start_frame ASC, c.id ASC LIMIT ?",
            (*params, limit),
        )
        for row in rows:
            row["tags"] = json.loads(row["tags"])
            row["annotation"] = json.loads(row["annotation"])
            row["favorite"] = bool(row["favorite"])
        return rows

    def enqueue(self, kind: str, payload: dict) -> dict:
        job_id = uid("job")
        timestamp = now()
        self.execute(
            "INSERT INTO jobs(id,kind,payload,created_at,updated_at) VALUES(?,?,?,?,?)",
            (job_id, kind, json.dumps(payload), timestamp, timestamp),
        )
        return self.job(job_id)

    def job(self, job_id: str) -> dict:
        row = self.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if row is None:
            raise KeyError("Job not found")
        for key in ("payload", "result"):
            row[key] = json.loads(row[key]) if row[key] else None
        return row

    def jobs(self):
        return [
            self.job(row["id"])
            for row in self.all("SELECT id FROM jobs ORDER BY created_at DESC LIMIT 100")
        ]

    def edit(self, edit_id: str, revision: int | None = None):
        row = self.one("SELECT * FROM edits WHERE id=?", (edit_id,))
        if row is None:
            raise KeyError("Edit not found")
        if revision is not None:
            version = self.one(
                "SELECT plan,revision FROM edit_versions WHERE edit_id=? AND revision=?",
                (edit_id, revision),
            )
            if not version:
                raise KeyError("Revision not found")
            row.update(version)
        row["plan"] = json.loads(row["plan"])
        row["artifacts"] = self.all(
            "SELECT id,kind,revision,created_at FROM artifacts WHERE edit_id=? ORDER BY created_at DESC",
            (edit_id,),
        )
        row["versions"] = self.all(
            "SELECT revision,created_at FROM edit_versions WHERE edit_id=? ORDER BY revision DESC",
            (edit_id,),
        )
        return row

    def save_edit(
        self, plan: dict, edit_id: str | None = None, expected_revision: int | None = None
    ):
        edit_id = edit_id or uid("edit")
        timestamp, serialized = now(), json.dumps(plan)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT revision FROM edits WHERE id=?", (edit_id,)).fetchone()
            if old and expected_revision != old["revision"]:
                raise ValueError("This edit changed in another session. Reload before saving.")
            revision = old["revision"] + 1 if old else 1
            if old:
                conn.execute(
                    "UPDATE edits SET name=?,revision=?,plan=?,updated_at=? WHERE id=?",
                    (plan["name"], revision, serialized, timestamp, edit_id),
                )
            else:
                conn.execute(
                    "INSERT INTO edits VALUES(?,?,?,?,?,?)",
                    (edit_id, plan["name"], revision, serialized, timestamp, timestamp),
                )
            conn.execute(
                "INSERT INTO edit_versions VALUES(?,?,?,?)",
                (edit_id, revision, serialized, timestamp),
            )
        return self.edit(edit_id)

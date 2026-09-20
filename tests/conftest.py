import json
import shutil
from fractions import Fraction

import pytest
from vidac.config import Workspace
from vidac.db import Database, now
from vidac.media import digest


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path / "workspace")


@pytest.fixture
def db(workspace):
    return Database(workspace.db_path)


@pytest.fixture
def seeded(db, tmp_path):
    """Small metadata catalogue; deliberately not playable media."""
    for index, (fps, frames, title, tags) in enumerate(
        [
            ("25/1", 200, "Working at desk", ["desk", "typing", "wide"]),
            ("30000/1001", 180, "Brushing teeth", ["bathroom", "routine", "close-up"]),
        ]
    ):
        path = tmp_path / f"source {index}.mp4"
        path.write_bytes(f"source-{index}".encode())
        db.execute(
            "INSERT INTO assets(id,path,name,sha256,size,duration,fps,frames,width,height,has_audio,metadata,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"asset_{index}",
                str(path),
                path.name,
                digest(path),
                path.stat().st_size,
                float(frames / Fraction(fps)),
                fps,
                frames,
                1920,
                1080,
                index == 0,
                json.dumps({"color_transfer": "bt709"}),
                "ready",
                now(),
            ),
        )
        db.execute(
            "INSERT INTO clips(id,asset_id,start_frame,end_frame,shot_start,shot_end,title,tags,provenance,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                f"clip_{index}",
                f"asset_{index}",
                0,
                frames,
                0,
                frames,
                title,
                json.dumps(tags),
                "manual",
                now(),
            ),
        )
    return db


@pytest.fixture
def require_media():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe are required for media integration tests")

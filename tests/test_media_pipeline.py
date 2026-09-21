from fractions import Fraction
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from vidac.api import create_app
from vidac.editing import build_plan, validate_plan
from vidac.exporting import export_edit
from vidac.ingest import generate_demo, ingest
from vidac.media import digest, ffmpeg, noop, probe
from vidac.models import CreateEdit, EditPlan

pytestmark = pytest.mark.media


def progress(*_):
    pass


def test_real_ingest_edit_render_export(workspace, db, require_media):
    source = generate_demo(workspace, noop)
    fingerprint = digest(source)
    result = ingest(workspace, db, source, progress, noop)
    assert result["shots"] == 3
    clips = sorted(db.clips(), key=lambda c: c["start_frame"])
    assert [(c["start_frame"], c["end_frame"]) for c in clips] == [(0, 100), (100, 200), (200, 300)]
    assert all(c["provenance"] == "shot-detector" for c in clips)
    assert digest(source) == fingerprint
    assert ingest(workspace, db, source, progress, noop)["duplicate"]
    assert len(db.clips()) == 3
    plan = build_plan(workspace, db, CreateEdit(duration=6, pace="energetic", look="warm"))
    plan.items[1].audio = False
    plan.items[2].look = "monochrome"
    edit = db.save_edit(plan.model_dump())
    preview = export_edit(workspace, db, edit["id"], 1, "preview", progress)
    info = probe(Path(preview["path"]))
    assert info["frames"] == 150
    assert info["fps"] == "25/1"
    assert info["width"] == 960
    assert info["has_audio"]
    assert info["duration"] == pytest.approx(6, abs=1 / 25)
    raw = info["metadata"]["probe"]
    audio = next(s for s in raw["streams"] if s["codec_type"] == "audio")
    assert float(audio["duration"]) == pytest.approx(6, abs=0.025)
    assert digest(source) == fingerprint
    for kind in ("otio", "fcpxml", "resolve"):
        assert Path(export_edit(workspace, db, edit["id"], 1, kind, progress)["path"]).is_file()
    with TestClient(create_app(workspace, run_worker=False)) as client:
        ranged = client.get(
            f"/api/media/{result['asset_id']}/proxy", headers={"Range": "bytes=0-99"}
        )
        assert ranged.status_code == 206
        assert len(ranged.content) == 100
        assert (
            client.get(f"/api/thumbnails/{clips[0]['id']}").headers["content-type"] == "image/jpeg"
        )
        assert client.get(preview["url"]).status_code == 200
    # Deleting a DERIVATIVE, never an original, must allow safe cache rebuild without new clips.
    proxy = Path(db.asset(result["asset_id"])["proxy_path"])
    proxy.unlink()
    # A moved original reimport reconnects the existing asset and rebuilds its proxy.
    moved = source.with_name("renamed-original.mp4")
    source.rename(moved)
    assert ingest(workspace, db, moved, progress, noop)["duplicate"]
    assert db.asset(result["asset_id"])["path"] == str(moved)
    assert db.edit(edit["id"])["plan"] == edit["plan"]
    assert proxy.is_file()
    assert len(db.clips()) == 3


def test_fractional_rate_silent_portrait_and_quoted_path(tmp_path, require_media):
    from vidac.config import Workspace
    from vidac.db import Database

    workspace = Workspace(tmp_path / "creator's footage workspace")
    db = Database(workspace.db_path)
    source = workspace.root / "demo" / "portrait clip.mp4"
    ffmpeg(
        [
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=180x320:r=30000/1001:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ]
    )
    ingest(workspace, db, source, progress, noop)
    clip = db.clips()[0]
    assert clip["fps"] == "30000/1001"
    plan = validate_plan(
        db,
        EditPlan(
            name="Frame accuracy",
            fps="30000/1001",
            width=1080,
            height=1920,
            items=[
                {"clip_id": clip["id"], "source_in": 10, "source_out": 17, "look": "cool"}
                for _ in range(8)
            ],
        ),
    )
    edit = db.save_edit(plan.model_dump())
    result = export_edit(workspace, db, edit["id"], 1, "preview", progress)
    info = probe(Path(result["path"]))
    assert info["frames"] == 56
    assert info["width"] == 540 and info["height"] == 960
    assert info["duration"] == pytest.approx(
        float(56 / Fraction(plan.fps)), abs=1 / float(Fraction(plan.fps))
    )
    assert info["has_audio"]  # Silent bed, not a missing stream between shots.


def test_vfr_conforms_to_referenced_intermediate(workspace, db, require_media):
    source = workspace.root / "demo" / "phone-vfr.mp4"
    ffmpeg(
        [
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=30:d=3",
            "-vf",
            "select='if(lt(t,1),1,not(mod(n,2)))'",
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ]
    )
    assert probe(source)["metadata"]["possible_vfr"]
    original = digest(source)
    result = ingest(workspace, db, source, progress, noop)
    asset = db.asset(result["asset_id"])
    assert asset["metadata"]["conformed"]
    assert Path(asset["metadata"]["edit_path"]).is_file()
    assert digest(source) == original
    assert all(c["end_frame"] <= asset["frames"] for c in db.clips())

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner
from vidac.cli import app
from vidac.editing import build_plan
from vidac.exporting import source_path
from vidac.jobs import Cancelled, Worker
from vidac.media import digest, noop
from vidac.models import CreateEdit
from vidac.relink import fingerprint, relink_file, search_relinks


def search(db, root, check=noop):
    return search_relinks(db, root, lambda *_: None, check)


def test_recursive_renamed_match_preserves_clips_and_revisions(workspace, seeded, tmp_path):
    edit = seeded.save_edit(build_plan(workspace, seeded, CreateEdit()).model_dump())
    clips = seeded.clips()
    before = seeded.asset("asset_0")
    root = tmp_path / "new drive"
    nested = root / "shoot" / "Zähne"
    nested.mkdir(parents=True)
    moved = nested / "renamed.video"
    Path(before["path"]).rename(moved)
    # Same filename and size are insufficient evidence.
    (root / before["name"]).write_bytes(b"wrong!!!")
    result = search(seeded, root)
    assert [r["asset_id"] for r in result["relinked"]] == ["asset_0"]
    assert not result["missing"]
    after = seeded.asset("asset_0")
    assert after == before | {"path": str(moved)}
    assert source_path(after) == moved
    assert seeded.clips() == clips
    assert seeded.edit(edit["id"]) == edit
    assert not search(seeded, root)["relinked"]


def test_no_false_matches_and_online_sources_stay_put(seeded, tmp_path):
    old = seeded.asset("asset_0")
    Path(old["path"]).rename(tmp_path / "outside-search")
    root = tmp_path / "search"
    root.mkdir()
    (root / old["name"]).write_bytes(b"wrong!!!")
    online = seeded.asset("asset_1")
    (root / "copy.mp4").write_bytes(Path(online["path"]).read_bytes())
    result = search(seeded, root)
    assert not result["relinked"]
    assert result["missing"][0]["asset_id"] == "asset_0"
    assert seeded.asset("asset_1") == online


def test_conform_reference_is_preserved(seeded, tmp_path):
    conform = tmp_path / "conformed.mov"
    metadata = {"conformed": True, "edit_path": str(conform)}
    seeded.execute("UPDATE assets SET metadata=? WHERE id='asset_0'", (json.dumps(metadata),))
    moved = tmp_path / "moved.mp4"
    Path(seeded.asset("asset_0")["path"]).rename(moved)
    asset = relink_file(seeded, "asset_0", moved)
    assert asset["metadata"] == metadata
    assert source_path(asset) == conform


def test_unstable_file_rejected(seeded, tmp_path, monkeypatch):
    path = Path(seeded.asset("asset_0")["path"])
    old_path = str(path)
    moved = tmp_path / "moved.mp4"
    path.rename(moved)
    real_digest = digest

    def mutate(path, check):
        sha = real_digest(path, check)
        path.write_bytes(b"changed after hashing")
        return sha

    monkeypatch.setattr("vidac.relink.digest", mutate)
    with pytest.raises(ValueError, match="changed"):
        relink_file(seeded, "asset_0", moved)
    assert seeded.asset("asset_0")["path"] == old_path
    with pytest.raises(ValueError, match="changed"):
        fingerprint(moved)


def test_cancellation_and_unreadable_candidates(seeded, tmp_path, monkeypatch):
    root = tmp_path / "search"
    root.mkdir()
    Path(seeded.asset("asset_0")["path"]).rename(root / "moved.mp4")

    def cancel():
        raise Cancelled()

    with pytest.raises(Cancelled):
        search(seeded, root, cancel)
    assert not seeded.asset("asset_0")["online"]

    def unreadable(*_):
        raise PermissionError("Cannot read candidate")

    monkeypatch.setattr("vidac.relink.fingerprint", unreadable)
    result = search(seeded, root)
    assert result["warnings"] == ["Cannot read candidate"]
    assert len(result["missing"]) == 1


def test_cli_search_queues_relink(workspace, seeded, tmp_path, monkeypatch):
    monkeypatch.setenv("VIDAC_WORKSPACE", str(workspace.root))
    result = CliRunner().invoke(app, ["relink", str(tmp_path), "--no-wait"])
    assert result.exit_code == 0, result.output
    job = json.loads(result.output)
    assert job["kind"] == "relink"
    Worker(workspace, seeded).run_one()
    assert seeded.job(job["id"])["status"] == "succeeded"

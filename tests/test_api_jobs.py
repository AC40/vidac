import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from vidac.api import create_app
from vidac.editing import build_plan
from vidac.jobs import Cancelled, Worker
from vidac.models import CreateEdit

HEADERS = {"X-Vidac-Client": "workspace"}


@pytest.fixture
def client(workspace, seeded):
    with TestClient(create_app(workspace, run_worker=False), headers=HEADERS) as client:
        yield client


def test_api_clip_patch_search_and_validation(client):
    assert client.get("/api/health").json()["status"] == "ok"
    response = client.patch(
        "/api/clips/clip_0",
        json={"title": "Desk details", "tags": [" typing ", "typing"], "favorite": True},
    )
    assert response.status_code == 200
    assert response.json()["tags"] == ["typing"]
    assert client.get("/api/clips?q=details&favorite=true").json()[0]["id"] == "clip_0"
    assert (
        client.patch("/api/clips/clip_0", json={"source_out": 0, "end_frame": 999}).status_code
        == 400
    )
    assert (
        client.patch("/api/clips/clip_0", json={"start_frame": 5, "end_frame": 4}).status_code
        == 400
    )
    assert client.get("/api/clips/nonexistent").status_code == 404
    assert client.post("/api/clips/clip_0/analyze").status_code == 400


def test_loopback_api_write_protection(client):
    assert client.post("/api/demo", headers={"X-Vidac-Client": ""}).status_code == 403
    assert (
        client.post("/api/demo", headers={"Origin": "https://untrusted.example"}).status_code == 403
    )
    assert client.post("/api/demo", headers={"Origin": "http://localhost:5173"}).status_code == 202


def test_private_proxy_hosts_are_opt_in(workspace, monkeypatch):
    host = "vidac.example-tailnet.ts.net:5173"
    with TestClient(create_app(workspace, run_worker=False)) as client:
        assert client.get("/api/health", headers={"Host": host}).status_code == 400
    monkeypatch.setenv("VIDAC_ALLOWED_HOSTS", " vidac.example-tailnet.ts.net,100.64.0.10 ")
    with TestClient(create_app(workspace, run_worker=False)) as client:
        assert client.get("/api/health", headers={"Host": host}).status_code == 200
        assert client.get("/api/health", headers={"Host": "100.64.0.10:5173"}).status_code == 200
        assert client.get("/api/health", headers={"Host": "untrusted.example"}).status_code == 400
        headers = {**HEADERS, "Host": host, "Origin": f"http://{host}"}
        assert client.post("/api/demo", headers=headers).status_code == 202
        headers["Origin"] = "https://untrusted.example"
        assert client.post("/api/demo", headers=headers).status_code == 403
        assert client.post("/api/demo", headers={"Host": host}).status_code == 403


@pytest.mark.parametrize("hosts", ["*", "*.ts.net", "https://server.ts.net", "server.ts.net:5173"])
def test_private_proxy_rejects_broad_or_malformed_hosts(workspace, monkeypatch, hosts):
    monkeypatch.setenv("VIDAC_ALLOWED_HOSTS", hosts)
    with pytest.raises(ValueError, match="exact hostnames"):
        create_app(workspace, run_worker=False)


def test_enqueue_cancel_retry(client, workspace, seeded):
    job = client.post("/api/edits", json={"brief": "desk", "duration": 4}).json()
    assert job["status"] == "queued"
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "cancelled"
    assert not Worker(workspace, seeded).run_one()
    assert client.post(f"/api/jobs/{job['id']}/retry").json()["status"] == "queued"
    assert Worker(workspace, seeded).run_one()
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "succeeded"
    edit = client.get(f"/api/edits/{done['result']['edit_id']}").json()
    assert edit["revision"] == 1
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 400


def test_api_revision_conflict_and_export_snapshot(client, workspace, seeded):
    edit = seeded.save_edit(build_plan(workspace, seeded, CreateEdit()).model_dump())
    edit_id = edit["id"]
    result = client.put(f"/api/edits/{edit_id}", json={"revision": 1, "plan": edit["plan"]})
    assert result.status_code == 200
    assert (
        client.put(f"/api/edits/{edit_id}", json={"revision": 1, "plan": edit["plan"]}).status_code
        == 409
    )
    queued = client.post(
        f"/api/edits/{edit_id}/export", json={"format": "otio", "revision": 1}
    ).json()
    assert queued["payload"]["revision"] == 1
    assert client.post(f"/api/edits/{edit_id}/export", json={"format": "bad"}).status_code == 400


def test_missing_and_exact_hash_relink(client, seeded, tmp_path):
    asset = seeded.asset("asset_0")
    moved = tmp_path / "relocated.mp4"
    Path(asset["path"]).rename(moved)
    assert (
        client.post(
            "/api/assets/asset_0/relink", json={"path": seeded.asset("asset_1")["path"]}
        ).status_code
        == 400
    )
    assert client.post("/api/assets/asset_0/relink", json={"path": str(moved)}).json()["online"]


def test_validate_entire_import_before_enqueue(client, seeded):
    before = len(seeded.jobs())
    result = client.post(
        "/api/ingest", json={"paths": [seeded.asset("asset_0")["path"], "/does/not/exist.mp4"]}
    )
    assert result.status_code == 400
    assert len(seeded.jobs()) == before


def test_settings_disallow_watching_workspace(client, workspace):
    settings = client.get("/api/settings").json()
    settings["watch_folders"] = [str(workspace.root)]
    assert client.put("/api/settings", json=settings).status_code == 400
    settings["watch_folders"] = []
    settings["select_seconds"] = 5
    assert client.put("/api/settings", json=settings).status_code == 200
    assert workspace.settings().select_seconds == 5


def test_worker_failure_persists(db, workspace):
    job = db.enqueue("ingest", {"path": "/missing/original.mp4"})
    assert Worker(workspace, db).run_one()
    assert db.job(job["id"])["status"] == "failed"
    assert "unavailable" in db.job(job["id"])["error"]


def test_cooperative_cancellation(db, workspace, monkeypatch):
    job = db.enqueue("ingest", {"path": "anything"})

    def cancel(*args):
        raise Cancelled()

    monkeypatch.setattr("vidac.jobs.ingest", cancel)
    Worker(workspace, db).run_one()
    assert db.job(job["id"])["status"] == "cancelled"


def test_recover_interrupted_jobs_and_single_worker(db, workspace):
    job = db.enqueue("unknown", {})
    db.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
    worker = Worker(workspace, db)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while db.job(job["id"])["status"] != "failed" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert db.job(job["id"])["status"] == "failed"
        assert db.job(job["id"])["attempts"] == 1
        competitor = Worker(workspace, db)
        competitor.start()
        competitor.thread.join(timeout=2)
        assert not competitor.active
        assert worker.active
    finally:
        worker.stop()


def test_watch_requires_stable_file(db, workspace, tmp_path, monkeypatch):
    folder = tmp_path / "inbox"
    folder.mkdir()
    path = folder / "recording.mp4"
    path.write_bytes(b"still copying")
    settings = workspace.settings().model_copy(update={"watch_folders": [str(folder)]})
    workspace.save_settings(settings)
    worker = Worker(workspace, db)
    worker.watch()
    assert not db.jobs()
    size, mtime, observed = worker.observed[str(path)]
    worker.observed[str(path)] = (size, mtime, observed - 20)
    real_now = time.time()
    monkeypatch.setattr("vidac.jobs.time.time", lambda: real_now + 20)
    worker.watch()
    worker.watch()
    assert len(db.jobs()) == 1

import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner
from vidac.api import create_app
from vidac.cli import app
from vidac.cli_providers import event_usage, parse_events, run_cli, strict_schema
from vidac.config import Settings
from vidac.editing import build_plan
from vidac.ingest import annotate_clip
from vidac.jobs import Cancelled, Worker
from vidac.media import noop
from vidac.model_catalogue import models_for
from vidac.models import CreateEdit, EditPlan, SegmentAnalysis
from vidac.providers import CLIProvider, intelligence


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr("vidac.cli_providers.shutil.which", lambda name: f"/bin/{name}")


def test_existing_settings_keep_ollama_defaults():
    settings = Settings.model_validate(
        {"vision_model": "local-vision", "planning_model": "local-plan"}
    )
    assert settings.vision_provider == settings.planning_provider == "ollama"
    assert not settings.allow_remote_ai


@pytest.mark.parametrize("provider", ["codex", "claude", "opencode"])
def test_cli_requires_data_sharing_opt_in(provider, installed):
    with pytest.raises(ValueError, match="data sharing"):
        intelligence(
            Settings(planning_provider=provider, planning_model="provider/model"), "planning"
        )


@pytest.mark.parametrize("value", ["--model bad", "name\n--flag", "x" * 201])
def test_model_ids_cannot_be_flags(value):
    with pytest.raises(ValidationError):
        Settings(planning_model=value)


def test_invalid_provider_and_opencode_model(installed):
    with pytest.raises(ValidationError):
        Settings(planning_provider="t3")
    with pytest.raises(ValueError, match="provider/model"):
        intelligence(
            Settings(
                planning_provider="opencode", planning_model="no-provider", allow_remote_ai=True
            ),
            "planning",
        )


@pytest.mark.parametrize("provider", ["codex", "claude", "opencode"])
def test_cli_transports_send_model_schema_and_real_image_bytes(
    provider, tmp_path, monkeypatch, installed
):
    frame = tmp_path / "frame with spaces.jpg"
    frame.write_bytes(b"frame-bytes")
    response = {"segments": [{"title": "Desk", "start_seconds": 0, "end_seconds": 8}]}

    def run(args, **kwargs):
        assert args[args.index("--model") + 1] == "provider/model"
        assert kwargs["cwd"] != tmp_path
        assert kwargs["timeout"] == 123
        assert "Describe" in kwargs["stdin"]
        if provider == "codex":
            assert args[args.index("--image") + 1] == str(frame)
            assert args[args.index("--sandbox") + 1] == "read-only"
            assert "--ignore-user-config" in args
            assert "features.shell_tool=false" in args
            schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
            assert schema["additionalProperties"] is False
            Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(response))
            return "ignored progress logs"
        if provider == "claude":
            payload = json.loads(kwargs["stdin"])
            assert payload["message"]["content"][1]["source"]["data"] == "ZnJhbWUtYnl0ZXM="
            assert args[args.index("--tools") + 1] == ""
            assert "--strict-mcp-config" in args
            assert "--bare" not in args  # bare mode would bypass subscription login
            return json.dumps(
                {"type": "result", "subtype": "success", "structured_output": response}
            )
        assert args[args.index("--file") + 1] == str(frame)
        config = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
        assert config["permission"] == {"*": "deny"}
        assert config["share"] == "disabled"
        assert "--pure" in args
        return "\n".join(
            json.dumps(e)
            for e in [
                {"type": "text", "part": {"text": json.dumps(response)}},
                {"type": "step_finish", "part": {"reason": "stop"}},
            ]
        )

    monkeypatch.setattr("vidac.providers.run_cli", run)
    result = CLIProvider(
        Settings(allow_remote_ai=True, model_timeout_seconds=123), provider
    ).generate("provider/model", "Describe", SegmentAnalysis.model_json_schema(), [frame])
    assert SegmentAnalysis.model_validate(result).segments[0].title == "Desk"


def test_strict_schema_closes_nested_objects_without_mutating_domain_schema():
    schema = EditPlan.model_json_schema()
    strict = strict_schema(schema)
    assert "default" in schema["properties"]["fps"]
    assert "default" not in strict["properties"]["fps"]
    item = strict["$defs"]["EditItem"]
    assert set(item["required"]) == set(item["properties"])
    assert item["additionalProperties"] is False


@pytest.mark.parametrize(
    "provider,events",
    [
        ("claude", [{"type": "result", "subtype": "error_max_turns", "is_error": True}]),
        ("claude", [{"type": "assistant", "message": "partial"}]),
        ("opencode", [{"type": "error", "error": {"message": "auth"}}]),
        ("opencode", [{"type": "text", "part": {"text": "{}"}}]),
        ("opencode", [{"type": "step_finish", "part": {"reason": "length"}}]),
    ],
)
def test_failed_or_incomplete_events_are_never_a_plan(provider, events):
    with pytest.raises((RuntimeError, ValueError)):
        parse_events(provider, "\n".join(json.dumps(e) for e in events))


def test_opencode_usage_is_normalized():
    raw = json.dumps(
        {
            "type": "step_finish",
            "part": {
                "reason": "stop",
                "cost": 0.0123,
                "tokens": {
                    "input": 1200,
                    "output": 80,
                    "reasoning": 10,
                    "cache": {"read": 40, "write": 20},
                },
            },
        }
    )
    assert event_usage("opencode", raw) == {
        "input_tokens": 1200,
        "output_tokens": 80,
        "reasoning_tokens": 10,
        "cache_read_tokens": 40,
        "cache_write_tokens": 20,
        "cost_usd": 0.0123,
    }


@pytest.mark.parametrize("provider", ["codex", "claude", "opencode"])
def test_planning_routes_and_records_provenance(
    provider, workspace, seeded, monkeypatch, installed
):
    workspace.save_settings(
        Settings(planning_provider=provider, planning_model="provider/model", allow_remote_ai=True)
    )
    monkeypatch.setattr(
        CLIProvider,
        "generate",
        lambda *args: {
            "name": "Proposal",
            "items": [{"clip_id": "clip_0", "source_in": 0, "source_out": 50}],
        },
    )
    plan = build_plan(workspace, seeded, CreateEdit(use_ai=True))
    assert plan.planner == f"{provider}:provider/model"
    assert plan.items[0].timeline_frames == 50
    monkeypatch.setattr(
        CLIProvider,
        "generate",
        lambda *args: {
            "name": "Invalid",
            "items": [{"clip_id": "invented", "source_in": 0, "source_out": 50}],
        },
    )
    with pytest.raises(ValueError, match="outside your supplied catalogue"):
        build_plan(workspace, seeded, CreateEdit(use_ai=True))


def test_job_cancellation_reaches_cli_planner(workspace, seeded, monkeypatch, installed):
    workspace.save_settings(
        Settings(planning_provider="codex", planning_model="chosen", allow_remote_ai=True)
    )
    job = seeded.enqueue("edit", CreateEdit(use_ai=True).model_dump())

    def generate(self, *args):
        seeded.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job["id"],))
        self.check()

    monkeypatch.setattr(CLIProvider, "generate", generate)
    Worker(workspace, seeded).run_one()
    assert seeded.job(job["id"])["status"] == "cancelled"
    assert seeded.all("SELECT id FROM edits") == []


def test_subprocess_stdin_exit_errors_and_timeout(tmp_path):
    assert (
        run_cli(
            [sys.executable, "-c", "import sys; print(sys.stdin.read())"],
            cwd=tmp_path,
            stdin="hello",
        ).strip()
        == "hello"
    )
    with pytest.raises(RuntimeError, match="Sign in") as error:
        run_cli(
            [sys.executable, "-c", "import sys; print('auth error secret-token'); sys.exit(1)"],
            cwd=tmp_path,
        )
    assert "secret-token" not in str(error.value)
    with pytest.raises(RuntimeError, match="timeout"):
        run_cli([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path, timeout=0.05)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group check")
def test_cancellation_kills_descendant_process(tmp_path):
    marker = tmp_path / "descendant-finished"
    started = tmp_path / "started"
    child = f"import time; from pathlib import Path; time.sleep(0.6); Path({str(marker)!r}).touch()"
    code = f"import subprocess, sys, time; from pathlib import Path; subprocess.Popen([sys.executable, '-c', {child!r}]); Path({str(started)!r}).touch(); time.sleep(30)"

    def check():
        if started.exists():
            raise Cancelled()

    with pytest.raises(Cancelled):
        run_cli([sys.executable, "-c", code], cwd=tmp_path, check=check)
    time.sleep(0.7)
    assert not marker.exists()


def test_model_discovery_and_api_settings(workspace, monkeypatch, installed, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(
        json.dumps({"models": [{"slug": "my-codex-model", "visibility": "list"}]})
    )
    assert models_for(Settings(), "codex")["models"] == ["my-codex-model"]
    assert models_for(Settings(), "claude")["models"] == ["sonnet", "opus", "haiku"]
    with TestClient(create_app(workspace, run_worker=False)) as client:
        response = client.put(
            "/api/settings",
            headers={"X-Vidac-Client": "workspace"},
            json={
                "planning_provider": "codex",
                "planning_model": "my-codex-model",
                "allow_remote_ai": True,
            },
        )
        assert response.status_code == 200
        assert client.get("/api/settings").json()["planning_provider"] == "codex"
        assert client.get("/api/models?provider=codex").json()["connected"] is True
        assert client.get("/api/models?provider=t3").status_code == 422


def test_missing_executable_has_actionable_status(monkeypatch):
    monkeypatch.setattr("vidac.cli_providers.shutil.which", lambda name: None)
    status = models_for(Settings(), "codex")
    assert not status["connected"]
    assert "VIDAC_CODEX" in status["detail"]


def test_cli_configure_uses_subscription_transport(workspace, monkeypatch, installed):
    monkeypatch.setenv("VIDAC_WORKSPACE", str(workspace.root))
    result = CliRunner().invoke(
        app, ["configure-ai", "planning", "codex", "chosen-model", "--allow-remote"]
    )
    assert result.exit_code == 0, result.output
    settings = workspace.settings()
    assert settings.planning_provider == "codex"
    assert settings.planning_model == "chosen-model"
    assert settings.allow_remote_ai


def test_cli_configures_optional_vision_review(workspace, monkeypatch, installed):
    monkeypatch.setenv("VIDAC_WORKSPACE", str(workspace.root))
    result = CliRunner().invoke(
        app, ["configure-ai", "vision-review", "codex", "review-model", "--allow-remote"]
    )
    assert result.exit_code == 0, result.output
    settings = workspace.settings()
    assert settings.vision_review_provider == "codex"
    assert settings.vision_review_model == "review-model"


@pytest.mark.parametrize("provider", ["codex", "claude", "opencode"])
def test_vision_routing_preserves_select_bounds(
    provider, workspace, seeded, monkeypatch, installed
):
    workspace.save_settings(
        Settings(vision_provider=provider, vision_model="provider/vision", allow_remote_ai=True)
    )
    seeded.execute("UPDATE assets SET proxy_path='/synthetic/proxy.mp4'")
    monkeypatch.setattr("vidac.providers.thumbnail", lambda *args: None)
    captured = {}

    def generate(self, model, prompt, schema, images):
        captured.update(provider=self.provider, model=model, images=images)
        return {
            "segments": [
                {
                    "title": "Desk detail",
                    "description": "Synthetic observation",
                    "start_seconds": 0,
                    "end_seconds": 8,
                }
            ]
        }

    monkeypatch.setattr(CLIProvider, "generate", generate)
    before = seeded.clip("clip_0")
    annotate_clip(workspace, seeded, "clip_0", noop)
    after = seeded.clip("clip_0")
    assert captured["provider"] == provider
    assert captured["model"] == "provider/vision"
    assert len(captured["images"]) == 8
    assert after["title"] == "Desk detail"
    assert after["provenance"] == f"{provider}:provider/vision"
    assert (after["start_frame"], after["end_frame"]) == (
        before["start_frame"],
        before["end_frame"],
    )


@pytest.mark.parametrize(
    "provider,event",
    [
        ("claude", {"type": "result", "subtype": "success", "result": None}),
        ("opencode", {"type": "step_finish", "part": None}),
        ("opencode", {"type": "text", "part": {"text": 10}}),
    ],
)
def test_malformed_events_have_a_validation_error(provider, event):
    with pytest.raises(ValueError):
        parse_events(provider, json.dumps(event))

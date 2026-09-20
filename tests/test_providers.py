import json

import httpx
import pytest
from vidac.config import Settings
from vidac.editing import build_plan
from vidac.models import CreateEdit, EditItem, EditPlan
from vidac.providers import Ollama


def test_ollama_structured_contract_and_local_images(tmp_path, monkeypatch):
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"frame-bytes")
    captured = {}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return httpx.Response(
            200,
            json={"response": json.dumps({"title": "Typing at desk"})},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("vidac.providers.httpx.post", post)
    result = Ollama(Settings()).generate(
        "vision-test", "Describe this", {"type": "object"}, [image]
    )
    assert result["title"] == "Typing at desk"
    assert captured["url"] == "http://127.0.0.1:11434/api/generate"
    assert captured["json"]["images"] == ["ZnJhbWUtYnl0ZXM="]
    assert captured["json"]["stream"] is False
    assert captured["json"]["format"] == {"type": "object"}


@pytest.mark.parametrize("payload", [{"response": "not-json"}, {"error": "missing-model"}])
def test_invalid_model_response_is_actionable(payload, monkeypatch):
    monkeypatch.setattr(
        "vidac.providers.httpx.post",
        lambda url, **_: httpx.Response(200, json=payload, request=httpx.Request("POST", url)),
    )
    with pytest.raises(RuntimeError, match="valid response"):
        Ollama(Settings()).generate("test", "prompt", {})


def test_no_silent_ai_fallback(workspace, seeded, monkeypatch):
    workspace.save_settings(Settings(planning_model="test"))

    def unavailable(*args, **kwargs):
        raise RuntimeError("Local model unavailable")

    monkeypatch.setattr(Ollama, "plan", unavailable)
    with pytest.raises(RuntimeError, match="unavailable"):
        build_plan(workspace, seeded, CreateEdit(use_ai=True))


@pytest.mark.parametrize(
    "items,match",
    [
        (
            [EditItem(clip_id="invented", source_in=0, source_out=10)],
            "outside your supplied catalogue",
        ),
        ([EditItem(clip_id="clip_0", source_in=0, source_out=201)], "outside the described select"),
        ([EditItem(clip_id="clip_0", source_in=0, source_out=10)] * 2, "repeated"),
    ],
)
def test_model_cannot_invent_media(workspace, seeded, monkeypatch, items, match):
    workspace.save_settings(Settings(planning_model="test"))
    monkeypatch.setattr(Ollama, "plan", lambda *args: EditPlan(name="AI", items=items))
    with pytest.raises(ValueError, match=match):
        build_plan(workspace, seeded, CreateEdit(use_ai=True))


def test_model_plan_validated_and_stamped(workspace, seeded, monkeypatch):
    workspace.save_settings(Settings(planning_model="test"))
    monkeypatch.setattr(
        Ollama,
        "plan",
        lambda *args: EditPlan(
            name="AI",
            fps="25/1",
            width=1280,
            height=720,
            items=[EditItem(clip_id="clip_0", source_in=25, source_out=75, look="warm")],
        ),
    )
    plan = build_plan(
        workspace, seeded, CreateEdit(name="My direction", clip_ids=["clip_0"], use_ai=True)
    )
    assert plan.name == "My direction"
    assert plan.width == 1920
    assert plan.items[0].timeline_frames == 50
    assert plan.items[0].asset_id == "asset_0"
    assert plan.planner == "ollama:test"

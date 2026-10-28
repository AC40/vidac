import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from vidac.config import Settings
from vidac.editing import build_plan, validate_plan
from vidac.models import CreateEdit, EditItem, EditPlan


def test_search_unicode_and_safe_fts(seeded):
    assert seeded.clips(query="desk")[0]["id"] == "clip_0"
    assert seeded.clips(query="brush teeth")[0]["id"] == "clip_1"
    assert seeded.clips(query='" OR 1=1 --') == []
    assert len(seeded.clips(query='"*')) == 2
    seeded.execute(
        "UPDATE clips SET tags=? WHERE id=?", (json.dumps(["Zähne", "routine"]), "clip_1")
    )
    assert seeded.clips(query="Zähne")[0]["id"] == "clip_1"
    seeded.execute("UPDATE clips SET favorite=1 WHERE id='clip_0'")
    assert [c["id"] for c in seeded.clips(favorite=True)] == ["clip_0"]


def test_deterministic_plan_and_rational_time(workspace, seeded):
    request = CreateEdit(brief="brushing teeth", duration=7, pace="balanced", look="warm")
    first = build_plan(workspace, seeded, request)
    second = build_plan(workspace, seeded, request)
    assert first == second
    assert first.items[0].clip_id == "clip_1"
    assert first.fps == "30000/1001"
    assert all(i.asset_id for i in first.items)
    assert all(i.look == "warm" for i in first.items)
    assert sum(i.timeline_frames for i in first.items) <= round(7 * 30000 / 1001)
    assert first.planner == "offline-assembler"


def test_selected_order_and_handles(workspace, seeded):
    plan = build_plan(
        workspace, seeded, CreateEdit(clip_ids=["clip_1", "clip_0", "clip_1"], duration=20)
    )
    assert [i.clip_id for i in plan.items] == ["clip_1", "clip_0"]
    assert "Duration differs" in " ".join(plan.notes)
    seeded.execute("UPDATE clips SET start_frame=25,end_frame=125 WHERE id='clip_0'")
    item = EditItem(clip_id="clip_0", source_in=0, source_out=150)
    assert (
        validate_plan(seeded, EditPlan(name="Handles", items=[item])).items[0].timeline_frames
        == 150
    )


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"source_out": 201}, "outside"),
        ({"asset_id": "asset_1"}, "different"),
        ({"clip_id": "invented"}, "Clip not found"),
    ],
)
def test_invalid_clip_references_rejected(seeded, changes, match):
    item = EditItem(**({"clip_id": "clip_0", "source_in": 0, "source_out": 25} | changes))
    with pytest.raises((ValueError, KeyError), match=match):
        validate_plan(seeded, EditPlan(name="Bad", items=[item]))


@pytest.mark.parametrize("updates", [{"fps": "23"}, {"width": 1921}, {"fps": "nan"}])
def test_invalid_timeline_rejected(seeded, updates):
    with pytest.raises(ValueError):
        validate_plan(seeded, EditPlan(name="Bad", **updates))


def test_strict_schema():
    with pytest.raises(ValidationError):
        EditItem(clip_id="x", source_in=1, source_out=1)
    with pytest.raises(ValidationError):
        EditPlan(name="Unsafe", command="rm -rf")
    with pytest.raises(ValidationError):
        Settings(ollama_url="https://external.example.com")
    with pytest.raises(ValidationError):
        Settings(ollama_url="http://user:secret@localhost")


def test_revision_history_is_immutable(workspace, seeded):
    plan = build_plan(workspace, seeded, CreateEdit()).model_dump()
    edit = seeded.save_edit(plan)
    plan["name"] = "Second pass"
    updated = seeded.save_edit(plan, edit["id"], expected_revision=1)
    assert updated["revision"] == 2
    assert seeded.edit(edit["id"], 1)["plan"]["name"] == "Untitled rough cut"
    assert len(updated["versions"]) == 2
    with pytest.raises(ValueError, match="another session"):
        seeded.save_edit(plan, edit["id"], expected_revision=1)
    assert seeded.edit(edit["id"])["revision"] == 2


def test_original_online_state(seeded):
    asset = seeded.asset("asset_0")
    assert asset["online"]
    Path(asset["path"]).rename(Path(asset["path"]).with_suffix(".moved"))
    assert not seeded.asset("asset_0")["online"]


def test_fts_migration_preserves_editorial_data(seeded):
    from vidac.db import Database

    seeded.execute("UPDATE clips SET tags=? WHERE id='clip_1'", (json.dumps(["Zähne"]),))
    seeded.execute("UPDATE clips_fts SET tags=? WHERE id='clip_1'", (json.dumps(["Zähne"]),))
    seeded.execute("PRAGMA user_version=1")
    migrated = Database(seeded.path)
    assert migrated.clips(query="Zähne")[0]["id"] == "clip_1"
    assert len(migrated.clips()) == 2
    assert migrated.one("PRAGMA user_version")["user_version"] == 3
    assert migrated.one("SELECT count(*) AS n FROM inference_usage")["n"] == 0

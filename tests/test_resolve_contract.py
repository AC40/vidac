from unittest.mock import MagicMock

from vidac.editing import build_plan
from vidac.media import LOOKS
from vidac.models import CreateEdit
from vidac.resolve_bridge import import_project


def test_resolve_adapter_requests_new_project_exclusive_out_and_cdl(workspace, seeded):
    """Contract test with a double, NOT a live Resolve compatibility certification."""
    plan = build_plan(workspace, seeded, CreateEdit(clip_ids=["clip_0"], duration=2, look="warm"))
    manifest = plan.model_dump() | {"assets": {"asset_0": seeded.asset("asset_0")}, "looks": LOOKS}
    resolve = MagicMock()
    manager = resolve.GetProjectManager.return_value
    project = manager.CreateProject.return_value
    pool = project.GetMediaPool.return_value
    timeline = pool.CreateEmptyTimeline.return_value
    video = MagicMock()
    video.GetDuration.return_value = plan.items[0].timeline_frames
    pool.ImportMedia.return_value = ["source-item"]
    pool.AppendToTimeline.return_value = [video]
    name, warnings = import_project(resolve, manifest)
    assert "Vidac" in name
    assert warnings == []
    manager.LoadProject.assert_not_called()
    first = pool.AppendToTimeline.call_args_list[0].args[0][0]
    assert first["endFrame"] == plan.items[0].source_out - 1
    assert first["startFrame"] == plan.items[0].source_in
    assert first["recordFrame"] == 0
    assert first["mediaType"] == 1
    assert pool.AppendToTimeline.call_args_list[1].args[0][0]["mediaType"] == 2
    video.SetCDL.assert_called_once()
    timeline.AddMarker.assert_called_once()
    manager.SaveProject.assert_called_once()

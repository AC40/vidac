import json
import xml.etree.ElementTree as ET
import zipfile
from fractions import Fraction
from pathlib import Path

import opentimelineio as otio
import pytest
from vidac.editing import build_plan
from vidac.exporting import export_edit, rational_seconds, to_otio
from vidac.media import noop
from vidac.models import CreateEdit


def test_rational_seconds():
    assert rational_seconds(1, "30000/1001") == "1001/30000s"
    assert rational_seconds(25, "25/1") == "1/1s"


@pytest.mark.parametrize("kind", ["otio", "fcpxml", "resolve"])
def test_portable_timeline_exports(workspace, seeded, kind):
    plan = build_plan(
        workspace, seeded, CreateEdit(duration=9, look="warm", clip_ids=["clip_0", "clip_1"])
    )
    plan.items[0].audio = False
    edit = seeded.save_edit(plan.model_dump())
    result = export_edit(workspace, seeded, edit["id"], 1, kind, lambda *_: None)
    path = Path(result["path"])
    assert path.is_file()
    assert seeded.edit(edit["id"])["artifacts"][0]["revision"] == 1
    if kind == "otio":
        timeline = otio.adapters.read_from_file(str(path))
        assert len(timeline.tracks[0]) == len(plan.items)
        first = timeline.tracks[0][0]
        assert first.source_range.start_time.value == plan.items[0].source_in
        assert first.metadata["vidac"]["look"] == "warm"
        assert isinstance(timeline.tracks[1][0], otio.schema.Gap)
        assert timeline.tracks[0][1].source_range.duration.rate == pytest.approx(30000 / 1001)
        assert "%20" in first.media_reference.target_url
    elif kind == "fcpxml":
        root = ET.parse(path).getroot()
        assert root.tag == "fcpxml"
        nodes = root.findall("./library/event/project/sequence/spine/asset-clip")
        assert len(nodes) == 2
        assert nodes[0].find("adjust-volume").get("amount") == "-96dB"
        second_offset = Fraction(nodes[1].get("offset").rstrip("s"))
        assert second_offset == Fraction(plan.items[0].timeline_frames) / Fraction(plan.fps)
        assert root.find("./resources/asset/media-rep").get("src").startswith("file://")
    else:
        with zipfile.ZipFile(path) as bundle:
            assert set(bundle.namelist()) == {
                "manifest.json",
                "timeline.otio",
                "import_to_resolve.py",
                "README.txt",
            }
            manifest = json.loads(bundle.read("manifest.json"))
            assert manifest["assets"]["asset_0"]["path"] == seeded.asset("asset_0")["path"]
            assert manifest["items"][0]["source_out"] == plan.items[0].source_out


def test_audio_and_video_tracks_align(workspace, seeded):
    plan = build_plan(workspace, seeded, CreateEdit(duration=10))
    timeline = to_otio(seeded, plan)
    assert timeline.tracks[0].duration() == timeline.tracks[1].duration()


def test_offline_source_blocks_handoff(workspace, seeded):
    plan = build_plan(workspace, seeded, CreateEdit(clip_ids=["clip_0"]))
    edit = seeded.save_edit(plan.model_dump())
    path = Path(seeded.asset("asset_0")["path"])
    path.rename(path.with_suffix(".offline"))
    with pytest.raises(ValueError, match="offline"):
        export_edit(workspace, seeded, edit["id"], 1, "resolve", lambda *_: None, noop)

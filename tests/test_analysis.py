import pytest
from vidac.config import Settings
from vidac.ingest import ingest
from vidac.media import noop
from vidac.models import SegmentAnalysis
from vidac.providers import Ollama


def fake_media(monkeypatch):
    """Exercise semantic segmentation without inference or video decoding."""
    info = {
        "duration": 8,
        "fps": "25/1",
        "frames": 200,
        "width": 1280,
        "height": 720,
        "has_audio": False,
        "metadata": {"possible_vfr": False},
    }
    monkeypatch.setattr("vidac.ingest.probe", lambda *args: info)
    monkeypatch.setattr("vidac.ingest.create_proxy", lambda *args: None)
    monkeypatch.setattr("vidac.ingest.thumbnail", lambda *args: None)
    monkeypatch.setattr("vidac.ingest.detect_shots", lambda *args: [(0, 200)])


def test_continuous_shot_is_split_by_observed_activity(workspace, db, tmp_path, monkeypatch):
    fake_media(monkeypatch)
    source = tmp_path / "continuous.mp4"
    source.write_bytes(b"synthetic-placeholder")
    workspace.save_settings(Settings(vision_model="vision-test"))
    monkeypatch.setattr(
        Ollama,
        "segments",
        lambda *args: SegmentAnalysis(
            segments=[
                {
                    "start_seconds": 0,
                    "end_seconds": 4,
                    "title": "Working",
                    "activity": "typing",
                    "tags": ["desk"],
                },
                {
                    "start_seconds": 4,
                    "end_seconds": 8,
                    "title": "Brushing",
                    "activity": "brushing teeth",
                    "tags": ["bathroom"],
                },
            ]
        ),
    )
    result = ingest(workspace, db, source, lambda *_: None, noop)
    assert result["shots"] == 1
    assert result["clips"] == 2
    assert not result["warnings"]
    clips = db.clips()
    assert [(c["start_frame"], c["end_frame"]) for c in clips] == [(0, 100), (100, 200)]
    assert all(c["shot_start"] == 0 and c["shot_end"] == 200 for c in clips)
    assert all(c["provenance"] == "ollama:vision-test" for c in clips)
    assert db.clips(query="brushing")[0]["annotation"]["activity"] == "brushing teeth"


@pytest.mark.parametrize("start,end", [(0, 99), (3, 8), (0, 5)])
def test_bad_semantic_ranges_fall_back_with_visible_warning(
    workspace, db, tmp_path, monkeypatch, start, end
):
    fake_media(monkeypatch)
    source = tmp_path / "bad-model.mp4"
    source.write_bytes(b"synthetic-placeholder")
    workspace.save_settings(Settings(vision_model="vision-test"))
    monkeypatch.setattr(
        Ollama,
        "segments",
        lambda *args: SegmentAnalysis(
            segments=[
                {"start_seconds": start, "end_seconds": end, "title": "Invalid range"},
            ]
        ),
    )
    result = ingest(workspace, db, source, lambda *_: None, noop)
    assert result["warnings"]
    clips = db.clips()
    assert len(clips) == 1
    assert clips[0]["provenance"] == "shot-detector"
    assert clips[0]["start_frame"] == 0 and clips[0]["end_frame"] == 200

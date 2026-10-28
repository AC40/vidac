import json

from vidac.classification import analyze_library
from vidac.config import Settings
from vidac.db import now
from vidac.media import noop
from vidac.models import SegmentAnalysis


class FakeVision:
    def __init__(self, role, calls):
        self.role = role
        self.calls = calls
        self.last_usage = {}

    def segments(self, proxy, cache, start, end, fps, check):
        self.calls.append((self.role, start, end))
        self.last_usage = {"input_tokens": 100, "output_tokens": 20, "cost_usd": 0.01}
        duration = (end - start) / 25
        if self.role == "vision" and start > 0:
            duration -= 2
        return SegmentAnalysis(
            segments=[
                {
                    "start_seconds": 0,
                    "end_seconds": duration,
                    "title": "Reviewed" if self.role == "vision_review" else "Cheap result",
                    "description": "Person walking beside a lake",
                    "tags": ["walking", "lake"],
                    "activity": "walking",
                    "setting": "outdoors",
                    "shot_size": "wide",
                    "camera_motion": "tracking",
                    "confidence": 0.95 if self.role == "vision_review" else 0.3,
                }
            ]
        )


def test_asset_windows_update_existing_clips_and_escalate(workspace, seeded, monkeypatch):
    proxy = workspace.cache("asset_0") / "proxy.mp4"
    proxy.write_bytes(b"synthetic proxy")
    seeded.execute(
        "UPDATE assets SET frames=1000,duration=40,proxy_path=? WHERE id='asset_0'",
        (str(proxy),),
    )
    seeded.execute(
        "UPDATE clips SET start_frame=0,end_frame=200,shot_end=1000,provenance='shot-detector',"
        "title='source 0 · shot 01',description='',tags='[]',annotation='{}' WHERE id='clip_0'"
    )
    for index in range(1, 5):
        seeded.execute(
            "INSERT INTO clips(id,asset_id,start_frame,end_frame,shot_start,shot_end,title,tags,provenance,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                f"clip_long_{index}",
                "asset_0",
                index * 200,
                (index + 1) * 200,
                0,
                1000,
                "source 0 · shot 01",
                json.dumps([]),
                "shot-detector",
                now(),
            ),
        )
    seeded.execute(
        "UPDATE clips SET title='Keep this title',description='My note',tags=? "
        "WHERE id='clip_long_4'",
        (json.dumps(["keeper"]),),
    )
    workspace.save_settings(
        Settings(
            vision_provider="opencode",
            vision_model="openrouter/qwen",
            vision_review_provider="codex",
            vision_review_model="review-model",
            allow_remote_ai=True,
        )
    )
    calls = []
    monkeypatch.setattr(
        "vidac.classification.intelligence", lambda settings, role, check: FakeVision(role, calls)
    )
    job = seeded.enqueue("analyze-library", {"asset_ids": ["asset_0"]})

    result = analyze_library(workspace, seeded, job["id"], ["asset_0"], lambda *_: None, noop)

    clips = seeded.clips(asset_id="asset_0", limit=100)
    assert result["windows"] == 2
    assert result["clips_updated"] == 5
    assert result["reviewed_windows"] == 2
    assert result["failed_windows"] == 0
    assert result["usage"] == {
        "requests": 4,
        "input_tokens": 400,
        "output_tokens": 80,
        "cost_usd": 0.04,
    }
    assert [clip["title"] for clip in clips] == [
        "Reviewed",
        "Reviewed",
        "Reviewed",
        "Reviewed",
        "Keep this title",
    ]
    assert clips[-1]["description"] == "My note\nPerson walking beside a lake"
    assert clips[-1]["tags"] == ["keeper", "walking", "lake"]
    assert all(clip["provenance"] == "codex:review-model" for clip in clips)
    assert [(clip["start_frame"], clip["end_frame"]) for clip in clips] == [
        (0, 200),
        (200, 400),
        (400, 600),
        (600, 800),
        (800, 1000),
    ]
    assert calls == [
        ("vision", 0, 750),
        ("vision_review", 0, 750),
        ("vision", 750, 1000),
        ("vision_review", 750, 1000),
    ]

    repeated = analyze_library(workspace, seeded, job["id"], ["asset_0"], lambda *_: None, noop)
    assert repeated["windows"] == 0
    assert repeated["clips_updated"] == 0

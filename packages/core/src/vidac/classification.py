from __future__ import annotations

import json
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

from vidac.config import Workspace
from vidac.db import Database
from vidac.media import Check
from vidac.models import SegmentAnalysis
from vidac.providers import Intelligence, intelligence

WINDOW_SECONDS = 30


def _validated_segments(analysis: SegmentAnalysis, start: int, end: int, fps: str) -> list[dict]:
    rate = Fraction(fps)
    duration = float((end - start) / rate)
    proposals = sorted(analysis.segments, key=lambda segment: segment.start_seconds)
    if abs(proposals[-1].end_seconds - duration) > 0.5:
        raise ValueError("Visual analysis did not cover the complete window.")
    cursor = start
    segments = []
    for index, segment in enumerate(proposals):
        local_start = start + round(segment.start_seconds * rate)
        local_end = start + round(segment.end_seconds * rate)
        if local_end <= local_start or local_start < start or local_end > end + 1:
            raise ValueError("Visual analysis returned an invalid time range.")
        if abs(local_start - cursor) > max(2, round(rate / 2)):
            raise ValueError("Visual analysis left a gap or overlapping activities.")
        next_end = end if index == len(proposals) - 1 else min(end, local_end)
        if next_end <= cursor:
            raise ValueError("Visual analysis produced an empty activity.")
        segments.append(
            {
                "start": cursor,
                "end": next_end,
                "annotation": segment.model_dump(exclude={"start_seconds", "end_seconds"}),
            }
        )
        cursor = next_end
    return segments


def _is_uncertain(analysis: SegmentAnalysis, threshold: float) -> bool:
    return any(segment.confidence < threshold for segment in analysis.segments)


def _annotation_for_clip(clip: dict, segments: list[dict]) -> dict | None:
    overlaps = [
        (
            min(clip["end_frame"], segment["end"]) - max(clip["start_frame"], segment["start"]),
            segment,
        )
        for segment in segments
        if segment["end"] > clip["start_frame"] and segment["start"] < clip["end_frame"]
    ]
    overlaps = [(size, segment) for size, segment in overlaps if size > 0]
    if not overlaps:
        return None
    primary = max(overlaps, key=lambda item: item[0])[1]["annotation"].copy()
    annotations = [segment["annotation"] for _, segment in overlaps]
    descriptions = list(
        dict.fromkeys(value for item in annotations if (value := item.get("description", "")))
    )
    tags = list(dict.fromkeys(tag for item in annotations for tag in item.get("tags", []) if tag))
    primary["description"] = " ".join(descriptions)
    primary["tags"] = tags
    primary["confidence"] = min(item.get("confidence", 0.5) for item in annotations)
    return primary


def _has_manual_metadata(clip: dict) -> bool:
    automatic_prefix = f"{Path(clip['asset_name']).stem} · shot "
    return bool(
        clip.get("description")
        or clip.get("tags")
        or not clip.get("title", "").startswith(automatic_prefix)
    )


def _record_usage(
    db: Database,
    job_id: str,
    engine: Intelligence,
    role: str,
    provider: str,
    model: str,
    asset_id: str,
    start: int,
    end: int,
) -> None:
    db.record_inference(
        job_id=job_id,
        task=role,
        provider=provider,
        model=model,
        asset_id=asset_id,
        start_frame=start,
        end_frame=end,
        usage=engine.last_usage,
    )


def analyze_library(
    workspace: Workspace,
    db: Database,
    job_id: str,
    asset_ids: list[str],
    progress,
    check: Check,
) -> dict:
    """Describe unclassified clips in 30-second source windows without changing their ranges."""
    settings = workspace.settings()
    primary = intelligence(settings, "vision", check)
    reviewer = (
        intelligence(settings, "vision_review", check) if settings.vision_review_model else None
    )
    if asset_ids:
        assets = [db.asset(asset_id) for asset_id in dict.fromkeys(asset_ids)]
    else:
        assets = [
            db.asset(row["id"]) for row in db.all("SELECT id FROM assets ORDER BY created_at")
        ]

    windows = []
    for asset in assets:
        clips = [
            clip
            for clip in db.clips(asset_id=asset["id"], limit=100000)
            if clip["provenance"] == "shot-detector"
        ]
        grouped: dict[tuple[int, int], list[dict]] = defaultdict(list)
        rate = Fraction(asset["fps"])
        step = max(1, round(WINDOW_SECONDS * rate))
        for clip in clips:
            midpoint = (clip["start_frame"] + clip["end_frame"]) // 2
            offset = max(0, midpoint - clip["shot_start"])
            start = clip["shot_start"] + (offset // step) * step
            end = min(clip["shot_end"], start + step)
            grouped[(start, end)].append(clip)
        windows.extend((asset, start, end, grouped[(start, end)]) for start, end in sorted(grouped))

    updated = 0
    reviewed = 0
    failed = 0
    warnings = []
    primary_provider, primary_model = settings.vision_provider, settings.vision_model
    review_provider, review_model = settings.vision_review_provider, settings.vision_review_model
    for index, (asset, start, end, clips) in enumerate(windows):
        check()
        progress(
            index / max(1, len(windows)),
            f"Analyzing {asset['name']} · window {index + 1} of {len(windows)}",
        )
        proxy = Path(asset["proxy_path"] or "")
        if not proxy.is_file():
            failed += 1
            warnings.append(f"{asset['name']}: review proxy is unavailable.")
            continue
        chosen = None
        provenance = f"{primary_provider}:{primary_model}"
        try:
            chosen = primary.segments(
                proxy, workspace.cache(asset["id"]), start, end, asset["fps"], check
            )
            _record_usage(
                db,
                job_id,
                primary,
                "vision",
                primary_provider,
                primary_model,
                asset["id"],
                start,
                end,
            )
            _validated_segments(chosen, start, end, asset["fps"])
        except (RuntimeError, ValueError) as exc:
            chosen = None
            if reviewer is None:
                failed += 1
                warnings.append(f"{asset['name']} at frame {start}: {exc}")
                continue
            warnings.append(
                f"{asset['name']} at frame {start}: primary analysis failed; used review model."
            )

        needs_review = chosen is None or _is_uncertain(chosen, settings.vision_review_threshold)
        if needs_review and reviewer is not None:
            try:
                reviewed_result = reviewer.segments(
                    proxy, workspace.cache(asset["id"]), start, end, asset["fps"], check
                )
                _record_usage(
                    db,
                    job_id,
                    reviewer,
                    "vision_review",
                    review_provider,
                    review_model,
                    asset["id"],
                    start,
                    end,
                )
                _validated_segments(reviewed_result, start, end, asset["fps"])
                chosen = reviewed_result
                provenance = f"{review_provider}:{review_model}"
                reviewed += 1
            except (RuntimeError, ValueError) as exc:
                if chosen is None:
                    failed += 1
                    warnings.append(f"{asset['name']} at frame {start}: review failed: {exc}")
                    continue
                warnings.append(
                    f"{asset['name']} at frame {start}: review failed; kept primary result: {exc}"
                )

        try:
            segments = _validated_segments(chosen, start, end, asset["fps"])
        except ValueError as exc:
            failed += 1
            warnings.append(f"{asset['name']} at frame {start}: {exc}")
            continue
        updates = []
        for clip in clips:
            annotation = _annotation_for_clip(clip, segments)
            if annotation is None:
                warnings.append(
                    f"{asset['name']} at frame {clip['start_frame']}: no matching segment."
                )
                continue
            manual = _has_manual_metadata(clip)
            description = annotation.get("description", "")
            if manual and clip["description"]:
                description = "\n".join(
                    dict.fromkeys(value for value in [clip["description"], description] if value)
                )
            tags = list(
                dict.fromkeys([*(clip["tags"] if manual else []), *annotation.get("tags", [])])
            )
            updates.append(
                (
                    clip["title"] if manual else annotation.get("title") or clip["title"],
                    description,
                    json.dumps(tags),
                    json.dumps(annotation),
                    provenance,
                    clip["id"],
                )
            )
        with db.connect() as conn:
            conn.executemany(
                "UPDATE clips SET title=?,description=?,tags=?,annotation=?,provenance=? "
                "WHERE id=? AND provenance='shot-detector'",
                updates,
            )
        updated += len(updates)

    totals = db.inference_totals(job_id)
    return {
        "assets": len(assets),
        "windows": len(windows),
        "clips_updated": updated,
        "reviewed_windows": reviewed,
        "failed_windows": failed,
        "usage": totals,
        "warnings": warnings[:100],
    }

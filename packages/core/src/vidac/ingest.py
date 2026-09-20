from __future__ import annotations

import json
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

from vidac.config import Workspace
from vidac.db import Database, now
from vidac.media import (
    Check,
    conform_vfr,
    create_proxy,
    detect_shots,
    digest,
    probe,
    thumbnail,
    waveform,
)
from vidac.providers import Ollama, transcribe

Progress = Callable[[float, str], None]


def ingest(
    workspace: Workspace, db: Database, path: Path, progress: Progress, check: Check
) -> dict:
    settings = workspace.settings()
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Source file is unavailable: {path}")
    before = path.stat()
    progress(0.03, "Fingerprinting original")
    sha = digest(path, check)
    existing = db.one("SELECT id,status,path FROM assets WHERE sha256=?", (sha,))
    if existing and existing["status"] == "ready":
        if not Path(existing["path"]).is_file():
            db.execute("UPDATE assets SET path=? WHERE id=?", (str(path), existing["id"]))
        asset = db.asset(existing["id"])
        cache = workspace.cache(asset["id"])
        edit_path = Path(asset["metadata"].get("edit_path", path))
        if not edit_path.is_file():
            progress(0.12, "Rebuilding missing editing intermediate")
            conform_vfr(path, edit_path, probe(path, check), check)
        if not Path(asset["proxy_path"]).is_file():
            progress(0.2, "Rebuilding missing review proxy")
            create_proxy(
                edit_path,
                Path(asset["proxy_path"]),
                probe(edit_path, check),
                settings.proxy_height,
                check,
            )
        if not Path(asset["thumb_path"]).is_file():
            thumbnail(Path(asset["proxy_path"]), Path(asset["thumb_path"]), 0, check)
        for clip in db.clips(asset_id=asset["id"], limit=100000):
            if not Path(clip["thumb_path"]).is_file():
                thumbnail(
                    Path(asset["proxy_path"]),
                    Path(clip["thumb_path"]),
                    (clip["start_frame"] + clip["end_frame"]) / 2 / Fraction(asset["fps"]),
                    check,
                )
        if asset["has_audio"] and not (cache / "waveform.png").is_file():
            waveform(Path(asset["proxy_path"]), cache / "waveform.png", check)
        return {
            "asset_id": existing["id"],
            "duplicate": True,
            "warnings": asset["metadata"].get("warnings", []),
        }
    asset_id = existing["id"] if existing else "asset_" + sha[:20]
    cache = workspace.cache(asset_id)
    progress(0.08, "Reading camera metadata")
    info = probe(path, check)
    original_metadata = info["metadata"]
    if info["metadata"]["possible_vfr"]:
        progress(0.12, "Conforming variable frame rate for accurate edits")
        info = conform_vfr(path, cache / "conformed.mov", info, check)
    edit_path = Path(info["metadata"].get("edit_path", path))
    if not existing:
        db.execute(
            "INSERT INTO assets(id,path,name,sha256,size,duration,fps,frames,width,height,has_audio,metadata,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                asset_id,
                str(path),
                path.name,
                sha,
                before.st_size,
                info["duration"],
                info["fps"],
                info["frames"],
                info["width"],
                info["height"],
                info["has_audio"],
                json.dumps(info["metadata"]),
                now(),
            ),
        )
    progress(0.18, "Creating review proxy")
    proxy = cache / "proxy.mp4"
    create_proxy(edit_path, proxy, info, settings.proxy_height, check)
    # The proxy is CFR. Limit the usable frame range to actual decoded output.
    proxy_info = probe(proxy, check)
    frames = min(info["frames"], proxy_info["frames"])
    fps = info["fps"]
    rate = Fraction(fps)
    thumbnail(proxy, cache / "poster.jpg", min(0.5, float(frames / rate) / 2), check)
    if info["has_audio"]:
        waveform(proxy, cache / "waveform.png", check)
    progress(0.38, "Detecting camera cuts")
    shots = detect_shots(
        proxy, fps, frames, settings.scene_threshold, settings.min_shot_seconds, check
    )
    transcript = []
    warnings = []
    if settings.transcribe and info["has_audio"]:
        progress(0.45, "Transcribing dialogue")
        try:
            transcript = transcribe(proxy, settings.transcription_model, check)
            (cache / "transcript.json").write_text(json.dumps(transcript, indent=2))
        except RuntimeError as exc:
            warnings.append(str(exc))
    clips = []
    for shot_index, (shot_start, shot_end) in enumerate(shots):
        check()
        step = max(1, round((30 if settings.vision_model else settings.select_seconds) * rate))
        for start in range(shot_start, shot_end, step):
            end = min(shot_end, start + step)
            if end <= start:
                continue
            segments = [
                {"start": start, "end": end, "annotation": {}, "provenance": "shot-detector"}
            ]
            if settings.vision_model:
                progress(
                    0.5 + 0.35 * start / frames,
                    f"Understanding shot {shot_index + 1} of {len(shots)}",
                )
                try:
                    analysis = Ollama(settings).segments(proxy, cache, start, end, fps, check)
                    proposals = sorted(analysis.segments, key=lambda x: x.start_seconds)
                    if abs(proposals[-1].end_seconds - float((end - start) / rate)) > 0.5:
                        raise ValueError("Visual analysis did not cover the complete window.")
                    # Reject invented/out-of-order ranges instead of silently making footage up.
                    cursor = start
                    proposed = []
                    for index, segment in enumerate(proposals):
                        local_start = start + round(segment.start_seconds * rate)
                        local_end = start + round(segment.end_seconds * rate)
                        if local_end <= local_start or local_start < start or local_end > end + 1:
                            raise ValueError("Visual analysis returned an invalid time range.")
                        if abs(local_start - cursor) > max(2, round(rate / 2)):
                            raise ValueError(
                                "Visual analysis left a gap or overlapping activities."
                            )
                        next_end = end if index == len(proposals) - 1 else min(end, local_end)
                        if next_end <= cursor:
                            raise ValueError("Visual analysis produced an empty activity.")
                        proposed.append(
                            {
                                "start": cursor,
                                "end": next_end,
                                "annotation": segment.model_dump(
                                    exclude={"start_seconds", "end_seconds"}
                                ),
                                "provenance": f"ollama:{settings.vision_model}",
                            }
                        )
                        cursor = next_end
                    segments = proposed
                except (RuntimeError, ValueError) as exc:
                    warnings.append(f"Shot {shot_index + 1}: {exc}")
            for segment in segments:
                # Long activities still need useful editorial selects with recoverable handles.
                select_size = max(1, round(settings.select_seconds * rate))
                for select_start in range(segment["start"], segment["end"], select_size):
                    select_end = min(segment["end"], select_start + select_size)
                    clip_id = f"clip_{sha[:16]}_{select_start}_{select_end}"
                    annotation = segment["annotation"]
                    spoken = " ".join(
                        s["text"].strip()
                        for s in transcript
                        if s["start"] < float(select_end / rate)
                        and s["end"] > float(select_start / rate)
                    )
                    title = annotation.get("title") or f"{path.stem} · shot {shot_index + 1:02d}"
                    description = annotation.get("description", "")
                    if spoken:
                        description = f"{description}\nDialogue: {spoken}".strip()
                    thumb = cache / f"{clip_id}.jpg"
                    thumbnail(proxy, thumb, float((select_start + select_end) / 2 / rate), check)
                    tags = annotation.get("tags", [])
                    clips.append(
                        (
                            clip_id,
                            asset_id,
                            select_start,
                            select_end,
                            shot_start,
                            shot_end,
                            title,
                            description,
                            json.dumps(tags),
                            json.dumps(annotation),
                            segment["provenance"],
                            str(thumb),
                            now(),
                        )
                    )
        progress(
            0.5 + 0.4 * (shot_index + 1) / len(shots), f"Preparing selects · {len(clips)} clips"
        )
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(
            "The source changed during import. Wait for the copy to finish and retry."
        )
    info["metadata"]["warnings"] = warnings[:30]
    info["metadata"]["original_color"] = {
        k: original_metadata.get(k) for k in ("color_primaries", "color_transfer", "color_space")
    }
    info["metadata"]["analysis"] = {
        "shot_detector": "ffmpeg-scene",
        "threshold": settings.scene_threshold,
        "vision_model": settings.vision_model or None,
        "shot_count": len(shots),
        "transcript_segments": len(transcript),
        "segmentation": "sampled-visual" if settings.vision_model else "shots-and-duration",
    }
    with db.connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO clips(id,asset_id,start_frame,end_frame,shot_start,shot_end,title,description,tags,annotation,provenance,thumb_path,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            clips,
        )
        conn.execute(
            "UPDATE assets SET status='ready',proxy_path=?,thumb_path=?,metadata=?,fps=?,frames=?,duration=? WHERE id=?",
            (
                str(proxy),
                str(cache / "poster.jpg"),
                json.dumps(info["metadata"]),
                fps,
                frames,
                float(frames / rate),
                asset_id,
            ),
        )
    progress(1, "Ready in library")
    return {
        "asset_id": asset_id,
        "clips": len(clips),
        "shots": len(shots),
        "warnings": warnings[:30],
    }


def annotate_clip(workspace: Workspace, db: Database, clip_id: str, check: Check) -> dict:
    clip = db.clip(clip_id)
    asset = db.asset(clip["asset_id"])
    settings = workspace.settings()
    result = Ollama(settings).segments(
        Path(asset["proxy_path"]),
        workspace.cache(asset["id"]),
        clip["start_frame"],
        clip["end_frame"],
        clip["fps"],
        check,
    )
    # Reanalysis annotates existing selects; preserve IDs/ranges used by saved edits.
    first = result.segments[0]
    description = " ".join(s.description for s in result.segments)
    tags = list(dict.fromkeys(t for s in result.segments for t in s.tags))
    annotation = first.model_dump(exclude={"start_seconds", "end_seconds"})
    db.execute(
        "UPDATE clips SET title=?,description=?,tags=?,annotation=?,provenance=? WHERE id=?",
        (
            first.title,
            description,
            json.dumps(tags),
            json.dumps(annotation),
            f"ollama:{settings.vision_model}",
            clip_id,
        ),
    )
    return {"clip_id": clip_id}


def generate_demo(workspace: Workspace, check: Check) -> Path:
    from vidac.media import ffmpeg

    target = workspace.root / "demo" / "color-study.mp4"
    if not target.exists():
        ffmpeg(
            [
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x59765f:s=960x540:r=25:d=4",
                "-f",
                "lavfi",
                "-i",
                "color=c=0xc2825c:s=960x540:r=25:d=4",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x536c8a:s=960x540:r=25:d=4",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:sample_rate=48000:duration=12",
                "-filter_complex",
                "[0:v]drawgrid=w=120:h=90:t=1:c=white@0.12[a];"
                "[1:v]drawgrid=w=120:h=90:t=1:c=white@0.12[b];"
                "[2:v]drawgrid=w=120:h=90:t=1:c=white@0.12[c];"
                "[a][b][c]concat=n=3:v=1:a=0[v]",
                "-map",
                "[v]",
                "-map",
                "3:a",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-af",
                "volume=0.08",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(target),
            ],
            check,
        )
    return target

from __future__ import annotations

import json
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from fractions import Fraction
from pathlib import Path

import opentimelineio as otio

from vidac.config import Workspace
from vidac.db import Database, now, uid
from vidac.editing import validate_plan
from vidac.media import LOOKS, Check, cube_lut, ffmpeg, noop
from vidac.models import EditPlan


def source_path(asset: dict) -> Path:
    return Path(asset["metadata"].get("edit_path", asset["path"]))


def assert_sources(db: Database, plan: EditPlan):
    for asset_id in {i.asset_id for i in plan.items}:
        path = source_path(db.asset(asset_id))
        if not path.is_file():
            raise ValueError(f"Source media is offline: {path}. Relink the asset before export.")


def to_otio(db: Database, plan: EditPlan) -> otio.schema.Timeline:
    timeline = otio.schema.Timeline(name=plan.name)
    rate = float(Fraction(plan.fps))
    timeline.global_start_time = otio.opentime.RationalTime(0, rate)
    timeline.metadata["vidac"] = {
        "brief": plan.brief,
        "fps": plan.fps,
        "width": plan.width,
        "height": plan.height,
        "notes": plan.notes,
    }
    video = otio.schema.Track(name="V1 · Picture", kind=otio.schema.TrackKind.Video)
    audio = otio.schema.Track(name="A1 · Source audio", kind=otio.schema.TrackKind.Audio)
    for index, item in enumerate(plan.items):
        asset = db.asset(item.asset_id)
        source_rate = float(Fraction(asset["fps"]))
        reference = otio.schema.ExternalReference(
            target_url=source_path(asset).as_uri(),
            available_range=otio.opentime.TimeRange(
                otio.opentime.RationalTime(0, source_rate),
                otio.opentime.RationalTime(asset["frames"], source_rate),
            ),
        )
        # Preserve source boundaries; mixed-rate NLEs quantize to the target grid.
        duration = item.source_out - item.source_in
        clip = otio.schema.Clip(
            name=item.title,
            media_reference=reference,
            source_range=otio.opentime.TimeRange(
                otio.opentime.RationalTime(item.source_in, source_rate),
                otio.opentime.RationalTime(duration, source_rate),
            ),
        )
        clip.metadata["vidac"] = {
            "clip_id": item.clip_id,
            "asset_id": item.asset_id,
            "look": item.look,
            "cdl": LOOKS[item.look],
            "reason": item.reason,
            "timeline_frames": item.timeline_frames,
            "source_timecode": asset["metadata"].get("timecode"),
            "fit": "letterbox",
        }
        clip.markers.append(
            otio.schema.Marker(
                name=f"{index + 1:02d} · {item.title}",
                marked_range=otio.opentime.TimeRange(
                    otio.opentime.RationalTime(item.source_in, source_rate),
                    otio.opentime.RationalTime(duration, source_rate),
                ),
                color=otio.schema.MarkerColor.GREEN,
                metadata={"vidac": {"note": item.reason, "look": item.look}},
            )
        )
        video.append(clip)
        if item.audio and asset["has_audio"]:
            audio.append(clip.deepcopy())
        else:
            audio.append(
                otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        otio.opentime.RationalTime(0, source_rate),
                        otio.opentime.RationalTime(duration, source_rate),
                    )
                )
            )
    timeline.tracks.extend([video, audio])
    return timeline


def rational_seconds(frames: int, fps: str) -> str:
    value = Fraction(frames) / Fraction(fps)
    return f"{value.numerator}/{value.denominator}s"


def write_fcpxml(db: Database, plan: EditPlan, path: Path):
    root = ET.Element("fcpxml", version="1.10")
    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources,
        "format",
        id="timeline_format",
        name="Vidac timeline",
        frameDuration=rational_seconds(1, plan.fps),
        width=str(plan.width),
        height=str(plan.height),
        colorSpace="1-1-1 (Rec. 709)",
    )
    unique = list(dict.fromkeys(i.asset_id for i in plan.items))
    for index, asset_id in enumerate(unique):
        asset = db.asset(asset_id)
        format_id = f"format{index}"
        ET.SubElement(
            resources,
            "format",
            id=format_id,
            frameDuration=rational_seconds(1, asset["fps"]),
            width=str(asset["width"]),
            height=str(asset["height"]),
        )
        attrs = {
            "id": f"asset{index}",
            "name": asset["name"],
            "start": "0s",
            "duration": rational_seconds(asset["frames"], asset["fps"]),
            "hasVideo": "1",
            "format": format_id,
        }
        if asset["has_audio"]:
            stream = next(
                (
                    s
                    for s in asset["metadata"].get("probe", {}).get("streams", [])
                    if s.get("codec_type") == "audio"
                ),
                {},
            )
            attrs.update(
                hasAudio="1",
                audioSources="1",
                audioChannels=str(stream.get("channels", 2)),
                audioRate=str(stream.get("sample_rate", 48000)),
            )
        node = ET.SubElement(resources, "asset", attrs)
        ET.SubElement(node, "media-rep", kind="original-media", src=source_path(asset).as_uri())
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="Vidac selects")
    project = ET.SubElement(event, "project", name=plan.name)
    sequence = ET.SubElement(
        project,
        "sequence",
        format="timeline_format",
        tcStart="0s",
        tcFormat="NDF",
        duration=rational_seconds(sum(i.timeline_frames for i in plan.items), plan.fps),
        audioLayout="stereo",
        audioRate="48k",
    )
    spine = ET.SubElement(sequence, "spine")
    offset = 0
    for item in plan.items:
        asset = db.asset(item.asset_id)
        node = ET.SubElement(
            spine,
            "asset-clip",
            name=item.title,
            ref=f"asset{unique.index(item.asset_id)}",
            offset=rational_seconds(offset, plan.fps),
            start=rational_seconds(item.source_in, asset["fps"]),
            duration=rational_seconds(item.timeline_frames, plan.fps),
            audioRole="dialogue",
        )
        ET.SubElement(node, "adjust-conform", type="fit")
        if not item.audio and asset["has_audio"]:
            ET.SubElement(node, "adjust-volume", amount="-96dB")
        ET.SubElement(
            node,
            "marker",
            start=rational_seconds(item.source_in, asset["fps"]),
            duration=rational_seconds(1, asset["fps"]),
            value=item.title,
            note=f"{item.reason} | Suggested look: {item.look}",
        )
        offset += item.timeline_frames
    ET.indent(root)
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
        + ET.tostring(root, encoding="unicode")
    )


def render_preview(
    workspace: Workspace, db: Database, plan: EditPlan, target: Path, progress, check: Check
):
    if not plan.items:
        raise ValueError("Cannot render an empty timeline.")
    fps = Fraction(plan.fps)
    scale = min(1, 960 / max(plan.width, plan.height))
    width, height = round(plan.width * scale / 2) * 2, round(plan.height * scale / 2) * 2
    with tempfile.TemporaryDirectory(prefix="render-", dir=target.parent) as temporary:
        temp = Path(temporary)
        for look in {i.look for i in plan.items}:
            cube_lut(look, temp / f"{look}.cube")
        segments = []
        for index, item in enumerate(plan.items):
            check()
            asset = db.asset(item.asset_id)
            source = Path(asset["proxy_path"])
            if not source.is_file():
                raise ValueError("Review proxy is missing. Reimport its source to regenerate it.")
            duration = float(Fraction(item.timeline_frames) / fps)
            source_rate = Fraction(asset["fps"])
            output = temp / f"segment-{index:04d}.mov"
            # Use frame-based decoding against the CFR proxy, rather than keyframe seeking.
            # Relative generated LUT names avoid FFmpeg filter escaping of user paths.
            lut = f"{item.look}.cube"
            vf = (
                f"trim=start_frame={item.source_in}:end_frame={item.source_out},setpts=PTS-STARTPTS,"
                f"fps={plan.fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                f"lut3d=file='{lut}',tpad=stop_mode=clone:stop_duration=1"
            )
            args = ["-v", "error", "-i", str(source)]
            has_audio = bool(item.audio and asset["has_audio"])
            if not has_audio:
                args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
            args += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0", "-vf", vf]
            if has_audio:
                args += [
                    "-af",
                    f"atrim=start={float(item.source_in / source_rate):.9f}:end={float(item.source_out / source_rate):.9f},asetpts=PTS-STARTPTS,apad",
                ]
            args += [
                "-t",
                f"{duration:.9f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-video_track_timescale",
                str(fps.numerator),
                str(output),
            ]
            ffmpeg(args, check, cwd=temp)
            segments.append(output)
            progress(
                0.85 * (index + 1) / len(plan.items),
                f"Rendering shot {index + 1} of {len(plan.items)}",
            )
        # Generated filenames only; no user paths interpolated into concat syntax.
        concat = temp / "segments.txt"
        concat.write_text(
            "\n".join(
                f"file '{p.name}'\nduration {float(Fraction(item.timeline_frames) / fps):.12f}"
                for p, item in zip(segments, plan.items, strict=True)
            )
            + "\n"
        )
        partial = target.with_name("preview.partial.mp4")
        ffmpeg(
            [
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(concat),
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-t",
                f"{float(Fraction(sum(i.timeline_frames for i in plan.items)) / fps):.12f}",
                "-movflags",
                "+faststart",
                str(partial),
            ],
            check,
        )
        partial.replace(target)


def register_artifact(db: Database, edit_id: str, revision: int, kind: str, path: Path) -> dict:
    artifact_id = uid("file")
    db.execute(
        "INSERT INTO artifacts VALUES(?,?,?,?,?,?)",
        (artifact_id, edit_id, revision, kind, str(path), now()),
    )
    return {
        "artifact_id": artifact_id,
        "edit_id": edit_id,
        "revision": revision,
        "kind": kind,
        "path": str(path),
        "url": f"/api/artifacts/{artifact_id}",
    }


def export_edit(
    workspace: Workspace,
    db: Database,
    edit_id: str,
    revision: int,
    kind: str,
    progress,
    check: Check = noop,
) -> dict:
    edit = db.edit(edit_id, revision)
    plan = validate_plan(db, EditPlan.model_validate(edit["plan"]))
    if not plan.items:
        raise ValueError("Add at least one shot before exporting.")
    folder = workspace.export_dir(edit_id, revision)
    progress(0.05, "Validating timeline")
    if kind != "preview":
        assert_sources(db, plan)
    if kind == "otio":
        path = folder / "timeline.otio"
        otio.adapters.write_to_file(to_otio(db, plan), str(path))
    elif kind == "fcpxml":
        path = folder / "timeline.fcpxml"
        write_fcpxml(db, plan, path)
    elif kind == "preview":
        path = folder / "preview.mp4"
        render_preview(workspace, db, plan, path, progress, check)
    elif kind == "resolve":
        path = folder / "resolve-handoff.zip"
        manifest = plan.model_dump()
        manifest["assets"] = {
            asset_id: db.asset(asset_id) for asset_id in {i.asset_id for i in plan.items}
        }
        manifest["looks"] = LOOKS
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
        otio.adapters.write_to_file(to_otio(db, plan), str(folder / "timeline.otio"))
        script = Path(__file__).with_name("resolve_bridge.py")
        shutil.copyfile(script, folder / "import_to_resolve.py")
        (folder / "README.txt").write_text(
            "VIDAC RESOLVE HANDOFF\n\nExtract this folder, keeping these files together.\n"
            "Camera originals are referenced by absolute path and are NOT inside this archive.\n"
            "In Resolve Studio, set Preferences > System > General > External scripting using: Local.\n"
            "Configure the Resolve Python scripting module, then run:\n"
            "  python import_to_resolve.py manifest.json\n\n"
            "Alternatively import timeline.otio through File > Import Timeline.\n"
            "OTIO preserves editorial metadata; importing it alone does not apply CDL grades.\n"
            "The script creates a NEW project and applies simple CDL grades when supported.\n"
            "It never modifies an existing project. Read all printed warnings.\n"
            "Looks assume Rec.709 input. HDR/log/unknown color needs human color management.\n"
        )
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name in ("manifest.json", "timeline.otio", "import_to_resolve.py", "README.txt"):
                bundle.write(folder / name, name)
    else:
        raise ValueError("Supported exports: otio, fcpxml, resolve, preview.")
    check()
    progress(1, "Export ready")
    return register_artifact(db, edit_id, revision, kind, path)

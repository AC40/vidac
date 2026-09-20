"""Standalone Resolve Studio importer; included verbatim in every handoff archive.

Run with Resolve's Python scripting module on PYTHONPATH. Always creates a new
project. Resolve must be running; external scripting must be set to Local.
"""

import json
import sys
from datetime import datetime
from fractions import Fraction
from pathlib import Path


def import_project(resolve, manifest):
    manager = resolve.GetProjectManager()
    name = manifest["name"] + " · Vidac " + datetime.now().strftime("%Y%m%d-%H%M%S")
    project = manager.CreateProject(name)
    if not project:
        raise RuntimeError(
            "Could not create a new Resolve project. No existing project was modified."
        )
    warnings = []
    # Resolve accepts decimal strings (23.976, 29.97, etc.) for timeline rates.
    fps = Fraction(manifest["fps"])
    rate_name = {"24000/1001": "23.976", "30000/1001": "29.97", "60000/1001": "59.94"}.get(
        manifest["fps"], str(int(fps))
    )
    for key, value in {
        "timelineFrameRate": rate_name,
        "timelineResolutionWidth": str(manifest["width"]),
        "timelineResolutionHeight": str(manifest["height"]),
    }.items():
        if not project.SetSetting(key, value):
            raise RuntimeError("Resolve rejected project setting: " + key)
    pool = project.GetMediaPool()
    media = {}
    for asset_id, asset in manifest["assets"].items():
        path = asset["metadata"].get("edit_path", asset["path"])
        if not Path(path).is_file():
            raise RuntimeError("Source media is offline: " + path)
        imported = pool.ImportMedia([path])
        if not imported:
            raise RuntimeError("Resolve could not import " + path)
        media[asset_id] = imported[0]
    timeline = pool.CreateEmptyTimeline(manifest["name"])
    if not timeline:
        raise RuntimeError("Resolve could not create the timeline.")
    if not timeline.SetStartTimecode("00:00:00:00"):
        raise RuntimeError("Resolve could not set the timeline start to frame zero.")
    position = 0
    for item in manifest["items"]:
        asset = manifest["assets"][item["asset_id"]]
        common = {
            "mediaPoolItem": media[item["asset_id"]],
            "startFrame": item["source_in"],
            "endFrame": item["source_out"] - 1,
            "recordFrame": position,
            "trackIndex": 1,
        }
        inserted = pool.AppendToTimeline([{**common, "mediaType": 1}])
        if not inserted:
            raise RuntimeError("Resolve could not append video: " + item["title"])
        if item["audio"] and asset["has_audio"]:
            if not pool.AppendToTimeline([{**common, "mediaType": 2}]):
                warnings.append("Audio import failed for " + item["title"])
        video = inserted[0]
        if int(round(video.GetDuration())) != item["timeline_frames"]:
            warnings.append(
                "Frame-rate conform differs for " + item["title"] + "; verify this edit point."
            )
        timeline.AddMarker(
            position,
            "Green",
            item["title"],
            item.get("reason", ""),
            item["timeline_frames"],
            item["clip_id"],
        )
        if item["look"] != "neutral":
            transfer = asset["metadata"].get("color_transfer", "unknown")
            if transfer != "bt709":
                warnings.append(
                    "Skipped look on " + item["title"] + ": confirm its input color space first."
                )
            else:
                cdl = manifest["looks"][item["look"]]
                values = {
                    "NodeIndex": "1",
                    "Slope": " ".join(map(str, cdl["slope"])),
                    "Offset": " ".join(map(str, cdl["offset"])),
                    "Power": " ".join(map(str, cdl["power"])),
                    "Saturation": str(cdl["saturation"]),
                }
                if not video.SetCDL(values):
                    warnings.append("CDL grade was not applied to " + item["title"])
        position += item["timeline_frames"]
    if not manager.SaveProject():
        raise RuntimeError("Resolve could not save the project.")
    return name, warnings


def main():
    try:
        import DaVinciResolveScript
    except ImportError as exc:
        raise SystemExit(
            "Add Resolve's Developer/Scripting/Modules directory to PYTHONPATH."
        ) from exc
    manifest_path = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("manifest.json")
    )
    manifest = json.loads(manifest_path.read_text())
    resolve = DaVinciResolveScript.scriptapp("Resolve")
    if not resolve:
        raise SystemExit("Open Resolve Studio and enable local external scripting first.")
    name, warnings = import_project(resolve, manifest)
    print("Created project:", name)
    for warning in warnings:
        print("WARNING:", warning)


if __name__ == "__main__":
    main()

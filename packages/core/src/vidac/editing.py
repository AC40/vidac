from __future__ import annotations

import re
from fractions import Fraction

from vidac.config import Workspace
from vidac.db import Database
from vidac.models import CreateEdit, EditItem, EditPlan
from vidac.providers import Ollama

SUPPORTED_FPS = {"24/1", "25/1", "30/1", "50/1", "60/1", "24000/1001", "30000/1001", "60000/1001"}


def validate_plan(db: Database, plan: EditPlan) -> EditPlan:
    if plan.fps not in SUPPORTED_FPS:
        raise ValueError("Choose a supported rational timeline frame rate.")
    if plan.width % 2 or plan.height % 2:
        raise ValueError("Timeline dimensions must be even.")
    timeline_rate = Fraction(plan.fps)
    items = []
    for item in plan.items:
        clip = db.clip(item.clip_id)
        asset = db.asset(clip["asset_id"])
        if asset["status"] != "ready":
            raise ValueError("Wait for source processing to finish before editing.")
        if item.asset_id and item.asset_id != clip["asset_id"]:
            raise ValueError("A clip cannot reference a different source asset.")
        # Manual extensions may use the full original shot, but never cross its cut.
        if item.source_in < clip["shot_start"] or item.source_out > clip["shot_end"]:
            raise ValueError(f"{clip['title']}: trim extends outside the original shot.")
        count = round(
            Fraction(item.source_out - item.source_in) / Fraction(asset["fps"]) * timeline_rate
        )
        if count < 1:
            raise ValueError("Clip is shorter than one timeline frame.")
        items.append(
            item.model_copy(
                update={"asset_id": asset["id"], "title": clip["title"], "timeline_frames": count}
            )
        )
    if sum(item.timeline_frames for item in items) > timeline_rate * 3600:
        raise ValueError("A rough cut cannot exceed one hour.")
    return plan.model_copy(update={"items": items})


def build_plan(workspace: Workspace, db: Database, request: CreateEdit) -> EditPlan:
    if request.clip_ids:
        candidates = [db.clip(clip_id) for clip_id in dict.fromkeys(request.clip_ids)]
    else:
        candidates = [c for c in db.clips() if c["asset_status"] == "ready"]
        words = set(re.findall(r"\w+", request.brief.casefold())) - {
            "the",
            "a",
            "of",
            "and",
            "to",
            "in",
            "with",
            "make",
            "video",
            "cut",
            "edit",
            "seconds",
        }

        def relevance(clip):
            text = f"{clip['title']} {clip['description']} {' '.join(clip['tags'])}".casefold()
            return sum(word in text for word in words) + (0.5 if clip["favorite"] else 0)

        candidates.sort(key=lambda c: (-relevance(c), c["asset_name"], c["start_frame"]))
        candidates = candidates[:80]
    if not candidates:
        raise ValueError("Import some footage before creating an edit.")
    width, height = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}[
        request.aspect
    ]
    first_fps = candidates[0]["fps"]
    fps = first_fps if first_fps in SUPPORTED_FPS else "25/1"
    rate = Fraction(fps)
    desired = round(request.duration * rate)
    pacing = {"calm": 6, "balanced": 4, "energetic": 2}[request.pace]
    remaining = desired
    items = []
    # The offline assembler is intentionally deterministic. The UI labels it as such.
    for clip in candidates:
        source_rate = Fraction(clip["fps"])
        available = clip["end_frame"] - clip["start_frame"]
        source_count = min(available, round(min(pacing, float(remaining / rate)) * source_rate))
        if source_count < 1:
            continue
        count = round(source_count / source_rate * rate)
        if count > remaining:
            source_count -= 1
            count = round(source_count / source_rate * rate)
        if source_count < 1 or count < 1:
            continue
        # Trim both ends symmetrically to avoid setup/teardown when room exists.
        start = clip["start_frame"] + min(round(source_rate / 2), (available - source_count) // 2)
        items.append(
            EditItem(
                clip_id=clip["id"],
                source_in=start,
                source_out=start + source_count,
                look=request.look,
                reason="Selected from your library using tags, favorites, and pacing.",
            )
        )
        remaining -= count
        if remaining <= 0:
            break
    plan = EditPlan(
        name=request.name,
        brief=request.brief,
        fps=fps,
        width=width,
        height=height,
        items=items,
        planner="offline-assembler",
    )
    if request.use_ai:
        plan = Ollama(workspace.settings()).plan(request.model_dump(), candidates, plan)
        allowed = {c["id"] for c in candidates}
        if any(item.clip_id not in allowed for item in plan.items):
            raise ValueError("The model selected a clip outside your supplied catalogue.")
        if len({item.clip_id for item in plan.items}) != len(plan.items):
            raise ValueError("The model repeated a clip; request a new plan.")
        bounds = {c["id"]: (c["start_frame"], c["end_frame"]) for c in candidates}
        if any(
            item.source_in < bounds[item.clip_id][0] or item.source_out > bounds[item.clip_id][1]
            for item in plan.items
        ):
            raise ValueError("The model extended a trim outside the described select.")
        plan.planner = f"ollama:{workspace.settings().planning_model}"
        plan.name, plan.brief = request.name, request.brief
        plan.width, plan.height, plan.fps = width, height, fps
    plan = validate_plan(db, plan)
    if not plan.items:
        raise ValueError("No usable clips were selected for this edit.")
    actual = sum(item.timeline_frames for item in plan.items) / float(rate)
    plan.notes.append(
        f"Requested {request.duration:g}s; assembled {actual:.2f}s from {len(plan.items)} clips."
    )
    if abs(actual - request.duration) > 1:
        plan.notes.append(
            "Duration differs from the brief. Adjust pacing, add clips, or refine the selection."
        )
    if not request.use_ai:
        plan.notes.append(
            "Offline assembly uses keyword matching and pacing. Configure a planning model for creative interpretation."
        )
    plan.notes.append(
        "Looks are simple Rec.709 CDL starting points. Verify source color spaces in your editor."
    )
    return plan

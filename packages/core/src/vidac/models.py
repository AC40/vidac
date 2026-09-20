from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Look = Literal["neutral", "warm", "cool", "monochrome"]


class ClipPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = Field(default=None, max_length=40)
    favorite: bool | None = None
    start_frame: int | None = Field(default=None, ge=0)
    end_frame: int | None = Field(default=None, gt=0)


class EditItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clip_id: str
    asset_id: str = ""
    source_in: int = Field(ge=0)
    source_out: int = Field(gt=0)
    timeline_frames: int = Field(default=0, ge=0)
    title: str = ""
    reason: str = Field(default="", max_length=1000)
    look: Look = "neutral"
    audio: bool = True

    @model_validator(mode="after")
    def range_order(self):
        if self.source_out <= self.source_in:
            raise ValueError("Out point must be after in point.")
        return self


class EditPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    brief: str = Field(default="", max_length=8000)
    fps: str = "25/1"
    width: int = Field(default=1920, ge=320, le=4096)
    height: int = Field(default=1080, ge=240, le=4096)
    items: list[EditItem] = Field(default_factory=list, max_length=200)
    notes: list[str] = Field(default_factory=list, max_length=50)
    planner: str = "manual"


class CreateEdit(BaseModel):
    name: str = Field(default="Untitled rough cut", min_length=1, max_length=200)
    brief: str = Field(default="", max_length=8000)
    duration: float = Field(default=30, ge=1, le=1800)
    pace: Literal["calm", "balanced", "energetic"] = "balanced"
    look: Look = "neutral"
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    clip_ids: list[str] = Field(default_factory=list, max_length=200)
    use_ai: bool = False


class Annotation(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=3000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    activity: str = Field(default="", max_length=100)
    setting: str = Field(default="", max_length=100)
    shot_size: str = Field(default="unknown", max_length=50)
    camera_motion: str = Field(default="unknown", max_length=100)
    quality_notes: str = Field(default="", max_length=1000)


class SemanticSegment(Annotation):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)


class SegmentAnalysis(BaseModel):
    segments: list[SemanticSegment] = Field(min_length=1, max_length=12)


class SaveEdit(BaseModel):
    revision: int = Field(ge=1)
    plan: EditPlan

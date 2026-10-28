from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ProviderName = Literal["ollama", "codex", "claude", "opencode"]


class Settings(BaseModel):
    scene_threshold: float = Field(default=0.3, ge=0.05, le=0.9)
    min_shot_seconds: float = Field(default=1.0, ge=0.25, le=10)
    select_seconds: float = Field(default=8.0, ge=2, le=60)
    proxy_height: int = Field(default=720, ge=240, le=1080)
    ollama_url: str = "http://127.0.0.1:11434"
    vision_provider: ProviderName = "ollama"
    vision_review_provider: ProviderName = "codex"
    planning_provider: ProviderName = "ollama"
    allow_remote_ai: bool = False
    model_timeout_seconds: int = Field(default=300, ge=10, le=1800)
    vision_model: str = ""
    vision_review_model: str = ""
    vision_review_threshold: float = Field(default=0.7, ge=0, le=1)
    planning_model: str = ""
    transcribe: bool = False
    transcription_model: str = "base"
    watch_folders: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("vision_model", "vision_review_model", "planning_model")
    @classmethod
    def model_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) > 200 or any(ord(c) < 32 for c in value) or value.startswith("-"):
            raise ValueError("Use a model ID, not command-line options.")
        return value

    @field_validator("ollama_url")
    @classmethod
    def local_model_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Use a local Ollama endpoint (http://localhost or 127.0.0.1).")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Ollama URL must not include credentials, query, or fragment.")
        return value.rstrip("/")


class Workspace:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or os.environ.get("VIDAC_WORKSPACE", ".vidac")).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("cache", "exports", "uploads", "demo"):
            (self.root / name).mkdir(exist_ok=True)
        self.db_path = self.root / "library.sqlite3"
        self.settings_path = self.root / "settings.json"

    def settings(self) -> Settings:
        if self.settings_path.exists():
            return Settings.model_validate_json(self.settings_path.read_text())
        return Settings()

    def save_settings(self, settings: Settings) -> None:
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(settings.model_dump_json(indent=2))
        temporary.replace(self.settings_path)

    def cache(self, asset_id: str) -> Path:
        path = self.root / "cache" / asset_id
        path.mkdir(exist_ok=True)
        return path

    def export_dir(self, edit_id: str, revision: int) -> Path:
        path = self.root / "exports" / edit_id / f"v{revision}"
        path.mkdir(parents=True, exist_ok=True)
        return path

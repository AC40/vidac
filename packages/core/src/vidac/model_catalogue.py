"""Best-effort model suggestions, never a claim that an account can run a model."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import httpx

from vidac.cli_providers import CLI_NAMES, cli_executable, run_cli
from vidac.config import ProviderName, Settings


def models_for(settings: Settings, provider: ProviderName) -> dict:
    result = {"provider": provider, "connected": False, "models": [], "detail": ""}
    try:
        if provider == "ollama":
            response = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3)
            response.raise_for_status()
            result.update(
                connected=True,
                models=[m["name"] for m in response.json()["models"]],
                detail="Installed Ollama models. Vision needs an image-capable model.",
            )
        else:
            executable = cli_executable(provider)
            result.update(
                connected=True,
                detail=f"{CLI_NAMES[provider]} found. Login and model access are checked when a job runs.",
            )
            if provider == "claude":
                result["models"] = ["sonnet", "opus", "haiku"]
                result["detail"] += " Suggestions are CLI aliases; you can enter an exact model ID."
            elif provider == "codex":
                cache = (
                    Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "models_cache.json"
                )
                if cache.is_file() and cache.stat().st_size < 8 * 1024 * 1024:
                    data = json.loads(cache.read_text())
                    result["models"] = [
                        m["slug"]
                        for m in data.get("models", [])
                        if isinstance(m, dict)
                        and isinstance(m.get("slug"), str)
                        and m.get("visibility", "list") == "list"
                    ]
                result["detail"] += (
                    " Suggestions come from the local Codex cache. Enter any model ID supported by your account."
                )
            else:
                with tempfile.TemporaryDirectory(prefix="vidac-models-") as temporary:
                    raw = run_cli([executable, "models"], cwd=Path(temporary), timeout=15)
                result["models"] = sorted(
                    {
                        line.strip()
                        for line in raw.splitlines()
                        if "/" in line and not any(c.isspace() for c in line)
                    }
                )
                result["detail"] += (
                    " Use provider/model IDs. Listed models may still require provider login."
                )
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        result["detail"] = (
            str(exc)
            if isinstance(exc, RuntimeError)
            else "Could not load model suggestions. Check the provider and refresh, or enter a model ID."
        )
    return result

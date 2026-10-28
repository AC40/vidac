"""Non-interactive CLI transports. Authentication stays with each installed CLI."""

from __future__ import annotations

import copy
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from vidac.media import Check, noop

MAX_OUTPUT = 8 * 1024 * 1024
CLI_NAMES = {"codex": "Codex CLI", "claude": "Claude Code", "opencode": "OpenCode"}


def cli_executable(provider: str) -> str:
    executable = shutil.which(os.environ.get(f"VIDAC_{provider.upper()}", provider))
    if not executable:
        raise RuntimeError(
            f"{CLI_NAMES[provider]} is not installed or is not on the Vidac server's PATH. "
            f"Install and sign in to {provider}, then restart Vidac. "
            f"VIDAC_{provider.upper()} can specify its executable path."
        )
    return executable


def run_cli(
    args: list[str],
    *,
    cwd: Path,
    stdin: str = "",
    env: dict[str, str] | None = None,
    timeout: float = 300,
    check: Check = noop,
) -> str:
    """Bound output and runtime, and terminate descendants on cancellation."""
    check()
    with (
        tempfile.TemporaryFile() as source,
        tempfile.TemporaryFile() as output,
        tempfile.TemporaryFile() as errors,
    ):
        source.write(stdin.encode())
        source.seek(0)
        try:
            proc = subprocess.Popen(
                args,
                stdin=source,
                stdout=output,
                stderr=errors,
                cwd=cwd,
                env=env,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise RuntimeError(f"Could not start {Path(args[0]).name}: {exc}") from exc
        started = time.monotonic()
        try:
            while proc.poll() is None:
                check()
                if time.monotonic() - started > timeout:
                    raise RuntimeError(
                        f"{Path(args[0]).name} exceeded the {timeout:g}s model timeout."
                    )
                if (
                    max(os.fstat(output.fileno()).st_size, os.fstat(errors.fileno()).st_size)
                    > MAX_OUTPUT
                ):
                    raise RuntimeError("Model CLI output exceeded the 8 MiB limit.")
                time.sleep(0.1)
            check()
        except BaseException:
            if os.name != "nt":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            proc.wait()
            raise
        output.seek(0)
        errors.seek(0)
        stdout = output.read(MAX_OUTPUT + 1)
        stderr = errors.read(MAX_OUTPUT + 1)
        if max(len(stdout), len(stderr)) > MAX_OUTPUT:
            raise RuntimeError("Model CLI output exceeded the 8 MiB limit.")
        if proc.returncode:
            # Avoid persisting raw CLI logs, which can echo prompts and credentials.
            detail = (stdout + stderr).decode(errors="replace").lower()
            if any(word in detail for word in ("login", "log in", "auth", "unauthorized")):
                hint = "Sign in using the CLI on the machine running Vidac."
            elif any(word in detail for word in ("rate limit", "quota", "usage limit", "429")):
                hint = (
                    "The provider's usage limit was reached; retry later or choose another model."
                )
            elif "model" in detail:
                hint = "Check that the selected model is available to your CLI account."
            else:
                hint = "Check CLI login, model access, and version in your terminal."
            raise RuntimeError(f"{Path(args[0]).name} exited with code {proc.returncode}. {hint}")
        return stdout.decode("utf-8", errors="replace")


def strict_schema(schema: dict) -> dict:
    """Codex structured outputs require all properties and closed objects."""
    result = copy.deepcopy(schema)

    def visit(value):
        if isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    return result


def json_object(raw: str) -> dict:
    if not isinstance(raw, str):
        raise ValueError("Expected a JSON string.")
    text = raw.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    elif text.startswith("```\n") and text.endswith("```"):
        text = text[4:-3].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


def parse_events(provider: str, raw: str) -> dict:
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not all(isinstance(event, dict) for event in events):
        raise ValueError("Invalid CLI event stream.")
    if provider == "claude":
        results = [e for e in events if e.get("type") == "result"]
        if not results:
            raise ValueError("Claude Code returned no completed result.")
        result = results[-1]
        if result.get("is_error") or result.get("subtype") != "success":
            raise RuntimeError(
                "Claude Code could not complete the request. Check login, model access and usage limits."
            )
        value = result.get("structured_output")
        if isinstance(value, dict):
            return value
        return json_object(result.get("result", ""))
    if any(e.get("type") == "error" or e.get("error") for e in events):
        raise RuntimeError(
            "OpenCode reported a provider error. Check login, model access and usage limits."
        )
    for event in events:
        if event.get("type") in {"text", "step_finish"} and not isinstance(event.get("part"), dict):
            raise ValueError("Invalid OpenCode response part.")
        if event.get("type") == "text" and not isinstance(event["part"].get("text"), str):
            raise ValueError("Invalid OpenCode response text.")
    finishes = [e for e in events if e.get("type") == "step_finish"]
    if not finishes or finishes[-1].get("part", {}).get("reason") != "stop":
        raise ValueError("OpenCode did not finish normally; the response may be truncated.")
    return json_object(
        "".join(e.get("part", {}).get("text", "") for e in events if e.get("type") == "text")
    )


def event_usage(provider: str, raw: str) -> dict:
    """Return normalized usage fields when a CLI includes them in its event stream."""
    try:
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    except (json.JSONDecodeError, TypeError):
        return {}
    if not all(isinstance(event, dict) for event in events):
        return {}
    if provider == "claude":
        results = [event for event in events if event.get("type") == "result"]
        source = results[-1] if results else {}
        tokens = source.get("usage") if isinstance(source.get("usage"), dict) else {}
        cost = source.get("total_cost_usd")
    else:
        finishes = [event for event in events if event.get("type") == "step_finish"]
        source = finishes[-1].get("part", {}) if finishes else {}
        tokens = source.get("tokens") if isinstance(source.get("tokens"), dict) else {}
        cost = source.get("cost")
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}

    def number(value):
        return value if isinstance(value, int | float) and value >= 0 else None

    usage = {
        "input_tokens": number(tokens.get("input") or tokens.get("input_tokens")),
        "output_tokens": number(tokens.get("output") or tokens.get("output_tokens")),
        "reasoning_tokens": number(tokens.get("reasoning") or tokens.get("reasoning_tokens")),
        "cache_read_tokens": number(cache.get("read") or tokens.get("cache_read_input_tokens")),
        "cache_write_tokens": number(
            cache.get("write") or tokens.get("cache_creation_input_tokens")
        ),
        "cost_usd": number(cost),
    }
    return {key: value for key, value in usage.items() if value is not None}

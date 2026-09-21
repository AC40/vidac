# Vidac

A local workspace for turning raw footage into a reusable clip library and editable rough cuts.

Vidac keeps your originals intact, generates review proxies, detects camera cuts, builds virtual selects, and exports timelines to DaVinci Resolve or Final Cut Pro. Optional Ollama, Codex CLI, Claude Code and OpenCode models describe footage and plan edits from creative direction. Everything lives in one repository.

## Start

Prerequisites: [uv](https://docs.astral.sh/uv/), Node.js 22+, and FFmpeg/ffprobe on PATH.

```sh
uv sync
npm ci
npm run dev
```

Open **http://localhost:5173**. The API runs at **http://127.0.0.1:8741**; interactive API docs are at `/docs`.

For a single local server:

```sh
npm run build
uv run vidac serve
```

Open **http://127.0.0.1:8741**. Stop with Ctrl-C. Library data persists in `.vidac/` (ignored by Git). Set `VIDAC_WORKSPACE=/absolute/path` to keep it on another drive. Run one API process per workspace; a file lock prevents duplicate job consumers. `VIDAC_FFMPEG` and `VIDAC_FFPROBE` can point to custom binaries.

For private remote access through Tailscale, see [tailnet serving](docs/development.md#private-tailnet-access). The API stays on loopback; only explicitly configured proxy hostnames are accepted. Access relies on your tailnet permissions, not an app login.

## What works

- Ingest local files/folders or upload video through the browser; SHA-256 deduplication.
- Relink moved or renamed originals individually or by searching a folder and its subfolders.
- FFmpeg shot detection, configurable select lengths, proxies, thumbnails, waveforms.
- Virtual clips with editable ranges, tags, descriptions, favorites and full-text search.
- Optional visual descriptions and activity segmentation through Ollama or an installed AI CLI.
- Optional local speech transcription with faster-whisper.
- Deterministic offline assembly or model-driven creative rough cuts.
- Reorder shots, adjust trims/looks/audio, save immutable edit revisions, restore history.
- Render an MP4 review copy; export OTIO, FCPXML or a Resolve handoff ZIP.
- Persistent queued jobs with retry, cancellation and restart recovery; watched footage folders.
- Agent-friendly CLI and strict JSON edit-plan schema.

The synthetic sample in the welcome screen is generated locally from three colored shots. It is a pipeline exercise, not footage or an AI classification demo.

## Moved footage and missing drives

Clips and saved edits stay in the library when an original goes offline. Under **Source footage**, choose **Relink media**, enter the new folder path, and select **Search and relink**. Vidac searches its subfolders, verifies exact file contents, and lists recovered and still-missing originals in the activity queue. Renamed files work too. To reconnect one source, select it in the relink dialog and enter its new file path.

Reimporting the same video automatically reconnects a missing original without duplicating clips. Watched folders also do this after files stop changing. Add the new drive's footage folder in Settings to use that behavior. Folder searches only run where you ask; Vidac does not scan all attached drives.

```sh
uv run vidac relink /Volumes/NewDrive/Footage
uv run vidac relink /Volumes/NewDrive/renamed.mov --asset-id ASSET_ID
```

Relinking updates the source location without moving files or changing clip IDs, metadata, trims, or saved revisions. Exact copies qualify; transcoded or edited replacements do not. Already-online sources are left alone during folder searches. Unreadable files appear as warnings. Cancelling a search keeps links already recovered; rerunning it searches for the remaining missing sources. VFR editing intermediates remain separate workspace derivatives. Reimport the original to rebuild a missing intermediate or proxy. Re-export editor handoffs to include the new paths; previously generated files and existing Resolve/FCP projects are unchanged.

## AI providers

Choose **Vision provider** and **Planning provider** independently in Settings, then enter a model ID or choose a suggestion. Empty model fields disable that task. Switching providers clears the model to avoid carrying an incompatible ID across services. Existing workspaces keep Ollama as their default.

| Provider | Setup on the machine running Vidac | Model selection |
| --- | --- | --- |
| Ollama | Install Ollama and pull a model yourself | Installed model names |
| Codex CLI | Install `codex`, then run `codex login` with your ChatGPT account | Model IDs supported by your account; suggestions use Codex's local model cache |
| Claude Code | Install `claude`, then run `claude auth login` | `sonnet`, `opus`, `haiku`, or an exact model ID |
| OpenCode | Install `opencode`, then run `opencode auth login` for your provider | `provider/model` IDs from `opencode models`, or enter one manually |

Use current CLIs. Vidac calls their non-interactive interfaces and reuses their authentication; it does not extract login tokens or implement subscription billing. Your CLI account controls model access, usage limits and charges. A detected executable or suggested model does not prove authentication or entitlement. Codex uses its saved CLI login, including ChatGPT subscription access when available. No separate API key is required by Vidac.

Enable **Allow CLI provider data sharing** before using a CLI provider. Planning sends your creative brief and catalogue descriptions. Vision sends sampled JPEG frames. Original video files stay on disk. The selected provider's data policies apply, and OpenCode may retain local sessions. Vidac never falls back to another provider automatically.

[T3 Code](https://github.com/pingdotgg/t3code) controls underlying agent CLIs. Select the same Codex, Claude Code or OpenCode provider in Vidac; there is no separate T3 inference adapter or model catalogue.

```sh
uv run vidac models --provider codex
uv run vidac configure-ai planning codex YOUR_MODEL_ID --allow-remote
uv run vidac configure-ai vision claude sonnet --allow-remote
uv run vidac edit "A calm morning routine" --ai
```

`--allow-remote` enables CLI data sharing for the workspace. Disable it in Settings to prevent further CLI inference. `VIDAC_CODEX`, `VIDAC_CLAUDE` and `VIDAC_OPENCODE` can point to executable files if the server's PATH differs from your terminal. These are paths, not shell commands. The model timeout is configurable from 10 to 1,800 seconds, with a 300-second default.

CLI integrations are tested against response fixtures and local subprocesses. No live account inference or subscription entitlement was verified by the test suite. See [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive), [Claude Code programmatic usage](https://code.claude.com/docs/en/headless), and [OpenCode CLI](https://opencode.ai/docs/cli/) for their authentication and command contracts.

### Local Ollama and transcription

Install [Ollama](https://ollama.com/), pull a vision-capable model that fits your hardware (for example `qwen3-vl:8b`), and select it in Settings. Vidac does not download models automatically. Configure a planning model separately; the same vision model may serve both roles.

```sh
ollama pull qwen3-vl:8b
```

New imports are analyzed when a vision model is configured. Existing clips can be annotated from the inspector. Analysis uses up to twelve timestamped frames per window, so it can miss quick actions; all results remain editable. Model failures preserve unclassified selects and appear as job warnings. With no model, the application explicitly uses camera cuts and duration-based selects; offline assembly uses keyword matching and pacing rather than pretending to understand the creative brief.

To enable speech transcription:

```sh
uv sync --extra transcription
```

Then enable transcription in Settings. The first transcription downloads the selected faster-whisper weights. Word timings are approximate and should be checked before dialogue cuts.

## CLI / AI agent use

```sh
uv run vidac ingest /path/to/footage
uv run vidac search "brushing teeth"
uv run vidac edit "A calm morning routine" --duration 30 --pace calm --ai
uv run vidac edits
uv run vidac export EDIT_ID --format resolve
uv run vidac export EDIT_ID --format preview
uv run vidac schema
uv run vidac import-plan plan.json
uv run vidac doctor
```

An external assistant can read the catalogue, write a plan matching `vidac schema`, and call `import-plan`. Source in/out points are integer frames on the asset's declared rational frame rate; out points are exclusive. The engine validates references, shot bounds, dimensions, and durations. This lets Codex operate the same engine as the UI without giving a model control of media paths or arbitrary render commands.

See [architecture](docs/architecture.md), [editor handoff](docs/handoff.md), [agent workflow](docs/agent-workflow.md), and [development](docs/development.md) for contracts and current limitations.

This release is a local web workspace, not a packaged desktop app. It reuses installed CLI logins for optional AI inference and also supports external assistants through the Vidac CLI. Looks are basic presets, not automatic camera matching. Native Resolve/FCP import needs a pilot on your editing machine; this repo tests export structure and the Resolve call contract, not live editor behavior. No cloud storage, semantic embeddings or iMovie export is included.

## Verify

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
npm run check
npm run check:format
npm run build
npm run test:browser
```

The media integration tests generate their own videos with FFmpeg, ingest them, validate cut ranges, export timelines and render real previews. They do not require AI models or external services.

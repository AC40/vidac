# Development

## Setup

Install uv, Node.js 22+, and an FFmpeg build with ffprobe, H.264 encoding, AAC, image filters and ProRes encoding. HDR proxy conversion additionally needs `zscale` and `tonemap`. `uv sync` selects Python 3.12 from `.python-version`; `uv` can install it if necessary.

```sh
uv sync --locked
npm ci
npm run dev
```

Vite runs on `127.0.0.1:5173` and forwards `/api` to `127.0.0.1:8741`. The developer runner starts both processes. API development does not use automatic reload, so restart it after Python changes. React changes use Vite's hot reload.

For production-like local use, run `npm run build`, then `uv run vidac serve`. Run commands from the repo root. Both dev and production use `.vidac` unless `VIDAC_WORKSPACE` is set. They should not run on port 8741 simultaneously.

## Private tailnet access

Use the built app, not the Vite development server, when sharing this workspace. Keep the API bound to loopback. Set `VIDAC_ALLOWED_HOSTS` to an explicit comma-separated list of the machine's Tailscale DNS name and IPv4 address (no schemes, ports or wildcards):

```sh
npm run build
VIDAC_ALLOWED_HOSTS=YOUR_MACHINE.YOUR_TAILNET.ts.net,YOUR_TAILSCALE_IPV4 uv run vidac serve
```

In another terminal:

```sh
tailscale serve --bg --http=5173 http://127.0.0.1:8741
tailscale serve status
```

Open the HTTP URL printed by Serve from a device connected to your tailnet. The connection between Tailscale devices is encrypted even when the application URL uses HTTP. This does not enable public Funnel access or change tailnet policy. Host allowlisting is not user authentication: anyone permitted by your tailnet policies to access this port can operate the workspace and access its media. Use this only on a trusted private tailnet.

Disable just this route with `tailscale serve --http=5173 off`. Do not use `tailscale serve reset`, which would remove other apps' routes too. The app process must also remain running for the route to work; the Serve background option only keeps the proxy configuration active.

## Checks

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
npm run check
npm run check:format
npm run build
```

Run only non-media tests with `uv run pytest -m 'not media'`. Media tests create their own synthetic assets under pytest's temporary directory and perform real ingest, deduplication, proxy generation, cut detection, CFR conformance, export and preview rendering. They skip if FFmpeg/ffprobe is absent. They never inspect your footage. Model tests stub responses; CLI transport tests also use local Python subprocesses to check cancellation, timeouts and error handling. They do not call model services, upload footage or verify live account access. Resolve tests use a contract double.

Browser tests use a separate fresh temporary workspace and port 8742. Build the frontend first:

```sh
npm run build
npx playwright install chromium
npm run test:browser
```

To use an already installed Chrome instead of downloading a browser:

```sh
VIDAC_BROWSER_EXECUTABLE=/usr/bin/google-chrome npm run test:browser
```

Screenshots and failure traces go to ignored `test-results/`. Test workspaces are created under the system temporary directory and retained for debugging; no user workspace is deleted. Browser scenarios cover the sample import, thumbnails, favorites, Unicode search, editing, revisions, rendering, handoff generation, persistence and mobile overflow. They do not certify live editor import or model quality.

Format with `uv run ruff format .` and `npm run format:web`. CI runs backend tests and frontend checks/build, then the browser scenarios using Chromium.

## Changing contracts

- Domain validation belongs in the Python engine, not only the UI.
- Changes to SQLite require a versioned migration. Never rebuild a catalogue to fix a search index.
- Saved revisions must remain immutable. Never read a current library trim in place of a saved edit's explicit trim.
- Keep generated files under the workspace. Use subprocess argument arrays, not shell commands. Do not overwrite originals.
- Every external model response must pass schema, reference and range validation before a persistent edit is created.
- Add a real-media regression test for timing/render fixes, including fractional rates and source-audio/silent transitions.
- Keep limitations in `handoff.md` current when adding editor features. Generated XML and mocked API calls are not proof of native editor compatibility.

## Known gaps / next increments

The current app intentionally uses a web workspace, not a packaged Tauri shell. Useful next increments are: a full timestamp audit and media conformance report; semantic embeddings for library retrieval; manual activity splitting/merging; thumbnail contact sheets for external agents; a native Resolve acceptance harness; camera-specific color normalization; autosaved drafts; and optional desktop packaging. Cloud storage and multi-user authentication require a separate design rather than exposing this local API.

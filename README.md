# Atlas

Atlas turns a YouTube playlist into study notes: it fetches each video's transcript, has an LLM
(Gemini by default, or OpenRouter) write Markdown notes, lets you review, edit and approve them
in a local web UI, and publishes the approved notes as numbered Google Docs plus an index Doc in
one Drive folder. It is a local, single-user tool: one process, one SQLite file, no login.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/getting-started/installation/). It installs the pinned Python
  (`.python-version`: 3.13.13; `pyproject.toml` requires `>=3.13,<3.14`).
- Node.js `^20.19.0 || >=22.12.0` (required by Vite 7), only to install and build the frontend.
- A YouTube Data API v3 key and a Gemini (Google AI Studio) or OpenRouter key.
- For publishing: a Google Cloud project with the Drive API and Docs API enabled.

## One-time setup

| | Windows (PowerShell) | macOS / Linux |
|---|---|---|
| Install deps, create `.env` | `.\scripts\setup.ps1` | `./scripts/setup.sh` |

Setup runs `uv sync --locked`, `npm --prefix frontend ci`, and copies `.env.example` to `.env`
if it does not exist. Put your keys in `.env`:

```dotenv
YOUTUBE_API_KEY=...      # required to add playlists
GEMINI_API_KEY=...       # required when notes_provider is gemini (default)
OPENROUTER_API_KEY=...   # only when notes_provider is openrouter
```

Every other setting lives in `src/configs/config.yaml` and can be overridden per run with
`ATLAS_<SETTING>` (for example `ATLAS_PORT=8001`). See [docs/CONFIG.md](docs/CONFIG.md). The
database is created and migrated on first start.

If PowerShell blocks the scripts, run them as
`powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1` (same for the others).

## Run

**Single-app mode** (normal use): API, background worker and built UI on one port
(`host`/`port` in `config.yaml`, default `http://127.0.0.1:8000`).

| | Windows (PowerShell) | macOS / Linux |
|---|---|---|
| Build the UI (once, and after frontend changes) | `.\scripts\build.ps1` | `./scripts/build.sh` |
| Start | `.\scripts\start.ps1` | `./scripts/start.sh` |

`start` runs `uv run --locked python -m backend`. It validates the configuration and that the port
is free first, and exits with a message if not. Without a build the page explains how to build.
Ctrl+C stops it; the worker shuts down gracefully and resumes interrupted work on the next start.

**Dev mode** (hot reload): `.\scripts\dev.ps1` or `./scripts/dev.sh` runs the backend with
`--reload` plus the Vite dev server. Open `http://127.0.0.1:5173`; Ctrl+C stops both. Vite
proxies `/api` to the configured backend port (the script passes it as `ATLAS_API_URL`).

Health: the **Diagnostics** screen in the UI (`/diagnostics`; JSON at `GET /api/diagnostics`) shows configuration, database, keys
set or not, Drive files and token, frontend build, last 50 warnings and errors; never secret
values).

## Google Drive consent (once)

1. In the [Google Cloud Console](https://console.cloud.google.com/), enable the **Google Drive
   API** and **Google Docs API** in your project.
2. Configure the OAuth consent screen (External) with the scopes `.../auth/drive.file` and
   `.../auth/documents`, and add yourself as a test user. While the app's publishing status is
   **Testing**, Google expires the refresh token after 7 days and you must re-authorize; set it to
   **In production** to avoid that.
3. Create an **OAuth client ID** of type **Desktop app** and download the JSON.
4. Start Atlas, open **Settings**, upload the JSON (Step 1, saved as `credentials.json`), then
   press **Authenticate account** (Step 2). This must run on the machine that runs Atlas: a
   browser opens there, and Atlas waits up to 5 minutes for Google's redirect to a local port
   (a free port for Desktop clients; `http://localhost:8080/` for a "web" client). The token is
   saved to `token.json` and refreshed automatically.

`credentials.json`, `token.json` and `.env` are gitignored; never commit them.

## Tests

| | Windows (PowerShell) | macOS / Linux |
|---|---|---|
| All checks | `.\scripts\test.ps1` | `./scripts/test.sh` |

Runs ruff, pytest (temporary SQLite, no network, no real keys), eslint, `tsc -b` and vitest.

## Docker

`docker compose up --build` builds the UI and serves API, UI and worker at
`http://127.0.0.1:8000`. Do the Drive consent on the host first, then copy `credentials.json` and
`token.json` into `./secrets` (mounted writable, because the token is rewritten on refresh).
`./data` and `./transcripts` are mounted so the database and subtitle files survive rebuilds.
Stop the container before running Atlas locally on port 8000.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): layers, data model, state machine, retries,
  publishing, crash recovery, concurrency, adding an LLM provider.
- [docs/CONFIG.md](docs/CONFIG.md): every setting and secret.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): symptoms, causes, fixes; logs; backups.
- [Atlas_PRD.md](Atlas_PRD.md) (requirements) and [design.md](design.md) (UI).

## License

MIT License. See [LICENSE](LICENSE).

# Atlas Setup and Run SOP

## Prerequisites

- `uv` (Python package manager; it installs Python 3.13 itself)
- Node.js 20.19+ or 22.12+ (only to install and build the frontend)
- Windows PowerShell, or a bash shell on macOS / Linux
- API keys in the repository `.env` file (setup creates it from `.env.example`):

```dotenv
YOUTUBE_API_KEY=your_youtube_key         # required: lists the playlist's videos
GEMINI_API_KEY=your_gemini_key           # required when notes_provider is gemini (default)
OPENROUTER_API_KEY=your_openrouter_key   # required only when notes_provider is openrouter
```

All other settings are in `src/configs/config.yaml`.

## Commands

| Task | Windows (PowerShell) | macOS / Linux |
|---|---|---|
| One-time setup | `.\scripts\setup.ps1` | `./scripts/setup.sh` |
| Build the UI | `.\scripts\build.ps1` | `./scripts/build.sh` |
| Run (single app, one port) | `.\scripts\start.ps1` | `./scripts/start.sh` |
| Develop (hot reload) | `.\scripts\dev.ps1` | `./scripts/dev.sh` |
| All checks | `.\scripts\test.ps1` | `./scripts/test.sh` |

If PowerShell blocks scripts, run them as
`powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1`.

- Single app: open `http://127.0.0.1:8000` (host and port from `config.yaml`).
- Dev mode: open `http://127.0.0.1:5173`; Vite proxies `/api` to the backend.
- Stop either with Ctrl+C; the background worker shuts down gracefully.

## Checking the setup

Open the Diagnostics page, or `http://127.0.0.1:8000/api/diagnostics`. It reports the
configuration, the database, whether each key is set (never its value), the Google Drive
credentials and token, whether the UI is built, and the last 50 warnings/errors.

Logs are written to the terminal and to `logs/atlas.log` (rotating, secrets redacted).

## Common problems

- "Atlas cannot start: invalid configuration": fix the listed keys in `config.yaml` or `.env`.
- "Port 8000 on 127.0.0.1 is already in use": another Atlas or its Docker container is running.
  Stop it, or change `port` in `config.yaml`.
- Ingesting answers `CONFIG_MISSING`: `YOUTUBE_API_KEY` is not set in `.env`.
- A video fails with "GEMINI_API_KEY is not set": add the key to `.env` and press Retry.

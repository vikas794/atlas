# Atlas Setup and Fast Start

Atlas normally starts with one command. The launcher reuses a ready backend and frontend, so later starts are fast.

## Prerequisites

- Windows PowerShell
- Node.js 18+ (`npm.cmd`)
- `uv` (Python package manager)
- API keys in the repository `.env` file (copy `.env.example` to `.env`; empty values mean "use the default"):

```dotenv
YOUTUBE_API_KEY=your_youtube_key         # required: lists the playlist's videos
GEMINI_API_KEY=your_gemini_key           # required when NOTES_PROVIDER=gemini (default)
OPENROUTER_API_KEY=your_openrouter_key   # required only when NOTES_PROVIDER=openrouter
```

The launcher creates `.env` from `.env.example` when it is missing and tells you to fill it in.

## First Start

From the repository root, run:

```powershell
.\scripts\start-dev.ps1
```

The launcher automatically creates `.venv` with Python 3.13.13, installs the backend and frontend dependencies from `uv.lock`, starts both servers, and waits until they respond. Database migrations run automatically when the backend starts, and the background worker (video processing, retries, publishing) starts with it.

Open:

- Frontend: `http://127.0.0.1:5173`
- API health: `http://127.0.0.1:8000/api/health`

## Google Drive consent (once)

Publishing needs a one-time Google consent on this machine: put the OAuth client file at `credentials.json` (or upload it in the Settings screen), then press Authorize in the Settings screen. A browser window opens; the token is stored in `token.json` and refreshed automatically. See the README for the Google Cloud steps.

## Later Starts

Run the same command:

```powershell
.\scripts\start-dev.ps1
```

It skips dependency installation when `.venv` and `frontend/node_modules` already exist, then starts only services that are not already running.

Each time the launcher starts a service, it overwrites that service's previous files in `logs/`, keeping the new run's log output clean. Application logs (with `playlist_id` and `video_id` on every pipeline event) are in `logs/backend-dev.err.log` and, with `ATLAS_LOG_FILE_LOGGING=true`, also in `logs/atlas.log`.

To open the frontend automatically:

```powershell
.\scripts\start-dev.ps1 -OpenBrowser
```

## Refresh Dependencies

After changing either dependency manifest, run:

```powershell
.\scripts\start-dev.ps1 -Install
```

The backend dependencies are defined in `pyproject.toml` and locked in `uv.lock` (run `uv lock` after editing `pyproject.toml`).

## Backups

The SQLite file `data/atlas.sqlite3` holds the only copy of transcripts and unreviewed edits. Back it up by copying it (stop Atlas first, or use `sqlite3 data/atlas.sqlite3 ".backup 'backup.sqlite3'"`). Atlas also writes `data/atlas.sqlite3.pre-<migration>.bak` automatically before applying a pending migration to an existing database.

## Stop Atlas

To stop the local frontend and backend safely, run:

```powershell
.\scripts\stop-dev.ps1
```

The script stops only a Python process on the Atlas API port (`8000`) and a Node process on the Vite port (`5173`). If another application owns either port, it leaves that process running.

## Troubleshooting

If PowerShell blocks the launcher, run it for the current session only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\start-dev.ps1
```

If a server is stuck, close its PowerShell process and run the launcher again. The script verifies both URLs before reporting success.

If ingesting a playlist answers `CONFIG_MISSING`, `YOUTUBE_API_KEY` is not set in `.env`. If a video fails with "GEMINI_API_KEY is not set", add the key to `.env` and use Retry on the video.

# Atlas

> Turn a YouTube playlist into a skimmable study "course": one Google Doc of AI-written notes
> per video, plus an index Doc, in a Google Drive folder.

Atlas is a single-user, local-first tool. You paste a playlist link; Atlas fetches every video,
gets each transcript, asks an LLM (Gemini by default, or OpenRouter) to write detailed notes
with your own prompt, lets you review/edit/approve them in a web UI, and publishes the approved
notes to Google Drive as numbered Docs (`001 - Title`, `002 - Title`, ...) with an index Doc on
top. Re-running a playlist only processes the videos added since last time.

What it does for you:

- **Pipeline per video:** queued -> fetching transcript -> generating notes -> pending review ->
  approved -> published, persisted in SQLite so nothing is ever recomputed unless you ask.
- **Failure handling:** permanent failures (private/deleted video, no subtitles, ...) fail fast
  with a readable reason; transient ones (rate limits, network) are retried automatically with
  exponential backoff, up to `ATLAS_MAX_AUTO_RETRIES`, then wait for a manual retry.
- **Background worker:** processing, retries and publishing run in a worker inside the API
  process, so requests return immediately. After a crash or restart in-flight work resumes.
- **Review UI:** status dashboard, inline editing, approve / approve-all, regenerate, retry.
- **Drive publishing:** one folder per playlist, numbered Docs with a heading and shaded code
  blocks, and an index Doc. Publishing is resumable and never duplicates Docs.

---

## Architecture

Hexagonal (ports and adapters):

- `src/domain/` - models, the stage machine, sync diffing, failure classification, Doc naming,
  Markdown-to-Docs conversion, and the ports (`PlaylistRepositoryPort`, `DrivePublisherPort`,
  `NotesGeneratorPort`, `PlaylistProviderPort`, ...).
- `src/application/` - use cases (ingest, process video, actions, publish) and the background
  `PlaylistWorker`. They depend only on ports.
- `src/infrastructure/` - adapters: YouTube Data API, yt-dlp transcripts, Gemini / OpenRouter
  (`llm/`), SQLite (`storage/`), Google Drive/Docs (`google/`).
- `src/transport/http/fastapi/` - REST API; `dependencies.py` is the composition root.
- `frontend/` - React + TypeScript + Vite.

See [`docs/SYSTEM_ARCHITECTURE.md`](docs/SYSTEM_ARCHITECTURE.md) and the product spec in
[`Atlas_PRD.md`](Atlas_PRD.md).

---

## One-time setup

Prerequisites: [`uv`](https://docs.astral.sh/uv/) (it installs Python 3.13 itself), Node.js
20.19+ or 22.12+ (only to install and build the frontend), and the keys below. No Docker needed.

```powershell
.\scripts\setup.ps1           # Windows: uv sync, npm ci, creates .env from .env.example
```

```bash
./scripts/setup.sh            # macOS / Linux: the same
```

(If PowerShell refuses to run scripts, use
`powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1`, and the same for the others.)

Configuration lives in two places only: **secrets in `.env`**, **everything else in
`src/configs/config.yaml`** (AI provider and model, concurrency, retries, database path, host,
port, log level, ...). Both are validated at startup; an invalid value stops Atlas with a list of
the problems. Any config key can be overridden for one run with `ATLAS_<KEY>`.

Edit `.env`:

| Variable | Needed for |
|---|---|
| `YOUTUBE_API_KEY` | **Required** to ingest playlists (YouTube Data API v3 key) |
| `GEMINI_API_KEY` | Required when `NOTES_PROVIDER=gemini` (the default) |
| `OPENROUTER_API_KEY` | Required when `NOTES_PROVIDER=openrouter` (OpenRouter, not OpenAI) |

A missing key does not stop Atlas from starting: `GET /api/diagnostics` (and the Diagnostics
page) lists what is missing. Using a feature without its key is reported with a clear message
naming the variable (HTTP 503
`CONFIG_MISSING` for the YouTube key, a permanent `config_error` failure on the video for an LLM
key) - it never retries forever.

**Database migrations run automatically** when the app starts (`data/atlas.sqlite3` is created
on first run). Nothing to run by hand.

### Google Drive consent (once)

1. In the [Google Cloud Console](https://console.cloud.google.com/) create a project and enable
   the **Google Drive API** and **Google Docs API**.
2. Configure the OAuth consent screen (External, Testing; add yourself as a test user) with the
   scopes `.../auth/drive.file` and `.../auth/documents`.
3. Create an **OAuth client ID** of type **Desktop app**, download the JSON and save it as
   `credentials.json` in the repository root (or upload it from the Settings screen).
4. Complete the consent **once, on the machine that runs Atlas**: start Atlas, open the
   Settings screen and press Authorize (or `POST /api/drive/auth`). A browser window opens and
   Google redirects to `http://localhost:8080/`. The token is stored in `token.json` and refreshed
   automatically.

`GET /api/drive/status` reports `credentials_missing`, `needs_consent` or `authorized`
(authorized means a valid or refreshable token exists). `credentials.json` and `token.json` are
gitignored; never commit them.

---

## Run it

**Single-app mode** (normal use): one process, one port. Build the UI once (and after each
frontend change), then start:

| | Windows (PowerShell) | macOS / Linux |
|---|---|---|
| Build the UI | `.\scripts\build.ps1` | `./scripts/build.sh` |
| Start | `.\scripts\start.ps1` | `./scripts/start.sh` |

Open <http://127.0.0.1:8000>. `start` is just `uv run python -m backend`: the API, the
background worker and the built UI (`frontend/dist`, with SPA fallback) on the `host`/`port`
from `config.yaml`. Nothing is built at runtime; without a build the page says how to build.
Ctrl+C stops it gracefully (the worker stops with the API process).

**Dev mode** (hot reload): `.\scripts\dev.ps1` or `./scripts/dev.sh` runs the backend with
auto-reload plus the Vite dev server, which proxies `/api` to the backend. Open
<http://127.0.0.1:5173>; Ctrl+C stops both.

If the port is taken (for example by the Docker container), Atlas says so and exits; stop the
other server or change `port` in `config.yaml`.

Health: `GET /api/health` (liveness) and `GET /api/diagnostics` (config, database, keys, Drive,
frontend build and the last 50 warnings/errors, never secret values).

---

## Docker

```bash
cp .env.example .env                 # fill in the keys
mkdir -p secrets && cp credentials.json token.json secrets/   # consent was done on the host
docker compose up --build            # UI + API at http://127.0.0.1:8000
```

- The container runs the API, the built frontend **and the background worker** in one process.
- Do the Google consent **on the host once** (it needs a browser and the `localhost:8080`
  redirect), then copy `credentials.json` and `token.json` into `./secrets`. That folder is
  mounted **writable** because Atlas rewrites `token.json` when the token refreshes; the old
  read-only file mounts made every refresh fail.
- Only port 8000 is published, and only on `127.0.0.1` (Atlas has no authentication). Port 8080 is
  not published; the consent flow does not run inside the container.
- `./data` (the SQLite database) and `./transcripts` are mounted so nothing is lost on rebuild.

---

## SQLite database and backups

All playlists, transcripts, notes and unpublished edits live in one file, `data/atlas.sqlite3`
(plus `-wal`/`-shm` side files while running). **That file is the only copy of your transcripts
and unreviewed edits**, so back it up by copying it:

```bash
# stop Atlas, then:
cp data/atlas.sqlite3 backups/atlas-$(date +%Y%m%d).sqlite3
# or, while running (safe with WAL):
sqlite3 data/atlas.sqlite3 ".backup 'backups/atlas-backup.sqlite3'"
```

To restore, stop Atlas and put the backup in place of `data/atlas.sqlite3`.

### Migration warning

Migration **0003** (the move from the old search-run app to the playlist pipeline) **drops the
legacy tables** `runs`, `videos`, `transcripts`, `summaries`, `comparisons`, `assignments`,
`generation_jobs` and `cache_entries`. Only `usage_ledger` survives. Migration **0004** rebuilds
`videos` (one row per playlist position, 1-based positions, duplicate-video support) and keeps
all existing rows. Before applying any pending migration to an existing, populated database,
Atlas now copies it to `data/atlas.sqlite3.pre-<migration>.bak` (e.g. `...pre-0003_playlist_pipeline.bak`).
**If you have data in an old database you care about, copy the file yourself before upgrading too.**

---

## Quality checks

`.\scripts\test.ps1` or `./scripts/test.sh` runs everything: ruff, pytest (temp SQLite; no
network, no real keys), eslint, `tsc -b` and vitest.

---

## License

MIT License. See [LICENSE](LICENSE).

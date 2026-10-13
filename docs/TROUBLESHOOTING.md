# Troubleshooting

Symptom, cause, fix. Messages are quoted as Atlas prints them. Start with
`GET /api/diagnostics` (open `http://127.0.0.1:8000/api/diagnostics`): it checks the
configuration, database, keys (set / not set, never the value), Drive credentials and token,
and the frontend build, and lists the last 50 warnings and errors. There is no Diagnostics
screen in the UI; the Settings screen shows the Google Drive state.

## Where to look

- **Logs**: the terminal, and `logs/atlas.log` (setting `log_file`; rotating, 5 MB x 3 backups,
  secrets redacted; disable with `log_file_logging: false`). Pipeline lines carry
  `playlist_id=... video_id=...`. Set `log_level: DEBUG` for more.
- **Recent errors**: `recent_errors` in `/api/diagnostics`, an in-memory buffer of the last 50
  WARNING+ records since the process started.
- **Per video**: the failure reason and kind are shown in the playlist table and returned by
  `GET /api/videos/{id}` (`failure_type`, `failure_kind`, `failure_reason`, `next_retry_at`,
  `auto_retry`, `retries_exhausted`).
- **Per publish**: `GET /api/playlists/{id}` has `status` (`publishing`, `published`,
  `publish_failed`) and `last_publish_error`.

## Startup

### Port already in use

```
Port 8000 on 127.0.0.1 is already in use (another Atlas, its Docker container or an old dev server?). Stop it, or set another `port` in src/configs/config.yaml (or ATLAS_PORT in .env).
```

Cause: something else listens on the port, typically the Atlas Docker container
(`127.0.0.1:8000`) or a dev server that is still running. Atlas exits with status 1 instead of a
traceback. `scripts/dev` runs the same check before starting Vite.

Fix: stop the other server (`docker compose down`), or change `port` in `config.yaml` /
`ATLAS_PORT`. For single-app mode that is enough. **Dev mode note:** the Vite proxy in
`frontend/vite.config.ts` always forwards `/api` to `127.0.0.1:8000`. With another port the
backend starts fine, but the dev UI talks to whatever owns port 8000 (in testing, the Docker
Atlas answered). In dev mode free port 8000, or change the proxy target as well.

### Invalid configuration (exit code 2)

```
Atlas cannot start: invalid configuration (config.yaml, environment/.env).
  - port: expected a whole number (got '8000')
  - unknown setting 'max_concurent_videos' in config.yaml (misspelled or removed?)
  - notes_prompt_name: prompt file 'missing.yaml' not found in 'src/prompts'
  - ATLAS_PORT='abc' in the environment/.env: expected int
```

Cause: a wrong type, an unknown key, an out-of-range value, an unparsable `ATLAS_*` value or a
missing prompt file. Every problem is listed at once. Fix each line in
`src/configs/config.yaml` or `.env` (rules in [CONFIG.md](CONFIG.md)); quote text values in
YAML, leave numbers unquoted.

### "The Atlas frontend has not been built yet"

Cause: single-app mode serves `frontend/dist`, which does not exist. The API still works; the
page answers 503 and `/api/diagnostics` shows a `frontend_build` warning. `scripts/start` also
warns before starting.

Fix: `.\scripts\build.ps1` or `./scripts/build.sh`, then reload (no restart needed). Rebuild
after every frontend change. In dev mode use `http://127.0.0.1:5173` instead.

### PowerShell refuses to run the scripts

Cause: the execution policy blocks unsigned scripts.
Fix: `powershell -ExecutionPolicy Bypass -File .\scripts\<name>.ps1` (for example
`setup`, `build`, `start`, `dev`, `test`). The scripts call `npm.cmd`/`npx.cmd`, so npm's own
`.ps1` shims are not needed.

## Adding a playlist

### 400 `INVALID_URL`

- `URL host 'example.com' is not a recognized YouTube domain.`: only `youtube.com`, `youtu.be` and
  their subdomains are accepted.
- `URL does not contain a 'list' playlist parameter.`: a watch URL without `&list=...`, or a
  channel URL. Paste the playlist URL (`https://www.youtube.com/playlist?list=...`) or a watch
  URL that contains `list=`.

### 404 `PLAYLIST_UNAVAILABLE`

`The playlist was not found or is private.` The YouTube API returned 404 or a
playlist-not-found/forbidden reason. Nothing is stored. Make the playlist public or unlisted,
or check the id. A playlist with no listable videos and no title is also a 404
`PLAYLIST_UNAVAILABLE` ("... returned no videos or is unavailable.").

### 503 `CONFIG_MISSING`

- `YOUTUBE_API_KEY is not set. ...`: add the key to `.env`. It is picked up on the next
  ingest without a restart.
- `The YouTube API rejected the configured key (YOUTUBE_API_KEY). Check that it is valid and that the YouTube Data API v3 is enabled for its project.`:
  the key is wrong, expired, restricted, or the API is not enabled in its Google Cloud project.

### 503 `RATE_LIMITED` on ingest

`The YouTube Data API quota was exceeded; try again later.` The daily Data API quota (or a
per-user rate limit) is used up. This is classified as transient `rate_limited`; nothing is
stored for a new playlist. Submit again later; the quota resets daily.

## Processing videos

### "YouTube is rate-limiting requests." (yt-dlp HTTP 429)

Cause: YouTube throttles subtitle downloads from your IP (`HTTP Error 429: Too Many Requests`,
or "Video unavailable ... try again later"). It is transient `rate_limited`: the video is
retried after about 5, 10, 20 and 40 seconds (`max_auto_retries: 4`, plus jitter). If all fail
the video stays `failed` with `retries_exhausted` and the reason
`YouTube is rate-limiting requests. Automatic retries are used up; press Retry to try again.`
(seen in a real run). It is never retried forever.

Fix: wait (often tens of minutes) and press Retry. Lower `max_concurrent_videos` (for example to
1) so fewer requests hit YouTube at once; raising `backoff_base_seconds` /
`backoff_cap_seconds` spreads automatic retries out.

### Permanent video failures

Shown at once, no automatic retry: private, deleted, age-restricted, region-blocked,
members-only, transcripts disabled by the uploader, and "No transcript is available in a usable
language." (no subtitle track at all; any existing track is used even if it is not
`transcript_language`). Nothing in Atlas can fix these; Retry only helps if the video changed.

### `config_error`: Gemini / OpenRouter key missing or rejected

- `GEMINI_API_KEY is not set. Add your Google AI Studio (Gemini) API key to .env as GEMINI_API_KEY=<key> to generate notes.`
- `Gemini rejected the API key (HTTP 400). Check that GEMINI_API_KEY in .env holds a valid key with access to this model.`
- `... does not know the model '...' (HTTP 404). Check NOTES_MODEL ...` or `... lack of credits (HTTP 402)`.

Cause: a permanent `config_error`, so the video fails without spending retries. Fix the key or
model in `.env` / `config.yaml`, restart Atlas, then press Retry on the failed videos.
The same kind is used for a missing or invalid prompt file.

### Gemini / OpenRouter 429, 5xx or timeout

`Gemini is rate-limiting requests (HTTP 429).`, `Gemini returned a server error (HTTP 503).`,
`A network error interrupted the request to Gemini.` These are transient and retried with
backoff like any other. One call is limited by `notes_request_timeout_seconds` (300 s); a
timeout is a `network_error`. If long transcripts keep timing out, raise the timeout.
The notes phase has its own retry budget, separate from the transcript phase.

### Malformed notes (`output_malformed`)

Empty or very short output, missing sections (Notes, Key Points, Important Details,
Takeaways), output cut off at the model's limit, or a content-filter block. Transient: retried
automatically, then Retry or Regenerate.

### Transcript too large

`The transcript is about N tokens (M characters), which is over the L-token input limit for this model.`
Cause: the estimate (characters / 4) exceeds `gemini_input_token_limit` /
`openrouter_input_token_limit`, or the provider rejected the prompt as too long. Permanent;
Atlas never truncates. Fix: use a model with a larger context and raise the matching limit,
then Retry.

### A video stays `queued`

- It repeats an earlier video in the playlist (`duplicate_of`): it waits until the first
  occurrence has notes, then copies them; if the first one fails for good, the duplicate fails
  with "Same video as #N, which failed: ...".
- It is flagged removed upstream (no longer in the YouTube playlist): the worker skips it and
  Retry/Regenerate refuse it. It is processed again if it reappears and the playlist is re-synced.

## Publishing to Google Drive

### 409 `DRIVE_AUTH_REQUIRED`: access expired or revoked

```
Google Drive access has expired or was revoked. Re-authorize Drive: Open Settings in Atlas and authorize Google Drive (once, on the machine that runs Atlas).
```

Cause: refreshing the token failed with `invalid_grant`. Either access was revoked in the Google
account, or the OAuth app is in **Testing** publishing status, where Google expires refresh
tokens after 7 days. `/api/diagnostics` shows a `drive_token` warning with the same message
(it verifies an expired token with Google instead of trusting the file). Once a refresh has
failed, the Settings screen shows "Not connected" with that message (`/api/drive/status` itself
makes no network call). Without any token the message starts with
`No usable token found at '...token.json'.`

Fix: Settings, Step 2 "Authenticate account". The consent opens a browser **on the machine that
runs Atlas** and waits up to 5 minutes for the redirect; only one consent can wait at a time.
Then publish again; it resumes where it stopped. To avoid the weekly expiry, set the OAuth
consent screen's publishing status to "In production" in Google Cloud.

If consent itself fails the API answers 400 `DRIVE_AUTH_FAILED` with the reason (for example no
`credentials.json` uploaded). An uploaded file that is not a Google OAuth client JSON is
rejected with 400 `DRIVE_INVALID_CREDENTIALS_FILE` and the existing file is kept.

### Publish failed half-way

The playlist shows `publish_failed` with the redacted `last_publish_error` (for example
`Google Drive API failed with HTTP 403: ...` after `drive_max_retries` retries of 429/5xx).
Every folder and Doc id was stored the moment it was created, so pressing Publish again resumes
without duplicating anything. 409 `CONFLICT` means a publish for that playlist is already running.

### Folder or Doc deleted (or trashed) in Drive

A deleted or trashed playlist folder makes the next publish start over in a new folder, with
every approved or published video re-created from its stored notes. A deleted or trashed video
Doc is re-created on the next publish that includes it. The index Doc's state is not checked:
if only the index Doc was deleted, a publish that needs to rewrite it can fail; trashing the
folder makes the next publish rebuild everything.

## Restarts and data

### Backend killed mid-run

On the next start the log shows `Crash recovery reset N in-flight videos to queued` (videos
that were fetching, between phases or generating) and, for an interrupted publish,
`Crash recovery re-queued an interrupted publish`. Stored transcripts and notes are reused, so
completed work is not redone; scheduled retries keep their `next_retry_at`.

### Backup and restore

All state is in `data/atlas.sqlite3` (setting `database_path`), plus `-wal`/`-shm` files while
running. It is the only copy of transcripts and unpublished edits.

```bash
# Atlas stopped: copy the file
cp data/atlas.sqlite3 backups/atlas-$(date +%Y%m%d).sqlite3
# Atlas running: use SQLite's online backup (consistent with WAL)
sqlite3 data/atlas.sqlite3 ".backup 'backups/atlas-backup.sqlite3'"
```

Restore: stop Atlas, delete any `atlas.sqlite3-wal` / `-shm` next to the target, put the backup
in place of `data/atlas.sqlite3`, start Atlas (pending migrations run automatically).

Before applying a pending migration to a database that already has migrations, Atlas writes
`data/atlas.sqlite3.pre-<migration>.bak` (for example `...pre-0004_duplicates_publish_state.bak`).
Migration `0003` drops the old search-run tables (`usage_ledger` survives). To roll back an
upgrade, stop Atlas and restore that file the same way.

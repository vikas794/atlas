# Troubleshooting

Symptom, cause, fix. Messages are quoted as Atlas prints them. Start with the
**Diagnostics** screen (top bar, or `http://127.0.0.1:8000/diagnostics`; the raw JSON is
`GET /api/diagnostics`): it checks the configuration, database, keys (set / not set, never the
value), Drive credentials and token (an expired token is actually refreshed, so a revoked one
shows as a warning), and the frontend build, and lists the last 50 warnings and errors. The
Settings screen is where you upload the OAuth client file and authorize Drive. Once Drive is
connected, the setup steps collapse behind the "Google Drive integration" header; click it to
expand them again.

## Where to look

- **Logs**: the terminal (with `scripts/dev.*` the backend's output goes to
  `logs/backend-dev.log` (stdout) and `logs/backend-dev.err.log` (stderr, where the log lines
  land) instead, both overwritten on every start), and `logs/atlas.log` (setting `log_file`; rotating, 5 MB x 3 backups,
  secrets redacted; disable with `log_file_logging: false`). Pipeline lines carry
  `playlist_id=... video_id=...`. Set `log_level: DEBUG` for more.
- **Recent errors**: the Diagnostics screen (`recent_errors` in `/api/diagnostics`), an in-memory buffer of the last 50
  WARNING+ records since the process started. The screen can filter them (All / Warnings /
  Errors), and **Copy for fixing** copies the checks needing attention plus the currently
  filtered records as plain text, ready to paste to whoever will fix them. **Clear log** empties the in-memory buffer
  (`DELETE /api/diagnostics/recent-errors`); `logs/atlas.log` keeps its history.
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
`ATLAS_PORT`. That is enough in both modes: `scripts/dev` reads the configured host and port
and passes them to Vite as `ATLAS_API_URL` (it prints "Vite proxies /api to ..."). When you run
`npm run dev` by hand against a non-default port, set `ATLAS_API_URL` yourself.

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
after every frontend change. In dev mode use `http://127.0.0.1:5174` instead.

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

Several keys can be set (`YOUTUBE_API_KEY=keyA,keyB`); a rejected one is skipped while a valid
one remains. See "Multiple keys per service" in CONFIG.md.

### 503 `RATE_LIMITED` on ingest

`The YouTube Data API quota was exceeded; try again later.` The daily Data API quota (or a
per-user rate limit) is used up. This is classified as transient `rate_limited`; nothing is
stored for a new playlist. Submit again later; the quota resets daily.

## Processing videos

### "YouTube is rate-limiting requests." (yt-dlp HTTP 429)

Cause: YouTube throttles subtitle downloads from your IP (`HTTP Error 429: Too Many Requests`,
or "Video unavailable ... try again later"). It is transient `rate_limited`: the video is
retried after about 1, 2, 4, 8, 15 and 15 minutes (`rate_limit_backoff_*`,
`rate_limit_max_auto_retries: 6`, plus jitter). If all fail
the video stays `failed` with `retries_exhausted` and the reason
`YouTube is rate-limiting requests. Automatic retries are used up; press Retry to try again.`
(seen in a real run). It is never retried forever.

Atlas asks yt-dlp for the original auto-caption track (`en-orig`) before the plain one (`en`).
YouTube often throttles the plain track with a 429 while serving the original; when the
original was downloaded, that 429 is ignored and logged at debug level only. Atlas also fetches
transcripts one at a time, `transcript_min_interval_seconds` (4s) apart. If it still happens:

1. **Find out whether it is one video or your whole IP.** From the project folder run
   `.venv/Scripts/python -m yt_dlp --skip-download --write-auto-subs --sub-langs en -o "%(id)s" <video url>`
   on the failing video and on another captioned video. If only one fails, compare
   `--sub-langs en-orig` with `--sub-langs en`: a 429 on `en` only is the throttled-derived-track
   case Atlas already works around. If every video fails, your IP is flagged.
2. Wait (often tens of minutes) and press Retry; or raise `transcript_min_interval_seconds`
   (for example 10) and the `rate_limit_backoff_*` values.
3. Install Deno (see the README prerequisites) and restart the backend. yt-dlp warns
   `No supported JavaScript runtime could be found` without it.
4. Update yt-dlp: the dependency is pinned in `pyproject.toml`, so bump the pin and run
   `uv add "yt-dlp[default,curl-cffi]==<version>"` (the project uses `uv`, there is no `pip` in
   the venv). YouTube changes often and old versions get blocked. The version in use is logged
   at startup. The `curl-cffi` extra lets yt-dlp impersonate a browser.
5. Cookies, only if the above is not enough: set `ytdlp_cookies_file` (a Netscape `cookies.txt`;
   a raw `Cookie:` header string does not work) or `ytdlp_cookies_from_browser` (for example
   `firefox`, signed in to YouTube; `Extracted 3 cookies` means it is not signed in). Use a
   throwaway Google account, keep the file out of git (`cookies.txt` at the repo root is
   gitignored; set `ATLAS_YTDLP_COOKIES_FILE` in `.env`), and export it from a private window
   that you then close. A cookie file exported from a normal tab gets rotated by YouTube and
   fails with `The page needs to be reloaded`. Cookies that were pasted anywhere shared should
   be revoked by signing out of that session.
6. A VPN or datacenter IP is throttled fastest; try another network (a phone hotspot is a quick
   test).

### Permanent video failures

Shown at once, no automatic retry: private, deleted, age-restricted, region-blocked,
members-only, transcripts disabled by the uploader, and "No transcript is available in a usable
language." (no subtitle track at all; any existing track is used even if it is not
`transcript_language`). Nothing in Atlas can fix these; Retry only helps if the video changed.

### `config_error`: LLM key missing or rejected

- `No notes models are enabled. Add one on the Models page.`
- `No usable API key for <provider>. Add one on the Keys page.`
- `Gemini rejected the API key '<label>' (Keys page) (HTTP 400). ...` (the wording names the key's label)
- `... does not know the model '...' (HTTP 404). ...` or `... lack of credits (HTTP 402)`.

Cause: a permanent `config_error`, so the video fails without spending retries. Fix it on the
**Models** or **Keys** page (no restart needed), then press Retry on the failed videos. The same
kind is used for a missing or invalid prompt file. A model with no usable key is skipped and the
next one in the chain is used; the error is raised only when every model was turned away
(`GET /api/diagnostics` shows key counts and statuses, never values).

### A key shows `invalid`, `rate_limited` or a cooldown badge

On the Keys page: `invalid` means the provider rejected the key (a health check or a real call got
401/403). Atlas parks it for 5 minutes and turns it off after the third consecutive invalid
check ("Disabled after 3 failed checks"). Replace the key value (that resets status and cooldowns),
or switch it back on, then press Check. `rate_limited` or a cooldown badge means a 429: the
model-and-key pair waits 2 min, then 10 min, 1 h, 24 h if it keeps happening, or the provider's
`Retry-After`. "Clear cooldowns" on the key's row lifts them early. A network error during a
check (or a non-401/403/429 answer) does not change the status; check that the endpoint is reachable (for FreeLLMAPI, that the
gateway is running at its `api_base`). A model shows "No keys" when no key of its provider is
usable (off, invalid, cooling down, over a monthly cap or over the model's RPM/RPD/TPM/TPD).

### The Models or Keys list is empty, or the old model is gone

The registry is seeded once, at the first start after migration `0007`, from the model your
previous version had saved (the `notes_provider` / `*_model` rows of `app_settings`) and the
`*_API_KEY` variables. Those settings no longer exist and changing the variables afterwards does
nothing; use the pages.
Deleted models and keys stay deleted. Deleting `data/atlas.sqlite3` resets everything and imports
again. LLM keys are stored unencrypted in that file (and in its `.bak` copies): keep it private.

### LLM 429, 5xx or timeout

`Gemini is rate-limiting requests (HTTP 429).`, `Gemini returned a server error (HTTP 503).`,
`A network error interrupted the request to Gemini.` These are transient and retried with
backoff like any other. One call is limited by `notes_request_timeout_seconds` (300 s); a
timeout is a `network_error`. If long transcripts keep timing out, raise the timeout.
The notes phase has its own retry budget, separate from the transcript phase.

### Malformed notes (`output_malformed`)

Empty or very short output, missing sections (Questions and Answers, Notes, History, or the older Key Points, Important Details,
Takeaways), output cut off at the model's limit, or a content-filter block. Transient: retried
automatically, then Retry or Regenerate.

### Transcript too large

`The transcript is about N tokens (M characters), which is over the L-token input limit for this model.`
Cause: the estimate (characters / 4) exceeds the model's input token limit (edit it on the Models
page) for every enabled model, or the provider rejected the prompt as too long. Permanent;
Atlas never truncates. Fix: add or enable a model with a larger context and raise its limit,
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
(it verifies an expired token with Google instead of trusting the file). The Settings screen
does the same: `/api/drive/status` refreshes an expired token, so it shows "Not connected" with
that message too (a network failure there leaves it "Connected"). Without any token the message starts with
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

All playlist folders live inside one `Atlas` folder. If that folder is trashed or deleted, the next publish re-creates it for new playlist folders (existing playlist folders keep their stored ids). Playlists published before the `Atlas` folder existed remain at the Drive root.

A deleted or trashed playlist folder makes the next publish start over in a new folder, with
every approved or published video re-created from its stored notes. A deleted or trashed video
Doc is re-created on the next publish that includes it; a deleted or trashed index Doc is
re-created with every title. The index shows each published video's title as a link to its Doc
plus a short description (the notes' Summary section, or the first sentences of Notes for notes
made before `notes_v1_2`). To change a description, edit the Summary in the review screen and
publish again; the index is rewritten in place. A publish only runs when something changed (a newly approved or
edited video), so if nothing changed, nothing is re-created until then.

## Settings page

### A saved value does not change anything

The field shows "Set by an environment variable": an `ATLAS_*` variable (or alias such as
`NOTES_PROMPT_NAME`) in the shell or `.env` takes precedence over a saved value. Unset it and restart.
The AI model is not a setting: change it on the Models page.
Settings marked "Needs restart" (server, logging, paths) are saved but only used after a restart.

### Settings were not saved (422)

The message under the field is the rule that failed, for example `backoff_cap_seconds: must be at
least backoff_base_seconds`. Nothing is saved until every value is valid.

### Saved settings disappeared

They live in the database (`app_settings`). Deleting or restoring an older `data/atlas.sqlite3`
removes them; "Reset to defaults" removes them on purpose.

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

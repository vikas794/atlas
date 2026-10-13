# Atlas configuration

Two places, nothing else:

- **`src/configs/config.yaml`**: every setting that is not a secret. A flat mapping whose keys are
  exactly the fields of `AtlasSettings` (`src/config/settings.py`).
- **`.env`** (repository root, created from `.env.example` by `scripts/setup`): the API keys,
  plus optional one-off overrides.

## How values are resolved

Highest precedence first:

1. `ATLAS_<FIELD_IN_UPPERCASE>` in the environment or `.env` (for example `ATLAS_PORT=8001`).
2. The legacy name `ATLAS_DB_PATH` (for `database_path` only).
3. The short aliases `NOTES_PROVIDER`, `NOTES_MODEL`, `NOTES_PROMPT_NAME`, `GEMINI_MODEL`,
   `GEMINI_INPUT_TOKEN_LIMIT`, `OPENROUTER_MODEL`, `OPENROUTER_BASE_URL`,
   `OPENROUTER_INPUT_TOKEN_LIMIT` (`ENV_ALIASES`).
4. `src/configs/config.yaml`.
5. The dataclass default.

`.env` is loaded without overriding variables already set in the shell. An empty value
(`KEY=`) counts as unset. Booleans from the environment accept `1/true/yes/on` and
`0/false/no/off`.

**Validation at startup** (`load_settings`, `AtlasSettings.problems`): an unknown key in
`config.yaml`, a value of the wrong type (YAML `port: "8000"` is text, not a number), an
environment value that cannot be converted, an out-of-range value or a missing prompt file
stops startup. `python -m backend` prints every problem and exits with status 2:

```
Atlas cannot start: invalid configuration (config.yaml, environment/.env).
  - port: must be between 1 and 65535 (got 70000)
  - notes_prompt_name: prompt file 'missing.yaml' not found in 'src/prompts'
Fix the values above in src/configs/config.yaml (settings) or .env (keys and ATLAS_* overrides), then start again.
```

Relative paths resolve against the repository root, not the working directory. The one
exception is `transcript_output_folder`, which the transcript writer resolves against the
working directory; the scripts always run from the repository root, so the result is the same.

Settings are read at startup; restart Atlas after changing `config.yaml` or an override.
API keys are read from the environment when first needed. Until the YouTube provider has been
built, every ingest re-reads settings (and `.env`), so adding `YOUTUBE_API_KEY` works without a
restart; for the other keys, restart to be sure.

## Settings

Every row is an `AtlasSettings` field and a key in `config.yaml`; the default shown is the same in
both. "Env" lists the override names in precedence order.

### HTTP server

| Setting | Default | Effect | Validation | Env |
|---|---|---|---|---|
| `host` | `"127.0.0.1"` | Interface `python -m backend` binds to. Keep it local: Atlas has no authentication. (The Docker image passes `--host 0.0.0.0` to uvicorn and publishes only `127.0.0.1:8000`.) | not empty; must resolve | `ATLAS_HOST` |
| `port` | `8000` | Port for the API, worker and built UI. Checked before start: a taken port prints one line and exits 1. `scripts/dev` passes the configured host/port to the Vite dev proxy as `ATLAS_API_URL` (default `http://127.0.0.1:8000` when Vite is started by hand). | 1 to 65535 | `ATLAS_PORT` |
| `cors_origins` | `""` | Comma-separated origins allowed to call the API cross-origin. Empty disables CORS (single-app mode is same-origin; the dev server proxies `/api`). | each must start with `http://` or `https://`; `*` rejected | `ATLAS_CORS_ORIGINS` |

### Storage

| Setting | Default | Effect | Validation | Env |
|---|---|---|---|---|
| `database_path` | `"data/atlas.sqlite3"` | The SQLite file holding all state. Created and migrated on startup. | not empty | `ATLAS_DATABASE_PATH`, `ATLAS_DB_PATH` |
| `transcript_output_folder` | `"transcripts"` | Where raw subtitle files are kept (one per fetch); transcript cleanup only deletes files inside it. | not empty | `ATLAS_TRANSCRIPT_OUTPUT_FOLDER` |

### Secrets (names, not values)

These hold the **name** of the environment variable that contains the key.

| Setting | Default | Effect | Validation | Env |
|---|---|---|---|---|
| `youtube_api_key_env` | `"YOUTUBE_API_KEY"` | Variable holding the YouTube Data API key. | not empty | `ATLAS_YOUTUBE_API_KEY_ENV` |
| `gemini_api_key_env` | `"GEMINI_API_KEY"` | Variable holding the Gemini key. | not empty | `ATLAS_GEMINI_API_KEY_ENV` |
| `openrouter_api_key_env` | `"OPENROUTER_API_KEY"` | Variable holding the OpenRouter key. | not empty | `ATLAS_OPENROUTER_API_KEY_ENV` |

### Google Drive

| Setting | Default | Effect | Validation | Env |
|---|---|---|---|---|
| `google_creds_path` | `"credentials.json"` | OAuth client file; the Settings upload writes here. | not empty | `ATLAS_GOOGLE_CREDS_PATH` |
| `google_token_path` | `"token.json"` | Token written by the consent flow and rewritten on refresh. | not empty | `ATLAS_GOOGLE_TOKEN_PATH` |
| `drive_max_retries` | `3` | Retries per Drive/Docs call for 429, 5xx, 403 rate-limit reasons, network errors and retryable token refreshes (so up to 4 attempts). | >= 0 | `ATLAS_DRIVE_MAX_RETRIES` |
| `drive_backoff_base_seconds` | `1.0` | Base delay of those retries (`min(cap, base * 2^(n-1))` plus up to 20% jitter). | >= 0 | `ATLAS_DRIVE_BACKOFF_BASE_SECONDS` |
| `drive_backoff_cap_seconds` | `10.0` | Cap of those retries. | >= `drive_backoff_base_seconds` | `ATLAS_DRIVE_BACKOFF_CAP_SECONDS` |

### Transcripts

| Setting | Default | Effect | Validation | Env |
|---|---|---|---|---|
| `transcript_language` | `"en"` | Preferred subtitle language for yt-dlp (manual or automatic). If neither it nor English exists, any available track is used and its language recorded; no track at all is `no_usable_language`. | not empty | `ATLAS_TRANSCRIPT_LANGUAGE` |

### Notes generation

| Setting | Default | Effect | Validation | Env |
|---|---|---|---|---|
| `notes_provider` | `"gemini"` | Which adapter `llm/factory.py` builds. | `gemini` or `openrouter` | `ATLAS_NOTES_PROVIDER`, `NOTES_PROVIDER` |
| `notes_model` | `""` | Model for whichever provider is active. Empty uses `gemini_model` / `openrouter_model` / the OpenRouter default. | none | `ATLAS_NOTES_MODEL`, `NOTES_MODEL` |
| `gemini_model` | `"gemini-2.5-flash"` | Gemini model when `notes_model` is empty. | none | `ATLAS_GEMINI_MODEL`, `GEMINI_MODEL` |
| `gemini_input_token_limit` | `1000000` | Pre-flight limit: a transcript estimated (characters / 4) above this fails as permanent `transcript_too_large` without calling Gemini. | >= 1 | `ATLAS_GEMINI_INPUT_TOKEN_LIMIT`, `GEMINI_INPUT_TOKEN_LIMIT` |
| `openrouter_model` | `""` | OpenRouter model when `notes_model` is empty; empty means `openai/gpt-5-mini`. | none | `ATLAS_OPENROUTER_MODEL`, `OPENROUTER_MODEL` |
| `openrouter_base_url` | `"https://openrouter.ai/api/v1"` | OpenAI-compatible endpoint for the OpenRouter adapter. | none | `ATLAS_OPENROUTER_BASE_URL`, `OPENROUTER_BASE_URL` |
| `openrouter_input_token_limit` | `128000` | Same pre-flight limit for OpenRouter; raise it when you pin a model with a bigger context. | >= 1 | `ATLAS_OPENROUTER_INPUT_TOKEN_LIMIT`, `OPENROUTER_INPUT_TOKEN_LIMIT` |
| `notes_prompt_name` | `"notes_v1_1.yaml"` | Prompt file inside `prompts_base_dir`. The file's own `version` key (e.g. `notes_v1.1`) is stored on each summary. The file must contain `{{Title}}` and `{{Transcript}}`. | not empty; file must exist | `ATLAS_NOTES_PROMPT_NAME`, `NOTES_PROMPT_NAME` |
| `notes_request_timeout_seconds` | `300.0` | Timeout of one LLM call. A timeout is a transient `network_error`, retried with backoff. | > 0 | `ATLAS_NOTES_REQUEST_TIMEOUT_SECONDS` |
| `prompts_base_dir` | `"src/prompts"` | Folder holding prompt files (`notes_v1.yaml`, `notes_v1_1.yaml`). | not empty | `ATLAS_PROMPTS_BASE_DIR` |

### Pipeline concurrency and retries

| Setting | Default | Effect | Validation | Env |
|---|---|---|---|---|
| `max_concurrent_videos` | `2` | Videos processed at the same time by the worker. Lower it if YouTube rate-limits yt-dlp. | >= 1 | `ATLAS_MAX_CONCURRENT_VIDEOS` |
| `max_auto_retries` | `4` | Automatic retries of a transient failure per phase (transcript, then notes); each phase makes at most `1 + max_auto_retries` attempts, then waits for a manual Retry. | >= 0 | `ATLAS_MAX_AUTO_RETRIES` |
| `backoff_base_seconds` | `5.0` | Base of the retry delay `min(cap, base * 2^(n-1))` plus up to 20% jitter, stored as `next_retry_at`. | >= 0 (0 = retry immediately) | `ATLAS_BACKOFF_BASE_SECONDS` |
| `backoff_cap_seconds` | `300.0` | Cap of that delay. | >= `backoff_base_seconds` | `ATLAS_BACKOFF_CAP_SECONDS` |
| `worker_poll_interval_seconds` | `2.0` | How often the idle worker looks for due retries and queued videos (it is also woken by new work). | > 0 | `ATLAS_WORKER_POLL_INTERVAL_SECONDS` |
| `worker_shutdown_timeout_seconds` | `5.0` | Grace period for running jobs on shutdown; the rest are cancelled and recovered on the next start. | >= 0 | `ATLAS_WORKER_SHUTDOWN_TIMEOUT_SECONDS` |

### Logging

| Setting | Default | Effect | Validation | Env |
|---|---|---|---|---|
| `log_level` | `"INFO"` | Root log level. | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (any case) | `ATLAS_LOG_LEVEL` |
| `log_format` | `"%(asctime)s %(levelname)s %(name)s: %(message)s"` | Line format; `extra` fields are appended as `key=value`, and the whole line is redacted. | none | `ATLAS_LOG_FORMAT` |
| `log_file_logging` | `true` | Also write a rotating file (5 MB, 3 backups). With `--reload` only the server child writes the file. | boolean | `ATLAS_LOG_FILE_LOGGING` |
| `log_file` | `"logs/atlas.log"` | Path of that file. | not empty | `ATLAS_LOG_FILE` |

Every field above is read by the code (checked with a grep of `src/` and `backend/`); there are
no unused keys in `config.yaml`, and no `AtlasSettings` field is missing from it.

## Secrets (`.env`)

From `.env.example`. None is needed to start Atlas; `GET /api/diagnostics` reports which are
missing, and each feature fails with a message naming the variable.

| Variable | Needed for | Without it |
|---|---|---|
| `YOUTUBE_API_KEY` | Listing a playlist (YouTube Data API v3, enabled in Google Cloud) | `POST /api/playlists` answers 503 `CONFIG_MISSING` |
| `GEMINI_API_KEY` | Notes when `notes_provider` is `gemini` (default); a Google AI Studio key | Each video fails with a permanent `config_error`, no retries |
| `OPENROUTER_API_KEY` | Notes when `notes_provider` is `openrouter` | Same as above |

Google Drive uses no key in `.env`: upload the OAuth client JSON on the Settings screen (or save it
at `google_creds_path`) and complete the consent once; the result is `token.json`. Both files are
gitignored, as are `.env` and `secrets/`.

`docker-compose.yml` overrides `ATLAS_DATABASE_PATH`, `ATLAS_TRANSCRIPT_OUTPUT_FOLDER`,
`ATLAS_GOOGLE_CREDS_PATH` and `ATLAS_GOOGLE_TOKEN_PATH` with paths inside the container.

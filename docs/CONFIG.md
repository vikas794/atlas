# Atlas configuration

Three places:

- **`src/configs/config.yaml`**: every setting that is not a secret. A flat mapping whose keys are
  exactly the fields of `AtlasSettings` (`src/config/settings.py`).
- **The Settings page** (`/settings`, "Application configuration"): edits are saved in the
  database (table `app_settings`, migration `0005`) and layered on top of `config.yaml`.
- **`.env`** (repository root, created from `.env.example` by `scripts/setup`): the API keys,
  plus optional one-off overrides.

## How values are resolved

Highest precedence first:

1. `ATLAS_<FIELD_IN_UPPERCASE>` in the environment or `.env` (for example `ATLAS_PORT=8001`).
2. The legacy name `ATLAS_DB_PATH` (for `database_path` only).
3. The short aliases `NOTES_MODELS`, `NOTES_PROVIDER`, `NOTES_MODEL`, `NOTES_PROMPT_NAME`, `GEMINI_MODEL`,
   `GEMINI_INPUT_TOKEN_LIMIT`, `OPENROUTER_MODEL`, `OPENROUTER_BASE_URL`,
   `OPENROUTER_INPUT_TOKEN_LIMIT` (`ENV_ALIASES`).
4. Settings saved from the Settings page (`app_settings` table).
5. `src/configs/config.yaml`.
6. The dataclass default.

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

Settings are read at startup; restart Atlas after changing `config.yaml` or an environment
override. Changes saved from the Settings page are the exception, see below.
API keys are read from the environment when first needed. Until the YouTube provider has been
built, every ingest re-reads settings (and `.env`), so adding `YOUTUBE_API_KEY` works without a
restart; for the other keys, restart to be sure.

## Editing settings in the UI

The Settings page lists every non-secret setting in groups (pipeline, retries and backoff,
timeouts and pacing, Google Drive, notes generation, transcripts, server and logging). Not
editable there: the `*_api_key_env` names and `google_creds_path` / `google_token_path` (the
Drive panel owns those), `database_path` (it locates the saved settings, so set it in
`config.yaml` or the environment) and `notes_models` (edit it in `config.yaml`).

- **Choosing the AI model.** Under "Notes generation", pick the AI provider, then the model.
  Gemini offers a fixed list; FreeLLMAPI's list is fetched live (`GET
  /api/settings/models?provider=freellmapi`, authenticated with `FREELLMAPI_API_KEY`, from
  `freellmapi_models_url` or `freellmapi_base_url`) into a searchable field, with a Refresh button.
  Saving applies it to new work at once. `notes_models`, when set in `config.yaml`, still wins.
- **Serial or parallel.** "Run mode" is `max_concurrent_videos`: Serial stores `1`, Parallel
  stores 2 or more. Transcript fetches are one at a time and paced either way.
- **Saving.** `PUT /api/settings` validates the whole set with `AtlasSettings.problems()` first;
  nothing is written unless every value is valid (422 `VALIDATION_ERROR`, `details.fields` names
  each bad setting). A value equal to `config.yaml`/the default removes its saved row. "Reset to
  defaults" (`DELETE /api/settings`) removes every saved row.
- **Live or restart.** Concurrency, poll and shutdown times, retries and backoff (including
  rate-limit), the LLM request timeout, transcript pacing, Drive retries and the notes
  provider/model settings apply to the running app on save. Running jobs finish with the values
  they started with; lowering the concurrency only stops new work being started. Server, logging
  and path settings are marked "Needs restart": they are saved, and the page shows a restart
  banner until the process has been restarted.
- **Environment variables win.** A setting that an `ATLAS_*` variable (or alias) decides is shown
  locked, and saving it is rejected. Unset the variable first.
- **The database is the store.** Deleting `data/atlas.sqlite3` also deletes saved settings. The
  endpoint is unauthenticated like the rest of Atlas; keep `host` at `127.0.0.1`.

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
| `transcript_min_interval_seconds` | `4.0` | YouTube transcript fetches run one at a time (whatever `max_concurrent_videos` is), at least this far apart (plus up to 25% jitter); also yt-dlp's `sleep_interval_subtitles`. `0` turns pacing off. | >= 0 | `ATLAS_TRANSCRIPT_MIN_INTERVAL_SECONDS` |
| `ytdlp_cookies_file` | `""` | Optional Netscape `cookies.txt` for yt-dlp; logged-in requests are throttled far less. Use a throwaway Google account. Wins over the browser setting. Relative paths resolve against the repo root; `cookies.txt` at the root is gitignored. Prefer setting it via `ATLAS_YTDLP_COOKIES_FILE` in `.env`. Never commit this file. | path or empty | `ATLAS_YTDLP_COOKIES_FILE` |
| `ytdlp_cookies_from_browser` | `""` | Optional browser name (`chrome`, `firefox`, `edge`, ...) to read cookies from. | name or empty | `ATLAS_YTDLP_COOKIES_FROM_BROWSER` |

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
| `transcript_language` | `"en"` | Preferred subtitle language for yt-dlp (manual or automatic). Atlas asks for the original auto-caption track (`<lang>-orig`) first, then the plain `<lang>` track (YouTube often throttles the plain one). If neither it nor English exists, any available track is used and its language recorded; no track at all is `no_usable_language`. | not empty | `ATLAS_TRANSCRIPT_LANGUAGE` |

### Notes generation

| Setting | Default | Effect | Validation | Env |
|---|---|---|---|---|
| `notes_models` | `[]` | Ordered list of LiteLLM models, tried first to last (later ones are fallbacks). Each entry: `model` (`<provider>/<model>`, required), `api_key_env`, `api_base`, `input_token_limit`. See [Choosing models](#choosing-models-litellm). Empty falls back to the single-model shorthand below. | each entry needs `model` like `gemini/gemini-3.5-flash`; no other keys | `ATLAS_NOTES_MODELS`, `NOTES_MODELS` (a JSON list) |
| `notes_provider` | `"gemini"` | Legacy single-model shorthand, used only when `notes_models` is empty. | `gemini`, `openrouter` or `freellmapi` | `ATLAS_NOTES_PROVIDER`, `NOTES_PROVIDER` |
| `notes_model` | `""` | Legacy shorthand: model for the active `notes_provider`. Advanced override; leave empty to use the model picked in the UI (`gemini_model` / `freellmapi_model` / `openrouter_model`). | none | `ATLAS_NOTES_MODEL`, `NOTES_MODEL` |
| `gemini_model` | `"gemini-3.5-flash"` | Gemini model when `notes_model` is empty. The UI dropdown offers `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3-flash-preview`, `gemini-flash-latest`, `gemini-3.1-pro-preview`. | none | `ATLAS_GEMINI_MODEL`, `GEMINI_MODEL` |
| `gemini_input_token_limit` | `1000000` | Pre-flight limit: a transcript estimated (characters / 4) above this fails as permanent `transcript_too_large` without calling Gemini. | >= 1 | `ATLAS_GEMINI_INPUT_TOKEN_LIMIT`, `GEMINI_INPUT_TOKEN_LIMIT` |
| `openrouter_model` | `""` | OpenRouter model when `notes_model` is empty; empty means `openai/gpt-5-mini`. | none | `ATLAS_OPENROUTER_MODEL`, `OPENROUTER_MODEL` |
| `openrouter_base_url` | `"https://openrouter.ai/api/v1"` | OpenAI-compatible endpoint for the OpenRouter adapter. | none | `ATLAS_OPENROUTER_BASE_URL`, `OPENROUTER_BASE_URL` |
| `openrouter_input_token_limit` | `128000` | Same pre-flight limit for OpenRouter; raise it when you pin a model with a bigger context. | >= 1 | `ATLAS_OPENROUTER_INPUT_TOKEN_LIMIT`, `OPENROUTER_INPUT_TOKEN_LIMIT` |
| `freellmapi_model` | `""` | FreeLLMAPI model id (sent as `openai/<id>`) when `notes_provider` is `freellmapi` and `notes_model` is empty. Required for that provider. | none | `ATLAS_FREELLMAPI_MODEL`, `FREELLMAPI_MODEL` |
| `freellmapi_base_url` | `"http://localhost:3001/v1"` | OpenAI-compatible endpoint notes are sent to. | none | `ATLAS_FREELLMAPI_BASE_URL`, `FREELLMAPI_BASE_URL` |
| `freellmapi_models_url` | `""` | Base URL the UI fetches `/models` from (for example a tunnel to the same gateway). Empty uses `freellmapi_base_url`. | none | `ATLAS_FREELLMAPI_MODELS_URL`, `FREELLMAPI_MODELS_URL` |
| `freellmapi_input_token_limit` | `128000` | Pre-flight limit for FreeLLMAPI models. | >= 1 | `ATLAS_FREELLMAPI_INPUT_TOKEN_LIMIT`, `FREELLMAPI_INPUT_TOKEN_LIMIT` |
| `notes_prompt_name` | `"notes_v1_2.yaml"` | Prompt file inside `prompts_base_dir`. The file's own `version` key (e.g. `notes_v1.2`) is stored on each summary (`custom` for a one-off regenerate prompt typed in the review screen, which never changes this file). The file must contain `{{Title}}` and `{{Transcript}}`. `notes_v1_2` makes the model start the notes with a short `Summary` section, which the Drive index shows next to each video; older prompt files still work (the index then uses the first sentences of the Notes section). | not empty; file must exist | `ATLAS_NOTES_PROMPT_NAME`, `NOTES_PROMPT_NAME` |
| `notes_request_timeout_seconds` | `300.0` | Timeout of one LLM call. A timeout is a transient `network_error`, retried with backoff. | > 0 | `ATLAS_NOTES_REQUEST_TIMEOUT_SECONDS` |
| `prompts_base_dir` | `"src/prompts"` | Folder holding prompt files (`notes_v1.yaml`, `notes_v1_1.yaml`, `notes_v1_2.yaml`). | not empty | `ATLAS_PROMPTS_BASE_DIR` |

### Pipeline concurrency and retries

| Setting | Default | Effect | Validation | Env |
|---|---|---|---|---|
| `max_concurrent_videos` | `2` | Videos processed at the same time by the worker. Lower it if YouTube rate-limits yt-dlp. | >= 1 | `ATLAS_MAX_CONCURRENT_VIDEOS` |
| `max_auto_retries` | `4` | Automatic retries of a transient failure per phase (transcript, then notes); each phase makes at most `1 + max_auto_retries` attempts, then waits for a manual Retry. | >= 0 | `ATLAS_MAX_AUTO_RETRIES` |
| `backoff_base_seconds` | `5.0` | Base of the retry delay `min(cap, base * 2^(n-1))` plus up to 20% jitter, stored as `next_retry_at`. | >= 0 (0 = retry immediately) | `ATLAS_BACKOFF_BASE_SECONDS` |
| `backoff_cap_seconds` | `300.0` | Cap of that delay. | >= `backoff_base_seconds` | `ATLAS_BACKOFF_CAP_SECONDS` |
| `rate_limit_max_auto_retries` | `6` | Automatic retries when the *YouTube transcript fetch* is rate-limited (replaces `max_auto_retries` for that case only; an LLM 429 keeps the regular policy). | >= 0 | `ATLAS_RATE_LIMIT_MAX_AUTO_RETRIES` |
| `rate_limit_backoff_base_seconds` | `60.0` | First delay for a throttled transcript fetch (doubles per attempt). | >= 0 | `ATLAS_RATE_LIMIT_BACKOFF_BASE_SECONDS` |
| `rate_limit_backoff_cap_seconds` | `900.0` | Cap of that delay. | >= `rate_limit_backoff_base_seconds` | `ATLAS_RATE_LIMIT_BACKOFF_CAP_SECONDS` |
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
| `GEMINI_API_KEY` | Notes with a `gemini/...` model (the default); a Google AI Studio key | That model is skipped; with no other model, each video fails with a permanent `config_error`, no retries |
| `OPENROUTER_API_KEY` | Notes with an `openrouter/...` model | Same as above |
| `FREELLMAPI_API_KEY` | Notes with the `freellmapi` provider, and listing its models in Settings | `GET /api/settings/models?provider=freellmapi` answers 400 `CONFIG_MISSING`; notes fail as above |
| `<PROVIDER>_API_KEY` / the entry's `api_key_env` | Notes with any other `notes_models` entry | Same as above |

### Choosing models (LiteLLM)

Every LLM call goes through [LiteLLM](https://docs.litellm.ai/docs/providers), so a provider is a
config entry, not code. `model` is LiteLLM's `<provider>/<model>` string. Example in
`config.yaml`:

```yaml
notes_models:
  - model: "gemini/gemini-3.5-flash"          # primary; key from GEMINI_API_KEY
    input_token_limit: 1000000
  - model: "openrouter/openai/gpt-5-mini"     # fallback; key from OPENROUTER_API_KEY
  - model: "openai/gpt-5-mini"                # any OpenAI-compatible gateway, e.g. FreeLLMAPI
    api_base: "http://localhost:3001/v1"
    api_key_env: "FREELLMAPI_API_KEY"
```

- `api_key_env` defaults to `<PROVIDER>_API_KEY` (`GEMINI_API_KEY`, `OPENROUTER_API_KEY`,
  `OPENAI_API_KEY`, `GROQ_API_KEY` ...). It may hold several keys (see below).
- `input_token_limit` defaults to 128000 (a wrong guess refuses early instead of paying for a
  rejected call).
- Models are tried in order. A model is skipped when its key is missing or the transcript is over
  its `input_token_limit`; the next one takes over when every key of a model is rate-limited or
  rejected, or on a 5xx / network error. A bad *answer* (empty, cut off, missing sections) is not
  a reason to fall back: it is a transient failure retried by the pipeline. If every model is
  turned away the transient error wins over a permanent one, so the pipeline still retries.
- `Summary.provider` and the usage ledger record the model that actually answered.
- Via env: `NOTES_MODELS='[{"model": "gemini/gemini-3.5-flash"}]'` (JSON).
- With `notes_models` empty, `notes_provider` / `notes_model` / `gemini_*` / `openrouter_*` build a
  single model exactly as before, so an older `config.yaml` keeps working.

### Multiple keys per service

Each of the three variables accepts several keys, so one key's rate limit or quota no longer stalls
Atlas. Use either form (they can be mixed; duplicates are ignored):

```
GEMINI_API_KEY=keyA,keyB,keyC          # comma, semicolon or whitespace separated
GEMINI_API_KEY_2=keyD                  # numbered extras: _2, _3, ...
```

Keys are used round-robin. A key that is rate-limited (HTTP 429) is parked for 60 s, one that is
out of YouTube quota or out of OpenRouter credit for an hour, and a rejected key is dropped until
`.env` changes; the call moves to the next key immediately. If every key is turned away the
failure is the normal transient `rate_limited` and the pipeline's usual backoff applies. A
failure that another key cannot fix (unknown model, private playlist, oversized transcript, 5xx)
is never rotated. Quotas are per Google Cloud / AI Studio *project*, so keys must come from
different projects to add capacity. `GET /api/diagnostics` shows the key count, never a value.
Implementation: `src/shared/api_keys.py`.

Google Drive uses no key in `.env`: upload the OAuth client JSON on the Settings screen (or save it
at `google_creds_path`) and complete the consent once; the result is `token.json`. Both files are
gitignored, as are `.env` and `secrets/`.

`docker-compose.yml` overrides `ATLAS_DATABASE_PATH`, `ATLAS_TRANSCRIPT_OUTPUT_FOLDER`,
`ATLAS_GOOGLE_CREDS_PATH` and `ATLAS_GOOGLE_TOKEN_PATH` with paths inside the container.

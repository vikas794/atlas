# Atlas configuration

Four places:

- **`src/configs/config.yaml`**: every setting that is not a secret. A flat mapping whose keys are
  exactly the fields of `AtlasSettings` (`src/config/settings.py`).
- **The Settings page** (`/settings`, "Application configuration"): edits are saved in the
  database (table `app_settings`, migration `0005`) and layered on top of `config.yaml`.
- **The Models and Keys pages**: the LLM providers, models, fallback order and API keys. They live
  in the database (the model registry, migration `0007`), not in `config.yaml` or `.env`. See
  [The LLM model registry](#the-llm-model-registry).
- **`.env`** (repository root, created from `.env.example` by `scripts/setup`): the YouTube key,
  optional one-off overrides, and LLM keys that are imported into the registry on first run.

## How values are resolved

Highest precedence first:

1. `ATLAS_<FIELD_IN_UPPERCASE>` in the environment or `.env` (for example `ATLAS_PORT=8001`).
2. The legacy name `ATLAS_DB_PATH` (for `database_path` only).
3. The short alias `NOTES_PROMPT_NAME` (`ENV_ALIASES`).
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
`YOUTUBE_API_KEY` is read from the environment when first needed. Until the YouTube provider has
been built, every ingest re-reads settings (and `.env`), so adding it works without a restart.
LLM keys are not read from the environment at run time: add and change them on the Keys page
(effective at once, no restart).

## Editing settings in the UI

The Settings page lists every non-secret setting in groups (pipeline, retries and backoff,
timeouts and pacing, Google Drive, notes generation, transcripts, server and logging). Not
editable there: `youtube_api_key_env`, `google_creds_path` / `google_token_path` (the
Drive panel owns those) and `database_path` (it locates the saved settings, so set it in
`config.yaml` or the environment).

- **Choosing the AI model.** Not a setting: open the **Models** page to add models, turn
  them on or off and drag them into fallback order, and the **Keys** page to manage API keys.
  Changes apply to new work at once. The Settings page links to both. The old provider and model
  settings (`notes_provider`, `notes_model`, `gemini_*`, `openrouter_*`, `freellmapi_*`,
  `nvidia_*`) no longer exist: saving one is rejected (422), and a row left in `app_settings` by an
  older version is ignored (it is only read once, by the [first-run import](#first-run-import)).
- **Serial or parallel.** "Run mode" is `max_concurrent_videos`: Serial stores `1`, Parallel
  stores 2 or more. Transcript fetches are one at a time and paced either way, and run ahead of the AI notes.
- **Saving.** `PUT /api/settings` validates the whole set with `AtlasSettings.problems()` first;
  nothing is written unless every value is valid (422 `VALIDATION_ERROR`, `details.fields` names
  each bad setting). A value equal to `config.yaml`/the default removes its saved row. "Reset to
  defaults" (`DELETE /api/settings`) removes every saved row.
- **Live or restart.** Concurrency, poll and shutdown times, retries and backoff (including
  rate-limit), the LLM request timeout, transcript pacing and Drive retries apply to the
  running app on save. Running jobs finish with the values
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

This holds the **name** of the environment variable that contains the YouTube key, which is read
from the environment at run time. LLM keys are not settings: they live in the registry.

| Setting | Default | Effect | Validation | Env |
|---|---|---|---|---|
| `youtube_api_key_env` | `"YOUTUBE_API_KEY"` | Variable holding the YouTube Data API key. | not empty | `ATLAS_YOUTUBE_API_KEY_ENV` |

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

Which models run, in which order, with which keys, is managed in the model registry (Models and
Keys pages), not here. The old `notes_models`, `notes_provider`, `notes_model`, `gemini_*`,
`openrouter_*`, `freellmapi_*`, `nvidia_*` and `*_api_key_env` settings (and their `NOTES_*`,
`GEMINI_MODEL`... environment names) were removed; an unknown key in `config.yaml` stops startup,
and old values in the environment or in `app_settings` are ignored (the saved ones are read once,
by the [first-run import](#first-run-import)). `notes_prompt_name`, `notes_request_timeout_seconds`
and `prompts_base_dir` are the notes settings that remain.

| Setting | Default | Effect | Validation | Env |
|---|---|---|---|---|
| `notes_prompt_name` | `"notes_v1_3.yaml"` | Prompt file inside `prompts_base_dir`. The file's own `version` key (e.g. `notes_v1.3`) is stored on each summary (`custom` for a one-off regenerate prompt typed in the review screen, which never changes this file). The file must contain `{{Title}}` and `{{Transcript}}`. `notes_v1_2` makes the model start the notes with a short `Summary` section, which the Drive index shows next to each video; older prompt files still work (the index then uses the first sentences of the Notes section). | not empty; file must exist | `ATLAS_NOTES_PROMPT_NAME`, `NOTES_PROMPT_NAME` |
| `notes_request_timeout_seconds` | `300.0` | Timeout of one LLM call. A timeout is a transient `network_error`, retried with backoff. | > 0 | `ATLAS_NOTES_REQUEST_TIMEOUT_SECONDS` |
| `prompts_base_dir` | `"src/prompts"` | Folder holding prompt files (`notes_v1.yaml`, `notes_v1_1.yaml`, `notes_v1_2.yaml`, `notes_v1_3.yaml`). | not empty | `ATLAS_PROMPTS_BASE_DIR` |

### Pipeline concurrency and retries

| Setting | Default | Effect | Validation | Env |
|---|---|---|---|---|
| `max_concurrent_videos` | `2` | Videos whose AI notes are generated at the same time. Transcripts download separately, one at a time and paced, without waiting for notes. | >= 1 | `ATLAS_MAX_CONCURRENT_VIDEOS` |
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
missing, and each feature fails with a message naming what to fix.

| Variable | Needed for | Without it |
|---|---|---|
| `YOUTUBE_API_KEY` | Listing a playlist (YouTube Data API v3, enabled in Google Cloud). Read from the environment at run time; accepts several keys (below). | `POST /api/playlists` answers 503 `CONFIG_MISSING` |
| `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `FREELLMAPI_API_KEY`, `OPENAI_API_KEY` | **First-run import only**: copied into the Keys page once (see [First-run import](#first-run-import)). Not read afterwards. | Add the key on the Keys page instead |

LLM keys live in the database and are managed on the Keys page. They are stored **unencrypted**:
`data/atlas.sqlite3` and the `.bak` copies migrations write beside it contain them in plain text.
Both are gitignored and dockerignored; do not share or commit them. The API only ever returns a
masked value (`abcd...wxyz`); the full key comes back only from the explicit reveal action.

### The LLM model registry

Modeled on FreeLLMAPI. Tables (migration `0007`): `llm_models`, `llm_api_keys`, `llm_fallback`
(the one global ordered chain), `llm_cooldowns`, `llm_rate_usage`, `llm_key_monthly_usage`,
`llm_meta`. Code: `src/infrastructure/llm/registry/` (`repo.py` storage, `service.py` policy,
`seed.py`, `health.py`, `providers.py`).

- **Models page** (`/models`): the fallback chain, top to bottom. Drag to reorder, switch a model
  on or off, edit limits, add a model (provider, model id, optional endpoint and limits), test it
  with a one-line ping. Edits are staged and applied with Save. Known providers: `gemini`,
  `openrouter`, `freellmapi` (OpenAI-compatible gateway, default `http://localhost:3001/v1`),
  `nvidia` (default timeout 3600 s) and `openai`; any other provider name is treated as a generic
  OpenAI-compatible endpoint (set its `api_base`).
- **Keys page** (`/keys`): keys grouped by provider, each with a label, masked value, status
  (`unknown`, `healthy`, `rate_limited`, `invalid`, `error`), an on/off switch, optional monthly
  request and token caps (0 or empty = unlimited), a cooldown badge, check, reveal and delete.
  Several keys per provider are used round-robin. Replacing a key's value resets its status and
  clears its cooldowns.
- **Order of attempts.** For each call Atlas walks the enabled models in chain order. For a model it
  tries the provider's usable keys one after another. A key is usable when it is enabled, its
  status is `healthy` or `unknown`, it is not cooling down for that model, the model's
  RPM/RPD/TPM/TPD limits (sliding 60 s and 24 h windows) have room, and the key is under its
  monthly caps. A key-scoped error moves on to the next key; anything else (unknown model, 5xx,
  network error) moves on to the next model. A transcript over a model's `input_token_limit`
  skips that model. A bad *answer* (empty, cut off, missing sections) is not a reason to fall back:
  it is a transient failure retried by the pipeline. When every model is turned away, a transient
  error wins over a permanent one, so the pipeline still retries. An empty chain fails the video
  with a permanent `config_error` ("No notes models are enabled").
- **Cooldowns.** HTTP 429 cools the model-and-key pair down on a ladder of 2 min, 10 min, 1 h,
  24 h (it climbs while the previous cooldown was set within 24 h; a success clears it), and a
  provider `Retry-After` is honoured as a floor. A rejected key (401/403) is marked `invalid`
  and cooled down for 5 min on every model; the third consecutive invalid verdict turns the key off
  ("Disabled after 3 failed checks"). A key out of credit (402) is parked for 24 h.
- **Health checks.** On demand only: Check one key or Check all on the Keys page. There is no
  background re-check, so idle keys cost no provider calls (expired cooldowns and old ledger rows
  are tidied once at startup). A check that gets 401/403 marks the key `invalid`; a 429 marks it
  `rate_limited` and parks it for an hour (it is used again by itself afterwards); any other answer
  or a network error leaves the status unchanged. An `invalid` key is not used until you press
  Check and it passes, re-enable it, or replace its value. A successful real call marks a key
  `healthy`.
- **Usage and quota.** Requests and tokens per key and model are written to `llm_rate_usage` and
  rolled into `llm_key_monthly_usage` (UTC month); `GET /api/llm/fallback/rate-limit-usage` and
  `/token-usage` back the badges on the Models page.
- **Per-key analytics.** Every provider call through a registry key (notes generation and the
  model test ping) is also logged to `llm_attempts` (migration `0009`), failures included, with
  latency, tokens and estimated cost. `GET /api/usage/keys` and the "API key analytics" section of
  Settings read it: success and errors per key, cost per model, and error categories. It is
  separate from `usage_ledger`, so existing `/api/usage` totals are unchanged. Calls made before
  the migration are not in it. Cost is the LiteLLM price estimate, so free-tier models show $0.
- `Summary.provider` / `Summary.model` and the usage ledger record the model that actually answered.
- API: everything under `/api/llm` (models, keys, fallback chain, health, usage); see
  `docs/ARCHITECTURE.md` section 9.

#### First-run import

The first start after migration `0007` seeds an empty registry once (marked in `llm_meta`):

- **Models:** the model your previous version had selected, read from the settings it saved in
  `app_settings` (`notes_provider`, then `notes_model` or that provider's `*_model`, with its
  `*_base_url`, `*_input_token_limit` and `nvidia_request_timeout_seconds`). It becomes the first,
  enabled model of the chain; with nothing saved the default is Gemini `gemini-3.5-flash`. Every
  other provider that has a key (or a saved model) is added **disabled** after it, with its saved
  model or its default (`gemini-3.5-flash`, `openai/gpt-5-mini`, `deepseek-ai/deepseek-v4.1-flash`).
  FreeLLMAPI has no default model: with no saved `freellmapi_model` it gets none.
- **Keys:** every key found in `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `FREELLMAPI_API_KEY`,
  `NVIDIA_API_KEY` and `OPENAI_API_KEY`, including comma-separated lists and numbered extras
  (`GEMINI_API_KEY_2`, ...), labelled "Imported from `<VARIABLE>` #n", duplicates dropped.

The saved settings are read straight from the `app_settings` table; no setting for them exists any
more, and `NOTES_PROVIDER`, `GEMINI_MODEL` and similar environment names are not read at all. The
import never runs again, so deleting a model or key on the pages is permanent. Deleting
`data/atlas.sqlite3` resets the registry and imports again. Do not hand-edit these tables.

### Multiple YouTube keys

`YOUTUBE_API_KEY` accepts several keys, so one key's quota no longer stalls ingest. Use either form
(they can be mixed; duplicates are ignored):

```
YOUTUBE_API_KEY=keyA,keyB,keyC          # comma, semicolon or whitespace separated
YOUTUBE_API_KEY_2=keyD                  # numbered extras: _2, _3, ...
```

Keys are used round-robin; a key that is out of quota or rejected is skipped (`KeyPool`,
`src/shared/api_keys.py`) and the call moves to the next one. Quotas are per Google Cloud
*project*, so keys must come from different projects to add capacity. `GET /api/diagnostics`
shows the key count, never a value.

Google Drive uses no key in `.env`: upload the OAuth client JSON on the Settings screen (or save it
at `google_creds_path`) and complete the consent once; the result is `token.json`. Both files are
gitignored, as are `.env` and `secrets/`.

`docker-compose.yml` overrides `ATLAS_DATABASE_PATH`, `ATLAS_TRANSCRIPT_OUTPUT_FOLDER`,
`ATLAS_GOOGLE_CREDS_PATH` and `ATLAS_GOOGLE_TOKEN_PATH` with paths inside the container.

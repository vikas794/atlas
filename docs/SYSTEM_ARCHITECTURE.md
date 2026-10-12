# Atlas - System Architecture

> Describes the code as it is now. The product spec is [`Atlas_PRD.md`](../Atlas_PRD.md);
> [`ARCHITECTURE.md`](ARCHITECTURE.md) and `IMPLEMENTATION_PLAN.md` are the original (search-run era)
> design notes and are historical.

## 1. What Atlas is

A single-user, local-first tool that turns a YouTube playlist into a Google Drive "course": a
folder with one numbered Google Doc of AI-written notes per video plus an index Doc. Videos move
through a persisted pipeline (queued -> fetching transcript -> generating notes -> pending review ->
approved -> published) driven by a background worker; the user reviews, edits and approves in a web UI.

```
Browser (React SPA) --REST/polling--> FastAPI --use cases--> ports --> adapters
                                         |                              |- YouTube Data API   (playlist listing)
                                         |                              |- yt-dlp             (transcripts)
                                         |                              |- Gemini / OpenRouter (notes)
                                         |                              |- Google Drive + Docs (publishing)
                                         |                              `- SQLite             (all state)
                                         `-- PlaylistWorker (same process, started by the app lifespan)
```

## 2. Layers (`src/`)

| Layer | Path | Responsibility |
|---|---|---|
| Domain | `src/domain/` | Models (`Playlist`, `Video`, `Transcript`, `Summary`, `Stage`, failure taxonomy), the stage machine, sync diffing, failure classification, Doc naming, Markdown-to-Docs conversion, and the **ports**: `PlaylistRepositoryPort`, `PlaylistProviderPort`, `TranscriptProviderPort`, `NotesGeneratorPort`, `DrivePublisherPort`, `UsageLedgerPort` |
| Application | `src/application/` | Use cases (`IngestPlaylist`, `ProcessVideo`, user actions, `PublishPlaylist`) and `PlaylistWorker`. Depends only on ports. |
| Infrastructure | `src/infrastructure/` | `youtube/playlist.py`, `transcript/ytdlp`, `llm/` (Gemini, OpenRouter, `factory.py`), `storage/` (SQLite repository, migrations, usage ledger), `google/` (Drive exporter + publisher) |
| Transport | `src/transport/http/fastapi/` | Routers, schemas, error mapping, middleware, and `dependencies.py` - the composition root |
| Config / shared | `src/config/`, `src/configs/config.yaml`, `src/prompts/`, `src/shared/` | `AtlasSettings` (env + yaml), versioned prompt files, redaction / logging / file helpers |

Retry policy: **`ProcessVideoUseCase` is the single owner of retries.** Adapters make exactly one
provider call. A transient failure is stored with `next_retry_at`; the worker re-runs the video when
it is due, up to `max_auto_retries`. After that the failure stays `transient` with no schedule
(`retries_exhausted`) and only a manual retry runs it again.

## 3. Data model (SQLite, `data/atlas.sqlite3`)

Migrations are numbered and applied automatically at startup (`storage/migrations.py`); a populated
database is copied to `*.pre-<version>.bak` first.

- `playlists`: ids, title, Drive folder/index Doc ids, `status` (`ingested` | `publishing` |
  `published` | `publish_failed`), `last_publish_error`, `pad_width`, `index_digest`.
- `videos`: **one row per playlist position** (`UNIQUE(playlist_id, position)`, positions are
  **1-based**), `stage`, failure type/kind/reason, retry state, `drive_doc_id`, `removed_upstream`,
  `duplicate_of_video_id` (a repeated video is processed once and its row reuses the first
  summary), `run_token` (stale-run guard) and `regen_requested`.
- `transcripts` (soft-deletable: text and raw subtitle file are removed, the audit row stays),
  `summaries` (review/publish flags, `prompt_version` taken from the prompt file), `usage_ledger`.

## 4. Worker and flows

`PlaylistWorker` (`application/worker.py`) runs inside the API process (FastAPI lifespan):

- **Video jobs:** queued videos (including `transcript_ready` ones and regenerate requests) and
  transiently failed videos whose `next_retry_at` is due, with bounded concurrency. Writes carry the
  video's `run_token`, so a reprocess/delete during a run discards the stale result.
- **Publish jobs:** `POST /api/playlists/{id}/publish` validates, marks the playlist `publishing`
  and returns 202; the worker runs it and records `published` / `publish_failed`.
- **Crash recovery on start:** in-flight stages (`fetching_transcript`, `transcript_ready`,
  `generating_notes`) are re-queued (a stored transcript is reused); playlists left `publishing` are
  re-queued (publishing is idempotent).
- **Shutdown:** running jobs get a short grace period, the rest are cancelled and recovered on the
  next start.

Publishing (`PublishPlaylistUseCase` + `GoogleDrivePublisher`): folder, then index Doc, then video
Docs in position order. The publisher reports each Drive object to a progress sink the moment it
exists, so the folder id, index Doc id and every Doc id are persisted immediately; creating a Doc and
formatting it are separate retried steps, so a failure never orphans or duplicates a Doc. Doc names are
`<position zero-padded to the width of the largest position> - <title>` and every Doc starts with that
text as a Heading 1.

## 5. HTTP surface

`/api/playlists` (POST ingest - 201 new / 200 re-sync; GET list/detail/videos; POST process,
reprocess, approve-all, publish (202); DELETE playlist, transcripts), `/api/videos/{id}` (GET,
summary GET/PUT, approve, retry, regenerate (202), DELETE transcript), `/api/drive/*` (status,
credentials upload, auth), `/api/usage`, `/api/health`. Errors use `{code, message, details}`:
404 `NOT_FOUND`, 409 `CONFLICT` / `INVALID_STATE` / `DRIVE_AUTH_REQUIRED`, 422 validation,
503 `CONFIG_MISSING`. The UI polls (no websockets).

## 6. Configuration, logging, deployment

- Settings: `ATLAS_<FIELD>` > documented names (`NOTES_PROVIDER`, `GEMINI_MODEL`, ...) > `config.yaml` >
  defaults; empty values count as unset. Keys come from `YOUTUBE_API_KEY`, `GEMINI_API_KEY`,
  `OPENROUTER_API_KEY`.
- Logging is configured at startup (`src/shared/logging_setup.py`): stderr (and optionally a rotating
  file), every `extra` rendered as `key=value` (pipeline events carry `playlist_id` and `video_id`), all
  output passed through the secret redactor.
- Docker: one container runs API + frontend + worker; `./data`, `./transcripts` and a writable
  `./secrets` are mounted; only `127.0.0.1:8000` is published. Google consent is done once on the host.

## 7. Known limits

- Single process, single SQLite file: no horizontal scaling (by design for v1).
- Between Drive returning a new Doc id and the database write that stores it there is a tiny window in
  which a crash would orphan one Doc; it is not closed by name lookups.
- A duplicate row whose first occurrence permanently fails stays queued (the UI shows `duplicate_of`).

# Atlas — System Architecture

> Describes the code **as it exists at commit `cc6af15`**. [`ARCHITECTURE.md`](ARCHITECTURE.md) is the original target design / migration plan; this document is the current-state reference.
>
> Interactive diagram: open [`atlas-architecture.html`](../.archify/architecture-atlas-overview/atlas-architecture.html) in a browser (generated with [Archify](https://github.com/tt-a1i/archify); source JSON in `.archify/architecture-atlas-overview/candidate.json`).

## 1. What Atlas is

An AI-assisted content-analysis platform for technical education. A learner searches YouTube, and Atlas pulls transcripts, summarizes each video with an LLM, compares the videos, and generates assignments. A separate flow turns a YouTube playlist into quizzes exported to Google Drive. A usage dashboard tracks token cost.

## 2. System overview

```
Learner ─HTTP─▶ React SPA ─REST/SSE─▶ FastAPI transport ─▶ Use cases ─▶ LLM adapters ─▶ OpenRouter / Gemini
                                            │                  ├──────▶ YouTube adapters ─▶ YouTube (Data API, yt-dlp)
                                            │                  ├──────▶ Drive exporter ─▶ Google Drive
                                            └─ reads ─▶ Storage adapters ◀─┘
                                                           ├─▶ SQLite (runs, cache, usage ledger)
                                                           └─▶ Artifact files (transcripts, summaries)
```

| Runtime unit | Where | Notes |
|---|---|---|
| React SPA | `frontend/` | Vite, TypeScript, Tailwind, TanStack Query, Framer Motion |
| API process | `backend/main.py` | One uvicorn/FastAPI app; also serves the built SPA when `frontend/dist` exists |
| SQLite | `data/atlas.sqlite3` | Single file; WAL mode, foreign keys, busy timeout via `src/infrastructure/storage/db.py` |
| Artifact files | `data/artifacts/` | Transcript / summary / assignment text per run + video |
| External APIs | network | OpenRouter (OpenAI-compatible), Gemini, YouTube Data API, YouTube (yt-dlp), Google Drive |

## 3. Layers (`src/`)

Clean/hexagonal layout; dependencies should point inward.

| Layer | Path | Responsibility |
|---|---|---|
| Transport | `src/transport/http/fastapi/` | Routers, Pydantic request/response schemas, error mapping, correlation-id + request-logging middleware, DI wiring (`dependencies.py`) |
| Application | `src/application/` | Six use cases (`search_pipeline`, `generate_transcripts`, `generate_summaries`, `generate_comparison`, `generate_assignments`, `generate_quiz`), DTOs, `ports/` (re-exports of domain interfaces), `artifact_readers/` (shape stored data for read endpoints) |
| Domain | `src/domain/` | Models (video, transcript, summary, comparison, assignment, quiz), port interfaces (`interfaces/`), pure services (`RunHashComputer`, `CacheKeyBuilder`, `ComparisonInferenceService`), exceptions (`DomainError`, `ProviderError`, `CacheError`, `StorageError`) |
| Infrastructure | `src/infrastructure/` | Adapters: `llm/openai`, `llm/gemini`, `youtube`, `transcript/ytdlp`, `google/drive`, `storage` (SQL repository, usage ledger, filesystem store, migrations), `cache/sql_cache` |
| Config / shared | `src/config/`, `src/configs/config.yaml`, `src/prompts/*.yaml`, `src/shared/` | `AtlasSettings` (env `ATLAS_*` overrides), prompt + model registries, text helpers |

Composition root: `src/transport/http/fastapi/dependencies.py` builds repositories, cache, ledger, artifact store and injects them into each use case via FastAPI `Depends`. Settings, prompt registry and model registry are `lru_cache` singletons.

## 4. HTTP surface

| Router | Endpoints |
|---|---|
| `runs` (`/api/runs`) | `GET ""`, `/latest`, `/{id}`, `/{id}/videos`, `/{id}/transcripts`, `/{id}/summaries`, `/{id}/comparison`, `/{id}/assignments` — read-only, served from stored runs |
| `pipeline` (`/api`) | `POST /pipeline/search`, `/runs/{id}/transcripts`, `/summaries`, `/comparison`, `/assignments` — each awaits the matching use case |
| `quiz` (`/api/quiz`) | `POST /playlist`, `POST /playlist/stream` (SSE progress: worker thread + queue), `GET /drive-status`, `POST /credentials`, `POST /auth` |
| `usage` | `GET /api/usage` — aggregate from the usage ledger |
| app | `GET /api/health`, `POST /api/dev/shutdown` (token-guarded, dev only), catch-all SPA fallback (never shadows `/api/*`) |

CORS origins come from `ATLAS_CORS_ORIGINS`.

## 5. Key flows

**Pipeline run** (each step is a separate call, driven from the dashboard):
1. `search` → YouTube Data API → videos stored on a new `run` (cache-aware via `prefer_cache`).
2. `transcripts` → yt-dlp fetches subtitles → text written to the artifact store, metadata/hash to SQLite.
3. `summaries` → OpenAI-compatible LLM (OpenRouter by default) per video, with retry policy.
4. `comparison` → rule-based `ComparisonInferenceService`, optionally enriched by `OpenAIInsightsProvider` (`use_ai_insights`).
5. `assignments` → LLM generates assignments from summaries.

**Quiz flow:** playlist URL → `YouTubePlaylistProvider` → transcripts → `GeminiQuizProvider` → `GoogleDriveExporter` (folder + docs). Progress streams to the UI over SSE.

**Read path:** `runs` routers → `get_run_repository` + `artifact_readers` → JSON for the UI. No LLM calls.

## 6. Persistence, caching and staleness

SQLite tables (`src/infrastructure/storage/migrations.py`): `runs`, `videos`, `transcripts`, `summaries`, `comparisons`, `assignments`, `generation_jobs`, `cache_entries`, `usage_ledger` (+ `schema_migrations`). Large text lives in files under `artifact_root`.

- **Cache keys:** `CacheKeyBuilder` (version `v2`) hashes `kind|params…` into a `CacheKey`; `SqlCacheAdapter` stores compressed payloads with a TTL (`cache_ttl_days`, default 30).
- **Staleness:** `RunHashComputer` produces stable state hashes of videos / transcripts / summaries so downstream steps can tell whether their inputs changed; `refresh=true` forces regeneration.
- **Cost tracking:** every LLM/provider call writes a `UsageRecord` to `usage_ledger`; `GET /api/usage` aggregates it.

## 7. Frontend (`frontend/src`)

- `lib/api.ts` — single fetch wrapper (`VITE_API_BASE_URL`, empty = same origin) plus SSE client for quiz streaming; `lib/types.ts` mirrors API schemas.
- `features/pipeline/` — run form, dashboard, per-artifact tabs, assignment progress hook.
- `features/quiz/` — playlist quiz panel, Google auth panel.
- `features/usage/` — usage dashboard.
- `components/ui`, `components/shared` — reusable primitives.

## 8. Deployment

Multi-stage `Dockerfile`: Node 20 builds the SPA, Python 3.13 + `uv` runs `uvicorn backend.main:app` on port 8000 and serves `frontend/dist`. `docker-compose.yml` publishes `8000:8000`, loads `.env`, and mounts `./data`, `./transcripts`, and read-only `credentials.json` / `token.json`.

## 9. Known gaps (observed)

- **Layering leaks:** use cases import concrete adapters (e.g. `YouTubeDataApiSearchProvider`, `YtDlpTranscriptProvider`, `OpenAISummarizerAdapter`, `GoogleDriveExporter`) instead of depending only on ports.
- `SqlRunRepository` (the port implementation) is a thin async wrapper over the legacy sync `RunRepository` in `storage/repository.py` (`sql.py:31`); the legacy class also backs `storage/cli.py`.
- Pipeline endpoints run work inside the request (no job queue); long runs rely on client timeouts. `generation_jobs` records job start/finish/fail per run, but no code was found that resumes interrupted jobs.
- Single-process SQLite: fine for one container, not for horizontal scaling.
- `credentials.json` / `token.json` live in the repo root (gitignored; mounted read-only into the container, not copied into the image).

## 10. Regenerating the diagram

```bash
node .claude/skills/archify/bin/archify.mjs finalize architecture \
  .archify/architecture-atlas-overview/candidate.json \
  .archify/architecture-atlas-overview/atlas-architecture.html \
  --repo-root . --quality showcase --json
```

Archify was installed with `npx skills add tt-a1i/archify --skill archify --agent claude-code --copy` (project-scoped, `.claude/skills/archify`). Ask Claude to "use archify" for workflow, sequence, data-flow or lifecycle diagrams.

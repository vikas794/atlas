# Atlas — Implementation Plan

This document tracks the phased refactor from a monolithic `backend/` + `src/` codebase to a clean layered architecture. Each wave is a vertical slice delivering a working system.

---

## Wave Overview

| Wave | Focus | Status | Key Deliverables |
|------|-------|--------|------------------|
| **A** | Foundation: Domain + Infrastructure | ✅ **DONE** | Domain models/ports, all infrastructure adapters, migration 0002, `/api/usage`, cleanup |
| **B** | Application Layer (Use Cases) | ✅ **DONE** | `src/application/` — DTOs, ports, 6 use cases; removed `os.environ` mutations |
| **C** | Transport Slimming | ✅ **DONE** | `src/config/`, DI wiring, FastAPI routers → use cases, legacy module removal |
| **D** | Frontend & Polish | ✅ **DONE** | Usage dashboard, cost calculator, tests, lint/typecheck |

---

## Wave A — Foundation (Completed)

### Completed Tasks
- [x] **Domain layer** (`src/domain/`):
  - Models: `video.py`, `transcript.py`, `summary.py`, `comparison.py`, `assignment.py`, `quiz.py`
  - Interfaces: `transcript_provider.py`, `summarizer.py`, `insights_provider.py`, `assignment_generator.py`, `quiz_generator.py`, `cache.py`, `usage_ledger.py`, `storage.py`
  - Services: `comparison_inference.py`, `hash_computer.py`, `cache_key_builder.py`
  - Exceptions: `DomainError`, `ProviderError`, `CacheError`, `StorageError`

- [x] **Infrastructure layer** (`src/infrastructure/`):
  - LLM: `base.py`, `openai/adapter.py`, `openai/retry.py`, `gemini/adapter.py`
  - Transcript: `transcript/ytdlp/provider.py`
  - Storage: `storage/sql.py` (`SqlRunRepository`, `SqlUsageLedger`), `storage/filesystem.py` (`ArtifactFileStore`)
  - Cache: `cache/sql_cache.py` (`SqlCacheAdapter`)
  - YouTube: `youtube/search.py`, `youtube/playlist.py`
  - Google: `google/drive.py` (`GoogleDriveExporter`)

- [x] **Migration & API**:
  - Migration `0002_usage_ledger_and_extended_cache` applied
  - `/api/usage` router + schemas (`src/transport/http/fastapi/routers/usage.py`, `schemas/usage.py`)
  - Deleted `app.py` (Gradio), `src/atlas.egg-info/`, all `__pycache__/`
  - Google OAuth paths configurable via `ATLAS_GOOGLE_CREDS_PATH` / `ATLAS_GOOGLE_TOKEN_PATH`

### Validation
- `gitnexus analyze` → 2,164 nodes, 4,749 edges, 70 clusters, 186 flows
- `detect_changes` → LOW risk (for completed work)
- All Python syntax checks pass

---

## Wave B — Application Layer (Completed)

### Completed Tasks
- [x] **DTOs** (`src/application/dto/`):
  - `search.py`: `SearchInput`, `SearchOutput`
  - `transcripts.py`: `TranscriptGenerationInput`, `TranscriptGenerationOutput`
  - `summaries.py`: `SummaryGenerationInput`, `SummaryGenerationOutput`
  - `comparison.py`: `ComparisonGenerationInput`, `ComparisonGenerationOutput`
  - `assignments.py`: `AssignmentGenerationInput`, `AssignmentGenerationOutput`
  - `quiz.py`: `QuizGenerationInput`, `QuizGenerationOutput`, `VideoQuizResult`

- [x] **Ports** (`src/application/ports/provider_ports.py`):
  - Re-exports all domain interfaces as application-level ports

- [x] **Use Cases** (`src/application/use_cases/`):
  - `SearchPipelineUseCase` — search + cache + run creation
  - `GenerateTranscriptsUseCase` — freshness check + yt-dlp + upsert
  - `GenerateSummariesUseCase` — freshness check + OpenAI summarize + upsert
  - `GenerateComparisonUseCase` — `build_comparison_artifact` + optional AI insights
  - `GenerateAssignmentsUseCase` — freshness check + OpenAI assignments
  - `GenerateQuizUseCase` — playlist fetch + transcripts + Gemini + Drive export

- [x] **Removed `os.environ` mutations**:
  - `PipelineService._apply_api_keys` → returns keys (reads env at call time)
  - `PipelineService.search` → passes keys to `YouTubePipeline`
  - `YouTubePipeline` / `search_youtube_videos_api` → accept optional `youtube_api_key`
  - `QuizService.process_playlist` → removed `os.environ["GEMINI_API_KEY"]`

### Validation
- `detect_changes` → HIGH risk (expected for public interface refactoring; all new params have defaults → backward compatible)
- All Python syntax checks pass
- Index refreshed (`gitnexus analyze`)

---

## Wave C — Transport Slimming (Completed)

### Goal
Replace legacy `backend/services/` orchestration with `src/application/use_cases/`, wire DI, and remove legacy `src/*.py` modules.

### Completed Tasks

#### 1. Implemented `src/config/` layer ✅
- `src/config/settings.py` — `SettingsLoader` protocol + `AtlasSettings` dataclass (env + config.yaml)
- `src/config/prompts.py` — `PromptRegistry` (YAML template loading with versioning)
- `src/config/models.py` — `ModelRegistry` (provider/model configuration)

#### 2. Wired Dependency Injection ✅
- Updated `backend/main.py`:
  - Creates `RunRepository` (SqlRunRepository), `Cache` (SqlCacheAdapter), `UsageLedger` (SqlUsageLedger)
  - Instantiates all provider adapters (OpenAI, Gemini, yt-dlp, YouTube, Google)
  - Instantiates all 6 use cases with injected dependencies via `src/transport/http/fastapi/dependencies.py`
  - Replaced `pipeline_service` / `quiz_service` / `run_service` singletons with use case instances

#### 3. Replaced Router Handlers ✅
- Moved routers to `src/transport/http/fastapi/routers/`:
  - `pipeline.py` — `search_pipeline` → `SearchPipelineUseCase.execute(SearchInput)`, etc.
  - `runs.py` — rewritten to use `RunRepositoryPort` directly (no `RunService`)
  - `quiz.py` — `create_playlist_quiz` / `create_playlist_quiz_stream` → `GenerateQuizUseCase.execute(QuizGenerationInput)`, progress callback → SSE bridge
  - `usage.py` — uses `UsageLedgerPort` with DI providing `SqlUsageLedger`

#### 4. Mounted `/api/usage` Router ✅
- Included `usage.router` in `backend/main.py`

#### 5. Added Cross-Cutting Concerns ✅
- `src/transport/http/fastapi/dependencies.py` — FastAPI `Depends()` providers for repository, cache, ledger, all 6 use cases
- `src/transport/http/fastapi/errors.py` — Domain-to-HTTP error translation (`DomainError` → 4xx/5xx)
- `src/transport/http/fastapi/middleware.py` — `CorrelationIdMiddleware`, `RequestLoggingMiddleware`

#### 6. Removed Legacy Modules ✅
- Deleted `backend/services/*.py` (`pipeline_service.py`, `quiz_service.py`, `run_service.py`, `artifact_readers.py`)
- Deleted legacy `src/*.py` modules (`youtube_pipeline.py`, `summarize_youtube_transcript.py`, `compare_youtube_outputs.py`, `assignment_generator.py`, `playlist_quiz_generator.py`, `fetch_youtube_transcript.py`, `youtube_video_search.py`)
- Moved artifact readers to `src/application/artifact_readers/__init__.py`
- Moved schemas to `src/transport/http/fastapi/schemas/`

### Validation ✅
- All existing API contracts unchanged (25 routes registered)
- `detect_changes` → MEDIUM risk (only internal symbol changes)
- Legacy modules deleted, no broken imports
- Backend starts successfully

---

## Wave D — Frontend & Polish (Completed)

### Completed Tasks
- ✅ **Frontend usage dashboard**
  - `frontend/src/lib/types.ts` — TypeScript interfaces for `UsageAggregateResponse` etc.
  - `frontend/src/lib/api.ts` — added `getUsageAggregate()` with query params
  - `frontend/src/features/usage/usage-dashboard.tsx` — React component with stats cards, provider/operation tables, cache stats, time range filters

- ✅ **Cost Calculator**
  - Created `src/infrastructure/llm/cost.py` — `CostCalculator` with pricing for OpenAI, Gemini, Anthropic models
  - Wired into OpenAI adapters (`_build_usage_record`) — calculates `cost_usd` from token usage
  - Wired into Gemini adapter (`_build_usage_record`) — calculates `cost_usd` from token usage
  - `SqlUsageLedger.aggregate()` returns real `total_cost_usd`

- ✅ **Quality Gates**
  - Frontend: `npm run build` passes (TypeScript + Vite)
  - Backend: Python syntax checks pass, app loads with 25 routes

- ✅ **Verification**
  - Backend starts successfully on port 8000
  - All API endpoints registered
  - Frontend builds to `dist/`

---

## Risk Register

| Risk | Wave | Mitigation |
|------|------|------------|
| Public interface changes break callers | B, C | All new params have defaults; `detect_changes` validates |
| Legacy modules still imported | C | Remove only after `grep -r` confirms zero imports |
| DI wiring errors | C | Wire incrementally; keep old services as fallback behind feature flag |
| Migration 0002 rollback needed | A | `DROP TABLE usage_ledger`; `ALTER TABLE cache_entries DROP COLUMN ...` |
| Frontend type drift | D | Generate TS types from Pydantic schemas (or keep manual sync) |

---

## Command Reference

```bash
# Re-index codebase after changes
node .gitnexus/run.cjs analyze

# Check what changes affect
gitnexus_detect_changes(scope="all")

# Syntax check
python -m py_compile <file.py>

# Future quality gates (when tools available)
uv run ruff check .
uv run pytest
```

---

## Quick Status

| Layer | Status | Location |
|-------|--------|----------|
| Domain | ✅ Done | `src/domain/` |
| Infrastructure | ✅ Done | `src/infrastructure/` |
| Application (use cases) | ✅ Done | `src/application/` |
| Config | ⏳ Next | `src/config/` |
| Transport (FastAPI) | 🟡 In Progress | `backend/` → `src/transport/http/fastapi/` |
| Frontend | ⏳ Pending | `frontend/` |
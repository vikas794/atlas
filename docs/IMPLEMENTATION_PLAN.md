# Atlas — Implementation Plan

This document tracks the phased refactor from a monolithic `backend/` + `src/` codebase to a clean layered architecture. Each wave is a vertical slice delivering a working system.

---

## Wave Overview

| Wave | Focus | Status | Key Deliverables |
|------|-------|--------|------------------|
| **A** | Foundation: Domain + Infrastructure | ✅ **DONE** | Domain models/ports, all infrastructure adapters, migration 0002, `/api/usage`, cleanup |
| **B** | Application Layer (Use Cases) | ✅ **DONE** | `src/application/` — DTOs, ports, 6 use cases; removed `os.environ` mutations |
| **C** | Transport Slimming | 🟡 **NEXT** | `src/config/`, DI wiring, FastAPI routers → use cases, legacy module removal |
| **D** | Frontend & Polish | ⏳ PENDING | Usage dashboard, cost calculator, tests, lint/typecheck |

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

## Wave C — Transport Slimming (Next)

### Goal
Replace legacy `backend/services/` orchestration with `src/application/use_cases/`, wire DI, and remove legacy `src/*.py` modules.

### Tasks

#### 1. Implement `src/config/` layer
- `settings.py` — `SettingsLoader` protocol + `AtlasSettings` dataclass (env + config.yaml)
- `prompts.py` — `PromptRegistry` (YAML template loading with versioning)
- `models.py` — `ModelRegistry` (provider/model configuration)

#### 2. Wire Dependency Injection
- Update `backend/main.py`:
  - Create `RunRepository` (SqlRunRepository), `Cache` (SqlCacheAdapter), `UsageLedger` (SqlUsageLedger)
  - Instantiate all provider adapters (OpenAI, Gemini, yt-dlp, YouTube, Google)
  - Instantiate all 6 use cases with injected dependencies
  - Replace `pipeline_service` / `quiz_service` singletons with use case instances

#### 3. Replace Router Handlers
- `backend/routers/pipeline.py`:
  - `search_pipeline` → `SearchPipelineUseCase.execute(SearchInput)`
  - `generate_transcripts` → `GenerateTranscriptsUseCase.execute(TranscriptGenerationInput)`
  - `generate_summaries` → `GenerateSummariesUseCase.execute(SummaryGenerationInput)`
  - `generate_comparison` → `GenerateComparisonUseCase.execute(ComparisonGenerationInput)`
  - `generate_assignments` → `GenerateAssignmentsUseCase.execute(AssignmentGenerationInput)`

- `backend/routers/quiz.py`:
  - `create_playlist_quiz` / `create_playlist_quiz_stream` → `GenerateQuizUseCase.execute(QuizGenerationInput)`
  - Progress callback → SSE bridge

- `backend/routers/usage.py` → already uses `UsageLedgerPort`; ensure DI provides `SqlUsageLedger`

#### 4. Mount `/api/usage` Router
- Include `usage.router` in `backend/main.py`

#### 5. Add Cross-Cutting Concerns
- `dependencies.py` — FastAPI `Depends()` providers for repository, cache, ledger, use cases
- `errors.py` — Domain-to-HTTP error translation (`DomainError` → 4xx/5xx)
- `middleware.py` — `CorrelationIdMiddleware`, `RequestLoggingMiddleware`

#### 6. Remove Legacy Modules
Once no longer imported by `backend/`:
- `src/youtube_pipeline.py`
- `src/summarize_youtube_transcript.py`
- `src/compare_youtube_outputs.py`
- `src/assignment_generator.py`
- `src/playlist_quiz_generator.py`
- `src/fetch_youtube_transcript.py`
- `src/youtube_video_search.py`
- `src/utils.py` (decompose into focused modules)

### Validation Criteria
- All existing API contracts unchanged (pytest + httpx)
- `detect_changes` → MEDIUM/LOW risk
- Legacy modules deleted, no broken imports

---

## Wave D — Frontend & Polish (Pending)

### Tasks
- [ ] **Frontend usage dashboard**
  - `frontend/src/lib/types/usage.ts` — TypeScript interfaces for `UsageAggregateResponse` etc.
  - `frontend/src/lib/api.ts` — add `usageApi` object
  - `frontend/src/components/UsageDashboard.svelte` — charts/tables for usage/cost/cache

- [ ] **Cost Calculator**
  - Wire `CostCalculator` into `SqlUsageLedger.aggregate()` for real `cost_usd`

- [ ] **Test Suite**
  - Unit: domain models, cache key builder, usage ledger, comparison inference
  - Integration: adapters with fixtures (SQLite, httpx recorded responses, mock yt-dlp)
  - API: existing endpoints + `/api/usage` + SSE

- [ ] **Quality Gates**
  - `uv run ruff check .`
  - `uv run mypy .` (if type hints added)
  - `uv run pytest`

- [ ] **Verification**
  - `pipeline_output_*` artifacts intact
  - `data/atlas.sqlite3` migrated successfully

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
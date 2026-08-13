# Atlas — Target Architecture

> **Status (post-Wave-B):** Domain ✅, Infrastructure ✅, Application (use cases) ✅, Transport (legacy backend) partially migrated. Next: Wave C (slim FastAPI routers onto application layer) + Wave D (frontend usage dashboard).

---

## 1. Target Directory Structure (with Current Status)

```
src/
├── domain/                          ✅ IMPLEMENTED (Wave A1)
│   ├── __init__.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── video.py            # VideoId, VideoMetadata, TranscriptRef, SummaryRef
│   │   ├── transcript.py       # Transcript, TranscriptContent, TranscriptStatus
│   │   ├── summary.py           # Summary, SummaryData, SummaryStatus
│   │   ├── comparison.py        # ComparisonRow, InsightsReport, Recommendations
│   │   ├── assignment.py        # Assignment, AssignmentMetadata
│   │   └── quiz.py              # QuizResult, PlaylistResult
│   ├── interfaces/
│   │   ├── __init__.py
│   │   ├── transcript_provider.py    # TranscriptProviderPort, TranscriptResult
│   │   ├── summarizer.py             # SummarizerPort, SummaryContext, SummaryResult
│   │   ├── insights_provider.py      # InsightsProviderPort, InsightsResult
│   │   ├── assignment_generator.py   # AssignmentGeneratorPort, AssignmentResult
│   │   ├── quiz_generator.py         # QuizGeneratorPort, QuizContext, QuizResult
│   │   ├── cache.py                  # CachePort, CacheKey
│   │   ├── usage_ledger.py           # UsageLedgerPort, UsageRecord, UsageAggregate
│   │   └── storage.py                # RunRepositoryPort, ArtifactStorePort, RunRecord, CacheEntry
│   ├── services/
│   │   ├── __init__.py
│   │   ├── comparison_inference.py   # ComparisonInferenceService (difficulty, style, value)
│   │   ├── hash_computer.py          # RunHashComputer (state hashes for staleness)
│   │   └── cache_key_builder.py      # CacheKeyBuilder (canonical key construction)
│   └── exceptions/
│       ├── __init__.py
│       └── domain.py                 # DomainError, ProviderError, CacheError, StorageError
│
├── application/                     ✅ IMPLEMENTED (Wave B)
│   ├── __init__.py
│   ├── use_cases/
│   │   ├── __init__.py
│   │   ├── search_pipeline.py        # SearchPipelineUseCase
│   │   ├── generate_transcripts.py   # GenerateTranscriptsUseCase
│   │   ├── generate_summaries.py     # GenerateSummariesUseCase
│   │   ├── generate_comparison.py    # GenerateComparisonUseCase
│   │   ├── generate_assignments.py   # GenerateAssignmentsUseCase
│   │   └── generate_quiz.py          # GenerateQuizUseCase
│   ├── ports/
│   │   ├── __init__.py
│   │   └── provider_ports.py         # Re-exports domain interfaces as app ports
│   └── dto/
│       ├── __init__.py
│       ├── search.py                 # SearchInput, SearchOutput
│       ├── transcripts.py            # TranscriptGenerationInput, TranscriptGenerationOutput
│       ├── summaries.py              # SummaryGenerationInput, SummaryGenerationOutput
│       ├── comparison.py             # ComparisonGenerationInput, ComparisonGenerationOutput
│       ├── assignments.py            # AssignmentGenerationInput, AssignmentGenerationOutput
│       └── quiz.py                   # QuizGenerationInput, QuizGenerationOutput, VideoQuizResult
│
├── infrastructure/                  ✅ IMPLEMENTED (Waves A2–A8)
│   ├── __init__.py
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py                   # LLMProvider base, RetryPolicy, LLMResponse, TokenUsage
│   │   │                             # SettingsLoader, UsageLedgerSink, RetryableProvider
│   │   ├── openai/
│   │   │   ├── __init__.py
│   │   │   ├── adapter.py            # OpenAISummarizerAdapter, OpenAIInsightsProvider, OpenAIAssignmentAdapter
│   │   │   └── retry.py              # OpenAIRetryableProvider (bounded exp backoff + jitter)
│   │   └── gemini/
│   │       ├── __init__.py
│   │       └── adapter.py            # GeminiQuizProvider (legacy retry: 2**attempt, max 3)
│   ├── transcript/
│   │   └── ytdlp/
│   │       ├── __init__.py
│   │       └── provider.py            # YtDlpTranscriptProvider (legacy retry [15,30,60,120]+jitter)
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── sql.py                     # SqlRunRepository (delegates to backend.storage.repository.RunRepository),
│   │   │                             # SqlUsageLedger (record/aggregate/recent + aliases)
│   │   └── filesystem.py             # ArtifactFileStore (atomic writes via src.utils.atomic_write)
│   ├── cache/
│   │   ├── __init__.py               # exports SqlCacheAdapter
│   │   └── sql_cache.py               # SqlCacheAdapter (zlib-compressed, TTL, hit_count, invalidate*)
│   ├── youtube/
│   │   ├── __init__.py               # exports both providers
│   │   ├── search.py                  # YouTubeDataApiSearchProvider
│   │   └── playlist.py                # YouTubePlaylistProvider (+ get_playlist_title)
│   └── google/
│       ├── __init__.py               # exports GoogleDriveExporter
│       └── drive.py                   # GoogleDriveExporter (OAuth web→installed rewrite preserved)
│
├── config/                            ⏳ PENDING (Wave C)
│   ├── __init__.py
│   ├── settings.py                    # SettingsLoader, AtlasSettings (env + config.yaml)
│   ├── prompts.py                     # PromptRegistry (YAML prompt templates with versioning)
│   └── models.py                      # ModelRegistry (provider/model configuration)
│
├── transport/
│   └── http/
│       ├── __init__.py
│       └── fastapi/
│           ├── __init__.py
│           ├── main.py                 # ⏳ FastAPI app, lifespan, CORS, middleware (still in backend/main.py)
│           ├── routers/
│           │   ├── __init__.py
│           │   ├── pipeline.py         # ⏳ /api/pipeline/search, /api/runs/{id}/transcripts|summaries|comparison|assignments
│           │   ├── runs.py             # ⏳ /api/runs, /api/runs/latest, /api/runs/{id}
│           │   ├── quiz.py             # ⏳ /api/quiz/playlist, /api/quiz/playlist/stream, /api/quiz/drive-status, /api/quiz/credentials, /api/quiz/auth
│           │   └── usage.py            # ✅ GET /api/usage (usage aggregation endpoint)
│           ├── schemas/
│           │   ├── __init__.py
│           │   ├── pipeline.py         # ✅ SearchRequest, ArtifactGenerationRequest, PipelineActionResponse
│           │   ├── runs.py             # ⏳ RunManifest, RunListResponse, SearchArtifactResponse, ...
│           │   └── quiz.py             # ✅ PlaylistQuizRequest, PlaylistQuizStatusResponse, DriveStatusResponse, VideoQuizResult
│           ├── dependencies.py          # ⏳ FastAPI Depends() providers
│           ├── errors.py                # ⏳ domain-to-HTTP error translation
│           └── middleware.py            # ⏳ CorrelationIdMiddleware, RequestLoggingMiddleware
│
└── (legacy transport still in backend/ — routers/, schemas/, services/, storage/ —
     remains until Wave C slims it; backend/storage/settings.py + repository.py + database.py + cache.py + migrations.py reused)
```

**Legend:** ✅ done · ⏳ pending/partial · `backend/` = legacy transport layer to be slimmed in Wave C.

---

## 2. Dependency Rules

### Allowed Directions

```
┌─────────────────────────────────────────────────────────────────┐
│                        LAYER LADDERS                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  transport/http/fastapi                                          │
│       │                                                          │
│       ▼                                                          │
│  application/use_cases                                           │
│       │                                                          │
│       ▼                                                          │
│  domain/                                                         │
│       ▲                                                          │
│       │                                                          │
│  infrastructure/                                                  │
│    (implements domain/application ports)                          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Explicit Forbidden Dependencies

| Layer | Forbidden Dependency | Rationale |
|-------|----------------------|-----------|
| `domain/` | FastAPI, HTTP, SQLite, yt-dlp, OpenAI SDK, Gemini SDK, filesystem paths, pandas | Domain must be pure, testable without external systems |
| `domain/` | `src/utils.py` (kitchen-sink module) | Too many mixed concerns; decompose into focused utilities |
| `application/` | Concrete provider SDKs (OpenAI, Gemini, yt-dlp) | Application orchestrates via ports; infrastructure provides adapters |
| `application/` | Pydantic models | Application uses plain Python DTOs/dataclasses; transport layer handles serialization |
| `infrastructure/` | `backend/routers/`, `backend/schemas/` | Infrastructure must not depend on transport layer |
| `transport/` | `src/youtube_pipeline.py`, `src/summarize_youtube_transcript.py`, etc. | Route handlers must call application use cases, not legacy modules directly (Wave C) |
| `infrastructure/llm/` | ThreadPoolExecutor for parallelism | Parallelism is an application concern; adapters should be stateless and reentrant |
| All layers | `os.environ[...] = ...` mutation | Secrets injected via configuration; never mutate global state (Wave B ✅) |

---

## 3. Interface Contracts

*Implemented as `typing.Protocol` classes in `src/domain/interfaces/`. All domain code is pure Python. Adapters live in `src/infrastructure/` and accept `SettingsLoader` + optional `UsageLedgerSink` via constructor injection.*

### 3.1 Transcript Provider

```python
class TranscriptProviderPort(Protocol):
    async def fetch_transcript(self, video_id: str, language: str = "en") -> TranscriptResult: ...
    async def fetch_transcripts(self, video_ids: list[str], language: str = "en") -> list[TranscriptResult]: ...

@dataclass(frozen=True)
class TranscriptResult:
    video_id: str
    language: str
    raw_srt: str
    cleaned_text: str
    content_hash: str
    artifact_path: str
    byte_size: int
```

Implementation: `YtDlpTranscriptProvider` — preserves legacy retry schedule, rate-limit cooldown, inter-video pacing.

### 3.2 Summarizer

```python
class SummarizerPort(Protocol):
    async def summarize(self, context: SummaryContext) -> SummaryResult: ...
    async def summarize_batch(self, contexts: list[SummaryContext]) -> list[SummaryResult]: ...

@dataclass(frozen=True)
class SummaryContext:
    video_id: str
    title: str
    channel: str
    transcript_text: str
    language: str = "en"
    prompt_version: str = "v1"
    model: str = "gpt-4o-mini"

@dataclass(frozen=True)
class SummaryResult:
    video_id: str
    summary_data: dict
    content_hash: str
    artifact_path: str
    byte_size: int
    prompt_version: str
    model: str
```

Implementation: `OpenAISummarizerAdapter` — bounded exp backoff retry, usage ledger emission.

### 3.3 Insights Provider

```python
class InsightsProviderPort(Protocol):
    async def generate_insights(self, video_metadata: dict, summary_data: dict, prompt_version: str = "v1", model: str = "gpt-4o-mini") -> InsightsResult: ...
    async def generate_insights_batch(self, video_metadata_list: list[dict], summary_data_list: list[dict], prompt_version: str = "v1", model: str = "gpt-4o-mini") -> list[InsightsResult]: ...

@dataclass(frozen=True)
class InsightsResult:
    video_id: str
    learning_outcome: str
    difficulty_level: str
    teaching_style: str
    practical_value: str
    content_depth: str
    target_audience: str
    key_differentiators: str
    time_investment_worth: str
    prerequisites: str
    follow_up_recommendations: str
    content_hash: str
    artifact_path: str
    prompt_version: str
    model: str
```

Implementation: `OpenAIInsightsProvider` — JSON fallback to `_get_fallback_insights()`.

### 3.4 Assignment Generator

```python
class AssignmentGeneratorPort(Protocol):
    async def generate_assignment(self, video_id: str, title: str, channel: str, summary_data: dict, prompt_version: str = "v1", model: str = "gpt-4o-mini") -> AssignmentResult: ...
    async def generate_assignments_batch(self, videos: list[dict], summaries: list[dict], prompt_version: str = "v1", model: str = "gpt-4o-mini") -> list[AssignmentResult]: ...

@dataclass(frozen=True)
class AssignmentResult:
    video_id: str
    markdown: str
    sections: list[dict]
    checklist: list[dict]
    metadata: dict
    display_metadata: dict[str, str]
    content_hash: str
    artifact_path: str
    byte_size: int
    prompt_version: str
    model: str
```

Implementation: `OpenAIAssignmentAdapter` — prompt templates loaded from YAML.

### 3.5 Quiz Generator

```python
class QuizGeneratorPort(Protocol):
    async def generate_quiz(self, transcript: TranscriptRef, title: str, context: QuizContext) -> QuizResult: ...

@dataclass(frozen=True)
class QuizContext:
    playlist_url: str
    gemini_api_key: str | None = None
    max_videos: int | None = None
    model: str = "gemini-1.5-pro"

@dataclass(frozen=True)
class QuizResult:
    video_id: str
    content: str
    token_usage: TokenUsage | None
    success: bool
```

Implementation: `GeminiQuizProvider` — new `genai.Client` per call (thread-safety), token counts from `usage_metadata`.

### 3.6 Cache Port

```python
class CachePort(Protocol):
    async def get(self, key: CacheKey) -> bytes | None: ...
    async def set(self, key: CacheKey, value: bytes, ttl: timedelta) -> None: ...
    async def delete(self, key: CacheKey) -> None: ...
    async def exists(self, key: CacheKey) -> bool: ...
    async def touch(self, key: CacheKey) -> None: ...

@dataclass(frozen=True)
class CacheKey:
    namespace: str
    version: str
    content_hash: str
    params_hash: str

    def __str__(self) -> str:
        return f"{self.namespace}:{self.version}:{self.content_hash}:{self.params_hash}"
```

Implementation: `SqlCacheAdapter` — zlib-compressed JSON, TTL check, hit_count tracking, default `run_id="global-cache"`.

### 3.7 Usage Ledger Port

```python
class UsageLedgerPort(Protocol):
    async def record(self, record: UsageRecord) -> None: ...
    async def aggregate(self, *, provider: str | None = None, model: str | None = None,
                        operation: str | None = None, since: datetime | None = None,
                        until: datetime | None = None, cache_status: bool | None = None) -> UsageAggregate: ...
    async def recent(self, limit: int = 100) -> list[UsageRecord]: ...

@dataclass(frozen=True)
class UsageRecord:
    timestamp: datetime
    provider: str
    operation: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    cache_hit: bool
    run_id: str | None = None
    video_id: str | None = None

@dataclass(frozen=True)
class UsageAggregate:
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cost_usd: float
    cache_hit_rate: float
```

Implementation: `SqlUsageLedger` — provides both protocol and legacy (`record_usage`, `get_aggregate`, `get_by_provider`, `get_by_operation`, `get_cache_stats`).

### 3.8 Run Repository Port

```python
class RunRepositoryPort(Protocol):
    async def create_run(self, *, run_id: str, cache_key: str | None, search_query: str, normalized_query: str,
                         max_videos: int, transcript_language: str, is_fallback: bool) -> str: ...
    async def get_run(self, run_id: str) -> dict | None: ...
    async def list_runs(self) -> list[dict]: ...
    async def latest_run(self) -> dict | None: ...
    async def set_run_status(self, run_id: str, status: str, error: str | None = None) -> None: ...
    async def set_videos(self, run_id: str, videos: list[dict]) -> None: ...
    async def get_videos(self, run_id: str) -> list[dict]: ...
    async def videos_state_hash(self, run_id: str) -> str: ...
    async def upsert_transcripts(self, run_id: str, transcripts: list[dict], settings: dict | None = None) -> None: ...
    async def get_transcripts(self, run_id: str) -> list[dict]: ...
    async def transcripts_state_hash(self, run_id: str) -> str: ...
    async def upsert_summaries(self, run_id: str, summaries: list[dict], settings: dict | None = None) -> None: ...
    async def get_summaries(self, run_id: str) -> list[dict]: ...
    async def summaries_state_hash(self, run_id: str) -> str: ...
    async def set_comparison(self, run_id: str, rows: list[dict], insights_report: dict, recommendations: dict,
                             settings: dict | None = None, status: str = "succeeded", error: str | None = None) -> None: ...
    async def get_comparison(self, run_id: str) -> tuple[list[dict], dict, dict] | None: ...
    async def upsert_assignments(self, run_id: str, assignments: list[dict], settings: dict | None = None) -> None: ...
    async def get_assignments(self, run_id: str) -> list[dict]: ...
    async def set_quiz_result(self, run_id: str, result: dict, settings: dict | None = None) -> None: ...
    async def get_quiz_result(self, run_id: str) -> dict | None: ...
    async def recompute_run_hashes(self, run_id: str) -> None: ...
    async def mark_stale_derived(self, run_id: str) -> None: ...
    async def purge_expired(self, retention_days: int = 90) -> dict: ...
    async def stats(self) -> dict: ...
    async def find_cached_run(self, cache_key: str) -> dict | None: ...
    async def put_cache_entry(self, cache_key: str, kind: str, run_id: str, normalized_query: str, settings: dict, ttl_days: int) -> None: ...
    async def touch_cache_hit(self, cache_key: str) -> None: ...
```

Implementation: `SqlRunRepository` — delegates to `backend.storage.repository.RunRepository` (no logic rewrite).

---

## 4. Cache Model Details

### 4.1 Key Construction

`CacheKeyBuilder.VERSION = "v2"` — methods: `transcript_key`, `summary_key`, `comparison_key`, `assignment_key`, `quiz_key`, `search_key`. Each builds a `CacheKey(namespace, version, content_hash, params_hash)`.

### 4.2 TTL Policy

| Namespace | TTL | Invalidation Trigger |
|-----------|-----|---------------------|
| `transcript` | 30 days | Video URL changes, language changes, manual refresh |
| `summary` | 90 days | Prompt version changes, model changes, transcript changes |
| `comparison` | 90 days | Input summaries change, prompt version changes, model changes |
| `assignment` | 90 days | Input summary changes, prompt version changes, model changes |
| `quiz` | 30 days | Transcript changes, prompt version changes, model changes |
| `search` | 7 days | Query changes, max_videos changes, language changes |

### 4.3 Storage Format

SQLite `cache_entries` extended with `namespace`, `version`, `content_hash`, `params_hash`, `value` (zlib-compressed JSON). Legacy rows remain readable.

---

## 5. Usage Ledger Model Details

### 5.1 SQL Schema (Migration 0002 — APPLIED ✅)

```sql
CREATE TABLE IF NOT EXISTS usage_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    run_id TEXT,
    timestamp TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    operation TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
    currency TEXT NOT NULL DEFAULT 'USD',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 1,
    error_category TEXT,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    cache_key TEXT,
    cache_namespace TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage_ledger(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_ledger(provider);
CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_ledger(model);
CREATE INDEX IF NOT EXISTS idx_usage_operation ON usage_ledger(operation);
CREATE INDEX IF NOT EXISTS idx_usage_run_id ON usage_ledger(run_id);
CREATE INDEX IF NOT EXISTS idx_usage_request_id ON usage_ledger(request_id);
```

### 5.2 Token Counting & Cost

- OpenAI/OpenRouter: `response.usage.*` when available
- Gemini: `response.usage_metadata.*` when available
- Cache hit: `input_tokens=0, output_tokens=0, cache_hit=True`
- Cost calculator not yet wired (adapters emit `cost_usd=0.0`)

---

## 6. API Compatibility Assessment

**No breaking changes.** All existing endpoint paths, methods, request/response shapes, and status codes preserved.

### Preserved Contracts (backend/ legacy transport, unchanged)

| Endpoint | Method | Request Shape | Response Shape | Status Codes |
|----------|--------|---------------|----------------|--------------|
| `/api/pipeline/search` | POST | `SearchRequest` | `PipelineActionResponse` | 200, 400, 404, 502 |
| `/api/runs/{id}/transcripts` | POST | `ArtifactGenerationRequest` | `PipelineActionResponse` | 200, 400, 404, 502 |
| `/api/runs/{id}/summaries` | POST | `ArtifactGenerationRequest` | `PipelineActionResponse` | 200, 400, 404, 502 |
| `/api/runs/{id}/comparison` | POST | `ArtifactGenerationRequest` | `PipelineActionResponse` | 200, 400, 404, 502 |
| `/api/runs/{id}/assignments` | POST | `ArtifactGenerationRequest` | `PipelineActionResponse` | 200, 400, 404, 502 |
| `/api/runs` | GET | — | `RunListResponse` | 200, 404 |
| `/api/runs/latest` | GET | — | `RunManifest` | 200, 404 |
| `/api/runs/{id}` | GET | — | `RunManifest` | 200, 404 |
| `/api/runs/{id}/videos` | GET | — | `SearchArtifactResponse` | 200, 404 |
| `/api/runs/{id}/transcripts` | GET | — | `TranscriptArtifactResponse` | 200, 404 |
| `/api/runs/{id}/summaries` | GET | — | `SummaryArtifactResponse` | 200, 404 |
| `/api/runs/{id}/comparison` | GET | — | `ComparisonArtifactResponse` | 200, 404 |
| `/api/runs/{id}/assignments` | GET | — | `AssignmentArtifactResponse` | 200, 404 |
| `/api/quiz/playlist` | POST | `PlaylistQuizRequest` | `PlaylistQuizStatusResponse` | 200, 400, 401, 502 |
| `/api/quiz/playlist/stream` | POST | `PlaylistQuizRequest` | SSE | 200, 400, 401, 502 |
| `/api/quiz/drive-status` | GET | — | `DriveStatusResponse` | 200 |
| `/api/quiz/credentials` | POST | UploadFile | `{status, message}` | 200, 400 |
| `/api/quiz/auth` | POST | — | `{status, message}` | 200, 400 |

### New Endpoint (Additive — IMPLEMENTED ✅)

| Endpoint | Method | Response | Description |
|----------|--------|----------|-------------|
| `/api/usage` | GET | `UsageAggregateResponse` | Aggregated usage/cost data with optional filters |

Router: `src/transport/http/fastapi/routers/usage.py`; schemas: `src/transport/http/fastapi/schemas/usage.py`. Not yet mounted in `backend/main.py` — Wave C.

---

## 7. Data Migration Strategy

### No Data Loss

1. **Existing SQLite database** (`data/atlas.sqlite3`) — preserved; migration `0002` appends `usage_ledger` + extends `cache_entries` ✅ applied
2. **Existing artifact files** (`data/artifacts/`, `pipeline_output_*/`) — preserved; `ArtifactFileStore` reads/writes under `artifact_root`
3. **Existing cache entries** — preserved; legacy rows readable (new columns nullable)

### Applied Migrations

- `0001_initial` — original schema
- `0002_usage_ledger_and_extended_cache` — `usage_ledger` table + 6 indexes; `ALTER TABLE cache_entries ADD COLUMN namespace/version/content_hash/params_hash/value`

---

## 8. Configuration

| Variable | Purpose | Required |
|----------|---------|----------|
| `OPENROUTER_API_KEY` | OpenAI API via OpenRouter | Yes |
| `YOUTUBE_API_KEY` | YouTube Data API | Yes |
| `GEMINI_API_KEY` | Google AI Studio / Gemini | Yes |
| `ATLAS_DB_PATH` | SQLite database path | No (default: `data/atlas.sqlite3`) |
| `ATLAS_ARTIFACT_ROOT` | Artifact storage root | No (default: `data/artifacts`) |
| `ATLAS_CACHE_TTL_DAYS` | Default cache TTL | No (default: 30) |
| `ATLAS_GOOGLE_CREDS_PATH` | Google OAuth credentials JSON | No (default: `credentials.json`) |
| `ATLAS_GOOGLE_TOKEN_PATH` | Google OAuth token JSON | No (default: `token.json`) |
| `ATLAS_DEV_SHUTDOWN_TOKEN` | Dev server shutdown token | No |
| `ATLAS_RETENTION_DAYS` | Artifact retention | No (default: 90) |

---

## 9. Testing Strategy

> **Current state:** no tests exist yet for new layers. Test suite to be added alongside Waves C–D.

### Unit Tests
- Domain models and services (pure Python)
- Cache key builder determinism
- Usage ledger aggregation correctness
- Comparison inference logic

### Integration Tests
- Each adapter with real/fixture external systems:
  - `SqlRunRepository`, `SqlCacheAdapter` with in-memory SQLite
  - LLM adapters with recorded HTTP fixtures (httpx)
  - `YtDlpTranscriptProvider` with mock responses

### API Tests
- Existing endpoint contracts verified (pytest + httpx)
- New `/api/usage` endpoint tested
- SSE streaming endpoint tested

---

## 10. Performance Targets

| Path | Current Behavior | Target After Refactor |
|------|------------------|-----------------------|
| Repeated summary request | Re-calls OpenAI every time | Cache hit → zero LLM calls, <50ms |
| Repeated comparison request | Re-calls OpenAI N times | Cache hit → zero LLM calls, <100ms |
| Repeated transcript request | Re-runs yt-dlp | Cache hit → zero network I/O, <10ms |
| Repeated assignment request | Re-calls OpenAI N times | Cache hit → zero LLM calls, <50ms |
| OpenAI client creation | New client per call | Reuse via DI |
| Gemini client creation | New client per request | Reuse via DI |
| Config loading | Reloads YAML on first call | Single load at startup |

---

## 11. Migration Checklist — **Current Progress**

- [x] Create `docs/ARCHITECTURE.md` (this document)
- [x] Define all domain models and ports (`src/domain/`, 18 files)
- [x] Implement `SqlRunRepository` + `SqlUsageLedger` (`src/infrastructure/storage/sql.py`)
- [x] Implement `SqlCacheAdapter` with content-derived keys (`src/infrastructure/cache/sql_cache.py`)
- [x] Implement OpenAI adapters with retry/backoff and instrumentation (`src/infrastructure/llm/openai/`)
- [x] Implement Gemini adapter with retry/backoff and instrumentation (`src/infrastructure/llm/gemini/`)
- [x] Implement yt-dlp transcript provider (`src/infrastructure/transcript/ytdlp/`)
- [x] Implement YouTube search/playlist adapters (`src/infrastructure/youtube/`)
- [x] Implement Google Drive exporter + ArtifactFileStore (`src/infrastructure/google/`, `src/infrastructure/storage/filesystem.py`)
- [x] Add `/api/usage` endpoint + schemas (`src/transport/http/fastapi/routers/usage.py`, `schemas/usage.py`)
- [x] Apply migration `0002_usage_ledger_and_extended_cache`
- [x] Delete `app.py` (Gradio legacy)
- [x] Remove stale build artifacts (`src/atlas.egg-info/`, all `__pycache__/`)
- [x] Make Google OAuth paths configurable (`ATLAS_GOOGLE_CREDS_PATH`, `ATLAS_GOOGLE_TOKEN_PATH`)
- [x] **Implement application use cases (Wave B)** — 6 use cases + DTOs + ports
- [x] **Remove `os.environ[...]` mutations** from `backend/services/pipeline_service.py` + `backend/services/quiz_service.py`
- [ ] Implement `src/config/` layer (settings, prompts, models) — Wave C
- [ ] Slim FastAPI routers to use application layer (Wave C)
- [ ] Mount `/api/usage` router into app + wire DI container (Wave C)
- [ ] Add correlation-ID middleware + error mapping (Wave C)
- [ ] Remove legacy `src/youtube_pipeline.py`, `src/summarize_youtube_transcript.py`, `src/compare_youtube_outputs.py`, `src/assignment_generator.py`, `src/playlist_quiz_generator.py`, `src/fetch_youtube_transcript.py`, `src/youtube_video_search.py` (Wave C — after backend no longer imports them)
- [ ] Add frontend usage dashboard (Wave D)
- [ ] Wire `CostCalculator` into ledger
- [ ] Add tests (domain/cache/usage/API)
- [ ] Run `uv run ruff check .`
- [ ] Run `uv run pytest`
- [ ] Verify `pipeline_output_*` artifacts intact
- [ ] Verify `data/atlas.sqlite3` migrated successfully

---

## 12. Implementation Plan Summary

### Wave A — Foundation (✅ COMPLETED)
- Domain models, interfaces, exceptions
- Infrastructure adapters (LLM, transcript, storage, cache, YouTube, Google)
- Migration `0002`, `/api/usage` router, cleanup

### Wave B — Application Layer (✅ COMPLETED)
- `src/application/dto/` — 6 DTO modules
- `src/application/ports/provider_ports.py` — domain interface re-exports
- `src/application/use_cases/` — 6 use cases orchestrating via ports
- Removed `os.environ` mutations from backend services

### Wave C — Transport Slimming (🟡 NEXT)
1. Implement `src/config/` (settings, prompts, models)
2. Wire DI in `backend/main.py` → inject use cases into FastAPI
3. Replace `PipelineService`/`QuizService` calls with `SearchPipelineUseCase` etc.
4. Mount `/api/usage` router
5. Add correlation-ID middleware, error mapping
6. Delete legacy `src/*.py` modules once no longer imported

### Wave D — Frontend & Polish (⏳ PENDING)
- Frontend usage dashboard (`types/usage.ts`, `api.ts`, dashboard component)
- Wire `CostCalculator` into ledger
- Comprehensive test suite
- Lint + typecheck + pytest
# Atlas — Target Architecture

## 1. Target Directory Structure

```
src/
├── domain/
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
│   │   ├── transcript_provider.py    # TranscriptProviderPort
│   │   ├── summarizer.py             # SummarizerPort
│   │   ├── insights_provider.py      # InsightsProviderPort
│   │   ├── assignment_generator.py   # AssignmentGeneratorPort
│   │   ├── quiz_generator.py         # QuizGeneratorPort
│   │   ├── cache.py                  # CachePort, CacheKey
│   │   ├── usage_ledger.py           # UsageLedgerPort, UsageRecord, UsageAggregate
│   │   └── storage.py                # RunRepositoryPort, ArtifactStorePort
│   ├── services/
│   │   ├── __init__.py
│   │   ├── comparison_inference.py   # ComparisonInferenceService (difficulty, style, value)
│   │   ├── hash_computer.py          # RunHashComputer (state hashes for staleness)
│   │   └── cache_key_builder.py      # CacheKeyBuilder (canonical key construction)
│   └── exceptions/
│       ├── __init__.py
│       └── domain.py                 # DomainError, ProviderError, CacheError
│
├── application/
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
│   │   └── provider_ports.py         # Concrete protocol classes mirroring domain interfaces
│   └── dto/
│       ├── __init__.py
│       ├── search.py                 # SearchInput, SearchOutput
│       ├── transcripts.py            # TranscriptGenerationInput, TranscriptGenerationOutput
│       ├── summaries.py              # SummaryGenerationInput, SummaryGenerationOutput
│       ├── comparison.py             # ComparisonGenerationInput, ComparisonGenerationOutput
│       ├── assignments.py            # AssignmentGenerationInput, AssignmentGenerationOutput
│       └── quiz.py                   # QuizGenerationInput, QuizGenerationOutput
│
├── infrastructure/
│   ├── __init__.py
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py                   # LLMProvider base, RetryPolicy, LLMResponse, TokenUsage
│   │   ├── openai/
│   │   │   ├── __init__.py
│   │   │   ├── adapter.py            # OpenAISummarizerAdapter, OpenAIInsightsProvider, OpenAIAssignmentAdapter
│   │   │   └── retry.py              # OpenAIRetryPolicy (exponential backoff, rate-limit handling)
│   │   └── gemini/
│   │       ├── __init__.py
│   │       └── adapter.py            # GeminiQuizProvider
│   ├── transcript/
│   │   └── ytdlp/
│   │       ├── __init__.py
│   │       └── provider.py            # YtDlpTranscriptProvider
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── sql.py                     # SqlRunRepository, SqlCacheRepository, SqlUsageLedger
│   │   └── filesystem.py             # ArtifactFileStore (reads/writes transcript/summary/assignment files)
│   ├── cache/
│   │   ├── __init__.py
│   │   └── sql_cache.py               # SqlCacheAdapter (content-derived keys, TTL, namespaces)
│   ├── youtube/
│   │   ├── __init__.py
│   │   ├── search.py                  # YouTubeDataApiSearchProvider
│   │   └── playlist.py                # YouTubePlaylistProvider
│   └── google/
│       ├── __init__.py
│       └── drive.py                   # GoogleDriveExporter
│
├── config/
│   ├── __init__.py
│   ├── settings.py                     # SettingsLoader, AtlasSettings (env + config.yaml)
│   ├── prompts.py                      # PromptRegistry (YAML prompt templates with versioning)
│   └── models.py                       # ModelRegistry (provider/model configuration)
│
└── transport/
    └── http/
        ├── __init__.py
        ├── fastapi/
        │   ├── __init__.py
        │   ├── main.py                 # FastAPI app, lifespan, CORS, middleware
        │   ├── routers/
        │   │   ├── __init__.py
        │   │   ├── pipeline.py         # /api/pipeline/search, /api/runs/{id}/transcripts|summaries|comparison|assignments
        │   │   ├── runs.py             # /api/runs, /api/runs/latest, /api/runs/{id}
        │   │   ├── quiz.py             # /api/quiz/playlist, /api/quiz/playlist/stream, /api/quiz/drive-status, /api/quiz/credentials, /api/quiz/auth
        │   │   └── usage.py            # GET /api/usage (usage aggregation endpoint)
        │   ├── schemas/
        │   │   ├── __init__.py
        │   │   ├── pipeline.py         # SearchRequest, ArtifactGenerationRequest, PipelineActionResponse
        │   │   ├── runs.py              # RunManifest, RunListResponse, SearchArtifactResponse, TranscriptArtifactResponse, SummaryArtifactResponse, ComparisonArtifactResponse, AssignmentArtifactResponse
        │   │   └── quiz.py              # PlaylistQuizRequest, PlaylistQuizStatusResponse, DriveStatusResponse, VideoQuizResult
        │   ├── dependencies.py          # FastAPI Depends() providers (repository, cache, ledger, providers)
        │   └── errors.py                # HTTPException mappings, domain-to-HTTP error translation
        │   └── middleware.py            # CorrelationIdMiddleware, RequestLoggingMiddleware
        │
        └── legacy/                     # Temporary compatibility shims during migration
            └── adapters.py             # Thin wrappers making new services look like old src/* classes
```

---

## 2. Dependency Rules

### Allowed Directions

```
┌─────────────────────────────────────────────────────────────────┐
│                        LAYER LADDERS                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  transport/http/fastapi                                         │
│       │                                                         │
│       ▼                                                         │
│  application/use_cases                                          │
│       │                                                         │
│       ▼                                                         │
│  domain/                                                        │
│       ▲                                                         │
│       │                                                         │
│  infrastructure/                                                 │
│    (implements domain/application ports)                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Explicit Forbidden Dependencies

| Layer | Forbidden Dependency | Rationale |
|-------|----------------------|-----------|
| `domain/` | FastAPI, HTTP, SQLite, yt-dlp, OpenAI SDK, Gemini SDK, filesystem paths, pandas | Domain must be pure, testable without external systems |
| `domain/` | `src/utils.py` (current kitchen-sink module) | Too many mixed concerns; decompose into focused utilities |
| `application/` | Concrete provider SDKs (OpenAI, Gemini, yt-dlp) | Application orchestrates via ports; infrastructure provides adapters |
| `application/` | Pydantic models | Application uses plain Python DTOs/dataclasses; transport layer handles serialization |
| `infrastructure/` | `backend/routers/`, `backend/schemas/` | Infrastructure must not depend on transport layer |
| `infrastructure/` | `app.py` (Gradio) | Legacy UI to be deleted |
| `transport/` | `src/youtube_pipeline.py`, `src/summarize_youtube_transcript.py`, etc. | Route handlers must call application use cases, not legacy modules directly |
| `infrastructure/llm/` | ThreadPoolExecutor for parallelism | Parallelism is an application concern; adapters should be stateless and reentrant |
| All layers | `os.environ[...] = ...` mutation | Secrets injected via configuration; never mutate global state |

---

## 3. Interface Contracts

### 3.1 Transcript Provider

```python
class TranscriptProviderPort(Protocol):
    def fetch_transcript(self, video_id: str, language: str) -> TranscriptResult:
        """Fetch transcript for a single video. Returns TranscriptResult with content, status, error."""
        ...

@dataclass(frozen=True)
class TranscriptResult:
    video_id: str
    language: str
    content: str | None           # SRT text
    content_hash: str | None      # sha256 of content
    byte_size: int | None
    status: Literal["succeeded", "no_subtitles", "rate_limited", "members_only", "failed"]
    error: str | None
    artifact_path: str | None     # If saved to disk
```

### 3.2 Summarizer

```python
class SummarizerPort(Protocol):
    def summarize(self, transcript: TranscriptRef, context: SummaryContext) -> SummaryResult:
        """Summarize a transcript. Cache key derived from transcript content + prompt version + model."""
        ...

@dataclass(frozen=True)
class SummaryContext:
    video_title: str
    video_description: str
    prompt_version: str           # Hash of prompt template content
    model: str

@dataclass(frozen=True)
class SummaryResult:
    video_id: str
    data: dict[str, Any]          # Structured summary JSON
    content_hash: str
    token_usage: TokenUsage | None
    latency_ms: int
```

### 3.3 Insights Provider

```python
class InsightsProviderPort(Protocol):
    def analyze(self, summary: SummaryData, video_meta: VideoMetadata) -> InsightsResult:
        """Generate AI-powered insights for comparison. Cache key from summary + prompt version."""
        ...

@dataclass(frozen=True)
class InsightsResult:
    video_id: str
    difficulty: str
    teaching_style: str
    practical_value: str
    content_depth: str
    learning_outcome: str
    target_audience: str
    prerequisites: str
    key_differentiators: str
    time_investment_worth: str
    token_usage: TokenUsage | None
```

### 3.4 Assignment Generator

```python
class AssignmentGeneratorPort(Protocol):
    def generate(self, summary: SummaryData, video_meta: VideoMetadata, template: str) -> AssignmentResult:
        """Generate educational assignment. Cache key from summary + prompt version + template."""
        ...

@dataclass(frozen=True)
class AssignmentResult:
    video_id: str
    markdown: str
    metadata: dict[str, Any]
    content_hash: str
    token_usage: TokenUsage | None
```

### 3.5 Quiz Generator

```python
class QuizGeneratorPort(Protocol):
    def generate_quiz(self, transcript: TranscriptRef, title: str) -> QuizResult:
        """Generate quiz from transcript. Cache key from transcript + prompt version + model."""
        ...

@dataclass(frozen=True)
class QuizResult:
    video_id: str
    content: str
    token_usage: TokenUsage | None
```

### 3.6 Cache Port

```python
class CachePort(Protocol):
    def get(self, key: CacheKey) -> bytes | None: ...
    def set(self, key: CacheKey, value: bytes, ttl: timedelta) -> None: ...
    def invalidate(self, key: CacheKey) -> None: ...
    def invalidate_namespace(self, namespace: str) -> None: ...

@dataclass(frozen=True)
class CacheKey:
    namespace: str                 # "transcript", "summary", "comparison", "assignment", "quiz"
    version: str                   # Semantic version of key schema (e.g., "v2")
    content_hash: str              # sha256(canonical_input)
    params_hash: str               # sha256(settings_json)
```

### 3.7 Usage Ledger Port

```python
class UsageLedgerPort(Protocol):
    def record(self, record: UsageRecord) -> None: ...
    def aggregate(self, *, provider: str | None = None, model: str | None = None,
                  operation: str | None = None, since: datetime | None = None,
                  until: datetime | None = None, cache_status: bool | None = None) -> UsageAggregate: ...
    def recent(self, limit: int = 100) -> list[UsageRecord]: ...

@dataclass(frozen=True)
class UsageRecord:
    request_id: str                # Correlation ID (UUID)
    run_id: str | None
    timestamp: datetime            # UTC
    provider: Literal["openai", "gemini", "youtube"]
    model: str
    operation: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    currency: Literal["USD"] = "USD"
    latency_ms: int
    success: bool
    error_category: str | None
    cache_hit: bool
    cache_key: str | None
    cache_namespace: str | None
    retry_count: int
    metadata: dict[str, Any]

@dataclass(frozen=True)
class UsageAggregate:
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    by_provider: dict[str, ProviderAggregate]
    by_operation: dict[str, OperationAggregate]
    by_cache_status: dict[str, CacheAggregate]
    time_range: TimeRange

@dataclass(frozen=True)
class ProviderAggregate:
    provider: str
    requests: int
    input_tokens: int
    output_tokens: int
    cost_usd: float

@dataclass(frozen=True)
class OperationAggregate:
    operation: str
    requests: int
    avg_latency_ms: float
    success_rate: float

@dataclass(frozen=True)
class CacheAggregate:
    cache_hit: bool
    requests: int
    saved_cost_usd: float

@dataclass(frozen=True)
class TimeRange:
    since: datetime
    until: datetime
```

### 3.8 Run Repository Port

```python
class RunRepositoryPort(Protocol):
    # Runs
    def create_run(self, *, run_id: str, search_query: str, normalized_query: str,
                   max_videos: int, transcript_language: str) -> str: ...
    def get_run(self, run_id: str) -> RunRecord | None: ...
    def list_runs(self) -> list[RunRecord]: ...
    def latest_run(self) -> RunRecord | None: ...
    def set_run_status(self, run_id: str, status: str, error: str | None = None) -> None: ...

    # Videos
    def set_videos(self, run_id: str, videos: list[dict]) -> None: ...
    def get_videos(self, run_id: str) -> list[dict]: ...
    def videos_state_hash(self, run_id: str) -> str: ...

    # Transcripts
    def upsert_transcripts(self, run_id: str, records: list[dict], settings: dict | None = None) -> None: ...
    def get_transcripts(self, run_id: str) -> list[dict]: ...
    def transcripts_state_hash(self, run_id: str) -> str: ...

    # Summaries
    def upsert_summaries(self, run_id: str, records: list[dict], settings: dict | None = None) -> None: ...
    def get_summaries(self, run_id: str) -> list[dict]: ...
    def summaries_state_hash(self, run_id: str) -> str: ...

    # Comparisons
    def set_comparison(self, run_id: str, payload: dict, settings: dict | None = None) -> None: ...
    def get_comparison(self, run_id: str) -> dict | None: ...

    # Assignments
    def upsert_assignments(self, run_id: str, records: list[dict], settings: dict | None = None) -> None: ...
    def get_assignments(self, run_id: str) -> list[dict]: ...

    # Generation jobs
    def start_job(self, run_id: str, kind: str, settings: dict | None = None) -> int: ...
    def finish_job(self, run_id: str, kind: str) -> None: ...
    def fail_job(self, run_id: str, kind: str, error: str | None = None) -> None: ...

    # Cache
    def get_cache_entry(self, cache_key: str) -> CacheEntry | None: ...
    def put_cache_entry(self, cache_key: str, kind: str, run_id: str, ttl_days: int = 30) -> None: ...
    def touch_cache_hit(self, cache_key: str) -> None: ...
    def find_cached_run(self, cache_key: str) -> CacheEntry | None: ...

    # Maintenance
    def recompute_run_hashes(self, run_id: str) -> None: ...
    def mark_stale_derived(self, run_id: str) -> None: ...
    def purge_expired(self, retention_days: int = 90) -> dict: ...
    def stats(self) -> dict: ...
```

---

## 4. Cache Model Details

### 4.1 Key Construction

```python
class CacheKeyBuilder:
    VERSION = "v2"

    def transcript_key(self, video_id: str, language: str, content_hash: str) -> CacheKey:
        return CacheKey(
            namespace="transcript",
            version=self.VERSION,
            content_hash=content_hash,  # sha256(transcript_text + language)
            params_hash=self._params_hash({"language": language}),
        )

    def summary_key(self, video_id: str, transcript_hash: str, prompt_version: str, model: str) -> CacheKey:
        return CacheKey(
            namespace="summary",
            version=self.VERSION,
            content_hash=sha256(f"{transcript_hash}:{prompt_version}:{model}"),
            params_hash=self._params_hash({"prompt_version": prompt_version, "model": model}),
        )

    def comparison_key(self, run_id: str, input_hash: str, prompt_version: str, model: str) -> CacheKey:
        return CacheKey(
            namespace="comparison",
            version=self.VERSION,
            content_hash=input_hash,
            params_hash=self._params_hash({"prompt_version": prompt_version, "model": model, "use_ai_insights": True}),
        )

    def assignment_key(self, video_id: str, summary_hash: str, prompt_version: str, model: str) -> CacheKey:
        return CacheKey(
            namespace="assignment",
            version=self.VERSION,
            content_hash=sha256(f"{summary_hash}:{prompt_version}:{model}"),
            params_hash=self._params_hash({"prompt_version": prompt_version, "model": model}),
        )

    def quiz_key(self, video_id: str, transcript_hash: str, prompt_version: str, model: str) -> CacheKey:
        return CacheKey(
            namespace="quiz",
            version=self.VERSION,
            content_hash=sha256(f"{transcript_hash}:{prompt_version}:{model}"),
            params_hash=self._params_hash({"prompt_version": prompt_version, "model": model}),
        )
```

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

Cache entries stored in SQLite `cache_entries` table extended with:
- `namespace` TEXT NOT NULL
- `version` TEXT NOT NULL
- `content_hash` TEXT NOT NULL
- `params_hash` TEXT NOT NULL
- `value` BLOB (compressed JSON via zlib)
- `created_at` TEXT NOT NULL
- `expires_at` TEXT NOT NULL
- `hit_count` INTEGER NOT NULL DEFAULT 0
- `last_hit_at` TEXT

---

## 5. Usage Ledger Model Details

### 5.1 SQL Schema (Migration)

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
    metadata TEXT,  -- JSON blob
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage_ledger(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_ledger(provider);
CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_ledger(model);
CREATE INDEX IF NOT EXISTS idx_usage_operation ON usage_ledger(operation);
CREATE INDEX IF NOT EXISTS idx_usage_run_id ON usage_ledger(run_id);
CREATE INDEX IF NOT EXISTS idx_usage_request_id ON usage_ledger(request_id);
```

### 5.2 Token Counting Strategy

- **OpenAI/OpenRouter**: Use `response.usage.prompt_tokens`, `response.usage.completion_tokens`, `response.usage.total_tokens` when available
- **Gemini**: Use `response.usage_metadata.prompt_token_count`, `response.usage_metadata.candidates_token_count` when available
- **Estimation fallback**: tiktoken encoding for known models when provider doesn't report usage
- **Cache hit**: input_tokens=0, output_tokens=0, cache_hit=True (no LLM call made)

### 5.3 Cost Calculation

```python
class CostCalculator:
    PRICING = {
        "openai/gpt-5-mini": {"input": 0.15, "output": 0.60},  # per 1M tokens
        "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "gemini-3.6-flash": {"input": 0.075, "output": 0.30},
    }

    def calculate(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        rates = self.PRICING.get(model, {"input": 0.0, "output": 0.0})
        return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
```

---

## 6. API Compatibility Assessment

**No breaking changes planned.** All existing endpoint paths, methods, request/response shapes, and status codes are preserved.

### Preserved Contracts

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

### New Endpoint (Backward Compatitive Addition)

| Endpoint | Method | Response | Description |
|----------|--------|----------|-------------|
| `/api/usage` | GET | `UsageAggregateResponse` | Aggregated usage/cost data with optional filters |

### Frontend Compatibility

- `frontend/src/lib/types.ts` interfaces mirror backend Pydantic schemas exactly
- No changes to existing types planned
- New types for usage aggregation will be added in a separate `types/usage.ts` file
- Existing `api.ts` functions will be preserved; new `usageApi` object added

---

## 7. Data Migration Strategy

### No Data Loss

1. **Existing SQLite database** (`data/atlas.sqlite3`) — preserved as-is; new migrations append tables
2. **Existing artifact files** (`data/artifacts/`, `pipeline_output_*/`) — preserved; read by `ArtifactFileStore`
3. **Existing cache entries** — preserved; new `cache_entries` columns added via migration

### New Tables (Appended Migrations)

- `usage_ledger` — new table, no migration needed
- `cache_entries` extended with `namespace`, `version`, `content_hash`, `params_hash`, `value` — migration alters existing table

### Reversibility

- Old `cache_entries` schema can be restored by dropping new columns (SQLite ALTER TABLE DROP COLUMN supported in 3.35+)
- `usage_ledger` can be dropped entirely if feature is reverted

---

## 8. Configuration

### Environment Variables (Preserved + Extended)

| Variable | Purpose | Required |
|----------|---------|----------|
| `OPENROUTER_API_KEY` | OpenAI API via OpenRouter | Yes (for summarization/comparison/assignments) |
| `YOUTUBE_API_KEY` | YouTube Data API | Yes (for search/playlist) |
| `GEMINI_API_KEY` | Google AI Studio / Gemini | Yes (for quiz generation) |
| `ATLAS_DB_PATH` | SQLite database path | No (default: `data/atlas.sqlite3`) |
| `ATLAS_ARTIFACT_ROOT` | Artifact storage root | No (default: `data/artifacts`) |
| `ATLAS_CACHE_TTL_DAYS` | Default cache TTL | No (default: 30) |
| `ATLAS_GOOGLE_CREDS_PATH` | Google OAuth credentials JSON | No (default: `credentials.json` in repo root) |
| `ATLAS_GOOGLE_TOKEN_PATH` | Google OAuth token JSON | No (default: `token.json` in repo root) |
| `ATLAS_DEV_SHUTDOWN_TOKEN` | Dev server shutdown token | No |
| `ATLAS_RETENTION_DAYS` | Artifact retention | No (default: 90) |

---

## 9. Testing Strategy

### Unit Tests

- Domain models and services (pure Python, no mocks of external systems)
- Cache key builder determinism
- Usage ledger aggregation correctness
- Comparison inference logic (difficulty, style, value)

### Integration Tests

- Each adapter with real or fixture-based external systems:
  - `SqlRunRepository` with in-memory SQLite
  - `SqlCacheAdapter` with in-memory SQLite
  - `OpenAISummarizerAdapter` with recorded HTTP fixtures (httpx)
  - `GeminiQuizProvider` with recorded HTTP fixtures
  - `YtDlpTranscriptProvider` with mock yt-dlp responses

### API Tests

- Existing endpoint contracts verified with pytest + httpx
- New `/api/usage` endpoint tested for aggregation correctness
- SSE streaming endpoint tested for proper event format

### Cache Tests

- Cold miss → populate → hot hit
- Different content → different key
- Changed parameters → different key
- Expired entry → miss
- Corrupted entry → miss + log
- Cache unavailable → graceful fallback
- Concurrent access (thread safety)

### Usage Tests

- Successful LLM call → record persisted
- Failed LLM call → error_category recorded
- Retry sequence → retry_count recorded
- Cache hit → zero tokens, cache_hit=True
- Cache miss → tokens recorded, cache_hit=False
- Aggregation correctness across time ranges

---

## 10. Performance Targets

| Path | Current Behavior | Target After Refactor |
|------|------------------|-----------------------|
| Repeated summary request | Re-calls OpenAI every time | Cache hit → zero LLM calls, <50ms response |
| Repeated comparison request | Re-calls OpenAI N times for insights | Cache hit → zero LLM calls, <100ms response |
| Repeated transcript request | Re-runs yt-dlp | Cache hit → zero network I/O, <10ms response |
| Repeated assignment request | Re-calls OpenAI N times | Cache hit → zero LLM calls, <50ms response |
| OpenAPI client creation | Creates new `OpenAI()` per thread per call | Reuse clients via dependency injection |
| Gemini client creation | Creates new `genai.Client` per request | Reuse clients via dependency injection |
| Config loading | Reloads YAML on first `get_config()` call | Single load at startup, passed via settings |

---

## 11. Migration Checklist

- [ ] Create `docs/ARCHITECTURE.md` (this document)
- [ ] Define all domain models and ports
- [ ] Implement `SqlRunRepository` with new schema
- [ ] Implement `SqlCacheAdapter` with content-derived keys
- [ ] Implement `SqlUsageLedger` with aggregation queries
- [ ] Implement OpenAI adapters with retry/backoff and instrumentation
- [ ] Implement Gemini adapter with retry/backoff and instrumentation
- [ ] Implement yt-dlp transcript provider
- [ ] Implement comparison inference service
- [ ] Implement application use cases
- [ ] Slim FastAPI routers to use application layer
- [ ] Add `/api/usage` endpoint
- [ ] Add frontend usage dashboard
- [ ] Delete `app.py` (Gradio legacy)
- [ ] Remove `src/youtube_pipeline.py`, `src/summarize_youtube_transcript.py`, etc. (migrated to new modules)
- [ ] Remove `os.environ[...]` mutations from services
- [ ] Make Google OAuth paths configurable
- [ ] Run `uv run ruff check .`
- [ ] Run `uv run pytest`
- [ ] Verify `pipeline_output_*` artifacts intact
- [ ] Verify `data/atlas.sqlite3` migrated successfully

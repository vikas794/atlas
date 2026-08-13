from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import Depends

from src.application.ports.provider_ports import (
    RunRepositoryPort,
    CachePort,
    UsageLedgerPort,
    TranscriptProviderPort,
    SummarizerPort,
    InsightsProviderPort,
    AssignmentGeneratorPort,
    QuizGeneratorPort,
    ArtifactStorePort,
)
from src.application.use_cases import (
    SearchPipelineUseCase,
    GenerateTranscriptsUseCase,
    GenerateSummariesUseCase,
    GenerateComparisonUseCase,
    GenerateAssignmentsUseCase,
    GenerateQuizUseCase,
)
from src.config import load_settings, PromptRegistry, ModelRegistry
from src.infrastructure.cache.sql_cache import SqlCacheAdapter
from src.infrastructure.llm.base import SettingsLoader
from src.infrastructure.llm.openai.adapter import (
    OpenAISummarizerAdapter,
    OpenAIInsightsProvider,
    OpenAIAssignmentAdapter,
)
from src.infrastructure.llm.gemini.adapter import GeminiQuizProvider
from src.infrastructure.storage.sql import SqlRunRepository, SqlUsageLedger
from src.infrastructure.storage.filesystem import ArtifactFileStore
from src.infrastructure.transcript.ytdlp.provider import YtDlpTranscriptProvider
from src.infrastructure.youtube.search import YouTubeDataApiSearchProvider
from src.infrastructure.youtube.playlist import YouTubePlaylistProvider
from src.infrastructure.google.drive import GoogleDriveExporter


@lru_cache(maxsize=1)
def get_settings_loader() -> SettingsLoader:
    """Get the application settings loader (singleton)."""
    settings = load_settings()
    return settings.to_loader()


@lru_cache(maxsize=1)
def get_prompt_registry() -> PromptRegistry:
    """Get the prompt registry (singleton)."""
    settings = load_settings()
    return PromptRegistry(settings.prompts_base_dir, get_settings_loader())


@lru_cache(maxsize=1)
def get_model_registry() -> ModelRegistry:
    """Get the model registry (singleton)."""
    return ModelRegistry()


def get_run_repository() -> RunRepositoryPort:
    """Get the run repository (request-scoped via Depends)."""
    settings = get_storage_settings()
    return SqlRunRepository(settings["database_path"], settings["artifact_root"])


def get_cache() -> CachePort:
    """Get the cache adapter (request-scoped via Depends)."""
    settings = get_storage_settings()
    return SqlCacheAdapter(settings["database_path"])


def get_usage_ledger() -> UsageLedgerPort:
    """Get the usage ledger (request-scoped via Depends)."""
    settings = get_storage_settings()
    return SqlUsageLedger(settings["database_path"])


def get_artifact_store() -> ArtifactStorePort:
    """Get the artifact store (request-scoped via Depends)."""
    settings = get_storage_settings()
    return ArtifactFileStore(settings["artifact_root"])


def get_settings_loader_dep() -> SettingsLoader:
    """FastAPI dependency for settings loader."""
    return get_settings_loader()


def get_search_use_case(
    repo: RunRepositoryPort = Depends(get_run_repository),
    cache: CachePort = Depends(get_cache),
    settings: SettingsLoader = Depends(get_settings_loader_dep),
    ledger: UsageLedgerPort = Depends(get_usage_ledger),
) -> SearchPipelineUseCase:
    return SearchPipelineUseCase(run_repository=repo, cache=cache, settings=settings, usage_ledger=ledger)


def get_transcripts_use_case(
    repo: RunRepositoryPort = Depends(get_run_repository),
    cache: CachePort = Depends(get_cache),
    settings: SettingsLoader = Depends(get_settings_loader_dep),
    ledger: UsageLedgerPort = Depends(get_usage_ledger),
    artifact_store: ArtifactStorePort = Depends(get_artifact_store),
) -> GenerateTranscriptsUseCase:
    return GenerateTranscriptsUseCase(
        run_repository=repo,
        cache=cache,
        settings=settings,
        usage_ledger=ledger,
        artifact_store=artifact_store,
    )


def get_summaries_use_case(
    repo: RunRepositoryPort = Depends(get_run_repository),
    cache: CachePort = Depends(get_cache),
    settings: SettingsLoader = Depends(get_settings_loader_dep),
    ledger: UsageLedgerPort = Depends(get_usage_ledger),
) -> GenerateSummariesUseCase:
    return GenerateSummariesUseCase(
        run_repository=repo,
        cache=cache,
        settings=settings,
        usage_ledger=ledger,
    )


def get_comparison_use_case(
    repo: RunRepositoryPort = Depends(get_run_repository),
    cache: CachePort = Depends(get_cache),
    settings: SettingsLoader = Depends(get_settings_loader_dep),
    ledger: UsageLedgerPort = Depends(get_usage_ledger),
) -> GenerateComparisonUseCase:
    return GenerateComparisonUseCase(
        run_repository=repo,
        cache=cache,
        settings=settings,
        usage_ledger=ledger,
    )


def get_assignments_use_case(
    repo: RunRepositoryPort = Depends(get_run_repository),
    cache: CachePort = Depends(get_cache),
    settings: SettingsLoader = Depends(get_settings_loader_dep),
    ledger: UsageLedgerPort = Depends(get_usage_ledger),
) -> GenerateAssignmentsUseCase:
    return GenerateAssignmentsUseCase(
        run_repository=repo,
        cache=cache,
        settings=settings,
        usage_ledger=ledger,
    )


def get_quiz_use_case(
    repo: RunRepositoryPort = Depends(get_run_repository),
    cache: CachePort = Depends(get_cache),
    settings: SettingsLoader = Depends(get_settings_loader_dep),
    ledger: UsageLedgerPort = Depends(get_usage_ledger),
) -> GenerateQuizUseCase:
    return GenerateQuizUseCase(
        run_repository=repo,
        cache=cache,
        settings=settings,
        usage_ledger=ledger,
    )


def get_storage_settings() -> dict[str, Any]:
    """Get storage settings from config."""
    from src.config import get_storage_settings as _get_storage_settings
    return _get_storage_settings(load_settings())
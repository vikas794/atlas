"""Composition root: builds the concrete adapters and wires them into use cases (PRD section 21).

This is the only place that knows both the application ports and the infrastructure that
implements them. Everything is built lazily on first use, so importing the app never needs an
API key; a missing key surfaces as a clear ``ConfigurationError`` (HTTP 503 ``CONFIG_MISSING``)
at the moment the feature is used, and a missing key is never cached.
"""

from __future__ import annotations

from functools import lru_cache, partial
from pathlib import Path
from typing import Any

from src.application.use_cases.playlist.actions import (
    ApproveAllUseCase,
    ApproveVideoUseCase,
    CleanupTranscriptsUseCase,
    DeletePlaylistUseCase,
    RegenerateVideoUseCase,
    ReprocessPlaylistUseCase,
    RetryVideoUseCase,
    SaveSummaryEditUseCase,
)
from src.application.use_cases.playlist.ingest import IngestPlaylistUseCase
from src.application.use_cases.playlist.process_video import ProcessVideoUseCase
from src.application.use_cases.playlist.publish import PublishPlaylistUseCase
from src.application.worker import PlaylistWorker
from src.config import load_settings
from src.config.settings import SettingsLoader, repo_root
from src.domain.exceptions import ConfigurationError
from src.domain.interfaces.drive_publisher import DrivePublisherPort
from src.domain.interfaces.notes_generator import NotesGeneratorPort
from src.domain.interfaces.playlist_provider import PlaylistProviderPort
from src.domain.interfaces.playlist_repository import PlaylistRepositoryPort
from src.domain.interfaces.transcript_provider import TranscriptProviderPort
from src.domain.interfaces.usage_ledger import UsageLedgerPort
from src.domain.services.sync_diff import PlaylistEntry
from src.infrastructure.google.drive import GoogleDriveExporter
from src.infrastructure.google.publisher import GoogleDrivePublisher
from src.infrastructure.llm.factory import build_notes_generator
from src.infrastructure.storage.playlist_repo import PlaylistRepository
from src.infrastructure.storage.sql import SqlUsageLedger
from src.shared.api_keys import reset_pools
from src.shared.files import delete_files


@lru_cache(maxsize=1)
def get_settings_loader() -> SettingsLoader:
    """Get the application settings loader (singleton)."""
    return load_settings().to_loader()


def get_settings_loader_dep() -> SettingsLoader:
    """FastAPI dependency for settings loader."""
    return get_settings_loader()


def get_storage_settings() -> dict[str, Any]:
    """Get storage settings from config."""
    from src.config import get_storage_settings as _get_storage_settings

    return _get_storage_settings(load_settings())


def _resolve_path(raw: str) -> str:
    """Relative configured paths resolve against the repository root, never the CWD."""
    path = Path(raw)
    return str(path if path.is_absolute() else repo_root() / path)


def get_usage_ledger() -> UsageLedgerPort:
    """Get the usage ledger (request-scoped via Depends)."""
    return SqlUsageLedger(get_storage_settings()["database_path"])


@lru_cache(maxsize=1)
def get_playlist_repo() -> PlaylistRepositoryPort:
    """Get the shared playlist repository (runs migrations on first use)."""
    return PlaylistRepository(get_storage_settings()["database_path"])


@lru_cache(maxsize=1)
def get_transcript_provider() -> TranscriptProviderPort:
    """Get the transcript provider."""
    from src.infrastructure.transcript.ytdlp.provider import YtDlpTranscriptProvider

    return YtDlpTranscriptProvider(
        get_settings_loader(),
        usage_ledger=get_usage_ledger(),
    )


@lru_cache(maxsize=1)
def get_notes_generator() -> NotesGeneratorPort:
    """Get the notes generator named by ``NOTES_PROVIDER`` (no retries inside; see the factory)."""
    try:
        return build_notes_generator(
            settings_loader=get_settings_loader(),
            usage_ledger=get_usage_ledger(),
        )
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc


_youtube_provider: PlaylistProviderPort | None = None


def get_youtube_provider() -> PlaylistProviderPort:
    """Get the YouTube playlist provider.

    Raises ``ConfigurationError`` (HTTP 503 ``CONFIG_MISSING``) naming the env var when the
    API key is not set. Failure is **not cached**: the settings (and ``.env``) are re-read on
    the next call, so adding the key takes effect without a restart.
    """
    global _youtube_provider
    if _youtube_provider is None:
        from src.infrastructure.youtube.playlist import YouTubePlaylistProvider

        settings = load_settings()
        _youtube_provider = YouTubePlaylistProvider(api_key_env=settings.youtube_api_key_env)
    return _youtube_provider


class _LazyYouTubeProvider:
    """Resolves the real provider on first use, not when the use case is built.

    A request is validated (e.g. 400 for a bad URL) *before* anything needs the API key, so a
    missing ``YOUTUBE_API_KEY`` only fails a request that actually has to talk to YouTube.
    """

    def get_playlist_title(self, playlist_id: str) -> str:
        return get_youtube_provider().get_playlist_title(playlist_id)

    def fetch_playlist_videos(self, playlist_id: str) -> list[PlaylistEntry]:
        return get_youtube_provider().fetch_playlist_videos(playlist_id)


def get_process_video_use_case() -> ProcessVideoUseCase:
    """Get ProcessVideoUseCase."""
    settings = load_settings()
    return ProcessVideoUseCase(
        repo=get_playlist_repo(),
        transcript_provider=get_transcript_provider(),
        notes_generator=get_notes_generator(),
        max_auto_retries=settings.max_auto_retries,
        backoff_base_seconds=settings.backoff_base_seconds,
        backoff_cap_seconds=settings.backoff_cap_seconds,
    )


def get_ingest_playlist_use_case() -> IngestPlaylistUseCase:
    """Get IngestPlaylistUseCase."""
    return IngestPlaylistUseCase(
        repo=get_playlist_repo(),
        youtube_provider=_LazyYouTubeProvider(),
        usage_ledger=get_usage_ledger(),
    )


def get_approve_video_use_case() -> ApproveVideoUseCase:
    return ApproveVideoUseCase(repo=get_playlist_repo())


def get_approve_all_use_case() -> ApproveAllUseCase:
    return ApproveAllUseCase(repo=get_playlist_repo())


def get_save_summary_edit_use_case() -> SaveSummaryEditUseCase:
    return SaveSummaryEditUseCase(repo=get_playlist_repo())


def get_retry_video_use_case() -> RetryVideoUseCase:
    return RetryVideoUseCase(repo=get_playlist_repo())


def get_regenerate_video_use_case() -> RegenerateVideoUseCase:
    return RegenerateVideoUseCase(repo=get_playlist_repo())


def _artifact_deleter() -> Any:
    """Deletes raw subtitle files, but only inside the configured transcripts folder."""
    return partial(delete_files, root=get_storage_settings()["transcripts_dir"])


def get_reprocess_playlist_use_case() -> ReprocessPlaylistUseCase:
    return ReprocessPlaylistUseCase(repo=get_playlist_repo(), delete_artifacts=_artifact_deleter())


def get_delete_playlist_use_case() -> DeletePlaylistUseCase:
    return DeletePlaylistUseCase(repo=get_playlist_repo(), delete_artifacts=_artifact_deleter())


def get_cleanup_transcripts_use_case() -> CleanupTranscriptsUseCase:
    return CleanupTranscriptsUseCase(
        repo=get_playlist_repo(), delete_artifacts=_artifact_deleter()
    )


@lru_cache(maxsize=1)
def get_drive_exporter() -> GoogleDriveExporter:
    """The shared Google Drive exporter (credential and token paths from settings)."""
    settings = load_settings()
    return GoogleDriveExporter(
        creds_path=_resolve_path(settings.google_creds_path),
        token_path=_resolve_path(settings.google_token_path),
    )


@lru_cache(maxsize=1)
def get_drive_publisher() -> DrivePublisherPort:
    """The shared Drive publisher; retry limits come from settings."""
    settings = load_settings()
    return GoogleDrivePublisher(
        exporter=get_drive_exporter(),
        max_retries=settings.drive_max_retries,
        backoff_base=settings.drive_backoff_base_seconds,
        backoff_cap=settings.drive_backoff_cap_seconds,
    )


def get_publish_playlist_use_case() -> PublishPlaylistUseCase:
    return PublishPlaylistUseCase(
        repo=get_playlist_repo(),
        publisher=get_drive_publisher(),
    )


_worker: PlaylistWorker | None = None


def get_worker() -> PlaylistWorker:
    """Get or create the singleton PlaylistWorker (started by the app lifespan)."""
    global _worker
    if _worker is None:
        settings = load_settings()
        _worker = PlaylistWorker(
            repo=get_playlist_repo(),
            process_video=get_process_video_use_case(),
            publish=get_publish_playlist_use_case(),
            max_concurrent_videos=settings.max_concurrent_videos,
            poll_interval_seconds=settings.worker_poll_interval_seconds,
            shutdown_timeout_seconds=settings.worker_shutdown_timeout_seconds,
        )
    return _worker


#: The cached factories, captured at import so a test that monkeypatches a module attribute
#: cannot make the reset miss (or choke on) the original.
_CACHED_FACTORIES = (
    get_settings_loader,
    get_playlist_repo,
    get_transcript_provider,
    get_notes_generator,
    get_drive_exporter,
    get_drive_publisher,
)


def reset_dependency_caches() -> None:
    """Forget every cached singleton (tests, and re-reading settings after an env change)."""
    global _worker, _youtube_provider
    _worker = None
    _youtube_provider = None
    reset_pools()
    for cached in _CACHED_FACTORIES:
        cached.cache_clear()

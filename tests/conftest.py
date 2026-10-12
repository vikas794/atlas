"""Shared pytest fixtures.

The fixtures here deliberately touch only `src.infrastructure.storage.db`
(connect/initialize). Migrations are owned elsewhere and are used as-is.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

# Tests never append to the developer's real logs/atlas.log (backend.main configures logging
# at import, and file logging is on by default). Set before anything imports the app.
os.environ.setdefault("ATLAS_LOG_FILE_LOGGING", "false")

from src.infrastructure.storage import db  # noqa: E402


@pytest.fixture
def clean_atlas_env() -> Iterator[None]:
    """Remove ATLAS_* overrides for the duration of a test.

    Keeps settings assertions deterministic when a developer (or CI) has
    ATLAS_* variables exported in their shell or in the repo `.env`.
    """
    saved = {k: v for k, v in os.environ.items() if k.startswith("ATLAS_")}
    for key in saved:
        del os.environ[key]
    try:
        yield
    finally:
        os.environ.update(saved)


@pytest.fixture(autouse=True)
def _isolated_database(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """No test may touch the developer's real ``data/atlas.sqlite3``.

    Anything that builds the app (``TestClient(app)``, the dependency factories) gets a
    throwaway database, and the cached singletons are rebuilt around every test.
    """
    from src.transport.http.fastapi import dependencies as deps

    path = tmp_path_factory.mktemp("isolated_db") / "atlas.sqlite3"
    monkeypatch.setenv("ATLAS_DATABASE_PATH", str(path))
    deps.reset_dependency_caches()
    yield
    deps.reset_dependency_caches()


@pytest.fixture
def temp_db(tmp_path: Path) -> Iterator[Path]:
    """Create and migrate a throwaway SQLite database, yielding its path."""
    db_path = tmp_path / "atlas.sqlite3"
    db.initialize(db_path)
    yield db_path


@pytest.fixture
def initialized_db(temp_db: Path) -> Iterator[sqlite3.Connection]:
    """Yield an open connection to the migrated temporary database."""
    conn = db.connect(temp_db)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# HTTP API harness shared by the API tests
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any  # noqa: E402

from src.application.worker import PlaylistWorker  # noqa: E402
from src.domain.interfaces.drive_publisher import PublishResult  # noqa: E402
from src.domain.interfaces.notes_generator import GeneratedNotes  # noqa: E402
from src.domain.interfaces.transcript_provider import TranscriptResult  # noqa: E402
from src.domain.services.sync_diff import PlaylistEntry  # noqa: E402


class FakeYouTube:
    """Stands in for the YouTube provider; shaped exactly like the real one (PlaylistEntry)."""

    def __init__(self) -> None:
        self.title = "Test Course"
        self.videos = [PlaylistEntry("vid_1", "Lesson 1", 1), PlaylistEntry("vid_2", "Lesson 2", 2)]

    def get_playlist_title(self, playlist_id: str) -> str:
        return self.title

    def fetch_playlist_videos(self, playlist_id: str) -> list[PlaylistEntry]:
        return list(self.videos)


class FakeTranscripts:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch_transcript(self, video_id: str, language: str = "en") -> TranscriptResult:
        self.calls.append(video_id)
        text = f"transcript {video_id}"
        return TranscriptResult(video_id, language, "", text, "h", "", len(text))


class FakeNotes:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate_notes(self, *, title: str, transcript: str) -> GeneratedNotes:
        self.calls.append(title)
        body = f"## Notes\n\nNotes for {title}\n\n## Key Points\n\n- point"
        return GeneratedNotes(body, "fake", "fake-model", "notes_test")


class FakePublisher:
    """Port-level publisher for API tests: records calls, never touches Google."""

    def __init__(self) -> None:
        self.authorized = True
        self.calls = 0
        self.fail: str | None = None

    async def ensure_authorized(self) -> None:
        from src.domain.interfaces.drive_publisher import DriveAuthorizationRequired

        if not self.authorized:
            raise DriveAuthorizationRequired("Google Drive is not authorized.")

    async def publish_playlist(self, *, entries, progress=None, existing_folder_id=None,
                               existing_index_doc_id=None, **_: Any) -> PublishResult:
        from src.domain.models.playlist import FailureType, PipelineError

        self.calls += 1
        if self.fail:
            raise PipelineError(FailureType.DRIVE_ERROR, self.fail)
        folder = existing_folder_id or "folder-1"
        if not existing_folder_id:
            await progress.folder_created(folder)
        index = existing_index_doc_id or "index-1"
        if not existing_index_doc_id:
            await progress.index_doc_created(index)
        await progress.index_written()
        ids: dict[int, str] = {}
        for entry in entries:
            doc = entry.existing_doc_id or f"doc-{entry.video_pk}"
            if not entry.existing_doc_id:
                await progress.doc_created(entry.video_pk, doc)
            await progress.doc_published(entry.video_pk, doc, created=not entry.existing_doc_id)
            ids[entry.video_pk] = doc
        return PublishResult(folder, index, created=len(ids), doc_ids=ids)


class _IdleWorker(PlaylistWorker):
    """The real worker class (real wake/enqueue_publish) with the background loop disabled,
    so API tests control exactly when pipeline work happens."""

    async def start(self) -> None:  # noqa: D102
        return None

    async def stop(self) -> None:  # noqa: D102
        return None


@dataclass
class ApiEnv:
    youtube: FakeYouTube = field(default_factory=FakeYouTube)
    transcripts: FakeTranscripts = field(default_factory=FakeTranscripts)
    notes: FakeNotes = field(default_factory=FakeNotes)
    publisher: FakePublisher = field(default_factory=FakePublisher)
    db_path: Path | None = None
    transcripts_dir: Path | None = None


@pytest.fixture
def api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ApiEnv:
    """Isolated app configuration: temp DB, fake integrations, real use cases and routers."""
    from src.transport.http.fastapi import dependencies as deps

    env = ApiEnv(db_path=tmp_path / "api.sqlite3", transcripts_dir=tmp_path / "transcripts")
    monkeypatch.setenv("ATLAS_DATABASE_PATH", str(env.db_path))
    monkeypatch.setenv("ATLAS_TRANSCRIPT_OUTPUT_FOLDER", str(env.transcripts_dir))
    monkeypatch.setenv("ATLAS_WORKER_POLL_INTERVAL_SECONDS", "0.05")
    monkeypatch.setenv("ATLAS_BACKOFF_BASE_SECONDS", "0")
    monkeypatch.setattr(deps, "get_youtube_provider", lambda: env.youtube)
    monkeypatch.setattr(deps, "get_transcript_provider", lambda: env.transcripts)
    monkeypatch.setattr(deps, "get_notes_generator", lambda: env.notes)
    monkeypatch.setattr(deps, "get_drive_publisher", lambda: env.publisher)
    deps.reset_dependency_caches()
    yield env
    deps.reset_dependency_caches()


@pytest.fixture
def idle_worker(monkeypatch: pytest.MonkeyPatch, api_env: ApiEnv) -> PlaylistWorker:
    """Replace the singleton worker by the real class with its loop disabled."""
    import backend.main as main_module
    from src.transport.http.fastapi import dependencies as deps

    worker = _IdleWorker(
        repo=deps.get_playlist_repo(),
        process_video=deps.get_process_video_use_case(),
        publish=deps.get_publish_playlist_use_case(),
        poll_interval_seconds=0.05,
    )
    from backend.main import app

    original_get_worker = deps.get_worker  # the object the routers' Depends() refer to
    monkeypatch.setattr(deps, "get_worker", lambda: worker)
    monkeypatch.setattr(main_module, "get_worker", lambda: worker)
    app.dependency_overrides[original_get_worker] = lambda: worker
    yield worker
    app.dependency_overrides.pop(original_get_worker, None)


async def drain_pipeline(worker: PlaylistWorker, rounds: int = 20) -> None:
    """Run the worker's tick until nothing is runnable (deterministic, no sleeping loops)."""
    for _ in range(rounds):
        started = await worker._tick()
        await asyncio.gather(*list(worker._active_tasks), *list(worker._publish_tasks))
        if not started:
            return

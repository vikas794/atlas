"""The real PlaylistWorker loop against a real database (PRD section 8, 15, 16).

These tests drive the actual worker (not a mock) so that a regression such as a renamed
method, a hot retry loop or a leaked task shows up here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from src.application.use_cases.playlist.process_video import ProcessVideoUseCase
from src.application.worker import PlaylistWorker
from src.domain.interfaces.notes_generator import GeneratedNotes
from src.domain.interfaces.transcript_provider import TranscriptResult
from src.domain.models.playlist import FailureKind, FailureType, PipelineError, Stage
from src.domain.services.sync_diff import PlaylistEntry
from src.infrastructure.llm.litellm_notes import LiteLLMNotesGenerator
from src.infrastructure.storage import db
from src.infrastructure.storage.playlist_repo import PlaylistRepository

GOOD_NOTES = "## Notes\n\n" + "- a detail worth remembering\n" * 4 + "\n## Key Points\n\n- one\n"


@pytest.fixture
def repo(tmp_path: Path) -> PlaylistRepository:
    db_path = tmp_path / "worker.sqlite3"
    db.initialize(db_path)
    return PlaylistRepository(db_path)


async def add_videos(repo: PlaylistRepository, count: int = 1):
    playlist = await repo.create_playlist("PL1", "https://youtube.com/playlist?list=PL1")
    videos = await repo.insert_videos(
        playlist.id, [PlaylistEntry(f"v{i}", f"Video {i}", i) for i in range(1, count + 1)]
    )
    return playlist, videos


class Transcripts:
    def __init__(self, *, fail_with: Exception | None = None, delay: float = 0.0) -> None:
        self.calls: list[str] = []
        self.fail_with = fail_with
        self.delay = delay
        self.running = 0
        self.max_running = 0

    async def fetch_transcript(self, video_id: str, language: str = "en") -> TranscriptResult:
        self.calls.append(video_id)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail_with:
                raise self.fail_with
            text = f"transcript of {video_id}"
            return TranscriptResult(video_id, language, "", text, "hash", "", len(text))
        finally:
            self.running -= 1


class Notes:
    def __init__(self, *, fail_times: int = 0, error: Exception | None = None) -> None:
        self.calls = 0
        self.fail_times = fail_times
        self.error = error or PipelineError(FailureType.RATE_LIMITED, "rate limited")

    async def generate_notes(self, *, title: str, transcript: str) -> GeneratedNotes:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error
        return GeneratedNotes(GOOD_NOTES, "fake", "fake-model", "v1")


async def wait_until(
    predicate: Callable[[], Awaitable[bool]], timeout: float = 6.0, interval: float = 0.02
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met in time")


def make_worker(repo: PlaylistRepository, transcripts: Any, notes: Any, **kwargs: Any):
    process = ProcessVideoUseCase(
        repo,
        transcripts,
        notes,
        max_auto_retries=kwargs.pop("max_auto_retries", 2),
        backoff_base_seconds=0.0,  # retries become due immediately: no real sleeping
    )
    return PlaylistWorker(repo, process, poll_interval_seconds=0.02, **kwargs)


# --------------------------------------------------------------------------- B2


def test_the_worker_exposes_the_method_the_routers_call() -> None:
    """Routers call worker.wake() and worker.enqueue_publish(); a rename must fail here."""
    for name in ("wake", "enqueue_publish", "start", "stop"):
        assert callable(getattr(PlaylistWorker, name)), name
    assert not hasattr(PlaylistWorker, "trigger")


async def test_wake_makes_a_sleeping_worker_pick_up_new_work_immediately(
    repo: PlaylistRepository,
) -> None:
    transcripts, notes = Transcripts(), Notes()
    process = ProcessVideoUseCase(repo, transcripts, notes)
    worker = PlaylistWorker(repo, process, poll_interval_seconds=30.0)  # would sleep 30s
    await worker.start()
    try:
        await asyncio.sleep(0.15)  # the idle loop is now waiting on the event
        _, videos = await add_videos(repo)
        worker.wake()

        async def done() -> bool:
            return (await repo.get_video(videos[0].id)).stage == Stage.PENDING_REVIEW

        await wait_until(done, timeout=3.0)
    finally:
        await worker.stop()


# --------------------------------------------------------------------------- B3


async def test_an_exhausted_video_is_never_redispatched_by_the_worker_loop(
    repo: PlaylistRepository,
) -> None:
    """Used to hot-loop forever (NULL next_retry_at was re-selected on every tick)."""
    _, videos = await add_videos(repo)
    transcripts = Transcripts(fail_with=PipelineError(FailureType.RATE_LIMITED, "429"))
    worker = make_worker(repo, transcripts, Notes(), max_auto_retries=2)

    await worker.start()
    try:
        async def exhausted() -> bool:
            video = await repo.get_video(videos[0].id)
            return video.stage == Stage.FAILED and video.retries_exhausted

        await wait_until(exhausted)
        calls_when_exhausted = len(transcripts.calls)
        await asyncio.sleep(0.6)  # ~30 poll ticks with nothing left to do
    finally:
        await worker.stop()

    assert calls_when_exhausted == 3  # 1 attempt + 2 automatic retries
    assert len(transcripts.calls) == 3  # not a single extra dispatch
    video = await repo.get_video(videos[0].id)
    assert video.failure_kind == FailureKind.TRANSIENT and video.next_retry_at is None
    assert await repo.list_transient_due() == []


async def test_a_manual_retry_runs_an_exhausted_video_again(repo: PlaylistRepository) -> None:
    from src.application.use_cases.playlist.actions import RetryVideoUseCase

    _, videos = await add_videos(repo)
    transcripts = Transcripts(fail_with=PipelineError(FailureType.RATE_LIMITED, "429"))
    worker = make_worker(repo, transcripts, Notes(), max_auto_retries=1)
    await worker.start()
    try:
        async def exhausted() -> bool:
            return (await repo.get_video(videos[0].id)).retries_exhausted

        await wait_until(exhausted)
        assert len(transcripts.calls) == 2

        transcripts.fail_with = None  # the provider recovers
        await RetryVideoUseCase(repo).execute(videos[0].id)
        worker.wake()

        async def done() -> bool:
            return (await repo.get_video(videos[0].id)).stage == Stage.PENDING_REVIEW

        await wait_until(done)
    finally:
        await worker.stop()


async def test_transient_failures_recover_through_the_worker(repo: PlaylistRepository) -> None:
    _, videos = await add_videos(repo)
    transcripts, notes = Transcripts(), Notes(fail_times=2)
    worker = make_worker(repo, transcripts, notes, max_auto_retries=3)

    await worker.start()
    try:
        async def done() -> bool:
            return (await repo.get_video(videos[0].id)).stage == Stage.PENDING_REVIEW

        await wait_until(done)
    finally:
        await worker.stop()

    video = await repo.get_video(videos[0].id)
    assert notes.calls == 3  # failed, failed, succeeded
    assert transcripts.calls == ["v1"]  # the transcript was fetched once and reused
    assert video.failure_type is None and video.failure_reason is None
    assert video.retry_count == 2 and video.next_retry_at is None


class FakeCompletion:
    """``litellm.acompletion`` double that always fails with a rate limit; counts provider calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, **_: Any) -> Any:
        self.calls += 1
        raise RuntimeError("429 Too Many Requests: slow down")


async def test_total_llm_calls_per_video_are_bounded_by_one_retry_layer(
    repo: PlaylistRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retries used to multiply (adapter wrapper x use case = up to 20 calls)."""
    from src.config.settings import AtlasSettings
    from src.shared.api_keys import KeyPool, reset_pools

    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key-not-real")
    # Parking the single key after a 429 would short-circuit later attempts; this test counts
    # how many provider calls the pipeline's own retry layer makes.
    monkeypatch.setattr(KeyPool, "cool_down", lambda self, key, seconds=0: None)
    reset_pools()

    _, videos = await add_videos(repo)
    client = FakeCompletion()
    generator = LiteLLMNotesGenerator(AtlasSettings().to_loader(), completion=client)
    worker = make_worker(repo, Transcripts(), generator, max_auto_retries=3)

    await worker.start()
    try:
        async def exhausted() -> bool:
            return (await repo.get_video(videos[0].id)).retries_exhausted

        await wait_until(exhausted)
        await asyncio.sleep(0.2)
    finally:
        await worker.stop()

    assert client.calls == 4  # 1 + max_auto_retries, never more
    video = await repo.get_video(videos[0].id)
    assert video.failure_type == FailureType.RATE_LIMITED


# ------------------------------------------------------------------ concurrency


async def test_concurrency_is_bounded_and_every_video_still_completes(
    repo: PlaylistRepository,
) -> None:
    _, videos = await add_videos(repo, count=6)

    transcripts = Transcripts()
    worker = make_worker(repo, transcripts, Notes(), max_concurrent_videos=2)

    await worker.start()
    try:
        async def all_done() -> bool:
            stages = [(await repo.get_video(v.id)).stage for v in videos]
            return all(stage == Stage.PENDING_REVIEW for stage in stages)

        await wait_until(all_done)
    finally:
        await worker.stop()

    assert sorted(transcripts.calls) == [f"v{i}" for i in range(1, 7)]


async def test_transcripts_keep_downloading_while_notes_are_pending(
    repo: PlaylistRepository,
) -> None:
    _, videos = await add_videos(repo, count=4)
    release = asyncio.Event()

    class SlowNotes(Notes):
        async def generate_notes(self, *, title: str, transcript: str) -> GeneratedNotes:
            await release.wait()
            return await super().generate_notes(title=title, transcript=transcript)

    transcripts = Transcripts()
    worker = make_worker(repo, transcripts, SlowNotes(), max_concurrent_videos=1)
    await worker.start()
    try:
        async def all_transcripts_stored() -> bool:
            return len(transcripts.calls) == len(videos)

        # Video 1's AI step is blocked, yet every transcript still downloads.
        await wait_until(all_transcripts_stored)
        assert (await repo.get_video(videos[0].id)).stage == Stage.GENERATING_NOTES
        release.set()

        async def all_done() -> bool:
            stages = [(await repo.get_video(v.id)).stage for v in videos]
            return all(stage == Stage.PENDING_REVIEW for stage in stages)

        await wait_until(all_done)
    finally:
        release.set()
        await worker.stop()


# ------------------------------------------------------------------------- stop


async def test_stop_cancels_a_hanging_job_after_the_grace_period(
    repo: PlaylistRepository,
) -> None:
    _, videos = await add_videos(repo)
    transcripts = Transcripts(delay=300.0)  # would block shutdown for five minutes
    worker = make_worker(repo, transcripts, Notes(), shutdown_timeout_seconds=0.2)
    await worker.start()

    async def started() -> bool:
        return bool(transcripts.calls)

    await wait_until(started)

    loop = asyncio.get_running_loop()
    began = loop.time()
    await worker.stop()

    assert loop.time() - began < 3.0
    # The interrupted video is left mid-stage; the next startup re-queues it.
    assert (await repo.get_video(videos[0].id)).stage == Stage.FETCHING_TRANSCRIPT
    assert await repo.recover_in_flight_videos() == 1
    assert (await repo.get_video(videos[0].id)).stage == Stage.QUEUED

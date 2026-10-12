"""Integration tests for playlist use cases and the pipeline (PRD M1, M2, section 15, 20).

Everything here runs the real use cases against a real migrated SQLite database; only the
network-facing ports (YouTube listing, transcript fetch, LLM) are replaced by scripted fakes.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from src.application.use_cases.playlist.actions import (
    ApproveAllUseCase,
    ApproveVideoUseCase,
    CleanupTranscriptsUseCase,
    RegenerateVideoUseCase,
    SaveSummaryEditUseCase,
)
from src.application.use_cases.playlist.ingest import IngestPlaylistUseCase
from src.application.use_cases.playlist.process_video import (
    INTERNAL_ERROR_REASON,
    ProcessVideoUseCase,
)
from src.application.worker import PlaylistWorker
from src.domain.exceptions import ConfigurationError, ConflictError, NotFoundError
from src.domain.interfaces.notes_generator import GeneratedNotes
from src.domain.interfaces.transcript_provider import TranscriptResult
from src.domain.models.playlist import (
    FailureKind,
    FailureType,
    PipelineError,
    Stage,
)
from src.domain.services.stage_machine import InvalidTransitionError
from src.domain.services.sync_diff import PlaylistEntry
from src.infrastructure.storage import db
from src.infrastructure.storage.playlist_repo import PlaylistRepository
from src.shared.files import delete_files

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL100"


@pytest.fixture
def repo(tmp_path: Path) -> PlaylistRepository:
    db_path = tmp_path / "test_pipeline.sqlite3"
    db.initialize(db_path)
    return PlaylistRepository(db_path)


def entry(video_id: str, title: str = "", position: int = 0) -> PlaylistEntry:
    return PlaylistEntry(youtube_video_id=video_id, title=title or video_id, position=position)


class FakeYouTubeProvider:
    """Implements PlaylistProviderPort; returns the PlaylistEntry shape the real one returns."""

    def __init__(self, videos: list[PlaylistEntry] | None = None, title: str = "Test Playlist"):
        self.videos = videos or []
        self.title = title
        self.fetch_calls = 0

    def get_playlist_title(self, playlist_id: str) -> str:
        return self.title

    def fetch_playlist_videos(self, playlist_id: str) -> list[PlaylistEntry]:
        self.fetch_calls += 1
        return list(self.videos)


class FakeTranscriptProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_with: Exception | None = None
        self.artifact_path = ""

    async def fetch_transcript(self, video_id: str, language: str = "en") -> TranscriptResult:
        self.calls.append(video_id)
        if self.fail_with:
            raise self.fail_with
        return TranscriptResult(
            video_id=video_id,
            language=language,
            raw_srt="1\n00:00:00,000 --> 00:00:01,000\nHello",
            cleaned_text=f"Cleaned transcript for {video_id}",
            content_hash=f"hash_{video_id}",
            artifact_path=self.artifact_path,
            byte_size=len(f"Cleaned transcript for {video_id}"),
        )


class FakeNotesGenerator:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_with: Exception | None = None
        self.fail_times: int | None = None  # None = fail forever when fail_with is set

    async def generate_notes(self, *, title: str, transcript: str) -> GeneratedNotes:
        self.calls.append(title)
        if self.fail_with and (self.fail_times is None or len(self.calls) <= self.fail_times):
            raise self.fail_with
        return GeneratedNotes(
            content=f"## Notes\n\nNotes for {title}\n\n## Key Points\n\nKey takeaways.",
            provider="fake_provider",
            model="fake_model",
            prompt_version="v1",
        )


async def wait_until(
    predicate: Callable[[], Awaitable[bool]], timeout: float = 20.0, interval: float = 0.02
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met in time")


async def new_video(repo: PlaylistRepository, video_id: str = "v1", title: str = "Intro"):
    playlist = await repo.create_playlist("PL1", "https://youtube.com/playlist?list=PL1")
    videos = await repo.insert_videos(playlist.id, [PlaylistEntry(video_id, title, 1)])
    return playlist, videos[0]


# ---------------------------------------------------------------------------
# Ingestion & Sync Tests
# ---------------------------------------------------------------------------


async def test_ingest_new_playlist_numbers_videos_from_one(repo: PlaylistRepository) -> None:
    yt = FakeYouTubeProvider(videos=[entry("v1", "Video One", 1), entry("v2", "Video Two", 2)],
                             title="My Course")
    use_case = IngestPlaylistUseCase(repo, yt)

    playlist = await use_case.execute(PLAYLIST_URL)
    assert playlist.playlist_id == "PL100"
    assert playlist.title == "My Course"
    assert playlist.video_count == 2

    stored_videos = await repo.list_videos(playlist.id)
    assert [v.youtube_video_id for v in stored_videos] == ["v1", "v2"]
    assert [v.position for v in stored_videos] == [1, 2]
    assert all(v.stage == Stage.QUEUED for v in stored_videos)


async def test_ingest_reports_created_then_synced(repo: PlaylistRepository) -> None:
    use_case = IngestPlaylistUseCase(repo, FakeYouTubeProvider(videos=[entry("v1")]))

    first = await use_case.ingest(PLAYLIST_URL)
    again = await use_case.ingest(PLAYLIST_URL)

    assert first.created is True
    assert again.created is False
    assert again.playlist.id == first.playlist.id
    assert len(await repo.list_videos(first.playlist.id)) == 1


async def test_concurrent_submissions_of_one_url_create_one_playlist(
    repo: PlaylistRepository,
) -> None:
    use_case = IngestPlaylistUseCase(repo, FakeYouTubeProvider(videos=[entry("a"), entry("b")]))

    results = await asyncio.gather(*(use_case.ingest(PLAYLIST_URL) for _ in range(5)))

    assert sorted(r.created for r in results) == [False] * 4 + [True]
    assert len({r.playlist.id for r in results}) == 1
    assert await repo.count_playlists() == 1
    assert len(await repo.list_videos(results[0].playlist.id)) == 2


async def test_losing_the_create_race_to_another_process_falls_back_to_sync(
    repo: PlaylistRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A UNIQUE violation on the playlist insert (another process won) syncs instead of 500ing."""
    yt = FakeYouTubeProvider(videos=[entry("v1")])
    use_case = IngestPlaylistUseCase(repo, yt)
    await use_case.ingest(PLAYLIST_URL)  # the "other process" already created it

    real_lookup = repo.get_playlist_by_youtube_id
    calls = {"n": 0}

    async def stale_lookup(playlist_id: str):
        calls["n"] += 1
        return None if calls["n"] == 1 else await real_lookup(playlist_id)

    monkeypatch.setattr(repo, "get_playlist_by_youtube_id", stale_lookup)
    result = await use_case.ingest(PLAYLIST_URL)

    assert result.created is False
    assert await repo.count_playlists() == 1


async def test_incremental_sync_appends_new_and_flags_removed(repo: PlaylistRepository) -> None:
    yt = FakeYouTubeProvider(videos=[entry("v1"), entry("v2")], title="My Course")
    use_case = IngestPlaylistUseCase(repo, yt)
    playlist = await use_case.execute(PLAYLIST_URL)

    yt.videos = [entry("v1"), entry("v3")]  # v2 removed, v3 added
    synced_playlist = await use_case.execute(PLAYLIST_URL)
    assert synced_playlist.id == playlist.id

    stored_videos = await repo.list_videos(playlist.id)
    assert [v.youtube_video_id for v in stored_videos] == ["v1", "v2", "v3"]
    assert not stored_videos[0].removed_upstream
    assert stored_videos[1].removed_upstream is True  # flagged, never deleted
    assert stored_videos[2].position == 3
    assert not stored_videos[2].removed_upstream
    assert synced_playlist.video_count == 3


async def test_a_video_that_reappears_is_unflagged_and_processed_again(
    repo: PlaylistRepository,
) -> None:
    yt = FakeYouTubeProvider(videos=[entry("v1"), entry("v2")])
    use_case = IngestPlaylistUseCase(repo, yt)
    playlist = await use_case.execute(PLAYLIST_URL)

    yt.videos = [entry("v1")]
    await use_case.execute(PLAYLIST_URL)
    v2 = (await repo.list_videos(playlist.id))[1]
    assert v2.removed_upstream is True
    assert [v.youtube_video_id for v in await repo.list_queued_videos()] == ["v1"]

    yt.videos = [entry("v1"), entry("v2")]
    await use_case.execute(PLAYLIST_URL)

    v2 = (await repo.list_videos(playlist.id))[1]
    assert v2.removed_upstream is False
    assert v2.position == 2  # never renumbered
    assert {v.youtube_video_id for v in await repo.list_queued_videos()} == {"v1", "v2"}

    # ... and the worker really processes it again.
    notes = FakeNotesGenerator()
    process = ProcessVideoUseCase(repo, FakeTranscriptProvider(), notes)
    worker = PlaylistWorker(repo, process, poll_interval_seconds=0.02)
    await worker.start()
    try:
        async def done() -> bool:
            return (await repo.get_video(v2.id)).stage == Stage.PENDING_REVIEW

        await wait_until(done)
    finally:
        await worker.stop()


async def test_a_repeated_video_in_a_playlist_gets_its_own_numbered_row(
    repo: PlaylistRepository,
) -> None:
    yt = FakeYouTubeProvider(videos=[entry("a", "A"), entry("b", "B"), entry("a", "A again")])
    use_case = IngestPlaylistUseCase(repo, yt)

    playlist = await use_case.execute(PLAYLIST_URL)

    rows = await repo.list_videos(playlist.id)
    assert [(v.position, v.youtube_video_id) for v in rows] == [(1, "a"), (2, "b"), (3, "a")]
    assert rows[2].duplicate_of == rows[0].id
    assert playlist.video_count == 3  # counts rows, consistent with numbering

    # Re-running with the same upstream list is a no-op (no extra duplicate rows).
    await use_case.execute(PLAYLIST_URL)
    assert len(await repo.list_videos(playlist.id)) == 3

    # A genuinely new third occurrence is appended.
    yt.videos = [*yt.videos, entry("a", "A third")]
    await use_case.execute(PLAYLIST_URL)
    assert [v.position for v in await repo.list_videos(playlist.id)] == [1, 2, 3, 4]


async def test_provider_failures_are_redacted_and_classified(repo: PlaylistRepository) -> None:
    class Exploding(FakeYouTubeProvider):
        def fetch_playlist_videos(self, playlist_id: str):
            raise RuntimeError("boom https://www.googleapis.com/x?part=a&key=AIzaSECRETSECRET123")

    use_case = IngestPlaylistUseCase(repo, Exploding(title="T"))

    with pytest.raises(PipelineError) as excinfo:
        await use_case.execute(PLAYLIST_URL)

    assert excinfo.value.failure_type is FailureType.PLAYLIST_UNAVAILABLE
    assert "AIzaSECRETSECRET123" not in excinfo.value.reason
    assert await repo.count_playlists() == 0  # nothing created for an unavailable playlist


async def test_a_missing_api_key_is_a_configuration_error_not_unavailable(
    repo: PlaylistRepository,
) -> None:
    class NoKey(FakeYouTubeProvider):
        def get_playlist_title(self, playlist_id: str) -> str:
            raise ConfigurationError("YOUTUBE_API_KEY is not set.")

    with pytest.raises(ConfigurationError, match="YOUTUBE_API_KEY"):
        await IngestPlaylistUseCase(repo, NoKey()).execute(PLAYLIST_URL)


# ---------------------------------------------------------------------------
# Process Video Tests (Happy Path, Skip-Completed, Failure Handling)
# ---------------------------------------------------------------------------


async def test_process_video_happy_path(repo: PlaylistRepository) -> None:
    _, video = await new_video(repo)
    proc = ProcessVideoUseCase(repo, FakeTranscriptProvider(), FakeNotesGenerator())

    result = await proc.execute(video.id)

    assert result is not None
    assert result.stage == Stage.PENDING_REVIEW
    tr = await repo.get_transcript(video.id)
    assert tr is not None and "Cleaned transcript for v1" in tr.content
    sm = await repo.get_summary(video.id)
    assert sm is not None and "Notes for Intro" in sm.content


async def test_process_video_skip_completed(repo: PlaylistRepository) -> None:
    _, video = await new_video(repo)
    await repo.upsert_transcript(video.id, "Existing transcript content")
    transcript_prov = FakeTranscriptProvider()
    notes_gen = FakeNotesGenerator()
    proc = ProcessVideoUseCase(repo, transcript_prov, notes_gen)

    result = await proc.execute(video.id)

    assert result is not None
    assert result.stage == Stage.PENDING_REVIEW
    assert len(transcript_prov.calls) == 0
    assert len(notes_gen.calls) == 1

    # Running it again never repeats either external call.
    await proc.execute(video.id)
    assert len(transcript_prov.calls) == 0
    assert len(notes_gen.calls) == 1


async def test_process_video_permanent_failure_no_retry(repo: PlaylistRepository) -> None:
    _, video = await new_video(repo)
    transcript_prov = FakeTranscriptProvider()
    transcript_prov.fail_with = PipelineError(
        failure_type=FailureType.VIDEO_PRIVATE, reason="Video is private"
    )
    proc = ProcessVideoUseCase(repo, transcript_prov, FakeNotesGenerator(), max_auto_retries=3)

    result = await proc.execute(video.id)

    assert result is not None
    assert result.stage == Stage.FAILED
    assert result.failure_type == FailureType.VIDEO_PRIVATE
    assert result.failure_kind == FailureKind.PERMANENT
    assert result.retry_count == 0
    assert result.next_retry_at is None
    assert result.auto_retry is False
    assert result.retries_exhausted is False


async def test_exhausted_transient_failure_is_marked_manual_retry_only(
    repo: PlaylistRepository,
) -> None:
    _, video = await new_video(repo)
    transcript_prov = FakeTranscriptProvider()
    transcript_prov.fail_with = PipelineError(FailureType.RATE_LIMITED, "Rate limit exceeded")
    proc = ProcessVideoUseCase(
        repo, transcript_prov, FakeNotesGenerator(), max_auto_retries=2, backoff_base_seconds=0.1
    )

    first = await proc.execute(video.id)
    assert (first.stage, first.retry_count) == (Stage.FAILED, 1)
    assert first.next_retry_at is not None
    assert first.auto_retry is True and first.retries_exhausted is False

    second = await proc.execute(video.id)
    assert (second.retry_count, second.auto_retry) == (2, True)

    third = await proc.execute(video.id)
    # Budget used up: still "transient" (UI: Failed - temporary) but no schedule.
    assert third.stage == Stage.FAILED
    assert third.failure_kind == FailureKind.TRANSIENT
    assert third.retry_count == 2
    assert third.next_retry_at is None
    assert third.auto_retry is False
    assert third.retries_exhausted is True


async def test_missing_api_key_is_a_permanent_config_failure_with_a_clear_reason(
    repo: PlaylistRepository,
) -> None:
    from src.domain.exceptions import NotesGeneratorConfigError

    _, video = await new_video(repo)
    notes = FakeNotesGenerator()
    notes.fail_with = NotesGeneratorConfigError("GEMINI_API_KEY is not set.")
    proc = ProcessVideoUseCase(repo, FakeTranscriptProvider(), notes, max_auto_retries=5)

    result = await proc.execute(video.id)

    assert result.stage == Stage.FAILED
    assert result.failure_type == FailureType.CONFIG_ERROR
    assert result.failure_kind == FailureKind.PERMANENT
    assert result.failure_reason == "GEMINI_API_KEY is not set."
    assert result.next_retry_at is None and result.retry_count == 0
    assert len(notes.calls) == 1


async def test_unexpected_errors_are_internal_failures_not_transcript_errors(
    repo: PlaylistRepository, caplog: pytest.LogCaptureFixture
) -> None:
    _, video = await new_video(repo)
    notes = FakeNotesGenerator()
    notes.fail_with = KeyError("a bug in Atlas, not in the video")
    proc = ProcessVideoUseCase(repo, FakeTranscriptProvider(), notes, max_auto_retries=5)

    with caplog.at_level("ERROR"):
        result = await proc.execute(video.id)

    assert result.failure_type == FailureType.INTERNAL_ERROR
    assert result.failure_kind == FailureKind.PERMANENT
    assert result.failure_reason == INTERNAL_ERROR_REASON
    assert "transcript provider" not in result.failure_reason
    assert result.next_retry_at is None
    # The traceback is logged with both ids for diagnosis.
    record = next(r for r in caplog.records if r.exc_info)
    assert record.playlist_id == video.playlist_id and record.video_id == video.id


# ---------------------------------------------------------------------------
# Stale runs: reprocess / delete while a task is in flight
# ---------------------------------------------------------------------------


class SlowTranscriptProvider(FakeTranscriptProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def fetch_transcript(self, video_id: str, language: str = "en") -> TranscriptResult:
        self.started.set()
        await self.release.wait()
        return await super().fetch_transcript(video_id, language)


async def test_reprocess_during_a_run_discards_the_stale_result(
    repo: PlaylistRepository,
) -> None:
    from src.application.use_cases.playlist.actions import ReprocessPlaylistUseCase

    playlist, video = await new_video(repo)
    slow = SlowTranscriptProvider()
    notes = FakeNotesGenerator()
    proc = ProcessVideoUseCase(repo, slow, notes)

    task = asyncio.create_task(proc.execute(video.id))
    await slow.started.wait()
    assert (await repo.get_video(video.id)).stage == Stage.FETCHING_TRANSCRIPT

    await ReprocessPlaylistUseCase(repo).execute(playlist.id)  # user clicks "reprocess"
    slow.release.set()
    await task

    after = await repo.get_video(video.id)
    assert after.stage == Stage.QUEUED  # the stale task did not advance it
    assert await repo.get_transcript(video.id) is None  # ...nor write its transcript
    assert notes.calls == []


async def test_delete_during_a_run_does_not_resurrect_anything(repo: PlaylistRepository) -> None:
    playlist, video = await new_video(repo)
    slow = SlowTranscriptProvider()
    proc = ProcessVideoUseCase(repo, slow, FakeNotesGenerator())

    task = asyncio.create_task(proc.execute(video.id))
    await slow.started.wait()
    await repo.delete_playlist(playlist.id)
    slow.release.set()

    assert await task is None
    assert await repo.get_video(video.id) is None
    assert await repo.get_transcript(video.id) is None


# ---------------------------------------------------------------------------
# Duplicates (PRD section 20): processed once, summary reused
# ---------------------------------------------------------------------------


async def test_a_duplicate_row_reuses_the_first_occurrences_summary(
    repo: PlaylistRepository,
) -> None:
    playlist = await repo.create_playlist("PL1", "https://youtube.com/playlist?list=PL1")
    rows = await repo.insert_videos(
        playlist.id, [entry("a", "A", 1), entry("b", "B", 2), entry("a", "A", 3)]
    )
    transcripts = FakeTranscriptProvider()
    notes = FakeNotesGenerator()
    process = ProcessVideoUseCase(repo, transcripts, notes)

    # Only the first occurrence and the unrelated video are runnable up front.
    assert [v.id for v in await repo.list_queued_videos()] == [rows[0].id, rows[1].id]

    await process.execute(rows[0].id)
    await process.execute(rows[1].id)
    assert [v.id for v in await repo.list_queued_videos()] == [rows[2].id]  # now runnable
    await process.execute(rows[2].id)

    dup = await repo.get_video(rows[2].id)
    assert dup.stage == Stage.PENDING_REVIEW
    first_summary = await repo.get_summary(rows[0].id)
    dup_summary = await repo.get_summary(rows[2].id)
    assert dup_summary.content == first_summary.content
    assert transcripts.calls == ["a", "b"]  # one fetch per distinct video
    assert len(notes.calls) == 2  # one generation per distinct video

    # The copy has its own review life cycle.
    await ApproveVideoUseCase(repo).execute(rows[2].id)
    assert (await repo.get_video(rows[0].id)).stage == Stage.PENDING_REVIEW


# ---------------------------------------------------------------------------
# Actions Tests (ApproveAll, SaveSummaryEdit, Regenerate, Retry, Cleanup)
# ---------------------------------------------------------------------------


async def test_approve_all_scope(repo: PlaylistRepository) -> None:
    playlist = await repo.create_playlist("PL1", "https://youtube.com/playlist?list=PL1")
    v_list = await repo.insert_videos(playlist.id, [
        entry("v1", "One", 1), entry("v2", "Two", 2), entry("v3", "Three", 3),
    ])
    for index, (video, stage) in enumerate(
        zip(v_list, [Stage.PENDING_REVIEW, Stage.PENDING_REVIEW, Stage.APPROVED], strict=True)
    ):
        await repo.upsert_summary(video.id, f"Notes {index}")
        await repo.update_stage(video.id, stage)

    approved_count = await ApproveAllUseCase(repo).execute(playlist.id)

    assert approved_count == 2
    for video in v_list:
        assert (await repo.get_video(video.id)).stage == Stage.APPROVED


async def test_approve_without_notes_is_a_conflict_not_a_silent_ok(
    repo: PlaylistRepository,
) -> None:
    _, video = await new_video(repo)
    await repo.update_stage(video.id, Stage.PENDING_REVIEW)  # stage says review, but no summary

    with pytest.raises(ConflictError):
        await ApproveVideoUseCase(repo).execute(video.id)
    assert (await repo.get_video(video.id)).stage == Stage.PENDING_REVIEW


async def test_approving_a_queued_video_is_an_invalid_transition(
    repo: PlaylistRepository,
) -> None:
    _, video = await new_video(repo)

    with pytest.raises(InvalidTransitionError):
        await ApproveVideoUseCase(repo).execute(video.id)


async def test_approving_an_unknown_video_is_not_found(repo: PlaylistRepository) -> None:
    with pytest.raises(NotFoundError):
        await ApproveVideoUseCase(repo).execute(999)


async def test_save_summary_edit(repo: PlaylistRepository) -> None:
    _, video = await new_video(repo)
    await repo.upsert_summary(video.id, "Original content")
    await repo.update_stage(video.id, Stage.PENDING_REVIEW)

    sm = await SaveSummaryEditUseCase(repo).execute(video.id, "Edited content by user")

    assert sm.content == "Edited content by user"
    assert sm.edited is True


@pytest.mark.parametrize("stage", [Stage.QUEUED, Stage.FETCHING_TRANSCRIPT, Stage.FAILED])
async def test_editing_is_refused_before_notes_exist_or_outside_review(
    repo: PlaylistRepository, stage: Stage
) -> None:
    _, video = await new_video(repo)
    await repo.update_stage(video.id, stage)

    with pytest.raises(ConflictError):
        await SaveSummaryEditUseCase(repo).execute(video.id, "x")


async def test_regenerate_queues_work_and_the_worker_replaces_the_summary(
    repo: PlaylistRepository,
) -> None:
    _, video = await new_video(repo)
    await repo.upsert_transcript(video.id, "Existing transcript")
    await repo.upsert_summary(video.id, "Old summary")
    await repo.update_stage(video.id, Stage.PENDING_REVIEW)
    await repo.approve(video.id)
    await repo.set_drive_doc_id(video.id, "doc-1")
    transcripts = FakeTranscriptProvider()
    notes = FakeNotesGenerator()

    queued = await RegenerateVideoUseCase(repo).execute(video.id)

    # Returns immediately: nothing slow happened yet.
    assert queued.stage == Stage.QUEUED and queued.regen_requested is True
    assert notes.calls == []
    assert (await repo.get_summary(video.id)).content == "Old summary"

    await ProcessVideoUseCase(repo, transcripts, notes).execute(video.id)

    after = await repo.get_video(video.id)
    assert after.stage == Stage.PENDING_REVIEW
    assert after.regen_requested is False
    assert after.drive_doc_id == "doc-1"  # kept: re-publish updates the Doc in place
    summary = await repo.get_summary(video.id)
    assert "Notes for Intro" in summary.content and summary.approved is False
    assert transcripts.calls == []  # the stored transcript was reused


async def test_regenerate_after_transcript_cleanup_refetches_the_transcript(
    repo: PlaylistRepository,
) -> None:
    _, video = await new_video(repo)
    await repo.upsert_transcript(video.id, "Existing transcript")
    await repo.upsert_summary(video.id, "Old summary")
    await repo.update_stage(video.id, Stage.PENDING_REVIEW)
    await CleanupTranscriptsUseCase(repo).execute_video(video.id)
    transcripts = FakeTranscriptProvider()
    notes = FakeNotesGenerator()

    await RegenerateVideoUseCase(repo).execute(video.id)  # used to raise DomainError / 500
    await ProcessVideoUseCase(repo, transcripts, notes).execute(video.id)

    assert transcripts.calls == ["v1"]  # re-entered fetching_transcript
    assert (await repo.get_video(video.id)).stage == Stage.PENDING_REVIEW
    assert (await repo.get_transcript(video.id)).content.startswith("Cleaned transcript")


async def test_regenerate_is_refused_while_the_video_is_in_flight(
    repo: PlaylistRepository,
) -> None:
    _, video = await new_video(repo)
    await repo.update_stage(video.id, Stage.GENERATING_NOTES)

    with pytest.raises(ConflictError):
        await RegenerateVideoUseCase(repo).execute(video.id)


async def test_cleanup_clears_text_and_deletes_the_raw_subtitle_file(
    repo: PlaylistRepository, tmp_path: Path
) -> None:
    _, video = await new_video(repo)
    artifact = tmp_path / "v1.abc123.srt"
    artifact.write_text("1\n00:00:00,000 --> 00:00:01,000\nraw subtitle")
    transcripts = FakeTranscriptProvider()
    transcripts.artifact_path = str(artifact)
    await ProcessVideoUseCase(repo, transcripts, FakeNotesGenerator()).execute(video.id)
    assert artifact.exists()
    before = await repo.get_transcript(video.id)

    cleared = await CleanupTranscriptsUseCase(
        repo, delete_artifacts=lambda paths: delete_files(paths, root=tmp_path)
    ).execute_video(video.id)

    assert cleared is True
    assert not artifact.exists()
    assert await repo.get_transcript(video.id) is None
    with sqlite3.connect(repo.db_path) as conn:
        row = conn.execute(
            "SELECT content, byte_size, content_hash, deleted_at, artifact_path"
            " FROM transcripts WHERE video_id = ?",
            (video.id,),
        ).fetchone()
    assert row[0] == ""  # text is actually gone, not just hidden
    assert row[1] == before.byte_size and row[2] == before.content_hash  # audit trail kept
    assert row[3] is not None and row[4] is None
    # Notes are untouched.
    assert await repo.get_summary(video.id) is not None
    # Cleaning twice is a no-op, not an error.
    assert await CleanupTranscriptsUseCase(repo).execute_video(video.id) is False


async def test_cleanup_refuses_to_delete_files_outside_the_transcripts_folder(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "keep.txt"
    outside.write_text("precious")
    inside = tmp_path / "transcripts"
    inside.mkdir()

    assert delete_files([str(outside)], root=inside) == 0
    assert outside.exists()


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "crashed_stage",
    [Stage.FETCHING_TRANSCRIPT, Stage.TRANSCRIPT_READY, Stage.GENERATING_NOTES],
)
async def test_crash_recovery_requeues_every_in_flight_stage(
    repo: PlaylistRepository, crashed_stage: Stage
) -> None:
    _, video = await new_video(repo)
    await repo.update_stage(video.id, crashed_stage)

    assert await repo.recover_in_flight_videos() == 1

    assert (await repo.get_video(video.id)).stage == Stage.QUEUED


async def test_worker_resumes_a_crashed_transcript_ready_video_at_notes(
    repo: PlaylistRepository,
) -> None:
    """transcript_ready survived a crash: the stored transcript is reused, only notes run."""
    _, video = await new_video(repo)
    await repo.upsert_transcript(video.id, "stored transcript")
    await repo.update_stage(video.id, Stage.TRANSCRIPT_READY)
    transcripts = FakeTranscriptProvider()
    notes = FakeNotesGenerator()
    worker = PlaylistWorker(
        repo, ProcessVideoUseCase(repo, transcripts, notes), poll_interval_seconds=0.02
    )

    await worker.start()
    try:
        async def done() -> bool:
            return (await repo.get_video(video.id)).stage == Stage.PENDING_REVIEW

        await wait_until(done)
    finally:
        await worker.stop()

    assert transcripts.calls == []
    assert len(notes.calls) == 1


async def test_worker_picks_up_an_orphaned_transcript_ready_video_without_a_restart(
    repo: PlaylistRepository,
) -> None:
    _, video = await new_video(repo)
    await repo.upsert_transcript(video.id, "stored transcript")
    worker = PlaylistWorker(
        repo,
        ProcessVideoUseCase(repo, FakeTranscriptProvider(), FakeNotesGenerator()),
        poll_interval_seconds=0.02,
    )
    await worker.start()
    try:
        # Simulate a task that died after storing the transcript (no restart involved).
        await repo.update_stage(video.id, Stage.TRANSCRIPT_READY)
        worker.wake()

        async def done() -> bool:
            return (await repo.get_video(video.id)).stage == Stage.PENDING_REVIEW

        await wait_until(done)
    finally:
        await worker.stop()


async def test_worker_crash_recovery(repo: PlaylistRepository) -> None:
    playlist = await repo.create_playlist("PL1", "https://youtube.com/playlist?list=PL1")
    v_list = await repo.insert_videos(playlist.id, [entry("v1", "One", 1), entry("v2", "Two", 2)])
    await repo.update_stage(v_list[0].id, Stage.FETCHING_TRANSCRIPT)
    await repo.update_stage(v_list[1].id, Stage.GENERATING_NOTES)
    proc = ProcessVideoUseCase(repo, FakeTranscriptProvider(), FakeNotesGenerator())
    worker = PlaylistWorker(repo, proc, max_concurrent_videos=2, poll_interval_seconds=0.02)

    await worker.start()
    try:
        async def done() -> bool:
            stages = [(await repo.get_video(v.id)).stage for v in v_list]
            return stages == [Stage.PENDING_REVIEW] * 2

        await wait_until(done)
    finally:
        await worker.stop()

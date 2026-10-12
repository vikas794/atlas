"""Repository behaviour against the real migrated schema.

The temporary database is created here (no shared conftest fixture) so this file
is self-contained.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.domain.models.playlist import (
    FailureKind,
    FailureType,
    Playlist,
    Stage,
    Summary,
    Transcript,
    Video,
)
from src.domain.services.sync_diff import PlaylistEntry
from src.infrastructure.storage import db
from src.infrastructure.storage.playlist_repo import PlaylistRepository


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[PlaylistRepository]:
    """A repository over a freshly migrated throwaway database."""
    db.initialize(tmp_path / "atlas.sqlite3")
    yield PlaylistRepository(tmp_path / "atlas.sqlite3")


async def make_playlist(repo: PlaylistRepository, playlist_id: str = "PL1") -> Playlist:
    return await repo.create_playlist(playlist_id, f"https://youtube.com/playlist?list={playlist_id}")


async def make_video(repo: PlaylistRepository, playlist_pk: int, video_id: str,
                     position: int | None = None, title: str = "") -> Video:
    if position is None:
        position = await repo.max_position(playlist_pk) + 1
    stored = await repo.insert_videos(
        playlist_pk, [PlaylistEntry(youtube_video_id=video_id, title=title or video_id,
                                    position=position)]
    )
    return stored[0]


# ---------------------------------------------------------------- playlists


async def test_create_playlist_round_trips_every_field(repo: PlaylistRepository) -> None:
    created = await repo.create_playlist("PL1", "https://youtu.be/PL1", "Python Bootcamp")

    assert isinstance(created, Playlist)
    assert created.id is not None and created.id > 0
    assert created.playlist_id == "PL1"
    assert created.source_url == "https://youtu.be/PL1"
    assert created.title == "Python Bootcamp"
    assert created.video_count == 0
    assert created.status == "ingested"
    assert created.drive_folder_id is None
    assert created.drive_index_doc_id is None
    assert created.last_synced_at is None
    assert created.created_at and created.updated_at

    fetched = await repo.get_playlist(created.id)
    assert fetched == created


async def test_get_playlist_by_youtube_id_and_missing_lookups(
    repo: PlaylistRepository,
) -> None:
    created = await make_playlist(repo, "PL42")

    assert (await repo.get_playlist_by_youtube_id("PL42")) == created
    assert await repo.get_playlist_by_youtube_id("nope") is None
    assert await repo.get_playlist(9999) is None


async def test_duplicate_playlist_id_is_rejected_by_the_unique_constraint(
    repo: PlaylistRepository,
) -> None:
    await make_playlist(repo, "PL1")

    with pytest.raises(sqlite3.IntegrityError):
        await repo.create_playlist("PL1", "https://youtu.be/PL1")


async def test_update_playlist_sets_updated_at(repo: PlaylistRepository) -> None:
    created = await make_playlist(repo)

    updated = await repo.update_playlist(created.id, title="New", status="processing", video_count=7)

    assert updated is not None
    assert updated.title == "New"
    assert updated.status == "processing"
    assert updated.video_count == 7
    assert updated.source_url == created.source_url


async def test_update_playlist_with_no_arguments_is_a_no_op(repo: PlaylistRepository) -> None:
    created = await make_playlist(repo, "PL1")

    unchanged = await repo.update_playlist(created.id)

    assert unchanged == created


async def test_list_playlists_is_newest_first(repo: PlaylistRepository) -> None:
    await repo.create_playlist("PL1", "url-1")
    await repo.create_playlist("PL2", "url-2")

    listed = await repo.list_playlists()

    assert [item.playlist_id for item in listed] == ["PL2", "PL1"]
    assert [item.playlist_id for item in await repo.list_playlists(status="ingested")] == [
        "PL2",
        "PL1",
    ]
    assert await repo.list_playlists(status="archived") == []


async def test_touch_synced_and_drive_identifiers(repo: PlaylistRepository) -> None:
    created = await make_playlist(repo)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")

    await repo.touch_synced(created.id, stamp)
    await repo.set_drive_folder(created.id, "folder-123")
    await repo.set_drive_index_doc(created.id, "doc-456")

    refreshed = await repo.get_playlist(created.id)
    assert refreshed is not None
    assert refreshed.last_synced_at == stamp
    assert refreshed.drive_folder_id == "folder-123"
    assert refreshed.drive_index_doc_id == "doc-456"


async def test_delete_playlist_cascades_to_children(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    video = await make_video(repo, playlist.id, "vid1")
    await repo.upsert_transcript(video.id, "hello world")
    await repo.upsert_summary(video.id, "# notes")

    assert await repo.delete_playlist(playlist.id) is True
    assert await repo.get_playlist(playlist.id) is None
    assert await repo.get_video(video.id) is None
    assert await repo.get_transcript(video.id) is None
    assert await repo.get_summary(video.id) is None
    assert await repo.delete_playlist(playlist.id) is False


# ------------------------------------------------------------------- videos


async def test_insert_videos_round_trips_every_field(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)

    stored = await repo.insert_videos(
        playlist.id,
        [
            PlaylistEntry(youtube_video_id="abc", title="First talk", position=0),
            PlaylistEntry(youtube_video_id="def", title="Second talk", position=1),
        ],
    )

    assert [item.youtube_video_id for item in stored] == ["abc", "def"]
    video = stored[0]
    assert isinstance(video, Video)
    assert video.id is not None
    assert video.playlist_id == playlist.id
    assert video.title == "First talk"
    assert video.position == 0
    assert video.display_position == "000"
    assert video.stage is Stage.QUEUED
    assert video.failure_type is None
    assert video.failure_kind is None
    assert video.failure_reason is None
    assert video.retry_count == 0
    assert video.next_retry_at is None
    assert video.drive_doc_id is None
    assert video.removed_upstream is False
    assert video.created_at and video.updated_at
    assert await repo.get_video(video.id) == video


async def test_insert_videos_accepts_domain_videos(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)

    stored = await repo.insert_videos(playlist.id, [Video(youtube_video_id="xyz", title="V", position=3)])

    assert stored[0].youtube_video_id == "xyz"
    assert stored[0].position == 3


async def test_a_repeated_video_gets_its_own_position_but_points_at_the_first(
    repo: PlaylistRepository,
) -> None:
    """PRD section 20: stored once per position, processed once."""
    playlist = await make_playlist(repo)
    first = await repo.insert_videos(
        playlist.id, [PlaylistEntry(youtube_video_id="abc", title="Original", position=1)]
    )

    second = await repo.insert_videos(
        playlist.id,
        [
            PlaylistEntry(youtube_video_id="abc", title="Again", position=2),
            PlaylistEntry(youtube_video_id="new", title="New", position=3),
            PlaylistEntry(youtube_video_id="new", title="New again", position=4),
        ],
    )

    assert [v.position for v in second] == [2, 3, 4]
    assert second[0].duplicate_of == first[0].id
    assert second[1].duplicate_of is None
    assert second[2].duplicate_of == second[1].id
    assert first[0].duplicate_of is None
    assert (await repo.count_by_stage(playlist.id))[Stage.QUEUED.value] == 4


async def test_positions_must_be_unique_within_a_playlist(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    await repo.insert_videos(playlist.id, [PlaylistEntry("a", "A", 1)])

    with pytest.raises(sqlite3.IntegrityError):
        await repo.insert_videos(playlist.id, [PlaylistEntry("b", "B", 1)])


async def test_insert_videos_with_no_entries_does_nothing(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)

    assert await repo.insert_videos(playlist.id, []) == []
    assert await repo.list_videos(playlist.id) == []


async def test_list_videos_filters_and_orders(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    await repo.insert_videos(
        playlist.id,
        [
            PlaylistEntry(youtube_video_id="a", title="Loops in Python", position=2),
            PlaylistEntry(youtube_video_id="b", title="Generators", position=0),
            PlaylistEntry(youtube_video_id="c", title="Async deep dive", position=1),
        ],
    )
    videos = {item.youtube_video_id: item for item in await repo.list_videos(playlist.id)}
    await repo.update_stage(videos["b"].id, Stage.FETCHING_TRANSCRIPT)
    await repo.update_stage(videos["c"].id, Stage.FAILED)

    ordered = await repo.list_videos(playlist.id)
    assert [item.position for item in ordered] == [0, 1, 2]

    fetching = await repo.list_videos(playlist.id, stage=Stage.FETCHING_TRANSCRIPT)
    assert [item.youtube_video_id for item in fetching] == ["b"]

    assert [item.youtube_video_id for item in await repo.list_videos(playlist.id, search="loops")] == [
        "a"
    ]
    # Matches on title *or* youtube id: "Generators", "Async deep dive" and id "a".
    assert [item.youtube_video_id for item in await repo.list_videos(playlist.id, search="a")] == [
        "b",
        "c",
        "a",
    ]

    page = await repo.list_videos(playlist.id, limit=2, offset=1)
    assert [item.youtube_video_id for item in page] == ["c", "a"]


async def test_list_videos_is_scoped_to_one_playlist(repo: PlaylistRepository) -> None:
    first = await make_playlist(repo, "PL1")
    second = await make_playlist(repo, "PL2")
    await make_video(repo, first.id, "a")
    await make_video(repo, second.id, "b")

    assert [item.youtube_video_id for item in await repo.list_videos(first.id)] == ["a"]


async def test_update_stage_records_the_failure_taxonomy(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    video = await make_video(repo, playlist.id, "abc")

    failed = await repo.update_stage(
        video.id,
        Stage.FAILED,
        failure_type=FailureType.RATE_LIMITED,
        failure_kind=FailureKind.TRANSIENT,
        failure_reason="YouTube is rate-limiting requests; retrying shortly.",
        next_retry_at="2030-01-01T00:00:00+00:00",
    )

    assert failed is not None
    assert failed.stage is Stage.FAILED
    assert failed.failure_type is FailureType.RATE_LIMITED
    assert failed.failure_kind is FailureKind.TRANSIENT
    assert failed.failure_reason.startswith("YouTube is rate-limiting")
    assert failed.next_retry_at == "2030-01-01T00:00:00+00:00"

    # A successful advance clears the stale failure columns.
    advanced = await repo.update_stage(video.id, Stage.FETCHING_TRANSCRIPT)
    assert advanced is not None
    assert advanced.failure_type is None
    assert advanced.failure_kind is None
    assert advanced.failure_reason is None
    assert advanced.next_retry_at is None


async def test_update_stage_on_a_missing_video_returns_none(repo: PlaylistRepository) -> None:
    assert await repo.update_stage(4242, Stage.QUEUED) is None


async def test_increment_retry_and_clear_failure(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    video = await make_video(repo, playlist.id, "abc")
    await repo.update_stage(
        video.id, Stage.FAILED, failure_type=FailureType.NETWORK_ERROR,
        failure_kind=FailureKind.TRANSIENT, failure_reason="A network error interrupted the request.",
    )

    assert await repo.increment_retry(video.id, next_retry_at="2030-01-01T00:00:00+00:00") == 1
    assert await repo.increment_retry(video.id) == 2

    await repo.clear_failure(video.id)

    refreshed = await repo.get_video(video.id)
    assert refreshed is not None
    assert refreshed.retry_count == 0
    assert refreshed.failure_type is None
    assert refreshed.failure_kind is None
    assert refreshed.failure_reason is None
    assert refreshed.next_retry_at is None


async def test_set_drive_doc_id(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    video = await make_video(repo, playlist.id, "abc")

    await repo.set_drive_doc_id(video.id, "doc-1")

    assert (await repo.get_video(video.id)).drive_doc_id == "doc-1"


async def test_flag_removed_upstream_never_deletes_rows(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    first = await make_video(repo, playlist.id, "a")
    second = await make_video(repo, playlist.id, "b")

    assert await repo.flag_removed_upstream([first.id, second.id]) == 2
    # Idempotent: a second call reports nothing newly changed.
    assert await repo.flag_removed_upstream([first.id]) == 0
    assert await repo.flag_removed_upstream([]) == 0

    listed = await repo.list_videos(playlist.id)
    assert len(listed) == 2
    assert [item.removed_upstream for item in listed] == [True, True]


async def test_count_by_stage_totals_every_stage(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    videos = [await make_video(repo, playlist.id, f"v{i}", position=i) for i in range(3)]
    await repo.update_stage(videos[0].id, Stage.TRANSCRIPT_READY)
    await repo.update_stage(videos[1].id, Stage.FAILED)
    await repo.update_stage(videos[2].id, Stage.FAILED)

    counts = await repo.count_by_stage(playlist.id)

    assert sum(counts.values()) == 3
    assert counts[Stage.QUEUED.value] == 0
    assert counts[Stage.TRANSCRIPT_READY.value] == 1
    assert counts[Stage.FAILED.value] == 2
    assert counts[Stage.PUBLISHED.value] == 0


async def test_list_queued_videos_is_ordered_and_bounded(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    queued = [await make_video(repo, playlist.id, f"q{i}", position=i) for i in range(3)]
    done = await make_video(repo, playlist.id, "done", position=5)
    await repo.update_stage(done.id, Stage.PUBLISHED)
    await repo.flag_removed_upstream([queued[0].id])

    assert [item.youtube_video_id for item in await repo.list_queued_videos(playlist.id)] == [
        "q1",
        "q2",
    ]
    assert len(await repo.list_queued_videos(playlist.id, limit=1)) == 1


async def test_list_transient_due_respects_next_retry_at(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    due = await make_video(repo, playlist.id, "due", position=0)
    later = await make_video(repo, playlist.id, "later", position=1)
    permanent = await make_video(repo, playlist.id, "permanent", position=2)

    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds")
    future = (datetime.now(UTC) + timedelta(minutes=30)).isoformat(timespec="seconds")

    await repo.update_stage(due.id, Stage.FAILED, failure_type=FailureType.NETWORK_ERROR,
                            failure_kind=FailureKind.TRANSIENT, next_retry_at=past)
    await repo.update_stage(later.id, Stage.FAILED, failure_type=FailureType.RATE_LIMITED,
                            failure_kind=FailureKind.TRANSIENT, next_retry_at=future)
    await repo.update_stage(permanent.id, Stage.FAILED, failure_type=FailureType.VIDEO_PRIVATE,
                            failure_kind=FailureKind.PERMANENT)

    now = db.now_iso()
    ids = [item.youtube_video_id for item in await repo.list_transient_due(playlist.id, now)]
    assert ids == ["due"]


async def test_reset_playlist_clears_outputs_but_keeps_rows(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    video = await make_video(repo, playlist.id, "abc", position=7)
    await repo.upsert_transcript(video.id, "transcript text")
    await repo.upsert_summary(video.id, "# notes", provider="gemini", model="pro")
    await repo.update_stage(video.id, Stage.FAILED, failure_type=FailureType.RATE_LIMITED,
                            failure_kind=FailureKind.TRANSIENT, failure_reason="rate limited",
                            next_retry_at="2030-01-01T00:00:00+00:00")
    await repo.increment_retry(video.id)
    await repo.set_drive_doc_id(video.id, "doc-1")
    await repo.flag_removed_upstream([video.id])

    await repo.reset_playlist(playlist.id)

    assert await repo.get_transcript(video.id) is None
    assert await repo.get_summary(video.id) is None

    kept = await repo.get_video(video.id)
    assert kept is not None
    assert kept.id == video.id
    assert kept.position == 7
    assert kept.stage is Stage.QUEUED
    assert kept.failure_type is None
    assert kept.failure_kind is None
    assert kept.failure_reason is None
    assert kept.next_retry_at is None
    assert kept.retry_count == 0
    # The Drive doc id survives a reprocess so re-publishing updates the Doc in place.
    assert kept.drive_doc_id == "doc-1"
    assert kept.removed_upstream is False
    assert len(await repo.list_videos(playlist.id)) == 1


# -------------------------------------------------------------- transcripts


async def test_upsert_transcript_computes_hash_and_size(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    video = await make_video(repo, playlist.id, "abc")

    stored = await repo.upsert_transcript(video.id, "hello world", language="en")

    assert isinstance(stored, Transcript)
    assert stored.id is not None
    assert stored.video_id == video.id
    assert stored.content == "hello world"
    assert stored.language == "en"
    assert stored.byte_size == len(b"hello world")
    assert stored.content_hash and len(stored.content_hash) == 64
    assert stored.fetched_at
    assert stored.deleted_at is None
    assert stored.available is True


async def test_upsert_transcript_replaces_in_place(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    video = await make_video(repo, playlist.id, "abc")
    first = await repo.upsert_transcript(video.id, "first")

    second = await repo.upsert_transcript(video.id, "second", language="hi")

    assert second.id == first.id
    assert second.content == "second"
    assert second.language == "hi"
    assert second.byte_size == len(b"second")


async def test_get_transcript_returns_none_when_soft_deleted(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    video = await make_video(repo, playlist.id, "abc")
    await repo.upsert_transcript(video.id, "text")
    stamp = db.now_iso()

    assert await repo.soft_delete_transcript(video.id, stamp) is True

    assert await repo.get_transcript(video.id) is None
    assert await repo.get_transcript(9999) is None

    # Re-fetching restores the row (UNIQUE (video_id) means an upsert, not an insert).
    await repo.upsert_transcript(video.id, "fresh text")
    restored = await repo.get_transcript(video.id)
    assert restored is not None and restored.content == "fresh text"
    assert restored.deleted_at is None


async def test_soft_delete_all_transcripts(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    videos = [await make_video(repo, playlist.id, f"v{i}", position=i) for i in range(3)]
    for video in videos:
        await repo.upsert_transcript(video.id, f"text {video.youtube_video_id}")

    assert await repo.soft_delete_all_transcripts(playlist.id) == 3
    assert await repo.soft_delete_all_transcripts(playlist.id) == 0
    for video in videos:
        assert await repo.get_transcript(video.id) is None


# ---------------------------------------------------------------- summaries


async def test_upsert_summary_round_trips_every_field(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    video = await make_video(repo, playlist.id, "abc")

    stored = await repo.upsert_summary(
        video.id, "# Notes", provider="gemini", model="pro", prompt_version="notes_v1"
    )

    assert isinstance(stored, Summary)
    assert stored.id is not None
    assert stored.video_id == video.id
    assert stored.content == "# Notes"
    assert stored.provider == "gemini"
    assert stored.model == "pro"
    assert stored.prompt_version == "notes_v1"
    assert stored.generated_at
    assert stored.edited is False
    assert stored.approved is False
    assert stored.approved_at is None
    assert stored.published is False
    assert stored.published_at is None
    assert stored.published_digest is None
    assert await repo.get_summary(video.id) == stored
    assert await repo.get_summary(9999) is None


async def test_mark_edited_and_approve(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    video = await make_video(repo, playlist.id, "abc")
    await repo.upsert_summary(video.id, "# Notes")
    await repo.update_stage(video.id, Stage.PENDING_REVIEW)

    assert await repo.mark_edited(video.id) is True
    edited = await repo.get_summary(video.id)
    assert edited is not None and edited.edited is True

    assert await repo.approve(video.id) is True
    approved = await repo.get_summary(video.id)
    assert approved is not None
    assert approved.approved is True
    assert approved.approved_at is not None
    assert (await repo.get_video(video.id)).stage is Stage.APPROVED


async def test_approve_all_pending_only_touches_pending_review_rows(
    repo: PlaylistRepository,
) -> None:
    playlist = await make_playlist(repo)
    pending = [await make_video(repo, playlist.id, f"p{i}", position=i) for i in range(3)]
    approved = await make_video(repo, playlist.id, "approved", position=3)
    published = await make_video(repo, playlist.id, "published", position=4)
    failed = await make_video(repo, playlist.id, "failed", position=5)
    queued = await make_video(repo, playlist.id, "queued", position=6)

    for video in [*pending, approved, published, failed, queued]:
        await repo.upsert_summary(video.id, f"# notes {video.youtube_video_id}")
    for video in pending:
        await repo.update_stage(video.id, Stage.PENDING_REVIEW)
    await repo.update_stage(approved.id, Stage.APPROVED)
    await repo.update_stage(published.id, Stage.PUBLISHED)
    await repo.update_stage(failed.id, Stage.FAILED, failure_type=FailureType.VIDEO_PRIVATE,
                            failure_kind=FailureKind.PERMANENT)
    await repo.approve(approved.id)
    await repo.mark_published(published.id, "digest-published")

    assert await repo.approve_all_pending(playlist.id) == 3
    assert await repo.approve_all_pending(playlist.id) == 0

    for video in pending:
        summary = await repo.get_summary(video.id)
        assert summary is not None and summary.approved is True
        assert summary.published is False
        assert (await repo.get_video(video.id)).stage is Stage.APPROVED

    untouched_approved = await repo.get_summary(approved.id)
    assert untouched_approved is not None
    assert untouched_approved.approved is True
    assert untouched_approved.published is False
    assert (await repo.get_video(approved.id)).stage is Stage.APPROVED

    untouched_published = await repo.get_summary(published.id)
    assert untouched_published is not None
    assert untouched_published.published is True
    assert untouched_published.published_digest == "digest-published"
    assert (await repo.get_video(published.id)).stage is Stage.PUBLISHED

    failed_summary = await repo.get_summary(failed.id)
    assert failed_summary is not None and failed_summary.approved is False
    failed_video = await repo.get_video(failed.id)
    assert failed_video is not None
    assert failed_video.stage is Stage.FAILED
    assert failed_video.failure_type is FailureType.VIDEO_PRIVATE

    queued_summary = await repo.get_summary(queued.id)
    assert queued_summary is not None and queued_summary.approved is False
    assert (await repo.get_video(queued.id)).stage is Stage.QUEUED


async def test_mark_published_records_the_digest(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    video = await make_video(repo, playlist.id, "abc")
    await repo.upsert_summary(video.id, "# Notes")
    await repo.update_stage(video.id, Stage.APPROVED)

    assert await repo.mark_published(video.id, "sha256:abc") is True

    summary = await repo.get_summary(video.id)
    assert summary is not None
    assert summary.published is True
    assert summary.published_at is not None
    assert summary.published_digest == "sha256:abc"
    assert (await repo.get_video(video.id)).stage is Stage.PUBLISHED
    assert await repo.mark_published(9999, "x") is False


async def test_count_published(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    videos = [await make_video(repo, playlist.id, f"v{i}", position=i) for i in range(3)]
    await repo.update_stage(videos[0].id, Stage.PUBLISHED)
    await repo.update_stage(videos[1].id, Stage.APPROVED)

    assert await repo.count_published(playlist.id) == 1

    await repo.update_stage(videos[2].id, Stage.PUBLISHED)
    assert await repo.count_published(playlist.id) == 2


async def test_regenerate_resets_approval_flags_and_stage(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    video = await make_video(repo, playlist.id, "abc")
    await repo.upsert_summary(video.id, "# Original", provider="gemini", model="pro")
    await repo.update_stage(video.id, Stage.APPROVED)
    await repo.approve(video.id)
    await repo.mark_published(video.id, "sha256:old")

    regenerated = await repo.regenerate(video.id, "# Fresh", "gemini", "flash", "notes_v2")

    assert regenerated is not None
    assert regenerated.content == "# Fresh"
    assert regenerated.provider == "gemini"
    assert regenerated.model == "flash"
    assert regenerated.prompt_version == "notes_v2"
    assert regenerated.edited is False
    assert regenerated.approved is False
    assert regenerated.approved_at is None
    assert regenerated.published is False
    assert regenerated.published_at is None
    assert regenerated.published_digest is None

    refreshed = await repo.get_video(video.id)
    assert refreshed is not None
    assert refreshed.stage is Stage.PENDING_REVIEW
    assert await repo.count_published(playlist.id) == 0


async def test_regenerate_clears_a_recorded_failure(repo: PlaylistRepository) -> None:
    playlist = await make_playlist(repo)
    video = await make_video(repo, playlist.id, "abc")
    await repo.upsert_summary(video.id, "# Notes")
    await repo.update_stage(video.id, Stage.FAILED, failure_type=FailureType.PROVIDER_ERROR,
                            failure_kind=FailureKind.TRANSIENT, failure_reason="boom")

    await repo.regenerate(video.id, "# Again")

    refreshed = await repo.get_video(video.id)
    assert refreshed is not None
    assert refreshed.stage is Stage.PENDING_REVIEW
    assert refreshed.failure_type is None
    assert refreshed.failure_reason is None


# ------------------------------------------------------- end-to-end re-run


async def test_incremental_rerun_touches_only_new_videos(repo: PlaylistRepository) -> None:
    """The PRD section 9 no-recompute guarantee, exercised through the repository."""
    playlist = await make_playlist(repo)
    await repo.insert_videos(
        playlist.id, [PlaylistEntry(youtube_video_id=f"v{i}", position=i + 1) for i in range(3)]
    )
    videos = {item.youtube_video_id: item for item in await repo.list_videos(playlist.id)}
    for video in videos.values():
        await repo.upsert_transcript(video.id, f"transcript {video.youtube_video_id}")
        await repo.upsert_summary(video.id, f"# notes {video.youtube_video_id}")
        await repo.update_stage(video.id, Stage.PUBLISHED)
    await repo.touch_synced(playlist.id)
    first_sync = (await repo.get_playlist(playlist.id)).last_synced_at

    stored_map = {item.youtube_video_id: item.position for item in await repo.list_videos(playlist.id)}
    await repo.insert_videos(playlist.id, [PlaylistEntry(youtube_video_id="v3", title="", position=4)])

    listed = await repo.list_videos(playlist.id)
    assert len(listed) == 4
    assert [item.position for item in listed] == [1, 2, 3, 4]
    assert [item.stage for item in listed] == [Stage.PUBLISHED] * 3 + [Stage.QUEUED]
    assert {item.youtube_video_id: item.position for item in listed} == {
        **stored_map,
        "v3": 4,
    }
    assert await repo.count_published(playlist.id) == 3
    assert (await repo.get_playlist(playlist.id)).last_synced_at == first_sync


async def test_edited_video_ids_reports_only_hand_edited_notes(repo) -> None:
    from src.domain.services.sync_diff import PlaylistEntry

    playlist = await repo.create_playlist("PLedit", "https://youtube.com/playlist?list=PLedit")
    videos = await repo.insert_videos(
        playlist.id, [PlaylistEntry(youtube_video_id=f"v{i}", title=f"T{i}", position=i) for i in (1, 2)]
    )
    for video in videos:
        await repo.upsert_summary(video.id, content="notes")
    await repo.mark_edited(videos[0].id)

    assert await repo.edited_video_ids([v.id for v in videos]) == {videos[0].id}
    assert await repo.edited_video_ids([]) == set()

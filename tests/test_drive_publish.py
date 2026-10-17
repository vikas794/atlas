"""Google Drive publishing (PRD M4, section 15, 19).

Two layers are tested on purpose:

* the **use case** against a fake publisher that honours the port contract, to prove that
  progress is persisted immediately and that a half-way failure resumes without duplicates;
* the **real** ``GoogleDrivePublisher`` + ``GoogleDriveExporter`` against stubbed Drive/Docs
  services, so the actual request sequence, retry policy and naming are exercised.
"""

from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import httplib2
import pytest
from googleapiclient.errors import HttpError

from src.application.use_cases.playlist.actions import SaveSummaryEditUseCase
from src.application.use_cases.playlist.publish import PublishPlaylistUseCase
from src.application.worker import PlaylistWorker
from src.domain.exceptions import ConflictError, NotFoundError
from src.domain.interfaces.drive_publisher import (
    DriveAuthorizationRequired,
    DrivePublisherPort,
    PublishEntry,
    PublishProgress,
    PublishResult,
)
from src.domain.models.playlist import FailureType, PipelineError, Stage
from src.domain.services.doc_naming import doc_name, playlist_folder_name
from src.domain.services.sync_diff import PlaylistEntry
from src.infrastructure.google.drive import GoogleDriveExporter
from src.infrastructure.google.publisher import GoogleDrivePublisher
from src.infrastructure.storage import db
from src.infrastructure.storage.playlist_repo import PlaylistRepository
from src.shared.text import sha256_text
from tests.docs_api_schema import validate_requests

# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def repo(tmp_path: Path) -> PlaylistRepository:
    db_path = tmp_path / "test_publish.sqlite3"
    db.initialize(db_path)
    return PlaylistRepository(db_path)


async def seed(
    repo: PlaylistRepository,
    titles: Sequence[str],
    *,
    approve: Sequence[int] | None = None,
    playlist_title: str = "Course 101",
):
    """A playlist whose videos have notes; ``approve`` lists 1-based positions to approve."""
    playlist = await repo.create_playlist("PL_1", "https://yt.com/pl_1", playlist_title)
    videos = await repo.insert_videos(
        playlist.id, [PlaylistEntry(f"vid{i}", title, i) for i, title in enumerate(titles, 1)]
    )
    await repo.update_playlist(playlist.id, video_count=len(videos))
    for video in videos:
        await repo.upsert_summary(video.id, f"## Notes\n\nNotes of {video.title}")
        await repo.update_stage(video.id, Stage.PENDING_REVIEW)
        if approve is None or video.position in approve:
            await repo.approve(video.id)
    return playlist, videos


# ------------------------------------------------------------------ fake publisher


class FakeDrivePublisher(DrivePublisherPort):
    """Port-level fake that obeys the progress contract and can fail on demand."""

    def __init__(self) -> None:
        self.folders: list[str] = []
        self.docs: dict[str, dict[str, Any]] = {}
        self.index: dict[str, list[tuple[int, str]]] = {}
        self.index_writes = 0
        self.renames: list[tuple[str, str]] = []
        self.publish_calls = 0
        self.authorized = True
        self.fail_after_docs: int | None = None  # raise once this many docs were created
        self.fail_format_for: set[int] = set()  # video_pks whose formatting step fails once
        self._next = 1

    def _id(self, prefix: str) -> str:
        value = f"{prefix}{self._next}"
        self._next += 1
        return value

    async def ensure_authorized(self) -> None:
        if not self.authorized:
            raise DriveAuthorizationRequired("Drive is not authorized.")

    async def publish_playlist(
        self,
        *,
        playlist_pk: int,
        playlist_title: str,
        pad_width: int,
        entries: Sequence[PublishEntry],
        index_titles: Sequence[tuple[int, str]],
        update_index: bool,
        existing_folder_id: str | None = None,
        existing_index_doc_id: str | None = None,
        progress: PublishProgress | None = None,
        index_details: Any = None,
    ) -> PublishResult:
        assert progress is not None
        self.index_details = dict(index_details or {})
        self.publish_calls += 1
        folder_id = existing_folder_id
        if not folder_id:
            folder_id = self._id("folder_")
            self.folders.append(folder_id)
            await progress.folder_created(folder_id)
        index_id = existing_index_doc_id
        if not index_id:
            index_id = self._id("idx_")
            self.index[index_id] = []
            await progress.index_doc_created(index_id)
            update_index = True
        if update_index:
            self.index[index_id] = list(index_titles)
            self.index_writes += 1
            await progress.index_written()

        created = updated = 0
        doc_ids: dict[int, str] = {}
        new_docs = 0
        for entry in entries:
            name = doc_name(entry.position, entry.title, width=pad_width)
            if entry.rename_only and entry.existing_doc_id:
                self.docs[entry.existing_doc_id]["name"] = name
                self.renames.append((entry.existing_doc_id, name))
                updated += 1
                continue
            was_created = not entry.existing_doc_id
            if was_created:
                if self.fail_after_docs is not None and new_docs >= self.fail_after_docs:
                    raise PipelineError(FailureType.DRIVE_ERROR, "Drive went away")
                doc_id = self._id("doc_")
                self.docs[doc_id] = {"name": name, "content": "", "folder": folder_id}
                new_docs += 1
                await progress.doc_created(entry.video_pk, doc_id)
            else:
                doc_id = entry.existing_doc_id
            if entry.video_pk in self.fail_format_for:
                self.fail_format_for.discard(entry.video_pk)
                raise PipelineError(FailureType.DRIVE_ERROR, "formatting failed (429)")
            self.docs[doc_id].update(name=name, content=entry.markdown)
            created += was_created
            updated += not was_created
            doc_ids[entry.video_pk] = doc_id
            await progress.doc_published(entry.video_pk, doc_id, created=was_created)
        return PublishResult(
            folder_id=folder_id,
            index_doc_id=index_id,
            created=created,
            updated=updated,
            doc_ids=doc_ids,
        )


@pytest.fixture
def fake() -> FakeDrivePublisher:
    return FakeDrivePublisher()


@pytest.fixture
def use_case(repo: PlaylistRepository, fake: FakeDrivePublisher) -> PublishPlaylistUseCase:
    return PublishPlaylistUseCase(repo=repo, publisher=fake)


# ------------------------------------------------------------------ use case behaviour


async def test_nothing_reaches_drive_before_approval(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, _ = await seed(repo, ["Lesson 1", "Lesson 2"], approve=[])

    result = await use_case.execute(playlist.id)

    assert (result.created, result.updated) == (0, 0)
    assert fake.docs == {} and fake.folders == [] and fake.publish_calls == 0


async def test_first_publish_creates_folder_index_and_numbered_docs(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, videos = await seed(repo, ["Threads & Runnables", "Locks & Synchronization"])

    result = await use_case.execute(playlist.id)

    assert result.created == 2 and result.updated == 0
    stored = await repo.get_playlist(playlist.id)
    assert stored.drive_folder_id == result.folder_id
    assert stored.drive_index_doc_id == result.index_doc_id
    assert stored.pad_width == 1
    for video in await repo.list_videos(playlist.id):
        assert video.stage == Stage.PUBLISHED and video.drive_doc_id
        summary = await repo.get_summary(video.id)
        assert summary.published and summary.published_digest == sha256_text(summary.content)
    names = sorted(doc["name"] for doc in fake.docs.values())
    assert names == ["1 - Threads & Runnables", "2 - Locks & Synchronization"]
    # Index: every title, in position order, titles only.
    assert fake.index[result.index_doc_id] == [
        (1, "Threads & Runnables"),
        (2, "Locks & Synchronization"),
    ]


async def test_publish_order_is_folder_then_index_then_docs(
    repo: PlaylistRepository, use_case: PublishPlaylistUseCase
) -> None:
    order: list[str] = []

    class Recording(FakeDrivePublisher):
        async def publish_playlist(self, *, progress: PublishProgress | None = None, **kw: Any):
            assert progress is not None

            class Spy:
                async def folder_created(self, i: str) -> None:
                    order.append("folder")
                    await progress.folder_created(i)

                async def index_doc_created(self, i: str) -> None:
                    order.append("index")
                    await progress.index_doc_created(i)

                async def index_written(self) -> None:
                    await progress.index_written()

                async def doc_created(self, pk: int, i: str) -> None:
                    order.append(f"doc{pk}")
                    await progress.doc_created(pk, i)

                async def doc_published(self, pk: int, i: str, *, created: bool) -> None:
                    await progress.doc_published(pk, i, created=created)

            return await super().publish_playlist(progress=Spy(), **kw)

    playlist, videos = await seed(repo, ["A", "B", "C"])
    uc = PublishPlaylistUseCase(repo=repo, publisher=Recording())

    await uc.execute(playlist.id)

    assert order == ["folder", "index", *(f"doc{v.id}" for v in videos)]


async def test_running_publish_twice_creates_no_duplicates(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, videos = await seed(repo, ["Intro"])

    first = await use_case.execute(playlist.id)
    second = await use_case.execute(playlist.id)

    assert first.created == 1
    assert (second.created, second.updated) == (0, 0)
    assert len(fake.docs) == 1 and len(fake.folders) == 1
    assert fake.publish_calls == 1  # nothing changed, so Drive was not even contacted again


async def test_an_edit_after_publish_updates_the_same_doc_in_place(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, videos = await seed(repo, ["Intro"])
    first = await use_case.execute(playlist.id)
    doc_id = first.doc_ids[videos[0].id]

    await SaveSummaryEditUseCase(repo).execute(videos[0].id, "## Notes\n\nEdited")
    second = await use_case.execute(playlist.id)

    assert (second.created, second.updated) == (0, 1)
    assert second.doc_ids[videos[0].id] == doc_id
    assert fake.docs[doc_id]["content"] == "## Notes\n\nEdited"
    assert len(fake.docs) == 1
    summary = await repo.get_summary(videos[0].id)
    assert summary.published_digest == sha256_text("## Notes\n\nEdited")


# ---------------------------------------------------------------- B4: resume after failure


async def test_a_failure_half_way_persists_progress_and_resume_creates_no_duplicates(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, videos = await seed(repo, ["One", "Two", "Three", "Four"])
    fake.fail_after_docs = 2  # Drive dies after two docs were created

    with pytest.raises(PipelineError):
        await use_case.execute(playlist.id)

    # Everything created before the failure was persisted immediately.
    stored = await repo.get_playlist(playlist.id)
    assert stored.drive_folder_id is not None and stored.drive_index_doc_id is not None
    rows = await repo.list_videos(playlist.id)
    assert [bool(v.drive_doc_id) for v in rows] == [True, True, False, False]
    assert [v.stage for v in rows] == [Stage.PUBLISHED, Stage.PUBLISHED, Stage.APPROVED, Stage.APPROVED]
    assert len(fake.docs) == 2 and len(fake.folders) == 1

    fake.fail_after_docs = None  # Drive is back; the user clicks publish again
    result = await use_case.execute(playlist.id)

    assert result.created == 2  # only the missing two
    assert len(fake.docs) == 4 and len(fake.folders) == 1  # no duplicates anywhere
    assert len(fake.index) == 1
    assert all(v.stage == Stage.PUBLISHED for v in await repo.list_videos(playlist.id))
    assert sorted(d["name"] for d in fake.docs.values()) == [
        "1 - One", "2 - Two", "3 - Three", "4 - Four",
    ]


async def test_a_failure_while_formatting_a_doc_reuses_the_created_doc_on_resume(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, videos = await seed(repo, ["One", "Two"])
    fake.fail_format_for = {videos[0].id}

    with pytest.raises(PipelineError):
        await use_case.execute(playlist.id)

    # The Doc exists in Drive and its id is stored, even though it was never formatted.
    first = await repo.get_video(videos[0].id)
    assert first.drive_doc_id and first.stage == Stage.APPROVED
    assert len(fake.docs) == 1

    result = await use_case.execute(playlist.id)

    assert len(fake.docs) == 2  # the formatted-later Doc was updated, not duplicated
    assert result.doc_ids[videos[0].id] == first.drive_doc_id
    assert result.created == 1 and result.updated == 1


# -------------------------------------------------------------------- index (M7c)


async def test_the_index_lists_all_titles_in_position_order_even_when_approved_out_of_order(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, videos = await seed(repo, ["A", "B", "C"], approve=[3])
    first = await use_case.execute(playlist.id)
    assert fake.index[first.index_doc_id] == [(1, "A"), (2, "B"), (3, "C")]
    assert fake.index_writes == 1

    await repo.approve(videos[0].id)  # video 1 is approved after video 3 was published
    second = await use_case.execute(playlist.id)

    assert second.created == 1
    assert fake.index[first.index_doc_id] == [(1, "A"), (2, "B"), (3, "C")]  # order intact
    # Titles are unchanged, but video 1 now has a Doc to link to, so the index is rewritten.
    assert fake.index_writes == 2
    assert fake.index_details[1].doc_id is None or fake.index_details[1].description
    assert len(fake.index) == 1  # still one index Doc


async def test_an_unchanged_index_is_not_rewritten(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, _ = await seed(repo, ["A", "B"])
    await use_case.execute(playlist.id)
    assert fake.index_writes == 1

    await use_case.execute(playlist.id)

    assert fake.index_writes == 1  # nothing changed (titles, descriptions, links)


async def test_the_index_gets_each_published_videos_description_and_a_link_flag(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, videos = await seed(repo, ["A", "B"], approve=[1])
    await repo.upsert_summary(
        videos[0].id, "## Summary\n\nA short tour of A.\n\n## Notes\n\nLong notes of A."
    )

    await use_case.execute(playlist.id)

    assert fake.index_details[1].description == "A short tour of A."
    assert 2 not in fake.index_details  # B is not approved: title only


async def test_editing_the_summary_of_a_published_video_rewrites_the_index(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, videos = await seed(repo, ["A"])
    await repo.upsert_summary(videos[0].id, "## Summary\n\nOld.\n\n## Notes\n\nLong notes.")
    await use_case.execute(playlist.id)
    writes = fake.index_writes

    await repo.upsert_summary(videos[0].id, "## Summary\n\nNew.\n\n## Notes\n\nLong notes.")
    await repo.mark_edited(videos[0].id)
    await use_case.execute(playlist.id)

    assert fake.index_writes == writes + 1
    assert fake.index_details[1].description == "New."


async def test_new_videos_from_a_resync_are_added_to_the_index(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, _ = await seed(repo, ["A", "B"])
    first = await use_case.execute(playlist.id)

    new = await repo.insert_videos(playlist.id, [PlaylistEntry("vid3", "C", 3)])
    await repo.upsert_summary(new[0].id, "## Notes\n\nC")
    await repo.update_stage(new[0].id, Stage.PENDING_REVIEW)
    await repo.approve(new[0].id)
    second = await use_case.execute(playlist.id)

    assert second.created == 1 and second.index_doc_id == first.index_doc_id
    assert fake.index[first.index_doc_id] == [(1, "A"), (2, "B"), (3, "C")]
    assert len(fake.docs) == 3  # existing docs not recreated or renumbered


async def test_a_failed_index_update_heals_on_the_next_publish_even_with_nothing_new(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, videos = await seed(repo, ["A"])
    await use_case.execute(playlist.id)
    await repo.insert_videos(playlist.id, [PlaylistEntry("vid2", "B", 2)])  # not approved
    # A previous publish wrote docs but its index write failed: digest is stale.
    await repo.set_index_digest(playlist.id, "stale")

    await use_case.execute(playlist.id)

    stored = await repo.get_playlist(playlist.id)
    assert fake.index[stored.drive_index_doc_id] == [(1, "A"), (2, "B")]


# ------------------------------------------------------------- width growth (M7b)


async def test_pad_width_grows_without_mixed_widths_and_renames_only_when_needed(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    """9 -> 10 videos is the same digit-count jump as 99 -> 100, with a fraction of the rows."""
    playlist, videos = await seed(repo, [f"T{i}" for i in range(1, 10)])
    await use_case.execute(playlist.id)
    assert (await repo.get_playlist(playlist.id)).pad_width == 1
    assert sorted(d["name"] for d in fake.docs.values())[0] == "1 - T1"

    # Same width: a re-publish renames nothing.
    await use_case.execute(playlist.id)
    assert fake.renames == []

    # The 10th video arrives (width 1 -> 2).
    new = await repo.insert_videos(playlist.id, [PlaylistEntry("vid10", "T10", 10)])
    await repo.upsert_summary(new[0].id, "## Notes\n\nx")
    await repo.update_stage(new[0].id, Stage.PENDING_REVIEW)
    await repo.approve(new[0].id)
    await use_case.execute(playlist.id)

    names = sorted(d["name"] for d in fake.docs.values())
    assert names[0] == "01 - T1" and names[-1] == "10 - T10"
    assert {len(name.split(" - ")[0]) for name in names} == {2}  # one width everywhere
    assert len(fake.renames) == 9  # the existing docs were renamed, not recreated
    assert len(fake.docs) == 10
    assert (await repo.get_playlist(playlist.id)).pad_width == 2

    # A further publish at the new width renames nothing more.
    await use_case.execute(playlist.id)
    assert len(fake.renames) == 9


async def test_the_pad_width_is_based_on_the_largest_position_not_the_video_count(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, videos = await seed(repo, ["A"])
    await repo.insert_videos(playlist.id, [PlaylistEntry("far", "Far", 120)])  # position gap

    await use_case.execute(playlist.id)

    assert next(iter(fake.docs.values()))["name"] == "001 - A"


# ----------------------------------------------------------- M1: reprocess keeps ids


async def test_reprocess_then_republish_updates_the_existing_docs(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    from src.application.use_cases.playlist.actions import ReprocessPlaylistUseCase

    playlist, videos = await seed(repo, ["A", "B"])
    first = await use_case.execute(playlist.id)
    doc_ids = dict(first.doc_ids)

    await ReprocessPlaylistUseCase(repo).execute(playlist.id)
    stored = await repo.get_playlist(playlist.id)
    assert stored.drive_folder_id == first.folder_id
    assert stored.drive_index_doc_id == first.index_doc_id
    for video in videos:
        await repo.upsert_summary(video.id, f"## Notes\n\nregenerated {video.title}")
        await repo.update_stage(video.id, Stage.PENDING_REVIEW)
        await repo.approve(video.id)
    second = await use_case.execute(playlist.id)

    assert second.created == 0 and second.updated == 2
    assert second.doc_ids == doc_ids
    assert len(fake.docs) == 2 and len(fake.folders) == 1


# ------------------------------------------------- request / background run (M8)


async def test_request_publish_validates_and_marks_the_playlist_publishing(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, _ = await seed(repo, ["A"])

    updated = await use_case.request_publish(playlist.id)

    assert updated.status == "publishing"
    assert fake.publish_calls == 0  # nothing slow ran in the request
    with pytest.raises(ConflictError):
        await use_case.request_publish(playlist.id)  # already publishing
    with pytest.raises(NotFoundError):
        await use_case.request_publish(999)


async def test_request_publish_without_drive_consent_is_rejected_up_front(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, _ = await seed(repo, ["A"])
    fake.authorized = False

    with pytest.raises(DriveAuthorizationRequired):
        await use_case.request_publish(playlist.id)

    assert (await repo.get_playlist(playlist.id)).status == "ingested"


async def wait_until(predicate: Callable[[], Awaitable[bool]], timeout: float = 20.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


async def test_the_worker_runs_a_requested_publish_and_reports_status(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    from src.application.use_cases.playlist.process_video import ProcessVideoUseCase

    playlist, videos = await seed(repo, ["A", "B"])
    process = ProcessVideoUseCase(repo, None, None)  # nothing queued: never used
    worker = PlaylistWorker(repo, process, publish=use_case, poll_interval_seconds=0.02)
    await worker.start()
    try:
        await use_case.request_publish(playlist.id)
        worker.enqueue_publish(playlist.id)

        async def done() -> bool:
            return (await repo.get_playlist(playlist.id)).status == "published"

        await wait_until(done)
    finally:
        await worker.stop()

    stored = await repo.get_playlist(playlist.id)
    assert stored.last_publish_error is None
    assert len(fake.docs) == 2


async def test_a_failed_background_publish_is_visible_and_can_be_retried(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    playlist, _ = await seed(repo, ["A", "B"])
    fake.fail_after_docs = 1

    await use_case.request_publish(playlist.id)
    assert await use_case.run(playlist.id) is None  # never raises

    stored = await repo.get_playlist(playlist.id)
    assert stored.status == "publish_failed"
    assert "Drive went away" in stored.last_publish_error

    fake.fail_after_docs = None
    await use_case.request_publish(playlist.id)  # allowed again after a failure
    result = await use_case.run(playlist.id)

    assert result.created == 1
    stored = await repo.get_playlist(playlist.id)
    assert stored.status == "published" and stored.last_publish_error is None
    assert len(fake.docs) == 2


async def test_a_publish_interrupted_by_a_crash_is_resumed_on_startup(
    repo: PlaylistRepository, fake: FakeDrivePublisher, use_case: PublishPlaylistUseCase
) -> None:
    from src.application.use_cases.playlist.process_video import ProcessVideoUseCase

    playlist, _ = await seed(repo, ["A"])
    await repo.begin_publish(playlist.id)  # status left 'publishing' by a crashed process
    worker = PlaylistWorker(
        repo, ProcessVideoUseCase(repo, None, None), publish=use_case, poll_interval_seconds=0.02
    )

    await worker.start()
    try:
        async def done() -> bool:
            return (await repo.get_playlist(playlist.id)).status == "published"

        await wait_until(done)
    finally:
        await worker.stop()
    assert len(fake.docs) == 1


# =============================================================================
# The REAL GoogleDrivePublisher against stubbed Google services
# =============================================================================


def http_error(status: int, reason: str = "", uri: str = "https://www.googleapis.com/drive/v3/files") -> HttpError:
    body = b'{"error": {"errors": [{"reason": "%s"}]}}' % reason.encode()
    return HttpError(httplib2.Response({"status": str(status)}), body, uri=uri)


class _Call:
    def __init__(self, owner: Any, kind: str, kwargs: dict[str, Any]) -> None:
        self.owner, self.kind, self.kwargs = owner, kind, kwargs

    def execute(self) -> Any:
        return self.owner._execute(self.kind, self.kwargs)


class StubGoogle:
    """One object standing in for both the Drive and the Docs service (and their resources)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.next_id = 1
        self.created: list[dict[str, Any]] = []  # files().create bodies, in order
        self.updates: list[dict[str, Any]] = []  # files().update calls
        self.batches: list[tuple[str, list[dict[str, Any]]]] = []  # (doc id, requests)
        self.end_index: dict[str, int] = {}
        self.failures: dict[str, list[Exception]] = {}  # kind -> queue of errors to raise
        #: (kind, predicate over the call kwargs, queue of errors) -- fail only matching calls
        self.conditional: list[tuple[str, Callable[[dict[str, Any]], bool], list[Exception]]] = []
        self.thread_ids: set[int] = set()
        #: kind -> errors raised *after* the call took effect (the response was lost)
        self.lost_responses: dict[str, list[Exception]] = {}
        self.trashed: set[str] = set()  # file ids in the Drive trash
        self.deleted: set[str] = set()  # file ids permanently deleted (404)
        self.calls: list[str] = []  # every executed call kind, in order

    # resources
    def files(self) -> Any:
        stub = self
        return type(
            "Files",
            (),
            {
                "create": lambda _s, **kw: _Call(stub, "files.create", kw),
                "update": lambda _s, **kw: _Call(stub, "files.update", kw),
                "list": lambda _s, **kw: _Call(stub, "files.list", kw),
                "get": lambda _s, **kw: _Call(stub, "files.get", kw),
            },
        )()

    def documents(self) -> Any:
        stub = self
        return type(
            "Documents",
            (),
            {
                "get": lambda _s, **kw: _Call(stub, "documents.get", kw),
                "batchUpdate": lambda _s, **kw: _Call(stub, "documents.batchUpdate", kw),
            },
        )()

    def fail(self, kind: str, *errors: Exception) -> None:
        self.failures.setdefault(kind, []).extend(errors)

    def lose_response(self, kind: str, *errors: Exception) -> None:
        """The next ``kind`` calls succeed in Drive but the client sees ``errors``."""
        self.lost_responses.setdefault(kind, []).extend(errors)

    def live(self) -> list[dict[str, Any]]:
        """Created files that are neither trashed nor deleted."""
        return [c for c in self.created if c["id"] not in self.trashed | self.deleted]

    def _list(self, q: str) -> dict[str, Any]:
        parents = re.findall(r"'([^']+)' in parents", q)
        mime = re.search(r"mimeType = '([^']+)'", q)
        props = dict(re.findall(r"key='([^']*)' and value='([^']*)'", q))
        hits = [
            c
            for c in self.live()
            if (not parents or parents[0] in c.get("parents", []))
            and (not mime or c["mimeType"] == mime.group(1))
            and all(c.get("appProperties", {}).get(k) == v for k, v in props.items())
        ]
        return {"files": [{"id": c["id"]} for c in hits]}

    def fail_if(
        self, kind: str, predicate: Callable[[dict[str, Any]], bool], *errors: Exception
    ) -> list[Exception]:
        queue = list(errors)
        self.conditional.append((kind, predicate, queue))
        return queue

    def _execute(self, kind: str, kw: dict[str, Any]) -> Any:
        self.thread_ids.add(threading.get_ident())
        with self.lock:
            for cond_kind, predicate, cond_queue in self.conditional:
                if cond_kind == kind and cond_queue and predicate(kw):
                    raise cond_queue.pop(0)
            queue = self.failures.get(kind)
            if queue:
                raise queue.pop(0)
            self.calls.append(kind)
            result = self._apply(kind, kw)
            lost = self.lost_responses.get(kind)
            if lost:
                raise lost.pop(0)
            return result

    def _apply(self, kind: str, kw: dict[str, Any]) -> Any:
        target = kw.get("fileId") or kw.get("documentId")
        if target is not None and target in self.deleted:
            raise http_error(404, "notFound")
        if kind == "files.list":
            return self._list(kw["q"])
        if kind == "files.get":
            return {"id": target, "trashed": target in self.trashed}
        if kind == "files.create":
            new_id = f"id{self.next_id}"
            self.next_id += 1
            self.created.append({"id": new_id, **kw["body"]})
            self.end_index[new_id] = 2
            return {"id": new_id}
        if kind == "files.update":
            self.updates.append({"fileId": kw["fileId"], **kw["body"]})
            return {}
        if kind == "documents.get":
            end = self.end_index.get(kw["documentId"], 2)
            return {"body": {"content": [{"endIndex": end}]}}
        if kind == "documents.batchUpdate":
            doc = kw["documentId"]
            requests = kw["body"]["requests"]
            validate_requests(requests)  # the real Docs API rejects unknown fields
            self.batches.append((doc, requests))
            for request in requests:
                if "insertText" in request:
                    self.end_index[doc] += len(request["insertText"]["text"])
                if "deleteContentRange" in request:
                    rng = request["deleteContentRange"]["range"]
                    self.end_index[doc] -= rng["endIndex"] - rng["startIndex"]
            return {}
        raise AssertionError(kind)

    # helpers for assertions
    def docs_named(self, prefix: str) -> list[dict[str, Any]]:
        return [c for c in self.created if c["name"].startswith(prefix)]

    def inserted_text(self, doc_id: str) -> str:
        return "".join(
            r["insertText"]["text"]
            for doc, requests in self.batches
            if doc == doc_id
            for r in requests
            if "insertText" in r
        )


def real_publisher(stub: StubGoogle, **kwargs: Any) -> tuple[GoogleDrivePublisher, dict[str, int]]:
    factory_calls = {"drive": 0, "docs": 0}

    def drive_factory() -> StubGoogle:
        factory_calls["drive"] += 1
        return stub

    def docs_factory() -> StubGoogle:
        factory_calls["docs"] += 1
        return stub

    exporter = GoogleDriveExporter(
        creds_path="unused-credentials.json",
        token_path="unused-token.json",
        drive_service_factory=drive_factory,
        docs_service_factory=docs_factory,
    )
    kwargs.setdefault("backoff_base", 0.0)  # no real sleeping in tests
    return GoogleDrivePublisher(exporter=exporter, **kwargs), factory_calls


@pytest.fixture
def stub() -> StubGoogle:
    return StubGoogle()


async def test_real_publisher_builds_folder_index_and_headed_docs(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    playlist, videos = await seed(repo, [f"Topic {i}" for i in range(1, 13)], approve=[12, 1])
    publisher, _ = real_publisher(stub)
    uc = PublishPlaylistUseCase(repo=repo, publisher=publisher)

    result = await uc.execute(playlist.id)

    assert result.created == 2
    folder = stub.created[0]
    assert folder["mimeType"] == "application/vnd.google-apps.folder"
    assert folder["name"] == playlist_folder_name("Course 101")
    index = stub.created[1]
    assert index["name"] == "00 - Index - Course 101" and index["parents"] == [result.folder_id]
    # Index is the first Doc in the folder; its text is the 12 titles in order, each published
    # video's title followed by its short description (here the Notes fallback).
    index_text = stub.inserted_text(result.index_doc_id)
    assert index_text == "".join(
        f"Topic {i} – Notes of Topic {i}\n" if i in (1, 12) else f"Topic {i}\n"
        for i in range(1, 13)
    )
    # The published videos' titles link to their Docs (written after the Docs exist).
    published_ids = {c["id"] for c in stub.created[2:]}
    links = {
        r["updateTextStyle"]["textStyle"]["link"]["url"]
        for doc, reqs in stub.batches
        if doc == result.index_doc_id
        for r in reqs
        if "link" in r.get("updateTextStyle", {}).get("textStyle", {})
    }
    assert links == {f"https://docs.google.com/document/d/{doc_id}/edit" for doc_id in published_ids}
    # Doc names: 1-based, zero-padded to the width of the largest position (12 -> 2 digits).
    names = [c["name"] for c in stub.created[2:]]
    assert names == ["01 - Topic 1", "12 - Topic 12"]
    assert all(c["parents"] == [result.folder_id] for c in stub.created[1:])
    # Each Doc starts with its heading (number + title) followed by the notes.
    twelve = stub.created[3]["id"]
    text = stub.inserted_text(twelve)
    assert text.startswith("12 - Topic 12\n")
    assert "Notes of Topic 12" in text
    heading_style = [
        r for doc, reqs in stub.batches if doc == twelve for r in reqs if "updateParagraphStyle" in r
    ]
    assert heading_style[0]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "HEADING_1"
    # Ids were persisted as they were created.
    stored = await repo.get_playlist(playlist.id)
    assert stored.drive_folder_id == folder["id"] and stored.drive_index_doc_id == index["id"]


def formats_doc_with(text: str) -> Callable[[dict[str, Any]], bool]:
    """Matches the Docs batchUpdate that writes a video Doc containing ``text``."""

    def predicate(kw: dict[str, Any]) -> bool:
        return any(text in r.get("insertText", {}).get("text", "") for r in kw["body"]["requests"])

    return predicate


async def test_a_429_while_formatting_does_not_create_a_second_doc(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    playlist, videos = await seed(repo, ["Only"])
    publisher, _ = real_publisher(stub)
    uc = PublishPlaylistUseCase(repo=repo, publisher=publisher)
    # The Doc is created, then its format step is rate limited twice before it works.
    stub.fail_if("documents.batchUpdate", formats_doc_with("Notes of Only"),
                 http_error(429), http_error(429))

    result = await uc.execute(playlist.id)

    docs = stub.docs_named("1 - Only")
    assert len(docs) == 1  # create ran once; only the format step was retried
    assert result.created == 1
    assert "Notes of Only" in stub.inserted_text(docs[0]["id"])


async def test_an_exhausted_format_retry_leaves_a_resumable_doc_not_an_orphan(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    playlist, videos = await seed(repo, ["Only"])
    publisher, _ = real_publisher(stub, max_retries=1)
    uc = PublishPlaylistUseCase(repo=repo, publisher=publisher)
    stub.fail_if("documents.batchUpdate", formats_doc_with("Notes of Only"),
                 http_error(503), http_error(503))  # both attempts fail

    with pytest.raises(PipelineError) as excinfo:
        await uc.execute(playlist.id)
    assert excinfo.value.failure_type == FailureType.DRIVE_ERROR

    doc_id = (await repo.get_video(videos[0].id)).drive_doc_id
    assert doc_id is not None  # persisted although formatting never succeeded
    assert len(stub.docs_named("1 - Only")) == 1
    assert (await repo.get_video(videos[0].id)).stage == Stage.APPROVED

    await uc.execute(playlist.id)  # resume: Drive is healthy again

    assert len(stub.docs_named("1 - Only")) == 1  # still exactly one Doc in Drive
    assert (await repo.get_video(videos[0].id)).drive_doc_id == doc_id
    assert "Notes of Only" in stub.inserted_text(doc_id)
    assert (await repo.get_video(videos[0].id)).stage == Stage.PUBLISHED


@pytest.mark.parametrize(
    "error",
    [
        http_error(429),
        http_error(503),
        http_error(403, "rateLimitExceeded"),
        http_error(403, "userRateLimitExceeded"),
        ConnectionResetError("connection reset by peer"),
        TimeoutError("timed out"),
        httplib2.ServerNotFoundError("Unable to find the server"),
    ],
)
async def test_transient_drive_errors_are_retried_and_then_succeed(
    stub: StubGoogle, error: Exception
) -> None:
    publisher, _ = real_publisher(stub, max_retries=3)
    stub.fail("files.create", error, error)

    result = await publisher.publish_playlist(
        playlist_pk=1, playlist_title="T", pad_width=1, entries=[],
        index_titles=[], update_index=False,
    )

    assert result.folder_id == "id1"
    folders = [c for c in stub.created if c["mimeType"].endswith("folder")]
    assert len(folders) == 1  # failed twice, created once


@pytest.mark.parametrize(
    ("error", "status"),
    [(http_error(403, "forbidden"), 403), (http_error(404, "notFound"), 404), (http_error(400, "badRequest"), 400)],
)
async def test_permanent_http_errors_are_not_retried(
    stub: StubGoogle, error: HttpError, status: int
) -> None:
    publisher, _ = real_publisher(stub, max_retries=3)
    stub.fail("files.create", error, http_error(200))

    with pytest.raises(PipelineError) as excinfo:
        await publisher.publish_playlist(
            playlist_pk=1, playlist_title="T", pad_width=1, entries=[],
            index_titles=[], update_index=False,
        )

    assert f"HTTP {status}" in excinfo.value.reason
    assert len(stub.failures["files.create"]) == 1  # the second queued error was never consumed


async def test_retry_limits_come_from_configuration(stub: StubGoogle) -> None:
    publisher, _ = real_publisher(stub, max_retries=2)
    stub.fail("files.create", *[http_error(503) for _ in range(10)])

    with pytest.raises(PipelineError):
        await publisher.publish_playlist(
            playlist_pk=1, playlist_title="T", pad_width=1, entries=[],
            index_titles=[], update_index=False,
        )

    assert len(stub.failures["files.create"]) == 10 - 3  # 1 attempt + 2 retries


async def test_drive_errors_never_leak_an_api_key(stub: StubGoogle) -> None:
    publisher, _ = real_publisher(stub, max_retries=0)
    leaky = http_error(
        400, "badRequest", uri="https://www.googleapis.com/drive/v3/files?alt=json&key=AIzaLEAKYLEAKY1234567"
    )
    stub.fail("files.create", leaky)

    with pytest.raises(PipelineError) as excinfo:
        await publisher.publish_playlist(
            playlist_pk=1, playlist_title="T", pad_width=1, entries=[],
            index_titles=[], update_index=False,
        )

    assert "AIzaLEAKYLEAKY1234567" not in str(excinfo.value)
    assert "AIzaLEAKYLEAKY1234567" not in excinfo.value.reason


async def test_updating_an_existing_doc_replaces_its_body_in_place(stub: StubGoogle) -> None:
    publisher, _ = real_publisher(stub)
    stub.end_index["doc-1"] = 40  # a Doc that already has content

    result = await publisher.publish_playlist(
        playlist_pk=1, playlist_title="T", pad_width=2,
        entries=[PublishEntry(7, 3, "Streams", "## Notes\n\nnew text", existing_doc_id="doc-1")],
        index_titles=[], update_index=False, existing_folder_id="folder-1",
        existing_index_doc_id="idx-1",
    )

    assert result.updated == 1 and result.created == 0 and stub.created == []
    (doc, requests), = stub.batches
    assert doc == "doc-1"
    assert requests[0] == {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": 39}}}
    assert stub.updates == [{"fileId": "doc-1", "name": "03 - Streams"}]


async def test_filling_a_brand_new_empty_doc_sends_no_delete_range(stub: StubGoogle) -> None:
    publisher, _ = real_publisher(stub)

    await publisher.publish_playlist(
        playlist_pk=1, playlist_title="T", pad_width=1, entries=[],
        index_titles=[(1, "A")], update_index=True,
    )

    index_batches = [reqs for _doc, reqs in stub.batches]
    assert index_batches and not any("deleteContentRange" in r for r in index_batches[0])


async def test_rename_only_entries_touch_the_name_and_nothing_else(stub: StubGoogle) -> None:
    publisher, _ = real_publisher(stub)

    await publisher.publish_playlist(
        playlist_pk=1, playlist_title="T", pad_width=3,
        entries=[PublishEntry(7, 9, "Old doc", "", existing_doc_id="doc-9", rename_only=True)],
        index_titles=[], update_index=False, existing_folder_id="f", existing_index_doc_id="i",
    )

    assert stub.updates == [{"fileId": "doc-9", "name": "009 - Old doc"}]
    assert stub.batches == [] and stub.created == []


async def test_google_services_are_built_per_thread_not_shared(stub: StubGoogle) -> None:
    """httplib2 is not thread-safe: each worker thread must get its own service."""
    publisher, calls = real_publisher(stub)
    exporter = publisher.exporter
    seen: dict[int, Any] = {}

    def grab() -> None:
        first = exporter.drive_service
        assert exporter.drive_service is first  # reused within one thread
        seen[threading.get_ident()] = first

    threads = [threading.Thread(target=grab) for _ in range(3)]
    for thread in threads:
        thread.start()
        thread.join()

    assert calls["drive"] == 3  # one build per thread
    assert len(seen) == 3


async def test_ensure_authorized_without_a_token_raises_the_domain_error(tmp_path: Path) -> None:
    exporter = GoogleDriveExporter(
        creds_path=tmp_path / "credentials.json", token_path=tmp_path / "token.json"
    )
    publisher = GoogleDrivePublisher(exporter=exporter)

    with pytest.raises(DriveAuthorizationRequired) as excinfo:
        await publisher.ensure_authorized()

    assert "Open Settings in Atlas and authorize Google Drive" in str(excinfo.value)


def test_drive_auth_state_distinguishes_missing_credentials_from_needing_consent(
    tmp_path: Path,
) -> None:
    creds, token = tmp_path / "credentials.json", tmp_path / "token.json"
    exporter = GoogleDriveExporter(creds_path=creds, token_path=token)
    assert exporter.auth_state()[0] == "credentials_missing"

    creds.write_text("{}")
    assert exporter.auth_state()[0] == "needs_consent"
    assert exporter.auth_status()[0] is False  # credentials alone are not "authorized"

    token.write_text("not a token")  # an unusable token is not authorization either
    assert exporter.auth_state()[0] == "needs_consent"
    assert exporter.auth_status()[0] is False

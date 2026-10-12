"""End-to-end publish scenarios with the REAL publisher + exporter against stubbed Google APIs.

``test_drive_publish.py`` proves the use case against a port-level fake. These tests run
``PublishPlaylistUseCase`` -> ``GoogleDrivePublisher`` -> ``GoogleDriveExporter`` with only
the Drive/Docs service objects faked (``StubGoogle`` records every call, validates every
Docs request against the documented schema, and can fail, lose responses, trash or delete
files), so the actual Google call sequence is what is asserted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.application.use_cases.playlist.actions import SaveSummaryEditUseCase
from src.application.use_cases.playlist.publish import PublishPlaylistUseCase
from src.domain.models.playlist import PipelineError, Stage
from src.domain.services.sync_diff import PlaylistEntry
from src.infrastructure.google.drive import DOC_MIME_TYPE, FOLDER_MIME_TYPE
from src.infrastructure.storage import db
from src.infrastructure.storage.playlist_repo import PlaylistRepository
from tests.test_drive_publish import StubGoogle, http_error, real_publisher, seed


@pytest.fixture
def repo(tmp_path: Path) -> PlaylistRepository:
    db_path = tmp_path / "resilience.sqlite3"
    db.initialize(db_path)
    return PlaylistRepository(db_path)


@pytest.fixture
def stub() -> StubGoogle:
    return StubGoogle()


def use_case(repo: PlaylistRepository, stub: StubGoogle, **kw) -> PublishPlaylistUseCase:
    publisher, _ = real_publisher(stub, **kw)
    return PublishPlaylistUseCase(repo=repo, publisher=publisher)


def docs(stub: StubGoogle) -> list[dict]:
    return [c for c in stub.live() if c["mimeType"] == DOC_MIME_TYPE]


def folders(stub: StubGoogle) -> list[dict]:
    return [c for c in stub.live() if c["mimeType"] == FOLDER_MIME_TYPE]


def last_body_text(stub: StubGoogle, doc_id: str) -> str:
    """The text of the most recent full-body write to ``doc_id``."""
    for doc, requests in reversed(stub.batches):
        if doc == doc_id:
            return "".join(r["insertText"]["text"] for r in requests if "insertText" in r)
    return ""


async def test_first_publish_creates_folder_then_index_then_docs_in_playlist_order(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    playlist, videos = await seed(repo, ["Alpha", "Beta", "Gamma"])

    await use_case(repo, stub).execute(playlist.id)

    kinds = [(c["mimeType"].rsplit(".", 1)[-1], c["name"]) for c in stub.created]
    assert kinds == [
        ("folder", "Course 101"),
        ("document", "00 - Index - Course 101"),
        ("document", "1 - Alpha"),
        ("document", "2 - Beta"),
        ("document", "3 - Gamma"),
    ]
    rows = await repo.list_videos(playlist.id)
    assert all(v.stage == Stage.PUBLISHED and v.drive_doc_id for v in rows)


async def test_second_publish_creates_zero_duplicates_and_makes_no_drive_calls(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    playlist, _ = await seed(repo, ["Alpha", "Beta"])
    uc = use_case(repo, stub)
    await uc.execute(playlist.id)
    created_before, calls_before = len(stub.created), len(stub.calls)

    result = await uc.execute(playlist.id)

    assert (result.created, result.updated) == (0, 0)
    assert len(stub.created) == created_before
    assert len(stub.calls) == calls_before  # nothing to do -> Drive not contacted


async def test_failure_on_doc_3_of_5_then_resume_creates_only_the_remaining_docs(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    playlist, videos = await seed(repo, [f"Lesson {i}" for i in range(1, 6)])
    uc = use_case(repo, stub, max_retries=1)
    # The 3rd video Doc's creation fails permanently (HTTP 403 forbidden, not a rate limit).
    stub.fail_if(
        "files.create",
        lambda kw: kw["body"]["name"] == "3 - Lesson 3",
        http_error(403, "forbidden"),
    )

    with pytest.raises(PipelineError) as excinfo:
        await uc.execute(playlist.id)
    assert "HTTP 403" in excinfo.value.reason

    rows = await repo.list_videos(playlist.id)
    assert [v.stage for v in rows] == [Stage.PUBLISHED] * 2 + [Stage.APPROVED] * 3
    assert [d["name"] for d in docs(stub)] == ["00 - Index - Course 101", "1 - Lesson 1", "2 - Lesson 2"]

    result = await uc.execute(playlist.id)

    assert result.created == 3
    assert [d["name"] for d in docs(stub)] == [
        "00 - Index - Course 101",
        *(f"{i} - Lesson {i}" for i in range(1, 6)),
    ]
    assert len(folders(stub)) == 1
    assert all(v.stage == Stage.PUBLISHED for v in await repo.list_videos(playlist.id))


async def test_edit_after_publish_republishes_into_the_same_doc_in_place(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    playlist, videos = await seed(repo, ["Alpha"])
    uc = use_case(repo, stub)
    await uc.execute(playlist.id)
    doc_id = (await repo.get_video(videos[0].id)).drive_doc_id

    await SaveSummaryEditUseCase(repo).execute(videos[0].id, "## Notes\n\nEdited `List<String>`")
    result = await uc.execute(playlist.id)

    assert (result.created, result.updated) == (0, 1)
    assert len(docs(stub)) == 2  # index + the one video Doc
    body = last_body_text(stub, doc_id)
    assert body.startswith("1 - Alpha\n") and "Edited List<String>" in body
    final_requests = next(r for d, r in reversed(stub.batches) if d == doc_id)
    assert "deleteContentRange" in final_requests[0]  # old body cleared, not appended to


@pytest.mark.parametrize("error", [http_error(429), http_error(500), http_error(503)])
async def test_429_and_5xx_on_every_kind_of_call_are_retried(
    repo: PlaylistRepository, stub: StubGoogle, error: Exception
) -> None:
    playlist, _ = await seed(repo, ["Alpha", "Beta"])
    for kind in ("files.create", "documents.get", "documents.batchUpdate", "files.list"):
        stub.fail(kind, error)

    result = await use_case(repo, stub, max_retries=2).execute(playlist.id)

    assert result.created == 2
    assert len(folders(stub)) == 1 and len(docs(stub)) == 3


async def test_403_user_rate_limit_is_retried_but_403_forbidden_is_not(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    playlist, _ = await seed(repo, ["Alpha"])
    stub.fail("files.create", http_error(403, "userRateLimitExceeded"))
    await use_case(repo, stub).execute(playlist.id)
    assert len(docs(stub)) == 2

    other = StubGoogle()
    playlist2 = await repo.create_playlist("PL_2", "https://yt.com/pl_2", "Two")
    vids = await repo.insert_videos(playlist2.id, [PlaylistEntry("x1", "X", 1)])
    await repo.upsert_summary(vids[0].id, "notes")
    await repo.update_stage(vids[0].id, Stage.PENDING_REVIEW)
    await repo.approve(vids[0].id)
    other.fail("files.create", http_error(403, "forbidden"), http_error(403, "forbidden"))
    with pytest.raises(PipelineError):
        await use_case(repo, other, max_retries=3).execute(playlist2.id)
    assert len(other.failures["files.create"]) == 1  # one attempt, no retry


# ----------------------------------------------- ambiguous failures (lost responses)


async def test_a_lost_create_response_adopts_the_doc_instead_of_duplicating_it(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    playlist, videos = await seed(repo, ["Alpha"])
    # The video Doc is created in Drive, but the client sees a connection reset.
    original = stub._apply
    lost = {"done": False}

    def apply(kind: str, kw: dict) -> object:
        result = original(kind, kw)
        if kind == "files.create" and kw["body"]["name"] == "1 - Alpha" and not lost["done"]:
            lost["done"] = True
            raise ConnectionResetError("connection reset by peer")
        return result

    stub._apply = apply  # type: ignore[method-assign]

    await use_case(repo, stub).execute(playlist.id)

    alpha = [d for d in docs(stub) if d["name"] == "1 - Alpha"]
    assert len(alpha) == 1  # adopted via its appProperties tag, not created twice
    assert (await repo.get_video(videos[0].id)).drive_doc_id == alpha[0]["id"]
    assert "Notes of Alpha" in last_body_text(stub, alpha[0]["id"])


async def test_a_lost_folder_create_response_does_not_leave_two_folders(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    playlist, _ = await seed(repo, ["Alpha"])
    original = stub._apply
    lost = {"done": False}

    def apply(kind: str, kw: dict) -> object:
        result = original(kind, kw)
        if kind == "files.create" and kw["body"]["mimeType"] == FOLDER_MIME_TYPE and not lost["done"]:
            lost["done"] = True
            raise TimeoutError("timed out")
        return result

    stub._apply = apply  # type: ignore[method-assign]

    await use_case(repo, stub).execute(playlist.id)

    assert len(folders(stub)) == 1
    assert (await repo.get_playlist(playlist.id)).drive_folder_id == folders(stub)[0]["id"]


async def test_a_lost_content_write_response_does_not_insert_the_notes_twice(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    playlist, videos = await seed(repo, ["Alpha"])
    original = stub._apply
    lost = {"done": False}

    def apply(kind: str, kw: dict) -> object:
        result = original(kind, kw)
        texts = [r["insertText"]["text"] for r in kw.get("body", {}).get("requests", []) if "insertText" in r]
        if kind == "documents.batchUpdate" and any("Notes of Alpha" in t for t in texts) and not lost["done"]:
            lost["done"] = True
            raise ConnectionResetError("reset")
        return result

    stub._apply = apply  # type: ignore[method-assign]

    await use_case(repo, stub).execute(playlist.id)

    doc_id = (await repo.get_video(videos[0].id)).drive_doc_id
    body = last_body_text(stub, doc_id)
    # The retry cleared what the lost write had inserted, then wrote the body once.
    assert stub.end_index[doc_id] == 2 + len(body)
    assert body.count("Notes of Alpha") == 1


async def test_a_doc_created_before_a_crash_but_never_stored_is_adopted_on_resume(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    """The unprotected window: Drive created the Doc, the process died before the id was
    written to the database. The resumed publish finds it by its tag."""
    playlist, videos = await seed(repo, ["Alpha", "Beta"])
    uc = use_case(repo, stub)
    await uc.execute(playlist.id)
    beta = await repo.get_video(videos[1].id)
    # Simulate: Beta's id never reached the database and it was never marked published.
    await repo.set_drive_doc_id(beta.id, None)
    await repo.update_stage(beta.id, Stage.APPROVED)
    docs_before = len(docs(stub))

    await uc.execute(playlist.id)

    assert len(docs(stub)) == docs_before
    assert (await repo.get_video(beta.id)).drive_doc_id == beta.drive_doc_id



# ------------------------------------------ objects deleted or trashed by the user


async def _add_approved_video(repo: PlaylistRepository, playlist_id: int, position: int, title: str) -> None:
    new = await repo.insert_videos(playlist_id, [PlaylistEntry(f"vid{position}", title, position)])
    await repo.upsert_summary(new[0].id, f"## Notes\n\nNotes of {title}")
    await repo.update_stage(new[0].id, Stage.PENDING_REVIEW)
    await repo.approve(new[0].id)


@pytest.mark.parametrize("how", ["trashed", "deleted"])
async def test_a_folder_deleted_in_drive_is_recreated_with_every_published_doc(
    repo: PlaylistRepository, stub: StubGoogle, how: str
) -> None:
    playlist, videos = await seed(repo, ["Alpha", "Beta"])
    uc = use_case(repo, stub)
    await uc.execute(playlist.id)
    old_folder = (await repo.get_playlist(playlist.id)).drive_folder_id
    gone = {c["id"] for c in stub.created}  # the folder and everything in it
    (stub.trashed if how == "trashed" else stub.deleted).update(gone)
    await _add_approved_video(repo, playlist.id, 3, "Late")

    result = await uc.execute(playlist.id)

    stored = await repo.get_playlist(playlist.id)
    assert stored.drive_folder_id not in (None, old_folder)
    assert [c["name"] for c in stub.live()] == [
        "Course 101",
        "00 - Index - Course 101",
        "1 - Alpha",
        "2 - Beta",
        "3 - Late",
    ]
    assert all(c["parents"] == [stored.drive_folder_id] for c in stub.live()[1:])
    assert result.created == 3
    for video in await repo.list_videos(playlist.id):
        assert video.stage == Stage.PUBLISHED and video.drive_doc_id not in gone


@pytest.mark.parametrize("how", ["trashed", "deleted"])
async def test_a_single_doc_deleted_in_drive_is_recreated_on_republish(
    repo: PlaylistRepository, stub: StubGoogle, how: str
) -> None:
    playlist, videos = await seed(repo, ["Alpha", "Beta"])
    uc = use_case(repo, stub)
    await uc.execute(playlist.id)
    alpha_old = (await repo.get_video(videos[0].id)).drive_doc_id
    (stub.trashed if how == "trashed" else stub.deleted).add(alpha_old)
    await SaveSummaryEditUseCase(repo).execute(videos[0].id, "## Notes\n\nFixed typo")

    result = await uc.execute(playlist.id)

    alpha_new = (await repo.get_video(videos[0].id)).drive_doc_id
    assert alpha_new != alpha_old and result.created == 1
    assert "Fixed typo" in last_body_text(stub, alpha_new)
    assert [d["name"] for d in docs(stub)].count("1 - Alpha") == 1


async def test_padding_growth_recreates_a_doc_that_vanished_instead_of_failing(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    playlist, videos = await seed(repo, [f"T{i}" for i in range(1, 10)])
    uc = use_case(repo, stub)
    await uc.execute(playlist.id)
    t1_old = (await repo.get_video(videos[0].id)).drive_doc_id
    stub.deleted.add(t1_old)
    await _add_approved_video(repo, playlist.id, 10, "T10")

    await uc.execute(playlist.id)

    t1_new = (await repo.get_video(videos[0].id)).drive_doc_id
    assert t1_new != t1_old
    assert "Notes of T1" in last_body_text(stub, t1_new)
    current = {d["id"]: d["name"] for d in docs(stub)}
    for update in stub.updates:  # renames
        if update["fileId"] in current:
            current[update["fileId"]] = update["name"]
    names = sorted(current.values())
    assert names[1:] == [f"{i:02d} - T{i}" for i in range(1, 11)]  # after "00 - Index"


# ---------------------------------------------------------- numbering / padding


async def test_99_to_100_videos_keeps_every_number_and_only_widens_the_padding(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    """PRD section 9: existing Docs are never renumbered. Crossing 99 -> 100 changes the
    zero-pad width (section 19/24); the existing Docs keep their number and their Doc id,
    only their padding is widened (rename, no re-create, no content rewrite)."""
    playlist, videos = await seed(repo, [f"T{i}" for i in range(1, 100)])
    uc = use_case(repo, stub)
    await uc.execute(playlist.id)
    ids_before = {v.id: v.drive_doc_id for v in await repo.list_videos(playlist.id)}
    assert stub.docs_named("01 - T1") and stub.docs_named("99 - T99")
    batches_before = len(stub.batches)

    # Re-publishing at the same width renames nothing.
    updates_before = len(stub.updates)
    await uc.execute(playlist.id)
    assert len(stub.updates) == updates_before

    await _add_approved_video(repo, playlist.id, 100, "T100")
    result = await uc.execute(playlist.id)

    assert result.created == 1
    ids_after = {v.id: v.drive_doc_id for v in await repo.list_videos(playlist.id)}
    assert all(ids_after[pk] == doc for pk, doc in ids_before.items())  # same Docs
    renames = {u["fileId"]: u["name"] for u in stub.updates if u["fileId"] in ids_before.values()}
    assert len(renames) == 99
    assert renames[ids_before[videos[0].id]] == "001 - T1"
    assert renames[ids_before[videos[98].id]] == "099 - T99"
    assert stub.docs_named("100 - T100")
    # Existing Docs' bodies were not rewritten: only the index and the new Doc were.
    rewritten = {doc for doc, _ in stub.batches[batches_before:]}
    assert rewritten <= {result.index_doc_id, ids_after[max(ids_after)]}
    assert (await repo.get_playlist(playlist.id)).pad_width == 3


async def test_only_approved_notes_ever_reach_drive(
    repo: PlaylistRepository, stub: StubGoogle
) -> None:
    playlist, videos = await seed(repo, ["Pending", "Approved", "Failed", "Edited"], approve=[2])
    await repo.update_stage(videos[2].id, Stage.FAILED, failure_reason="boom")
    # Editing a pending note does not approve it.
    await SaveSummaryEditUseCase(repo).execute(videos[3].id, "## Notes\n\nedited, unapproved")

    await use_case(repo, stub).execute(playlist.id)

    video_docs = [d["name"] for d in docs(stub) if not d["name"].startswith("00 -")]
    assert video_docs == ["2 - Approved"]
    all_text = "".join(stub.inserted_text(d["id"]) for d in docs(stub))
    for unapproved in ("Notes of Pending", "Notes of Failed", "edited, unapproved"):
        assert unapproved not in all_text
    rows = await repo.list_videos(playlist.id)
    assert [v.stage for v in rows] == [
        Stage.PENDING_REVIEW, Stage.PUBLISHED, Stage.FAILED, Stage.PENDING_REVIEW,
    ]

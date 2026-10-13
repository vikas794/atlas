"""The REAL YouTubePlaylistProvider against a stubbed googleapiclient service (PRD stage 1).

Only the ``googleapiclient`` service object is stubbed (it is what talks to the network); the
provider, its pagination, position numbering, error translation and redaction are real, and the
whole thing runs through ``asyncio.to_thread`` exactly as the ingest use case calls it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

import httplib2
import pytest
from googleapiclient.errors import HttpError

from src.application.use_cases.playlist.ingest import IngestPlaylistUseCase
from src.domain.exceptions import ConfigurationError, ProviderError
from src.domain.interfaces.usage_ledger import UsageRecord
from src.domain.models.playlist import FailureType, PipelineError, Stage
from src.domain.services.sync_diff import PlaylistEntry
from src.infrastructure.storage import db
from src.infrastructure.storage.playlist_repo import PlaylistRepository
from src.infrastructure.youtube.playlist import YouTubePlaylistProvider

API_KEY = "AIzaSyFAKEKEYFORTESTING0123456789"


def item(video_id: str | None, title: str, position: int) -> dict[str, Any]:
    snippet: dict[str, Any] = {"title": title, "position": position}  # API position is 0-based
    if video_id:
        snippet["resourceId"] = {"kind": "youtube#video", "videoId": video_id}
    return {"snippet": snippet, "contentDetails": {"videoId": video_id}}


class StubYouTube:
    """googleapiclient-shaped service: playlistItems().list(...).execute() with pages."""

    def __init__(self, pages: list[dict[str, Any]], *, title_items: list[dict[str, Any]] | None = None):
        self.pages = pages
        self.title_items = title_items if title_items is not None else [{"snippet": {"title": "Java Course"}}]
        self.requests: list[dict[str, Any]] = []
        self.threads: set[int] = set()
        self.error: Exception | None = None

    def playlistItems(self) -> Any:  # noqa: N802 - mirrors the Google client
        stub = self

        class Resource:
            def list(self, **kwargs: Any) -> Any:
                stub.requests.append(kwargs)
                stub.threads.add(threading.get_ident())
                token = kwargs.get("pageToken")
                index = int(token) if token else 0
                return type("Req", (), {"execute": lambda _s: stub._respond(index)})()

        return Resource()

    def playlists(self) -> Any:
        stub = self

        class Resource:
            def list(self, **kwargs: Any) -> Any:
                stub.requests.append(kwargs)
                return type("Req", (), {"execute": lambda _s: stub._title()})()

        return Resource()

    def _respond(self, index: int) -> dict[str, Any]:
        if self.error:
            raise self.error
        page = dict(self.pages[index])
        if index + 1 < len(self.pages):
            page["nextPageToken"] = str(index + 1)
        return page

    def _title(self) -> dict[str, Any]:
        if self.error:
            raise self.error
        return {"items": self.title_items}


def make_provider(stub: StubYouTube) -> tuple[YouTubePlaylistProvider, list[int]]:
    built: list[int] = []

    def factory() -> StubYouTube:
        built.append(threading.get_ident())
        return stub

    return YouTubePlaylistProvider(api_key=API_KEY, service_factory=factory), built


def http_error(status: int, reason: str, uri: str | None = None) -> HttpError:
    body = b'{"error": {"errors": [{"reason": "%s"}]}}' % reason.encode()
    return HttpError(
        httplib2.Response({"status": str(status)}),
        body,
        uri=uri or f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&key={API_KEY}",
    )


# ---------------------------------------------------------------- listing & numbering


async def test_a_real_multi_page_playlist_returns_playlist_entries_with_one_based_positions() -> None:
    """Used to raise TypeError on every item (VideoMetadata has no `position`)."""
    pages = [
        {"items": [item("aaa", "Intro", 0), item("bbb", "Variables", 1)]},
        {"items": [item("ccc", "Loops", 2)]},
        {"items": [item("ddd", "Functions", 3)]},
    ]
    stub = StubYouTube(pages)
    provider, _ = make_provider(stub)

    entries = await asyncio.to_thread(provider.fetch_playlist_videos, "PL1")

    assert entries == [
        PlaylistEntry("aaa", "Intro", 1),
        PlaylistEntry("bbb", "Variables", 2),
        PlaylistEntry("ccc", "Loops", 3),
        PlaylistEntry("ddd", "Functions", 4),
    ]
    assert [r.get("pageToken") for r in stub.requests] == [None, "1", "2"]
    assert all(r["playlistId"] == "PL1" and r["maxResults"] == 50 for r in stub.requests)


async def test_private_and_deleted_videos_are_skipped_and_numbering_stays_dense() -> None:
    pages = [
        {
            "items": [
                item("aaa", "Intro", 0),
                item("bbb", "Private video", 1),
                item("ccc", "Deleted video", 2),
                item(None, "Broken item", 3),
                item("ddd", "Real second", 4),
            ]
        }
    ]
    provider, _ = make_provider(StubYouTube(pages))

    entries = await asyncio.to_thread(provider.fetch_playlist_videos, "PL1")

    assert [(e.youtube_video_id, e.position) for e in entries] == [("aaa", 1), ("ddd", 2)]


async def test_a_repeated_video_is_returned_twice_in_upstream_order() -> None:
    pages = [{"items": [item("aaa", "A", 0), item("bbb", "B", 1), item("aaa", "A", 2)]}]
    provider, _ = make_provider(StubYouTube(pages))

    entries = await asyncio.to_thread(provider.fetch_playlist_videos, "PL1")

    assert [(e.youtube_video_id, e.position) for e in entries] == [("aaa", 1), ("bbb", 2), ("aaa", 3)]


async def test_max_videos_stops_paging_early() -> None:
    pages = [{"items": [item(f"v{i}", f"T{i}", i) for i in range(50)]}, {"items": [item("zzz", "Z", 50)]}]
    stub = StubYouTube(pages)
    provider, _ = make_provider(stub)

    entries = await asyncio.to_thread(provider.fetch_playlist_videos, "PL1", 10)

    assert len(entries) == 10 and len(stub.requests) == 1


async def test_the_playlist_title_is_read_and_defaults_when_missing() -> None:
    provider, _ = make_provider(StubYouTube([{"items": []}]))
    assert await asyncio.to_thread(provider.get_playlist_title, "PL1") == "Java Course"

    empty, _ = make_provider(StubYouTube([{"items": []}], title_items=[]))
    assert await asyncio.to_thread(empty.get_playlist_title, "PL1") == "Untitled Playlist"


async def test_a_new_service_is_built_per_call_for_thread_safety() -> None:
    stub = StubYouTube([{"items": [item("aaa", "A", 0)]}])
    provider, built = make_provider(stub)

    await asyncio.to_thread(provider.fetch_playlist_videos, "PL1")
    await asyncio.to_thread(provider.fetch_playlist_videos, "PL1")
    await asyncio.to_thread(provider.get_playlist_title, "PL1")

    assert len(built) == 3  # never one shared httplib2 object


# --------------------------------------------------------- errors & key redaction (M4)


@pytest.mark.parametrize("path", ["fetch", "title"])
async def test_http_errors_never_leak_the_api_key(
    path: str, caplog: pytest.LogCaptureFixture
) -> None:
    stub = StubYouTube([{"items": []}])
    stub.error = http_error(500, "backendError")
    provider, _ = make_provider(stub)

    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderError) as excinfo:
        if path == "fetch":
            await asyncio.to_thread(provider.fetch_playlist_videos, "PL1")
        else:
            await asyncio.to_thread(provider.get_playlist_title, "PL1")

    assert API_KEY not in str(excinfo.value)
    assert API_KEY not in caplog.text
    assert "key=" not in str(excinfo.value) or "<redacted>" in str(excinfo.value)
    # The chained original (whose text holds the key) is not attached to the raised error.
    assert excinfo.value.__suppress_context__ is True


async def test_a_server_error_message_carries_no_request_url() -> None:
    stub = StubYouTube([{"items": []}])
    stub.error = http_error(500, "backendError")
    provider, _ = make_provider(stub)

    with pytest.raises(ProviderError) as excinfo:
        await asyncio.to_thread(provider.fetch_playlist_videos, "PL1")

    message = str(excinfo.value)
    assert "HTTP 500" in message
    assert "googleapis.com" not in message


async def test_an_unknown_playlist_is_classified_playlist_unavailable() -> None:
    stub = StubYouTube([{"items": []}])
    stub.error = http_error(404, "playlistNotFound")
    provider, _ = make_provider(stub)

    with pytest.raises(PipelineError) as excinfo:
        await asyncio.to_thread(provider.fetch_playlist_videos, "PLnope")

    assert excinfo.value.failure_type is FailureType.PLAYLIST_UNAVAILABLE
    assert API_KEY not in str(excinfo.value)


async def test_a_private_playlist_is_classified_playlist_unavailable() -> None:
    stub = StubYouTube([{"items": []}])
    stub.error = http_error(403, "playlistItemsNotAccessible")
    provider, _ = make_provider(stub)

    with pytest.raises(PipelineError) as excinfo:
        await asyncio.to_thread(provider.fetch_playlist_videos, "PLprivate")

    assert excinfo.value.failure_type is FailureType.PLAYLIST_UNAVAILABLE


async def test_an_exceeded_quota_is_a_transient_rate_limit() -> None:
    stub = StubYouTube([{"items": []}])
    stub.error = http_error(403, "quotaExceeded")
    provider, _ = make_provider(stub)

    with pytest.raises(PipelineError) as excinfo:
        await asyncio.to_thread(provider.fetch_playlist_videos, "PL1")

    assert excinfo.value.failure_type is FailureType.RATE_LIMITED


async def test_a_rejected_key_is_a_configuration_error_naming_the_variable() -> None:
    stub = StubYouTube([{"items": []}])
    stub.error = http_error(400, "keyInvalid")
    provider, _ = make_provider(stub)

    with pytest.raises(ConfigurationError) as excinfo:
        await asyncio.to_thread(provider.fetch_playlist_videos, "PL1")

    assert "YOUTUBE_API_KEY" in str(excinfo.value)
    assert API_KEY not in str(excinfo.value)


async def test_network_failures_are_provider_errors_not_playlist_unavailable() -> None:
    stub = StubYouTube([{"items": []}])
    stub.error = ConnectionError(f"connection reset while calling ...&key={API_KEY}")
    provider, _ = make_provider(stub)

    with pytest.raises(ProviderError) as excinfo:
        await asyncio.to_thread(provider.fetch_playlist_videos, "PL1")

    assert API_KEY not in str(excinfo.value)


def test_a_missing_key_is_a_configuration_error_naming_the_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)

    with pytest.raises(ConfigurationError) as excinfo:
        YouTubePlaylistProvider()

    assert "YOUTUBE_API_KEY" in str(excinfo.value)


def test_the_key_is_read_from_the_configured_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_YT_KEY", "AIzaFromCustomVariable0000")

    assert YouTubePlaylistProvider(api_key_env="MY_YT_KEY").api_key == "AIzaFromCustomVariable0000"


# ------------------------------------------- through the ingest use case (the real path)


async def test_ingest_with_the_real_provider_stores_one_based_numbering_and_records_usage(
    tmp_path: Path,
) -> None:
    """End to end: stubbed Google service -> real provider -> to_thread -> ingest -> SQLite.

    Also covers the old `asyncio.create_task` from a worker thread (RuntimeError: no running
    loop): usage is now recorded from async context.
    """
    db_path = tmp_path / "yt.sqlite3"
    db.initialize(db_path)
    repo = PlaylistRepository(db_path)
    pages = [
        {"items": [item("aaa", "Intro", 0), item("bbb", "Private video", 1)]},
        {"items": [item("ccc", "Loops", 2)]},
    ]
    provider, _ = make_provider(StubYouTube(pages))
    records: list[UsageRecord] = []

    class Ledger:
        async def record_usage(self, record: UsageRecord) -> None:
            records.append(record)

    use_case = IngestPlaylistUseCase(repo, provider, usage_ledger=Ledger())

    playlist = await use_case.execute("https://www.youtube.com/playlist?list=PL_REAL")

    rows = await repo.list_videos(playlist.id)
    assert [(v.youtube_video_id, v.position, v.stage) for v in rows] == [
        ("aaa", 1, Stage.QUEUED),
        ("ccc", 2, Stage.QUEUED),
    ]
    assert playlist.title == "Java Course"
    assert [r.operation for r in records] == ["playlist_fetch"]
    assert records[0].provider == "youtube"


async def test_ingest_with_a_leaky_provider_error_stores_nothing_and_leaks_nothing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "yt2.sqlite3"
    db.initialize(db_path)
    repo = PlaylistRepository(db_path)
    stub = StubYouTube([{"items": []}])
    stub.error = http_error(500, "backendError")
    provider, _ = make_provider(stub)

    with pytest.raises(ProviderError) as excinfo:
        await IngestPlaylistUseCase(repo, provider).execute(
            "https://www.youtube.com/playlist?list=PL_X"
        )

    assert API_KEY not in str(excinfo.value)
    assert await repo.count_playlists() == 0


async def test_a_malformed_playlist_id_is_playlist_unavailable_not_a_server_error() -> None:
    """Seen against the real API: HTTP 400 reason 'invalid' for an id that is not a playlist."""
    stub = StubYouTube([{"items": []}])
    stub.error = http_error(400, "invalid")
    provider, _ = make_provider(stub)

    with pytest.raises(PipelineError) as excinfo:
        await asyncio.to_thread(provider.fetch_playlist_videos, "PLabc")

    assert excinfo.value.failure_type is FailureType.PLAYLIST_UNAVAILABLE

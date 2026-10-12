"""Failure injection for the playlist pipeline (PRD sections 8, 9, 15, 18, 20, 22 M1/M2/M5).

Every scenario runs the REAL use cases, worker, repository (temporary SQLite) and, where it
matters, the REAL adapters: ``YouTubePlaylistProvider`` over a stubbed googleapiclient
service, ``YtDlpTranscriptProvider`` over stubbed yt-dlp boundaries, and
``GeminiNotesGenerator`` over a client double that raises genuine ``google.genai`` errors.
Nothing touches the network.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from google.genai import errors as genai_errors
from yt_dlp.utils import DownloadError

from src.application.use_cases.playlist.actions import RetryVideoUseCase
from src.application.use_cases.playlist.ingest import IngestPlaylistUseCase
from src.application.use_cases.playlist.process_video import ProcessVideoUseCase
from src.application.worker import PlaylistWorker
from src.domain.exceptions import ProviderError
from src.domain.interfaces.notes_generator import GeneratedNotes
from src.domain.interfaces.transcript_provider import TranscriptResult
from src.domain.models.playlist import FailureKind, FailureType, PipelineError, Stage
from src.domain.services.sync_diff import PlaylistEntry
from src.infrastructure.llm.gemini.notes import GeminiNotesGenerator
from src.infrastructure.storage import db
from src.infrastructure.storage.playlist_repo import PlaylistRepository
from src.infrastructure.transcript.ytdlp import provider as ytdlp_module
from src.infrastructure.transcript.ytdlp.provider import YtDlpTranscriptProvider
from tests.test_youtube_provider import StubYouTube, http_error, item, make_provider

URL = "https://www.youtube.com/playlist?list=PLinjection"
GOOD_NOTES = (
    "## Notes\n\n- a detail worth remembering about the topic\n\n"
    "## Key Points\n\n- one\n- two\n\n## Takeaways\n\n- practise it\n"
)


@pytest.fixture
def repo(tmp_path: Path) -> PlaylistRepository:
    path = tmp_path / "injection.sqlite3"
    db.initialize(path)
    return PlaylistRepository(path)


async def wait_until(predicate: Any, timeout: float = 20.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


def pages_of(video_ids: list[str | None], titles: dict[int, str] | None = None) -> list[dict]:
    """Split ``video_ids`` into API pages of 50 (``None`` = a deleted/private placeholder)."""
    titles = titles or {}
    items = [
        item(vid, titles.get(i, f"Video {vid}") if vid else titles.get(i, "Deleted video"), i)
        for i, vid in enumerate(video_ids)
    ]
    return [{"items": items[start : start + 50]} for start in range(0, len(items), 50)] or [
        {"items": []}
    ]


class Transcripts:
    """Transcript port double with per-video scripted failures."""

    def __init__(self, failures: dict[str, list[Exception]] | None = None) -> None:
        self.failures = failures or {}
        self.calls: list[str] = []

    async def fetch_transcript(self, video_id: str, language: str = "en") -> TranscriptResult:
        self.calls.append(video_id)
        scripted = self.failures.get(video_id)
        if scripted:
            raise scripted.pop(0)
        text = f"transcript of {video_id}"
        return TranscriptResult(video_id, "en", "", text, f"h-{video_id}", "", len(text))


class Notes:
    """Notes port double with scripted failures and an optional gate to hold a call open."""

    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.failures = list(failures or [])
        self.calls: list[str] = []
        self.gate: asyncio.Event | None = None
        self.gated_title: str | None = None

    async def generate_notes(self, *, title: str, transcript: str) -> GeneratedNotes:
        self.calls.append(title)
        if self.gate is not None and title == self.gated_title:
            await self.gate.wait()
        if self.failures:
            raise self.failures.pop(0)
        return GeneratedNotes(GOOD_NOTES, "fake", "fake-model", "v1")


def worker_for(repo: PlaylistRepository, transcripts: Any, notes: Any, **kwargs: Any):
    process = ProcessVideoUseCase(
        repo,
        transcripts,
        notes,
        max_auto_retries=kwargs.pop("max_auto_retries", 3),
        backoff_base_seconds=kwargs.pop("backoff_base_seconds", 0.0),
        backoff_cap_seconds=kwargs.pop("backoff_cap_seconds", 0.0),
    )
    return PlaylistWorker(repo, process, poll_interval_seconds=0.02, **kwargs), process


async def all_settled(repo: PlaylistRepository, playlist_pk: int) -> bool:
    videos = await repo.list_videos(playlist_pk)
    return all(
        v.stage == Stage.PENDING_REVIEW or (v.stage == Stage.FAILED and v.next_retry_at is None)
        for v in videos
    )


# ------------------------------------------------------------------ ingestion failures


@pytest.mark.parametrize("reason", ["playlistNotFound", "playlistItemsNotAccessible"])
async def test_private_or_missing_playlist_fails_ingestion_and_stores_nothing(
    repo: PlaylistRepository, reason: str
) -> None:
    stub = StubYouTube(pages_of(["a"]), title_items=[])
    stub.error = http_error(404 if reason == "playlistNotFound" else 403, reason)
    provider, _ = make_provider(stub)

    with pytest.raises(PipelineError) as excinfo:
        await IngestPlaylistUseCase(repo, provider).ingest(URL)

    assert excinfo.value.failure_type is FailureType.PLAYLIST_UNAVAILABLE
    assert excinfo.value.failure_kind is FailureKind.PERMANENT
    assert await repo.count_playlists() == 0


async def test_quota_exceeded_during_ingestion_is_transient_and_stores_nothing(
    repo: PlaylistRepository,
) -> None:
    stub = StubYouTube(pages_of(["a"]))
    stub.error = http_error(403, "quotaExceeded")
    provider, _ = make_provider(stub)

    with pytest.raises(PipelineError) as excinfo:
        await IngestPlaylistUseCase(repo, provider).ingest(URL)

    assert excinfo.value.failure_type is FailureType.RATE_LIMITED
    assert excinfo.value.failure_kind is FailureKind.TRANSIENT
    assert "quota" in excinfo.value.reason
    assert await repo.count_playlists() == 0


async def test_network_error_during_ingestion_stores_nothing(repo: PlaylistRepository) -> None:
    stub = StubYouTube(pages_of([f"v{i}" for i in range(120)]))
    calls = {"n": 0}
    real = stub._respond

    def flaky(index: int) -> dict:
        calls["n"] += 1
        if calls["n"] == 2:  # the connection drops on page 2 of 3
            raise TimeoutError("The read operation timed out")
        return real(index)

    stub._respond = flaky  # type: ignore[method-assign]
    provider, _ = make_provider(stub)

    with pytest.raises(ProviderError):
        await IngestPlaylistUseCase(repo, provider).ingest(URL)
    # Never a half-ingested playlist: the list is fetched completely before anything is stored.
    assert await repo.count_playlists() == 0


async def test_pagination_beyond_50_with_placeholders_numbers_densely_from_one(
    repo: PlaylistRepository,
) -> None:
    ids: list[str | None] = [f"v{i:03d}" for i in range(130)]
    ids[10] = None  # "Deleted video"
    ids[75] = None  # "Private video"
    titles = {75: "Private video"}
    stub = StubYouTube(pages_of(ids, titles))
    provider, _ = make_provider(stub)

    playlist = await IngestPlaylistUseCase(repo, provider).execute(URL)

    videos = await repo.list_videos(playlist.id)
    assert len(stub.requests) == 1 + 3  # title lookup + 3 pages (50, 50, 30)
    assert [r.get("pageToken") for r in stub.requests[1:]] == [None, "1", "2"]
    assert len(videos) == 128
    assert [v.position for v in videos] == list(range(1, 129))
    assert [v.youtube_video_id for v in videos] == [vid for vid in ids if vid]
    assert playlist.video_count == 128


async def test_250_video_playlist_is_stored_completely_and_processed_with_bounded_concurrency(
    repo: PlaylistRepository,
) -> None:
    ids = [f"vid{i:04d}" for i in range(250)]
    provider, _ = make_provider(StubYouTube(pages_of(ids)))
    ingest = IngestPlaylistUseCase(repo, provider)

    playlist = await ingest.execute(URL)
    again = await ingest.execute(URL)  # resubmitting does not create duplicates (M1)

    videos = await repo.list_videos(playlist.id)
    assert again.id == playlist.id
    assert len(videos) == 250
    assert [v.position for v in videos] == list(range(1, 251))
    assert [v.youtube_video_id for v in videos] == ids

    transcripts, notes = Transcripts(), Notes()
    worker, _ = worker_for(repo, transcripts, notes, max_concurrent_videos=3)
    await worker.start()
    try:
        # Generous: this is a throughput-bound loop over a real SQLite file, slow on a busy CI box.
        await wait_until(lambda: all_settled(repo, playlist.id), timeout=600)
    finally:
        await worker.stop()

    counts = await repo.count_by_stage(playlist.id)
    assert counts[Stage.PENDING_REVIEW.value] == 250
    assert sorted(transcripts.calls) == ids  # exactly one transcript fetch per video
    assert len(notes.calls) == 250  # exactly one LLM call per video


# ---------------------------------------------------------------- per-video failures


async def _ingest(repo: PlaylistRepository, ids: list[str]) -> int:
    provider, _ = make_provider(StubYouTube(pages_of(ids)))  # type: ignore[arg-type]
    return (await IngestPlaylistUseCase(repo, provider).execute(URL)).id


def ytdlp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, Any]
) -> YtDlpTranscriptProvider:
    """The real yt-dlp adapter; ``outcomes[video_id]`` is an exception, "none" or "ok"."""

    def download(options: dict[str, Any], urls: list[str]) -> None:
        video_id = urls[0].rsplit("=", 1)[-1]
        outcome = outcomes.get(video_id, "ok")
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == "ok":
            out = Path(str(options["outtmpl"])).parent / f"{video_id}.en.srt"
            out.write_text("1\n00:00:00,000 --> 00:00:02,000\nhello world\n", encoding="utf-8")

    monkeypatch.setattr(ytdlp_module, "download_subtitles", download)
    monkeypatch.setattr(ytdlp_module, "list_subtitle_languages", lambda options, url: [])
    folder = tmp_path / "subs"
    return YtDlpTranscriptProvider(
        lambda key, default=None: {"transcript_output_folder": str(folder)}.get(key, default)
    )


async def test_deleted_video_fails_permanently_with_zero_retries_and_no_llm_call(
    repo: PlaylistRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    playlist_pk = await _ingest(repo, ["gone", "fine"])
    removed = DownloadError(
        "ERROR: [youtube] gone: Video unavailable. This video has been removed by the uploader"
    )
    transcripts = ytdlp(tmp_path, monkeypatch, {"gone": removed})
    notes = Notes()
    worker, _ = worker_for(repo, transcripts, notes)
    await worker.start()
    try:
        await wait_until(lambda: all_settled(repo, playlist_pk))
    finally:
        await worker.stop()

    gone, fine = await repo.list_videos(playlist_pk)
    assert gone.stage == Stage.FAILED
    assert gone.failure_type == FailureType.VIDEO_DELETED
    assert gone.failure_kind == FailureKind.PERMANENT
    assert gone.failure_reason == "This video was deleted by the uploader."
    assert gone.retry_count == 0 and gone.next_retry_at is None
    assert fine.stage == Stage.PENDING_REVIEW
    assert notes.calls == ["Video fine"]  # the deleted video never reached the LLM


async def test_video_without_any_transcript_fails_fast_and_is_never_retried(
    repo: PlaylistRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    playlist_pk = await _ingest(repo, ["mute"])
    attempts: list[str] = []
    transcripts = ytdlp(tmp_path, monkeypatch, {"mute": "none"})
    original = ytdlp_module.download_subtitles

    def counting(options: dict[str, Any], urls: list[str]) -> None:
        attempts.append(urls[0])
        original(options, urls)

    monkeypatch.setattr(ytdlp_module, "download_subtitles", counting)
    notes = Notes()
    worker, _ = worker_for(repo, transcripts, notes)
    await worker.start()
    try:
        await wait_until(lambda: all_settled(repo, playlist_pk))
        await asyncio.sleep(0.2)  # several idle ticks: nothing may be re-dispatched
    finally:
        await worker.stop()

    (video,) = await repo.list_videos(playlist_pk)
    assert video.failure_type == FailureType.NO_USABLE_LANGUAGE
    assert video.failure_kind == FailureKind.PERMANENT
    assert video.failure_reason == "No transcript is available in a usable language."
    assert video.retry_count == 0 and video.next_retry_at is None
    assert len(attempts) == 1  # one fetch attempt, zero retries
    assert notes.calls == []


def gemini_429() -> genai_errors.APIError:
    body = {"error": {"code": 429, "message": "Resource exhausted", "status": "RESOURCE_EXHAUSTED"}}
    return genai_errors.ClientError(429, body)


class GeminiClient:
    """``google.genai`` client double: raises the scripted errors, then answers."""

    def __init__(self, errors: list[Exception]) -> None:
        self.errors = list(errors)
        self.calls = 0
        outer = self

        class _Models:
            async def generate_content(self, *, model: str, contents: str) -> Any:
                outer.calls += 1
                if outer.errors:
                    raise outer.errors.pop(0)
                finish = type("Candidate", (), {"finish_reason": "STOP"})()
                return type(
                    "Response",
                    (),
                    {"text": GOOD_NOTES, "usage_metadata": None, "candidates": [finish]},
                )()

        self.aio = type("Aio", (), {"models": _Models()})()


def gemini(client: GeminiClient) -> GeminiNotesGenerator:
    settings = {
        "gemini_model": "gemini-2.5-flash",
        "notes_prompt_name": "notes_v1_1.yaml",
        "prompts_base_dir": "src/prompts",
    }
    return GeminiNotesGenerator(
        lambda key, default=None: settings.get(key, default), client_factory=lambda: client
    )


async def test_forced_429_schedules_a_persisted_backoff_then_succeeds(
    repo: PlaylistRepository,
) -> None:
    playlist_pk = await _ingest(repo, ["v1"])
    (video,) = await repo.list_videos(playlist_pk)
    client = GeminiClient([gemini_429(), gemini_429()])
    process = ProcessVideoUseCase(
        repo,
        Transcripts(),
        gemini(client),
        max_auto_retries=3,
        backoff_base_seconds=60.0,
        backoff_cap_seconds=600.0,
    )

    before = datetime.now(UTC)
    failed = await process.execute(video.id)

    # The schedule is persisted, so the UI can render "Retrying in 1m (attempt 1/3)".
    assert failed.stage == Stage.FAILED
    assert failed.failure_type == FailureType.RATE_LIMITED
    assert failed.failure_kind == FailureKind.TRANSIENT
    assert "Gemini" in failed.failure_reason
    assert failed.retry_count == 1
    assert failed.auto_retry is True
    delay = (datetime.fromisoformat(failed.next_retry_at) - before).total_seconds()
    assert 59 <= delay <= 60 * 1.2 + 2  # base delay plus at most 20 % jitter
    # Not due yet: the worker must not pick it up before its time.
    assert await repo.list_transient_due() == []

    # The second failure doubles the delay (exponential).
    await repo.update_stage(
        video.id,
        Stage.FAILED,
        failure_type=FailureType.RATE_LIMITED,
        failure_kind=FailureKind.TRANSIENT,
        failure_reason="x",
        next_retry_at=before.isoformat(),
    )
    second_start = datetime.now(UTC)
    failed = await process.execute(video.id)
    assert failed.retry_count == 2
    second = (datetime.fromisoformat(failed.next_retry_at) - second_start).total_seconds()
    assert 119 <= second <= 120 * 1.2 + 2

    # Third attempt succeeds; the transcript was fetched once and reused.
    await repo.update_stage(
        video.id,
        Stage.FAILED,
        failure_type=FailureType.RATE_LIMITED,
        failure_kind=FailureKind.TRANSIENT,
        failure_reason="x",
        next_retry_at=before.isoformat(),
    )
    done = await process.execute(video.id)
    assert done.stage == Stage.PENDING_REVIEW
    assert done.failure_type is None and done.next_retry_at is None
    assert client.calls == 3
    assert process.transcript_provider.calls == ["v1"]


async def test_429_until_exhaustion_leaves_a_visible_transient_failure(
    repo: PlaylistRepository,
) -> None:
    playlist_pk = await _ingest(repo, ["v1"])
    client = GeminiClient([gemini_429() for _ in range(10)])
    worker, _ = worker_for(repo, Transcripts(), gemini(client), max_auto_retries=2)
    await worker.start()
    try:
        await wait_until(lambda: all_settled(repo, playlist_pk))
        await asyncio.sleep(0.2)
    finally:
        await worker.stop()

    (video,) = await repo.list_videos(playlist_pk)
    assert video.stage == Stage.FAILED
    assert video.failure_type == FailureType.RATE_LIMITED
    assert video.failure_kind == FailureKind.TRANSIENT
    assert video.retries_exhausted is True
    assert video.failure_reason == "Gemini is rate-limiting requests (HTTP 429)."
    assert client.calls == 3  # 1 attempt + 2 automatic retries, then manual only

    # The manual retry button runs it again with a fresh budget.
    client.errors.clear()
    await RetryVideoUseCase(repo).execute(video.id)
    worker2, _ = worker_for(repo, Transcripts(), gemini(client))
    await worker2.start()
    try:
        await wait_until(lambda: all_settled(repo, playlist_pk))
    finally:
        await worker2.stop()
    (video,) = await repo.list_videos(playlist_pk)
    assert video.stage == Stage.PENDING_REVIEW


async def test_invalid_gemini_key_fails_permanently_after_one_call(
    repo: PlaylistRepository,
) -> None:
    playlist_pk = await _ingest(repo, ["v1"])
    body = {"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT"}}
    client = GeminiClient([genai_errors.ClientError(400, body) for _ in range(5)])
    worker, _ = worker_for(repo, Transcripts(), gemini(client))
    await worker.start()
    try:
        await wait_until(lambda: all_settled(repo, playlist_pk))
        await asyncio.sleep(0.2)
    finally:
        await worker.stop()

    (video,) = await repo.list_videos(playlist_pk)
    assert video.failure_type == FailureType.CONFIG_ERROR
    assert video.failure_kind == FailureKind.PERMANENT
    assert "GEMINI_API_KEY" in video.failure_reason
    assert video.next_retry_at is None and video.retry_count == 0
    assert client.calls == 1


async def test_network_error_mid_run_only_affects_that_video_and_recovers(
    repo: PlaylistRepository,
) -> None:
    ids = ["v1", "v2", "v3"]
    playlist_pk = await _ingest(repo, ids)
    network = PipelineError(FailureType.NETWORK_ERROR, "A network error interrupted the request.")
    transcripts = Transcripts({"v2": [network]})
    notes = Notes()
    worker, _ = worker_for(repo, transcripts, notes, max_concurrent_videos=1)
    await worker.start()
    try:
        await wait_until(lambda: all_settled(repo, playlist_pk))
    finally:
        await worker.stop()

    videos = await repo.list_videos(playlist_pk)
    assert [v.stage for v in videos] == [Stage.PENDING_REVIEW] * 3
    # One automatic retry fixed it; the counter restarts once the transcript is stored.
    assert videos[1].retry_count == 0
    assert transcripts.calls.count("v2") == 2
    assert transcripts.calls.count("v1") == transcripts.calls.count("v3") == 1
    assert len(notes.calls) == 3


async def test_crash_mid_run_resumes_without_redoing_completed_work(
    repo: PlaylistRepository,
) -> None:
    ids = ["v1", "v2", "v3"]
    playlist_pk = await _ingest(repo, ids)
    transcripts, notes = Transcripts(), Notes()
    notes.gate = asyncio.Event()  # v2's LLM call hangs until the "crash"
    notes.gated_title = "Video v2"
    worker, _ = worker_for(
        repo, transcripts, notes, max_concurrent_videos=1, shutdown_timeout_seconds=0.05
    )
    await worker.start()

    async def v2_generating() -> bool:
        return (await repo.list_videos(playlist_pk))[1].stage == Stage.GENERATING_NOTES

    await wait_until(v2_generating)
    await worker.stop()  # the hanging job is cancelled: v2 is left in generating_notes

    left = await repo.list_videos(playlist_pk)
    assert left[0].stage == Stage.PENDING_REVIEW
    assert left[1].stage == Stage.GENERATING_NOTES
    assert left[2].stage == Stage.QUEUED

    notes.gate = None
    restarted, _ = worker_for(repo, transcripts, notes, max_concurrent_videos=1)
    await restarted.start()  # crash recovery re-queues v2
    try:
        await wait_until(lambda: all_settled(repo, playlist_pk))
    finally:
        await restarted.stop()

    assert [v.stage for v in await repo.list_videos(playlist_pk)] == [Stage.PENDING_REVIEW] * 3
    assert sorted(transcripts.calls) == ids  # no transcript was fetched twice
    # v1 was never regenerated; v2 needed one more call (its first never returned).
    assert notes.calls == ["Video v1", "Video v2", "Video v2", "Video v3"]


async def test_running_the_same_playlist_twice_creates_no_duplicates_or_extra_calls(
    repo: PlaylistRepository,
) -> None:
    stub = StubYouTube(pages_of(["a", "b"]))
    provider, _ = make_provider(stub)
    ingest = IngestPlaylistUseCase(repo, provider)
    transcripts, notes = Transcripts(), Notes()
    worker, _ = worker_for(repo, transcripts, notes)

    results = await asyncio.gather(ingest.ingest(URL), ingest.ingest(URL))
    playlist_pk = results[0].playlist.id
    await worker.start()
    try:
        await wait_until(lambda: all_settled(repo, playlist_pk))
        await ingest.ingest(URL)  # a plain re-run while everything is done
        await asyncio.sleep(0.2)
        stub.pages = pages_of(["a", "b", "c"])  # upstream gained one video
        await ingest.ingest(URL)
        await wait_until(lambda: all_settled(repo, playlist_pk))
    finally:
        await worker.stop()

    videos = await repo.list_videos(playlist_pk)
    assert [(v.youtube_video_id, v.position) for v in videos] == [("a", 1), ("b", 2), ("c", 3)]
    assert sorted(transcripts.calls) == ["a", "b", "c"]
    assert len(notes.calls) == 3
    assert await repo.count_playlists() == 1


async def test_duplicate_of_a_permanently_failed_video_is_not_left_queued_forever(
    repo: PlaylistRepository,
) -> None:
    """PRD 20: a repeated video is processed once; if that fails, the repeat must say so."""
    playlist_pk = await _ingest(repo, ["a", "b", "a"])
    no_subs = PipelineError(FailureType.NO_USABLE_LANGUAGE, "No transcript.")
    transcripts = Transcripts({"a": [no_subs]})
    worker, _ = worker_for(repo, transcripts, Notes())
    await worker.start()
    try:
        await wait_until(lambda: all_settled(repo, playlist_pk))
    finally:
        await worker.stop()

    first, other, repeat = await repo.list_videos(playlist_pk)
    assert first.stage == Stage.FAILED and other.stage == Stage.PENDING_REVIEW
    assert repeat.duplicate_of == first.id
    assert repeat.stage == Stage.FAILED
    assert repeat.failure_type == FailureType.NO_USABLE_LANGUAGE
    assert repeat.failure_kind == FailureKind.PERMANENT
    assert repeat.failure_reason.startswith("Same video as #1")
    assert transcripts.calls.count("a") == 1  # the repeat never fetched on its own


async def test_duplicate_reuses_the_first_summary_without_an_llm_call(
    repo: PlaylistRepository,
) -> None:
    playlist_pk = await _ingest(repo, ["a", "a"])
    transcripts, notes = Transcripts(), Notes()
    worker, _ = worker_for(repo, transcripts, notes)
    await worker.start()
    try:
        await wait_until(lambda: all_settled(repo, playlist_pk))
    finally:
        await worker.stop()

    first, repeat = await repo.list_videos(playlist_pk)
    assert [first.position, repeat.position] == [1, 2]
    assert repeat.stage == Stage.PENDING_REVIEW
    assert (await repo.get_summary(repeat.id)).content == (await repo.get_summary(first.id)).content
    assert transcripts.calls == ["a"] and len(notes.calls) == 1


async def test_upstream_removal_and_rename_are_flagged_not_applied(
    repo: PlaylistRepository,
) -> None:
    stub = StubYouTube(pages_of(["a", "b", "c"]))
    provider, _ = make_provider(stub)
    ingest = IngestPlaylistUseCase(repo, provider)
    playlist = await ingest.execute(URL)

    stub.pages = pages_of(["c", "a", "d"], titles={1: "A renamed upstream"})
    await ingest.execute(URL)

    videos = {v.youtube_video_id: v for v in await repo.list_videos(playlist.id)}
    assert videos["b"].removed_upstream is True  # flagged, never deleted
    assert videos["a"].title == "Video a"  # renamed upstream: title kept
    assert [videos[k].position for k in "abcd"] == [1, 2, 3, 4]  # no renumbering; d appended
    assert videos["d"].stage == Stage.QUEUED


async def test_entries_with_no_video_id_are_skipped(repo: PlaylistRepository) -> None:
    pages = pages_of(["a", "b"])
    pages[0]["items"].insert(1, {"snippet": {"title": "Weird"}, "contentDetails": {}})
    provider, _ = make_provider(StubYouTube(pages))
    playlist = await IngestPlaylistUseCase(repo, provider).execute(URL)
    assert [(v.youtube_video_id, v.position) for v in await repo.list_videos(playlist.id)] == [
        ("a", 1),
        ("b", 2),
    ]


def test_playlist_entry_type_is_what_the_repo_inserts() -> None:
    # Guard for the helpers above: StubYouTube pages carry 0-based API positions only.
    assert PlaylistEntry("x", "t", 1).position == 1


async def test_a_freed_slot_is_refilled_without_waiting_for_the_idle_poll(
    repo: PlaylistRepository,
) -> None:
    """With every slot busy the loop sleeps up to ``poll_interval_seconds`` (2 s by default);
    a finished job must wake it, or each slot idles that long between videos."""
    playlist_pk = await _ingest(repo, ["v1", "v2", "v3"])
    process = ProcessVideoUseCase(repo, Transcripts(), Notes(), backoff_base_seconds=0.0)
    worker = PlaylistWorker(repo, process, max_concurrent_videos=1, poll_interval_seconds=30.0)
    await worker.start()
    try:
        await wait_until(lambda: all_settled(repo, playlist_pk), timeout=10)
    finally:
        await worker.stop()


async def test_transcript_retries_do_not_use_up_the_notes_retry_budget(
    repo: PlaylistRepository,
) -> None:
    playlist_pk = await _ingest(repo, ["v1"])
    throttled = PipelineError(FailureType.RATE_LIMITED, "YouTube is rate-limiting requests.")
    transcripts = Transcripts({"v1": [throttled, throttled]})
    llm_429 = PipelineError(FailureType.RATE_LIMITED, "Gemini is rate-limiting requests.")
    notes = Notes([llm_429, llm_429])
    worker, _ = worker_for(repo, transcripts, notes, max_auto_retries=2)
    await worker.start()
    try:
        await wait_until(lambda: all_settled(repo, playlist_pk))
    finally:
        await worker.stop()

    (video,) = await repo.list_videos(playlist_pk)
    assert video.stage == Stage.PENDING_REVIEW
    assert len(transcripts.calls) == 3 and len(notes.calls) == 3


async def test_a_stale_task_cannot_schedule_a_retry_after_a_manual_retry(
    repo: PlaylistRepository,
) -> None:
    from src.domain.exceptions import StaleRunError

    playlist_pk = await _ingest(repo, ["v1"])
    (video,) = await repo.list_videos(playlist_pk)
    await repo.clear_failure(video.id)  # a manual retry bumps the run token
    with pytest.raises(StaleRunError):
        await repo.increment_retry(video.id, next_retry_at="2030-01-01T00:00:00+00:00", token=0)
    assert (await repo.get_video(video.id)).retry_count == 0


@pytest.mark.parametrize("reason", ["rateLimitExceeded", "userRateLimitExceeded"])
async def test_youtube_api_throttling_is_a_transient_rate_limit(
    repo: PlaylistRepository, reason: str
) -> None:
    stub = StubYouTube(pages_of(["a"]))
    stub.error = http_error(403, reason)
    provider, _ = make_provider(stub)
    with pytest.raises(PipelineError) as excinfo:
        await IngestPlaylistUseCase(repo, provider).ingest(URL)
    assert excinfo.value.failure_type is FailureType.RATE_LIMITED


async def test_youtube_api_server_error_is_a_provider_error_not_unavailable(
    repo: PlaylistRepository,
) -> None:
    stub = StubYouTube(pages_of(["a"]))
    stub.error = http_error(503, "backendError")
    provider, _ = make_provider(stub)
    with pytest.raises(ProviderError):
        await IngestPlaylistUseCase(repo, provider).ingest(URL)
    assert await repo.count_playlists() == 0


async def test_invalid_youtube_key_as_the_live_api_reports_it_is_a_config_error(
    repo: PlaylistRepository,
) -> None:
    """Observed against the real API: a bad key is HTTP 400 reason ``badRequest``."""
    import httplib2
    from googleapiclient.errors import HttpError

    from src.domain.exceptions import ConfigurationError

    body = (
        b'{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",'
        b' "errors": [{"message": "API key not valid. Please pass a valid API key.",'
        b' "domain": "global", "reason": "badRequest"}], "status": "INVALID_ARGUMENT"}}'
    )
    stub = StubYouTube(pages_of(["a"]))
    stub.error = HttpError(httplib2.Response({"status": "400"}), body, uri="https://x?key=abc")
    provider, _ = make_provider(stub)
    with pytest.raises(ConfigurationError, match="YOUTUBE_API_KEY"):
        await IngestPlaylistUseCase(repo, provider).ingest(URL)


async def test_retry_or_regenerate_of_a_removed_upstream_video_is_refused_not_stranded(
    repo: PlaylistRepository,
) -> None:
    from src.application.use_cases.playlist.actions import RegenerateVideoUseCase
    from src.domain.exceptions import ConflictError

    playlist_pk = await _ingest(repo, ["a"])
    (video,) = await repo.list_videos(playlist_pk)
    await repo.update_stage(
        video.id,
        Stage.FAILED,
        failure_type=FailureType.NO_USABLE_LANGUAGE,
        failure_kind=FailureKind.PERMANENT,
        failure_reason="No transcript.",
    )
    await repo.flag_removed_upstream([video.id])

    with pytest.raises(ConflictError, match="no longer in the YouTube playlist"):
        await RetryVideoUseCase(repo).execute(video.id)
    with pytest.raises(ConflictError, match="no longer in the YouTube playlist"):
        await RegenerateVideoUseCase(repo).execute(video.id)
    assert (await repo.get_video(video.id)).stage == Stage.FAILED  # untouched, not queued

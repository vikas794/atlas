"""HTTP API behaviour for playlists and videos (PRD section 16).

The real FastAPI app, routers, use cases, repository (temp SQLite) and the real
``PlaylistWorker`` class are used; only YouTube, the transcript fetcher, the LLM and Google
Drive are replaced by the fakes in ``conftest.py``.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from src.application.worker import PlaylistWorker
from src.domain.models.playlist import FailureKind, FailureType, Stage
from src.domain.services.sync_diff import PlaylistEntry
from src.transport.http.fastapi import dependencies as deps
from tests.conftest import ApiEnv, drain_pipeline

URL = "https://www.youtube.com/playlist?list=PL_TEST_123"


@pytest.fixture
def client(api_env: ApiEnv, idle_worker: PlaylistWorker) -> Any:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def ingest(client: TestClient, url: str = URL) -> dict[str, Any]:
    resp = client.post("/api/playlists", json={"url": url})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def run_pipeline(worker: PlaylistWorker) -> None:
    asyncio.run(drain_pipeline(worker))


def videos_of(client: TestClient, playlist_id: int, **params: Any) -> list[dict[str, Any]]:
    return client.get(f"/api/playlists/{playlist_id}/videos", params=params).json()["videos"]


def repo_call(coro_fn: Any, *args: Any, **kwargs: Any) -> Any:
    return asyncio.run(coro_fn(*args, **kwargs))


# ------------------------------------------------------------------ ingestion


def test_new_playlist_returns_201_and_resubmitting_returns_200(client: TestClient) -> None:
    first = client.post("/api/playlists", json={"url": URL})
    assert first.status_code == 201
    data = first.json()
    assert data["playlist_id"] == "PL_TEST_123"
    assert data["title"] == "Test Course"
    assert data["video_count"] == 2
    assert data["stage_counts"]["queued"] == 2

    again = client.post("/api/playlists", json={"url": URL})
    assert again.status_code == 200  # the incremental re-run, not a creation
    assert again.json()["id"] == data["id"]
    assert client.get("/api/playlists").json()["total"] == 1


def test_positions_are_one_based(client: TestClient) -> None:
    playlist = ingest(client)

    assert [v["position"] for v in videos_of(client, playlist["id"])] == [1, 2]


def test_resubmitting_after_upstream_changes_appends_only_new_videos(
    client: TestClient, api_env: ApiEnv
) -> None:
    playlist = ingest(client)
    api_env.youtube.videos.append(PlaylistEntry("vid_3", "Lesson 3", 3))

    again = client.post("/api/playlists", json={"url": URL})

    assert again.status_code == 200
    assert again.json()["video_count"] == 3
    assert [v["position"] for v in videos_of(client, playlist["id"])] == [1, 2, 3]


def test_invalid_url_returns_envelope(client: TestClient) -> None:
    resp = client.post("/api/playlists", json={"url": "https://example.com/not-a-youtube-playlist"})

    assert resp.status_code == 400
    data = resp.json()
    assert data["code"] == "INVALID_URL"
    assert data["details"]["failure_type"] == "invalid_url"


def test_missing_youtube_key_is_a_503_naming_the_variable(
    api_env: ApiEnv, idle_worker: PlaylistWorker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real provider factory, with no key: a clear config error, and nothing cached."""
    monkeypatch.undo()  # restore the real get_youtube_provider (and the env) for this test
    monkeypatch.setenv("ATLAS_DB_PATH", str(api_env.db_path))
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.setattr(
        "src.config.settings.load_dotenv", lambda *a, **k: False
    )  # ignore a developer's real .env
    deps.reset_dependency_caches()

    with TestClient(app, raise_server_exceptions=False) as test_client:
        # Validation comes first: a bad URL is a 400 even before the key is needed.
        bad = test_client.post("/api/playlists", json={"url": "https://example.com/x"})
        assert bad.status_code == 400 and bad.json()["code"] == "INVALID_URL"

        resp = test_client.post("/api/playlists", json={"url": URL})
        assert resp.status_code == 503
        body = resp.json()
        assert body["code"] == "CONFIG_MISSING"
        assert "YOUTUBE_API_KEY" in body["message"]
        assert set(body) == {"code", "message", "details"}

        # Not cached: once the key exists the very next call works without a restart.
        monkeypatch.setenv("YOUTUBE_API_KEY", "AIzaFAKEKEYFORTESTS000000")
        provider = deps.get_youtube_provider()
        assert provider.api_key == "AIzaFAKEKEYFORTESTS000000"
    deps._youtube_provider = None


def test_unknown_playlist_is_404_with_envelope(client: TestClient) -> None:
    for method, path in [
        ("get", "/api/playlists/99999"),
        ("get", "/api/playlists/99999/videos"),
        ("post", "/api/playlists/99999/process"),
        ("post", "/api/playlists/99999/reprocess"),
        ("delete", "/api/playlists/99999"),
        ("post", "/api/playlists/99999/approve-all"),
        ("delete", "/api/playlists/99999/transcripts"),
        ("post", "/api/playlists/99999/publish"),
    ]:
        resp = getattr(client, method)(path)
        assert resp.status_code == 404, path
        body = resp.json()
        assert body["code"] in {"NOT_FOUND", "HTTP_404"}, path
        assert set(body) == {"code", "message", "details"}


def test_unknown_video_is_404(client: TestClient) -> None:
    for method, path in [
        ("get", "/api/videos/99999"),
        ("get", "/api/videos/99999/summary"),
        ("post", "/api/videos/99999/approve"),
        ("post", "/api/videos/99999/retry"),
        ("post", "/api/videos/99999/regenerate"),
        ("delete", "/api/videos/99999/transcript"),
    ]:
        assert getattr(client, method)(path).status_code == 404, path
    assert client.put("/api/videos/99999/summary", json={"content": "x"}).status_code == 404


# ----------------------------------------------------------- listing & totals


def test_list_totals_are_true_totals_not_page_sizes(client: TestClient, api_env: ApiEnv) -> None:
    for i in range(3):
        api_env.youtube.videos = [PlaylistEntry(f"v{i}_{n}", f"T{n}", n + 1) for n in range(5)]
        ingest(client, f"https://www.youtube.com/playlist?list=PL_{i}")

    page = client.get("/api/playlists", params={"limit": 2}).json()
    assert len(page["playlists"]) == 2
    assert page["total"] == 3

    playlist_id = page["playlists"][0]["id"]
    paged = client.get(f"/api/playlists/{playlist_id}/videos", params={"page_size": 2, "page": 2})
    body = paged.json()
    assert len(body["videos"]) == 2
    assert body["total"] == 5  # all matching videos, not len(page)
    assert [v["position"] for v in body["videos"]] == [3, 4]


def test_filtering_videos_by_stage_and_rejecting_an_unknown_stage(client: TestClient) -> None:
    playlist = ingest(client)

    assert client.get(f"/api/playlists/{playlist['id']}/videos?stage=queued").json()["total"] == 2
    assert client.get(f"/api/playlists/{playlist['id']}/videos?stage=published").json()["total"] == 0
    bad = client.get(f"/api/playlists/{playlist['id']}/videos?stage=banana")
    assert bad.status_code == 422
    assert set(bad.json()) == {"code", "message", "details"}


def test_video_payload_exposes_retry_state_for_the_ui(
    client: TestClient, idle_worker: PlaylistWorker
) -> None:
    playlist = ingest(client)
    repo = deps.get_playlist_repo()
    vids = videos_of(client, playlist["id"])
    exhausted, scheduled, permanent = vids[0]["id"], vids[1]["id"], vids[1]["id"]
    repo_call(repo.update_stage, exhausted, Stage.FAILED, failure_type=FailureType.RATE_LIMITED,
              failure_kind=FailureKind.TRANSIENT, failure_reason="rate limited", next_retry_at=None)

    video = client.get(f"/api/videos/{exhausted}").json()

    assert video["failure_kind"] == "transient"
    assert video["retries_exhausted"] is True and video["auto_retry"] is False
    repo_call(repo.update_stage, scheduled, Stage.FAILED, failure_type=FailureType.RATE_LIMITED,
              failure_kind=FailureKind.TRANSIENT, failure_reason="x",
              next_retry_at="2099-01-01T00:00:00+00:00")
    again = client.get(f"/api/videos/{scheduled}").json()
    assert again["auto_retry"] is True and again["retries_exhausted"] is False
    assert again["duplicate_of"] is None and again["regenerating"] is False
    del permanent


# --------------------------------------------------------- worker wiring (B2)


def test_process_and_reprocess_wake_the_real_worker(
    client: TestClient, idle_worker: PlaylistWorker
) -> None:
    playlist = ingest(client)
    idle_worker._wake_event.clear()

    assert client.post(f"/api/playlists/{playlist['id']}/process").status_code == 202
    assert idle_worker._wake_event.is_set()

    idle_worker._wake_event.clear()
    assert client.post(f"/api/playlists/{playlist['id']}/reprocess").status_code == 202
    assert idle_worker._wake_event.is_set()


def test_retry_wakes_the_worker_and_requeues(
    client: TestClient, idle_worker: PlaylistWorker
) -> None:
    playlist = ingest(client)
    video_id = videos_of(client, playlist["id"])[0]["id"]
    repo = deps.get_playlist_repo()
    repo_call(repo.update_stage, video_id, Stage.FAILED, failure_type=FailureType.RATE_LIMITED,
              failure_kind=FailureKind.TRANSIENT, failure_reason="429")
    idle_worker._wake_event.clear()

    resp = client.post(f"/api/videos/{video_id}/retry")

    assert resp.status_code == 200
    assert resp.json()["stage"] == "queued" and resp.json()["failure_type"] is None
    assert idle_worker._wake_event.is_set()


# ----------------------------------------------------- review flow end to end


def test_full_review_flow_through_the_pipeline(
    client: TestClient, idle_worker: PlaylistWorker, api_env: ApiEnv
) -> None:
    playlist = ingest(client)
    run_pipeline(idle_worker)  # the worker processes both videos
    vids = videos_of(client, playlist["id"])
    assert [v["stage"] for v in vids] == ["pending_review", "pending_review"]
    assert sorted(api_env.transcripts.calls) == ["vid_1", "vid_2"]
    video_id = vids[0]["id"]

    summary = client.get(f"/api/videos/{video_id}/summary").json()
    assert "Notes for Lesson 1" in summary["content"] and summary["edited"] is False
    assert summary["prompt_version"] == "notes_test"

    put = client.put(f"/api/videos/{video_id}/summary", json={"content": "## Notes\n\nEdited"})
    assert put.status_code == 200 and put.json()["edited"] is True

    approved = client.post(f"/api/videos/{video_id}/approve")
    assert approved.status_code == 200 and approved.json()["stage"] == "approved"

    bulk = client.post(f"/api/playlists/{playlist['id']}/approve-all")
    assert bulk.json()["count"] == 1  # only the remaining pending_review video


def test_edit_approve_and_regenerate_guards_return_409(
    client: TestClient, idle_worker: PlaylistWorker
) -> None:
    playlist = ingest(client)
    video_id = videos_of(client, playlist["id"])[0]["id"]  # still queued: no notes yet

    edit = client.put(f"/api/videos/{video_id}/summary", json={"content": "x"})
    approve = client.post(f"/api/videos/{video_id}/approve")
    regenerate_queued = client.post(f"/api/videos/{video_id}/regenerate")

    assert edit.status_code == 409 and edit.json()["code"] == "CONFLICT"
    assert approve.status_code == 409 and approve.json()["code"] == "INVALID_STATE"
    assert regenerate_queued.status_code == 409
    for resp in (edit, approve, regenerate_queued):
        assert set(resp.json()) == {"code", "message", "details"}

    # Stage says review but no notes exist: approving must not silently succeed.
    repo = deps.get_playlist_repo()
    repo_call(repo.update_stage, video_id, Stage.PENDING_REVIEW)
    no_notes = client.post(f"/api/videos/{video_id}/approve")
    assert no_notes.status_code == 409 and no_notes.json()["code"] == "CONFLICT"


def test_regenerate_returns_202_immediately_and_the_worker_finishes_it(
    client: TestClient, idle_worker: PlaylistWorker, api_env: ApiEnv
) -> None:
    playlist = ingest(client)
    run_pipeline(idle_worker)
    video_id = videos_of(client, playlist["id"])[0]["id"]
    calls_before = len(api_env.notes.calls)

    resp = client.post(f"/api/videos/{video_id}/regenerate")

    assert resp.status_code == 202
    body = resp.json()
    assert body["stage"] == "queued" and body["regenerating"] is True
    assert len(api_env.notes.calls) == calls_before  # no LLM call inside the request

    run_pipeline(idle_worker)

    done = client.get(f"/api/videos/{video_id}").json()
    assert done["stage"] == "pending_review" and done["regenerating"] is False
    assert len(api_env.notes.calls) == calls_before + 1
    assert sorted(api_env.transcripts.calls) == ["vid_1", "vid_2"]  # transcript reused, not re-fetched


def test_regenerate_with_a_temporary_prompt_uses_it_for_that_run_only(
    client: TestClient, idle_worker: PlaylistWorker, api_env: ApiEnv
) -> None:
    playlist = ingest(client)
    run_pipeline(idle_worker)
    video_id = videos_of(client, playlist["id"])[0]["id"]
    prompt = "Keep it short. {{Title}} {{Transcript}}"

    resp = client.post(f"/api/videos/{video_id}/regenerate", json={"prompt": prompt})
    assert resp.status_code == 202
    run_pipeline(idle_worker)

    assert api_env.notes.templates[-1] == prompt
    summary = client.get(f"/api/videos/{video_id}/summary").json()
    assert summary["prompt_version"] == "custom"

    assert client.post(f"/api/videos/{video_id}/regenerate").status_code == 202  # no body
    run_pipeline(idle_worker)
    assert api_env.notes.templates[-1] is None
    assert client.get(f"/api/videos/{video_id}/summary").json()["prompt_version"] == "notes_test"


def test_regenerate_rejects_a_prompt_without_the_placeholders(
    client: TestClient, idle_worker: PlaylistWorker, api_env: ApiEnv
) -> None:
    playlist = ingest(client)
    run_pipeline(idle_worker)
    video_id = videos_of(client, playlist["id"])[0]["id"]
    calls_before = len(api_env.notes.calls)

    resp = client.post(f"/api/videos/{video_id}/regenerate", json={"prompt": "no placeholders"})

    assert resp.status_code == 409
    assert "{{Transcript}}" in resp.json()["message"]
    run_pipeline(idle_worker)
    assert len(api_env.notes.calls) == calls_before  # nothing was queued
    assert client.get(f"/api/videos/{video_id}").json()["stage"] == "pending_review"


def test_regenerate_after_transcript_cleanup_refetches(
    client: TestClient, idle_worker: PlaylistWorker, api_env: ApiEnv
) -> None:
    playlist = ingest(client)
    run_pipeline(idle_worker)
    video_id = videos_of(client, playlist["id"])[0]["id"]

    assert client.delete(f"/api/videos/{video_id}/transcript").status_code == 200
    assert client.delete(f"/api/videos/{video_id}/transcript").status_code == 404  # already clear
    assert client.post(f"/api/videos/{video_id}/regenerate").status_code == 202
    run_pipeline(idle_worker)

    assert sorted(api_env.transcripts.calls) == ["vid_1", "vid_1", "vid_2"]  # re-entered fetching
    assert client.get(f"/api/videos/{video_id}").json()["stage"] == "pending_review"


def test_playlist_transcript_cleanup_counts_cleared_rows(
    client: TestClient, idle_worker: PlaylistWorker
) -> None:
    playlist = ingest(client)
    run_pipeline(idle_worker)

    resp = client.delete(f"/api/playlists/{playlist['id']}/transcripts")

    assert resp.status_code == 200 and resp.json()["count"] == 2


def test_delete_playlist(client: TestClient) -> None:
    playlist = ingest(client)

    deleted = client.delete(f"/api/playlists/{playlist['id']}")

    assert deleted.status_code == 200 and deleted.json()["status"] == "ok"
    assert client.get(f"/api/playlists/{playlist['id']}").status_code == 404


# --------------------------------------------------------------- publishing (M2, M8)


def approve_everything(client: TestClient, worker: PlaylistWorker, playlist_id: int) -> None:
    run_pipeline(worker)
    assert client.post(f"/api/playlists/{playlist_id}/approve-all").status_code == 200


def test_publish_returns_202_at_once_and_the_worker_publishes_in_the_background(
    client: TestClient, idle_worker: PlaylistWorker, api_env: ApiEnv
) -> None:
    playlist = ingest(client)
    approve_everything(client, idle_worker, playlist["id"])

    resp = client.post(f"/api/playlists/{playlist['id']}/publish")

    assert resp.status_code == 202
    assert resp.json()["status"] == "accepted"
    assert api_env.publisher.calls == 0  # nothing slow ran inside the request
    polled = client.get(f"/api/playlists/{playlist['id']}").json()
    assert polled["status"] == "publishing"
    # A second request while one is running is a conflict, not a duplicate job.
    assert client.post(f"/api/playlists/{playlist['id']}/publish").status_code == 409

    run_pipeline(idle_worker)

    polled = client.get(f"/api/playlists/{playlist['id']}").json()
    assert polled["status"] == "published" and polled["last_publish_error"] is None
    assert polled["drive_folder_id"] == "folder-1" and polled["drive_index_doc_id"] == "index-1"
    assert {v["stage"] for v in videos_of(client, playlist["id"])} == {"published"}
    assert all(v["drive_doc_id"] for v in videos_of(client, playlist["id"]))


def test_a_failed_background_publish_shows_its_error_on_the_playlist(
    client: TestClient, idle_worker: PlaylistWorker, api_env: ApiEnv
) -> None:
    playlist = ingest(client)
    approve_everything(client, idle_worker, playlist["id"])
    api_env.publisher.fail = "Drive quota exhausted"

    assert client.post(f"/api/playlists/{playlist['id']}/publish").status_code == 202
    run_pipeline(idle_worker)

    polled = client.get(f"/api/playlists/{playlist['id']}").json()
    assert polled["status"] == "publish_failed"
    assert "Drive quota exhausted" in polled["last_publish_error"]


def test_publish_without_drive_consent_is_409_with_a_hint(
    client: TestClient, api_env: ApiEnv
) -> None:
    playlist = ingest(client)
    api_env.publisher.authorized = False

    resp = client.post(f"/api/playlists/{playlist['id']}/publish")

    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "DRIVE_AUTH_REQUIRED"
    assert "authorize Google Drive" in body["details"]["hint"]
    assert client.get(f"/api/playlists/{playlist['id']}").json()["status"] == "ingested"


# ----------------------------------------------------------------------- drive


def test_drive_status_reports_states_without_triggering_oauth(
    client: TestClient, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.infrastructure.google.drive import GoogleDriveExporter

    exporter = GoogleDriveExporter(tmp_path / "credentials.json", tmp_path / "token.json")
    app.dependency_overrides[deps.get_drive_exporter] = lambda: exporter
    try:
        missing = client.get("/api/drive/status").json()
        assert missing["state"] == "credentials_missing" and missing["configured"] is False

        (tmp_path / "credentials.json").write_text("{}")
        consent = client.get("/api/drive/status").json()
        assert consent["state"] == "needs_consent" and consent["configured"] is False
    finally:
        app.dependency_overrides.pop(deps.get_drive_exporter, None)


def test_drive_auth_runs_the_consent_flow_off_the_event_loop(client: TestClient) -> None:
    seen: dict[str, Any] = {}

    class Exporter:
        creds_path = None

        def authenticate(self) -> None:
            seen["thread"] = threading.current_thread().name
            time.sleep(0.01)

    app.dependency_overrides[deps.get_drive_exporter] = Exporter
    try:
        resp = client.post("/api/drive/auth")
    finally:
        app.dependency_overrides.pop(deps.get_drive_exporter, None)

    assert resp.status_code == 200
    # asyncio.to_thread uses the loop's default executor ("asyncio_N" threads).
    assert seen["thread"].startswith("asyncio_")


# --------------------------------------------------------- real worker end to end


def test_the_lifespan_starts_the_real_worker_and_the_pipeline_runs_unattended(
    api_env: ApiEnv,
) -> None:
    """No idle worker here: startup must launch the worker (the Docker container relies on it)."""
    with TestClient(app) as client:
        playlist = ingest(client)
        deadline = time.time() + 10
        stages: list[str] = []
        while time.time() < deadline:
            stages = [v["stage"] for v in videos_of(client, playlist["id"])]
            if stages == ["pending_review", "pending_review"]:
                break
            time.sleep(0.05)

        assert stages == ["pending_review", "pending_review"]
        assert client.post(f"/api/playlists/{playlist['id']}/approve-all").json()["count"] == 2
        assert client.post(f"/api/playlists/{playlist['id']}/publish").status_code == 202
        deadline = time.time() + 10
        status = ""
        while time.time() < deadline:
            status = client.get(f"/api/playlists/{playlist['id']}").json()["status"]
            if status == "published":
                break
            time.sleep(0.05)
        assert status == "published"


def test_video_response_exposes_the_retry_limit_for_attempt_n_of_m() -> None:
    from src.domain.models.playlist import Video
    from src.transport.http.fastapi.schemas.playlists import VideoResponse

    video = Video(youtube_video_id="v1", id=1, playlist_id=1, retry_count=2)
    assert VideoResponse.from_domain(video).max_auto_retries >= 1


def test_summary_response_flags_edits_made_after_publishing() -> None:
    from src.domain.models.playlist import Summary
    from src.shared.text import sha256_text
    from src.transport.http.fastapi.schemas.playlists import SummaryResponse

    published = Summary(
        video_id=1, id=1, content="v1", published=True, published_digest=sha256_text("v1")
    )
    assert SummaryResponse.from_domain(published).needs_republish is False
    published.content, published.edited = "v2", True
    assert SummaryResponse.from_domain(published).needs_republish is True
    draft = Summary(video_id=1, id=2, content="x", edited=True)
    assert SummaryResponse.from_domain(draft).needs_republish is False

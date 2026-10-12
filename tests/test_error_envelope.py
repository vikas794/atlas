"""Every error kind answers with the PRD body ``{code, message, details}`` (section 16)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.main import app
from src.domain.exceptions import (
    ConfigurationError,
    ConflictError,
    NotFoundError,
    ProviderError,
)
from src.domain.models.playlist import FailureType, PipelineError
from src.transport.http.fastapi.errors import HTTPError, register_error_handlers
from src.transport.http.fastapi.middleware import RequestLoggingMiddleware

KEY = "AIzaSyA1234567890abcdefghijklmnopqrstu"


def assert_envelope(resp, status: int, code: str | None = None) -> dict:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"code", "message", "details"}, body
    assert isinstance(body["details"], dict)
    assert body["message"], "message must never be empty"
    if code is not None:
        assert body["code"] == code
    assert "Traceback" not in resp.text
    assert KEY not in resp.text
    return body


class Payload(BaseModel):
    count: int


@pytest.fixture(scope="module")
def probe() -> TestClient:
    """A throwaway app with the production handlers and one route per error kind."""
    probe_app = FastAPI()
    probe_app.add_middleware(RequestLoggingMiddleware)
    register_error_handlers(probe_app)

    raisers = {
        "not-found": NotFoundError("Playlist 9 not found"),
        "conflict": ConflictError("already publishing"),
        "config": ConfigurationError(f"YOUTUBE_API_KEY is not set (got {KEY})"),
        "provider": ProviderError(f"upstream said key={KEY}", provider="youtube"),
        "pipeline-url": PipelineError(FailureType.INVALID_URL, "Not a playlist URL"),
        "http-error": HTTPError(400, f"bad {KEY}", error_code="DRIVE_AUTH_FAILED"),
        "boom": RuntimeError(f"kaboom {KEY}"),
        "dict-detail": HTTPException(status_code=409, detail={"reason": "x"}),
    }

    @probe_app.get("/raise/{kind}")
    def raise_kind(kind: str) -> None:
        raise raisers[kind]

    @probe_app.post("/body")
    def body(payload: Payload) -> dict:
        return {"count": payload.count}

    return TestClient(probe_app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("kind", "status", "code"),
    [
        ("not-found", 404, "NOT_FOUND"),
        ("conflict", 409, "CONFLICT"),
        ("config", 503, "CONFIG_MISSING"),
        ("provider", 502, "PROVIDER_ERROR_YOUTUBE"),
        ("pipeline-url", 400, "INVALID_URL"),
        ("http-error", 400, "DRIVE_AUTH_FAILED"),
        ("boom", 500, "INTERNAL_ERROR"),
        ("dict-detail", 409, "HTTP_409"),
    ],
)
def test_each_error_kind_maps_to_status_and_envelope(probe, kind, status, code) -> None:
    assert_envelope(probe.get(f"/raise/{kind}"), status, code)


def test_unhandled_exception_hides_the_message(probe) -> None:
    body = assert_envelope(probe.get("/raise/boom"), 500, "INTERNAL_ERROR")
    assert "kaboom" not in body["message"]


def test_dict_detail_keeps_details(probe) -> None:
    body = assert_envelope(probe.get("/raise/dict-detail"), 409)
    assert body["details"] == {"reason": "x"}


def test_validation_errors_name_the_field(probe) -> None:
    body = assert_envelope(probe.post("/body", json={"count": "many"}), 422, "VALIDATION_ERROR")
    assert "body.count" in body["message"]
    assert body["details"]["errors"][0]["loc"] == ["body", "count"]


def test_malformed_json_is_a_422_envelope(probe) -> None:
    resp = probe.post("/body", content=b"{not json", headers={"content-type": "application/json"})
    assert_envelope(resp, 422, "VALIDATION_ERROR")


def test_method_not_allowed_is_an_envelope(probe) -> None:
    assert_envelope(probe.delete("/body"), 405, "HTTP_405")


def test_only_server_errors_reach_the_recent_errors_buffer(probe) -> None:
    from src.shared.logging_setup import RECENT_ERRORS, configure_logging

    configure_logging()
    RECENT_ERRORS.clear()
    probe.get("/raise/not-found")
    probe.post("/body", json={"count": "many"})
    assert RECENT_ERRORS.snapshot() == []

    probe.get("/raise/boom")
    messages = [entry["message"] for entry in RECENT_ERRORS.snapshot()]
    assert any("Unhandled error in GET /raise/boom" in m for m in messages)
    assert all(KEY not in m for m in messages)


def test_correlation_id_is_echoed_only_when_safe(probe) -> None:
    ok = probe.get("/raise/not-found", headers={"X-Correlation-ID": "abc-123"})
    assert ok.headers["X-Correlation-ID"] == "abc-123"
    evil = probe.get("/raise/not-found", headers={"X-Correlation-ID": "x\tforged log line"})
    assert evil.headers["X-Correlation-ID"] != "x\tforged log line"


# --- the real app -----------------------------------------------------------------------


def test_real_app_unknown_route_and_bad_ids(api_env) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    assert_envelope(client.get("/api/nope"), 404, "HTTP_404")
    assert_envelope(client.post("/api/nope"), 404, "HTTP_404")
    assert_envelope(client.get("/api/playlists/abc"), 422, "VALIDATION_ERROR")
    assert_envelope(client.get("/api/playlists/999"), 404, "NOT_FOUND")
    assert_envelope(client.get("/api/videos/999"), 404, "NOT_FOUND")
    assert_envelope(client.post("/api/playlists/999/approve-all"), 404, "NOT_FOUND")
    assert_envelope(client.get("/api/playlists/1/videos?stage=bogus"), 422, "VALIDATION_ERROR")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "https://example.com/playlist?list=PL123",
        "https://www.youtube.com/watch?v=abc",
        "javascript:alert(1)",
    ],
)
def test_real_app_rejects_invalid_playlist_urls_before_storing(api_env, url: str) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    assert_envelope(client.post("/api/playlists", json={"url": url}), 400, "INVALID_URL")
    assert client.get("/api/playlists").json()["total"] == 0


def test_real_app_missing_url_is_422(api_env) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    assert_envelope(client.post("/api/playlists", json={}), 422, "VALIDATION_ERROR")

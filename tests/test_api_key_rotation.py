"""Several API keys per service: parsing, the rotation pool, and failover in each adapter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httplib2
import pytest
from googleapiclient.errors import HttpError

from backend.services.diagnostics import check_llm_key, check_youtube_key
from src.config import load_settings
from src.domain.exceptions import ConfigurationError
from src.domain.models.playlist import FailureType, PipelineError
from src.infrastructure.llm.gemini.notes import GeminiNotesGenerator
from src.infrastructure.youtube import playlist as yt
from src.infrastructure.youtube.playlist import YouTubePlaylistProvider
from src.shared.api_keys import KeyPool, pool_for, read_api_keys, reset_pools

KEY_A, KEY_B, KEY_C = "AIzaKEYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1", "AIzaKEYBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB2", "AIzaKEYCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC3"

NOTES = "## Notes\n" + "A useful line of notes. " * 6 + "\n\n## Key Points\n- one\n- two\n"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    for name in ("GEMINI_API_KEY", "YOUTUBE_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
        for i in range(2, 5):
            monkeypatch.delenv(f"{name}_{i}", raising=False)
    reset_pools()
    yield
    reset_pools()


# --- parsing -------------------------------------------------------------------------------


def test_keys_can_be_a_list_numbered_extras_or_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", f" {KEY_A}, {KEY_B};{KEY_A}\n")
    monkeypatch.setenv("GEMINI_API_KEY_3", KEY_C)
    monkeypatch.setenv("GEMINI_API_KEY_2", "")

    assert read_api_keys("GEMINI_API_KEY") == (KEY_A, KEY_B, KEY_C)


def test_no_keys_is_an_empty_tuple() -> None:
    assert read_api_keys("GEMINI_API_KEY") == ()


# --- pool ----------------------------------------------------------------------------------


def test_pool_round_robins_and_skips_parked_keys() -> None:
    now = [0.0]
    pool = KeyPool(["a", "b", "c"], clock=lambda: now[0])

    assert [pool.acquire() for _ in range(4)] == ["a", "b", "c", "a"]

    pool.cool_down("b", 30)
    pool.disable("c")
    assert [pool.acquire() for _ in range(3)] == ["a", "a", "a"]
    assert pool.status() == {"total": 3, "available": 1, "cooling": 1, "disabled": 1}

    now[0] = 31.0  # the cooldown lapses, the disabled key stays out
    assert {pool.acquire(), pool.acquire()} == {"a", "b"}
    assert pool.acquire(exclude={"a", "b"}) is None


def test_pool_for_keeps_state_until_the_keys_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", f"{KEY_A},{KEY_B}")
    pool = pool_for("GEMINI_API_KEY")
    pool.disable(KEY_A)
    assert pool_for("GEMINI_API_KEY") is pool

    monkeypatch.setenv("GEMINI_API_KEY", f"{KEY_A},{KEY_B},{KEY_C}")
    assert pool_for("GEMINI_API_KEY") is not pool


# --- YouTube -------------------------------------------------------------------------------


def _http_error(status: int, reason: str) -> HttpError:
    body = f'{{"error": {{"errors": [{{"reason": "{reason}"}}]}}}}'.encode()
    return HttpError(httplib2.Response({"status": status}), body)


class _Service:
    """A YouTube service stub for one key: raises ``error`` or serves one video."""

    def __init__(self, key: str, error: Exception | None, calls: list[str]) -> None:
        self.key, self.error, self.calls = key, error, calls

    def playlistItems(self) -> _Service:  # noqa: N802 - mirrors the client library
        return self

    def playlists(self) -> _Service:
        return self

    def list(self, **_: Any) -> _Service:
        return self

    def execute(self) -> dict:
        self.calls.append(self.key)
        if self.error:
            raise self.error
        return {"items": [{"snippet": {"title": "T", "resourceId": {"videoId": "vid1"}}}]}


def _patch_build(monkeypatch: pytest.MonkeyPatch, errors: dict[str, Exception]) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        yt, "build", lambda *_a, developerKey, **_k: _Service(developerKey, errors.get(developerKey), calls)
    )
    return calls


def test_youtube_moves_to_the_next_key_when_one_is_out_of_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", f"{KEY_A},{KEY_B}")
    calls = _patch_build(monkeypatch, {KEY_A: _http_error(403, "quotaExceeded")})

    entries = YouTubePlaylistProvider().fetch_playlist_videos("PL1")

    assert [e.youtube_video_id for e in entries] == ["vid1"]
    assert calls == [KEY_A, KEY_B]
    # The exhausted key stays parked: the next request goes straight to the good one.
    YouTubePlaylistProvider().fetch_playlist_videos("PL1")
    assert calls[2:] == [KEY_B]


def test_youtube_all_keys_exhausted_is_a_rate_limit_with_no_key_in_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", f"{KEY_A},{KEY_B}")
    _patch_build(monkeypatch, {k: _http_error(403, "quotaExceeded") for k in (KEY_A, KEY_B)})

    with pytest.raises(PipelineError) as excinfo:
        YouTubePlaylistProvider().fetch_playlist_videos("PL1")

    assert excinfo.value.failure_type is FailureType.RATE_LIMITED
    assert KEY_A not in str(excinfo.value) and KEY_B not in str(excinfo.value)


def test_youtube_rejected_key_is_dropped_and_a_missing_playlist_does_not_rotate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", f"{KEY_A},{KEY_B}")
    calls = _patch_build(monkeypatch, {KEY_A: _http_error(400, "keyInvalid")})
    assert YouTubePlaylistProvider().get_playlist_title("PL1") == "T"
    assert pool_for("YOUTUBE_API_KEY").status()["disabled"] == 1

    calls.clear()
    reset_pools()
    calls = _patch_build(monkeypatch, {KEY_A: _http_error(404, "playlistNotFound")})
    with pytest.raises(PipelineError) as excinfo:
        YouTubePlaylistProvider().fetch_playlist_videos("PL1")
    assert excinfo.value.failure_type is FailureType.PLAYLIST_UNAVAILABLE
    assert calls == [KEY_A]  # another key cannot fix a private playlist


def test_youtube_without_any_key_still_names_the_variable() -> None:
    with pytest.raises(ConfigurationError, match="YOUTUBE_API_KEY"):
        YouTubePlaylistProvider()


# --- Gemini --------------------------------------------------------------------------------


class _ApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _gemini_client(error: Exception | None, calls: list[str], key: str) -> Any:
    async def generate_content(**_: Any) -> Any:
        calls.append(key)
        if error:
            raise error
        return SimpleNamespace(text=NOTES, usage_metadata=None, candidates=[], prompt_feedback=None)

    return SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))


def _generator(monkeypatch: pytest.MonkeyPatch, errors: dict[str, Exception]) -> tuple[Any, list[str]]:
    calls: list[str] = []
    generator = GeminiNotesGenerator(load_settings().to_loader())
    monkeypatch.setattr(
        generator, "_client_for", lambda key: _gemini_client(errors.get(key), calls, key)
    )
    return generator, calls


async def test_gemini_rate_limited_key_fails_over_and_is_parked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", f"{KEY_A},{KEY_B}")
    generator, calls = _generator(monkeypatch, {KEY_A: _ApiError(429, "RESOURCE_EXHAUSTED")})

    notes = await generator.generate_notes(title="T", transcript="hello")
    assert notes.provider == "gemini"
    assert calls == [KEY_A, KEY_B]

    await generator.generate_notes(title="T", transcript="hello")
    assert calls[2:] == [KEY_B]  # A is cooling down


async def test_gemini_every_key_rate_limited_surfaces_a_transient_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", f"{KEY_A},{KEY_B}")
    err = _ApiError(429, "RESOURCE_EXHAUSTED")
    generator, calls = _generator(monkeypatch, {KEY_A: err, KEY_B: err})

    with pytest.raises(PipelineError) as excinfo:
        await generator.generate_notes(title="T", transcript="hello")

    assert excinfo.value.failure_type is FailureType.RATE_LIMITED
    assert calls == [KEY_A, KEY_B]


async def test_gemini_rejected_key_is_skipped_but_a_server_error_does_not_rotate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", f"{KEY_A},{KEY_B}")
    generator, calls = _generator(monkeypatch, {KEY_A: _ApiError(400, "API key not valid")})
    await generator.generate_notes(title="T", transcript="hello")
    assert calls == [KEY_A, KEY_B]
    assert pool_for("GEMINI_API_KEY").status()["disabled"] == 1

    reset_pools()
    generator, calls = _generator(monkeypatch, {KEY_A: _ApiError(503, "unavailable")})
    with pytest.raises(PipelineError) as excinfo:
        await generator.generate_notes(title="T", transcript="hello")
    assert excinfo.value.failure_type is FailureType.PROVIDER_ERROR
    assert calls == [KEY_A]


# --- diagnostics ---------------------------------------------------------------------------


def test_diagnostics_reports_the_key_count_but_never_a_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", f"{KEY_A},{KEY_B}")
    monkeypatch.setenv("GEMINI_API_KEY", KEY_C)
    settings = load_settings()

    youtube, llm = check_youtube_key(settings), check_llm_key(settings)

    assert "2 keys" in youtube.message and "keys" not in llm.message.split("(")[0]
    for check in (youtube, llm):
        assert check.status == "ok"
        assert not any(k in check.message for k in (KEY_A, KEY_B, KEY_C))

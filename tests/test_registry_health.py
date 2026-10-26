"""Key probing and model listing against a stubbed HTTP layer (never the network)."""

from __future__ import annotations

import json

import pytest

from src.infrastructure.llm.registry import health
from src.infrastructure.llm.registry.health import ProviderQueryError

SECRET = "sk-very-secret-value"


class Http:
    def __init__(self, status: int = 200, body: object = None, error: Exception | None = None):
        self.status, self.error = status, error
        self.body = json.dumps(body if body is not None else {}).encode()
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        self.calls.append((url, headers))
        if self.error:
            raise self.error
        return self.status, self.body


def _stub(monkeypatch: pytest.MonkeyPatch, http: Http) -> Http:
    monkeypatch.setattr(health, "_http_get", http)
    return http


@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, "healthy"), (401, "invalid"), (403, "invalid"), (429, "rate_limited"), (503, "error")],
)
async def test_probe_maps_http_status(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: str
) -> None:
    _stub(monkeypatch, Http(status))

    verdict, error = await health.probe_key("nvidia", SECRET, None)

    assert verdict == expected
    assert (error is None) == (expected == "healthy")
    assert SECRET not in (error or "")


async def test_probe_transport_error_is_not_the_keys_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, Http(error=OSError(f"boom {SECRET}")))

    verdict, error = await health.probe_key("freellmapi", SECRET, None)

    assert verdict == "error" and SECRET not in error


async def test_probe_sends_the_key_in_a_header_not_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _stub(monkeypatch, Http())

    await health.probe_key("gemini", SECRET, None)
    await health.probe_key("openrouter", SECRET, None)
    await health.probe_key("freellmapi", SECRET, "http://gw:3001/v1/")

    gemini, openrouter, gateway = http.calls
    assert gemini[0].startswith("https://generativelanguage.googleapis.com")
    assert gemini[1] == {"x-goog-api-key": SECRET}
    assert openrouter[0] == "https://openrouter.ai/api/v1/auth/key"
    assert gateway == ("http://gw:3001/v1/models", {"Authorization": f"Bearer {SECRET}"})
    assert all(SECRET not in url for url, _ in http.calls)


async def test_probe_without_any_endpoint_reports_an_error() -> None:
    verdict, error = await health.probe_key("openai", SECRET, None)

    assert verdict == "error" and "endpoint" in error


async def test_fetch_models_parses_both_response_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, Http(body={"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}]}))
    assert await health.fetch_provider_models("nvidia", SECRET, None) == ["a", "b"]

    _stub(monkeypatch, Http(body={"models": [{"name": "models/gemini-x"}]}))
    assert await health.fetch_provider_models("gemini", SECRET, None) == ["gemini-x"]


async def test_fetch_models_errors_never_contain_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, Http(status=401))
    with pytest.raises(ProviderQueryError) as rejected:
        await health.fetch_provider_models("nvidia", SECRET, None)
    assert rejected.value.status == 401 and SECRET not in rejected.value.message

    _stub(monkeypatch, Http(error=OSError(f"refused {SECRET}")))
    with pytest.raises(ProviderQueryError) as offline:
        await health.fetch_provider_models("nvidia", SECRET, None)
    assert SECRET not in offline.value.message

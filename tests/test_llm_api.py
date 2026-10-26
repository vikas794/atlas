"""The /api/llm routes: models, keys, fallback chain, health checks, usage and the key mask."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.config import load_settings
from src.infrastructure.llm.registry.repo import RegistryRepo
from src.transport.http.fastapi import dependencies as deps
from src.transport.http.fastapi.routers import llm as llm_router
from tests.conftest import ApiEnv

SECRET = "sk-test-0123456789abcdef"


@pytest.fixture
def client(api_env: ApiEnv) -> TestClient:
    """A client on an empty, already-seeded registry (nothing imported from the environment)."""
    from backend.main import app

    assert api_env.db_path is not None
    RegistryRepo(api_env.db_path).set_meta("seeded", "1")
    llm_router._last_test.clear()
    return TestClient(app)


def _add_model(client: TestClient, **overrides) -> dict:
    body = {"provider": "gemini", "model_id": "gemini-test", **overrides}
    response = client.post("/api/llm/models", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _add_key(client: TestClient, provider: str = "gemini", key: str = SECRET, **extra) -> dict:
    response = client.post("/api/llm/keys", json={"provider": provider, "key": key, **extra})
    assert response.status_code == 201, response.text
    return response.json()


# --- providers -----------------------------------------------------------------------------


def test_providers_lists_the_known_ones_with_key_counts(client: TestClient) -> None:
    _add_key(client, "nvidia")
    providers = {p["name"]: p for p in client.get("/api/llm/providers").json()["providers"]}
    assert {"gemini", "openrouter", "freellmapi", "nvidia"} <= set(providers)
    assert providers["nvidia"]["key_count"] == 1
    assert providers["nvidia"]["usable_key_count"] == 1
    assert providers["gemini"]["key_count"] == 0
    assert providers["freellmapi"]["default_api_base"] == "http://localhost:3001/v1"


# --- models --------------------------------------------------------------------------------


def test_creating_a_model_fills_provider_defaults_and_appends_to_the_chain(
    client: TestClient,
) -> None:
    first = _add_model(client)
    second = _add_model(client, provider="nvidia", model_id="deepseek-ai/x", display_name="DS")

    assert first["litellm_model"] == "gemini/gemini-test"
    assert first["display_name"] == "gemini-test"
    assert first["input_token_limit"] == 1_000_000
    assert first["enabled"] is True and first["chain_enabled"] is True
    assert (first["priority"], second["priority"]) == (1, 2)
    assert second["litellm_model"] == "openai/deepseek-ai/x"
    assert second["api_base"] == "https://integrate.api.nvidia.com/v1"
    assert second["request_timeout_seconds"] == 3600.0
    assert second["display_name"] == "DS"
    assert [m["id"] for m in client.get("/api/llm/models").json()["models"]] == [
        first["id"],
        second["id"],
    ]


def test_a_duplicate_model_is_a_409_and_a_bad_body_a_422(client: TestClient) -> None:
    _add_model(client)
    duplicate = client.post("/api/llm/models", json={"provider": "gemini", "model_id": "gemini-test"})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "CONFLICT"

    assert client.post("/api/llm/models", json={"provider": "gemini"}).status_code == 422
    assert (
        client.post(
            "/api/llm/models", json={"provider": "gem ini", "model_id": "x"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/llm/models", json={"provider": "gemini", "model_id": "y", "bogus": 1}
        ).status_code
        == 422
    )


def test_patching_a_model_changes_only_what_was_sent(client: TestClient) -> None:
    model = _add_model(client, rpm_limit=10)
    patched = client.patch(
        f"/api/llm/models/{model['id']}", json={"display_name": "Fast", "rpd_limit": 100}
    ).json()
    assert patched["display_name"] == "Fast"
    assert patched["rpd_limit"] == 100
    assert patched["rpm_limit"] == 10

    unlimited = client.patch(f"/api/llm/models/{model['id']}", json={"rpm_limit": None}).json()
    assert unlimited["rpm_limit"] is None

    disabled = client.patch(f"/api/llm/models/{model['id']}", json={"enabled": False}).json()
    assert disabled["enabled"] is False and disabled["chain_enabled"] is False


def test_nullable_model_fields_that_must_have_a_value_are_rejected(client: TestClient) -> None:
    model = _add_model(client)
    for field in ("display_name", "input_token_limit", "enabled"):
        response = client.patch(f"/api/llm/models/{model['id']}", json={field: None})
        assert response.status_code == 422, field


def test_unknown_ids_are_404_and_delete_removes_the_model(client: TestClient) -> None:
    model = _add_model(client)
    assert client.patch("/api/llm/models/999", json={"enabled": False}).status_code == 404
    assert client.delete("/api/llm/models/999").status_code == 404
    assert client.post("/api/llm/models/999/test").status_code == 404

    assert client.delete(f"/api/llm/models/{model['id']}").status_code == 204
    assert client.get("/api/llm/models").json()["models"] == []


# --- fallback chain ------------------------------------------------------------------------


def test_fallback_reports_key_availability_per_model(client: TestClient) -> None:
    _add_model(client)
    _add_model(client, provider="nvidia", model_id="n1")
    _add_key(client, "gemini")

    entries = {e["provider"]: e for e in client.get("/api/llm/fallback").json()["entries"]}
    assert entries["gemini"]["key_count"] == 1
    assert entries["gemini"]["usable_key_count"] == 1
    assert entries["gemini"]["blocked"] is None
    assert entries["nvidia"]["usable_key_count"] == 0
    assert entries["nvidia"]["blocked"] == "no_keys"


def test_put_fallback_reorders_and_toggles_and_skips_unknown_ids(client: TestClient) -> None:
    a = _add_model(client, model_id="a")
    b = _add_model(client, model_id="b")
    c = _add_model(client, model_id="c")

    response = client.put(
        "/api/llm/fallback",
        json={
            "entries": [
                {"model_id": c["id"], "priority": 1, "enabled": True},
                {"model_id": a["id"], "priority": 2, "enabled": False},
                {"model_id": b["id"], "priority": 3, "enabled": True},
                {"model_id": 12345, "priority": 4, "enabled": True},
            ]
        },
    )
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert [e["model_id"] for e in entries] == ["c", "a", "b"]
    assert [e["priority"] for e in entries] == [1, 2, 3]
    assert [e["chain_enabled"] for e in entries] == [True, False, True]
    assert client.get("/api/llm/fallback").json()["entries"] == entries


def test_put_fallback_validates_the_body(client: TestClient) -> None:
    assert client.put("/api/llm/fallback", json={"entries": [{"model_id": 1}]}).status_code == 422
    bad_priority = {"entries": [{"model_id": 1, "priority": 0, "enabled": True}]}
    assert client.put("/api/llm/fallback", json=bad_priority).status_code == 422


def test_usage_endpoints(client: TestClient) -> None:
    model = _add_model(client, rpm_limit=5)
    _add_key(client)
    rows = client.get("/api/llm/fallback/rate-limit-usage").json()["rows"]
    assert rows[0]["model_pk"] == model["id"]
    assert rows[0]["rpm"] == {"used": 0, "limit": 5}

    usage = client.get("/api/llm/fallback/token-usage").json()
    assert usage["total_requests"] == 0 and usage["total_tokens"] == 0
    assert len(usage["keys"]) == 1


# --- keys ----------------------------------------------------------------------------------


def test_a_key_is_masked_in_every_response_but_reveal(client: TestClient) -> None:
    created = _add_key(client, label="Main", monthly_request_cap=100)
    assert created["label"] == "Main"
    assert created["masked_key"] == "sk-t...cdef"
    assert created["status"] == "unknown" and created["enabled"] is True
    assert created["monthly_request_cap"] == 100 and created["monthly_token_cap"] is None
    assert created["cooldowns"] == [] and created["auto_disabled"] is False

    listing = client.get("/api/llm/keys")
    patched = client.patch(f"/api/llm/keys/{created['id']}", json={"label": "Renamed"})
    for response in (listing, patched, client.get("/api/llm/providers")):
        assert SECRET not in response.text

    assert client.post(f"/api/llm/keys/{created['id']}/reveal").json() == {"key": SECRET}


def test_a_blank_label_gets_a_generated_one(client: TestClient) -> None:
    assert _add_key(client, key="k-1111111111")["label"] == "Gemini key 1"
    assert _add_key(client, key="k-2222222222")["label"] == "Gemini key 2"


def test_key_validation(client: TestClient) -> None:
    assert client.post("/api/llm/keys", json={"provider": "gemini"}).status_code == 422
    assert client.post("/api/llm/keys", json={"provider": "gemini", "key": ""}).status_code == 422
    assert client.post("/api/llm/keys", json={"provider": "gemini", "key": "   "}).status_code == 422
    negative = {"provider": "gemini", "key": "abc", "monthly_token_cap": -1}
    assert client.post("/api/llm/keys", json=negative).status_code == 422


def test_patching_a_key(client: TestClient) -> None:
    created = _add_key(client, monthly_request_cap=50, monthly_token_cap=1000)
    key_id = created["id"]

    capped = client.patch(f"/api/llm/keys/{key_id}", json={"monthly_request_cap": 0}).json()
    assert capped["monthly_request_cap"] is None  # 0 means unlimited
    assert capped["monthly_token_cap"] == 1000

    off = client.patch(f"/api/llm/keys/{key_id}", json={"enabled": False}).json()
    assert off["enabled"] is False

    for field in ("label", "enabled", "key"):
        assert client.patch(f"/api/llm/keys/{key_id}", json={field: None}).status_code == 422
    assert client.patch("/api/llm/keys/999", json={"label": "x"}).status_code == 404


def test_replacing_a_key_resets_its_state_and_cooldowns(client: TestClient) -> None:
    registry = deps.get_model_registry()
    key = registry.repo.add_key(provider="gemini", label="k", key_value="old-key-0000000")
    registry.repo.set_key_health(key.id, status="invalid", error="x", consecutive_failures=2)
    registry.repo.set_cooldown(key.id, "*", 9e12, "heuristic", 0.0)

    patched = client.patch(f"/api/llm/keys/{key.id}", json={"key": "new-key-1111111"}).json()
    assert patched["status"] == "unknown"
    assert patched["consecutive_failures"] == 0
    assert patched["cooldowns"] == []
    assert client.post(f"/api/llm/keys/{key.id}/reveal").json() == {"key": "new-key-1111111"}


def test_cooldowns_show_on_the_key_and_can_be_cleared(client: TestClient) -> None:
    model = _add_model(client)
    key = _add_key(client)
    registry = deps.get_model_registry()
    registry.repo.set_cooldown(key["id"], str(model["id"]), 9e12, "heuristic", 0.0)

    shown = client.get("/api/llm/keys").json()["keys"][0]["cooldowns"]
    assert shown == [{"model_id": str(model["id"]), "expires_at": 9e12, "source": "heuristic"}]
    blocked = client.get("/api/llm/fallback").json()["entries"][0]
    assert blocked["blocked"] == "cooling_down"

    assert client.delete(f"/api/llm/keys/{key['id']}/cooldowns").json() == {"cleared": 1}
    assert client.get("/api/llm/keys").json()["keys"][0]["cooldowns"] == []
    assert client.delete("/api/llm/keys/999/cooldowns").status_code == 404


def test_a_key_switched_off_by_failed_checks_is_flagged(client: TestClient) -> None:
    key = _add_key(client)
    registry = deps.get_model_registry()
    registry.repo.set_key_health(
        key["id"], status="invalid", error="rejected", consecutive_failures=3, enabled=False
    )
    shown = client.get("/api/llm/keys").json()["keys"][0]
    assert shown["auto_disabled"] is True and shown["enabled"] is False

    registry.repo.update_key(key["id"], {"enabled": False})
    registry.repo.set_key_health(key["id"], status="healthy", error=None, consecutive_failures=0)
    assert client.get("/api/llm/keys").json()["keys"][0]["auto_disabled"] is False


def test_delete_key(client: TestClient) -> None:
    key = _add_key(client)
    assert client.delete(f"/api/llm/keys/{key['id']}").status_code == 204
    assert client.get("/api/llm/keys").json()["keys"] == []
    assert client.delete(f"/api/llm/keys/{key['id']}").status_code == 404
    assert client.post(f"/api/llm/keys/{key['id']}/reveal").status_code == 404


def test_check_records_the_verdict(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.infrastructure.llm.registry import health

    verdicts = iter([("healthy", None), ("invalid", "HTTP 401")])

    async def fake_probe(provider: str, key_value: str, api_base: str | None):
        return next(verdicts)

    monkeypatch.setattr(health, "probe_key", fake_probe)
    key = _add_key(client)

    ok = client.post(f"/api/llm/keys/{key['id']}/check").json()
    assert ok["status"] == "healthy" and ok["last_checked_at"] and ok["last_healthy_at"]

    bad = client.post(f"/api/llm/keys/{key['id']}/check").json()
    assert bad["status"] == "invalid"
    assert bad["last_health_error"] == "HTTP 401"
    assert bad["consecutive_failures"] == 1
    assert client.post("/api/llm/keys/999/check").status_code == 404


def test_check_all_returns_every_enabled_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.infrastructure.llm.registry import health

    async def fake_probe(provider: str, key_value: str, api_base: str | None):
        return ("healthy", None)

    monkeypatch.setattr(health, "probe_key", fake_probe)
    one = _add_key(client, key="k-aaaaaaaaaa")
    two = _add_key(client, "nvidia", key="k-bbbbbbbbbb")
    client.patch(f"/api/llm/keys/{two['id']}", json={"enabled": False})

    keys = client.post("/api/llm/keys/check-all").json()["keys"]
    assert [k["id"] for k in keys] == [one["id"]]
    assert keys[0]["status"] == "healthy"


# --- available models ----------------------------------------------------------------------


def test_available_models_need_a_key(client: TestClient) -> None:
    response = client.get("/api/llm/providers/gemini/available-models")
    assert response.status_code == 400
    assert response.json()["code"] == "CONFIG_MISSING"


def test_available_models_come_from_the_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple] = []

    async def fake_fetch(provider: str, key_value: str, api_base: str | None) -> list[str]:
        seen.append((provider, key_value, api_base))
        return ["b-model", "a-model", "a-model"]

    monkeypatch.setattr(llm_router, "fetch_provider_models", fake_fetch)
    _add_model(client, provider="nvidia", model_id="n1")
    _add_key(client, "nvidia")

    body = client.get("/api/llm/providers/nvidia/available-models").json()
    assert body == {"provider": "nvidia", "models": ["a-model", "b-model"]}
    assert seen == [("nvidia", SECRET, "https://integrate.api.nvidia.com/v1")]


def test_a_provider_failure_is_a_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.infrastructure.llm.registry.health import ProviderQueryError

    async def failing(provider: str, key_value: str, api_base: str | None) -> list[str]:
        raise ProviderQueryError(401, "Provider answered 401.")

    monkeypatch.setattr(llm_router, "fetch_provider_models", failing)
    _add_key(client)
    response = client.get("/api/llm/providers/gemini/available-models")
    assert response.status_code == 502
    assert response.json()["code"] == "PROVIDER_ERROR"


# --- model test ----------------------------------------------------------------------------


class _Usage:
    total_tokens = 7


class _Response:
    usage = _Usage()


def test_testing_a_model_pings_through_a_usable_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []

    async def completion(**params):
        calls.append(params)
        return _Response()

    monkeypatch.setattr(llm_router, "_completion", lambda: completion)
    model = _add_model(client)
    _add_key(client)

    body = client.post(f"/api/llm/models/{model['id']}/test").json()
    assert body["success"] is True and body["error"] is None
    assert isinstance(body["latency_ms"], int)
    assert calls[0]["model"] == "gemini/gemini-test"
    assert calls[0]["api_key"] == SECRET

    key = client.get("/api/llm/keys").json()["keys"][0]
    assert key["status"] == "healthy"
    assert client.get("/api/llm/fallback/token-usage").json()["total_tokens"] == 7


def test_testing_a_model_is_throttled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def completion(**params):
        return _Response()

    monkeypatch.setattr(llm_router, "_completion", lambda: completion)
    model = _add_model(client)
    _add_key(client)

    assert client.post(f"/api/llm/models/{model['id']}/test").status_code == 200
    throttled = client.post(f"/api/llm/models/{model['id']}/test")
    assert throttled.status_code == 429
    assert int(throttled.headers["Retry-After"]) >= 1


def test_testing_a_model_without_a_usable_key_explains_why(client: TestClient) -> None:
    model = _add_model(client)
    body = client.post(f"/api/llm/models/{model['id']}/test").json()
    assert body["success"] is False and body["latency_ms"] is None
    assert "Keys page" in body["error"]


def test_a_rejected_key_fails_the_test_and_is_marked_invalid(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Rejected(Exception):
        status_code = 401

    async def completion(**params):
        raise Rejected(f"bad key {params['api_key']}")

    monkeypatch.setattr(llm_router, "_completion", lambda: completion)
    model = _add_model(client)
    _add_key(client)

    body = client.post(f"/api/llm/models/{model['id']}/test").json()
    assert body["success"] is False
    assert SECRET not in body["error"]

    key = client.get("/api/llm/keys").json()["keys"][0]
    assert key["status"] == "invalid"
    assert key["consecutive_failures"] == 1


# --- diagnostics ---------------------------------------------------------------------------


def test_the_diagnostics_llm_check_reads_the_registry(client: TestClient) -> None:
    from backend.services.diagnostics import check_llm_key

    settings = load_settings()
    _add_model(client)
    missing = check_llm_key(settings)
    assert missing.status == "error"
    assert "Keys page" in missing.message

    _add_key(client)
    ok = check_llm_key(settings)
    assert ok.status == "ok"
    assert "1 usable key" in ok.message
    assert SECRET not in ok.message

    client.patch("/api/llm/models/1", json={"enabled": False})
    assert check_llm_key(settings).status == "error"

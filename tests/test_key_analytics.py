"""Per-key analytics: the attempt log (migration 0009), registry stats and GET /api/usage/keys."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from src.infrastructure.llm.registry.repo import RegistryRepo
from src.infrastructure.llm.registry.service import ModelRegistry
from src.infrastructure.storage.migrations import MIGRATIONS
from src.transport.http.fastapi.routers import llm as llm_router
from tests.conftest import ApiEnv

NOW = 1_800_000_000.0
DAY = 86400.0


def _registry(tmp_path) -> ModelRegistry:
    repo = RegistryRepo(tmp_path / "atlas.db")
    return ModelRegistry(repo, clock=lambda: NOW)


def _seed(registry: ModelRegistry):
    repo = registry.repo
    model = repo.add_model(
        provider="gemini",
        model_id="gem-1",
        display_name="gem-1",
        litellm_model="gemini/gem-1",
        api_base=None,
        input_token_limit=1000,
    )
    good = repo.add_key(provider="gemini", label="good", key_value="k-good")
    bad = repo.add_key(provider="gemini", label="bad", key_value="k-bad")
    return model, good, bad


def test_migration_0009_creates_the_attempt_table(tmp_path) -> None:
    assert MIGRATIONS[-1][0] >= "0009_llm_attempts"
    registry = _registry(tmp_path)
    conn = sqlite3.connect(tmp_path / "atlas.db")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_attempts)")}
    conn.close()
    assert {"key_id", "key_label", "success", "error_category", "latency_ms", "cost_usd"} <= columns
    assert registry.attempt_stats("all") == {"keys": [], "models": [], "errors": []}


def test_attempt_stats_split_by_key_model_and_error(tmp_path) -> None:
    registry = _registry(tmp_path)
    model, good, bad = _seed(registry)
    registry.log_attempt(
        good,
        model,
        operation="generate_notes",
        success=True,
        latency_ms=100,
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.5,
    )
    registry.log_attempt(
        good,
        model,
        operation="generate_notes",
        success=True,
        latency_ms=300,
        input_tokens=20,
        output_tokens=5,
        cost_usd=1.5,
    )
    registry.log_attempt(
        bad,
        model,
        operation="generate_notes",
        success=False,
        latency_ms=50,
        error_category="rejected",
    )

    stats = registry.attempt_stats("all")
    keys = {row["label"]: row for row in stats["keys"]}
    assert keys["good"]["requests"] == 2 and keys["good"]["successes"] == 2
    assert keys["good"]["avg_latency_ms"] == 200
    assert keys["good"]["cost_usd"] == pytest.approx(2.0)
    assert keys["bad"]["requests"] == 1 and keys["bad"]["successes"] == 0

    (row,) = stats["models"]
    assert (row["provider"], row["model"], row["requests"]) == ("gemini", "gem-1", 3)
    assert row["input_tokens"] == 30 and row["cost_usd"] == pytest.approx(2.0)
    assert stats["errors"] == [{"category": "rejected", "count": 1}]


def test_range_excludes_old_attempts_and_deleted_keys_keep_their_label(tmp_path) -> None:
    registry = _registry(tmp_path)
    model, good, _bad = _seed(registry)
    repo = registry.repo
    repo.log_attempt(
        key_pk=good.id,
        key_label="good",
        provider="gemini",
        model_id="gem-1",
        operation="generate_notes",
        success=True,
        error_category=None,
        latency_ms=1,
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.1,
        created_at="2020-01-01T00:00:00+00:00",
    )
    registry.log_attempt(good, model, operation="generate_notes", success=True, latency_ms=1)

    assert registry.attempt_stats("24h")["keys"][0]["requests"] == 1
    assert registry.attempt_stats("all")["keys"][0]["requests"] == 2

    repo.delete_key(good.id)
    (row,) = registry.attempt_stats("all")["keys"]
    assert row["label"] == "good" and row["key_id"] == good.id


@pytest.fixture
def client(api_env: ApiEnv) -> TestClient:
    from backend.main import app

    assert api_env.db_path is not None
    RegistryRepo(api_env.db_path).set_meta("seeded", "1")
    llm_router._last_test.clear()
    return TestClient(app)


class _Usage:
    total_tokens = 7
    prompt_tokens = 4
    completion_tokens = 3


class _Response:
    usage = _Usage()


def test_model_test_shows_up_in_the_key_analytics(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def completion(**params):
        return _Response()

    monkeypatch.setattr(llm_router, "_completion", lambda: completion)
    model = client.post(
        "/api/llm/models", json={"provider": "gemini", "model_id": "gemini-test"}
    ).json()
    client.post("/api/llm/keys", json={"provider": "gemini", "key": "sk-test-0123456789abcdef"})
    assert client.post(f"/api/llm/models/{model['id']}/test").json()["success"] is True

    body = client.get("/api/usage/keys", params={"range": "24h"}).json()
    assert body["total_requests"] == 1 and body["total_errors"] == 0
    (key,) = body["by_key"]
    assert key["requests"] == 1 and key["successes"] == 1 and key["success_rate"] == 100.0
    assert key["input_tokens"] == 4 and key["output_tokens"] == 3
    (row,) = body["by_model"]
    assert row["model"] == "gemini-test" and row["total_tokens"] == 7


def test_a_failed_model_test_counts_as_an_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def completion(**params):
        raise RuntimeError("boom")

    monkeypatch.setattr(llm_router, "_completion", lambda: completion)
    model = client.post(
        "/api/llm/models", json={"provider": "gemini", "model_id": "gemini-test"}
    ).json()
    client.post("/api/llm/keys", json={"provider": "gemini", "key": "sk-test-0123456789abcdef"})
    assert client.post(f"/api/llm/models/{model['id']}/test").json()["success"] is False

    body = client.get("/api/usage/keys").json()
    assert body["total_errors"] == 1
    assert body["by_key"][0]["errors"] == 1 and body["by_key"][0]["success_rate"] == 0.0
    assert body["errors"][0]["count"] == 1


def test_invalid_range_is_rejected(client: TestClient) -> None:
    assert client.get("/api/usage/keys", params={"range": "1y"}).status_code == 422

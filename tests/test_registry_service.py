"""Registry policy: usable keys, cooldown ladder, limits, quotas and failure handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.infrastructure.llm.registry import ModelRegistry, RegistryRepo
from src.infrastructure.llm.registry.repo import ALL_MODELS
from src.infrastructure.llm.registry.service import RATE_LIMIT_LADDER


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def registry(tmp_path: Path, clock: Clock) -> ModelRegistry:
    return ModelRegistry(RegistryRepo(tmp_path / "atlas.sqlite3"), clock=clock)


def _model(registry: ModelRegistry, provider: str = "gemini", model_id: str = "m1", **kw):
    return registry.repo.add_model(
        provider=provider,
        model_id=model_id,
        display_name=model_id,
        litellm_model=f"{provider}/{model_id}",
        api_base=None,
        input_token_limit=1000,
        **kw,
    )


def _key(registry: ModelRegistry, provider: str = "gemini", value: str = "k1", **kw):
    return registry.repo.add_key(provider=provider, label=value, key_value=value, **kw)


def test_chain_order_and_blocked_reason_without_keys(registry: ModelRegistry) -> None:
    first = _model(registry, model_id="a")
    second = _model(registry, model_id="b")
    registry.repo.replace_chain([(first.id, 2, True), (second.id, 1, True)])

    result = registry.candidates()

    assert [c.model.model_id for c in result] == ["b", "a"]
    assert all(c.keys == [] and c.blocked == "no_keys" for c in result)


def test_disabled_models_and_keys_are_skipped(registry: ModelRegistry) -> None:
    model = _model(registry)
    _key(registry, value="off", enabled=False)
    good = _key(registry, value="on")
    registry.repo.update_model(_model(registry, model_id="x").id, {"enabled": False})

    (candidate,) = registry.candidates()

    assert candidate.model.id == model.id
    assert [k.id for k in candidate.keys] == [good.id]


def test_keys_rotate_round_robin(registry: ModelRegistry) -> None:
    model = _model(registry)
    a, b = _key(registry, value="a"), _key(registry, value="b")

    first = registry.usable_keys(model)[0]
    second = registry.usable_keys(model)[0]
    third = registry.usable_keys(model)[0]

    assert [first[0].id, second[0].id, third[0].id] == [a.id, b.id, a.id]


def test_rate_limit_ladder_climbs_and_a_success_clears_it(
    registry: ModelRegistry, clock: Clock
) -> None:
    model = _model(registry)
    key = _key(registry)

    durations = []
    for _ in range(5):
        registry.record_failure(key, model, "rate_limited")
        expires, source, set_at = registry.repo.get_cooldown(key.id, str(model.id))
        durations.append(expires - set_at)
        assert source == "heuristic"
        clock.now += 1
    assert durations == [*RATE_LIMIT_LADDER, RATE_LIMIT_LADDER[-1]]
    assert registry.usable_keys(model) == ([], "cooling_down")

    registry.record_success(key, model, 10)

    assert registry.repo.get_cooldown(key.id, str(model.id)) is None
    assert len(registry.usable_keys(model)[0]) == 1


def test_ladder_restarts_after_a_quiet_day(registry: ModelRegistry, clock: Clock) -> None:
    model = _model(registry)
    key = _key(registry)
    registry.record_failure(key, model, "rate_limited")
    registry.record_failure(key, model, "rate_limited")
    clock.now += 2 * 86400

    registry.record_failure(key, model, "rate_limited")

    expires, _, set_at = registry.repo.get_cooldown(key.id, str(model.id))
    assert expires - set_at == RATE_LIMIT_LADDER[0]


def test_retry_after_is_a_floor_and_authoritative(registry: ModelRegistry) -> None:
    model = _model(registry)
    key = _key(registry)

    registry.record_failure(key, model, "rate_limited", retry_after=900)

    expires, source, set_at = registry.repo.get_cooldown(key.id, str(model.id))
    assert (expires - set_at, source) == (900, "authoritative")


def test_rejected_key_turns_invalid_and_is_disabled_after_three(
    registry: ModelRegistry, clock: Clock
) -> None:
    model = _model(registry)
    key = _key(registry)

    for _ in range(3):
        registry.record_failure(key, model, "rejected")

    stored = registry.repo.get_key(key.id)
    assert (stored.status, stored.enabled, stored.consecutive_failures) == ("invalid", False, 3)
    assert registry.repo.get_cooldown(key.id, ALL_MODELS) is not None


def test_credits_failure_cools_the_whole_key_for_a_day(registry: ModelRegistry) -> None:
    model = _model(registry)
    other = _model(registry, model_id="m2")
    key = _key(registry)

    registry.record_failure(key, model, "credits")

    expires, source, set_at = registry.repo.get_cooldown(key.id, ALL_MODELS)
    assert (expires - set_at, source) == (86400, "credit")
    assert registry.usable_keys(other) == ([], "cooling_down")


def test_other_failures_do_not_touch_the_key(registry: ModelRegistry) -> None:
    model = _model(registry)
    key = _key(registry)

    registry.record_failure(key, model, None)

    assert registry.repo.get_key(key.id).status == "unknown"
    assert registry.cooldowns_by_key() == {}


def test_rpm_window_blocks_then_frees(registry: ModelRegistry, clock: Clock) -> None:
    model = _model(registry, rpm_limit=2)
    key = _key(registry)
    registry.begin_attempt(key, model)
    registry.begin_attempt(key, model)

    assert registry.usable_keys(model) == ([], "rate_limited")
    clock.now += 61
    assert len(registry.usable_keys(model)[0]) == 1


def test_tpm_counts_the_estimate_and_rpd_uses_a_day_window(
    registry: ModelRegistry, clock: Clock
) -> None:
    model = _model(registry, tpm_limit=100, rpd_limit=1)
    key = _key(registry)
    registry.record_success(key, model, 60)

    assert registry.usable_keys(model, estimated_tokens=50)[1] == "rate_limited"
    registry.begin_attempt(key, model)
    clock.now += 61
    assert registry.usable_keys(model, estimated_tokens=10)[1] == "rate_limited"  # rpd
    clock.now += 86400
    assert registry.usable_keys(model, estimated_tokens=10)[1] is None


def test_monthly_caps_block_a_key(registry: ModelRegistry) -> None:
    model = _model(registry)
    key = _key(registry, monthly_token_cap=100)
    registry.record_success(key, model, 100)

    assert registry.usable_keys(model) == ([], "monthly_cap")
    usage = registry.token_usage()
    assert usage["total_tokens"] == 100 and usage["total_requests"] == 1
    assert usage["keys"][0]["token_cap"] == 100


def test_usage_reports_follow_the_next_key(registry: ModelRegistry) -> None:
    model = _model(registry, rpm_limit=5)
    key = _key(registry)
    registry.begin_attempt(key, model)
    registry.record_success(key, model, 40)

    (row,) = registry.rate_limit_usage()

    assert row["model_pk"] == model.id
    assert row["rpm"] == {"used": 1, "limit": 5}
    assert row["tpm"]["used"] == 40


def test_success_marks_the_key_healthy(registry: ModelRegistry) -> None:
    model = _model(registry)
    key = _key(registry)

    registry.record_success(key, model, 5)

    stored = registry.repo.get_key(key.id)
    assert stored.status == "healthy" and stored.last_healthy_at


async def test_check_key_updates_status_and_auto_disables(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    _model(registry)
    key = _key(registry)
    verdicts = iter([("healthy", None), ("invalid", "bad"), ("invalid", "bad"), ("invalid", "bad")])

    async def fake_probe(provider, key_value, api_base):
        return next(verdicts)

    monkeypatch.setattr("src.infrastructure.llm.registry.health.probe_key", fake_probe)

    assert (await registry.check_key(key.id)).status == "healthy"
    for _ in range(3):
        checked = await registry.check_key(key.id)
    assert (checked.status, checked.enabled, checked.last_health_error) == ("invalid", False, "bad")


async def test_transport_error_keeps_the_status(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = _key(registry)
    registry.record_success(key, _model(registry), 1)

    async def fake_probe(provider, key_value, api_base):
        return "error", "offline"

    monkeypatch.setattr("src.infrastructure.llm.registry.health.probe_key", fake_probe)

    checked = await registry.check_key(key.id)

    assert (checked.status, checked.last_health_error) == ("healthy", "offline")


async def test_check_all_skips_disabled_keys(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    on = _key(registry, value="on")
    _key(registry, value="off", enabled=False)

    async def fake_probe(provider, key_value, api_base):
        return "healthy", None

    monkeypatch.setattr("src.infrastructure.llm.registry.health.probe_key", fake_probe)

    assert [k.id for k in await registry.check_all()] == [on.id]


def test_deleting_a_key_removes_its_cooldowns_and_usage(registry: ModelRegistry) -> None:
    model = _model(registry)
    key = _key(registry)
    registry.record_failure(key, model, "rate_limited")
    registry.record_success(key, model, 3)

    assert registry.repo.delete_key(key.id)

    assert registry.cooldowns_by_key() == {}
    assert registry.repo.monthly_usage(key.id, "2027-01") == (0, 0)

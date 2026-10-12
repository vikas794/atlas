"""Exponential backoff, jitter and retry timestamps."""

from __future__ import annotations

import asyncio
import itertools
import random
from datetime import UTC, datetime

import pytest

from src.domain.services.backoff import compute_delay, iso_after, sleep_for


def test_first_attempt_waits_roughly_the_base_delay() -> None:
    assert compute_delay(1, 5.0, 300.0, jitter_ratio=0.0) == 5.0


def test_delays_grow_monotonically_without_jitter() -> None:
    delays = [compute_delay(attempt, 5.0, 10_000.0, jitter_ratio=0.0) for attempt in range(1, 7)]
    assert delays == [5.0, 10.0, 20.0, 40.0, 80.0, 160.0]
    assert all(b > a for a, b in itertools.pairwise(delays))


def test_growth_is_monotonic_even_with_jitter() -> None:
    rng = random.Random(7)
    delays = [compute_delay(attempt, 2.0, 10_000.0, rng=rng) for attempt in range(1, 6)]
    assert delays == sorted(delays)
    assert delays[0] >= 2.0
    # Attempt 5 waits 16s of backoff, plus at most 20% jitter.
    assert delays[-1] <= 2.0 * 2**4 * 1.2


def test_cap_is_respected_as_the_ceiling_of_the_base_curve() -> None:
    for attempt in (1, 3, 8, 40):
        delay = compute_delay(attempt, 5.0, 60.0, jitter_ratio=0.0)
        assert delay <= 60.0
        assert delay == min(60.0, 5.0 * 2 ** (attempt - 1))


def test_jitter_only_ever_adds_and_stays_within_the_ratio() -> None:
    rng = random.Random(0)
    for attempt in (1, 4, 20):
        base = min(60.0, 5.0 * 2 ** (attempt - 1))
        delay = compute_delay(attempt, 5.0, 60.0, jitter_ratio=0.2, rng=rng)
        assert base <= delay <= base * 1.2


def test_at_the_cap_the_delay_stays_in_the_jitter_band() -> None:
    rng = random.Random(1)
    for _ in range(5):
        delay = compute_delay(30, 5.0, 60.0, jitter_ratio=0.2, rng=rng)
        assert 60.0 <= delay <= 72.0


def test_jitter_is_deterministic_with_an_injected_rng() -> None:
    first = [compute_delay(attempt, 5.0, 300.0, rng=random.Random(0)) for attempt in range(1, 6)]
    second = [compute_delay(attempt, 5.0, 300.0, rng=random.Random(0)) for attempt in range(1, 6)]
    assert first == second


def test_different_seeds_produce_different_delays() -> None:
    first = compute_delay(3, 5.0, 300.0, rng=random.Random(0))
    second = compute_delay(3, 5.0, 300.0, rng=random.Random(99))
    assert first != second


def test_jitter_ratio_zero_disables_jitter() -> None:
    rng = random.Random(0)
    assert compute_delay(4, 5.0, 300.0, jitter_ratio=0.0, rng=rng) == 40.0


def test_attempt_is_one_based_and_clamped() -> None:
    rng = random.Random(0)
    assert compute_delay(0, 5.0, 300.0, jitter_ratio=0.0) == 5.0
    assert compute_delay(-3, 5.0, 300.0, jitter_ratio=0.0) == 5.0
    assert compute_delay(1, 5.0, 300.0, jitter_ratio=0.0, rng=rng) == 5.0


def test_non_positive_base_never_waits() -> None:
    assert compute_delay(3, 0.0, 300.0) == 0.0
    assert compute_delay(3, -1.0, 300.0) == 0.0


async def test_sleep_for_yields_to_the_event_loop() -> None:
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0)
            ticks += 1

    task = asyncio.create_task(ticker())
    await sleep_for(0.05)
    task.cancel()

    assert ticks > 1, "a blocking sleep would have frozen the loop"


async def test_sleep_for_skips_non_positive_delays() -> None:
    await sleep_for(0)
    await sleep_for(-10)


def test_iso_after_is_a_parseable_future_utc_timestamp() -> None:
    before = datetime.now(UTC)
    stamp = iso_after(90)
    parsed = datetime.fromisoformat(stamp)

    assert parsed.tzinfo is not None
    assert parsed > before
    assert (parsed - before).total_seconds() >= 89


def test_iso_after_matches_the_storage_timestamp_format() -> None:
    from src.infrastructure.storage.db import now_iso

    stamp = iso_after(30)
    assert stamp.endswith("+00:00")
    assert len(stamp) == len(now_iso())


def test_iso_after_never_returns_the_past() -> None:
    assert datetime.fromisoformat(iso_after(-500)) <= datetime.now(UTC)


@pytest.mark.parametrize("attempt", [1, 2, 5, 10])
def test_delay_is_never_negative(attempt: int) -> None:
    assert compute_delay(attempt, 1.0, 600.0, rng=random.Random(attempt)) >= 0

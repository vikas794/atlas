"""Exponential backoff with jitter for transient pipeline failures.

PRD section 10 requires transient failures to recover without manual
intervention; the delay doubles per attempt, is capped, and gets a little
random jitter so a re-run of a whole playlist does not hammer the provider in
lockstep. Attempts are 1-based: attempt 1 waits roughly ``base_seconds``.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta

#: Seeded lazily on first use so tests can inject their own ``random.Random``.
_RNG = random.Random()


def compute_delay(
    attempt: int,
    base_seconds: float,
    cap_seconds: float,
    jitter_ratio: float = 0.2,
    rng: random.Random | None = None,
) -> float:
    """Seconds to wait before ``attempt`` (1-based).

    The un-jittered delay is ``min(cap, base * 2 ** (attempt - 1))``; jitter adds
    up to ``jitter_ratio`` of that delay on top, so the returned value may exceed
    ``cap_seconds`` by at most that ratio once the cap is reached. Pass an
    injected ``rng`` (e.g. ``random.Random(0)``) for deterministic results.
    """
    if base_seconds <= 0:
        return 0.0
    attempt = max(1, int(attempt))
    base = min(float(cap_seconds), float(base_seconds) * 2 ** (attempt - 1))
    if jitter_ratio <= 0:
        return base
    generator = _RNG if rng is None else rng
    return base + generator.uniform(0.0, jitter_ratio * base)


async def sleep_for(seconds: float) -> None:
    """Await ``seconds`` without blocking the event loop."""
    if seconds > 0:
        await asyncio.sleep(seconds)


def iso_after(seconds: float) -> str:
    """UTC ISO-8601 timestamp ``seconds`` from now, for ``next_retry_at``."""
    moment = datetime.now(UTC) + timedelta(seconds=max(0.0, float(seconds)))
    return moment.isoformat(timespec="seconds")

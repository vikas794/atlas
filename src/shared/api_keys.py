"""Several API keys per service, rotated so one key's rate limit never stalls Atlas.

A service's keys come from the environment variable named in settings (``GEMINI_API_KEY``):

* a single key: ``GEMINI_API_KEY=abc``;
* a list, separated by commas, semicolons or whitespace: ``GEMINI_API_KEY=abc,def,ghi``;
* numbered extras next to it: ``GEMINI_API_KEY_2=def``, ``GEMINI_API_KEY_3=ghi``.

All forms can be mixed; duplicates are dropped and order is preserved.

:class:`KeyPool` hands keys out round-robin and lets a caller park a key that was rate-limited
(``cool_down``) or rejected (``disable``). It is thread-safe: the YouTube provider runs in worker
threads, the notes generators on the event loop. Key values are never logged here.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable, Iterable

#: Cooldown after a rate-limit / quota answer when the provider gives no better hint.
DEFAULT_COOLDOWN_SECONDS = 60.0
#: Cooldown after the provider says a key has no credit or quota left (retrying soon is futile).
LONG_COOLDOWN_SECONDS = 3600.0

_SPLIT = re.compile(r"[,;\s]+")


def read_api_keys(env_name: str) -> tuple[str, ...]:
    """Every key configured for ``env_name``: the variable itself, then ``<env_name>_2``, ``_3``..."""
    values = [os.environ.get(env_name, "")]
    numbered = re.compile(rf"^{re.escape(env_name)}_(\d+)$")
    extras = sorted(
        (int(m.group(1)), name) for name in os.environ if (m := numbered.match(name))
    )
    values += [os.environ.get(name, "") for _, name in extras]
    keys: list[str] = []
    for value in values:
        for key in _SPLIT.split(value.strip().strip("\"'")):
            key = key.strip().strip("\"'")
            if key and key not in keys:
                keys.append(key)
    return tuple(keys)


class KeyPool:
    """Round-robin over ``keys``, skipping keys that are cooling down or disabled."""

    def __init__(
        self, keys: Iterable[str], *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._keys = tuple(dict.fromkeys(k for k in keys if k))
        self._clock = clock
        self._lock = threading.Lock()
        self._next = 0
        self._cool_until: dict[str, float] = {}
        self._disabled: set[str] = set()

    @property
    def keys(self) -> tuple[str, ...]:
        return self._keys

    def __len__(self) -> int:
        return len(self._keys)

    def acquire(self, exclude: Iterable[str] = ()) -> str | None:
        """The next usable key, or None when every key is excluded, cooling down or disabled."""
        skip = set(exclude)
        now = self._clock()
        with self._lock:
            total = len(self._keys)
            for offset in range(total):
                index = (self._next + offset) % total
                key = self._keys[index]
                if key in skip or key in self._disabled or self._cool_until.get(key, 0.0) > now:
                    continue
                self._next = (index + 1) % total
                return key
        return None

    def cool_down(self, key: str, seconds: float = DEFAULT_COOLDOWN_SECONDS) -> None:
        """Stop using ``key`` for ``seconds`` (it was rate-limited or out of quota)."""
        with self._lock:
            self._cool_until[key] = self._clock() + seconds

    def disable(self, key: str) -> None:
        """Stop using ``key`` until the pool is rebuilt (the provider rejected it)."""
        with self._lock:
            self._disabled.add(key)

    def status(self) -> dict[str, int]:
        """Counts only, safe to show: ``{"total", "available", "cooling", "disabled"}``."""
        now = self._clock()
        with self._lock:
            disabled = sum(1 for k in self._keys if k in self._disabled)
            cooling = sum(
                1 for k in self._keys if k not in self._disabled and self._cool_until.get(k, 0.0) > now
            )
        return {
            "total": len(self._keys),
            "available": len(self._keys) - disabled - cooling,
            "cooling": cooling,
            "disabled": disabled,
        }


_pools: dict[str, KeyPool] = {}
_pools_lock = threading.Lock()


def pool_for(env_name: str) -> KeyPool:
    """The shared pool for ``env_name``. Rebuilt when the configured keys change, so a key added
    to ``.env`` (or a fixed one) takes effect without a restart while cooldowns persist otherwise.
    """
    keys = read_api_keys(env_name)
    with _pools_lock:
        pool = _pools.get(env_name)
        if pool is None or pool.keys != keys:
            pool = _pools[env_name] = KeyPool(keys)
        return pool


def reset_pools() -> None:
    """Forget every pool and its cooldown state (tests, settings reload)."""
    with _pools_lock:
        _pools.clear()


__all__ = [
    "DEFAULT_COOLDOWN_SECONDS",
    "LONG_COOLDOWN_SECONDS",
    "KeyPool",
    "pool_for",
    "read_api_keys",
    "reset_pools",
]

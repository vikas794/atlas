from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class CacheKey:
    namespace: str
    version: str
    content_hash: str
    params_hash: str

    def __str__(self) -> str:
        return f"{self.namespace}:{self.version}:{self.content_hash}:{self.params_hash}"


class CachePort(Protocol):
    async def get(self, key: CacheKey) -> T | None:
        """Retrieve a cached value by key."""
        ...

    async def set(self, key: CacheKey, value: T, ttl_days: int = 30) -> None:
        """Store a value in cache with TTL."""
        ...

    async def delete(self, key: CacheKey) -> None:
        """Remove a value from cache."""
        ...

    async def exists(self, key: CacheKey) -> bool:
        """Check if a key exists in cache."""
        ...

    async def touch(self, key: CacheKey) -> None:
        """Update last access time for a key (hit tracking)."""
        ...

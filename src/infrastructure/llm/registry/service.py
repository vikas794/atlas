"""Registry policy: which key may serve a model right now, and what a result does to a key.

Storage is :class:`~src.infrastructure.llm.registry.repo.RegistryRepo`; this module decides.
A key is *usable* for a model when it is enabled, healthy or not yet checked, not cooling down
(for this model or for the whole key), inside the model's RPM/RPD/TPM/TPD windows and inside the
key's monthly caps. Cooldowns are scoped by ``str(model.id)``; ``ALL_MODELS`` cools the whole key.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.infrastructure.llm.registry.repo import ALL_MODELS, KeyRow, ModelRow, RegistryRepo

logger = logging.getLogger(__name__)

#: Cooldown ladder for a rate-limited (key, model): climbs while 429s keep coming within a day.
RATE_LIMIT_LADDER = (120.0, 600.0, 3600.0, 86400.0)
LADDER_MEMORY_SECONDS = 86400.0
MAX_COOLDOWN_SECONDS = 86400.0
REJECTED_KEY_COOLDOWN_SECONDS = 300.0
CREDITS_COOLDOWN_SECONDS = 86400.0
#: Consecutive ``invalid`` verdicts after which a key is switched off.
AUTO_DISABLE_AFTER = 3
#: ``rate_limited`` is usable: its cooldown row blocks the key for the hour and then it recovers
#: by itself (there is no background checker to flip the status back).
USABLE_STATUSES = ("healthy", "unknown", "rate_limited")

#: Analytics ranges in seconds; ``all`` has no lower bound.
ATTEMPT_RANGES: dict[str, float | None] = {
    "24h": 86400.0,
    "7d": 7 * 86400.0,
    "30d": 30 * 86400.0,
    "all": None,
}

_MINUTE = 60.0
_DAY = 86400.0
#: Order in which a blocked reason is reported when keys are blocked for different reasons.
_REASON_PRIORITY = ("cooling_down", "rate_limited", "monthly_cap")


@dataclass(frozen=True, slots=True)
class Candidate:
    model: ModelRow
    #: Usable keys in the order to try (the starting key rotates per call).
    keys: list[KeyRow]
    #: ``None`` when ``keys`` is non-empty, else why: ``no_keys``, ``cooling_down``,
    #: ``rate_limited`` or ``monthly_cap``.
    blocked: str | None


class ModelRegistry:
    """Selection and bookkeeping over a :class:`RegistryRepo`."""

    def __init__(self, repo: RegistryRepo, *, clock: Callable[[], float] = time.time) -> None:
        self._repo = repo
        self._clock = clock
        self._rotation: dict[int, int] = {}

    @property
    def repo(self) -> RegistryRepo:
        return self._repo

    # --- selection -------------------------------------------------------

    def _month(self, now: float) -> str:
        return datetime.fromtimestamp(now, UTC).strftime("%Y-%m")

    def candidates(self, estimated_tokens: int = 0) -> list[Candidate]:
        """Enabled chain models in priority order, blocked ones included."""
        result: list[Candidate] = []
        for model in self._repo.list_models():
            if model.priority is None or not model.chain_enabled or not model.enabled:
                continue
            keys, blocked = self.usable_keys(model, estimated_tokens)
            result.append(Candidate(model=model, keys=keys, blocked=blocked))
        return result

    def usable_keys(
        self, model: ModelRow, estimated_tokens: int = 0
    ) -> tuple[list[KeyRow], str | None]:
        """Usable keys for ``model`` (rotated round-robin) and the block reason when none."""
        eligible, reason = self._eligible(model, estimated_tokens)
        if len(eligible) > 1:
            start = self._rotation.get(model.id, 0) % len(eligible)
            self._rotation[model.id] = start + 1
            eligible = eligible[start:] + eligible[:start]
        return eligible, reason

    def _eligible(self, model: ModelRow, estimated_tokens: int) -> tuple[list[KeyRow], str | None]:
        now = self._clock()
        month = self._month(now)
        cooling = {(row["key_id"], row["model_id"]) for row in self._repo.active_cooldowns(now)}
        scope = str(model.id)
        usable: list[KeyRow] = []
        reasons: set[str] = set()
        candidates = [
            k
            for k in self._repo.list_keys(model.provider)
            if k.enabled and k.status in USABLE_STATUSES
        ]
        if not candidates:
            return [], "no_keys"
        for key in candidates:
            if (key.id, scope) in cooling or (key.id, ALL_MODELS) in cooling:
                reasons.add("cooling_down")
            elif self._over_window(key, model, scope, now, estimated_tokens):
                reasons.add("rate_limited")
            elif self._over_monthly(key, month):
                reasons.add("monthly_cap")
            else:
                usable.append(key)
        if usable:
            return usable, None
        for reason in _REASON_PRIORITY:
            if reason in reasons:
                return [], reason
        return [], "no_keys"  # pragma: no cover - reasons is never empty here

    def _over_window(
        self, key: KeyRow, model: ModelRow, scope: str, now: float, estimated_tokens: int
    ) -> bool:
        if model.rpm_limit is not None or model.tpm_limit is not None:
            requests, tokens = self._repo.window_usage(key.id, scope, now - _MINUTE)
            if model.rpm_limit is not None and requests >= model.rpm_limit:
                return True
            if model.tpm_limit is not None and tokens + estimated_tokens > model.tpm_limit:
                return True
        if model.rpd_limit is not None or model.tpd_limit is not None:
            requests, tokens = self._repo.window_usage(key.id, scope, now - _DAY)
            if model.rpd_limit is not None and requests >= model.rpd_limit:
                return True
            if model.tpd_limit is not None and tokens >= model.tpd_limit:
                return True
        return False

    def _over_monthly(self, key: KeyRow, month: str) -> bool:
        if not key.monthly_request_cap and not key.monthly_token_cap:
            return False
        requests, tokens = self._repo.monthly_usage(key.id, month)
        if key.monthly_request_cap and requests >= key.monthly_request_cap:
            return True
        return bool(key.monthly_token_cap and tokens >= key.monthly_token_cap)

    # --- outcomes --------------------------------------------------------

    def begin_attempt(self, key: KeyRow, model: ModelRow) -> None:
        """Count the request against the model's windows (failures count too)."""
        self._repo.record_request(key.id, str(model.id), self._clock())

    def record_success(self, key: KeyRow, model: ModelRow, tokens: int) -> None:
        now = self._clock()
        scope = str(model.id)
        self._repo.clear_cooldowns(key.id, scope)
        self._repo.set_key_health(key.id, status="healthy", error=None, consecutive_failures=0)
        self._repo.record_tokens(key.id, scope, max(0, int(tokens)), now, self._month(now))

    def log_attempt(
        self,
        key: KeyRow,
        model: ModelRow,
        *,
        operation: str,
        success: bool,
        latency_ms: int,
        error_category: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        error_message: str | None = None,
    ) -> None:
        """Record one provider call for analytics; never fails the call itself."""
        try:
            self._repo.log_attempt(
                key_pk=key.id,
                key_label=key.label,
                provider=model.provider,
                model_id=model.model_id,
                operation=operation,
                success=success,
                error_category=None if success else (error_category or "unknown"),
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                error_message=None if success else error_message,
                created_at=datetime.fromtimestamp(self._clock(), UTC).isoformat(timespec="seconds"),
            )
        except Exception:
            logger.warning("Attempt log write failed for key %s", key.id, exc_info=False)

    def attempt_stats(self, range_: str) -> dict[str, list[dict[str, Any]]]:
        """Per-key, per-model and per-error attempt stats for ``24h``, ``7d``, ``30d`` or ``all``."""
        seconds = ATTEMPT_RANGES.get(range_)
        since = (
            datetime.fromtimestamp(self._clock() - seconds, UTC).isoformat(timespec="seconds")
            if seconds
            else None
        )
        return self._repo.attempt_stats(since)

    def record_failure(
        self,
        key: KeyRow,
        model: ModelRow,
        problem: str | None,
        retry_after: float | None = None,
    ) -> None:
        """Apply a classified failure: ``rate_limited``, ``rejected``, ``credits`` or ``None``."""
        now = self._clock()
        if problem == "rate_limited":
            self._cool_down_rate_limited(key, str(model.id), now, retry_after)
        elif problem == "rejected":
            self._reject(key, now)
        elif problem == "credits":
            self._repo.set_cooldown(
                key.id, ALL_MODELS, now + CREDITS_COOLDOWN_SECONDS, "credit", now
            )

    def _cool_down_rate_limited(
        self, key: KeyRow, scope: str, now: float, retry_after: float | None
    ) -> None:
        previous = self._repo.get_cooldown(key.id, scope)
        duration = RATE_LIMIT_LADDER[0]
        if previous is not None:
            expires_at, source, set_at = previous
            if source == "heuristic" and now - set_at <= LADDER_MEMORY_SECONDS:
                last = max(expires_at - set_at, 0.0)
                duration = next((s for s in RATE_LIMIT_LADDER if s > last), RATE_LIMIT_LADDER[-1])
        source = "heuristic"
        if retry_after is not None and retry_after > 0:
            duration = max(duration, retry_after)
            source = "authoritative"
        duration = min(duration, MAX_COOLDOWN_SECONDS)
        self._repo.set_cooldown(key.id, scope, now + duration, source, now)

    def _reject(self, key: KeyRow, now: float) -> None:
        current = self._repo.get_key(key.id)
        failures = (current.consecutive_failures if current else key.consecutive_failures) + 1
        disable = failures >= AUTO_DISABLE_AFTER
        self._repo.set_key_health(
            key.id,
            status="invalid",
            error="The provider rejected this key.",
            consecutive_failures=failures,
            enabled=False if disable else None,
        )
        self._repo.set_cooldown(
            key.id, ALL_MODELS, now + REJECTED_KEY_COOLDOWN_SECONDS, "heuristic", now
        )
        if disable:
            logger.warning(
                "Key %s (%s) disabled after %d rejections", key.id, key.provider, failures
            )

    # --- reporting -------------------------------------------------------

    def cooldowns_by_key(self) -> dict[int, list[dict[str, Any]]]:
        result: dict[int, list[dict[str, Any]]] = {}
        for row in self._repo.active_cooldowns(self._clock()):
            result.setdefault(row["key_id"], []).append(
                {
                    "model_id": row["model_id"],
                    "expires_at": row["expires_at"],
                    "source": row["source"],
                }
            )
        return result

    def rate_limit_usage(self) -> list[dict[str, Any]]:
        """Window usage per chain model, measured on the key the router would pick next."""
        now = self._clock()
        rows: list[dict[str, Any]] = []
        for model in self._repo.list_models():
            if model.priority is None:
                continue
            eligible, _ = self._eligible(model, 0)
            key = eligible[0] if eligible else None
            if key is None:
                key = next((k for k in self._repo.list_keys(model.provider) if k.enabled), None)
            scope = str(model.id)
            if key is not None:
                req_m, tok_m = self._repo.window_usage(key.id, scope, now - _MINUTE)
                req_d, tok_d = self._repo.window_usage(key.id, scope, now - _DAY)
            else:
                req_m = tok_m = req_d = tok_d = 0
            rows.append(
                {
                    "model_pk": model.id,
                    "rpm": {"used": req_m, "limit": model.rpm_limit},
                    "rpd": {"used": req_d, "limit": model.rpd_limit},
                    "tpm": {"used": tok_m, "limit": model.tpm_limit},
                    "tpd": {"used": tok_d, "limit": model.tpd_limit},
                }
            )
        return rows

    def token_usage(self) -> dict[str, Any]:
        month = self._month(self._clock())
        keys: list[dict[str, Any]] = []
        total_requests = total_tokens = 0
        for key in self._repo.list_keys():
            requests, tokens = self._repo.monthly_usage(key.id, month)
            total_requests += requests
            total_tokens += tokens
            keys.append(
                {
                    "key_id": key.id,
                    "requests": requests,
                    "tokens": tokens,
                    "request_cap": key.monthly_request_cap,
                    "token_cap": key.monthly_token_cap,
                }
            )
        return {
            "month": month,
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "keys": keys,
        }

    # --- health ----------------------------------------------------------

    def _api_base_for(self, provider: str) -> str | None:
        return next(
            (m.api_base for m in self._repo.list_models() if m.provider == provider and m.api_base),
            None,
        )

    async def check_key(self, key_pk: int) -> KeyRow:
        """Probe one key and record the verdict. A transport error leaves its status alone."""
        from src.infrastructure.llm.registry.health import probe_key

        key = self._repo.get_key(key_pk)
        if key is None:
            raise KeyError(key_pk)
        status, error = await probe_key(
            key.provider, key.key_value, self._api_base_for(key.provider)
        )
        now = self._clock()
        if status == "healthy":
            self._repo.set_key_health(key.id, status="healthy", error=None, consecutive_failures=0)
        elif status == "invalid":
            failures = key.consecutive_failures + 1
            disable = failures >= AUTO_DISABLE_AFTER
            self._repo.set_key_health(
                key.id,
                status="invalid",
                error=error,
                consecutive_failures=failures,
                enabled=False if disable else None,
            )
        elif status == "rate_limited":
            self._repo.set_key_health(
                key.id,
                status="rate_limited",
                error=error,
                consecutive_failures=key.consecutive_failures,
            )
            self._repo.set_cooldown(key.id, ALL_MODELS, now + 3600.0, "heuristic", now)
        else:
            self._repo.set_key_health(
                key.id,
                status=key.status,
                error=error,
                consecutive_failures=key.consecutive_failures,
            )
        updated = self._repo.get_key(key.id)
        assert updated is not None
        return updated

    async def check_all(self) -> list[KeyRow]:
        """Check every enabled key (four at a time)."""
        gate = asyncio.Semaphore(4)
        keys = [k for k in self._repo.list_keys() if k.enabled]

        async def one(key: KeyRow) -> KeyRow | None:
            async with gate:
                try:
                    return await self.check_key(key.id)
                except KeyError:  # deleted while the pass was running
                    return None

        results = await asyncio.gather(*(one(k) for k in keys))
        return [k for k in results if k is not None]


__all__ = [
    "AUTO_DISABLE_AFTER",
    "RATE_LIMIT_LADDER",
    "Candidate",
    "ModelRegistry",
]

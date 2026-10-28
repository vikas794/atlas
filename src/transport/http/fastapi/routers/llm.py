"""Model registry API: models, API keys, the fallback chain, health checks and usage.

Modelled on FreeLLMAPI's admin routes. Everything is stored in SQLite (migration 0007); keys are
plain text there and are masked in every response except ``POST /keys/{id}/reveal``.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.infrastructure.llm.catalog import catalog_models
from src.infrastructure.llm.registry.health import ProviderQueryError, fetch_provider_models
from src.infrastructure.llm.registry.providers import (
    PROVIDERS,
    known_providers,
    litellm_model_for,
    mask_key,
    provider_info,
)
from src.infrastructure.llm.registry.repo import KeyRow, ModelRow
from src.transport.http.fastapi.errors import HTTPError
from src.transport.http.fastapi.schemas.llm import (
    AvailableModelsResponse,
    BulkDeletedResponse,
    BulkUpdatedResponse,
    ChainUpdate,
    ClearedResponse,
    CooldownOut,
    FallbackEntryOut,
    FallbackResponse,
    KeyCreate,
    KeyOut,
    KeyReveal,
    KeysResponse,
    KeyUpdate,
    ModelBulkCreate,
    ModelBulkHide,
    ModelBulkIds,
    ModelBulkResponse,
    ModelCreate,
    ModelOut,
    ModelsResponse,
    ModelTestResponse,
    ModelUpdate,
    ProviderOut,
    ProvidersResponse,
    RateLimitUsageResponse,
)

if TYPE_CHECKING:
    from src.infrastructure.llm.registry.service import ModelRegistry

router = APIRouter(prefix="/api/llm", tags=["llm"])

#: Failed health checks in a row after which a key is switched off (mirrors the registry).
AUTO_DISABLE_AFTER = 3
#: Minimum seconds between two tests of the same model.
TEST_INTERVAL_SECONDS = 5.0
_last_test: dict[int, float] = {}


def _registry() -> ModelRegistry:
    from src.transport.http.fastapi.dependencies import get_model_registry

    return get_model_registry()


def _completion() -> Any:
    """``litellm.acompletion``; a seam so tests never reach a provider."""
    from src.infrastructure.llm.litellm_notes import _litellm_completion

    return _litellm_completion()


def _not_found(what: str, pk: int) -> HTTPError:
    return HTTPError(status_code=404, detail=f"No {what} with id {pk}.", error_code="NOT_FOUND")


def _model_out(model: ModelRow) -> ModelOut:
    return ModelOut(
        id=model.id,
        provider=model.provider,
        model_id=model.model_id,
        display_name=model.display_name,
        litellm_model=model.litellm_model,
        api_base=model.api_base,
        input_token_limit=model.input_token_limit,
        request_timeout_seconds=model.request_timeout_seconds,
        rpm_limit=model.rpm_limit,
        rpd_limit=model.rpd_limit,
        tpm_limit=model.tpm_limit,
        tpd_limit=model.tpd_limit,
        enabled=model.enabled,
        source=model.source,
        priority=model.priority,
        chain_enabled=model.chain_enabled,
        hidden=model.hidden,
    )


def _key_out(key: KeyRow, cooldowns: list[dict[str, Any]]) -> KeyOut:
    return KeyOut(
        id=key.id,
        provider=key.provider,
        label=key.label,
        masked_key=mask_key(key.key_value),
        status=key.status,
        enabled=key.enabled,
        consecutive_failures=key.consecutive_failures,
        last_checked_at=key.last_checked_at,
        last_healthy_at=key.last_healthy_at,
        last_health_error=key.last_health_error,
        monthly_request_cap=key.monthly_request_cap,
        monthly_token_cap=key.monthly_token_cap,
        cooldowns=[
            CooldownOut(
                model_id=str(c["model_id"]),
                expires_at=float(c["expires_at"]),
                source=str(c["source"]),
            )
            for c in cooldowns
        ],
        auto_disabled=(not key.enabled and key.consecutive_failures >= AUTO_DISABLE_AFTER),
    )


def _keys_out(registry: ModelRegistry, keys: list[KeyRow]) -> list[KeyOut]:
    cooldowns = registry.cooldowns_by_key()
    return [_key_out(k, cooldowns.get(k.id, [])) for k in keys]


def _usable(key: KeyRow) -> bool:
    return key.enabled and key.status in ("healthy", "unknown")


# --- providers ---------------------------------------------------------------------------


@router.get("/providers", response_model=ProvidersResponse)
def list_providers() -> ProvidersResponse:
    """Every provider Atlas knows, plus any other provider that has a model or key."""
    registry = _registry()
    keys = registry.repo.list_keys()
    names = list(known_providers())
    for name in [m.provider for m in registry.repo.list_models()] + [k.provider for k in keys]:
        if name not in names:
            names.append(name)
    providers = []
    for name in names:
        info = provider_info(name)
        own = [k for k in keys if k.provider == name]
        providers.append(
            ProviderOut(
                name=name,
                label=info.label,
                default_api_base=info.default_api_base,
                key_count=len(own),
                usable_key_count=sum(1 for k in own if _usable(k)),
            )
        )
    return ProvidersResponse(providers=providers)


@router.get("/providers/{provider}/available-models", response_model=AvailableModelsResponse)
async def available_models(provider: str) -> AvailableModelsResponse:
    """Model ids for the add-model picker: LiteLLM's table merged with the provider's live list.

    The live list needs one of the provider's keys. Without a key, or when the provider cannot be
    reached, the LiteLLM table alone is returned; only when that is empty too does this fail.
    """
    registry = _registry()
    name = provider.strip().lower()
    known = catalog_models(name)
    keys = [k for k in registry.repo.list_keys(name) if k.enabled]
    key = next((k for k in keys if _usable(k)), keys[0] if keys else None)

    live: list[str] = []
    warning: str | None = None
    if key is None:
        if not known:
            raise HTTPError(
                status_code=400,
                detail=f"No enabled {provider_info(name).label} key. Add one on the Keys page to list models.",
                error_code="CONFIG_MISSING",
            )
    else:
        api_base = next(
            (m.api_base for m in registry.repo.list_models() if m.provider == name and m.api_base),
            None,
        )
        try:
            live = await fetch_provider_models(name, key.key_value, api_base)
        except ProviderQueryError as exc:
            if not known:
                raise HTTPError(
                    status_code=502, detail=exc.message, error_code="PROVIDER_ERROR"
                ) from exc
            warning = exc.message
    return AvailableModelsResponse(
        provider=name,
        models=sorted({*known, *live}),
        live=bool(live),
        warning=warning,
        input_token_limits={m: limit for m, limit in known.items() if limit},
    )


# --- models ------------------------------------------------------------------------------


@router.get("/models", response_model=ModelsResponse)
def list_models() -> ModelsResponse:
    return ModelsResponse(models=[_model_out(m) for m in _registry().repo.list_models()])


@router.post("/models", response_model=ModelOut, status_code=201)
def create_model(body: ModelCreate) -> ModelOut:
    registry = _registry()
    provider = body.provider.strip().lower()
    model_id = body.model_id.strip()
    info = provider_info(provider)
    try:
        model = registry.repo.add_model(
            provider=provider,
            model_id=model_id,
            display_name=(body.display_name or "").strip() or model_id,
            litellm_model=litellm_model_for(provider, model_id),
            api_base=(body.api_base or "").strip() or info.default_api_base,
            input_token_limit=body.input_token_limit or info.default_input_token_limit,
            request_timeout_seconds=body.request_timeout_seconds
            or info.default_timeout_seconds,
            rpm_limit=body.rpm_limit,
            rpd_limit=body.rpd_limit,
            tpm_limit=body.tpm_limit,
            tpd_limit=body.tpd_limit,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPError(
            status_code=409,
            detail=f"{info.label} model {model_id!r} is already registered.",
            error_code="CONFLICT",
        ) from exc
    return _model_out(model)


@router.post("/models/bulk", response_model=ModelBulkResponse, status_code=201)
def create_models_bulk(body: ModelBulkCreate) -> ModelBulkResponse:
    """Add many models of one provider, disabled; ids already registered (hidden ones too) are skipped."""
    registry = _registry()
    provider = body.provider.strip().lower()
    info = provider_info(provider)
    api_base = (body.api_base or "").strip() or info.default_api_base
    limits = catalog_models(provider)
    hidden_ids = {m.model_id for m in registry.repo.list_models() if m.provider == provider and m.hidden}
    added = skipped = skipped_hidden = 0
    for model_id in dict.fromkeys(m.strip() for m in body.model_ids if m.strip()):
        try:
            registry.repo.add_model(
                provider=provider,
                model_id=model_id,
                display_name=model_id,
                litellm_model=litellm_model_for(provider, model_id),
                api_base=api_base,
                input_token_limit=limits.get(model_id) or info.default_input_token_limit,
                request_timeout_seconds=info.default_timeout_seconds,
                enabled=False,
            )
            added += 1
        except sqlite3.IntegrityError:
            skipped += 1
            skipped_hidden += model_id in hidden_ids
    return ModelBulkResponse(added=added, skipped=skipped, skipped_hidden=skipped_hidden)


@router.post("/models/bulk-delete", response_model=BulkDeletedResponse)
def delete_models_bulk(body: ModelBulkIds) -> BulkDeletedResponse:
    """Delete many models at once (unknown ids are ignored)."""
    deleted = _registry().repo.delete_models(body.ids)
    for pk in body.ids:
        _last_test.pop(pk, None)
    return BulkDeletedResponse(deleted=deleted)


@router.post("/models/bulk-hide", response_model=BulkUpdatedResponse)
def hide_models_bulk(body: ModelBulkHide) -> BulkUpdatedResponse:
    """Hide (switched off, kept registered so bulk import skips them) or unhide many models."""
    return BulkUpdatedResponse(updated=_registry().repo.set_hidden(body.ids, body.hidden))


@router.patch("/models/{model_pk}", response_model=ModelOut)
def update_model(model_pk: int, body: ModelUpdate) -> ModelOut:
    registry = _registry()
    changes = body.model_dump(exclude_unset=True)
    for required in ("display_name", "input_token_limit", "enabled"):
        if required in changes and changes[required] is None:
            raise HTTPError(
                status_code=422,
                detail=f"{required} cannot be null.",
                error_code="VALIDATION_ERROR",
            )
    if "api_base" in changes:
        changes["api_base"] = (changes["api_base"] or "").strip() or None
    model = registry.repo.update_model(model_pk, changes)
    if model is None:
        raise _not_found("model", model_pk)
    return _model_out(model)


@router.delete("/models/{model_pk}", status_code=204)
def delete_model(model_pk: int) -> Response:
    if not _registry().repo.delete_model(model_pk):
        raise _not_found("model", model_pk)
    _last_test.pop(model_pk, None)
    return Response(status_code=204)


@router.post("/models/{model_pk}/test", response_model=ModelTestResponse)
async def test_model(model_pk: int) -> ModelTestResponse:
    """Send a one-line ping through the real key gates; at most one per model every 5 seconds."""
    registry = _registry()
    model = registry.repo.get_model(model_pk)
    if model is None:
        raise _not_found("model", model_pk)

    now = time.monotonic()
    wait = TEST_INTERVAL_SECONDS - (now - _last_test.get(model_pk, -1e9))
    if wait > 0:
        raise StarletteHTTPException(
            status_code=429,
            detail="This model was tested a moment ago. Wait a few seconds.",
            headers={"Retry-After": str(max(1, int(wait + 0.999)))},
        )
    _last_test[model_pk] = now

    keys, blocked = registry.usable_keys(model)
    if not keys:
        reason = {
            "no_keys": f"No enabled {provider_info(model.provider).label} key. Add one on the Keys page.",
            "cooling_down": "Every key is cooling down after a recent failure.",
            "rate_limited": "Every key is at one of this model's rate limits.",
            "monthly_cap": "Every key has reached its monthly cap.",
        }.get(blocked or "", "No usable key for this provider.")
        return ModelTestResponse(success=False, latency_ms=None, error=reason)
    key = keys[0]

    from src.infrastructure.llm.notes_base import (
        _key_problem_of,
        classify_provider_exception,
        error_category_of,
    )

    params: dict[str, Any] = {
        "model": model.litellm_model,
        "messages": [{"role": "user", "content": "ping"}],
        "api_key": key.key_value,
        "timeout": model.request_timeout_seconds or 60.0,
        "num_retries": 0,
        "max_tokens": 16,
    }
    if model.api_base:
        params["api_base"] = model.api_base

    registry.begin_attempt(key, model)
    started = time.monotonic()
    try:
        response = await _completion()(**params)
    except Exception as exc:
        error = classify_provider_exception(
            exc,
            provider_label=provider_info(model.provider).label,
            api_key_env=f"registry:{key.label}",
            model=model.model_id,
            secrets=(key.key_value,),
        )
        registry.record_failure(key, model, _key_problem_of(error))
        registry.log_attempt(
            key,
            model,
            operation="model_test",
            success=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            error_category=error_category_of(error),
        )
        return ModelTestResponse(success=False, latency_ms=None, error=str(error))
    latency = int((time.monotonic() - started) * 1000)
    usage = getattr(response, "usage", None)
    tokens = int(getattr(usage, "total_tokens", 0) or 0)
    registry.record_success(key, model, tokens)
    cost = await _record_test_usage(model, response, usage)
    registry.log_attempt(
        key,
        model,
        operation="model_test",
        success=True,
        latency_ms=latency,
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        cost_usd=cost,
    )
    return ModelTestResponse(success=True, latency_ms=latency, error=None)


async def _record_test_usage(model: ModelRow, response: Any, usage: Any) -> float:
    """Put the test ping's tokens and LiteLLM-priced cost in the usage ledger (never fails the test).

    Returns the priced cost (0.0 when pricing or the ledger write failed).
    """
    from datetime import UTC, datetime

    from src.domain.interfaces.usage_ledger import UsageRecord
    from src.infrastructure.llm.pricing import call_cost
    from src.transport.http.fastapi.dependencies import get_usage_ledger

    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    cost = 0.0
    try:
        cost = call_cost(model.litellm_model, input_tokens, output_tokens, response=response)
        await get_usage_ledger().record_usage(
            UsageRecord(
                timestamp=datetime.now(UTC),
                provider=model.provider,
                operation="model_test",
                model=model.model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                cost_usd=cost,
                cache_hit=False,
            )
        )
    except Exception:  # a ledger failure must not fail the test
        pass
    return cost or 0.0


# --- fallback chain ----------------------------------------------------------------------


def _fallback(registry: ModelRegistry) -> FallbackResponse:
    keys = registry.repo.list_keys()
    entries: list[FallbackEntryOut] = []
    for model in registry.repo.list_models():
        own = [k for k in keys if k.provider == model.provider]
        usable, blocked = registry.usable_keys(model)
        entries.append(
            FallbackEntryOut(
                **_model_out(model).model_dump(),
                key_count=len(own),
                enabled_key_count=sum(1 for k in own if k.enabled),
                usable_key_count=len(usable),
                blocked=None if usable else (blocked or "no_keys"),
            )
        )
    return FallbackResponse(entries=entries)


@router.get("/fallback", response_model=FallbackResponse)
def get_fallback() -> FallbackResponse:
    """The whole registry in chain order (models outside the chain last), with key availability."""
    return _fallback(_registry())


@router.put("/fallback", response_model=FallbackResponse)
def put_fallback(body: ChainUpdate) -> FallbackResponse:
    """Replace priority and enabled flag of the listed models (unknown ids are skipped)."""
    registry = _registry()
    registry.repo.replace_chain([(e.model_id, e.priority, e.enabled) for e in body.entries])
    return _fallback(registry)


@router.get("/fallback/rate-limit-usage", response_model=RateLimitUsageResponse)
def rate_limit_usage() -> RateLimitUsageResponse:
    return RateLimitUsageResponse(rows=_registry().rate_limit_usage())


@router.get("/fallback/token-usage")
def token_usage() -> dict[str, Any]:
    return _registry().token_usage()


# --- keys --------------------------------------------------------------------------------


def _get_key(registry: ModelRegistry, key_pk: int) -> KeyRow:
    key = registry.repo.get_key(key_pk)
    if key is None:
        raise _not_found("key", key_pk)
    return key


def _cap(value: int | None) -> int | None:
    return value or None  # 0 means unlimited


@router.get("/keys", response_model=KeysResponse)
def list_keys() -> KeysResponse:
    registry = _registry()
    return KeysResponse(keys=_keys_out(registry, registry.repo.list_keys()))


@router.post("/keys/check-all", response_model=KeysResponse)
async def check_all_keys() -> KeysResponse:
    registry = _registry()
    keys = await registry.check_all()
    return KeysResponse(keys=_keys_out(registry, keys))


@router.post("/keys", response_model=KeyOut, status_code=201)
def create_key(body: KeyCreate) -> KeyOut:
    registry = _registry()
    provider = body.provider.strip().lower()
    value = body.key.strip()
    if not value:
        raise HTTPError(status_code=422, detail="key cannot be blank.", error_code="VALIDATION_ERROR")
    label = (body.label or "").strip()
    if not label:
        count = len(registry.repo.list_keys(provider))
        label = f"{provider_info(provider).label} key {count + 1}"
    key = registry.repo.add_key(
        provider=provider,
        label=label,
        key_value=value,
        monthly_request_cap=_cap(body.monthly_request_cap),
        monthly_token_cap=_cap(body.monthly_token_cap),
    )
    return _key_out(key, [])


@router.patch("/keys/{key_pk}", response_model=KeyOut)
def update_key(key_pk: int, body: KeyUpdate) -> KeyOut:
    registry = _registry()
    _get_key(registry, key_pk)
    raw = body.model_dump(exclude_unset=True)
    changes: dict[str, Any] = {}
    for name, value in raw.items():
        if name == "key":
            if value is None or not value.strip():
                raise HTTPError(
                    status_code=422, detail="key cannot be blank.", error_code="VALIDATION_ERROR"
                )
            changes["key_value"] = value.strip()
        elif name in ("label", "enabled"):
            if value is None:
                raise HTTPError(
                    status_code=422,
                    detail=f"{name} cannot be null.",
                    error_code="VALIDATION_ERROR",
                )
            changes[name] = value.strip() if name == "label" else value
        else:
            changes[name] = _cap(value)
    key = registry.repo.update_key(key_pk, changes)
    assert key is not None
    return _keys_out(registry, [key])[0]


@router.delete("/keys/{key_pk}", status_code=204)
def delete_key(key_pk: int) -> Response:
    if not _registry().repo.delete_key(key_pk):
        raise _not_found("key", key_pk)
    return Response(status_code=204)


@router.post("/keys/{key_pk}/reveal", response_model=KeyReveal)
def reveal_key(key_pk: int) -> KeyReveal:
    """The one place a full key leaves the server (single-user local app; never logged)."""
    return KeyReveal(key=_get_key(_registry(), key_pk).key_value)


@router.delete("/keys/{key_pk}/cooldowns", response_model=ClearedResponse)
def clear_cooldowns(key_pk: int) -> ClearedResponse:
    registry = _registry()
    _get_key(registry, key_pk)
    return ClearedResponse(cleared=registry.repo.clear_cooldowns(key_pk))


@router.post("/keys/{key_pk}/check", response_model=KeyOut)
async def check_key(key_pk: int) -> KeyOut:
    registry = _registry()
    await asyncio.to_thread(_get_key, registry, key_pk)
    key = await registry.check_key(key_pk)
    return _keys_out(registry, [key])[0]


__all__ = ["router"]

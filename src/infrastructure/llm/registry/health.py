"""Provider-side key checks (on demand) and model listing.

Blocking ``urllib`` calls run in worker threads (no extra dependency). A key value never
appears in a URL or in any message raised from here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request

from src.infrastructure.llm.registry.providers import provider_info

logger = logging.getLogger(__name__)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_TIMEOUT_SECONDS = 15.0


class ProviderQueryError(Exception):
    """A provider call failed. ``message`` is safe to show: it never contains the key."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _http_get(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
    """``(status, body)``. HTTP error statuses are returned; transport errors raise ``OSError``."""
    request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _request_for(
    provider: str, key_value: str, api_base: str | None, *, probe: bool
) -> tuple[str, dict[str, str]]:
    info = provider_info(provider)
    base = (api_base or info.default_api_base or "").rstrip("/")
    if info.health == "gemini":
        return f"{GEMINI_BASE}/models", {"x-goog-api-key": key_value}
    if not base:
        raise ProviderQueryError(f"No endpoint is configured for {info.label}.")
    path = "/auth/key" if probe and info.health == "openrouter" else "/models"
    return f"{base}{path}", {"Authorization": f"Bearer {key_value}"}


def _clean(text: str, key_value: str) -> str:
    return text.replace(key_value, "***") if key_value else text


async def fetch_provider_models(provider: str, key_value: str, api_base: str | None) -> list[str]:
    """The model ids the provider offers for this key."""
    url, headers = _request_for(provider, key_value, api_base, probe=False)
    try:
        status, body = await asyncio.to_thread(_http_get, url, headers)
    except (OSError, ValueError) as exc:
        raise ProviderQueryError(
            f"Could not reach {provider_info(provider).label}: {_clean(str(exc), key_value)}"
        ) from exc
    if status != 200:
        raise ProviderQueryError(
            f"{provider_info(provider).label} answered HTTP {status} to the model list request.",
            status,
        )
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise ProviderQueryError(
            f"{provider_info(provider).label} returned an unreadable model list.", status
        ) from exc
    ids: set[str] = set()
    for item in payload.get("data") or payload.get("models") or []:
        raw = item.get("id") or item.get("name") if isinstance(item, dict) else None
        if raw:
            ids.add(str(raw).removeprefix("models/"))
    return sorted(ids)


async def probe_key(provider: str, key_value: str, api_base: str | None) -> tuple[str, str | None]:
    """``(status, error)``: healthy, invalid, rate_limited or error (transport; not the key's fault)."""
    label = provider_info(provider).label
    try:
        url, headers = _request_for(provider, key_value, api_base, probe=True)
        status, _ = await asyncio.to_thread(_http_get, url, headers)
    except ProviderQueryError as exc:
        return "error", exc.message
    except (OSError, ValueError) as exc:
        return "error", f"Could not reach {label}: {_clean(str(exc), key_value)}"
    if 200 <= status < 300:
        return "healthy", None
    if status in (401, 403):
        return "invalid", f"{label} rejected the key (HTTP {status})."
    if status == 429:
        return "rate_limited", f"{label} is rate-limiting the key check (HTTP 429)."
    return "error", f"{label} answered the key check with HTTP {status}."


__all__ = ["ProviderQueryError", "fetch_provider_models", "probe_key"]

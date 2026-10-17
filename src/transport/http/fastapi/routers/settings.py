from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from src.config import AtlasSettings, SettingsError, load_settings
from src.config.editable import (
    EDITABLE_FIELDS,
    FIELDS_BY_KEY,
    GROUPS,
    NOT_STORABLE,
    default_values,
    is_editable,
)
from src.config.settings import SettingsLayers, check_value, field_kind, read_layers
from src.domain.exceptions import NotesGeneratorConfigError
from src.infrastructure.llm.models import GEMINI_MODEL_CHOICES
from src.infrastructure.llm.notes_base import NotesPromptSource
from src.infrastructure.storage.settings_repo import SqlSettingsRepository
from src.shared.api_keys import read_api_keys
from src.transport.http.fastapi.dependencies import (
    apply_settings,
    get_boot_settings,
    get_settings_loader,
    get_settings_repo,
)
from src.transport.http.fastapi.errors import HTTPError
from src.transport.http.fastapi.schemas.playlists import NotesPromptResponse
from src.transport.http.fastapi.schemas.settings import (
    ModelListResponse,
    SettingField,
    SettingGroup,
    SettingsResponse,
    SettingsUpdateRequest,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

RepoDep = Annotated[SqlSettingsRepository, Depends(get_settings_repo)]


def _baseline(layers: SettingsLayers) -> dict[str, Any]:
    """Values with no saved change: the dataclass defaults overlaid with config.yaml."""
    return {**default_values(), **layers.yaml}


def _source(key: str, layers: SettingsLayers) -> str:
    if key in layers.env:
        return "env"
    if key in layers.db and key not in NOT_STORABLE:
        return "db"
    return "yaml" if key in layers.yaml else "default"


def _snapshot() -> SettingsResponse:
    layers = read_layers()
    effective = load_settings()
    baseline = _baseline(layers)
    boot = get_boot_settings()
    fields: list[SettingField] = []
    restart_required = False
    for spec in EDITABLE_FIELDS:
        value = getattr(effective, spec.key)
        if spec.apply == "restart" and getattr(boot, spec.key) != value:
            restart_required = True
        fields.append(
            SettingField(
                key=spec.key,
                group=spec.group,
                label=spec.label,
                help=spec.help,
                kind=field_kind(spec.key),
                unit=spec.unit,
                choices=list(spec.choices),
                minimum=spec.minimum,
                apply=spec.apply,
                editable=is_editable(spec.key),
                value=value,
                default=baseline[spec.key],
                source=_source(spec.key, layers),
                locked=spec.key in layers.env,
            )
        )
    return SettingsResponse(
        groups=[SettingGroup(id=gid, label=label) for gid, label in GROUPS],
        fields=fields,
        restart_required=restart_required,
    )


def _validation_error(fields: dict[str, str], general: list[str]) -> HTTPError:
    first = next(iter(fields.values()), None) or (general[0] if general else "Invalid settings")
    return HTTPError(
        status_code=422,
        detail=f"Settings were not saved: {first}",
        error_code="VALIDATION_ERROR",
        details={"fields": fields, "general": general},
    )


def _problem_key(problem: str) -> str | None:
    """``AtlasSettings.problems()`` lines start with the field name (``name: rule``)."""
    name = problem.split(":", 1)[0].strip()
    return name if name in FIELDS_BY_KEY else None


def _check_request(values: dict[str, Any], layers: SettingsLayers) -> dict[str, str]:
    """Per-field reasons a requested value cannot be saved (empty when all are acceptable)."""
    errors: dict[str, str] = {}
    for key, value in values.items():
        if not is_editable(key):
            errors[key] = "This setting cannot be changed here."
        elif key in layers.env:
            errors[key] = "Set by an environment variable, which takes precedence. Unset it first."
        else:
            problem = check_value(key, value)
            if problem:
                errors[key] = problem.split(":", 1)[1].strip()
    return errors


def _save(repo: SqlSettingsRepository, values: dict[str, Any]) -> None:
    layers = read_layers()
    errors = _check_request(values, layers)

    baseline = _baseline(layers)
    stored = {k: v for k, v in layers.db.items() if k not in NOT_STORABLE}
    for key, value in values.items():
        if key in errors:
            continue  # reported below; the rest are still checked against each other
        value = float(value) if field_kind(key) == "float" else value
        if value == baseline[key]:
            stored.pop(key, None)  # same as config.yaml/default: no row needed
        else:
            stored[key] = value

    candidate_problems = AtlasSettings(**layers.merged(stored)).problems()
    fields: dict[str, str] = dict(errors)
    general: list[str] = []
    for problem in candidate_problems:
        key = _problem_key(problem)
        if key is None:
            general.append(problem)
        else:
            fields.setdefault(key, problem.split(":", 1)[1].strip())
    if fields or general:
        raise _validation_error(fields, general)

    repo.replace(stored)


def _reload_and_apply() -> None:
    try:
        apply_settings(load_settings())
    except SettingsError as exc:  # cannot happen right after validation; never leave a 500
        raise HTTPError(status_code=500, detail=str(exc), error_code="SETTINGS_INVALID") from exc


@router.get("", response_model=SettingsResponse)
async def get_settings() -> SettingsResponse:
    """Every editable setting with its effective value, source and whether it applies live."""
    return await asyncio.to_thread(_snapshot)


def _fetch_openai_models(label: str, base: str, key_env: str) -> list[str]:
    """Model ids from an OpenAI-compatible ``GET <base>/models`` (sorted, de-duplicated)."""
    base = base.strip().rstrip("/")
    keys = read_api_keys(key_env)
    if not keys:
        raise HTTPError(
            status_code=400,
            detail=f"{key_env} is not set. Add the {label} API key to .env to list its models.",
            error_code="CONFIG_MISSING",
        )
    request = urllib.request.Request(  # noqa: S310 - URL comes from the operator's own settings
        f"{base}/models", headers={"Authorization": f"Bearer {keys[0]}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise HTTPError(
            status_code=502,
            detail=f"{label} answered {exc.code} for {base}/models.",
            error_code="PROVIDER_ERROR",
        ) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HTTPError(
            status_code=502,
            detail=f"Could not reach {label} at {base}: {getattr(exc, 'reason', exc)}",
            error_code="PROVIDER_ERROR",
        ) from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    ids = {m["id"] for m in data or [] if isinstance(m, dict) and isinstance(m.get("id"), str)}
    return sorted(ids)


def _fetch_freellmapi_models() -> list[str]:
    settings = load_settings()
    return _fetch_openai_models(
        "FreeLLMAPI",
        settings.freellmapi_models_url or settings.freellmapi_base_url,
        settings.freellmapi_api_key_env,
    )


def _fetch_nvidia_models() -> list[str]:
    settings = load_settings()
    return _fetch_openai_models("NVIDIA", settings.nvidia_base_url, settings.nvidia_api_key_env)


@router.get("/models", response_model=ModelListResponse)
async def list_models(provider: str) -> ModelListResponse:
    """The models selectable for ``provider``: a fixed Gemini list, or the live list of FreeLLMAPI or NVIDIA."""
    if provider == "gemini":
        return ModelListResponse(provider=provider, models=list(GEMINI_MODEL_CHOICES))
    if provider == "freellmapi":
        models = await asyncio.to_thread(_fetch_freellmapi_models)
        return ModelListResponse(provider=provider, models=models)
    if provider == "nvidia":
        models = await asyncio.to_thread(_fetch_nvidia_models)
        return ModelListResponse(provider=provider, models=models)
    raise HTTPError(
        status_code=404,
        detail=f"No model list for provider {provider!r}.",
        error_code="NOT_FOUND",
    )


def _read_notes_prompt() -> NotesPromptResponse:
    source = NotesPromptSource(get_settings_loader())
    try:
        return NotesPromptResponse(prompt=source.template(), version=source.version())
    except NotesGeneratorConfigError as exc:
        raise HTTPError(
            status_code=500, detail=str(exc), error_code="PROMPT_UNAVAILABLE"
        ) from exc


@router.get("/notes-prompt", response_model=NotesPromptResponse)
async def get_notes_prompt() -> NotesPromptResponse:
    """The prompt template the notes generator uses now (read fresh from the prompt file).

    The review screen shows it as the starting point of a temporary regenerate prompt.
    """
    return await asyncio.to_thread(_read_notes_prompt)


@router.put("", response_model=SettingsResponse)
async def update_settings(body: SettingsUpdateRequest, repo: RepoDep) -> SettingsResponse:
    """Validate and save the given settings, then apply the live ones to the running app.

    A value equal to config.yaml/default removes its saved row. Nothing is written unless every
    value is valid (422 ``VALIDATION_ERROR``, ``details.fields`` names each bad setting).
    """

    await asyncio.to_thread(_save, repo, body.values)
    _reload_and_apply()  # on the event loop: it wakes the worker's asyncio.Event
    return await asyncio.to_thread(_snapshot)


@router.delete("", response_model=SettingsResponse)
async def reset_settings(repo: RepoDep) -> SettingsResponse:
    """Forget every saved change: values fall back to config.yaml and the defaults."""

    await asyncio.to_thread(repo.clear)
    _reload_and_apply()
    return await asyncio.to_thread(_snapshot)


__all__ = ["router"]

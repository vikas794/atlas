from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SettingGroup(BaseModel):
    id: str
    label: str


class SettingField(BaseModel):
    key: str
    group: str
    label: str
    help: str = ""
    #: ``int`` | ``float`` | ``bool`` | ``list`` | ``text``.
    kind: str
    unit: str = ""
    choices: list[str] = Field(default_factory=list)
    minimum: float | None = None
    #: ``live`` (applied on save) or ``restart``.
    apply: str
    #: False when the value can only be changed in config.yaml / the environment.
    editable: bool
    value: Any
    #: What the value would be with no saved change (config.yaml, else the built-in default).
    default: Any
    #: Where the effective value comes from: ``default`` | ``yaml`` | ``db`` | ``env``.
    source: str
    #: True when an environment variable decides the value, so a saved change would be ignored.
    locked: bool


class SettingsResponse(BaseModel):
    groups: list[SettingGroup]
    fields: list[SettingField]
    #: True when a saved restart-only setting differs from what the running process uses.
    restart_required: bool


class SettingsUpdateRequest(BaseModel):
    """Setting values to save, keyed by setting name."""

    values: dict[str, Any]


class ModelListResponse(BaseModel):
    """Model ids a provider offers, for the notes-model dropdown."""

    provider: str
    models: list[str]


__all__ = [
    "ModelListResponse",
    "SettingField",
    "SettingGroup",
    "SettingsResponse",
    "SettingsUpdateRequest",
]

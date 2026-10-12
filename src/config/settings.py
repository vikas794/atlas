from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Protocol

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Sentinel that distinguishes "resolved to None" from "did not resolve at all".
_MISSING: Any = object()


def repo_root() -> Path:
    """Repository root, derived from this file's location (CWD-independent)."""
    return Path(__file__).resolve().parents[2]


# The YAML config lives beside the sources, NOT at the repository root.
# load_settings() used to look for <repo_root>/config.yaml, which does not exist,
# so the file was silently skipped and every value fell back to its dataclass
# default. Keep this in sync with src/configs/config.yaml.
DEFAULT_CONFIG_RELPATH = Path("src") / "configs" / "config.yaml"


class SettingsLoader(Protocol):
    """Protocol for loading configuration values by key path.

    Implementations can read from YAML, environment variables, or both.
    Keys are AtlasSettings field names (flat). Dotted paths are still accepted
    for legacy callers and are walked as attribute/dict lookups.
    """

    def __call__(self, key_path: str, default: Any = None) -> Any:
        ...


@dataclass(frozen=True)
class AtlasSettings:
    """Application settings with environment variable overrides.

    The YAML file is a FLAT mapping whose keys are exactly the field names below
    (``AtlasSettings.from_dict`` drops anything it does not recognise, so a nested
    file is discarded wholesale).

    Environment overrides, highest precedence first:

    1. ``ATLAS_<FIELD_NAME>`` (e.g. ``ATLAS_MAX_CONCURRENT_VIDEOS``);
    2. the documented, unprefixed names in :data:`ENV_ALIASES`
       (``NOTES_PROVIDER``, ``GEMINI_MODEL``, ``OPENROUTER_MODEL`` ...), which is what
       ``.env.example`` lists;
    3. ``config.yaml``; then the defaults below.

    An **empty** value counts as unset (``KEY=`` in a ``.env`` file is the normal way to
    leave a setting at its default), and a malformed number is ignored with a warning
    instead of crashing startup.

    API keys are never stored here: ``*_api_key_env`` name the environment variables that
    hold them (``YOUTUBE_API_KEY``, ``GEMINI_API_KEY``, ``OPENROUTER_API_KEY``).
    """

    # --- Storage ---------------------------------------------------------
    database_path: str = "data/atlas.sqlite3"

    # --- Secrets (names of the env vars that hold keys, never the keys) ----
    openrouter_api_key_env: str = "OPENROUTER_API_KEY"
    youtube_api_key_env: str = "YOUTUBE_API_KEY"
    gemini_api_key_env: str = "GEMINI_API_KEY"

    # --- Google OAuth ----------------------------------------------------
    google_creds_path: str = "credentials.json"
    google_token_path: str = "token.json"
    drive_max_retries: int = 3
    drive_backoff_base_seconds: float = 1.0
    drive_backoff_cap_seconds: float = 10.0

    # --- Transcripts -----------------------------------------------------
    transcript_language: str = "en"
    transcript_output_folder: str = "transcripts"

    # --- Notes generation (playlist -> per-video Markdown notes) ----------
    notes_provider: str = "gemini"  # gemini | openrouter
    notes_model: str = ""  # empty -> the provider's own default model
    gemini_model: str = "gemini-2.5-flash"
    gemini_input_token_limit: int = 1000000
    openrouter_model: str = ""  # used when notes_model is empty
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_input_token_limit: int = 128000
    notes_prompt_name: str = "notes_v1_1.yaml"  # the prompt file's own `version` is recorded

    # --- Pipeline concurrency & retries ----------------------------------
    max_concurrent_videos: int = 2
    max_auto_retries: int = 4
    backoff_base_seconds: float = 5.0
    backoff_cap_seconds: float = 300.0
    worker_poll_interval_seconds: float = 2.0
    worker_shutdown_timeout_seconds: float = 5.0

    # --- Paths -----------------------------------------------------------
    prompts_base_dir: str = "src/prompts"

    # --- Logging ---------------------------------------------------------
    log_level: str = "INFO"
    log_format: str = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    log_file_logging: bool = False
    log_file: str = "logs/atlas.log"

    # --- Development -----------------------------------------------------
    dev_shutdown_token: str | None = None

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> AtlasSettings:
        """Load settings from YAML file."""
        data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AtlasSettings:
        """Create settings from a flat dict, ignoring unknown keys."""
        valid_keys = {f.name for f in fields(cls)}
        unknown = sorted(k for k in data if k not in valid_keys)
        if unknown:
            logger.debug(
                "Ignoring %d unknown key(s) in settings mapping: %s", len(unknown), ", ".join(unknown)
            )
        return cls(**{k: v for k, v in data.items() if k in valid_keys})

    def to_loader(self) -> SettingsLoader:
        """Return a SettingsLoader that reads from this instance + env overrides."""
        return _AtlasSettingsLoader(self)


#: Documented, unprefixed environment variable names -> AtlasSettings field names. These are
#: the names ``.env.example`` lists; ``ATLAS_<FIELD>`` still wins when both are set.
ENV_ALIASES: dict[str, str] = {
    "NOTES_PROVIDER": "notes_provider",
    "NOTES_MODEL": "notes_model",
    "NOTES_PROMPT_NAME": "notes_prompt_name",
    "GEMINI_MODEL": "gemini_model",
    "GEMINI_INPUT_TOKEN_LIMIT": "gemini_input_token_limit",
    "OPENROUTER_MODEL": "openrouter_model",
    "OPENROUTER_BASE_URL": "openrouter_base_url",
    "OPENROUTER_INPUT_TOKEN_LIMIT": "openrouter_input_token_limit",
}

# Older ``ATLAS_*`` names that predate the field names and must keep working
# (e.g. ATLAS_DB_PATH -> database_path).
_LEGACY_ENV_ALIASES = {
    "db_path": "database_path",
}

_FIELD_TYPES: dict[str, str] = {f.name: str(f.type).replace(" ", "") for f in fields(AtlasSettings)}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _env_names_for(field_name: str) -> list[str]:
    """Environment variable names that can set ``field_name``, highest precedence first."""
    names = [f"ATLAS_{field_name.upper()}"]
    names += [f"ATLAS_{legacy.upper()}" for legacy, name in _LEGACY_ENV_ALIASES.items() if name == field_name]
    names += [alias for alias, name in ENV_ALIASES.items() if name == field_name]
    return names


def _coerce_field(field_name: str, raw: str) -> Any:
    """Convert ``raw`` to the field's declared type. Raises ValueError when it does not fit."""
    declared = _FIELD_TYPES.get(field_name, "str")
    text = raw.strip()
    if declared.startswith("bool"):
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ValueError(f"not a boolean: {raw!r}")
    if declared.startswith("int"):
        return int(text)
    if declared.startswith("float"):
        return float(text)
    return text


def _env_override(field_name: str) -> Any:
    """The environment value for ``field_name`` coerced to its type, or ``_MISSING``.

    Empty values are treated as unset, and a value that cannot be coerced is skipped
    with a warning (the setting keeps its configured/default value).
    """
    for name in _env_names_for(field_name):
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            continue
        try:
            return _coerce_field(field_name, raw)
        except ValueError:
            logger.warning("Ignoring %s=%r: expected %s", name, raw, _FIELD_TYPES[field_name])
    return _MISSING


def _coerce_env_value(raw: str) -> Any:
    """Parse an env var as JSON when possible, else return the raw string (non-field keys)."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


class _AtlasSettingsLoader:
    """SettingsLoader implementation that reads from AtlasSettings + env vars."""

    def __init__(self, settings: AtlasSettings) -> None:
        self._settings = settings
        self._flat: dict[str, Any] = {f.name: getattr(settings, f.name) for f in fields(settings)}

    def __call__(self, key_path: str, default: Any = None) -> Any:
        # 1. Environment override always wins (empty values count as unset).
        if key_path in _FIELD_TYPES:
            override = _env_override(key_path)
            if override is not _MISSING:
                return override
        else:
            raw = os.environ.get(f"ATLAS_{key_path.upper().replace('.', '_')}")
            if raw is not None and raw.strip():
                return _coerce_env_value(raw)

        # 2. Exact match against the flat settings map. This is the only form
        #    the YAML file uses, so a plain key must never reach the dot-walk
        #    below (splitting on "." used to guarantee a silent miss).
        if key_path in self._flat:
            return self._flat[key_path]

        # 3. Legacy dotted paths: walk nested dicts / attributes.
        value: Any = self._settings
        try:
            for key in key_path.split("."):
                if isinstance(value, dict):
                    value = value[key]
                else:
                    value = getattr(value, key)
        except (KeyError, AttributeError, TypeError):
            value = _MISSING
        if value is not _MISSING:
            return value

        # 4. Nothing resolved. Say so: the silence here is what hid the
        #    never-loaded config.yaml and the discarded nested mapping.
        logger.debug(
            "settings key %r not found in AtlasSettings or env; using default %r", key_path, default
        )
        return default


def load_settings(config_path: str | Path | None = None) -> AtlasSettings:
    """Load settings from config.yaml with environment variable overrides.

    ``.env`` is loaded first (without overriding real environment variables) so
    that secrets and overrides defined there are visible to the env scan below.
    When ``config_path`` is None the YAML is read from
    ``<repo_root>/src/configs/config.yaml``, resolved from this module's location
    so the result does not depend on the current working directory.
    """
    load_dotenv(repo_root() / ".env", override=False)

    if config_path is None:
        config_path = repo_root() / DEFAULT_CONFIG_RELPATH

    if Path(config_path).exists():
        settings = AtlasSettings.from_yaml(config_path)
        logger.debug("Loaded settings from %s", config_path)
    else:
        logger.debug(
            "Config file %s not found; falling back to AtlasSettings defaults", config_path
        )
        settings = AtlasSettings()

    env_overrides: dict[str, Any] = {}
    for field_name in _FIELD_TYPES:
        value = _env_override(field_name)
        if value is not _MISSING:
            env_overrides[field_name] = value

    if env_overrides:
        current = {f.name: getattr(settings, f.name) for f in fields(AtlasSettings)}
        current.update(env_overrides)
        settings = AtlasSettings(**current)
        logger.debug("Applied env overrides: %s", ", ".join(sorted(env_overrides)))

    return settings


def get_storage_settings(settings: AtlasSettings | None = None) -> dict[str, Any]:
    """Return effective storage configuration (env overrides config.yaml).

    Relative paths resolve against the repository root, never the CWD.
    """
    if settings is None:
        settings = load_settings()

    root = repo_root()

    db_path = settings.database_path
    transcripts_dir = settings.transcript_output_folder
    if not os.path.isabs(db_path):
        db_path = str(root / db_path)
    if not os.path.isabs(transcripts_dir):
        transcripts_dir = str(root / transcripts_dir)

    return {"database_path": db_path, "transcripts_dir": transcripts_dir}

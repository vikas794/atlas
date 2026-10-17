from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Protocol

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

NOTES_PROVIDERS = ("gemini", "openrouter", "omniroute")
#: Keys an entry of ``notes_models`` may carry (see ``AtlasSettings.notes_models``).
NOTES_MODEL_KEYS = ("model", "api_key_env", "api_base", "input_token_limit")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class SettingsError(ValueError):
    """The configuration is invalid. ``str()`` is a message meant for a human, not a dump."""

    def __init__(self, problems: list[str], sources: str) -> None:
        self.problems = problems
        lines = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__(
            f"Atlas cannot start: invalid configuration ({sources}).\n{lines}\n"
            "Fix the values above in src/configs/config.yaml (settings) or .env (keys and "
            "ATLAS_* overrides), then start again."
        )


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
    omniroute_api_key_env: str = "OMNIROUTE_API_KEY"

    # --- Google OAuth ----------------------------------------------------
    google_creds_path: str = "credentials.json"
    google_token_path: str = "token.json"
    drive_max_retries: int = 3
    drive_backoff_base_seconds: float = 1.0
    drive_backoff_cap_seconds: float = 10.0

    # --- Transcripts -----------------------------------------------------
    transcript_language: str = "en"
    transcript_output_folder: str = "transcripts"
    #: Minimum gap between two YouTube transcript fetches (they run one at a time); it is also
    #: yt-dlp's pause between subtitle requests. Spreads load so YouTube does not throttle.
    transcript_min_interval_seconds: float = 4.0
    #: Optional logged-in cookies for yt-dlp (far fewer throttles/bot checks). Either a
    #: Netscape cookies.txt path or a browser name (chrome, firefox, edge, ...). Use a
    #: throwaway Google account. Empty = anonymous.
    ytdlp_cookies_file: str = ""
    ytdlp_cookies_from_browser: str = ""

    # --- Notes generation (playlist -> per-video Markdown notes) ----------
    #: Ordered LiteLLM models, tried first to last (the rest are fallbacks). Each entry is a
    #: mapping with ``model`` ("<litellm-provider>/<model>", e.g. "gemini/gemini-3.5-flash") and
    #: optional ``api_key_env``, ``api_base`` (OpenAI-compatible gateways such as OmniRoute) and
    #: ``input_token_limit``. Empty -> derived from the legacy ``notes_provider`` settings below.
    notes_models: list[dict[str, Any]] = field(default_factory=list)
    notes_provider: str = "gemini"  # legacy single-provider shorthand: gemini | openrouter
    notes_model: str = ""  # empty -> the provider's own default model
    gemini_model: str = "gemini-3.5-flash"
    gemini_input_token_limit: int = 1000000
    openrouter_model: str = ""  # used when notes_model is empty
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_input_token_limit: int = 128000
    #: OmniRoute: an OpenAI-compatible gateway. Notes go to ``omniroute_base_url``; the model
    #: list for the UI is read from ``omniroute_models_url`` (empty -> the base URL).
    omniroute_model: str = ""
    omniroute_base_url: str = "http://localhost:20128/v1"
    omniroute_models_url: str = ""
    omniroute_input_token_limit: int = 128000
    notes_prompt_name: str = "notes_v1_2.yaml"  # the prompt file's own `version` is recorded
    notes_request_timeout_seconds: float = 300.0  # a hung LLM call must not hold a slot forever

    # --- Pipeline concurrency & retries ----------------------------------
    max_concurrent_videos: int = 2
    max_auto_retries: int = 4
    backoff_base_seconds: float = 5.0
    backoff_cap_seconds: float = 300.0
    #: YouTube throttles last minutes, so a rate-limited transcript fetch backs off longer and
    #: gets more automatic retries than other transient failures.
    rate_limit_max_auto_retries: int = 6
    rate_limit_backoff_base_seconds: float = 60.0
    rate_limit_backoff_cap_seconds: float = 900.0
    worker_poll_interval_seconds: float = 2.0
    worker_shutdown_timeout_seconds: float = 5.0

    # --- Paths -----------------------------------------------------------
    prompts_base_dir: str = "src/prompts"

    # --- Logging ---------------------------------------------------------
    log_level: str = "INFO"
    log_format: str = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    log_file_logging: bool = True
    log_file: str = "logs/atlas.log"

    # --- HTTP ------------------------------------------------------------
    #: Interface and port the server binds to (``python -m backend``). Keep 127.0.0.1:
    #: Atlas has no authentication.
    host: str = "127.0.0.1"
    port: int = 8000
    #: Comma-separated browser origins allowed to call the API cross-origin. Empty (the
    #: default) disables CORS: single-app mode is same-origin and the Vite dev server proxies
    #: /api, so neither needs it.
    cors_origins: str = ""

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

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def _notes_models_problems(self) -> list[str]:
        found: list[str] = []
        for index, entry in enumerate(self.notes_models, start=1):
            where = f"notes_models[{index}]"
            if not isinstance(entry, dict):
                found.append(f"{where}: must be a mapping with at least a 'model' key")
                continue
            unknown = sorted(set(entry) - set(NOTES_MODEL_KEYS))
            if unknown:
                found.append(
                    f"{where}: unknown key(s) {', '.join(unknown)} "
                    f"(allowed: {', '.join(NOTES_MODEL_KEYS)})"
                )
            model = entry.get("model")
            if not isinstance(model, str) or "/" not in model.strip("/ "):
                found.append(
                    f"{where}.model: must look like '<provider>/<model>', e.g. "
                    f"'gemini/gemini-3.5-flash' (got {model!r})"
                )
            for key in ("api_key_env", "api_base"):
                if key in entry and not isinstance(entry[key], str):
                    found.append(f"{where}.{key}: must be text (got {entry[key]!r})")
            limit = entry.get("input_token_limit")
            if limit is not None and (
                isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
            ):
                found.append(f"{where}.input_token_limit: must be a whole number of at least 1")
        return found

    def problems(self) -> list[str]:
        """Human-readable reasons this configuration cannot run (empty when it is valid)."""
        found: list[str] = []

        def need(ok: bool, name: str, rule: str) -> None:
            if not ok:
                found.append(f"{name}: {rule} (got {getattr(self, name)!r})")

        for name in (
            "database_path", "google_creds_path", "google_token_path", "transcript_output_folder",
            "prompts_base_dir", "notes_prompt_name", "log_file", "transcript_language",
            "gemini_api_key_env", "youtube_api_key_env", "openrouter_api_key_env",
            "omniroute_api_key_env",
        ):  # fmt: skip
            need(bool(str(getattr(self, name)).strip()), name, "must not be empty")
        need(self.notes_provider in NOTES_PROVIDERS, "notes_provider",
             f"must be one of {', '.join(NOTES_PROVIDERS)}")  # fmt: skip
        need(str(self.log_level).upper() in LOG_LEVELS, "log_level",
             f"must be one of {', '.join(LOG_LEVELS)}")  # fmt: skip
        need(bool(self.host.strip()), "host", "must not be empty")
        need(1 <= self.port <= 65535, "port", "must be between 1 and 65535")
        need(self.max_concurrent_videos >= 1, "max_concurrent_videos", "must be at least 1")
        for name in ("max_auto_retries", "drive_max_retries", "rate_limit_max_auto_retries"):
            need(getattr(self, name) >= 0, name, "must be 0 or more")
        for name in (
            "backoff_base_seconds", "drive_backoff_base_seconds", "worker_shutdown_timeout_seconds",
            "rate_limit_backoff_base_seconds", "transcript_min_interval_seconds",
        ):  # fmt: skip
            need(getattr(self, name) >= 0, name, "must be 0 or more")
        need(self.rate_limit_backoff_cap_seconds >= self.rate_limit_backoff_base_seconds,
             "rate_limit_backoff_cap_seconds",
             "must be at least rate_limit_backoff_base_seconds")  # fmt: skip
        need(self.backoff_cap_seconds >= self.backoff_base_seconds, "backoff_cap_seconds",
             "must be at least backoff_base_seconds")  # fmt: skip
        need(self.drive_backoff_cap_seconds >= self.drive_backoff_base_seconds,
             "drive_backoff_cap_seconds", "must be at least drive_backoff_base_seconds")  # fmt: skip
        need(self.notes_request_timeout_seconds > 0, "notes_request_timeout_seconds",
             "must be greater than 0")  # fmt: skip
        need(self.worker_poll_interval_seconds > 0, "worker_poll_interval_seconds",
             "must be greater than 0")  # fmt: skip
        for name in (
            "gemini_input_token_limit", "openrouter_input_token_limit",
            "omniroute_input_token_limit",
        ):  # fmt: skip
            need(getattr(self, name) >= 1, name, "must be at least 1")
        found.extend(self._notes_models_problems())
        prompts_dir = Path(self.prompts_base_dir)
        if not prompts_dir.is_absolute():
            prompts_dir = repo_root() / prompts_dir
        prompt = prompts_dir / self.notes_prompt_name
        if self.notes_prompt_name.strip() and not prompt.is_file():
            found.append(
                f"notes_prompt_name: prompt file {self.notes_prompt_name!r} not found in "
                f"{self.prompts_base_dir!r}"
            )
        for origin in self.cors_origin_list:
            if origin == "*" or not origin.startswith(("http://", "https://")):
                found.append(
                    f"cors_origins: {origin!r} is not an origin like http://localhost:5173 "
                    "(wildcards are not allowed)"
                )
        return found


#: Documented, unprefixed environment variable names -> AtlasSettings field names. These are
#: the names ``.env.example`` lists; ``ATLAS_<FIELD>`` still wins when both are set.
ENV_ALIASES: dict[str, str] = {
    "NOTES_MODELS": "notes_models",
    "NOTES_PROVIDER": "notes_provider",
    "NOTES_MODEL": "notes_model",
    "NOTES_PROMPT_NAME": "notes_prompt_name",
    "GEMINI_MODEL": "gemini_model",
    "GEMINI_INPUT_TOKEN_LIMIT": "gemini_input_token_limit",
    "OPENROUTER_MODEL": "openrouter_model",
    "OPENROUTER_BASE_URL": "openrouter_base_url",
    "OPENROUTER_INPUT_TOKEN_LIMIT": "openrouter_input_token_limit",
    "OMNIROUTE_MODEL": "omniroute_model",
    "OMNIROUTE_BASE_URL": "omniroute_base_url",
    "OMNIROUTE_MODELS_URL": "omniroute_models_url",
    "OMNIROUTE_INPUT_TOKEN_LIMIT": "omniroute_input_token_limit",
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
    if declared.startswith("list"):
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"not a JSON list: {raw!r}") from exc
        if not isinstance(value, list):
            raise ValueError(f"not a JSON list: {raw!r}")
        return value
    if declared.startswith("int"):
        return int(text)
    if declared.startswith("float"):
        return float(text)
    return text


def _env_override(field_name: str, problems: list[str] | None = None) -> Any:
    """The environment value for ``field_name`` coerced to its type, or ``_MISSING``.

    Empty values are treated as unset. A value that cannot be coerced is reported in
    ``problems`` when given (``load_settings`` then fails fast); otherwise it is skipped
    with a warning (runtime lookups through the loader never crash a running app).
    """
    for name in _env_names_for(field_name):
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            continue
        try:
            return _coerce_field(field_name, raw)
        except ValueError:
            expected = _FIELD_TYPES[field_name]
            if problems is not None:
                problems.append(f"{name}={raw!r} in the environment/.env: expected {expected}")
                return _MISSING
            logger.warning("Ignoring %s=%r: expected %s", name, raw, expected)
    return _MISSING


def _check_yaml_value(name: str, value: Any) -> str | None:
    """Why a YAML value does not fit the field's type, or None when it does."""
    declared = _FIELD_TYPES[name]
    if declared.startswith("bool"):
        ok = isinstance(value, bool)
        expected = "true or false"
    elif declared.startswith("list"):
        ok = isinstance(value, list)
        expected = "a list"
    elif declared.startswith("int"):
        ok = isinstance(value, int) and not isinstance(value, bool)
        expected = "a whole number"
    elif declared.startswith("float"):
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        expected = "a number"
    else:
        ok = isinstance(value, str)
        expected = "text (quote it)"
    return None if ok else f"{name}: expected {expected} (got {value!r})"


#: Public views of the field typing for the settings editor.
FIELD_TYPES = _FIELD_TYPES
check_value = _check_yaml_value


def field_kind(name: str) -> str:
    """``int`` | ``float`` | ``bool`` | ``list`` | ``text`` for a settings field."""
    declared = _FIELD_TYPES[name]
    for kind in ("bool", "list", "int", "float"):
        if declared.startswith(kind):
            return kind
    return "text"


def _read_yaml(config_path: Path, problems: list[str]) -> dict[str, Any]:
    """The valid entries of the YAML file; every unreadable/unknown/mistyped one is a problem."""
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        problems.append(f"{config_path.name} is not valid YAML: {exc}")
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        problems.append(f"{config_path.name} must be a flat mapping of setting: value")
        return {}
    valid: dict[str, Any] = {}
    for key, value in data.items():
        if key not in _FIELD_TYPES:
            problems.append(f"unknown setting {key!r} in {config_path.name} (misspelled or removed?)")
            continue
        problem = _check_yaml_value(key, value)
        if problem:
            problems.append(problem)
        else:
            valid[key] = float(value) if _FIELD_TYPES[key].startswith("float") else value
    return valid


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


@dataclass(frozen=True)
class SettingsLayers:
    """The configuration layers, lowest to highest precedence: yaml < db < env."""

    config_name: str
    yaml: dict[str, Any]
    db: dict[str, Any]
    env: dict[str, Any]
    problems: list[str]

    def merged(self, db: dict[str, Any] | None = None) -> dict[str, Any]:
        """Effective values; ``db`` substitutes the stored overrides (for what-if validation)."""
        return {**self.yaml, **(self.db if db is None else db), **self.env}


def _resolve_db_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else repo_root() / path


def _read_db_overrides(db_path: Path, problems: list[str]) -> dict[str, Any]:
    """UI-edited overrides from the ``app_settings`` table.

    Tolerant by design: no database file or no table yet (a fresh install, or a database that
    predates migration 0005) simply means no overrides. Settings must stay loadable even when
    the store is not, so this reads with plain ``sqlite3`` and never creates anything.
    """
    if not db_path.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        logger.warning("Could not open %s to read saved settings", db_path, exc_info=True)
        return {}
    try:
        rows = conn.execute("SELECT key, value_json FROM app_settings").fetchall()
    except sqlite3.Error:
        return {}  # table not created yet
    finally:
        conn.close()
    valid: dict[str, Any] = {}
    for key, raw in rows:
        if key not in _FIELD_TYPES:
            logger.warning("Ignoring unknown saved setting %r (removed in this version?)", key)
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            problems.append(f"saved setting {key}: stored value is not valid JSON ({raw!r})")
            continue
        problem = _check_yaml_value(key, value)
        if problem:
            problems.append(f"saved setting {problem}")
        else:
            valid[key] = float(value) if _FIELD_TYPES[key].startswith("float") else value
    return valid


def read_layers(config_path: str | Path | None = None) -> SettingsLayers:
    """Read every layer without merging or validating the result."""
    load_dotenv(repo_root() / ".env", override=False)

    if config_path is None:
        config_path = repo_root() / DEFAULT_CONFIG_RELPATH
    config_path = Path(config_path)

    problems: list[str] = []
    yaml_values: dict[str, Any] = {}
    if config_path.exists():
        yaml_values = _read_yaml(config_path, problems)
        logger.debug("Loaded settings from %s", config_path)
    else:
        logger.warning("Config file %s not found; using built-in defaults", config_path)

    env_values: dict[str, Any] = {}
    for field_name in _FIELD_TYPES:
        value = _env_override(field_name, problems)
        if value is not _MISSING:
            env_values[field_name] = value
    if env_values:
        logger.debug("Applied env overrides: %s", ", ".join(sorted(env_values)))

    # database_path decides where the saved settings live, so it can only come from env/yaml.
    db_path = _resolve_db_path(
        str({**yaml_values, **env_values}.get("database_path", AtlasSettings.database_path))
    )
    db_values = _read_db_overrides(db_path, problems)
    return SettingsLayers(config_path.name, yaml_values, db_values, env_values, problems)


def load_settings(config_path: str | Path | None = None) -> AtlasSettings:
    """Load settings: environment > saved (UI) settings > config.yaml > defaults.

    ``.env`` is loaded first (without overriding real environment variables) so
    that secrets and overrides defined there are visible to the env scan below.
    When ``config_path`` is None the YAML is read from
    ``<repo_root>/src/configs/config.yaml``, resolved from this module's location
    so the result does not depend on the current working directory.
    """
    layers = read_layers(config_path)
    problems = list(layers.problems)
    # ``database_path`` is restart-only and never read from the store it locates.
    saved = {k: v for k, v in layers.db.items() if k != "database_path"}
    settings = AtlasSettings(**layers.merged(saved))
    problems += settings.problems()
    if problems:
        raise SettingsError(problems, f"{layers.config_name}, saved settings, environment/.env")
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

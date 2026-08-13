from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml


class SettingsLoader(Protocol):
    """Protocol for loading configuration values by dot-notation key path.

    Implementations can read from YAML, environment variables, or both.
    """

    def __call__(self, key_path: str, default: Any = None) -> Any:
        ...


@dataclass(frozen=True)
class AtlasSettings:
    """Application settings with environment variable overrides.

    All fields can be overridden via environment variables prefixed with ATLAS_.
    """

    database_path: str = "data/atlas.sqlite3"
    artifact_root: str = "data/artifacts"
    cache_ttl_days: int = 30
    cleanup_retention_days: int = 90

    openrouter_api_key_env: str = "OPENROUTER_API_KEY"
    youtube_api_key_env: str = "YOUTUBE_API_KEY"
    gemini_api_key_env: str = "GEMINI_API_KEY"

    google_creds_path: str = "credentials.json"
    google_token_path: str = "token.json"

    dev_shutdown_token: str | None = None

    # API settings
    openai_model: str = "openai/gpt-5-mini"
    openai_timeout: int = 180
    openai_base_url: str = "https://openrouter.ai/api/v1"
    openai_max_retries: int = 3

    gemini_model: str = "gemini-3.6-flash"

    youtube_api_version: str = "v3"
    youtube_timeout: int = 30
    youtube_type: str = "video"
    youtube_order: str = "relevance"
    youtube_max_results: int = 5

    search_default_max_results: int = 10
    search_description_max_length: int = 200

    transcript_language: str = "en"
    transcript_output_folder: str = "transcripts"
    transcript_retry_wait_seconds: list[int] = field(default_factory=lambda: [15, 30, 60, 120])
    transcript_retry_jitter_seconds: float = 5.0
    transcript_min_delay_between_videos: float = 15.0
    transcript_rate_limit_cooldown_seconds: float = 120.0
    transcript_delay_between_requests: float = 4.0

    playlist_quiz_max_videos: int = 50
    playlist_quiz_max_retries: int = 3
    playlist_quiz_delay_between_requests: float = 2.0

    summarizer_prompt_version: str = "v1"
    summarizer_default_model: str = "openai/gpt-5-mini"
    summarizer_prompt_name: str = "summarizer_youtube_v2.yaml"

    insights_prompt_version: str = "v1"
    insights_prompt_name: str = "insights_provider.yaml"

    assignment_prompt_version: str = "v1"
    assignment_prompt_name: str = "assignment_generator.yaml"

    quiz_prompt_name: str = "quiz_generator.yaml"

    # Processing workers
    workers_auto_detect: bool = True
    workers_cpu_ratio: float = 0.5
    workers_min_workers: int = 2
    workers_max_workers: int = 16

    # Paths
    prompts_base_dir: str = "src/prompts"

    # Logging
    log_level: str = "INFO"
    log_format: str = "[%(levelname)s] %(message)s"
    log_file_logging: bool = False
    log_file: str = "logs/atlas.log"

    # Storage
    cache_ttl_days_override: int | None = None

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> AtlasSettings:
        """Load settings from YAML file."""
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AtlasSettings:
        """Create settings from dict, ignoring unknown keys."""
        import inspect
        valid_keys = {p.name for p in inspect.signature(cls).parameters.values() if p.name != "self"}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    def to_loader(self) -> SettingsLoader:
        """Return a SettingsLoader that reads from this instance + env overrides."""
        return _AtlasSettingsLoader(self)


class _AtlasSettingsLoader:
    """SettingsLoader implementation that reads from AtlasSettings + env vars."""

    def __init__(self, settings: AtlasSettings) -> None:
        self._settings = settings

    def __call__(self, key_path: str, default: Any = None) -> Any:
        # First check environment variable override
        env_key = f"ATLAS_{key_path.upper().replace('.', '_')}"
        if env_key in os.environ:
            value = os.environ[env_key]
            # Try to parse as JSON for complex types
            try:
                import json
                return json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return value

        # Fall back to settings instance
        keys = key_path.split(".")
        value: Any = self._settings
        try:
            for key in keys:
                if isinstance(value, dict):
                    value = value[key]
                else:
                    value = getattr(value, key)
            return value
        except (KeyError, AttributeError, TypeError):
            return default


def load_settings(config_path: str | Path | None = None) -> AtlasSettings:
    """Load settings from config.yaml with environment variable overrides.

    If config_path is None, looks for config.yaml in project root.
    """
    if config_path is None:
        repo_root = Path(__file__).resolve().parents[3]
        config_path = repo_root / "config.yaml"

    if Path(config_path).exists():
        settings = AtlasSettings.from_yaml(config_path)
    else:
        settings = AtlasSettings()

    # Apply environment variable overrides
    env_overrides = {}
    for key, value in os.environ.items():
        if key.startswith("ATLAS_"):
            setting_key = key[6:].lower()
            try:
                import json
                env_overrides[setting_key] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                env_overrides[setting_key] = value

    if env_overrides:
        # Merge env overrides into settings
        current = settings.__dict__.copy()
        current.update(env_overrides)
        settings = AtlasSettings(**current)

    return settings


def get_storage_settings(settings: AtlasSettings | None = None) -> dict[str, Any]:
    """Return effective storage configuration (env overrides config.yaml)."""
    if settings is None:
        settings = load_settings()

    repo_root = Path(__file__).resolve().parents[3]

    db_path = settings.database_path
    artifact_root = settings.artifact_root

    if not os.path.isabs(db_path):
        db_path = str(repo_root / db_path)
    if not os.path.isabs(artifact_root):
        artifact_root = str(repo_root / artifact_root)

    return {
        "database_path": db_path,
        "artifact_root": artifact_root,
        "cache_ttl_days": settings.cache_ttl_days,
        "cleanup_retention_days": settings.cleanup_retention_days,
    }
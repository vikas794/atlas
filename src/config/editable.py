"""Which settings the UI may edit, how they are grouped and whether they apply live.

Secrets are not editable here: the ``*_api_key_env`` names, and the Google credential/token
paths (the Drive panel owns those). Everything else is, flagged ``live`` when a running
Atlas picks the new value up without a restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.config.settings import LOG_LEVELS, NOTES_PROVIDERS


@dataclass(frozen=True)
class EditableField:
    key: str
    group: str
    label: str
    help: str
    #: ``live`` = applied to the running app on save; ``restart`` = needs a restart.
    apply: str = "live"
    unit: str = ""
    choices: tuple[str, ...] = ()
    #: Smallest allowed value for numbers (the server's ``problems()`` stays authoritative).
    minimum: float | None = None


GROUPS: tuple[tuple[str, str], ...] = (
    ("pipeline", "Pipeline"),
    ("retries", "Retries and backoff"),
    ("timeouts", "Timeouts and pacing"),
    ("drive", "Google Drive publishing"),
    ("notes", "Notes generation"),
    ("transcripts", "Transcripts"),
    ("server", "Server and logging"),
)

EDITABLE_FIELDS: tuple[EditableField, ...] = (
    EditableField("max_concurrent_videos", "pipeline", "Videos processed at once",
                  "1 runs the pipeline in series, one video at a time; more runs videos in "
                  "parallel. Transcript fetches stay one at a time either way.",
                  minimum=1),
    EditableField("worker_poll_interval_seconds", "pipeline", "Worker poll interval",
                  "How often the background worker looks for new work.", unit="s", minimum=0.1),
    EditableField("worker_shutdown_timeout_seconds", "pipeline", "Shutdown wait",
                  "How long running jobs get to finish when Atlas stops.", unit="s", minimum=0),

    EditableField("max_auto_retries", "retries", "Automatic retries",
                  "Retries for a transient failure before the video is marked failed.",
                  minimum=0),
    EditableField("backoff_base_seconds", "retries", "Backoff base",
                  "Wait before the first retry; it doubles each attempt.", unit="s", minimum=0),
    EditableField("backoff_cap_seconds", "retries", "Backoff cap",
                  "The longest wait between retries.", unit="s", minimum=0),
    EditableField("rate_limit_max_auto_retries", "retries", "Rate-limit retries",
                  "Retries when YouTube throttles a transcript fetch.", minimum=0),
    EditableField("rate_limit_backoff_base_seconds", "retries", "Rate-limit backoff base",
                  "First wait after a throttled fetch.", unit="s", minimum=0),
    EditableField("rate_limit_backoff_cap_seconds", "retries", "Rate-limit backoff cap",
                  "The longest wait after a throttled fetch.", unit="s", minimum=0),

    EditableField("notes_request_timeout_seconds", "timeouts", "LLM request timeout",
                  "A hung notes request is abandoned after this long.", unit="s", minimum=1),
    EditableField("transcript_min_interval_seconds", "timeouts", "Gap between transcript fetches",
                  "Minimum pause between two YouTube transcript fetches.", unit="s", minimum=0),

    EditableField("drive_max_retries", "drive", "Drive retries",
                  "Retries for a transient Google Drive/Docs error.", minimum=0),
    EditableField("drive_backoff_base_seconds", "drive", "Drive backoff base",
                  "Wait before the first Drive retry.", unit="s", minimum=0),
    EditableField("drive_backoff_cap_seconds", "drive", "Drive backoff cap",
                  "The longest wait between Drive retries.", unit="s", minimum=0),

    EditableField("notes_provider", "notes", "Notes provider",
                  "Single-provider shorthand, used when no model list is set.",
                  choices=NOTES_PROVIDERS),
    EditableField("notes_model", "notes", "Notes model",
                  "Advanced: overrides the model picked above. Leave empty."),
    EditableField("gemini_model", "notes", "Gemini model", "Used when the provider is gemini."),
    EditableField("gemini_input_token_limit", "notes", "Gemini input token limit",
                  "Longer transcripts are split to fit.", minimum=1),
    EditableField("openrouter_model", "notes", "OpenRouter model",
                  "Used when the provider is openrouter and no notes model is set."),
    EditableField("openrouter_base_url", "notes", "OpenRouter base URL", ""),
    EditableField("openrouter_input_token_limit", "notes", "OpenRouter input token limit",
                  "", minimum=1),
    EditableField("omniroute_model", "notes", "OmniRoute model",
                  "Used when the provider is omniroute and no notes model is set."),
    EditableField("omniroute_base_url", "notes", "OmniRoute base URL",
                  "OpenAI-compatible endpoint notes are sent to, for example "
                  "http://localhost:20128/v1."),
    EditableField("omniroute_models_url", "notes", "OmniRoute model list URL",
                  "Where the model list is fetched from (its /models is appended). Empty uses "
                  "the base URL."),
    EditableField("omniroute_input_token_limit", "notes", "OmniRoute input token limit",
                  "Longer transcripts are split to fit.", minimum=1),
    EditableField("notes_prompt_name", "notes", "Notes prompt file",
                  "A file in the prompts folder."),

    EditableField("transcript_language", "transcripts", "Transcript language",
                  "Preferred caption language code, for example en."),
    EditableField("transcript_output_folder", "transcripts", "Transcript folder",
                  "Where downloaded subtitle files are kept."),
    EditableField("ytdlp_cookies_file", "transcripts", "yt-dlp cookies file",
                  "Optional cookies.txt path. Empty is anonymous.", apply="restart"),
    EditableField("ytdlp_cookies_from_browser", "transcripts", "yt-dlp cookies browser",
                  "Optional browser name to read cookies from.", apply="restart"),

    EditableField("log_level", "server", "Log level", "", apply="restart", choices=LOG_LEVELS),
    EditableField("log_format", "server", "Log format", "", apply="restart"),
    EditableField("log_file_logging", "server", "Write a log file", "", apply="restart"),
    EditableField("log_file", "server", "Log file", "", apply="restart"),
    EditableField("host", "server", "Host", "Keep 127.0.0.1: Atlas has no authentication.",
                  apply="restart"),
    EditableField("port", "server", "Port", "", apply="restart", minimum=1),
    EditableField("cors_origins", "server", "Allowed CORS origins",
                  "Comma-separated browser origins. Empty disables CORS.", apply="restart"),
    EditableField("prompts_base_dir", "server", "Prompts folder", "", apply="restart"),
    EditableField("database_path", "server", "Database file",
                  "Set in config.yaml or the environment only: it locates the saved settings.",
                  apply="restart"),
)

#: Fields that must stay out of the database: it is where the store itself is found.
NOT_STORABLE: frozenset[str] = frozenset({"database_path"})

FIELDS_BY_KEY: dict[str, EditableField] = {f.key: f for f in EDITABLE_FIELDS}


def is_editable(key: str) -> bool:
    return key in FIELDS_BY_KEY and key not in NOT_STORABLE


def default_values() -> dict[str, Any]:
    from dataclasses import fields

    from src.config.settings import AtlasSettings

    base = AtlasSettings()
    return {f.name: getattr(base, f.name) for f in fields(base)}

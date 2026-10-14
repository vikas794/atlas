"""First-run health checks behind ``GET /api/diagnostics``.

Each check answers "can Atlas do X right now, and if not, what do I do?" without ever
returning a secret value: keys are reported as set / not set, files by their path. The only
network call is a Google token refresh when the saved Drive token has expired: a token that
merely *looks* refreshable on disk is often revoked (e.g. the 7-day expiry of an OAuth app
in Testing mode), and reporting it as "ok" would be a false success.

Statuses: ``ok``; ``warning`` (Atlas runs, one feature is unavailable, e.g. Drive publishing
before consent); ``error`` (a core feature cannot work, e.g. no YouTube key).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from src.config.settings import (
    AtlasSettings,
    SettingsError,
    get_storage_settings,
    load_settings,
    repo_root,
)
from src.shared.api_keys import read_api_keys
from src.shared.oauth_client import client_secrets_problem

Status = Literal["ok", "warning", "error"]


@dataclass(frozen=True)
class Check:
    id: str
    label: str
    status: Status
    message: str


CHECK_LABELS: dict[str, str] = {
    "config": "Configuration",
    "database": "Database",
    "youtube_key": "YouTube API key",
    "llm_key": "AI provider key",
    "drive_credentials": "Google Drive credentials",
    "drive_token": "Google Drive authorization",
    "frontend_build": "Frontend build",
}


def _display(path: Path) -> str:
    """A path relative to the repository when inside it (shorter, no user name)."""
    try:
        return path.resolve().relative_to(repo_root()).as_posix()
    except ValueError:
        return str(path)


def _resolve(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else repo_root() / path


def _check(check_id: str, status: Status, message: str) -> Check:
    return Check(check_id, CHECK_LABELS[check_id], status, message)


def check_config() -> tuple[Check, AtlasSettings | None]:
    try:
        settings = load_settings()
    except SettingsError as exc:
        return _check("config", "error", "; ".join(exc.problems)), None
    return (
        _check(
            "config",
            "ok",
            f"src/configs/config.yaml is valid (notes provider: {settings.notes_provider}, "
            f"{settings.max_concurrent_videos} video(s) at a time).",
        ),
        settings,
    )


def check_database(settings: AtlasSettings) -> Check:
    from src.infrastructure.storage.migrations import MIGRATIONS

    path = Path(get_storage_settings(settings)["database_path"])
    shown = _display(path)
    if not path.exists():
        if os.access(_nearest_existing(path.parent), os.W_OK):
            return _check(
                "database", "warning", f"{shown} does not exist yet; it is created on startup."
            )
        return _check("database", "error", f"{shown} does not exist and its folder is not writable.")
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return _check("database", "error", f"{shown} cannot be read: {exc}.")
    pending = [version for version, _ in MIGRATIONS if version not in applied]
    if pending:
        return _check(
            "database",
            "warning",
            f"{shown} has {len(pending)} pending migration(s); restart Atlas to apply them.",
        )
    return _check("database", "ok", f"{shown} is reachable and its schema is up to date.")


def _nearest_existing(path: Path) -> Path:
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def _key_is_set(env_name: str) -> bool:
    return bool(read_api_keys(env_name))


def _key_summary(env_name: str) -> str:
    """``NAME is set`` or ``NAME is set (3 keys, rotated on rate limits)``; never a value."""
    count = len(read_api_keys(env_name))
    if count > 1:
        return f"{env_name} is set ({count} keys, rotated when one is rate-limited)"
    return f"{env_name} is set"


def check_youtube_key(settings: AtlasSettings) -> Check:
    name = settings.youtube_api_key_env
    if _key_is_set(name):
        return _check("youtube_key", "ok", f"{_key_summary(name)}.")
    return _check(
        "youtube_key",
        "error",
        f"{name} is not set, so playlists cannot be ingested. Add {name}=<key> to .env "
        "(Google Cloud Console, YouTube Data API v3).",
    )


def check_llm_key(settings: AtlasSettings) -> Check:
    if settings.notes_provider == "openrouter":
        name = settings.openrouter_api_key_env
        model = settings.notes_model or settings.openrouter_model or "the provider default"
    else:
        name = settings.gemini_api_key_env
        model = settings.notes_model or settings.gemini_model
    if _key_is_set(name):
        return _check(
            "llm_key", "ok", f"{name} is set (provider {settings.notes_provider}, model {model})."
        )
    return _check(
        "llm_key",
        "error",
        f"{name} is not set, so notes cannot be generated with provider "
        f"{settings.notes_provider}. Add {name}=<key> to .env.",
    )


def check_drive_credentials(settings: AtlasSettings) -> Check:
    path = _resolve(settings.google_creds_path)
    shown = _display(path)
    if not path.is_file():
        return _check(
            "drive_credentials",
            "warning",
            f"{shown} not found. Upload the Google OAuth client file on the Settings page "
            "to publish to Google Drive.",
        )
    try:
        problem = client_secrets_problem(path.read_bytes())
    except OSError as exc:
        problem = f"cannot be read ({exc.strerror})"
    if problem:
        return _check("drive_credentials", "error", f"{shown}: {problem}")
    return _check("drive_credentials", "ok", f"{shown} is a valid OAuth client file.")


def check_drive_token(settings: AtlasSettings) -> Check:
    from src.infrastructure.google.drive import (
        STATE_AUTHORIZED,
        GoogleDriveAuthorizationRequired,
        GoogleDriveExporter,
    )

    token = _resolve(settings.google_token_path)
    if not token.is_file():
        return _check(
            "drive_token",
            "warning",
            "Google Drive is not authorized yet. Complete the consent flow once on the "
            "Settings page before publishing.",
        )
    exporter = GoogleDriveExporter(
        creds_path=_resolve(settings.google_creds_path), token_path=token
    )
    state, message = exporter.auth_state()
    if state != STATE_AUTHORIZED:
        return _check("drive_token", "warning", message)
    try:
        exporter.ensure_authorized()  # refreshes an expired token; no consent window
    except GoogleDriveAuthorizationRequired as exc:
        return _check("drive_token", "warning", str(exc))
    except Exception:  # network down, Google 5xx: unknown, not "ok"
        return _check(
            "drive_token",
            "warning",
            "Could not reach Google to verify the Drive token; check the network and reload.",
        )
    return _check("drive_token", "ok", message)


def check_frontend_build(dist_dir: Path) -> Check:
    if (dist_dir / "index.html").is_file():
        return _check(
            "frontend_build", "ok", f"{_display(dist_dir)} is built and served by this server."
        )
    return _check(
        "frontend_build",
        "warning",
        f"{_display(dist_dir)} not found. Run scripts/build (.ps1 or .sh) to serve the UI "
        "from this server; not needed when using the Vite dev server (scripts/dev).",
    )


def run_checks(dist_dir: Path) -> list[Check]:
    """Every check, in a fixed order; a broken configuration skips the dependent ones."""
    config, settings = check_config()
    checks = [config]
    if settings is None:
        skipped = "Not checked: fix the configuration first."
        checks += [
            _check(check_id, "warning", skipped)
            for check_id in ("database", "youtube_key", "llm_key", "drive_credentials", "drive_token")
        ]
    else:
        checks += [
            check_database(settings),
            check_youtube_key(settings),
            check_llm_key(settings),
            check_drive_credentials(settings),
            check_drive_token(settings),
        ]
    checks.append(check_frontend_build(dist_dir))
    return checks


def diagnostics_report(dist_dir: Path) -> dict:
    """The ``GET /api/diagnostics`` body: ``{checks: [...], recent_errors: [...]}``."""
    from src.shared.logging_setup import recent_errors

    return {
        "checks": [asdict(check) for check in run_checks(dist_dir)],
        "recent_errors": recent_errors(),
    }


__all__ = ["CHECK_LABELS", "Check", "diagnostics_report", "run_checks"]

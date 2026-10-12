"""Validation of a Google OAuth client file (``credentials.json``), shared by upload and diagnostics."""

from __future__ import annotations

import json


def client_secrets_problem(raw: bytes) -> str | None:
    """Why ``raw`` is not a usable Google OAuth client file, or ``None`` when it is.

    Google's "Desktop app" download is ``{"installed": {...}}`` (``{"web": {...}}`` for a web
    client); both must carry ``client_id`` and ``client_secret``. The returned text never
    contains values from the file.
    """
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "The file is not valid JSON."
    section = None
    if isinstance(data, dict):
        section = data.get("installed") or data.get("web")
    if not isinstance(section, dict):
        return (
            "This is not a Google OAuth client file: expected an 'installed' (Desktop app) "
            "or 'web' section. Download it from Google Cloud > APIs & Services > Credentials."
        )
    missing = [key for key in ("client_id", "client_secret") if not section.get(key)]
    if missing:
        return f"The OAuth client file is missing: {', '.join(missing)}."
    return None


__all__ = ["client_secrets_problem"]

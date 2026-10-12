"""Secret redaction shared by every layer that turns provider text into a log or reason.

Provider error messages are the one untrusted text Atlas stringifies. Two real
leaks motivated this module: googleapiclient's ``HttpError`` text contains the
full request URL including ``key=<API key>``, and yt-dlp echoes the URL it was
fetching. Everything that logs or stores such text goes through
:func:`redact_text`.

Two layers, both applied to every string:

1. **Known credential shapes, wherever they appear** - Google API keys (``AIza...``,
   which is also the Gemini key format), Google OAuth access tokens (``ya29.``),
   refresh tokens (``1//0...``), OAuth client secrets (``GOCSPX-``) and
   OpenAI/OpenRouter keys (``sk-...``). These are caught even in free text such
   as ``"API key not valid: AIza..."``.
2. **Named assignments** - ``key=...``, ``api_key: ...``, ``"refresh_token": "..."``,
   ``client_secret=...``, ``x-goog-api-key: ...`` and the like, in query strings,
   headers, JSON and Python reprs.
"""

from __future__ import annotations

import re
from typing import Final

REDACTED: Final[str] = "<redacted>"

#: Credential formats recognised by shape alone.
_KNOWN_SHAPES: Final[re.Pattern[str]] = re.compile(
    r"AIza[0-9A-Za-z_\-]{30,}"  # Google / Gemini API key
    r"|ya29\.[0-9A-Za-z_\-.]+"  # Google OAuth access token
    r"|\b1//0[0-9A-Za-z_\-]{10,}"  # Google OAuth refresh token
    r"|GOCSPX-[0-9A-Za-z_\-]+"  # Google OAuth client secret
    r"|\bsk-[0-9A-Za-z_\-]{16,}"  # OpenAI / OpenRouter key
)

#: ``name<sep>value`` where the name ends in a credential word, optionally prefixed
#: (``refresh_token``, ``client_secret``, ``x-goog-api-key``). The separator allows the
#: quotes and spaces of JSON / repr output (``"access_token": "..."``).
_SECRET_ASSIGNMENT: Final[re.Pattern[str]] = re.compile(
    r"(?i)(\b(?:[a-z0-9]+[-_])*(?:api[-_]?key|key|token|secret|password|passwd|signature)\b)"
    r"(\s*[\"']?\s*[:=]\s*[\"']?\s*)"
    r"([^\s&;,\"'}\]]+)"
)
#: ``Authorization: Bearer <jwt>`` headers echoed back by a provider.
_BEARER_TOKEN: Final[re.Pattern[str]] = re.compile(r"(?i)\bbearer\s+[\w.\-/+=]+")

#: Secrets shorter than this are not replaced verbatim: doing so would mangle
#: unrelated text without protecting anything meaningful.
_MIN_SECRET_LENGTH: Final[int] = 8


def redact_text(text: object, *secrets: str | None) -> str:
    """Return ``text`` with credential-shaped substrings and known secrets blanked out.

    Args:
        text: Any value; it is stringified first.
        *secrets: Exact secret values (for example the configured API key) to
            replace wherever they appear, in addition to the pattern matching.
    """
    redacted = text if isinstance(text, str) else str(text)
    for secret in secrets:
        if secret and len(secret) >= _MIN_SECRET_LENGTH:
            redacted = redacted.replace(secret, REDACTED)
    redacted = _KNOWN_SHAPES.sub(REDACTED, redacted)
    redacted = _BEARER_TOKEN.sub(f"Bearer {REDACTED}", redacted)
    return _SECRET_ASSIGNMENT.sub(_replace_assignment, redacted)


def _replace_assignment(match: re.Match[str]) -> str:
    if match.group(3) == REDACTED:
        return match.group(0)
    return f"{match.group(1)}{match.group(2)}{REDACTED}"


__all__ = ["REDACTED", "redact_text"]

"""Secret redaction shared by every layer that turns provider text into a log or reason.

Provider error messages are the one untrusted text Atlas stringifies. Two real
leaks motivated this module: googleapiclient's ``HttpError`` text contains the
full request URL including ``key=<API key>``, and yt-dlp echoes the URL it was
fetching. Everything that logs or stores such text goes through
:func:`redact_text`.
"""

from __future__ import annotations

import re
from typing import Final

REDACTED: Final[str] = "<redacted>"

#: ``key=AIza...`` / ``"api_key": "..."`` / ``token=...`` style secrets.
_SECRET_ASSIGNMENT: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(api[-_]?key|key|token|access[-_]?token|auth[-_]?token|signature|password|secret)"
    r"([=:\"']+)(\s*)([^\s&;,\"']+)"
)
#: ``Authorization: Bearer <jwt>`` headers echoed back by a provider.
_BEARER_TOKEN: Final[re.Pattern[str]] = re.compile(r"(?i)\bbearer\s+[\w.\-]+")

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
    redacted = _BEARER_TOKEN.sub(f"Bearer {REDACTED}", redacted)
    return _SECRET_ASSIGNMENT.sub(rf"\1\2{REDACTED}", redacted)


__all__ = ["REDACTED", "redact_text"]

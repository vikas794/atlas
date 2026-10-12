"""Application logging setup (PRD section 21: structured logs with playlist and video ids).

Why this exists: under ``uvicorn`` only uvicorn's own loggers are configured, so the root
logger has no handler and every ``src.*`` INFO line was silently dropped. ``configure_logging``
installs one stderr handler (and optionally a rotating file) on the root logger, honouring
``log_level`` / ``log_file_logging`` / ``log_file`` from the settings.

Format: the usual ``asctime level logger: message`` line followed by every ``extra=`` field as
``key=value`` (so ``playlist_id=3 video_id=17`` appears on each pipeline event), then the
traceback if any. The whole rendered line is passed through the shared secret redactor, so an
API key quoted by a provider error (``...&key=AIza...``) can never reach a log.

Never log keys, tokens or transcript text; this formatter is a backstop, not a licence.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import IO, Any

from src.shared.redaction import redact_text

_HANDLER_MARK = "_atlas_handler"

#: Attributes every LogRecord has; anything else on a record came from ``extra=``.
_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}

#: Third-party loggers that are chatty at INFO.
_QUIET_LOGGERS = ("googleapiclient", "httpx", "httpcore", "urllib3", "asyncio")

_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _render(value: Any) -> str:
    text = str(value)
    return f'"{text}"' if (" " in text or not text) else text


class AtlasFormatter(logging.Formatter):
    """Standard line + ``extra`` fields as ``key=value`` + traceback, with secrets redacted."""

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        if self.usesTime():
            record.asctime = self.formatTime(record, self.datefmt)
        line = self.formatMessage(record)

        extras = [
            f"{key}={_render(value)}"
            for key, value in record.__dict__.items()
            if key not in _STANDARD_ATTRS and not key.startswith("_")
        ]
        if extras:
            line = f"{line} | {' '.join(extras)}"

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line = f"{line}\n{record.exc_text}"
        if record.stack_info:
            line = f"{line}\n{self.formatStack(record.stack_info)}"
        return redact_text(line)


def _level_of(name: str) -> int:
    level = logging.getLevelName(str(name or "INFO").strip().upper())
    return level if isinstance(level, int) else logging.INFO


def configure_logging(
    settings: Any | None = None,
    *,
    stream: IO[str] | None = None,
) -> logging.Handler:
    """Install (or replace) Atlas' root log handler and return the stream handler.

    Idempotent: calling it again swaps the previous Atlas handlers instead of stacking
    duplicates. Safe to call at import time.

    Args:
        settings: An ``AtlasSettings`` (anything with ``log_level``, ``log_format``,
            ``log_file_logging`` and ``log_file``). Defaults are used when omitted.
        stream: Where the console handler writes (default ``sys.stderr``).
    """
    level = _level_of(getattr(settings, "log_level", "INFO"))
    fmt = getattr(settings, "log_format", None) or _DEFAULT_FORMAT
    formatter = AtlasFormatter(fmt)

    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARK, False):
            root.removeHandler(existing)
            existing.close()

    console = logging.StreamHandler(stream or sys.stderr)
    console.setFormatter(formatter)
    setattr(console, _HANDLER_MARK, True)
    root.addHandler(console)

    if getattr(settings, "log_file_logging", False):
        from src.config.settings import repo_root

        path = Path(getattr(settings, "log_file", "logs/atlas.log"))
        if not path.is_absolute():
            path = repo_root() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        setattr(file_handler, _HANDLER_MARK, True)
        root.addHandler(file_handler)

    root.setLevel(level)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
    return console


__all__ = ["AtlasFormatter", "configure_logging"]

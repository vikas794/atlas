"""The in-memory WARNING+ ring buffer behind /api/diagnostics ``recent_errors``."""

from __future__ import annotations

import io
import logging
from datetime import datetime

from src.config.settings import AtlasSettings
from src.shared.logging_setup import RECENT_ERRORS, RecentErrorsHandler, configure_logging

KEY = "AIzaSyA1234567890abcdefghijklmnopqrstu"


def make_logger(handler: RecentErrorsHandler) -> logging.Logger:
    logger = logging.getLogger("atlas.test.recent")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger


def test_only_warning_and_above_are_kept_newest_first() -> None:
    handler = RecentErrorsHandler()
    logger = make_logger(handler)
    logger.info("ignored")
    logger.warning("first %s", 1)
    logger.error("second", extra={"playlist_id": 7})

    entries = handler.snapshot()

    assert [e["level"] for e in entries] == ["ERROR", "WARNING"]
    assert entries[0]["message"] == "second | playlist_id=7"
    assert entries[1]["message"] == "first 1"
    assert entries[0]["logger"] == "atlas.test.recent"
    assert datetime.fromisoformat(entries[0]["timestamp"]).tzinfo is not None
    assert set(entries[0]) == {"timestamp", "level", "logger", "message"}


def test_capacity_is_bounded() -> None:
    handler = RecentErrorsHandler(capacity=50)
    logger = make_logger(handler)
    for i in range(80):
        logger.warning("w%d", i)
    entries = handler.snapshot()
    assert len(entries) == 50
    assert entries[0]["message"] == "w79" and entries[-1]["message"] == "w30"


def test_secrets_are_redacted_and_tracebacks_are_summarised() -> None:
    handler = RecentErrorsHandler()
    logger = make_logger(handler)
    try:
        raise RuntimeError(f"bad key={KEY}")
    except RuntimeError:
        logger.exception("Gemini call failed with %s", KEY)

    (entry,) = handler.snapshot()
    assert KEY not in entry["message"]
    assert "RuntimeError" in entry["message"]
    assert "Traceback" not in entry["message"] and "\n" not in entry["message"]


def test_configure_logging_installs_the_shared_buffer_once() -> None:
    settings = AtlasSettings(log_file_logging=False)
    configure_logging(settings, stream=io.StringIO())
    configure_logging(settings, stream=io.StringIO())
    root = logging.getLogger()
    assert root.handlers.count(RECENT_ERRORS) == 1

    RECENT_ERRORS.clear()
    logging.getLogger("src.some.module").warning("visible to diagnostics")
    assert RECENT_ERRORS.snapshot()[0]["message"] == "visible to diagnostics"

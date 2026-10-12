"""Logging (M6): src.* INFO logs must reach stderr, carry playlist_id and video_id, never keys."""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.application.use_cases.playlist.process_video import ProcessVideoUseCase
from src.config.settings import AtlasSettings
from src.domain.interfaces.notes_generator import GeneratedNotes
from src.domain.interfaces.transcript_provider import TranscriptResult
from src.domain.models.playlist import FailureType, PipelineError
from src.domain.services.sync_diff import PlaylistEntry
from src.infrastructure.storage import db
from src.infrastructure.storage.playlist_repo import PlaylistRepository
from src.shared.logging_setup import AtlasFormatter, configure_logging


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    """Install Atlas logging into a StringIO and restore the previous handlers afterwards."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    buffer = io.StringIO()
    yield buffer
    for handler in list(root.handlers):
        if handler not in saved_handlers:
            root.removeHandler(handler)
            handler.close()
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_src_info_logs_reach_the_configured_stream_with_extras(stream: io.StringIO) -> None:
    configure_logging(AtlasSettings(log_level="INFO"), stream=stream)

    logging.getLogger("src.application.demo").info(
        "Fetching transcript", extra={"playlist_id": 3, "video_id": 17}
    )

    line = stream.getvalue()
    assert "INFO src.application.demo: Fetching transcript" in line
    assert "playlist_id=3" in line and "video_id=17" in line


def test_the_log_level_setting_is_honoured(stream: io.StringIO) -> None:
    configure_logging(AtlasSettings(log_level="WARNING"), stream=stream)

    logging.getLogger("src.demo").info("quiet")
    logging.getLogger("src.demo").warning("loud")

    assert "quiet" not in stream.getvalue() and "loud" in stream.getvalue()


def test_an_invalid_level_falls_back_to_info(stream: io.StringIO) -> None:
    configure_logging(AtlasSettings(log_level="BANANA"), stream=stream)

    assert logging.getLogger().level == logging.INFO


def test_configuring_twice_does_not_duplicate_lines(stream: io.StringIO) -> None:
    configure_logging(AtlasSettings(), stream=stream)
    configure_logging(AtlasSettings(), stream=stream)

    logging.getLogger("src.demo").info("once")

    assert stream.getvalue().count("once") == 1


def test_log_file_logging_writes_a_file(tmp_path: Path, stream: io.StringIO) -> None:
    target = tmp_path / "logs" / "atlas.log"
    configure_logging(
        AtlasSettings(log_file_logging=True, log_file=str(target)), stream=stream
    )

    logging.getLogger("src.demo").info("to the file", extra={"video_id": 9})
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert "to the file" in target.read_text(encoding="utf-8")
    assert "video_id=9" in target.read_text(encoding="utf-8")


def test_secrets_are_redacted_from_messages_and_tracebacks(stream: io.StringIO) -> None:
    configure_logging(AtlasSettings(), stream=stream)
    key = "AIzaSyLEAKYLEAKYLEAKY0123456789"
    log = logging.getLogger("src.demo")

    log.warning("request failed: https://googleapis.com/x?part=a&key=%s", key)
    try:
        raise RuntimeError(f"HttpError ... key={key}")
    except RuntimeError:
        log.exception("boom", extra={"note": f"token={key}"})

    text = stream.getvalue()
    assert key not in text
    assert "<redacted>" in text
    assert "Traceback" in text  # the traceback itself is kept, just scrubbed


def test_values_with_spaces_are_quoted() -> None:
    record = logging.LogRecord("n", logging.INFO, "f", 1, "msg", (), None)
    record.reason = "two words"

    assert 'reason="two words"' in AtlasFormatter("%(message)s").format(record)


async def test_every_pipeline_event_carries_playlist_id_and_video_id(
    tmp_path: Path, stream: io.StringIO
) -> None:
    configure_logging(AtlasSettings(log_level="INFO"), stream=stream)
    db.initialize(tmp_path / "log.sqlite3")
    repo = PlaylistRepository(tmp_path / "log.sqlite3")
    playlist = await repo.create_playlist("PL1", "u")
    video = (await repo.insert_videos(playlist.id, [PlaylistEntry("v1", "T", 1)]))[0]

    class Transcripts:
        async def fetch_transcript(self, video_id: str, language: str = "en") -> TranscriptResult:
            return TranscriptResult(video_id, language, "", "text", "h", "", 4)

    class Notes:
        calls = 0

        async def generate_notes(self, *, title: str, transcript: str) -> GeneratedNotes:
            Notes.calls += 1
            if Notes.calls == 1:
                raise PipelineError(FailureType.RATE_LIMITED, "slow down")
            return GeneratedNotes("## Notes\n\nx\n\n## Key Points\n\ny", "p", "m", "v")

    process = ProcessVideoUseCase(repo, Transcripts(), Notes(), max_auto_retries=2,
                                  backoff_base_seconds=0.0)
    await process.execute(video.id)  # fails transiently, schedules a retry
    await process.execute(video.id)  # succeeds

    pipeline_lines = [
        line for line in stream.getvalue().splitlines()
        if "process_video:" in line
    ]
    assert len(pipeline_lines) >= 5
    for line in pipeline_lines:
        assert f"playlist_id={playlist.id}" in line, line
        assert f"video_id={video.id}" in line, line
    assert any("failure_type=rate_limited" in line for line in pipeline_lines)
    assert "text" not in "".join(pipeline_lines).replace("Fetching transcript", "")  # no transcript


def test_the_app_installs_logging_at_import_so_uvicorn_shows_src_logs() -> None:
    """backend.main calls configure_logging(); under uvicorn the root logger has no handler."""
    import backend.main  # noqa: F401  (import installs the handler)

    marked = [h for h in logging.getLogger().handlers if getattr(h, "_atlas_handler", False)]
    assert marked, "Atlas logging was not installed on the root logger"

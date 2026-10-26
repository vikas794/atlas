"""Google OAuth handling for Drive publishing (PRD section 17, 19).

Covers what happens when the stored token stops working: a revoked / expired refresh
token (``invalid_grant``) must become one clear, permanent "re-authorize Drive" error --
never an endless retry -- while a network blip during a refresh stays transient. Also
covers the atomic token write and the consent-flow guard rails.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest
from google.auth.exceptions import RefreshError, TransportError
from google.oauth2.credentials import Credentials

from src.application.use_cases.playlist.publish import PublishPlaylistUseCase
from src.domain.interfaces.drive_publisher import DriveAuthorizationRequired
from src.infrastructure.google import drive as drive_module
from src.infrastructure.google.drive import (
    CONSENT_TIMEOUT_SECONDS,
    GoogleDriveAuthorizationRequired,
    GoogleDriveExporter,
)
from src.infrastructure.google.publisher import GoogleDrivePublisher
from src.infrastructure.storage import db
from src.infrastructure.storage.playlist_repo import PlaylistRepository
from tests.test_drive_publish import StubGoogle, real_publisher, seed

INVALID_GRANT = RefreshError(
    "invalid_grant: Token has been expired or revoked.",
    {"error": "invalid_grant", "error_description": "Token has been expired or revoked."},
)


def write_expired_token(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "token": "ya29.expired",
                "refresh_token": "1//refresh",
                "client_id": "cid.apps.googleusercontent.com",
                "client_secret": "secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": drive_module.SCOPES,
                "expiry": "2020-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def token_files(tmp_path: Path) -> tuple[Path, Path]:
    creds, token = tmp_path / "credentials.json", tmp_path / "token.json"
    creds.write_text('{"installed": {}}', encoding="utf-8")
    write_expired_token(token)
    return creds, token


# ------------------------------------------------------------ refresh at token load


def test_a_revoked_refresh_token_is_a_permanent_reauthorize_error(
    token_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    creds, token = token_files
    calls = {"n": 0}

    def refresh(self: Credentials, request: Any) -> None:
        calls["n"] += 1
        raise INVALID_GRANT

    monkeypatch.setattr(Credentials, "refresh", refresh)
    exporter = GoogleDriveExporter(creds_path=creds, token_path=token)
    # Before anything is tried, an expired-but-refreshable token looks authorized.
    assert exporter.auth_state()[0] == "authorized"

    with pytest.raises(GoogleDriveAuthorizationRequired) as excinfo:
        exporter.ensure_authorized()

    assert "Re-authorize Drive" in str(excinfo.value)
    assert calls["n"] == 1
    # Now that the token is known to be dead, the status endpoint says so.
    state, message = exporter.auth_state()
    assert state == "needs_consent" and "Re-authorize" in message

    # Re-authorizing (a new token file) clears the "dead token" verdict.
    write_expired_token(token)

    os.utime(token, (1_900_000_000, 1_900_000_000))
    assert exporter.auth_state()[0] == "authorized"


async def test_a_network_error_during_refresh_is_not_reported_as_reauthorize(
    token_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    creds, token = token_files

    def refresh(self: Credentials, request: Any) -> None:
        raise TransportError("Connection reset by peer")

    monkeypatch.setattr(Credentials, "refresh", refresh)
    exporter = GoogleDriveExporter(creds_path=creds, token_path=token)

    with pytest.raises(TransportError):
        exporter.ensure_authorized()
    assert exporter.auth_state()[0] == "authorized"  # nothing permanent was concluded

    # The publish request is accepted; the background job retries the refresh.
    await GoogleDrivePublisher(exporter=exporter).ensure_authorized()


async def test_publish_request_with_a_revoked_token_is_rejected_up_front(
    token_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    creds, token = token_files
    monkeypatch.setattr(Credentials, "refresh", lambda self, request: (_ for _ in ()).throw(INVALID_GRANT))
    publisher = GoogleDrivePublisher(exporter=GoogleDriveExporter(creds_path=creds, token_path=token))

    with pytest.raises(DriveAuthorizationRequired) as excinfo:
        await publisher.ensure_authorized()
    assert "Re-authorize Drive" in str(excinfo.value)


# ------------------------------------------------------------ status endpoint


async def test_drive_status_verifies_an_expired_token_with_google(
    token_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.transport.http.fastapi.routers.drive_auth import get_drive_status

    creds, token = token_files
    monkeypatch.setattr(Credentials, "refresh", lambda self, request: (_ for _ in ()).throw(INVALID_GRANT))
    exporter = GoogleDriveExporter(creds_path=creds, token_path=token)

    status = await get_drive_status(exporter)

    assert status.configured is False and status.state == "needs_consent"
    assert "Re-authorize Drive" in status.message


async def test_drive_status_stays_authorized_when_google_is_unreachable(
    token_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.transport.http.fastapi.routers.drive_auth import get_drive_status

    creds, token = token_files
    monkeypatch.setattr(Credentials, "refresh", lambda self, request: (_ for _ in ()).throw(TransportError("reset")))
    exporter = GoogleDriveExporter(creds_path=creds, token_path=token)

    status = await get_drive_status(exporter)

    assert status.configured is True and status.state == "authorized"
    assert "Could not reach Google" in status.message


# ------------------------------------------------- refresh failing mid-publish


@pytest.fixture
def repo(tmp_path: Path) -> PlaylistRepository:
    db_path = tmp_path / "auth.sqlite3"
    db.initialize(db_path)
    return PlaylistRepository(db_path)


async def test_token_revoked_mid_publish_fails_once_with_a_clear_visible_error(
    repo: PlaylistRepository, tmp_path: Path
) -> None:
    """The access token expires during a long publish and its refresh hits invalid_grant:
    no retry loop, the job fails with a human-readable 're-authorize' reason in the
    playlist status the UI polls, and the Drive status flips to needs_consent."""
    stub = StubGoogle()
    publisher, _ = real_publisher(stub, max_retries=5)
    token = tmp_path / "token.json"
    write_expired_token(token)
    publisher.exporter.token_path = token
    playlist, _videos = await seed(repo, ["One", "Two"])
    stub.fail("documents.batchUpdate", INVALID_GRANT, INVALID_GRANT, INVALID_GRANT)
    uc = PublishPlaylistUseCase(repo=repo, publisher=publisher)
    await repo.begin_publish(playlist.id)

    assert await uc.run(playlist.id) is None

    stored = await repo.get_playlist(playlist.id)
    assert stored.status == "publish_failed"
    assert "Re-authorize Drive" in stored.last_publish_error
    assert len(stub.failures["documents.batchUpdate"]) == 2  # exactly one attempt, no retries
    assert publisher.exporter.auth_state()[0] == "needs_consent"


async def test_a_retryable_refresh_error_mid_publish_is_retried() -> None:
    stub = StubGoogle()
    publisher, _ = real_publisher(stub, max_retries=3)
    stub.fail("files.create", RefreshError("temporarily unavailable", retryable=True))

    result = await publisher.publish_playlist(
        playlist_pk=1, playlist_title="T", pad_width=1, entries=[],
        index_titles=[], update_index=False,
    )

    assert result.folder_id


# ------------------------------------------------------------- token persistence


def test_the_token_is_written_atomically_and_a_failed_write_keeps_the_old_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = tmp_path / "token.json"
    token.write_text('{"old": true}', encoding="utf-8")
    exporter = GoogleDriveExporter(creds_path=tmp_path / "c.json", token_path=token)

    class FakeCreds:
        def to_json(self) -> str:
            return '{"new": true}'

    def broken_replace(src: str, dst: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(drive_module.os, "replace", broken_replace)
    with pytest.raises(OSError):
        exporter._write_token(FakeCreds())  # type: ignore[arg-type]
    assert json.loads(token.read_text(encoding="utf-8")) == {"old": True}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["token.json"]  # no temp leftovers

    monkeypatch.undo()
    exporter._write_token(FakeCreds())  # type: ignore[arg-type]
    assert json.loads(token.read_text(encoding="utf-8")) == {"new": True}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["token.json"]


# ------------------------------------------------------------------ consent flow


def test_consent_flow_uses_a_free_port_and_a_timeout_and_never_runs_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    creds = tmp_path / "credentials.json"
    creds.write_text('{"installed": {"client_id": "x"}}', encoding="utf-8")
    exporter = GoogleDriveExporter(creds_path=creds, token_path=tmp_path / "token.json")
    seen: dict[str, Any] = {}
    release = threading.Event()
    entered = threading.Event()

    class FakeFlow:
        def run_local_server(self, **kwargs: Any) -> Any:
            seen.update(kwargs)
            entered.set()
            release.wait(5)

            class C:
                def to_json(self) -> str:
                    return "{}"

            return C()

    monkeypatch.setattr(
        drive_module.InstalledAppFlow,
        "from_client_secrets_file",
        classmethod(lambda cls, path, scopes: FakeFlow()),
    )

    worker = threading.Thread(target=exporter.authenticate)
    worker.start()
    assert entered.wait(5)
    with pytest.raises(RuntimeError, match="already waiting"):
        exporter.authenticate()
    release.set()
    worker.join(5)

    assert seen == {"port": 0, "timeout_seconds": CONSENT_TIMEOUT_SECONDS}
    assert (tmp_path / "token.json").exists()

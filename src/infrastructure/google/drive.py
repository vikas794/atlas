from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
]

#: ``auth_state()`` values, in the order a user moves through them.
STATE_CREDENTIALS_MISSING = "credentials_missing"
STATE_NEEDS_CONSENT = "needs_consent"
STATE_AUTHORIZED = "authorized"

_REAUTH_HINT = (
    "Google Drive is not authorized. Upload OAuth client credentials via "
    "POST /api/drive/credentials, then run POST /api/drive/auth to grant access "
    "(complete the consent once on the machine that runs Atlas)."
)

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

#: :meth:`GoogleDriveExporter.file_state` results.
FILE_OK = "ok"
FILE_TRASHED = "trashed"
FILE_MISSING = "missing"
DOC_MIME_TYPE = "application/vnd.google-apps.document"

#: Docs ``body.content`` of a brand-new, empty document ends at index 2 (one empty paragraph).
_EMPTY_DOC_END_INDEX = 2

#: How long the interactive consent flow waits for the browser redirect before giving up,
#: so an abandoned consent never pins a server thread (and the redirect port) forever.
CONSENT_TIMEOUT_SECONDS = 300

#: Redirect port for "web"-type OAuth clients, whose redirect URI must match exactly.
#: "installed" (Desktop app) clients accept any loopback port, so a free one is used.
_WEB_CLIENT_REDIRECT_PORT = 8080


class GoogleDriveAuthorizationRequired(RuntimeError):
    """Raised when an operation needs Drive access that has not been granted."""


def is_permanent_refresh_error(exc: BaseException) -> bool:
    """Whether a token-refresh failure needs the user to re-authorize.

    ``invalid_grant`` (refresh token revoked or expired -- e.g. the 7-day expiry of an
    OAuth app in "Testing" mode) is permanent; google-auth marks failures that are worth
    retrying (5xx, timeouts) as ``retryable``.
    """
    return isinstance(exc, RefreshError) and not getattr(exc, "retryable", False)


def reauthorize_message(exc: BaseException) -> str:
    """The user-facing message for a revoked/expired refresh token."""
    return (
        "Google Drive access has expired or was revoked "
        f"({type(exc).__name__}: {str(exc).splitlines()[0][:120] if str(exc) else 'no detail'}). "
        f"Re-authorize Drive. {_REAUTH_HINT}"
    )


class GoogleDriveExporter:
    """Thin, blocking wrapper over the Google Drive and Docs APIs.

    Authentication is **lazy**: constructing the exporter performs no network
    calls and no OAuth flow. Credentials are resolved on first use by
    :meth:`_ensure_credentials`, which only ever loads/refreshes an existing
    token -- it never opens a consent window. The interactive consent flow is
    the explicit job of :meth:`authenticate`.

    ``httplib2`` (under ``googleapiclient``) is not thread-safe and every call here
    runs in ``asyncio.to_thread`` workers, so API services are built **per thread**
    (and reused only within that thread) instead of being shared.
    """

    def __init__(
        self,
        creds_path: str | Path | None = None,
        token_path: str | Path | None = None,
        *,
        drive_service_factory: Callable[[], Any] | None = None,
        docs_service_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.logger = logging.getLogger(__name__)

        if creds_path is None:
            creds_path = os.getenv("ATLAS_GOOGLE_CREDS_PATH") or "credentials.json"
        if token_path is None:
            token_path = os.getenv("ATLAS_GOOGLE_TOKEN_PATH") or "token.json"

        self.creds_path = Path(creds_path)
        self.token_path = Path(token_path)

        self._credentials: Credentials | None = None
        self._creds_lock = threading.Lock()
        self._consent_lock = threading.Lock()
        #: (token file mtime, message) of a refresh that failed permanently. While the
        #: token file is unchanged, the token is known to be dead even though it *looks*
        #: refreshable, so the status endpoint must not claim "authorized".
        self._dead_token: tuple[float, str] | None = None
        self._local = threading.local()
        self._drive_service_factory = drive_service_factory
        self._docs_service_factory = docs_service_factory

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    def auth_state(self) -> tuple[str, str]:
        """Return ``(state, message)`` based on files on disk only; no network, no consent flow.

        ``authorized`` only when a token exists that is valid or refreshable.
        """
        has_creds = self.creds_path.exists()
        if self.token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
            except Exception:
                creds = None
            dead = self._dead_token_message()
            if dead is not None:
                return STATE_NEEDS_CONSENT, dead
            if creds is not None and (creds.valid or (creds.expired and creds.refresh_token)):
                return STATE_AUTHORIZED, "Google Drive is authorized."
            if not has_creds:
                return STATE_CREDENTIALS_MISSING, (
                    f"The saved token '{self.token_path.name}' is unusable and "
                    f"'{self.creds_path.name}' is missing. Upload the OAuth client credentials."
                )
            return STATE_NEEDS_CONSENT, (
                f"The saved token '{self.token_path.name}' is expired or invalid. "
                "Run the Google consent flow again."
            )
        if has_creds:
            return STATE_NEEDS_CONSENT, (
                f"'{self.creds_path.name}' is present but consent has not been granted yet. "
                "Run the Google consent flow once."
            )
        return STATE_CREDENTIALS_MISSING, (
            f"Missing '{self.creds_path.name}'. Upload the OAuth client credentials JSON "
            "downloaded from Google Cloud."
        )

    def auth_status(self) -> tuple[bool, str]:
        """Return ``(authorized, message)``; see :meth:`auth_state`."""
        state, message = self.auth_state()
        return state == STATE_AUTHORIZED, message

    def ensure_authorized(self) -> None:
        """Raise :class:`GoogleDriveAuthorizationRequired` unless usable credentials exist."""
        self._ensure_credentials()

    def _token_mtime(self) -> float | None:
        try:
            return self.token_path.stat().st_mtime
        except OSError:
            return None

    def _dead_token_message(self) -> str | None:
        if self._dead_token is None:
            return None
        mtime, message = self._dead_token
        return message if self._token_mtime() == mtime else None

    def mark_token_revoked(self, exc: BaseException) -> GoogleDriveAuthorizationRequired:
        """Record a permanent refresh failure and return the error to raise.

        Drops the cached credentials and per-thread services so the next use re-reads the
        token file (e.g. after the user re-authorizes), and makes :meth:`auth_state`
        report ``needs_consent`` until the token file changes.
        """
        message = reauthorize_message(exc)
        with self._creds_lock:
            self._credentials = None
            self._local = threading.local()
            mtime = self._token_mtime()
            self._dead_token = (mtime, message) if mtime is not None else None
        return GoogleDriveAuthorizationRequired(message)

    def authenticate(self) -> None:
        """Run the interactive OAuth consent flow and persist the token file.

        Blocking (it waits for the browser, at most :data:`CONSENT_TIMEOUT_SECONDS`);
        callers run it in a worker thread. Only one consent flow runs at a time: a second
        concurrent request fails fast instead of fighting over the redirect port.
        """
        if not self._consent_lock.acquire(blocking=False):
            raise RuntimeError(
                "A Google consent flow is already waiting for the browser; finish it first."
            )
        try:
            creds = self._run_consent_flow()
            self._write_token(creds)
            with self._creds_lock:
                self._credentials = creds
                self._dead_token = None
                self._local = threading.local()
        finally:
            self._consent_lock.release()

    def _ensure_credentials(self) -> Credentials:
        """Resolve usable credentials without ever starting a consent flow."""
        with self._creds_lock:
            if self._credentials is not None and self._credentials.valid:
                return self._credentials

            creds: Credentials | None = None
            if self.token_path.exists():
                creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

            if creds is not None and creds.valid:
                self._credentials = creds
                return creds

            if creds is not None and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except RefreshError as exc:
                    if not is_permanent_refresh_error(exc):
                        raise  # transient (5xx/timeout): the caller's retry policy decides
                    # A revoked/expired refresh token cannot be fixed by retrying.
                    mtime = self._token_mtime()
                    message = reauthorize_message(exc)
                    self._dead_token = (mtime, message) if mtime is not None else None
                    raise GoogleDriveAuthorizationRequired(message) from exc
                # Network errors (google.auth TransportError) propagate unchanged: they
                # are transient and must not be reported as "re-authorize".
                self._write_token(creds)
                self._credentials = creds
                return creds

        reason = (
            f"'{self.token_path}' exists but is expired and cannot be refreshed; "
            "re-run authentication."
            if creds is not None
            else f"No usable token found at '{self.token_path}'."
        )
        raise GoogleDriveAuthorizationRequired(f"{reason} {_REAUTH_HINT}")

    def _run_consent_flow(self) -> Credentials:
        """Perform the interactive OAuth flow and return fresh credentials."""
        if not self.creds_path.exists():
            raise FileNotFoundError(
                f"Google Drive OAuth requires '{self.creds_path}' in the root directory."
            )

        with open(self.creds_path) as f:
            creds_data = json.load(f)

        if "web" in creds_data:
            self.logger.info(
                "Detected 'web' credentials. Generating compatible 'installed' flow..."
            )
            creds_data["installed"] = creds_data.pop("web")
            if "redirect_uris" not in creds_data["installed"]:
                creds_data["installed"]["redirect_uris"] = ["http://localhost:8080/"]
            else:
                if "http://localhost:8080/" not in creds_data["installed"]["redirect_uris"]:
                    creds_data["installed"]["redirect_uris"].append("http://localhost:8080/")

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as temp_creds:
                json.dump(creds_data, temp_creds)
                temp_creds_path = temp_creds.name

            try:
                flow = InstalledAppFlow.from_client_secrets_file(temp_creds_path, SCOPES)
                return flow.run_local_server(
                    port=_WEB_CLIENT_REDIRECT_PORT, timeout_seconds=CONSENT_TIMEOUT_SECONDS
                )
            finally:
                os.unlink(temp_creds_path)

        # Desktop ("installed") clients accept any loopback port: port 0 picks a free one,
        # so the flow cannot collide with the frontend dev server or another app on 8080.
        flow = InstalledAppFlow.from_client_secrets_file(str(self.creds_path), SCOPES)
        return flow.run_local_server(port=0, timeout_seconds=CONSENT_TIMEOUT_SECONDS)

    def _write_token(self, creds: Credentials) -> None:
        """Write the token file atomically (temp file + rename) and owner-only.

        A crash or full disk mid-write must never leave a truncated token behind: that
        would silently turn a working setup into "consent required".
        """
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.token_path.name}.", suffix=".tmp", dir=self.token_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(creds.to_json())
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(tmp_name, 0o600)
            except OSError:  # pragma: no cover - e.g. filesystems without POSIX modes
                pass
            os.replace(tmp_name, self.token_path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Per-thread API services
    # ------------------------------------------------------------------

    @property
    def drive_service(self) -> Any:
        """The Drive v3 service for the **calling thread** (built on first use)."""
        service = getattr(self._local, "drive", None)
        if service is None:
            if self._drive_service_factory is not None:
                service = self._drive_service_factory()
            else:
                service = build(
                    "drive", "v3", credentials=self._ensure_credentials(), cache_discovery=False
                )
            self._local.drive = service
        return service

    @property
    def docs_service(self) -> Any:
        """The Docs v1 service for the **calling thread** (built on first use)."""
        service = getattr(self._local, "docs", None)
        if service is None:
            if self._docs_service_factory is not None:
                service = self._docs_service_factory()
            else:
                service = build(
                    "docs", "v1", credentials=self._ensure_credentials(), cache_discovery=False
                )
            self._local.docs = service
        return service

    # ------------------------------------------------------------------
    # Drive / Docs operations (each is one idempotency-friendly API step)
    # ------------------------------------------------------------------

    def create_folder(
        self, folder_name: str, app_properties: dict[str, str] | None = None
    ) -> str:
        """Create a folder in Google Drive and return its ID.

        ``app_properties`` are private key/values (visible only to this OAuth client) that
        :meth:`find_tagged_file` can search for, so a create whose response was lost can be
        detected instead of repeated.
        """
        file_metadata: dict[str, Any] = {
            "name": folder_name,
            "mimeType": FOLDER_MIME_TYPE,
        }
        if app_properties:
            file_metadata["appProperties"] = dict(app_properties)
        file = self.drive_service.files().create(body=file_metadata, fields="id").execute()
        return file.get("id")

    def create_empty_doc(
        self,
        title: str,
        folder_id: str | None,
        app_properties: dict[str, str] | None = None,
    ) -> str:
        """Create an empty Google Doc (in ``folder_id``, or the root) and return its ID.

        Creating and filling a Doc are separate steps on purpose: the caller persists
        the id between them, and each step is retried on its own, so a failure while
        filling never creates a second Doc.
        """
        file_metadata: dict[str, Any] = {
            "name": title,
            "mimeType": DOC_MIME_TYPE,
        }
        if folder_id:
            file_metadata["parents"] = [folder_id]
        if app_properties:
            file_metadata["appProperties"] = dict(app_properties)
        doc = self.drive_service.files().create(body=file_metadata, fields="id").execute()
        return doc.get("id")

    def file_state(self, file_id: str) -> str:
        """Return ``"ok"``, ``"trashed"`` or ``"missing"`` (deleted / no longer accessible)."""
        try:
            meta = (
                self.drive_service.files()
                .get(fileId=file_id, fields="id,trashed")
                .execute()
            )
        except HttpError as exc:
            if int(getattr(exc.resp, "status", 0) or 0) == 404:
                return FILE_MISSING
            raise
        return FILE_TRASHED if meta.get("trashed") else FILE_OK

    def find_tagged_file(
        self,
        app_properties: dict[str, str],
        *,
        mime_type: str,
        parent_id: str | None = None,
    ) -> str | None:
        """Return the id of a non-trashed file this app created with ``app_properties``.

        Used before (re)creating a Drive object, so a create that succeeded on Google's
        side but whose response never arrived (timeout, connection reset, crash before
        the id was stored) is adopted instead of duplicated. ``drive.file`` scope only
        ever sees files this app created, which is exactly the search space wanted.
        """

        def quoted(value: str) -> str:
            return value.replace("\\", "\\\\").replace("'", "\\'")

        clauses = [f"mimeType = '{quoted(mime_type)}'", "trashed = false"]
        if parent_id:
            clauses.append(f"'{quoted(parent_id)}' in parents")
        for key, value in sorted(app_properties.items()):
            clauses.append(
                f"appProperties has {{ key='{quoted(key)}' and value='{quoted(value)}' }}"
            )
        response = (
            self.drive_service.files()
            .list(
                q=" and ".join(clauses),
                spaces="drive",
                fields="files(id)",
                pageSize=1,
            )
            .execute()
        )
        files = response.get("files") or []
        return files[0].get("id") if files else None

    def apply_requests(self, doc_id: str, requests: list[dict]) -> None:
        """Apply Docs ``batchUpdate`` requests to ``doc_id`` (no-op for an empty list)."""
        if not requests:
            return
        self.docs_service.documents().batchUpdate(
            documentId=doc_id, body={"requests": requests}
        ).execute()

    def rename_doc(self, file_id: str, name: str) -> None:
        """Rename the Drive file backing a Google Doc."""
        self.drive_service.files().update(
            fileId=file_id, body={"name": name}, fields="id,name"
        ).execute()

    # ------------------------------------------------------------------
    # Idempotent (in-place) publishing
    # ------------------------------------------------------------------

    def document_body_end_index(self, doc_id: str) -> int:
        """Return the ``endIndex`` of ``doc_id``'s body content.

        The index is read from the live document rather than guessed, because
        every Docs request after an insertion is relative to it and a stale
        value silently corrupts the document instead of raising.
        """
        document = self.docs_service.documents().get(documentId=doc_id).execute()
        body = document.get("body", {})
        content = body.get("content") or []
        if content:
            return content[-1].get("endIndex", 1)
        return body.get("endIndex", 1)

    def replace_doc_body(
        self,
        doc_id: str,
        requests_builder: Callable[[int], list[dict[str, Any]]],
        *,
        title: str | None = None,
    ) -> None:
        """Replace the entire body of ``doc_id`` in place, then optionally rename.

        Re-publishing an edited note must **update** the existing Doc, not
        append to it (PRD section 15, "idempotent publishing"; PRD section 19
        "already-created Docs are detected by stored IDs"). The Docs API has no
        "replace all" call, so the body range is deleted and rebuilt:

        1. ``documents().get()`` to learn the current ``endIndex``;
        2. one ``deleteContentRange`` covering ``[1, endIndex - 1)``, which
           leaves a single empty paragraph -- index 1 stays valid and the
           mandatory trailing section break is never touched. A brand-new, empty
           Doc (``endIndex == 2``) has nothing to delete, so no delete is sent;
        3. ``requests_builder(1)`` to produce the new content requests **at the
           rebuilt index**.

        The builder is a callable rather than a list because the requests are
        index-sensitive: they can only be built once the final insert index is
        known. ``src.domain.services.markdown_to_docs.markdown_to_docs``
        already takes that index, so the intended call is::

            exporter.replace_doc_body(
                doc_id,
                lambda index: markdown_to_docs(markdown, start_index=index),
                title=doc_name,
            )

        An empty request list is still sent when the builder returns ``[]``, so
        an emptied note clears its Doc instead of leaving stale content behind.
        """
        end_index = self.document_body_end_index(doc_id)

        requests: list[dict[str, Any]] = []
        if end_index > _EMPTY_DOC_END_INDEX:
            requests.append(
                {
                    "deleteContentRange": {
                        "range": {"startIndex": 1, "endIndex": end_index - 1}
                    }
                }
            )
        requests.extend(requests_builder(1))

        self.apply_requests(doc_id, requests)

        if title:
            self.rename_doc(doc_id, title)


__all__ = [
    "CONSENT_TIMEOUT_SECONDS",
    "DOC_MIME_TYPE",
    "FOLDER_MIME_TYPE",
    "SCOPES",
    "STATE_AUTHORIZED",
    "STATE_CREDENTIALS_MISSING",
    "STATE_NEEDS_CONSENT",
    "GoogleDriveAuthorizationRequired",
    "GoogleDriveExporter",
    "is_permanent_refresh_error",
    "reauthorize_message",
]

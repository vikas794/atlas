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

#: Docs ``body.content`` of a brand-new, empty document ends at index 2 (one empty paragraph).
_EMPTY_DOC_END_INDEX = 2


class GoogleDriveAuthorizationRequired(RuntimeError):
    """Raised when an operation needs Drive access that has not been granted."""


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

    def authenticate(self) -> None:
        """Run the interactive OAuth consent flow and persist the token file.

        Blocking (it waits for the browser); callers run it in a worker thread.
        """
        creds = self._run_consent_flow()
        self._write_token(creds)
        with self._creds_lock:
            self._credentials = creds
        self._local = threading.local()

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
                except Exception as exc:
                    # A revoked/expired refresh token cannot be fixed by retrying.
                    raise GoogleDriveAuthorizationRequired(
                        f"The Google token could not be refreshed ({type(exc).__name__}); "
                        f"re-run authentication. {_REAUTH_HINT}"
                    ) from exc
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
                return flow.run_local_server(port=8080)
            finally:
                os.unlink(temp_creds_path)

        flow = InstalledAppFlow.from_client_secrets_file(str(self.creds_path), SCOPES)
        return flow.run_local_server(port=8080)

    def _write_token(self, creds: Credentials) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.token_path, "w") as token:
            token.write(creds.to_json())

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

    def create_folder(self, folder_name: str) -> str:
        """Create a folder in Google Drive and return its ID."""
        file_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        file = self.drive_service.files().create(body=file_metadata, fields="id").execute()
        return file.get("id")

    def create_empty_doc(self, title: str, folder_id: str | None) -> str:
        """Create an empty Google Doc (in ``folder_id``, or the root) and return its ID.

        Creating and filling a Doc are separate steps on purpose: the caller persists
        the id between them, and each step is retried on its own, so a failure while
        filling never creates a second Doc.
        """
        file_metadata: dict[str, Any] = {
            "name": title,
            "mimeType": "application/vnd.google-apps.document",
        }
        if folder_id:
            file_metadata["parents"] = [folder_id]
        doc = self.drive_service.files().create(body=file_metadata, fields="id").execute()
        return doc.get("id")

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
    "SCOPES",
    "STATE_AUTHORIZED",
    "STATE_CREDENTIALS_MISSING",
    "STATE_NEEDS_CONSENT",
    "GoogleDriveAuthorizationRequired",
    "GoogleDriveExporter",
]

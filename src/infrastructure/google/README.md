# google

## Overview
Google Drive and Docs integration: OAuth authorization, low-level Drive/Docs operations, and the playlist publisher that writes per-video notes documents plus an index doc.

## Interface
- `google/__init__.py` exports only `GoogleDriveExporter`; the rest below is imported from `drive.py` / `publisher.py`.
- `GoogleDriveExporter` (`drive.py`, re-exported by `__init__`): `auth_state()`, `auth_status()`, `ensure_authorized()`, `authenticate()`, `mark_token_revoked(exc)`, `drive_service()`, `docs_service()`, `create_folder`, `create_empty_doc`, `file_state(file_id)`, `find_tagged_file`, `apply_requests`, `rename_doc`, `document_body_end_index`, `replace_doc_body`.
- `GoogleDriveAuthorizationRequired`, `is_permanent_refresh_error(exc)`, `reauthorize_message(exc)`.
- Constants: `SCOPES`, `STATE_CREDENTIALS_MISSING`, `STATE_NEEDS_CONSENT`, `STATE_AUTHORIZED`, `FILE_OK`, `FILE_TRASHED`, `FILE_MISSING` (the three `FILE_*` are not in `drive.py` `__all__`), `FOLDER_MIME_TYPE`, `DOC_MIME_TYPE`, `CONSENT_TIMEOUT_SECONDS`.
- `GoogleDrivePublisher` (`publisher.py`, implements `DrivePublisherPort`): `ensure_authorized()`, `publish_playlist(...)`; `is_transient_http_error(exc)`. Constructor retry knobs `max_retries=3`, `backoff_base=1.0`, `backoff_cap=10.0`.

## Invariants
- A permanently failed refresh token is reported as re-authorization required, not retried.
- Rate-limit and network errors are classed transient; others are permanent.
- `publish_playlist` raises `DriveFolderMissing` when the stored folder is trashed or missing; a trashed or missing stored index doc is silently re-created and rewritten.
- `ensure_authorized` swallows refresh and network errors; only `GoogleDriveAuthorizationRequired` becomes `DriveAuthorizationRequired`.
- Publishing is resumable: progress callbacks record created folder/doc ids so reruns reuse them; files are located by tag before creating new ones.

## Side Effects
- Network calls to Drive and Docs APIs; local OAuth consent flow (loopback redirect, port 8080 for web clients).
- Reads `credentials.json` and reads/writes `token.json`.

## Verification
- Run: `pytest` (from repo root)

"""Google Drive and Google Docs publisher adapter (PRD section 19).

Idempotency is built from three rules:

* every Drive object is reported to the caller's :class:`PublishProgress` the moment it is
  created (folder, index Doc, each video Doc), so ids are persisted *before* anything that
  could fail;
* creating a Doc and writing its content are **separate, separately retried** steps, so a
  429 while formatting re-runs only the formatting against the already-created Doc id and
  never creates a second Doc;
* a Doc that already has a stored id is updated in place.

Retry policy: Drive/Docs calls retry transient failures -- HTTP 429, 5xx, 403 rate-limit
reasons (``rateLimitExceeded`` / ``userRateLimitExceeded``) and network errors -- with the
shared exponential backoff; the limits come from configuration. Anything else fails with a
classified ``DRIVE_ERROR`` whose text is redacted.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

import httplib2
from googleapiclient.errors import HttpError

from google.auth.exceptions import RefreshError, TransportError
from src.domain.interfaces.drive_publisher import (
    DriveAuthorizationRequired,
    DrivePublisherPort,
    PublishEntry,
    PublishProgress,
    PublishResult,
)
from src.domain.models.playlist import FailureType, PipelineError
from src.domain.services.backoff import compute_delay
from src.domain.services.doc_naming import (
    doc_name,
    index_doc_name,
    playlist_folder_name,
    render_doc_markdown,
)
from src.domain.services.markdown_to_docs import markdown_to_docs, render_index_doc
from src.infrastructure.google.drive import (
    DOC_MIME_TYPE,
    FOLDER_MIME_TYPE,
    GoogleDriveAuthorizationRequired,
    GoogleDriveExporter,
    is_permanent_refresh_error,
)
from src.shared.redaction import redact_text

logger = logging.getLogger(__name__)

#: 403 reasons that mean "slow down", as opposed to a real permission problem.
_RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})


def _http_reasons(exc: HttpError) -> set[str]:
    """Extract ``error.errors[].reason`` codes from an HttpError body, best effort."""
    try:
        body = json.loads(exc.content.decode("utf-8")) if exc.content else {}
    except (ValueError, AttributeError):
        return set()
    errors = body.get("error", {}).get("errors", []) if isinstance(body, dict) else []
    return {str(item.get("reason", "")) for item in errors if isinstance(item, dict)}


def is_transient_http_error(exc: HttpError) -> bool:
    """Whether retrying this Drive/Docs HTTP error can help."""
    status = int(getattr(exc.resp, "status", 500) or 500)
    if status == 429 or 500 <= status < 600:
        return True
    return status == 403 and bool(_http_reasons(exc) & _RATE_LIMIT_REASONS)


#: Network-level failures: connection resets, timeouts, TLS errors, DNS (httplib2), and a
#: token refresh that could not reach Google (google-auth ``TransportError``).
_NETWORK_ERRORS = (OSError, httplib2.HttpLib2Error, TransportError)


class _NoProgress:
    """Progress sink used when the caller does not care."""

    async def folder_created(self, folder_id: str) -> None:
        return None

    async def index_doc_created(self, doc_id: str) -> None:
        return None

    async def index_written(self) -> None:
        return None

    async def doc_created(self, video_pk: int, doc_id: str) -> None:
        return None

    async def doc_published(self, video_pk: int, doc_id: str, *, created: bool) -> None:
        return None


class GoogleDrivePublisher(DrivePublisherPort):
    """Publishes approved notes into Google Drive folders and Docs."""

    def __init__(
        self,
        exporter: GoogleDriveExporter | None = None,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        backoff_cap: float = 10.0,
    ) -> None:
        self.exporter = exporter or GoogleDriveExporter()
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap

    async def ensure_authorized(self) -> None:
        """Fail fast (no Drive call) unless a usable Google token exists.

        A token that needs refreshing is refreshed here; if Google cannot be reached
        right now, the publish is still accepted -- the background job retries the
        refresh with backoff like any other transient Drive failure.
        """
        try:
            await asyncio.to_thread(self.exporter.ensure_authorized)
        except GoogleDriveAuthorizationRequired as exc:
            raise DriveAuthorizationRequired(str(exc)) from exc
        except (RefreshError, *_NETWORK_ERRORS) as exc:
            logger.warning(
                "Could not refresh the Google token now (%s); the publish job will retry",
                type(exc).__name__,
            )

    async def _retry_call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run a blocking Google API call in a thread, retrying transient failures."""
        attempt = 1
        while True:
            try:
                return await asyncio.to_thread(func, *args, **kwargs)
            except GoogleDriveAuthorizationRequired as exc:
                raise DriveAuthorizationRequired(str(exc)) from exc
            except RefreshError as exc:
                # Raised mid-call when the access token expires and its refresh fails.
                if is_permanent_refresh_error(exc):
                    # invalid_grant: revoked/expired consent. Retrying cannot help.
                    raise DriveAuthorizationRequired(
                        str(self.exporter.mark_token_revoked(exc))
                    ) from None
                if attempt <= self.max_retries:
                    await self._backoff(attempt, "token refresh")
                    attempt += 1
                    continue
                raise PipelineError(
                    failure_type=FailureType.DRIVE_ERROR,
                    reason=f"Google token refresh kept failing: {redact_text(str(exc))}",
                ) from None
            except HttpError as exc:
                status_code = int(getattr(exc.resp, "status", 500) or 500)
                if is_transient_http_error(exc) and attempt <= self.max_retries:
                    await self._backoff(attempt, f"HTTP {status_code}")
                    attempt += 1
                    continue
                raise PipelineError(
                    failure_type=FailureType.DRIVE_ERROR,
                    reason=(
                        f"Google Drive API failed with HTTP {status_code}: "
                        f"{redact_text(str(exc))}"
                    ),
                ) from None
            except _NETWORK_ERRORS as exc:
                if attempt <= self.max_retries:
                    await self._backoff(attempt, type(exc).__name__)
                    attempt += 1
                    continue
                raise PipelineError(
                    failure_type=FailureType.DRIVE_ERROR,
                    reason=f"Google Drive network error: {redact_text(str(exc))}",
                ) from None
            except Exception as exc:
                if isinstance(exc, DriveAuthorizationRequired | PipelineError):
                    raise
                raise PipelineError(
                    failure_type=FailureType.DRIVE_ERROR,
                    reason=f"Google Drive error: {redact_text(str(exc))}",
                ) from None

    async def _create_once(
        self,
        create: Callable[[], str],
        find: Callable[[], str | None],
        *,
        find_first: bool,
    ) -> str:
        """Create a Drive object at most once, even across ambiguous failures.

        A create can succeed on Google's side while its response is lost (timeout,
        connection reset, 5xx after commit). Retrying it blindly would leave a duplicate
        Doc or folder in Drive, so every *retry* first looks for the object by its private
        ``appProperties`` tag and adopts it. With ``find_first`` the very first attempt
        looks too, which also covers a crash between Drive creating the object and the
        database storing its id.
        """
        state = {"attempted": False}

        def attempt() -> str:
            if find_first or state["attempted"]:
                found = find()
                if found:
                    logger.info("Adopted an existing Drive object (id=%s) instead of re-creating it", found)
                    return found
            state["attempted"] = True
            return create()

        return await self._retry_call(attempt)

    async def _backoff(self, attempt: int, what: str) -> None:
        delay = compute_delay(attempt, self.backoff_base, self.backoff_cap)
        logger.warning(
            "Google Drive transient error (%s) on attempt %d/%d; retrying in %.2fs",
            what,
            attempt,
            self.max_retries,
            delay,
        )
        await asyncio.sleep(delay)

    async def publish_playlist(
        self,
        *,
        playlist_pk: int,
        playlist_title: str,
        pad_width: int,
        entries: Sequence[PublishEntry],
        index_titles: Sequence[tuple[int, str]],
        update_index: bool,
        existing_folder_id: str | None = None,
        existing_index_doc_id: str | None = None,
        progress: PublishProgress | None = None,
    ) -> PublishResult:
        """Create or update folder, index Doc, and per-video Docs (in that order)."""
        sink: PublishProgress = progress or _NoProgress()
        ctx = {"playlist_id": playlist_pk}

        # 1. Folder: create once, persist its id right away.
        folder_id = existing_folder_id
        if not folder_id:
            folder_name = playlist_folder_name(playlist_title)
            folder_tag = {"atlasKind": "playlist-folder", "atlasPlaylist": str(playlist_pk)}
            # Only retries search: a first-time search could adopt the folder of an older,
            # deleted playlist that happened to have the same database id.
            folder_id = await self._create_once(
                lambda: self.exporter.create_folder(folder_name, folder_tag),
                lambda: self.exporter.find_tagged_file(folder_tag, mime_type=FOLDER_MIME_TYPE),
                find_first=False,
            )
            await sink.folder_created(folder_id)
            logger.info("Created Drive folder (id=%s)", folder_id, extra=ctx)

        # 2. Index Doc: create empty (persist id), then write its content as its own step.
        index_doc_id = existing_index_doc_id
        idx_name = index_doc_name(playlist_title)
        if not index_doc_id:
            index_tag = {"atlasKind": "index", "atlasPlaylist": str(playlist_pk)}
            index_doc_id = await self._create_once(
                lambda: self.exporter.create_empty_doc(idx_name, folder_id, index_tag),
                lambda: self.exporter.find_tagged_file(
                    index_tag, mime_type=DOC_MIME_TYPE, parent_id=folder_id
                ),
                find_first=True,
            )
            await sink.index_doc_created(index_doc_id)
            update_index = True
            logger.info("Created Drive index Doc (id=%s)", index_doc_id, extra=ctx)
        if update_index and index_titles:
            titles = list(index_titles)
            await self._retry_call(
                self.exporter.replace_doc_body,
                index_doc_id,
                lambda start, titles=titles: render_index_doc(titles, start_index=start),
                title=idx_name,
            )
            await sink.index_written()
            logger.info("Wrote index Doc %s (%d titles)", index_doc_id, len(titles), extra=ctx)

        # 3. Video Docs in playlist order.
        created_count = 0
        updated_count = 0
        doc_ids: dict[int, str] = {}

        for entry in entries:
            name = doc_name(entry.position, entry.title, width=pad_width)
            vctx = {**ctx, "video_id": entry.video_pk}

            if entry.rename_only and entry.existing_doc_id:
                await self._retry_call(self.exporter.rename_doc, entry.existing_doc_id, name)
                doc_ids[entry.video_pk] = entry.existing_doc_id
                updated_count += 1
                logger.info("Renamed Doc %s to '%s'", entry.existing_doc_id, name, extra=vctx)
                continue

            markdown = render_doc_markdown(
                entry.position, entry.title, entry.markdown, width=pad_width
            )
            if entry.existing_doc_id:
                # Update the existing Doc in place (a retried or resumed publish lands here).
                doc_id = entry.existing_doc_id
                await self._retry_call(
                    self.exporter.replace_doc_body,
                    doc_id,
                    lambda start, md=markdown: markdown_to_docs(md, start_index=start),
                    title=name,
                )
                updated_count += 1
                created = False
                logger.info("Updated existing Doc %s", doc_id, extra=vctx)
            else:
                # Create, persist the id, THEN format: a failure while formatting leaves a
                # Doc whose id is already stored, so the retry updates it instead of
                # creating a duplicate. A Doc created earlier whose id never reached the
                # database (lost response, crash) is found by its tag and adopted.
                doc_tag = {"atlasKind": "video", "atlasVideo": str(entry.video_pk)}
                doc_id = await self._create_once(
                    lambda name=name, tag=doc_tag: self.exporter.create_empty_doc(
                        name, folder_id, tag
                    ),
                    lambda tag=doc_tag: self.exporter.find_tagged_file(
                        tag, mime_type=DOC_MIME_TYPE, parent_id=folder_id
                    ),
                    find_first=True,
                )
                await sink.doc_created(entry.video_pk, doc_id)
                # Written as a full-body replace (read the end index, clear, insert), not a
                # bare insert: if a write succeeds but its response is lost, the retry
                # rewrites the body instead of inserting the notes a second time.
                await self._retry_call(
                    self.exporter.replace_doc_body,
                    doc_id,
                    lambda start, md=markdown: markdown_to_docs(md, start_index=start),
                )
                created_count += 1
                created = True
                logger.info("Created Doc %s ('%s')", doc_id, name, extra=vctx)

            doc_ids[entry.video_pk] = doc_id
            await sink.doc_published(entry.video_pk, doc_id, created=created)

        return PublishResult(
            folder_id=folder_id,
            index_doc_id=index_doc_id,
            created=created_count,
            updated=updated_count,
            skipped=0,
            doc_ids=doc_ids,
        )

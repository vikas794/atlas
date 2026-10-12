"""Ports for Google Drive publishing.

The application layer depends only on these protocols, so the publisher can be
faked in tests and the concrete Google implementation stays in infrastructure.

Idempotency contract (PRD section 15 and 19): the publisher reports every Drive
object to a :class:`PublishProgress` sink *the moment it exists*, so the caller
persists the folder id, the index Doc id and each video Doc id immediately. A
publish that fails half-way is then resumed by simply running it again: ids that
were persisted are reused (updated in place) and nothing is created twice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from src.domain.exceptions import DomainError


@dataclass(frozen=True, slots=True)
class PublishEntry:
    """A single approved video's notes to be published or updated in Drive."""

    video_pk: int
    #: 1-based playlist position (PRD section 19: Doc numbering starts at 001).
    position: int
    title: str
    markdown: str
    existing_doc_id: str | None = None
    #: Only rename the existing Doc (the zero-pad width grew); leave its body alone.
    #: ``markdown`` is still the published notes, so a Doc that has disappeared from
    #: Drive can be re-created instead of failing the publish.
    rename_only: bool = False


@dataclass(frozen=True, slots=True)
class PublishedDoc:
    """The result of publishing one video's notes to Drive."""

    #: Drive file ID of the Google Doc. Persisted immediately after creation so
    #: a re-run updates this doc instead of creating a duplicate (PRD section 15).
    doc_id: str
    #: Final Drive filename, e.g. "007 - Streams Overview".
    name: str
    #: True when an existing doc was updated in place rather than created.
    updated: bool = False


@dataclass(frozen=True, slots=True)
class PublishResult:
    """Outcome of publishing one playlist."""

    folder_id: str
    index_doc_id: str | None = None
    created: int = 0
    updated: int = 0
    skipped: int = 0
    #: video_pk -> Drive doc id of every Doc that was created or updated.
    doc_ids: dict[int, str] = field(default_factory=dict)


class DriveAuthorizationRequired(DomainError):
    """Raised when Drive access is needed but consent has not been completed."""


class DriveFolderMissing(DomainError):
    """The playlist's stored Drive folder was deleted or moved to the trash.

    Every Doc Atlas created lives in that folder, so the caller forgets the stored
    folder/index/Doc ids and publishes the playlist again into a fresh folder.
    """

    def __init__(self, folder_id: str, state: str) -> None:
        super().__init__(f"Drive folder {folder_id} is {state}")
        self.folder_id = folder_id
        self.state = state


class PublishProgress(Protocol):
    """Sink the publisher calls as soon as each Drive object exists or is finished."""

    async def folder_created(self, folder_id: str) -> None:
        """The playlist folder was created (persist its id now)."""
        ...

    async def index_doc_created(self, doc_id: str) -> None:
        """The (still empty) index Doc was created (persist its id now)."""
        ...

    async def index_written(self) -> None:
        """The index Doc content now matches the requested titles."""
        ...

    async def doc_created(self, video_pk: int, doc_id: str) -> None:
        """A video Doc was created in Drive, before its content is written."""
        ...

    async def doc_published(self, video_pk: int, doc_id: str, *, created: bool) -> None:
        """A video Doc has its final name and content."""
        ...


class DrivePublisherPort(Protocol):
    """Publishes approved notes into a per-playlist Drive folder."""

    async def ensure_authorized(self) -> None:
        """Raise :class:`DriveAuthorizationRequired` unless a usable token exists.

        Cheap and local (no Drive call), so a publish request can fail fast with
        a clear message instead of failing later in the background worker.
        """
        ...

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
        """Create or update the folder, index Doc and per-video Docs.

        Order (PRD section 19): folder, then index Doc, then video Docs in
        playlist order.

        Args:
            playlist_pk: Internal playlist id.
            playlist_title: Used for the folder and index Doc names.
            pad_width: Zero-pad width of Doc names (digits of the playlist's
                largest 1-based position; it never shrinks).
            entries: PublishEntry instances, already approved and in order.
            index_titles: ``(position, title)`` of every video, for the index Doc.
            update_index: Whether the index Doc content must be (re)written.
            existing_folder_id: Reuse this folder instead of creating one. Raises
                :class:`DriveFolderMissing` if it was deleted or trashed in Drive.
            existing_index_doc_id: Reuse this index Doc instead of creating one.
            progress: Sink told about each Drive object as soon as it exists.

        Returns:
            A PublishResult with the folder id, index doc id and counts.
        """
        ...

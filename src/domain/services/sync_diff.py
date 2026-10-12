"""Incremental playlist re-sync diffing (PRD section 9).

Pure functions, no I/O: the caller fetches the current YouTube playlist, passes
the entries in, and gets back exactly what is new and what disappeared upstream.

Two deliberate rules from the PRD:

* nothing already stored is recomputed — existing videos keep their position and
  their pipeline stage;
* nothing is ever deleted — videos that vanished upstream are only *flagged*, so
  their published Drive docs and audit trail survive.

Because new videos are appended rather than interleaved, published Doc numbering
reflects discovery order across runs, not YouTube's live order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PlaylistEntry:
    """One video as listed by YouTube for a playlist."""

    youtube_video_id: str
    title: str = ""
    #: 1-based playlist position.
    position: int = 0


@dataclass(slots=True)
class SyncResult:
    """What an incremental re-run must do, and what it must leave alone."""

    added: list[PlaylistEntry] = field(default_factory=list)
    existing_ids: set[str] = field(default_factory=set)
    removed_youtube_ids: list[str] = field(default_factory=list)
    #: Stored videos that are still (or again) listed upstream; the caller clears
    #: their ``removed_upstream`` flag so a returning video is processed again.
    reappeared_ids: set[str] = field(default_factory=set)

    @property
    def has_changes(self) -> bool:
        """Whether the re-run has anything to do (a no-op re-run is the common case)."""
        return bool(self.added or self.removed_youtube_ids)


def diff_playlist(
    incoming: Sequence[PlaylistEntry],
    stored: Mapping[str, int],
    *,
    stored_counts: Mapping[str, int] | None = None,
    max_position: int | None = None,
) -> SyncResult:
    """Diff ``incoming`` against ``stored`` (``youtube_video_id -> first position``).

    Positions are **1-based** (PRD section 19: Doc numbering starts at 001).

    A video that appears more than once in a playlist is stored once *per
    position* but processed once (PRD section 20), so the diff counts
    occurrences: ``stored_counts`` says how many rows each video already has
    (default 1 for every stored video). An incoming video is *new* only for the
    occurrences beyond that count, and keying on the first occurrence means a
    plain re-run never re-adds the first copy of anything.

    New entries are numbered continuing from ``max_position + 1`` (default
    ``max(stored.values())``) in incoming order -- appended, never interleaved.
    """
    result = SyncResult()
    seen_counts: dict[str, int] = {}
    counts = stored_counts if stored_counts is not None else dict.fromkeys(stored, 1)
    last_position = (
        max_position if max_position is not None else max(stored.values(), default=0)
    )
    next_position = last_position + 1

    for entry in incoming:
        video_id = entry.youtube_video_id
        seen_counts[video_id] = seen_counts.get(video_id, 0) + 1
        if video_id in stored:
            result.existing_ids.add(video_id)
        if seen_counts[video_id] <= counts.get(video_id, 0):
            continue
        result.added.append(
            PlaylistEntry(
                youtube_video_id=video_id,
                title=entry.title,
                position=next_position,
            )
        )
        next_position += 1

    result.removed_youtube_ids = [video_id for video_id in stored if video_id not in seen_counts]
    result.reappeared_ids = {video_id for video_id in seen_counts if video_id in stored}
    return result

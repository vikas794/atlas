"""Incremental re-sync diffing (PRD section 9, 20). Positions are 1-based."""

from __future__ import annotations

import pytest

from src.domain.services.sync_diff import PlaylistEntry, SyncResult, diff_playlist


def entry(video_id: str, title: str = "", position: int = 0) -> PlaylistEntry:
    return PlaylistEntry(youtube_video_id=video_id, title=title or f"Video {video_id}",
                         position=position)


def ids(entries: list[PlaylistEntry]) -> list[str]:
    return [item.youtube_video_id for item in entries]


def test_first_ingest_appends_every_video_in_order_from_position_one() -> None:
    result = diff_playlist([entry("a"), entry("b"), entry("c")], {})

    assert ids(result.added) == ["a", "b", "c"]
    assert [item.position for item in result.added] == [1, 2, 3]
    assert result.existing_ids == set()
    assert result.removed_youtube_ids == []
    assert result.has_changes is True


def test_new_videos_continue_after_the_highest_stored_position() -> None:
    stored = {"a": 1, "b": 2, "c": 3}

    result = diff_playlist([entry("a"), entry("b"), entry("c"), entry("d"), entry("e")], stored)

    assert ids(result.added) == ["d", "e"]
    assert [item.position for item in result.added] == [4, 5]
    assert result.existing_ids == {"a", "b", "c"}
    assert result.removed_youtube_ids == []


def test_positions_continue_from_max_not_from_count() -> None:
    """Gaps exist by design: a published Doc number is never renumbered."""
    stored = {"a": 1, "b": 7, "c": 9}

    result = diff_playlist([entry("a"), entry("b"), entry("c"), entry("d")], stored)

    assert [item.position for item in result.added] == [10]


def test_explicit_max_position_wins_over_first_occurrence_positions() -> None:
    """Duplicate rows can sit after every first occurrence: the caller passes the true max."""
    stored = {"a": 1, "b": 2}

    result = diff_playlist(
        [entry("a"), entry("b"), entry("c")], stored, max_position=5
    )

    assert [item.position for item in result.added] == [6]


def test_duplicates_in_a_new_playlist_get_their_own_position() -> None:
    """PRD section 20: stored once per position, so numbering matches YouTube."""
    incoming = [
        entry("a", "First title"),
        entry("b", "B title"),
        entry("a", "Later duplicate"),
        entry("c", "C title"),
    ]

    result = diff_playlist(incoming, {})

    assert ids(result.added) == ["a", "b", "a", "c"]
    assert [item.position for item in result.added] == [1, 2, 3, 4]
    assert result.added[2].title == "Later duplicate"


def test_a_stored_duplicate_pair_is_a_no_op_on_resync() -> None:
    stored = {"a": 1, "b": 2}
    counts = {"a": 2, "b": 1}

    result = diff_playlist(
        [entry("a"), entry("b"), entry("a")], stored, stored_counts=counts, max_position=3
    )

    assert result.added == []
    assert result.has_changes is False


def test_a_newly_repeated_video_is_appended_as_a_new_occurrence() -> None:
    result = diff_playlist([entry("a"), entry("a")], {"a": 1}, max_position=1)

    assert ids(result.added) == ["a"]
    assert result.added[0].position == 2
    assert result.existing_ids == {"a"}


def test_removals_are_detected_without_deleting_anything() -> None:
    stored = {"a": 1, "b": 2, "gone": 3}

    result = diff_playlist([entry("a"), entry("b")], stored)

    assert result.removed_youtube_ids == ["gone"]
    assert result.added == []
    assert result.existing_ids == {"a", "b"}
    # The caller only flags them; `stored` is never mutated by a diff.
    assert stored == {"a": 1, "b": 2, "gone": 3}


def test_reappeared_videos_are_reported_so_the_removed_flag_can_be_cleared() -> None:
    stored = {"a": 1, "b": 2}

    result = diff_playlist([entry("a"), entry("b")], stored)

    assert result.reappeared_ids == {"a", "b"}


def test_resync_with_zero_changes_is_a_no_op() -> None:
    stored = {"a": 1, "b": 2, "c": 3}
    incoming = [entry("c"), entry("a"), entry("b")]

    result = diff_playlist(incoming, stored)

    assert result.added == []
    assert result.removed_youtube_ids == []
    assert result.existing_ids == {"a", "b", "c"}
    assert result.has_changes is False


def test_positions_continue_across_two_consecutive_reruns() -> None:
    stored: dict[str, int] = {}

    first = diff_playlist([entry("a"), entry("b")], stored)
    stored.update({item.youtube_video_id: item.position for item in first.added})

    second = diff_playlist(
        [entry("a"), entry("b"), entry("c"), entry("d")], stored
    )
    stored.update({item.youtube_video_id: item.position for item in second.added})

    assert ids(first.added) == ["a", "b"]
    assert ids(second.added) == ["c", "d"]
    assert [second.added[0].position, second.added[1].position] == [3, 4]

    third = diff_playlist([entry("a"), entry("b"), entry("c"), entry("d"), entry("e")], stored)

    assert ids(third.added) == ["e"]
    assert third.added[0].position == 5
    assert third.removed_youtube_ids == []


def test_removals_and_additions_in_the_same_run() -> None:
    stored = {"a": 1, "dropped": 2, "b": 3}

    result = diff_playlist([entry("a"), entry("b"), entry("new")], stored)

    assert ids(result.added) == ["new"]
    assert result.added[0].position == 4
    assert result.removed_youtube_ids == ["dropped"]
    assert result.existing_ids == {"a", "b"}


def test_removal_order_follows_stored_order_not_incoming_order() -> None:
    stored = {"x": 5, "y": 1, "z": 9}

    result = diff_playlist([entry("x")], stored)

    assert result.removed_youtube_ids == ["y", "z"]


def test_empty_incoming_removes_everything_without_deleting() -> None:
    stored = {"a": 1, "b": 2}

    result = diff_playlist([], stored)

    assert result.added == []
    assert result.existing_ids == set()
    assert result.removed_youtube_ids == ["a", "b"]
    assert stored == {"a": 1, "b": 2}


def test_incoming_position_values_are_ignored_in_favour_of_append_order() -> None:
    incoming = [entry("a", position=17), entry("b", position=3)]

    result = diff_playlist(incoming, {})

    assert [item.position for item in result.added] == [1, 2]


def test_sync_result_defaults_are_independent() -> None:
    first = SyncResult()
    second = SyncResult()

    first.added.append(entry("a"))
    first.existing_ids.add("b")

    assert second.added == []
    assert second.existing_ids == set()
    assert second.has_changes is False


def test_playlist_entry_is_immutable_and_comparable() -> None:
    original = PlaylistEntry(youtube_video_id="a", title="A", position=1)

    assert original == PlaylistEntry(youtube_video_id="a", title="A", position=1)
    with pytest.raises(AttributeError):
        original.position = 5  # type: ignore[misc]

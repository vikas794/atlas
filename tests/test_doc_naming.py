"""Drive naming scheme (PRD section 19 + section 24).

Every function here is total and deterministic: a naming mistake must never be
able to fail a publish half-way, so nothing raises and the same input always
produces the same name.
"""

from __future__ import annotations

import pytest

from src.domain.services.doc_naming import (
    DEFAULT_MAX_TITLE_LENGTH,
    FALLBACK_TITLE,
    INDEX_DOC_SLOT,
    doc_name,
    index_doc_name,
    playlist_folder_name,
    position_token,
    sanitize_drive_filename,
    sanitize_title,
)

ILLEGAL_CHARACTERS = ['/', "\\", ":", "*", "?", '"', "<", ">", "|"]


# ---------------------------------------------------------------------------
# Zero-padding width
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("playlist_size", "width"),
    [(1, 1), (9, 1), (10, 2), (99, 2), (100, 3), (250, 3)],
)
def test_zero_pad_width_is_the_digit_count_of_the_playlist_size(
    playlist_size: int, width: int
) -> None:
    token = position_token(7, playlist_size)

    assert token == str(7).zfill(width)
    assert len(token) == width


def test_single_digit_playlist_is_not_padded() -> None:
    assert doc_name(3, "Intro", 9) == "3 - Intro"


def test_ten_video_playlist_pads_to_two_digits() -> None:
    assert doc_name(3, "Intro", 10) == "03 - Intro"


def test_hundred_video_playlist_pads_to_three_digits() -> None:
    assert doc_name(3, "Intro", 100) == "003 - Intro"


def test_large_playlist_pads_to_three_digits() -> None:
    assert doc_name(42, "Intro", 250) == "042 - Intro"


def test_zero_playlist_size_still_produces_a_usable_name() -> None:
    """Total, not crashing: a caller with no playlist size yet must not fail."""
    assert position_token(1, 0) == "1"
    assert doc_name(1, "Intro", 0) == "1 - Intro"


# ---------------------------------------------------------------------------
# Gaps are preserved, never renumbered densely
# ---------------------------------------------------------------------------


def test_a_position_gap_keeps_its_own_number() -> None:
    """Position 7 of a 10-video playlist is ``07``, never renumbered to ``08``."""
    assert doc_name(7, "Deep Dive", 10) == "07 - Deep Dive"


def test_a_gap_does_not_shift_later_positions() -> None:
    assert doc_name(6, "Six", 10) == "06 - Six"
    assert doc_name(7, "Seven", 10) == "07 - Seven"
    assert doc_name(9, "Nine", 10) == "09 - Nine"


def test_a_large_gap_still_renders_the_raw_position() -> None:
    assert doc_name(97, "Later", 100) == "097 - Later"


def test_names_stay_lexicographically_ordered_across_a_gap() -> None:
    """The whole point of padding: Drive's string sort matches playlist order."""
    names = [doc_name(position, "T", 100) for position in (0, 5, 47, 98)]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("character", ILLEGAL_CHARACTERS)
def test_every_illegal_character_is_replaced_by_a_space(character: str) -> None:
    assert sanitize_title(f"a{character}b") == "a b"


@pytest.mark.parametrize("character", ILLEGAL_CHARACTERS)
def test_illegal_characters_never_reach_a_doc_name(character: str) -> None:
    name = doc_name(1, f"Episode 1{character}Part A", 10)
    assert character not in name


def test_a_title_of_only_illegal_characters_falls_back() -> None:
    assert sanitize_title("/\\:*?\"<>|") == FALLBACK_TITLE


def test_control_characters_become_spaces_like_every_other_illegal_character() -> None:
    """Same rule as ``a/b`` -> ``a b``: replace, never delete, so words stay apart."""
    assert sanitize_title("Intro\x00\x07\x1fBody") == "Intro Body"


def test_newlines_and_tabs_collapse_to_single_spaces() -> None:
    assert sanitize_title("Two\n\nlines\there") == "Two lines here"


def test_runs_of_whitespace_collapse() -> None:
    assert sanitize_title("A     B") == "A B"


def test_trailing_dots_are_stripped_because_drive_strips_them_too() -> None:
    assert sanitize_title("Episode one.") == "Episode one"


def test_leading_and_trailing_whitespace_is_trimmed() -> None:
    assert sanitize_title("   Padded   ") == "Padded"


# ---------------------------------------------------------------------------
# Empty / degenerate input never returns an empty string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title", ["", " ", "   ", "\t", "\n", "...", " . ", None, "\\", "|||"]
)
def test_blank_titles_fall_back_to_untitled(title: str | None) -> None:
    result = sanitize_title(title)  # type: ignore[arg-type]

    assert result == FALLBACK_TITLE
    assert result != ""


def test_non_string_input_is_coerced_rather_than_raising() -> None:
    assert sanitize_title(12345) == "12345"  # type: ignore[arg-type]


def test_doc_name_is_never_empty_even_without_a_title() -> None:
    assert doc_name(4, "", 10) == "04 - Untitled"


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


def test_a_long_title_is_capped_at_the_default_length() -> None:
    result = sanitize_title("A" * 500)

    assert len(result) == DEFAULT_MAX_TITLE_LENGTH
    assert result == "A" * DEFAULT_MAX_TITLE_LENGTH


def test_truncation_uses_no_ellipsis() -> None:
    result = sanitize_title("B" * 500)

    assert "..." not in result
    assert not result.endswith(".")


def test_truncation_does_not_leave_a_trailing_space() -> None:
    result = sanitize_title("word " * 100)

    assert result == result.rstrip()


def test_custom_max_length_is_honoured() -> None:
    assert sanitize_title("C" * 50, max_length=10) == "C" * 10


def test_a_max_length_below_one_still_yields_a_name() -> None:
    assert sanitize_title("Something", max_length=0) == "S"


# ---------------------------------------------------------------------------
# Index Doc naming
# ---------------------------------------------------------------------------


def test_index_doc_name_shape() -> None:
    assert index_doc_name("Python Mastery") == "00 - Index - Python Mastery"


def test_index_doc_occupies_slot_zero() -> None:
    assert index_doc_name("Any Playlist").startswith(INDEX_DOC_SLOT)


def test_index_doc_sorts_before_the_first_video_doc() -> None:
    """The folder listing must open on the index, then the videos in order."""
    assert index_doc_name("Playlist") < doc_name(1, "Anything", 100)


def test_index_doc_uses_the_index_marker_word() -> None:
    assert "Index" in index_doc_name("Playlist")


def test_index_doc_name_is_sanitized() -> None:
    assert index_doc_name("A/B: C") == "00 - Index - A B C"


def test_index_doc_name_falls_back_when_the_playlist_title_is_blank() -> None:
    assert index_doc_name("   ") == "00 - Index - Untitled"


# ---------------------------------------------------------------------------
# Playlist folder naming
# ---------------------------------------------------------------------------


def test_playlist_folder_name_is_the_sanitized_title() -> None:
    assert playlist_folder_name("Python: Mastery") == "Python Mastery"


def test_playlist_folder_name_falls_back_when_blank() -> None:
    assert playlist_folder_name("") == FALLBACK_TITLE


def test_playlist_folder_name_is_capped() -> None:
    assert len(playlist_folder_name("D" * 400)) == DEFAULT_MAX_TITLE_LENGTH


# ---------------------------------------------------------------------------
# Determinism and totality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Plain title",
        "Title with / slashes and | pipes",
        "",
        "   ",
        "A" * 300,
        "Unicode: café — naïve \U0001f600",
        "\x00\x01",
        "Trailing dots...",
    ],
)
def test_sanitizing_twice_gives_the_same_answer(title: str) -> None:
    assert sanitize_title(title) == sanitize_title(title)
    assert doc_name(1, title, 10) == doc_name(1, title, 10)


@pytest.mark.parametrize(
    "title",
    ["Plain", "/\\:*?\"<>|", "", "  ", "\n\t", "\x00", "A" * 500, "..."],
)
def test_sanitizing_is_idempotent(title: str) -> None:
    once = sanitize_title(title)
    assert sanitize_title(once) == once


def test_non_integer_position_does_not_raise() -> None:
    assert doc_name("not-a-number", "Title", 10) == "00 - Title"  # type: ignore[arg-type]


def test_negative_position_clamps_to_zero() -> None:
    assert doc_name(-5, "Title", 10) == "00 - Title"


def test_sanitize_drive_filename_matches_sanitize_title() -> None:
    """Drive callers can use the file-oriented alias interchangeably."""
    assert sanitize_drive_filename("A/B: C") == sanitize_title("A/B: C")

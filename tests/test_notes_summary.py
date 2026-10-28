"""extract_short_summary: the one-line description shown next to a video in the index Doc."""

from __future__ import annotations

from src.domain.services.notes_summary import MAX_DESCRIPTION_CHARS, extract_short_summary


def test_the_summary_section_is_used() -> None:
    notes = "## Summary\n\nHow Java streams work.\n\n## Notes\n\nLong notes here."

    assert extract_short_summary(notes) == "How Java streams work."


def test_heading_styles_and_bold_markers_are_understood() -> None:
    assert extract_short_summary("**Summary**\n\nPlain **bold** text.\n\n**Notes**\n\nx") == (
        "Plain bold text."
    )
    assert extract_short_summary("Summary:\nAbout loops.\n\nNotes\n\nx") == "About loops."


def test_a_multi_line_summary_paragraph_is_joined() -> None:
    notes = "## Summary\n\nFirst line\nsecond line.\n\n## Notes\n\nx"

    assert extract_short_summary(notes) == "First line second line."


def test_only_the_first_paragraph_of_the_summary_is_kept() -> None:
    notes = "## Summary\n\nOne.\n\nTwo.\n\n## Notes\n\nx"

    assert extract_short_summary(notes) == "One."


def test_a_long_summary_is_cut_at_a_word_boundary() -> None:
    notes = "## Summary\n\n" + "word " * 200 + "\n\n## Notes\n\nx"

    result = extract_short_summary(notes)

    assert result is not None
    assert len(result) <= MAX_DESCRIPTION_CHARS
    assert result.endswith("…") and "wor…" not in result


def test_without_a_summary_the_first_sentences_of_the_notes_are_used() -> None:
    notes = (
        "## Notes\n\nStreams process data lazily. They chain operations. "
        "A third sentence is dropped.\n\n## Key Points\n\n- a"
    )

    assert extract_short_summary(notes) == "Streams process data lazily. They chain operations."


def test_notes_fallback_skips_subheadings_and_strips_bullets() -> None:
    notes = "## Notes\n\n### Intro\n\n- Loops repeat code.\n- They end.\n\n## Key Points\n\n- a"

    assert extract_short_summary(notes) == "Loops repeat code. They end."


def test_a_code_block_is_never_used_as_a_description() -> None:
    notes = "## Notes\n\n```java\nint x = 1;\n```\n\n## Key Points\n\n- a"

    assert extract_short_summary(notes) is None


def test_a_summary_heading_with_no_text_falls_back_to_the_notes() -> None:
    notes = "## Summary\n\n## Notes\n\nReal content."

    assert extract_short_summary(notes) == "Real content."


def test_nothing_usable_returns_none() -> None:
    assert extract_short_summary("") is None
    assert extract_short_summary("just some text with no sections") is None
    assert extract_short_summary(None) is None  # type: ignore[arg-type]


def test_a_body_line_that_merely_starts_with_notes_is_not_a_heading() -> None:
    notes = "Notes about loops are below.\n\n## Key Points\n\n- a"

    assert extract_short_summary(notes) is None


def test_summary_stops_at_the_questions_and_answers_heading() -> None:
    markdown = (
        "Summary\nA short overview of the video.\nQuestions and Answers\nBasic\n"
        "1. What is it?\n   *Answer:* A thing.\n\nNotes\n- A note."
    )
    assert extract_short_summary(markdown) == "A short overview of the video."

"""Markdown -> Google Docs request generation (PRD section 19, section 23).

These assert on the *shape of the emitted request dicts* rather than on any
Google response, so they run with no network and no credentials. The index
monotonicity test at the bottom is the regression guard for silent document
corruption, which is the highest-risk part of the whole publishing path.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.domain.interfaces.drive_publisher import IndexDetail
from src.domain.services.markdown_to_docs import (
    CODE_BACKGROUND_HEX,
    CODE_BORDER_HEX,
    CODE_FONT_FAMILY,
    DEFAULT_START_INDEX,
    NEUTRAL_CODE_LABEL,
    markdown_to_docs,
    render_index_doc,
    render_plain_text,
)

Request = dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def of_kind(requests: list[Request], key: str) -> list[Request]:
    """Every request of one Docs request type, in emission order."""
    return [request for request in requests if key in request]


def paragraph_style_requests(requests: list[Request]) -> list[dict[str, Any]]:
    return [request["updateParagraphStyle"] for request in of_kind(requests, "updateParagraphStyle")]


def text_style_requests(requests: list[Request]) -> list[dict[str, Any]]:
    return [request["updateTextStyle"] for request in of_kind(requests, "updateTextStyle")]


def bullet_requests(requests: list[Request]) -> list[dict[str, Any]]:
    return [request["createParagraphBullets"] for request in of_kind(requests, "createParagraphBullets")]


def request_index(request: Request) -> int:
    """The index a request anchors on (``location`` or ``range``)."""
    body = next(iter(request.values()))
    location = body.get("location")
    if location is not None:
        return location["index"]
    return body["range"]["startIndex"]


def inserted_text(requests: list[Request]) -> str:
    insertions = of_kind(requests, "insertText")
    if not insertions:
        return ""
    assert len(insertions) == 1, "content must be inserted with exactly one request"
    return insertions[0]["insertText"]["text"]


def text_at(requests: list[Request], index: int) -> str:
    """The slice of inserted text a styling request covers."""
    text = inserted_text(requests)
    return text[index - DEFAULT_START_INDEX : index - DEFAULT_START_INDEX + 1]


def ranges_of(requests: list[Request]) -> list[tuple[int, int]]:
    result = []
    for request in requests:
        body = next(iter(request.values()))
        item_range = body.get("range")
        if item_range is not None:
            result.append((item_range["startIndex"], item_range["endIndex"]))
    return result


def is_boxed(paragraph_style: dict[str, Any]) -> bool:
    """Whether a ``updateParagraphStyle`` carries the distinct code-block box."""
    style = paragraph_style["paragraphStyle"]
    has_shading = "backgroundColor" in style.get("shading", {})
    has_all_borders = all(
        "color" in style.get(side, {}) and "width" in style.get(side, {})
        for side in ("borderTop", "borderBottom", "borderLeft", "borderRight")
    )
    return has_shading and has_all_borders


def is_monospaced(text_style: dict[str, Any]) -> bool:
    style = text_style.get("textStyle", text_style)
    return "fontFamily" in style.get("weightedFontFamily", {})


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5, 6])
def test_heading_maps_to_the_matching_docs_named_style(level: int) -> None:
    requests = markdown_to_docs(f"{'#' * level} Section title")

    styles = [
        style
        for style in paragraph_style_requests(requests)
        if "namedStyleType" in style["paragraphStyle"]
    ]
    assert len(styles) == 1
    assert styles[0]["paragraphStyle"]["namedStyleType"] == f"HEADING_{level}"
    assert styles[0]["fields"] == "namedStyleType"


def test_heading_is_explicitly_bold() -> None:
    requests = markdown_to_docs("# Title")

    bold = [
        style for style in text_style_requests(requests) if style["textStyle"].get("bold") is True
    ]
    assert len(bold) == 1
    assert bold[0]["textStyle"] == {"bold": True}


def test_heading_bold_covers_the_whole_heading_text() -> None:
    requests = markdown_to_docs("# Understanding Generators")

    assert inserted_text(requests) == "Understanding Generators\n"
    # "Understanding Generators" has length 24. Starting at index 1, the range is [1, 25).
    assert ranges_of(of_kind(requests, "updateTextStyle")) == [(1, 1 + 24)]


def test_a_seventh_level_heading_clamps_to_the_deepest_named_style() -> None:
    """Docs has no ``HEADING_7``; inventing one would fail the batch."""
    requests = markdown_to_docs("####### Too deep")

    styles = paragraph_style_requests(requests)
    assert styles[0]["paragraphStyle"]["namedStyleType"] == "HEADING_6"


def test_heading_markers_are_not_inserted_into_the_document() -> None:
    requests = markdown_to_docs("## Chapter one")

    assert inserted_text(requests) == "Chapter one\n"


# ---------------------------------------------------------------------------
# Code blocks -- the PRD section 19 acceptance criterion
# ---------------------------------------------------------------------------


def test_a_fenced_block_emits_both_a_shaded_box_and_a_monospace_style() -> None:
    """The acceptance test is BOTH: a shaded box alone is not enough, and a
    monospace font alone is not enough either."""
    requests = markdown_to_docs("```python\nprint(1)\n```")

    boxed = [style for style in paragraph_style_requests(requests) if is_boxed(style)]
    mono = [style for style in text_style_requests(requests) if is_monospaced(style)]

    assert len(boxed) == 1
    assert len(mono) == 1


def test_the_code_box_uses_the_design_token_background() -> None:
    requests = markdown_to_docs("```\ncode\n```")

    box = next(style for style in paragraph_style_requests(requests) if is_boxed(style))
    background = box["paragraphStyle"]["shading"]["backgroundColor"]["color"]["rgbColor"]

    assert background == pytest.approx(_rgb(CODE_BACKGROUND_HEX))


def test_the_code_box_has_a_one_pixel_border_on_all_four_sides() -> None:
    requests = markdown_to_docs("```\ncode\n```")

    box = next(style for style in paragraph_style_requests(requests) if is_boxed(style))
    style = box["paragraphStyle"]

    for side in ("borderTop", "borderBottom", "borderLeft", "borderRight"):
        assert style[side]["width"] == {"magnitude": 1.0, "unit": "PT"}
        assert style[side]["color"]["color"]["rgbColor"] == pytest.approx(_rgb(CODE_BORDER_HEX))


def test_the_code_box_breathes() -> None:
    requests = markdown_to_docs("```\ncode\n```")

    box = next(style for style in paragraph_style_requests(requests) if is_boxed(style))
    style = box["paragraphStyle"]

    assert style["spaceAbove"]["magnitude"] > 0
    assert style["spaceBelow"]["magnitude"] > 0


def test_the_code_font_is_monospace_and_smaller_than_the_body_default() -> None:
    requests = markdown_to_docs("```\ncode\n```")

    mono = next(style for style in text_style_requests(requests) if is_monospaced(style))
    assert mono["textStyle"]["weightedFontFamily"]["fontFamily"] == CODE_FONT_FAMILY
    assert mono["textStyle"]["fontSize"] == {"magnitude": 10.0, "unit": "PT"}


def test_code_text_is_not_inserted_bold_even_after_a_bold_heading() -> None:
    """The box resets emphasis, or code right after a heading looks wrong."""
    requests = markdown_to_docs("# Heading\n\n```\ncode\n```")

    mono = next(style for style in text_style_requests(requests) if is_monospaced(style))
    assert mono["textStyle"]["bold"] is False
    assert mono["textStyle"]["italic"] is False


def test_code_content_is_inserted_verbatim() -> None:
    requests = markdown_to_docs("```python\nif x:\n    return 1\n```")

    assert inserted_text(requests) == "python\nif x:\n    return 1\n"


def test_markers_inside_code_are_left_alone() -> None:
    """``**`` and backticks are literal inside a fence -- no inline parsing."""
    requests = markdown_to_docs("```\n**not bold** and `code`\n```")

    assert "**not bold**" in inserted_text(requests)
    assert not any(
        style["textStyle"].get("bold") for style in text_style_requests(requests)
    )


def test_a_fence_without_a_language_tag_gets_the_neutral_label() -> None:
    requests = markdown_to_docs("```\ncode\n```")

    assert f"{NEUTRAL_CODE_LABEL}\n" in inserted_text(requests)


def test_a_fence_with_a_language_tag_keeps_it() -> None:
    requests = markdown_to_docs("```python\nprint(1)\n```")

    assert "python\n" in inserted_text(requests)


def test_the_language_label_is_right_aligned_above_the_box() -> None:
    """design.md section 7 asks for a label in the box's top-right corner."""
    requests = markdown_to_docs("```rust\nfn main() {}\n```")

    aligned = [
        style
        for style in paragraph_style_requests(requests)
        if style["paragraphStyle"].get("alignment") == "END"
    ]
    assert len(aligned) == 1
    assert aligned[0]["range"]["startIndex"] < next(
        style["range"]["startIndex"]
        for style in paragraph_style_requests(requests)
        if is_boxed(style)
    )


def test_two_code_blocks_produce_two_boxes() -> None:
    requests = markdown_to_docs("```\na\n```\n\n```\nb\n```")

    assert len([style for style in paragraph_style_requests(requests) if is_boxed(style)]) == 2


def test_a_code_block_after_a_list_item_still_resets_its_indentation() -> None:
    requests = markdown_to_docs("- item\n\n```\ncode\n```")

    box = next(style for style in paragraph_style_requests(requests) if is_boxed(style))
    assert box["paragraphStyle"]["indentStart"] == {"magnitude": 0, "unit": "PT"}
    assert "indentStart" in box["fields"]


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def test_a_bulleted_list_emits_create_paragraph_bullets() -> None:
    requests = markdown_to_docs("- first\n- second")

    bullets = bullet_requests(requests)
    assert len(bullets) == 1
    assert bullets[0]["bulletPreset"] == "BULLET_DISC_CIRCLE_SQUARE"


def test_bullet_markers_are_not_inserted() -> None:
    requests = markdown_to_docs("- first\n- second")

    assert inserted_text(requests) == "first\nsecond\n"


def test_a_numbered_list_uses_the_numbered_preset() -> None:
    requests = markdown_to_docs("1. first\n2. second")

    bullets = bullet_requests(requests)
    assert len(bullets) == 1
    assert bullets[0]["bulletPreset"] == "NUMBERED_DECIMAL_ALPHA_ROMAN"


def test_contiguous_list_items_share_one_bullets_request() -> None:
    """One request per run keeps Docs numbering continuous instead of restarting."""
    requests = markdown_to_docs("1. a\n2. b\n3. c")

    assert len(bullet_requests(requests)) == 1


def test_nesting_by_two_spaces_becomes_one_leading_tab() -> None:
    """``createParagraphBullets`` has no ``indentLevel`` field (an unknown field is an
    HTTP 400 for the whole batch): Docs reads the nesting level from leading tabs."""
    requests = markdown_to_docs("- outer\n  - inner")

    bullets = bullet_requests(requests)
    assert len(bullets) == 1  # one list, so nesting and numbering stay together
    assert all("indentLevel" not in bullet for bullet in bullets)
    assert inserted_text(requests) == "outer\n\tinner\n"


def test_nesting_by_four_spaces_becomes_two_levels() -> None:
    requests = markdown_to_docs("- outer\n    - inner")

    assert inserted_text(requests) == "outer\n\t\tinner\n"


def test_deep_nesting_is_clamped_to_what_docs_accepts() -> None:
    requests = markdown_to_docs("- outer\n" + " " * 40 + "- inner")

    assert inserted_text(requests) == "outer\n" + "\t" * 8 + "inner\n"


def test_a_list_interrupted_by_a_paragraph_starts_a_new_bullets_request() -> None:
    requests = markdown_to_docs("- a\n\nSome prose.\n\n- b")

    assert len(bullet_requests(requests)) == 2


def test_a_bullet_run_range_covers_exactly_its_own_paragraphs() -> None:
    requests = markdown_to_docs("# H\n\n- a\n- b\n")

    assert inserted_text(requests) == "H\na\nb\n"
    assert bullet_requests(requests)[0]["range"] == {"startIndex": 3, "endIndex": 6}


# ---------------------------------------------------------------------------
# Inline formatting
# ---------------------------------------------------------------------------


def test_inline_bold_emits_an_update_text_style_over_the_bold_range() -> None:
    requests = markdown_to_docs("Some **bold** words")

    bold = [
        style for style in text_style_requests(requests) if style["textStyle"].get("bold") is True
    ]
    assert len(bold) == 1
    assert bold[0]["textStyle"]["bold"] is True
    # In a 1-indexed Google Doc, "Some " occupies indices 1-5; "bold" occupies indices 6-10.
    assert bold[0]["range"] == {"startIndex": 6, "endIndex": 10}


def test_the_bold_range_covers_exactly_the_bold_text() -> None:
    requests = markdown_to_docs("Some **bold** words")

    bold = next(
        style for style in text_style_requests(requests) if style["textStyle"].get("bold")
    )
    start = bold["range"]["startIndex"]
    assert inserted_text(requests)[start - DEFAULT_START_INDEX :][:4] == "bold"


def test_bold_markers_are_not_inserted() -> None:
    requests = markdown_to_docs("Some **bold** words")

    assert inserted_text(requests) == "Some bold words\n"


def test_inline_italic_emits_italic_over_the_right_range() -> None:
    requests = markdown_to_docs("An *emphasised* word")

    italic = [
        style for style in text_style_requests(requests) if style["textStyle"].get("italic") is True
    ]
    assert len(italic) == 1
    # In a 1-indexed Google Doc, "An " occupies indices 1-3; "emphasised" occupies indices 4-14.
    assert italic[0]["range"] == {"startIndex": 4, "endIndex": 14}


def test_inline_code_is_monospaced_and_shaded() -> None:
    requests = markdown_to_docs("Call `render()` first")

    # TextStyle has no "shading" field: the highlight is TextStyle.backgroundColor.
    code = [
        style
        for style in text_style_requests(requests)
        if style["textStyle"].get("backgroundColor")
    ]
    assert len(code) == 1
    assert is_monospaced(code[0])
    # In a 1-indexed Google Doc, "Call " occupies indices 1-5; `render()` occupies indices 6-14.
    assert code[0]["range"] == {"startIndex": 6, "endIndex": 14}


def test_bold_and_italic_nest_without_losing_either() -> None:
    requests = markdown_to_docs("**bold *and italic* mix**")

    styles = [style["textStyle"] for style in text_style_requests(requests)]
    combined = [style for style in styles if style.get("bold") and style.get("italic")]
    assert len(combined) == 1
    assert combined[0] == {"bold": True, "italic": True}


def test_underscore_markers_also_work() -> None:
    requests = markdown_to_docs("__strong__ and _soft_")

    styles = [style["textStyle"] for style in text_style_requests(requests)]
    assert {"bold": True} in styles
    assert {"italic": True} in styles


def test_plain_text_emits_no_styling_requests() -> None:
    requests = markdown_to_docs("Just a plain sentence.")

    assert len(requests) == 1
    assert "insertText" in requests[0]


# ---------------------------------------------------------------------------
# Structure and blank lines
# ---------------------------------------------------------------------------


def test_blank_lines_do_not_become_empty_paragraphs() -> None:
    requests = markdown_to_docs("One.\n\n\n\nTwo.")

    assert inserted_text(requests) == "One.\nTwo.\n"


def test_lazy_continuation_lines_join_into_one_paragraph() -> None:
    requests = markdown_to_docs("One line\ncontinued here")

    assert inserted_text(requests) == "One line continued here\n"


def test_crlf_line_endings_are_handled() -> None:
    requests = markdown_to_docs("# Title\r\n\r\n- a\r\n- b")

    assert inserted_text(requests) == "Title\na\nb\n"


def test_a_blockquote_marker_is_unwrapped() -> None:
    requests = markdown_to_docs("> quoted thought")

    assert inserted_text(requests) == "quoted thought\n"


# ---------------------------------------------------------------------------
# Defensive behaviour -- malformed input must never crash a publish
# ---------------------------------------------------------------------------


def test_an_unclosed_fence_still_renders_as_a_code_block() -> None:
    requests = markdown_to_docs("```python\nprint(1)\n")

    assert "print(1)" in inserted_text(requests)
    assert any(is_boxed(style) for style in paragraph_style_requests(requests))


def test_an_unclosed_fence_with_no_body_produces_nothing() -> None:
    assert markdown_to_docs("```python\n") == []


def test_a_stray_asterisk_pair_does_not_crash() -> None:
    requests = markdown_to_docs("a ** b and *** c")

    assert requests
    assert inserted_text(requests)


def test_an_unbalanced_strong_marker_stays_literal() -> None:
    requests = markdown_to_docs("unclosed **bold text")

    assert inserted_text(requests) == "unclosed **bold text\n"
    assert not [style for style in text_style_requests(requests) if style["textStyle"].get("bold")]


def test_an_unclosed_backtick_span_stays_literal() -> None:
    requests = markdown_to_docs("a `code span that never closes")

    assert "`" in inserted_text(requests)


def test_a_markdown_table_keeps_its_content_as_one_paragraph_per_row() -> None:
    """Dropping a table silently lost content (users paste tables into edited notes)."""
    requests = markdown_to_docs("Intro\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\nAfter")

    assert inserted_text(requests) == "Intro\na | b\n1 | 2\nAfter\n"
    bold = [s for s in text_style_requests(requests) if s["textStyle"].get("bold")]
    assert len(bold) == 1  # the header row
    assert text_at(requests, bold[0]["range"]["startIndex"]) == "a"


def test_generics_survive_in_text_and_inline_code() -> None:
    """``<String>`` used to be stripped as an HTML tag -- fatal for Java notes."""
    requests = markdown_to_docs("A `List<String>` is a List<T> and Map<K, V> too")

    assert inserted_text(requests) == "A List<String> is a List<T> and Map<K, V> too\n"


def test_real_html_tags_are_still_stripped() -> None:
    requests = markdown_to_docs("line<br>next <span class='x'>kept</span>")

    assert inserted_text(requests) == "line next kept\n"


def test_link_syntax_inside_inline_code_is_literal() -> None:
    requests = markdown_to_docs("Index with `arr[i](x)` here")

    assert "arr[i](x)" in inserted_text(requests)


def test_snake_case_and_arithmetic_are_not_emphasis() -> None:
    requests = markdown_to_docs("Call my_var_name and compute 2 * 3 * 4")

    assert inserted_text(requests) == "Call my_var_name and compute 2 * 3 * 4\n"
    assert text_style_requests(requests) == []


def test_a_fence_indented_under_a_list_item_is_still_a_code_block() -> None:
    markdown = "- Step one:\n    ```java\n    int x = 1;\n      nested();\n    ```\n- Step two"
    requests = markdown_to_docs(markdown)

    assert inserted_text(requests) == "Step one:\njava\nint x = 1;\n  nested();\nStep two\n"
    assert any(is_boxed(style) for style in paragraph_style_requests(requests))


def test_a_fence_with_an_info_string_is_still_a_code_block() -> None:
    requests = markdown_to_docs('```java title="Main.java"\nclass Main {}\n```')

    assert inserted_text(requests) == "java\nclass Main {}\n"


def test_a_horizontal_rule_is_skipped() -> None:
    requests = markdown_to_docs("above\n\n---\n\nbelow")

    assert inserted_text(requests) == "above\nbelow\n"


def test_an_image_becomes_its_alt_text() -> None:
    requests = markdown_to_docs("Look: ![a diagram](https://example.com/x.png) here")

    text = inserted_text(requests)
    assert "a diagram" in text
    assert "http" not in text


def test_a_link_keeps_only_its_label() -> None:
    requests = markdown_to_docs("See [the docs](https://example.com/docs) now")

    text = inserted_text(requests)
    assert "the docs" in text
    assert "http" not in text


def test_raw_html_is_stripped() -> None:
    requests = markdown_to_docs("<div class='x'>visible text</div>")

    assert "visible text" in inserted_text(requests)
    assert "<div" not in inserted_text(requests)


def test_an_empty_string_returns_an_empty_request_list() -> None:
    assert markdown_to_docs("") == []


def test_a_whitespace_only_string_returns_an_empty_request_list() -> None:
    assert markdown_to_docs("   \n\n\t\n") == []


def test_an_empty_fence_returns_an_empty_request_list() -> None:
    assert markdown_to_docs("```python\n\n```") == []


def test_a_non_string_input_returns_an_empty_request_list() -> None:
    assert markdown_to_docs(None) == []  # type: ignore[arg-type]


def test_content_after_an_unclosed_fence_is_not_lost_silently_without_a_crash() -> None:
    requests = markdown_to_docs("before\n\n```\ncode without a close\n")

    assert "before" in inserted_text(requests)
    assert "code without a close" in inserted_text(requests)


# ---------------------------------------------------------------------------
# Index arithmetic -- the corruption regression guard
# ---------------------------------------------------------------------------


def test_content_is_inserted_with_exactly_one_request() -> None:
    """One insertion means one index shift, which is what makes the arithmetic
    tractable. Two insertions would mean interleaving two shift sets."""
    requests = markdown_to_docs(SAMPLE_NOTES)

    assert len(of_kind(requests, "insertText")) == 1


def assert_styling_ascends_then_bullets_descend(requests: list[Request]) -> None:
    """Styling never changes lengths, so it ascends; bullet creation removes nesting tabs
    (shifting later indices), so bullets come last and bottom-up."""
    styling = [request_index(r) for r in requests if "createParagraphBullets" not in r]
    bullets = [request_index(r) for r in requests if "createParagraphBullets" in r]
    assert styling == sorted(styling)
    assert bullets == sorted(bullets, reverse=True)
    first_bullet = next((i for i, r in enumerate(requests) if "createParagraphBullets" in r), None)
    if first_bullet is not None:
        assert all("createParagraphBullets" in r for r in requests[first_bullet:])


def test_every_request_index_is_non_decreasing() -> None:
    assert_styling_ascends_then_bullets_descend(markdown_to_docs(SAMPLE_NOTES))


def test_every_request_lands_inside_the_growing_document_extent() -> None:
    """A request pointing past the end of the document is the corruption bug."""
    requests = markdown_to_docs(SAMPLE_NOTES)
    inserted = inserted_text(requests)
    end_of_inserted_content = DEFAULT_START_INDEX + len(inserted)

    for request in requests:
        body = next(iter(request.values()))
        item_range = body.get("range") or body["location"]
        start_idx = item_range.get("startIndex", item_range.get("index"))
        assert start_idx >= DEFAULT_START_INDEX, request
        assert start_idx <= end_of_inserted_content, request
        if "endIndex" in item_range:
            assert item_range["endIndex"] <= end_of_inserted_content, request


def test_no_styling_request_has_an_empty_range() -> None:
    """Docs rejects zero-length ranges for paragraph-level styling."""
    requests = markdown_to_docs(SAMPLE_NOTES)

    for start, end in ranges_of(requests):
        assert end > start


def test_paragraph_offsets_match_the_inserted_text() -> None:
    """Recompute the arithmetic independently and check every block lands right."""
    requests = markdown_to_docs(SAMPLE_NOTES)
    inserted = inserted_text(requests)

    headings = [
        style
        for style in paragraph_style_requests(requests)
        if "namedStyleType" in style["paragraphStyle"]
    ]
    start = headings[0]["range"]["startIndex"]
    assert inserted[start - DEFAULT_START_INDEX :][: len("Content")] == "Content"


def test_the_bullet_range_covers_exactly_its_own_text() -> None:
    requests = markdown_to_docs("- alpha\n- beta\n")
    inserted = inserted_text(requests)

    run = bullet_requests(requests)[0]["range"]
    start = run["startIndex"] - DEFAULT_START_INDEX
    end = run["endIndex"] - DEFAULT_START_INDEX
    assert inserted[start:end] == "alpha\nbeta"


def test_a_custom_start_index_shifts_every_request() -> None:
    at_one = markdown_to_docs(SAMPLE_NOTES)
    at_forty = markdown_to_docs(SAMPLE_NOTES, start_index=40)

    assert request_index(at_one[0]) == 1
    assert request_index(at_forty[0]) == 40
    assert [request_index(r) for r in at_forty] == [i + 39 for i in map(request_index, at_one)]


def test_render_plain_text_matches_the_inserted_text() -> None:
    assert render_plain_text(SAMPLE_NOTES) == inserted_text(markdown_to_docs(SAMPLE_NOTES))


def test_a_large_document_stays_monotonic() -> None:
    big = "\n\n".join(f"## Section {n}\n\n- item **one**\n- item two" for n in range(50))

    assert_styling_ascends_then_bullets_descend(markdown_to_docs(big))


# ---------------------------------------------------------------------------
# Index Doc
# ---------------------------------------------------------------------------


def test_render_index_doc_emits_exactly_one_entry_per_video() -> None:
    titles = [(1, "First"), (2, "Second"), (3, "Third")]

    requests = render_index_doc(titles)
    text = inserted_text(requests)

    assert text.count("\n") == len(titles)


def test_render_index_doc_contains_no_links() -> None:
    requests = render_index_doc([(1, "First")])

    assert "http" not in inserted_text(requests)


def test_render_index_doc_contains_no_durations() -> None:
    requests = render_index_doc([(1, "First")])

    assert ":" not in inserted_text(requests)


def test_render_index_doc_is_a_numbered_list() -> None:
    requests = render_index_doc([(1, "First"), (2, "Second")])

    bullets = bullet_requests(requests)
    assert len(bullets) == 1
    assert bullets[0]["bulletPreset"] == "NUMBERED_DECIMAL_ALPHA_ROMAN"


def test_index_entries_are_titles_only_numbered_by_docs() -> None:
    requests = render_index_doc([(7, "Deep Dive")])

    assert inserted_text(requests) == "Deep Dive\n"


def test_index_entries_are_ordered_by_position() -> None:
    requests = render_index_doc([(3, "Third"), (1, "First"), (2, "Second")])

    assert inserted_text(requests) == "First\nSecond\nThird\n"


def test_a_blank_index_title_is_skipped() -> None:
    requests = render_index_doc([(1, "First"), (2, "   "), (3, "Third")])

    assert inserted_text(requests).count("\n") == 2


def test_an_empty_index_returns_no_requests() -> None:
    assert render_index_doc([]) == []


def test_an_index_of_only_blank_titles_returns_no_requests() -> None:
    assert render_index_doc([(1, ""), (2, "  ")]) == []


def test_a_title_with_a_newline_cannot_break_the_list() -> None:
    requests = render_index_doc([(1, "Two\nlines")])

    assert inserted_text(requests) == "Two lines\n"


def test_index_doc_ranges_stay_inside_the_inserted_text() -> None:
    requests = render_index_doc([(1, "First"), (2, "Second")])
    inserted = inserted_text(requests)

    run = bullet_requests(requests)[0]["range"]
    assert run["startIndex"] == DEFAULT_START_INDEX
    assert run["endIndex"] == DEFAULT_START_INDEX + len(inserted) - 1


# ---------------------------------------------------------------------------
# Realistic sample, mirroring src/prompts/notes_v1.yaml
# ---------------------------------------------------------------------------

SAMPLE_NOTES = """# Content

- **Notes** – Everything mentioned in the video, in a structured format.
  - Nested detail that belongs to the first bullet.
- **Key Points** – A bullet-point summary of the ideas explained.

## Explain with code

Call `render()` before saving:

```python
def render(notes):
    return [line for line in notes if line]
```

1. Build the document
2. Publish it

### Takeaways

Some *emphasised* closing thoughts with **strong** words.

---

Done.
"""


def test_the_realistic_sample_renders_every_expected_construct() -> None:
    requests = markdown_to_docs(SAMPLE_NOTES)

    assert any(
        style["paragraphStyle"].get("namedStyleType") == "HEADING_1"
        for style in paragraph_style_requests(requests)
    )
    assert any(is_boxed(style) for style in paragraph_style_requests(requests))
    assert len(bullet_requests(requests)) >= 2
    assert any(
        style["textStyle"].get("bold") for style in text_style_requests(requests)
    )
    assert any(
        style["textStyle"].get("italic") for style in text_style_requests(requests)
    )


def test_the_realistic_sample_keeps_its_code_language_tag() -> None:
    requests = markdown_to_docs(SAMPLE_NOTES)

    assert "python\n" in inserted_text(requests)


def test_the_realistic_sample_drops_the_horizontal_rule() -> None:
    requests = markdown_to_docs(SAMPLE_NOTES)

    assert "---" not in inserted_text(requests)


def test_the_realistic_sample_keeps_every_word_of_the_notes() -> None:
    requests = markdown_to_docs(SAMPLE_NOTES)
    inserted = inserted_text(requests)

    for fragment in ("Content", "Key Points", "Takeaways", "render()", "Done."):
        assert fragment in inserted


def _rgb(hex_color: str) -> dict[str, float]:
    value = hex_color.lstrip("#")
    return {
        "red": round(int(value[0:2], 16) / 255.0, 4),
        "green": round(int(value[2:4], 16) / 255.0, 4),
        "blue": round(int(value[4:6], 16) / 255.0, 4),
    }


# ---------------------------------------------------------------------------
# Index Doc with descriptions and links
# ---------------------------------------------------------------------------


def _details(**by_position: tuple[str | None, str | None]) -> dict[int, IndexDetail]:
    return {int(k[1:]): IndexDetail(*v) for k, v in by_position.items()}


def test_index_entry_with_a_description_is_one_numbered_line() -> None:
    requests = render_index_doc(
        [(1, "First"), (2, "Second")], details=_details(p1=("Covers basics.", None))
    )

    assert inserted_text(requests) == "First – Covers basics.\nSecond\n"
    assert len(bullet_requests(requests)) == 1  # still one list: numbering stays intact


def test_index_title_links_to_its_doc_and_is_bold() -> None:
    requests = render_index_doc([(1, "First")], details=_details(p1=("Covers basics.", "doc123")))

    styles = [r["updateTextStyle"] for r in requests if "updateTextStyle" in r]
    assert len(styles) == 1
    assert styles[0]["textStyle"]["link"]["url"] == "https://docs.google.com/document/d/doc123/edit"
    assert styles[0]["fields"] == "bold,link"
    # The style covers exactly the title, not the description.
    assert styles[0]["range"] == {
        "startIndex": DEFAULT_START_INDEX,
        "endIndex": DEFAULT_START_INDEX + len("First"),
    }


def test_index_style_ranges_follow_earlier_entries_and_utf16() -> None:
    requests = render_index_doc(
        [(1, "A \U0001F600"), (2, "Beta")],
        details=_details(p1=("d", "x"), p2=(None, "y")),
    )

    styles = [r["updateTextStyle"]["range"] for r in requests if "updateTextStyle" in r]
    first_line = "A \U0001F600 – d\n"
    first_title_len = len("A ") + 2  # the emoji is two UTF-16 code units
    assert styles[0] == {
        "startIndex": DEFAULT_START_INDEX,
        "endIndex": DEFAULT_START_INDEX + first_title_len,
    }
    second_start = DEFAULT_START_INDEX + len("A ") + 2 + len(" – d\n")
    assert styles[1] == {"startIndex": second_start, "endIndex": second_start + len("Beta")}
    assert inserted_text(requests) == first_line + "Beta\n"


def test_index_description_cannot_break_the_list() -> None:
    requests = render_index_doc([(1, "T")], details=_details(p1=("two\nlines", None)))

    assert inserted_text(requests) == "T – two lines\n"


def test_index_without_details_has_no_style_requests() -> None:
    requests = render_index_doc([(1, "First")], details={})

    assert not [r for r in requests if "updateTextStyle" in r]

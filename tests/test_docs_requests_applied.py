"""Markdown -> Docs requests, *applied* to a model of the Docs API.

``test_markdown_to_docs.py`` asserts on request shapes. These tests go one step further:
every request is validated against the documented Docs API field names (an unknown field
is an HTTP 400 for the whole batch in real Docs) and then applied to
:class:`tests.docs_api_schema.FakeDocument`, which measures indices in UTF-16 code units
and removes nesting tabs when bullets are created, exactly like the real API. The
assertions are on the *resulting* document: which text ended up bold, which paragraphs
are bulleted at which level, and so on.
"""

from __future__ import annotations

import pytest

from src.domain.services.markdown_to_docs import (
    markdown_to_docs,
    render_index_doc,
    utf16_len,
)
from tests.docs_api_schema import FakeDocument, validate_requests

KITCHEN_SINK = """## Generics \U0001F680 in Java

Use `List<String>` and **bold \U0001F600 text** then *italic*.

1. First step
   - nested bullet with `code`
2. Second step

```java
Map<String, Integer> m = new HashMap<>(); // \U0001F600
```

- \U0001D4B3 maths **after** astral
"""

SAMPLE = """# Content

- **Notes** - Everything mentioned in the video.
  - Nested detail.
- **Key Points** - A summary.

Call `render()` before saving:

```python
def render(notes):
    return [line for line in notes if line]
```

1. Build the document
2. Publish it
"""


def apply(markdown: str) -> FakeDocument:
    doc = FakeDocument()
    doc.apply(markdown_to_docs(markdown))
    return doc


def runs_with(doc: FakeDocument, key: str) -> list[str]:
    """Maximal runs of text whose text style has a truthy ``key``."""
    runs: list[list[int]] = []
    current: list[int] = []
    for unit, style in zip(doc.units, doc.text_styles, strict=True):  # UTF-16 units
        if style.get(key) and unit != 0x0A:
            current.append(unit)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return [b"".join(u.to_bytes(2, "little") for u in run).decode("utf-16-le") for run in runs]


def test_utf16_len_counts_surrogate_pairs_twice() -> None:
    assert utf16_len("a\U0001F600b") == 4
    assert utf16_len("é") == 1


@pytest.mark.parametrize(
    "markdown",
    [SAMPLE, KITCHEN_SINK, "- a\n  - b\n    - c\n- d", "Call `x()`", "```\ncode\n```"],
)
def test_every_request_uses_only_documented_docs_api_fields(markdown: str) -> None:
    """Regression: per-side border padding maps, ``fontWeight``, ``TextStyle.shading`` and
    ``createParagraphBullets.indentLevel`` were all sent before, and each one makes the
    real Docs API reject the whole batch with HTTP 400."""
    validate_requests(markdown_to_docs(markdown))
    validate_requests(render_index_doc([(1, "A \U0001F600"), (2, "B")]))


def test_utf16_surrogate_pairs_do_not_shift_later_styling() -> None:
    doc = apply("\U0001F600\U0001F600 then **bold** and `code` \U0001D4B3 *it*")

    assert runs_with(doc, "bold") == ["bold"]
    assert runs_with(doc, "italic") == ["it"]
    assert runs_with(doc, "backgroundColor") == ["code"]
    assert doc.text().startswith("\U0001F600\U0001F600 then bold and code \U0001D4B3 it\n")


def test_kitchen_sink_lands_every_style_on_the_right_text_after_bullets_remove_tabs() -> None:
    doc = apply(KITCHEN_SINK)
    paragraphs = doc.paragraphs()
    texts = [text for text, _style, _bullet in paragraphs]

    assert "\t" not in doc.text()  # every nesting tab was consumed by createParagraphBullets
    assert texts[0] == "Generics \U0001F680 in Java"
    assert paragraphs[0][1]["namedStyleType"] == "HEADING_2"
    assert runs_with(doc, "bold") == ["Generics \U0001F680 in Java", "bold \U0001F600 text", "after"]
    assert runs_with(doc, "italic") == ["italic"]
    assert runs_with(doc, "backgroundColor") == ["List<String>", "code"]
    nesting = {text: bullet for text, _style, bullet in paragraphs if bullet is not None}
    assert nesting == {
        "First step": 0,
        "nested bullet with code": 1,
        "Second step": 0,
        "\U0001D4B3 maths after astral": 0,
    }
    code = [p for p in paragraphs if "HashMap" in p[0]]
    assert code[0][0] == "Map<String, Integer> m = new HashMap<>(); // \U0001F600"
    assert "shading" in code[0][1] and code[0][2] is None


def test_the_sample_notes_apply_cleanly() -> None:
    doc = apply(SAMPLE)

    nesting = [(t, b) for t, _s, b in doc.paragraphs() if b is not None]
    assert nesting == [
        ("Notes - Everything mentioned in the video.", 0),
        ("Nested detail.", 1),
        ("Key Points - A summary.", 0),
        ("Build the document", 0),
        ("Publish it", 0),
    ]
    assert runs_with(doc, "bold") == ["Content", "Notes", "Key Points"]


def test_an_ordered_list_with_nested_bullets_is_one_list_so_numbering_continues() -> None:
    requests = markdown_to_docs("1. one\n   - detail\n2. two\n3. three")

    assert len([r for r in requests if "createParagraphBullets" in r]) == 1


def test_index_doc_numbered_range_counts_utf16_units() -> None:
    doc = FakeDocument()
    doc.apply(render_index_doc([(1, "Intro \U0001F600"), (2, "Streams \U0001D4B3")]))

    numbered = [text for text, _style, bullet in doc.paragraphs() if bullet is not None]
    assert numbered == ["Intro \U0001F600", "Streams \U0001D4B3"]

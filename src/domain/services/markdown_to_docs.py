"""Markdown -> Google Docs ``batchUpdate`` requests (PRD section 19).

Pure transformation: Markdown in, ``list[dict]`` out. **No network calls live
here.** The module is deliberately ignorant of Google credentials; the caller
hands the request list to
:meth:`~src.infrastructure.google.drive.GoogleDriveExporter.replace_doc_body`
(PRD section 15: re-publishing an edited note must update the Doc in place).

Why the output looks the way it does
------------------------------------

Google Docs has **no native code-block style**. A "distinct shaded monospace
box" (PRD section 19, ``design.md`` section 7) therefore has to be assembled
from two primitives, deliberately:

``updateParagraphStyle``
    ``shading.backgroundColor`` from ``design.md``'s ``--surface-muted``, a 1px
    ``--border``-coloured box on all four sides, ``spaceAbove``/``spaceBelow``
    so the box breathes, and indentation reset to 0pt so a code block nested in
    a list never inherits list indentation.

``updateTextStyle``
    ``weightedFontFamily`` set to ``design.md``'s monospace, ``fontSize``
    10pt (13px at 96dpi, i.e. slightly smaller than Docs' 11pt body default),
    plus explicit ``bold: False`` / ``italic: False`` so a code block following
    a bold heading cannot inherit bold from the previous paragraph.

The shaded box, not the font name, is what makes a code block read as one —
Docs silently substitutes a default when the font is not installed. The font is
therefore a refinement, never the load-bearing part of the acceptance
criterion.

Index arithmetic
----------------

``batchUpdate`` applies requests **in order**, each one seeing the result of the
previous ones, and every inserted character shifts every later index. Getting
this wrong does not raise — it silently corrupts the document.

The module sidesteps the problem instead of simulating it:

1. The *whole* document text is rendered first, then inserted with a **single**
   ``insertText`` at ``start_index``. One insertion means exactly one shift.
2. Because that text is already fully known, the start index of every paragraph
   is a pure arithmetic offset into it — no guessing, no re-walking.
3. Every subsequent request is a *styling* request. Styling never changes a
   length, so all indices stay valid for the remainder of the batch.
4. Styling requests are emitted in ascending index order after the insertion;
   ``createParagraphBullets`` requests come last, bottom-up, because they remove
   the leading nesting tabs and so shift every later index. Indices are UTF-16
   code units (:func:`utf16_len`), not Python characters.

:func:`render_plain_text` exposes the exact string that gets inserted so a
caller — or a test — can recompute the growing document extent independently.

Robustness
-----------

The notes generator is told not to use headings, tables or emojis, but it is a
language model, so **every construct is treated as optional**. Unbalanced
``**``, unclosed fences, tables, horizontal rules, images and raw HTML are all
handled explicitly and logged at debug level rather than crashing a publish.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace

from src.domain.services.doc_naming import sanitize_drive_filename, sanitize_title

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (design.md tokens and PRD-derived geometry)
# ---------------------------------------------------------------------------

#: Index at which generated content is inserted. Matches Google's own
#: "insert at 1 into an empty document" convention and the index
#: ``GoogleDriveExporter.replace_doc_body`` rebuilds the body at.
DEFAULT_START_INDEX = 1

#: Docs' own named heading styles. Levels deeper than 6 reuse ``HEADING_6``
#: rather than inventing a style Docs does not have.
HEADING_NAMED_STYLES: dict[int, str] = {
    1: "HEADING_1",
    2: "HEADING_2",
    3: "HEADING_3",
    4: "HEADING_4",
    5: "HEADING_5",
    6: "HEADING_6",
}
DEEPEST_HEADING_LEVEL = 6

#: ``design.md`` section 7: code block background (``--surface-muted``, light).
CODE_BACKGROUND_HEX = "#F1F5F9"

#: ``design.md`` section 3.1: code block border (``--border``, light).
CODE_BORDER_HEX = "#E2E8F0"

#: ``design.md`` section 3.1: ``--text-muted``, light. Language label only.
MUTED_TEXT_HEX = "#64748B"

#: ``design.md`` section 4: the monospace family.
CODE_FONT_FAMILY = "JetBrains Mono"
CODE_FONT_WEIGHT = 400

#: 13px (design.md section 7) at 96dpi is 9.75pt and Docs' body default is
#: 11pt, so 10pt reads as "slightly smaller" without becoming hard to read.
CODE_FONT_SIZE_PT = 10.0

#: Inline code sits on a lighter tint of the code-block background.
INLINE_CODE_BACKGROUND_HEX = "#EEF2FF"

#: Language-label caption: 8pt bold, right aligned, sitting just above the box
#: in place of the CSS "label in the top-right corner" that Docs cannot do.
CODE_LABEL_FONT_SIZE_PT = 8.0
CODE_LABEL_SPACE_BELOW_PT = 2.0

#: Space above/below a code box, and the padding inside its border.
CODE_SPACE_ABOVE_PT = 6.0
CODE_SPACE_BELOW_PT = 6.0
CODE_BORDER_WIDTH_PT = 1.0
CODE_BORDER_PADDING_PT = 8.0

#: Bullet presets Docs itself offers, so the result is editable by hand.
BULLET_PRESET_DISC = "BULLET_DISC_CIRCLE_SQUARE"
BULLET_PRESET_NUMBERED = "NUMBERED_DECIMAL_ALPHA_ROMAN"

#: Docs supports nesting levels 0-8 (one leading tab each); deeper nesting is clamped.
MAX_INDENT_LEVEL = 8

#: Label for a fence with no language tag — the tag is optional, never assumed.
NEUTRAL_CODE_LABEL = "code"

#: Inline styling is skipped above this size. The pattern below is linear per
#: marker, so a pathological input (hundreds of unmatched backticks) is
#: quadratic; real notes are far below this and the cap bounds the worst case.
MAX_INLINE_PARSE_CHARS = 200_000

#: Indentation, in spaces, that counts as one level of list nesting — matches
#: the 2-space indentation the notes generator produces.
SPACES_PER_INDENT_LEVEL = 2


def _rgb(hex_color: str) -> dict[str, float]:
    """Convert ``#RRGGBB`` to the 0.0-1.0 ``RgbColor`` the Docs API expects."""
    value = hex_color.lstrip("#")
    return {
        "red": round(int(value[0:2], 16) / 255.0, 4),
        "green": round(int(value[2:4], 16) / 255.0, 4),
        "blue": round(int(value[4:6], 16) / 255.0, 4),
    }


def _optional_color(hex_color: str) -> dict[str, object]:
    return {"color": {"rgbColor": _rgb(hex_color)}}


def _pt(magnitude: float) -> dict[str, object]:
    return {"magnitude": magnitude, "unit": "PT"}


def _code_border() -> dict[str, object]:
    """A 1px solid ``--border`` box edge with padding inside it."""
    # ``ParagraphBorder.padding`` is a single ``Dimension`` (the padding on the side the
    # border is drawn), not a per-side map; a map is rejected by the Docs API with HTTP 400.
    return {
        "color": _optional_color(CODE_BORDER_HEX),
        "width": _pt(CODE_BORDER_WIDTH_PT),
        "padding": _pt(CODE_BORDER_PADDING_PT),
        "dashStyle": "SOLID",
    }


# ---------------------------------------------------------------------------
# Intermediate representation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InlineSpan:
    """A styled character range inside a :class:`Block`'s plain text.

    Offsets are relative to ``Block.text``, i.e. to the text that is actually
    inserted into the Doc — markers removed.
    """

    start: int
    end: int
    bold: bool = False
    italic: bool = False
    code: bool = False


@dataclass(frozen=True, slots=True)
class Block:
    """One Google Docs paragraph produced from the Markdown source."""

    #: ``heading`` | ``paragraph`` | ``bullet`` | ``ordered`` | ``code`` | ``code_label``
    kind: str
    text: str
    #: Heading level for ``heading``; list nesting depth for list blocks.
    level: int = 0
    language: str = ""
    spans: tuple[InlineSpan, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------

#: Any indentation is accepted (a fence nested under a list item is usually indented
#: by 2-4 spaces) and an info string after the language (```java title="x") is ignored.
_FENCE_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})[ \t]*(?P<lang>[^\s`]*)[^`]*$")
_HEADING_RE = re.compile(r"^\s{0,3}(?P<hashes>#{1,})(?:\s+|$)(?P<text>.*)$")
_HR_RE = re.compile(r"^\s{0,3}(?P<char>[-*_])(?:\s*\1){2,}\s*$")
_BULLET_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>[-*+])\s+(?P<text>\S.*)$")
_ORDERED_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<number>\d{1,9})[.)]\s+(?P<text>\S.*)$")
_QUOTE_RE = re.compile(r"^\s{0,3}>\s?(?P<text>.*)$")
_TABLE_DELIM_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|[\s:|-]*$")
_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<url>[^)]*)\)")
#: Only real HTML tags are stripped. A generic ``<[^<>]>`` pattern ate Java/C++/TS
#: generics (``List<String>``, ``Map<K, V>``) -- the bread and butter of the notes.
_HTML_RE = re.compile(
    r"</?(?:a|abbr|b|br|center|code|del|details|div|em|font|h[1-6]|hr|i|img|ins|kbd|li|"
    r"mark|ol|p|pre|s|small|span|strong|sub|summary|sup|table|tbody|td|th|thead|tr|u|ul)"
    r"(?:\s[^<>]{0,400})?/?>",
    re.IGNORECASE,
)
#: Inline code spans, which are literal: nothing inside them is ever stripped.
_CODE_SPAN_RE = re.compile(r"(`+)(?:.+?)\1", re.DOTALL)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"[ \t]+")

#: Emphasis follows CommonMark's flanking rules closely enough for notes: the opening
#: marker must be followed, and the closing marker preceded, by a non-space (so
#: ``2 * 3 * 4`` stays arithmetic), and ``_`` never opens or closes inside a word (so
#: ``snake_case_name`` keeps its underscores).
_INLINE_RE = re.compile(
    r"(?P<code>`+)(?P<code_text>.+?)(?P=code)"
    r"|\*\*(?=\S)(?P<strong_s>.+?)(?<=\S)\*\*"
    r"|(?<!\w)__(?=\S)(?P<strong_u>.+?)(?<=\S)__(?!\w)"
    r"|\*(?=[^\s*])(?P<em_s>[^*\n]+?)(?<=[^\s*])\*"
    r"|(?<!\w)_(?=[^\s_])(?P<em_u>[^_\n]+?)(?<=[^\s_])_(?!\w)",
    re.DOTALL,
)

_MAX_INLINE_DEPTH = 4


def _indent_level(indent: str) -> int:
    """Turn leading whitespace into a list nesting level (2 spaces == 1 level)."""
    spaces = len(indent.expandtabs(SPACES_PER_INDENT_LEVEL))
    return min(spaces // SPACES_PER_INDENT_LEVEL, MAX_INDENT_LEVEL)


def _strip_noise(text: str) -> str:
    """Remove images, HTML tags and the target half of Markdown links.

    ``![alt](url)`` keeps ``alt`` (a broken image icon helps nobody), ``[x](y)``
    keeps ``x``, and raw HTML tags become a space. Each is logged at debug level
    because a well-behaved notes generator never emits them.
    """

    def _image(match: re.Match[str]) -> str:
        logger.debug("markdown: dropped image markup, kept alt text")
        return match.group("alt")

    def _link(match: re.Match[str]) -> str:
        logger.debug("markdown: dropped inline link target %r", match.group("url"))
        return match.group("text")

    def _html(match: re.Match[str]) -> str:
        logger.debug("markdown: dropped raw HTML tag %r", match.group(0))
        return " "

    def _clean(segment: str) -> str:
        segment = _IMAGE_RE.sub(_image, segment)
        segment = _LINK_RE.sub(_link, segment)
        return _HTML_RE.sub(_html, segment)

    # Code spans are literal, so only the text between them is cleaned.
    pieces: list[str] = []
    cursor = 0
    for match in _CODE_SPAN_RE.finditer(text):
        pieces.append(_clean(text[cursor : match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(_clean(text[cursor:]))
    return "".join(pieces)


def _parse_inline(text: str, *, depth: int = 0) -> tuple[str, tuple[InlineSpan, ...]]:
    """Resolve inline markers into plain text plus styled spans.

    Unbalanced markers simply fail to match and survive as literal characters —
    the function is total and never raises.
    """
    if not text:
        return "", ()

    if depth >= _MAX_INLINE_DEPTH or len(text) > MAX_INLINE_PARSE_CHARS:
        logger.debug("markdown: inline parsing skipped (depth or size guard)")
        return text, ()

    pieces: list[str] = []
    spans: list[InlineSpan] = []
    length = 0
    cursor = 0

    for match in _INLINE_RE.finditer(text):
        if match.start() > cursor:
            chunk = text[cursor : match.start()]
            pieces.append(chunk)
            length += len(chunk)

        if match.group("code") is not None:
            inner, kind = match.group("code_text"), "code"
        elif match.group("strong_s") is not None or match.group("strong_u") is not None:
            inner, kind = match.group("strong_s") or match.group("strong_u"), "bold"
        else:
            inner, kind = match.group("em_s") or match.group("em_u"), "italic"

        inner = inner or ""
        start = length

        if kind == "code":
            # A code span is literal: no nesting, no further parsing.
            pieces.append(inner)
            spans.append(InlineSpan(start, start + len(inner), code=True))
        else:
            nested_text, nested_spans = _parse_inline(inner, depth=depth + 1)
            pieces.append(nested_text)
            # `**bold *and italic***` yields spans from the recursion; plain
            # `**bold**` yields none, so the whole inner range becomes the span.
            if nested_spans:
                spans.extend(
                    replace(
                        span,
                        bold=span.bold or kind == "bold",
                        italic=span.italic or kind == "italic",
                    )
                    for span in _shift(nested_spans, start)
                )
            elif inner:
                spans.append(
                    InlineSpan(
                        start=start,
                        end=start + len(nested_text),
                        bold=kind == "bold",
                        italic=kind == "italic",
                    )
                )

        length += len(pieces[-1])
        cursor = match.end()

    if cursor < len(text):
        pieces.append(text[cursor:])

    return "".join(pieces), tuple(spans)


def _shift(spans: Sequence[InlineSpan], offset: int) -> list[InlineSpan]:
    return [replace(span, start=span.start + offset, end=span.end + offset) for span in spans]


def _finalise(text: str, spans: Sequence[InlineSpan]) -> tuple[str, tuple[InlineSpan, ...]]:
    """Trim ``text`` and rebase ``spans`` onto the trimmed result.

    Trimming must not silently drop styling: ``"  **b**"`` loses two leading
    characters, so the spans move with it. A span that still does not fit is
    genuinely unrepresentable (the model wrote something like ``**a **b``) and
    is dropped with a debug log rather than emitted with an out-of-range
    ``endIndex``, which Docs would reject for the whole batch.
    """
    leading = len(text) - len(text.lstrip())
    trimmed = text.strip()
    if not trimmed:
        return "", ()

    kept: list[InlineSpan] = []
    for span in spans:
        start, end = span.start - leading, span.end - leading
        if 0 <= start < end <= len(trimmed):
            kept.append(replace(span, start=start, end=end))
        else:
            logger.debug("markdown: dropped unrepresentable inline span %r", span)
    return trimmed, tuple(kept)


def _normalise(text: str) -> str:
    """Flatten whitespace and strip control characters Docs would reject."""
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\t", " ")
    text = _CONTROL_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text)


def _inline_block(kind: str, raw_text: str, *, level: int = 0, language: str = "") -> Block | None:
    """Build a block from raw markdown, or ``None`` when nothing survives."""
    text, spans = _parse_inline(_normalise(_strip_noise(raw_text)))
    text, spans = _finalise(text, spans)
    if not text:
        return None
    return Block(kind=kind, text=text, level=level, language=language, spans=spans)


def _code_label(language: str) -> str:
    """Return the caption for a code block, falling back to a neutral label."""
    label = (language or "").strip()
    return label[:24] or NEUTRAL_CODE_LABEL


def _append_table_row(blocks: list[Block], line: str, *, header: bool) -> None:
    """Append one Markdown table row as a paragraph: cells joined by `` | ``."""
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    block = _inline_block("paragraph", " | ".join(cell for cell in cells))
    if block is None:
        return
    if header:
        block = replace(block, spans=(InlineSpan(0, len(block.text), bold=True),))
    blocks.append(block)


def extract_blocks(markdown: str) -> list[Block]:
    """Parse Markdown into :class:`Block` objects, one per Docs paragraph.

    Lazy continuation lines (two non-blank lines in a row) join into a single
    paragraph, as CommonMark specifies. Constructs the notes prompt explicitly
    rules out are degraded rather than rendered as noise: horizontal rules are
    skipped, images keep their alt text, known HTML tags are stripped, and table
    rows become plain paragraphs (cells joined by `` | ``) so no content is lost.
    """
    if not isinstance(markdown, str) or not markdown.strip():
        return []

    blocks: list[Block] = []
    pending: list[str] = []
    previous_had_pipe = False
    fence = ""
    fence_lang = ""
    fence_indent = 0
    fence_lines: list[str] = []

    def flush_pending() -> None:
        if not pending:
            return
        raw = " ".join(pending)
        pending.clear()
        block = _inline_block("paragraph", raw)
        if block is not None:
            blocks.append(block)

    def close_fence(*, unterminated: bool = False) -> None:
        nonlocal fence, fence_lang, fence_lines
        lines = fence_lines
        fence_lines = []
        fence = ""
        if unterminated:
            logger.debug("markdown: unclosed code fence, rendering what we have")
        if not lines or not any(line.strip() for line in lines):
            logger.debug("markdown: empty code block dropped")
            return
        label = _code_label(fence_lang)
        fence_lang = ""
        blocks.append(Block(kind="code_label", text=label))
        blocks.append(
            Block(kind="code", text="\n".join(line.rstrip() for line in lines), language=label)
        )

    for raw_line in markdown.split("\n"):
        line = raw_line.rstrip()

        if fence:
            closing = _FENCE_RE.match(line)
            if (
                closing
                and closing.group("fence")[0] == fence[0]
                and len(closing.group("fence")) >= len(fence)
                and not closing.group("lang")
            ):
                close_fence()
            else:
                # Drop the fence's own indentation (a fence nested in a list item),
                # but never more than the line's leading whitespace.
                if fence_indent:
                    expanded = line.expandtabs(4)
                    strip = min(fence_indent, len(expanded) - len(expanded.lstrip(" ")))
                    fence_lines.append(expanded[strip:])
                else:
                    fence_lines.append(line)
            continue

        opening = _FENCE_RE.match(line)
        if opening:
            flush_pending()
            fence = opening.group("fence")
            fence_lang = opening.group("lang")
            fence_indent = len(opening.group("indent").expandtabs(4))
            fence_lines = []
            previous_had_pipe = False
            continue

        if not line.strip():
            flush_pending()
            previous_had_pipe = False
            continue

        if _TABLE_DELIM_RE.match(line) and "|" in line:
            # Docs tables need a structural insert whose index math is far riskier
            # than plain text, so a table becomes one paragraph per row with the
            # cells joined by " | " (the header row bold). Its content is kept,
            # never silently dropped -- users may paste tables into edited notes.
            logger.debug("markdown: rendering table rows as text")
            header = pending.pop() if pending and "|" in pending[-1] else None
            flush_pending()
            if header is not None:
                _append_table_row(blocks, header, header=True)
            previous_had_pipe = True
            continue

        if previous_had_pipe:
            if "|" in line:
                _append_table_row(blocks, line, header=False)
                continue
            previous_had_pipe = False

        if _HR_RE.match(line):
            flush_pending()
            logger.debug("markdown: dropped horizontal rule")
            previous_had_pipe = False
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush_pending()
            block = _inline_block(
                "heading",
                heading.group("text"),
                level=min(len(heading.group("hashes")), DEEPEST_HEADING_LEVEL),
            )
            if block is not None:
                blocks.append(block)
            previous_had_pipe = False
            continue

        matched = False
        for pattern, kind in ((_BULLET_RE, "bullet"), (_ORDERED_RE, "ordered")):
            match = pattern.match(line)
            if not match:
                continue
            flush_pending()
            block = _inline_block(kind, match.group("text"), level=_indent_level(match.group("indent")))
            if block is not None:
                blocks.append(block)
            matched = True
            break
        if matched:
            previous_had_pipe = False
            continue

        quoted = _QUOTE_RE.match(line)
        if quoted:
            logger.debug("markdown: unwrapped blockquote marker")
            flush_pending()
            block = _inline_block("paragraph", quoted.group("text"))
            if block is not None:
                blocks.append(block)
            previous_had_pipe = False
            continue

        pending.append(_strip_noise(line))
        previous_had_pipe = "|" in line

    if fence:
        close_fence(unterminated=True)
    flush_pending()
    return blocks


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def utf16_len(text: str) -> int:
    """Length of ``text`` in UTF-16 code units -- the unit of every Docs API index.

    Python's ``len`` counts code points, so any character outside the Basic
    Multilingual Plane (emoji, mathematical alphanumerics, some CJK) is one
    Python character but **two** Docs index units. Using ``len`` would shift every
    later range by one per such character: styling lands on the wrong text and
    a range can run past the end of the document, which rejects the whole batch.
    """
    return len(text.encode("utf-16-le")) // 2


def _is_list(block: Block) -> bool:
    return block.kind in ("bullet", "ordered")


def _inserted(block: Block) -> str:
    """The paragraph text actually inserted for ``block`` (without its newline).

    List items carry one leading tab per nesting level: ``createParagraphBullets``
    derives the nesting level from leading tabs and then removes them (it has no
    ``indentLevel`` field).
    """
    if _is_list(block):
        return "\t" * block.level + block.text
    return block.text


def render_plain_text(markdown: str) -> str:
    """Return the exact string :func:`markdown_to_docs` inserts.

    Every block contributes its text plus one ``\\n`` (list items are prefixed by
    one tab per nesting level, which Docs removes when it creates the bullets).
    Exposed so callers and tests can recompute paragraph offsets without
    duplicating the parser. Offsets must be measured with :func:`utf16_len`.
    """
    return "".join(f"{_inserted(block)}\n" for block in extract_blocks(markdown))


def _span_requests(block: Block, base: int) -> Iterator[dict]:
    """Emit an ``updateTextStyle`` request per styled inline span."""
    for span in block.spans:
        style: dict[str, object] = {}
        fields: list[str] = []
        if span.bold:
            style["bold"] = True
            fields.append("bold")
        if span.italic:
            style["italic"] = True
            fields.append("italic")
        if span.code:
            style["weightedFontFamily"] = {
                "fontFamily": CODE_FONT_FAMILY,
                "weight": CODE_FONT_WEIGHT,
            }
            style["fontSize"] = _pt(CODE_FONT_SIZE_PT)
            # ``TextStyle`` has no ``shading``; text highlight is ``backgroundColor``.
            style["backgroundColor"] = _optional_color(INLINE_CODE_BACKGROUND_HEX)
            fields.extend(("weightedFontFamily", "fontSize", "backgroundColor"))
        if not fields:
            continue
        yield {
            "updateTextStyle": {
                "range": {
                    "startIndex": base + utf16_len(block.text[: span.start]),
                    "endIndex": base + utf16_len(block.text[: span.end]),
                },
                "textStyle": style,
                "fields": ",".join(fields),
            }
        }


def _block_requests(block: Block, base: int) -> Iterator[dict]:
    """Emit the styling requests for one non-list block."""
    full_range = {"startIndex": base, "endIndex": base + utf16_len(block.text)}

    if block.kind == "heading":
        yield {
            "updateParagraphStyle": {
                "range": full_range,
                "paragraphStyle": {
                    "namedStyleType": HEADING_NAMED_STYLES.get(
                        block.level, HEADING_NAMED_STYLES[DEEPEST_HEADING_LEVEL]
                    )
                },
                "fields": "namedStyleType",
            }
        }
        # Named heading styles are already bold; asserted explicitly so notes
        # pasted from a bold-styled source still read as headings.
        yield {
            "updateTextStyle": {
                "range": full_range,
                "textStyle": {"bold": True},
                "fields": "bold",
            }
        }

    elif block.kind == "code_label":
        # design.md section 7 asks for a language label in the box's top-right
        # corner. Docs has no floating label, so it is a right-aligned caption
        # paragraph immediately above the box — same position, no overlay.
        yield {
            "updateParagraphStyle": {
                "range": full_range,
                "paragraphStyle": {
                    "alignment": "END",
                    "spaceBelow": _pt(CODE_LABEL_SPACE_BELOW_PT),
                },
                "fields": "alignment,spaceBelow",
            }
        }
        yield {
            "updateTextStyle": {
                "range": full_range,
                "textStyle": {
                    "fontSize": _pt(CODE_LABEL_FONT_SIZE_PT),
                    "foregroundColor": _optional_color(MUTED_TEXT_HEX),
                },
                "fields": "fontSize,foregroundColor",
            }
        }

    elif block.kind == "code":
        border = _code_border()
        yield {
            "updateParagraphStyle": {
                "range": full_range,
                "paragraphStyle": {
                    "shading": {"backgroundColor": _optional_color(CODE_BACKGROUND_HEX)},
                    "borderTop": border,
                    "borderBottom": dict(border),
                    "borderLeft": dict(border),
                    "borderRight": dict(border),
                    "spaceAbove": _pt(CODE_SPACE_ABOVE_PT),
                    "spaceBelow": _pt(CODE_SPACE_BELOW_PT),
                    # A fenced block inside a list must not inherit indentation.
                    "indentStart": _pt(0),
                    "indentFirstLine": _pt(0),
                },
                "fields": (
                    "shading,borderTop,borderBottom,borderLeft,borderRight,"
                    "spaceAbove,spaceBelow,indentStart,indentFirstLine"
                ),
            }
        }
        yield {
            "updateTextStyle": {
                "range": full_range,
                "textStyle": {
                    "weightedFontFamily": {
                        "fontFamily": CODE_FONT_FAMILY,
                        "weight": CODE_FONT_WEIGHT,
                    },
                    "fontSize": _pt(CODE_FONT_SIZE_PT),
                    # Neutralise inherited emphasis: a code block right after a
                    # bold heading must not render bold.
                    "bold": False,
                    "italic": False,
                },
                "fields": "weightedFontFamily,fontSize,bold,italic",
            }
        }
        return

    yield from _span_requests(block, base)


def _list_run_end(blocks: Sequence[Block], start: int) -> int:
    """Return the index of the last block in the list run beginning at ``start``.

    A run is every contiguous list item, nested ones included, so that one
    ``createParagraphBullets`` request covers it: Docs then numbers an ordered
    list continuously across its nested items instead of restarting at 1. A
    top-level item of the other list kind (bullets <-> numbers) starts a new run.
    """
    head_kind = blocks[start].kind
    end = start
    while end + 1 < len(blocks):
        nxt = blocks[end + 1]
        if not _is_list(nxt) or (nxt.level == 0 and nxt.kind != head_kind):
            break
        end += 1
    return end


def markdown_to_docs(markdown: str, *, start_index: int = DEFAULT_START_INDEX) -> list[dict]:
    """Convert Markdown into Google Docs ``batchUpdate`` requests.

    Order: the single ``insertText`` first, then every styling request in
    ascending index order (styling never changes a length), then the
    ``createParagraphBullets`` requests in **descending** index order. Bullet
    creation removes the leading nesting tabs, which shifts every later index;
    applying the bottom-most list first means no request ever refers to an
    index that an earlier request has already moved.

    All indices are UTF-16 code units (see :func:`utf16_len`).

    Returns ``[]`` for input that produces no text at all (empty string,
    whitespace only, or a fence with no body) rather than an invalid empty
    ``insertText``.
    """
    blocks = extract_blocks(markdown)
    if not blocks:
        return []

    text = "".join(f"{_inserted(block)}\n" for block in blocks)
    requests: list[dict] = [
        {"insertText": {"location": {"index": start_index}, "text": text}}
    ]

    # Pure arithmetic: each paragraph starts where the previous one ended, plus
    # the newline that terminates it.
    offsets: list[int] = []
    cursor = start_index
    for block in blocks:
        offsets.append(cursor)
        cursor += utf16_len(_inserted(block)) + 1

    bullets: list[dict] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]

        if _is_list(block):
            run_end = _list_run_end(blocks, index)
            bullets.append(
                {
                    "createParagraphBullets": {
                        "range": {
                            "startIndex": offsets[index],
                            "endIndex": offsets[run_end] + utf16_len(_inserted(blocks[run_end])),
                        },
                        "bulletPreset": (
                            BULLET_PRESET_DISC if block.kind == "bullet" else BULLET_PRESET_NUMBERED
                        ),
                    }
                }
            )
            for item in range(index, run_end + 1):
                # Inline styles are applied before the bullets remove the tabs, so
                # they are anchored after the tabs and move with the text.
                requests.extend(_span_requests(blocks[item], offsets[item] + blocks[item].level))
            index = run_end + 1
            continue

        requests.extend(_block_requests(block, offsets[index]))
        index += 1

    requests.extend(reversed(bullets))
    return requests


def render_index_doc(
    titles: Sequence[tuple[int, str]], *, start_index: int = DEFAULT_START_INDEX
) -> list[dict]:
    """Render the playlist index Doc: a numbered list of video titles only.

    PRD section 19 -- titles only, no links, no durations. The text of each
    entry is just the title; Google Docs numbers the list ``1..N`` itself, which
    matches the Doc numbering because the caller lists the playlist's videos in
    order without gaps (positions are dense and 1-based).

    Entries are emitted in ascending ``position`` order (stable for ties), and
    blank titles are skipped after whitespace flattening so one bad row cannot
    break the list.
    """
    entries: list[tuple[int, str]] = []
    for position, title in titles:
        # Newlines in a title would split one index entry into two paragraphs,
        # so flatten before anything else.
        flat = _normalise(str(title or "").replace("\r", " ").replace("\n", " ")).strip()
        if not flat:
            logger.debug("index: skipped blank title at position %r", position)
            continue
        entries.append((int(position), flat))

    if not entries:
        return []

    entries.sort(key=lambda item: item[0])
    text = "".join(f"{title}\n" for _position, title in entries)

    return [
        {"insertText": {"location": {"index": start_index}, "text": text}},
        {
            "createParagraphBullets": {
                "range": {
                    "startIndex": start_index,
                    "endIndex": start_index + utf16_len(text) - 1,
                },
                "bulletPreset": BULLET_PRESET_NUMBERED,
            }
        },
    ]


__all__ = [
    "CODE_BACKGROUND_HEX",
    "CODE_BORDER_HEX",
    "CODE_FONT_FAMILY",
    "CODE_FONT_SIZE_PT",
    "DEFAULT_START_INDEX",
    "NEUTRAL_CODE_LABEL",
    "Block",
    "InlineSpan",
    "extract_blocks",
    "markdown_to_docs",
    "render_index_doc",
    "render_plain_text",
    "sanitize_drive_filename",
    "utf16_len",
    "sanitize_title",
]

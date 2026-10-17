"""Short per-video description pulled out of the generated notes Markdown.

The notes prompt (``notes_v1_2``) asks the model for a ``Summary`` section at the top:
one or two plain sentences saying what the video covers. The Drive index Doc shows that
text next to each video's title and link, so it is read from the stored notes at publish
time (an edit to the Summary therefore reaches the index on the next publish, and no extra
AI call or database column is needed).

Notes written before the Summary section existed, or by a custom prompt that omits it,
fall back to the first sentences of the ``Notes`` section; ``None`` means nothing usable.
Pure text processing: no I/O.
"""

from __future__ import annotations

import re

#: Longest description written to the index; longer text is cut at a word boundary.
MAX_DESCRIPTION_CHARS = 280

#: Sentences kept when the description is derived from the ``Notes`` fallback.
_FALLBACK_SENTENCES = 2

#: A section heading line on its own (``## Summary``, ``**Summary**``, ``Summary:``).
_SECTION_HEADING = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:[-*][ \t]+)?(?:\*\*|__)?[ \t]*"
    r"(?P<name>Summary|Notes|Key Points|Important Details|Takeaways)"
    r"[ \t]*:?[ \t]*(?:\*\*|__)?[ \t]*:?[ \t]*$",
    re.IGNORECASE,
)
_MARKDOWN_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+\S")
_LIST_MARKER = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _clean_line(line: str) -> str:
    text = _LIST_MARKER.sub("", line)
    for marker in ("**", "__", "`"):
        text = text.replace(marker, "")
    return text.strip()


def _first_paragraph(lines: list[str], wanted: str) -> str:
    """First paragraph under the ``wanted`` section heading, flattened to one line."""
    start = None
    for index, line in enumerate(lines):
        match = _SECTION_HEADING.match(line)
        if match and match.group("name").lower() == wanted:
            start = index + 1
            break
    if start is None:
        return ""

    collected: list[str] = []
    for line in lines[start:]:
        if line.lstrip().startswith("```"):
            break
        if _SECTION_HEADING.match(line):
            break
        if not line.strip():
            if collected:
                break
            continue
        if _MARKDOWN_HEADING.match(line):
            # A sub-heading before any text (e.g. "### Intro"): skip it, keep looking.
            if collected:
                break
            continue
        cleaned = _clean_line(line)
        if cleaned:
            collected.append(cleaned)
    return " ".join(collected)


def _shorten(text: str, *, sentences: int | None, limit: int) -> str:
    text = " ".join(text.split())
    if sentences is not None:
        kept = ""
        for sentence in _SENTENCE_END.split(text)[:sentences]:
            candidate = f"{kept} {sentence}".strip()
            if kept and len(candidate) > limit:
                break
            kept = candidate
        text = kept
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{cut or text[: limit - 1]}…"


def extract_short_summary(markdown: str, *, limit: int = MAX_DESCRIPTION_CHARS) -> str | None:
    """Return the one-line description of a video's notes, or ``None`` if there is none."""
    lines = (markdown or "").splitlines()
    summary = _first_paragraph(lines, "summary")
    if summary:
        return _shorten(summary, sentences=None, limit=limit)
    fallback = _first_paragraph(lines, "notes")
    if fallback:
        return _shorten(fallback, sentences=_FALLBACK_SENTENCES, limit=limit)
    return None


__all__ = ["MAX_DESCRIPTION_CHARS", "extract_short_summary"]

"""Deterministic Drive naming for playlists, video Docs and the index Doc.

Pure functions, no I/O (PRD section 19 + section 24). Every function is *total*:
it returns a usable string for any input whatsoever and never raises, because a
naming mistake must never be able to fail a publish half-way.

The scheme, and why:

``{position:0{width}d} - {title}``
    Positions are **1-based** (the first video is ``001``) and are stored that
    way, so callers pass them through unchanged. ``width`` is the number of
    digits of the playlist's *largest position*, so a playlist whose last video
    is #100 pads to three digits and Drive's lexicographic sort reproduces
    playlist order. Basing the width on the largest position (not the video
    count) keeps it stable even when positions have gaps. Positions are never
    renumbered: a Doc's number is stable for the life of the playlist.

    Growth (99 -> 100 videos) changes the width, which would leave ``99 - x``
    next to ``100 - y`` and break the sort. The publish use case remembers the
    width its published Docs use (``playlists.pad_width``; it never shrinks) and,
    only when the width has actually grown, *renames* the already-published
    Docs to the new width. Numbers never change, only their zero padding, and
    nothing is renamed while the width is unchanged.

``00 - Index - {playlist title}``
    Slot ``00`` is reserved for the index Doc, so it sorts ahead of the first
    video (``01 - ...``). Putting the literal word ``Index`` immediately after
    the slot — rather than after the playlist title — means two index Docs in
    the same account still cluster together at the top of their folder, and the
    reservation is legible in the Drive UI. The one caveat is a playlist whose
    stored positions are 0-based *and* whose first video is titled so that it
    out-sorts ``Index``; positions are 1-based, as the PRD's "playlist order
    number" describes.

All Drive-facing names pass through :func:`sanitize_title`, which strips the
characters Drive handles badly, collapses whitespace and caps the length.
"""

from __future__ import annotations

import re

#: Separator between the order number and the human-readable part of a name.
NAME_SEPARATOR = " - "

#: Reserved zero-padded slot occupied by the index Doc inside a playlist folder.
INDEX_DOC_SLOT = "00"

#: Marker word for the index Doc, placed right after the reserved slot.
INDEX_DOC_MARKER = "Index"

#: Returned whenever sanitization leaves nothing behind. Never an empty name.
FALLBACK_TITLE = "Untitled"

#: Cap on the human-readable part of a name (PRD section 19: Drive handles very
#: long names poorly, and a 100-character title is already unreadable).
DEFAULT_MAX_TITLE_LENGTH = 100

#: Drive handles these badly (they are shell/Windows metacharacters or field
#: separators), plus the C0/C1 control characters that are never legal in a
#: file name. Each is replaced by a space so ``a/b`` becomes ``a b`` rather than
#: the misleading ``ab``.
_ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f-\x9f]')

_WHITESPACE_RE = re.compile(r"\s+")


def _as_int(value: object, default: int = 0) -> int:
    """Best-effort ``int()`` that never raises, so naming stays total."""
    try:
        return int(value)  # type: ignore[call-type]
    except (TypeError, ValueError):
        return default


def sanitize_title(title: str, *, max_length: int = DEFAULT_MAX_TITLE_LENGTH) -> str:
    """Return ``title`` made safe for a Drive file or folder name.

    Illegal and control characters become a space, runs of whitespace collapse
    to one space, trailing dots and spaces are dropped (Drive and Windows both
    strip them, which would make the visible name differ from the stored one),
    and the result is capped at ``max_length`` characters without an ellipsis.
    Returns :data:`FALLBACK_TITLE` rather than an empty string.
    """
    if isinstance(title, str):
        text = title
    elif title is None:
        text = ""
    else:
        text = str(title)

    text = _ILLEGAL_CHARS_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = text.rstrip(". ")

    limit = max(1, _as_int(max_length, DEFAULT_MAX_TITLE_LENGTH))
    if len(text) > limit:
        text = text[:limit].rstrip(". ")

    return text or FALLBACK_TITLE


def sanitize_drive_filename(name: str) -> str:
    """Drive-facing alias of :func:`sanitize_title` for callers that think in
    file names rather than titles."""
    return sanitize_title(name)


def pad_width(max_position: int) -> int:
    """Number of digits needed to zero-pad every position up to ``max_position``."""
    size = _as_int(max_position)
    return len(str(size)) if size > 0 else 1


def position_token(position: int, playlist_size: int | None = None, *, width: int | None = None) -> str:
    """Return the zero-padded order number used in a Doc name.

    ``playlist_size`` is the playlist's largest 1-based position (see the module
    docstring); its digit count is the pad width, so every order number in a
    folder has the same width and Drive sorts them numerically. Pass ``width``
    to force an explicit width instead (used when the stored width must not shrink).
    """
    use_width = width if width is not None else pad_width(playlist_size or 0)
    return f"{max(_as_int(position), 0):0{max(_as_int(use_width, 1), 1)}d}"


def doc_name(position: int, title: str, playlist_size: int | None = None, *, width: int | None = None) -> str:
    """Return the Drive name of a video Doc, e.g. ``"007 - Deep Dive"``.

    ``position`` is used **raw** (1-based, never renumbered).
    """
    token = position_token(position, playlist_size, width=width)
    return f"{token}{NAME_SEPARATOR}{sanitize_title(title)}"


def doc_heading(position: int, title: str, playlist_size: int | None = None, *, width: int | None = None) -> str:
    """Return the Heading 1 text that opens every published Doc (PRD section 6 FR6).

    Identical to the Doc's file name, e.g. ``"012 - Streams Overview"``: the
    sequence number and the video title.
    """
    return doc_name(position, title, playlist_size, width=width)


def render_doc_markdown(
    position: int, title: str, notes: str, *, width: int | None = None, playlist_size: int | None = None
) -> str:
    """Return the Markdown of a published Doc: a ``# 012 - Title`` heading, then the notes.

    The notes prompt tells the model *not* to repeat the title, so the heading
    supplied here is the only title in the Doc.
    """
    heading = doc_heading(position, title, playlist_size, width=width)
    return f"# {heading}\n\n{notes.strip()}\n"


def index_doc_name(playlist_title: str) -> str:
    """Return the Drive name of a playlist's index Doc.

    ``"00 - Index - {playlist title}"`` — slot ``00`` is reserved so the index
    sorts first inside the folder, ahead of every ``01 - ...`` video Doc.
    """
    marker = f"{INDEX_DOC_SLOT}{NAME_SEPARATOR}{INDEX_DOC_MARKER}"
    return f"{marker}{NAME_SEPARATOR}{sanitize_title(playlist_title)}"


def playlist_folder_name(playlist_title: str) -> str:
    """Return the Drive name of a playlist's folder."""
    return sanitize_title(playlist_title)


__all__ = [
    "DEFAULT_MAX_TITLE_LENGTH",
    "FALLBACK_TITLE",
    "INDEX_DOC_MARKER",
    "INDEX_DOC_SLOT",
    "NAME_SEPARATOR",
    "doc_heading",
    "doc_name",
    "index_doc_name",
    "pad_width",
    "playlist_folder_name",
    "position_token",
    "render_doc_markdown",
    "sanitize_drive_filename",
    "sanitize_title",
]

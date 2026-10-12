"""A strict subset of the Google Docs API v1 request schema, for tests.

The Docs API rejects an *entire* ``batchUpdate`` with HTTP 400 when any request carries
an unknown field name (e.g. ``indentLevel`` on ``createParagraphBullets`` or ``shading``
on a ``TextStyle``). A stub that accepts any dict hides that class of bug completely, so
every request Atlas builds is checked against the field names documented at
https://developers.google.com/docs/api/reference/rest/v1/documents/request and
https://developers.google.com/docs/api/reference/rest/v1/documents .

Only the request types and objects Atlas uses are modelled; anything else fails loudly.
"""

from __future__ import annotations

from typing import Any

_DIMENSION = {"magnitude": None, "unit": None}
_RGB = {"red": None, "green": None, "blue": None}
_OPTIONAL_COLOR = {"color": {"rgbColor": _RGB}}
_RANGE = {"startIndex": None, "endIndex": None, "segmentId": None, "tabId": None}
_LOCATION = {"index": None, "segmentId": None, "tabId": None}
_BORDER = {"color": _OPTIONAL_COLOR, "width": _DIMENSION, "padding": _DIMENSION, "dashStyle": None}

TEXT_STYLE = {
    "bold": None,
    "italic": None,
    "underline": None,
    "strikethrough": None,
    "smallCaps": None,
    "backgroundColor": _OPTIONAL_COLOR,
    "foregroundColor": _OPTIONAL_COLOR,
    "fontSize": _DIMENSION,
    "weightedFontFamily": {"fontFamily": None, "weight": None},
    "baselineOffset": None,
    "link": {"url": None, "bookmarkId": None, "headingId": None},
}

PARAGRAPH_STYLE = {
    "headingId": None,
    "namedStyleType": None,
    "alignment": None,
    "lineSpacing": None,
    "direction": None,
    "spacingMode": None,
    "spaceAbove": _DIMENSION,
    "spaceBelow": _DIMENSION,
    "borderBetween": _BORDER,
    "borderTop": _BORDER,
    "borderBottom": _BORDER,
    "borderLeft": _BORDER,
    "borderRight": _BORDER,
    "indentFirstLine": _DIMENSION,
    "indentStart": _DIMENSION,
    "indentEnd": _DIMENSION,
    "tabStops": None,
    "keepLinesTogether": None,
    "keepWithNext": None,
    "avoidWidowAndOrphan": None,
    "shading": {"backgroundColor": _OPTIONAL_COLOR},
    "pageBreakBefore": None,
}

REQUESTS: dict[str, dict[str, Any]] = {
    "insertText": {"location": _LOCATION, "text": None, "endOfSegmentLocation": None},
    "deleteContentRange": {"range": _RANGE},
    "updateTextStyle": {"range": _RANGE, "textStyle": TEXT_STYLE, "fields": None},
    "updateParagraphStyle": {"range": _RANGE, "paragraphStyle": PARAGRAPH_STYLE, "fields": None},
    "createParagraphBullets": {"range": _RANGE, "bulletPreset": None},
    "deleteParagraphBullets": {"range": _RANGE},
}

_FIELD_MASK_OWNER = {"updateTextStyle": TEXT_STYLE, "updateParagraphStyle": PARAGRAPH_STYLE}


def _check(value: Any, schema: dict[str, Any] | None, path: str) -> None:
    if schema is None or not isinstance(value, dict):
        return
    for key, item in value.items():
        if key not in schema:
            raise AssertionError(f"Unknown Docs API field '{path}.{key}' (HTTP 400 in real Docs)")
        _check(item, schema[key], f"{path}.{key}")


def validate_request(request: dict[str, Any]) -> None:
    """Raise AssertionError if ``request`` would be rejected for its shape."""
    assert len(request) == 1, f"a request has exactly one kind: {request}"
    (kind, body), = request.items()
    if kind not in REQUESTS:
        raise AssertionError(f"Unsupported/unknown request kind '{kind}'")
    _check(body, REQUESTS[kind], kind)
    rng = body.get("range")
    if rng is not None:
        assert rng["startIndex"] < rng["endIndex"], f"empty or inverted range in {request}"
        assert rng["startIndex"] >= 1, request
    owner = _FIELD_MASK_OWNER.get(kind)
    if owner is not None:
        fields = [f for f in body["fields"].split(",") if f]
        assert fields, f"empty field mask in {request}"
        for name in fields:
            if name.split(".")[0] not in owner:
                raise AssertionError(f"Unknown field '{name}' in {kind}.fields")


def validate_requests(requests: list[dict[str, Any]]) -> None:
    for request in requests:
        validate_request(request)


class FakeDocument:
    """A tiny model of a Docs body that *applies* requests the way the Docs API does.

    Text is held as UTF-16 code units (the Docs index unit), each with its own text
    style dict; paragraph styles and bullets are attached to the paragraph's newline.
    Supported: insertText, deleteContentRange, updateTextStyle, updateParagraphStyle,
    createParagraphBullets (nesting = leading tabs, which are then removed),
    deleteParagraphBullets. Every request is schema-validated first, and every index
    must lie inside the current body, or an AssertionError is raised -- exactly the
    cases in which the real API rejects the whole batch.
    """

    def __init__(self) -> None:
        # Index 0 is the section break; the body starts at 1 with one empty paragraph.
        self.units: list[int] = [0x0A]
        self.text_styles: list[dict[str, Any]] = [{}]
        self.paragraph_styles: dict[int, dict[str, Any]] = {}  # keyed by newline position
        self.bullets: dict[int, int] = {}  # newline position -> nesting level

    # -- helpers -------------------------------------------------------------
    @property
    def end_index(self) -> int:
        return 1 + len(self.units)

    def text(self) -> str:
        return b"".join(u.to_bytes(2, "little") for u in self.units).decode("utf-16-le")

    def _slice(self, start: int, end: int) -> str:
        units = self.units[start - 1 : end - 1]
        return b"".join(u.to_bytes(2, "little") for u in units).decode("utf-16-le")

    def _paragraphs(self) -> list[tuple[int, int]]:
        """(start, newline index) of every paragraph, in document indices."""
        result, start = [], 1
        for offset, unit in enumerate(self.units):
            if unit == 0x0A:
                result.append((start, offset + 1))
                start = offset + 2
        return result

    def _overlapping(self, rng: dict[str, int]) -> list[tuple[int, int]]:
        return [
            (s, nl) for s, nl in self._paragraphs() if s < rng["endIndex"] and nl >= rng["startIndex"]
        ]

    def _shift_keys(self, mapping: dict[int, Any], at: int, delta: int) -> dict[int, Any]:
        return {(k + delta if k >= at else k): v for k, v in mapping.items()}

    def _insert(self, index: int, text: str) -> None:
        assert 1 <= index < self.end_index, f"insert index {index} outside body ({self.end_index})"
        data = text.encode("utf-16-le")
        units = [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]
        inherited = dict(self.text_styles[index - 2]) if index > 1 else {}
        pos = index - 1
        self.units[pos:pos] = units
        self.text_styles[pos:pos] = [dict(inherited) for _ in units]
        self.paragraph_styles = self._shift_keys(self.paragraph_styles, index, len(units))
        self.bullets = self._shift_keys(self.bullets, index, len(units))

    def _delete(self, start: int, end: int) -> None:
        assert 1 <= start < end < self.end_index, f"delete [{start},{end}) invalid ({self.end_index})"
        del self.units[start - 1 : end - 1]
        del self.text_styles[start - 1 : end - 1]
        size = end - start
        self.paragraph_styles = {
            (k - size if k >= end else k): v for k, v in self.paragraph_styles.items() if not start <= k < end
        }
        self.bullets = {(k - size if k >= end else k): v for k, v in self.bullets.items() if not start <= k < end}

    # -- API -------------------------------------------------------------------
    def apply(self, requests: list[dict[str, Any]]) -> None:
        for request in requests:
            validate_request(request)
            (kind, body), = request.items()
            rng = body.get("range")
            if rng is not None:
                assert rng["endIndex"] <= self.end_index, f"{kind} range past end: {rng} / {self.end_index}"
            if kind == "insertText":
                self._insert(body["location"]["index"], body["text"])
            elif kind == "deleteContentRange":
                self._delete(rng["startIndex"], rng["endIndex"])
            elif kind == "updateTextStyle":
                for i in range(rng["startIndex"], rng["endIndex"]):
                    self.text_styles[i - 1].update(body["textStyle"])
            elif kind == "updateParagraphStyle":
                for _start, nl in self._overlapping(rng):
                    self.paragraph_styles.setdefault(nl, {}).update(body["paragraphStyle"])
            elif kind == "deleteParagraphBullets":
                for _start, nl in self._overlapping(rng):
                    self.bullets.pop(nl, None)
            elif kind == "createParagraphBullets":
                # Process bottom-up so removing tabs never moves a paragraph still to do.
                for start, nl in reversed(self._overlapping(rng)):
                    tabs = 0
                    while self.units[start - 1 + tabs] == 0x09:
                        tabs += 1
                    self.bullets[nl] = tabs
                    if tabs:
                        self._delete(start, start + tabs)

    def styled(self, key: str, value: Any = True) -> list[str]:
        """The maximal runs of text whose text style has ``key == value``."""
        runs, current = [], []
        for i, unit in enumerate(self.units):
            if self.text_styles[i].get(key) == value and unit != 0x0A:
                current.append(unit)
            elif current:
                runs.append(current)
                current = []
        if current:
            runs.append(current)
        return [b"".join(u.to_bytes(2, "little") for u in r).decode("utf-16-le") for r in runs]

    def paragraphs(self) -> list[tuple[str, dict[str, Any], int | None]]:
        """(text, paragraph style, bullet nesting or None) for every paragraph."""
        return [
            (self._slice(s, nl), self.paragraph_styles.get(nl, {}), self.bullets.get(nl))
            for s, nl in self._paragraphs()
        ]

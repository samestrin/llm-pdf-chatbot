"""Split read pages into chunks by headings and paragraphs.

A chunk never crosses a page, so it is always a span `[start, end)` of one page's
text, and `page.text[start:end] == chunk.text` exactly. Offsets count Python
characters (Unicode code points), not bytes.

A heading line starts a new chunk, and names every chunk after it until the next
heading, across pages. Paragraphs (runs of lines between blank lines and headings)
are packed into a chunk up to `max_chars`; a paragraph longer than that is split
at a sentence end, else at whitespace, else hard.

A heading is a Markdown `#` line, a numbered line such as `2.1 Results`, or a short
all-capitals line such as `SUMMARY`. Plain text carries no font sizes, so a bold
Title Case heading is read as a paragraph.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.reader import PageRead, TextBox

DEFAULT_MAX_CHARS = 1200
MAX_HEADING_CHARS = 80
MAX_HEADING_WORDS = 12

_MARKDOWN_HEADING = re.compile(r"#{1,6}\s+(.*?)(?:\s+#+)?")
_NUMBERED_HEADING = re.compile(r"\d+(?:\.\d+)*\.?\s+(\S)")
_SENTENCE_END = ".!?"
_NO_HEADING_END = ".,;:!?"


@dataclass(frozen=True)
class Chunk:
    page: int
    """1-based page number."""
    start: int
    end: int
    """Character offsets into the page text, end exclusive."""
    text: str
    """`page.text[start:end]`."""
    heading: str | None
    """The heading this chunk falls under, or None before the first heading."""
    bbox: tuple[float, float, float, float] | None
    """Union of the reader's boxes this chunk overlaps; None when there are none."""


@dataclass(frozen=True)
class _Unit:
    start: int
    end: int
    heading: str | None = None
    """Set when this unit is a heading line."""


def chunk_pages(pages: Iterable[PageRead], max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    if max_chars < 1:
        raise ValueError(f"max_chars must be at least 1, got {max_chars}")
    chunks: list[Chunk] = []
    heading: str | None = None
    for page in pages:
        if page.text is None:
            continue
        spans: list[tuple[int, int, str | None]] = []
        group: tuple[int, int] | None = None
        for unit in _units(page.text):
            if unit.heading is not None:
                if group:
                    spans.append((*group, heading))
                    group = None
                heading = unit.heading
            if unit.end - unit.start > max_chars:
                if group:
                    spans.append((*group, heading))
                    group = None
                for start, end in _split(page.text, unit.start, unit.end, max_chars):
                    spans.append((start, end, heading))
            elif group and unit.heading is None and unit.end - group[0] <= max_chars:
                group = (group[0], unit.end)
            else:
                if group:
                    spans.append((*group, heading))
                group = (unit.start, unit.end)
        if group:
            spans.append((*group, heading))
        chunks.extend(
            Chunk(page.page, start, end, page.text[start:end], name, _bbox(page.boxes, start, end))
            for start, end, name in spans
        )
    return chunks


def _units(text: str) -> list[_Unit]:
    """Heading lines and paragraphs, each trimmed of surrounding whitespace."""
    units: list[_Unit] = []
    paragraph: tuple[int, int] | None = None
    position = 0
    while position <= len(text):
        newline = text.find("\n", position)
        line_end = len(text) if newline == -1 else newline
        start, end = _trim(text, position, line_end)
        heading = _heading(text[start:end]) if start < end else None
        if start == end or heading is not None:
            if paragraph:
                units.append(_Unit(*paragraph))
                paragraph = None
            if heading is not None:
                units.append(_Unit(start, end, heading))
        else:
            paragraph = (paragraph[0] if paragraph else start, end)
        if newline == -1:
            break
        position = newline + 1
    if paragraph:
        units.append(_Unit(*paragraph))
    return units


def _heading(line: str) -> str | None:
    if match := _MARKDOWN_HEADING.fullmatch(line):
        return match.group(1).strip() or None
    if (
        len(line) > MAX_HEADING_CHARS
        or len(line.split()) > MAX_HEADING_WORDS
        or line[-1] in _NO_HEADING_END
    ):
        return None
    if (match := _NUMBERED_HEADING.match(line)) and match.group(1).isupper():
        return line
    letters = [c for c in line if c.isalpha()]
    if len(letters) >= 4 and all(c.isupper() for c in letters):
        return line
    return None


def _split(text: str, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    """Pieces of `text[start:end]`, each at most `max_chars` long and trimmed."""
    pieces = []
    while end - start > max_chars:
        cut = _break(text, start, start + max_chars, max_chars)
        piece = _trim(text, start, cut)
        if piece[0] < piece[1]:
            pieces.append(piece)
        start = _trim(text, cut, end)[0]
    if start < end:
        pieces.append((start, end))
    return pieces


def _break(text: str, start: int, limit: int, max_chars: int) -> int:
    """Where to cut a piece starting at `start` that may end no later than `limit`."""
    # A sentence end, unless it would leave a very short piece (as after "Dr.").
    for i in range(limit, start + max(1, max_chars // 3) - 1, -1):
        if text[i - 1] in _SENTENCE_END and (i == len(text) or text[i].isspace()):
            return i
    for i in range(limit, start, -1):
        if text[i].isspace():
            return i
    return limit


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _bbox(boxes: tuple[TextBox, ...], start: int, end: int) -> tuple[float, ...] | None:
    hits = [box.bbox for box in boxes if box.start < end and box.end > start]
    if not hits:
        return None
    return (
        min(b[0] for b in hits),
        min(b[1] for b in hits),
        max(b[2] for b in hits),
        max(b[3] for b in hits),
    )

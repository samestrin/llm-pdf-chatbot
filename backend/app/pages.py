"""Store a document's read pages and their chunks, and resolve a citation offset.

The exact page text is stored with the chunks that index into it, so any chunk
offset (or a narrower span a citation picks inside it) can be turned back into
the text on the page.
"""

from collections.abc import Sequence
from dataclasses import replace

from sqlalchemy import Engine, delete, insert, select

from app.chunking import DEFAULT_MAX_CHARS, chunk_pages
from app.db import chunks, pages
from app.reader import PageRead


def store_pages(
    engine: Engine,
    document_id: str,
    read: Sequence[PageRead],
    max_chars: int = DEFAULT_MAX_CHARS,
) -> None:
    """Replace the document's stored pages and chunks with these, in one transaction."""
    # Postgres text cannot hold NUL. One character for one keeps every offset in place.
    read = [p if p.text is None else replace(p, text=p.text.replace("\x00", "�")) for p in read]
    page_rows = [
        {
            "document_id": document_id,
            "page": p.page,
            "text": p.text,
            "method": p.method,
            "ocr_error": p.ocr_error,
            "error": p.error,
        }
        for p in read
    ]
    chunk_rows = [
        {
            "document_id": document_id,
            "page": c.page,
            "seq": seq,
            "start_offset": c.start,
            "end_offset": c.end,
            "text": c.text,
            "heading": c.heading,
            "bbox": list(c.bbox) if c.bbox else None,
        }
        for seq, c in enumerate(chunk_pages(read, max_chars))
    ]
    with engine.begin() as conn:
        conn.execute(delete(chunks).where(chunks.c.document_id == document_id))
        conn.execute(delete(pages).where(pages.c.document_id == document_id))
        if page_rows:
            conn.execute(insert(pages), page_rows)
        if chunk_rows:
            conn.execute(insert(chunks), chunk_rows)


def resolve_offset(engine: Engine, document_id: str, page: int, start: int, end: int) -> str:
    """The exact text at `[start, end)` on the stored page.

    Raises LookupError when the page is not stored or has no text, and ValueError when
    the span is empty or outside the page text.
    """
    query = select(pages.c.text).where(pages.c.document_id == document_id, pages.c.page == page)
    with engine.connect() as conn:
        text = conn.execute(query).scalar()
    if text is None:
        raise LookupError(f"document {document_id!r} has no text stored for page {page}")
    if not 0 <= start < end <= len(text):
        raise ValueError(f"offsets [{start}, {end}) are outside page {page} ({len(text)} chars)")
    return text[start:end]

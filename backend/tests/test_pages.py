import pymupdf
import pytest
from sqlalchemy import create_engine, func, insert, select

from app.db import chunks, collections, documents, init_db, pages
from app.pages import resolve_offset, store_pages
from app.reader import OcrSettings, PageRead, TextBox, read_pdf


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    init_db(engine)
    return engine


def add_document(engine, document_id: str = "doc1", page_count: int = 3) -> str:
    with engine.begin() as conn:
        collection_id = conn.execute(
            insert(collections).values(name=f"c-{document_id}").returning(collections.c.id)
        ).scalar_one()
        conn.execute(
            insert(documents).values(
                id=document_id,
                collection_id=collection_id,
                filename="x.pdf",
                size_bytes=1,
                page_count=page_count,
            )
        )
    return document_id


def stored_chunks(engine, document_id: str) -> list[dict]:
    query = select(chunks).where(chunks.c.document_id == document_id).order_by(chunks.c.seq)
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(query)]


def write_pdf(path, texts: list[str]) -> str:
    doc = pymupdf.open()
    for text in texts:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(path)
    return str(path)


def test_every_stored_chunk_offset_resolves_to_its_exact_text_on_the_page(engine, tmp_path):
    path = write_pdf(
        tmp_path / "doc.pdf",
        [
            "INTRODUCTION\nThe first page says hello.\n\nA second paragraph.",
            "",
            "2. Results\nResults are listed here.",
        ],
    )
    read = read_pdf(path, OcrSettings(url=None))
    document_id = add_document(engine)

    store_pages(engine, document_id, read, max_chars=30)

    rows = stored_chunks(engine, document_id)
    assert {row["page"] for row in rows} == {1, 3}
    for row in rows:
        quote = resolve_offset(
            engine, document_id, row["page"], row["start_offset"], row["end_offset"]
        )
        assert quote == row["text"]
        assert quote == read[row["page"] - 1].text[row["start_offset"] : row["end_offset"]]
    assert "Results are listed here." in [row["text"] for row in rows]
    assert {row["heading"] for row in rows if row["page"] == 3} == {"2. Results"}


def test_the_exact_page_text_and_read_result_are_stored(engine):
    document_id = add_document(engine)
    read = [
        PageRead(1, "  page one\n", "tesseract"),
        PageRead(2, None, None, ocr_error="OCR timed out", error="page has no text layer"),
        PageRead(3, "page three", "pymupdf", ocr_error="OCR failed on this page: bad"),
    ]
    store_pages(engine, document_id, read)

    with engine.connect() as conn:
        rows = conn.execute(
            select(pages).where(pages.c.document_id == document_id).order_by(pages.c.page)
        ).all()
    assert [(r.page, r.text, r.method, r.ocr_error, r.error) for r in rows] == [
        (1, "  page one\n", "tesseract", None, None),
        (2, None, None, "OCR timed out", "page has no text layer"),
        (3, "page three", "pymupdf", "OCR failed on this page: bad", None),
    ]


def test_nul_characters_are_replaced_in_place_so_offsets_still_line_up(engine):
    # Postgres text cannot hold NUL. The replacement is one character for one, so the
    # stored text has the same length and every offset still points at the same place.
    document_id = add_document(engine, page_count=1)
    store_pages(engine, document_id, [PageRead(1, "Bad\x00byte here.", "pymupdf")])
    [row] = stored_chunks(engine, document_id)
    assert row["text"] == "Bad�byte here."
    assert resolve_offset(engine, document_id, 1, 0, 3) == "Bad"


def test_bounding_box_is_stored_only_when_the_reader_returns_one(engine):
    document_id = add_document(engine, page_count=2)
    store_pages(
        engine,
        document_id,
        [
            PageRead(1, "Boxed.", "tesseract", boxes=(TextBox(0, 6, (1.0, 2.0, 3.0, 4.0)),)),
            PageRead(2, "Not boxed.", "pymupdf"),
        ],
    )
    assert [row["bbox"] for row in stored_chunks(engine, document_id)] == [
        [1.0, 2.0, 3.0, 4.0],
        None,
    ]


def test_storing_again_replaces_the_previous_pages_and_chunks(engine):
    document_id = add_document(engine, page_count=1)
    store_pages(engine, document_id, [PageRead(1, "Old text.", "pymupdf")])
    store_pages(engine, document_id, [PageRead(1, "New text.", "pymupdf")])
    assert [row["text"] for row in stored_chunks(engine, document_id)] == ["New text."]
    assert resolve_offset(engine, document_id, 1, 0, 9) == "New text."


def test_documents_do_not_share_chunks(engine):
    add_document(engine, "a", 1)
    add_document(engine, "b", 1)
    store_pages(engine, "a", [PageRead(1, "From a.", "pymupdf")])
    store_pages(engine, "b", [PageRead(1, "From b.", "pymupdf")])
    assert [r["text"] for r in stored_chunks(engine, "a")] == ["From a."]
    assert resolve_offset(engine, "b", 1, 0, 7) == "From b."


@pytest.mark.parametrize("start,end", [(-1, 3), (3, 3), (5, 2), (0, 11), (11, 12)])
def test_an_offset_outside_the_page_text_is_rejected(engine, start, end):
    document_id = add_document(engine, page_count=1)
    store_pages(engine, document_id, [PageRead(1, "page text.", "pymupdf")])
    with pytest.raises(ValueError):
        resolve_offset(engine, document_id, 1, start, end)


def test_resolving_an_unknown_or_failed_page_raises_lookup_error(engine):
    document_id = add_document(engine, page_count=2)
    store_pages(engine, document_id, [PageRead(1, None, None, error="unreadable")])
    with pytest.raises(LookupError):
        resolve_offset(engine, document_id, 1, 0, 1)
    with pytest.raises(LookupError):
        resolve_offset(engine, document_id, 2, 0, 1)
    with pytest.raises(LookupError):
        resolve_offset(engine, "missing", 1, 0, 1)


def test_deleting_the_document_removes_its_pages_and_chunks(engine):
    document_id = add_document(engine, page_count=1)
    store_pages(engine, document_id, [PageRead(1, "Some text.", "pymupdf")])
    with engine.begin() as conn:
        conn.execute(documents.delete().where(documents.c.id == document_id))
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(pages)).scalar() == 0
        assert conn.execute(select(func.count()).select_from(chunks)).scalar() == 0

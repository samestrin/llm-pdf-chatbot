import pytest

from app.chunking import chunk_pages
from app.reader import PageRead, TextBox


def page(number: int, text: str | None, boxes: tuple[TextBox, ...] = ()) -> PageRead:
    if text is None:
        return PageRead(number, None, None, error="page has no text layer")
    return PageRead(number, text, "pymupdf", boxes=boxes)


def assert_offsets_exact(pages: list[PageRead], chunks) -> None:
    texts = {p.page: p.text for p in pages}
    for chunk in chunks:
        assert texts[chunk.page][chunk.start : chunk.end] == chunk.text


def test_paragraphs_separated_by_blank_lines_pack_into_one_chunk_under_the_limit():
    text = "First paragraph here.\n\nSecond paragraph here.\n"
    chunks = chunk_pages([page(1, text)])
    assert [c.text for c in chunks] == ["First paragraph here.\n\nSecond paragraph here."]
    assert (chunks[0].start, chunks[0].end) == (0, len(text) - 1)


def test_paragraphs_split_into_separate_chunks_when_together_they_exceed_the_limit():
    pages = [page(1, "Alpha alpha alpha.\n\nBravo bravo bravo.\n\nCharlie charlie.")]
    chunks = chunk_pages(pages, max_chars=40)
    assert [c.text for c in chunks] == [
        "Alpha alpha alpha.\n\nBravo bravo bravo.",
        "Charlie charlie.",
    ]
    assert_offsets_exact(pages, chunks)


@pytest.mark.parametrize(
    "heading",
    ["# Introduction", "## 2.1 Method", "1. Introduction", "2.3 Results", "SUMMARY OF FINDINGS"],
)
def test_a_heading_starts_a_new_chunk_and_names_it(heading):
    text = f"Some preamble text.\n{heading}\nBody under the heading."
    pages = [page(1, text)]
    chunks = chunk_pages(pages)
    assert [c.text for c in chunks] == [
        "Some preamble text.",
        f"{heading}\nBody under the heading.",
    ]
    assert chunks[0].heading is None
    assert chunks[1].heading == heading.lstrip("#").strip()
    assert_offsets_exact(pages, chunks)


@pytest.mark.parametrize(
    "line",
    [
        "This sentence ends with a period.",
        "3.5 kg of material was used in total and it ends with a period.",
        "A",
        "OK.",
        "#hashtag without a space",
    ],
)
def test_ordinary_lines_are_not_headings(line):
    chunks = chunk_pages([page(1, f"Before.\n{line}\nAfter.")])
    assert len(chunks) == 1
    assert chunks[0].heading is None


def test_the_current_heading_carries_over_to_the_next_page():
    pages = [page(1, "# Background\nStarts here."), page(2, "Continues on the next page.")]
    chunks = chunk_pages(pages)
    assert [(c.page, c.heading) for c in chunks] == [(1, "Background"), (2, "Background")]


def test_a_paragraph_longer_than_the_limit_splits_at_a_sentence_then_a_space():
    text = "One two three. Four five six seven eight nine ten eleven twelve."
    pages = [page(1, text)]
    chunks = chunk_pages(pages, max_chars=30)
    assert chunks[0].text == "One two three."
    assert all(len(c.text) <= 30 for c in chunks)
    assert " ".join(c.text for c in chunks) == text
    assert_offsets_exact(pages, chunks)


def test_a_word_longer_than_the_limit_is_split_hard():
    pages = [page(1, "x" * 25)]
    chunks = chunk_pages(pages, max_chars=10)
    assert [c.text for c in chunks] == ["x" * 10, "x" * 10, "x" * 5]
    assert_offsets_exact(pages, chunks)


def test_failed_and_blank_pages_have_no_chunks():
    chunks = chunk_pages([page(1, None), page(2, " \n\n  \n"), page(3, "Text.")])
    assert [c.page for c in chunks] == [3]


def test_chunks_keep_document_order_across_pages():
    pages = [page(1, "A.\n\n# H\nB."), page(2, "C.")]
    chunks = chunk_pages(pages, max_chars=3)
    assert [(c.page, c.text) for c in chunks] == [(1, "A."), (1, "# H"), (1, "B."), (2, "C.")]


def test_offsets_are_exact_for_unicode_and_odd_whitespace():
    text = "﻿  Café — naïve 日本語 text.\r\n\r\n\tΣ section two.  \n\n\n"
    pages = [page(1, text)]
    chunks = chunk_pages(pages, max_chars=12)
    assert chunks
    assert_offsets_exact(pages, chunks)
    for chunk in chunks:
        assert chunk.text == chunk.text.strip()


def test_no_bounding_box_when_the_reader_returns_none():
    chunks = chunk_pages([page(1, "Para one.\n\nPara two.")])
    assert [c.bbox for c in chunks] == [None]


def test_bounding_box_is_the_union_of_the_reader_boxes_the_chunk_overlaps():
    text = "Para one.\n\nPara two.\n\nPara three."
    boxes = (
        TextBox(0, 9, (10.0, 10.0, 100.0, 20.0)),
        TextBox(11, 20, (12.0, 30.0, 90.0, 40.0)),
        TextBox(22, 33, (10.0, 50.0, 80.0, 60.0)),
    )
    chunks = chunk_pages([page(1, text, boxes)], max_chars=20)
    assert [c.text for c in chunks] == ["Para one.\n\nPara two.", "Para three."]
    assert chunks[0].bbox == (10.0, 10.0, 100.0, 40.0)
    assert chunks[1].bbox == (10.0, 50.0, 80.0, 60.0)


def test_no_bounding_box_for_a_chunk_no_reader_box_overlaps():
    text = "Para one.\n\nPara two."
    chunks = chunk_pages([page(1, text, (TextBox(0, 9, (1.0, 2.0, 3.0, 4.0)),))], max_chars=10)
    assert [c.bbox for c in chunks] == [(1.0, 2.0, 3.0, 4.0), None]


def test_max_chars_must_be_positive():
    with pytest.raises(ValueError):
        chunk_pages([page(1, "Text.")], max_chars=0)


def test_a_heading_longer_than_the_limit_is_split_like_a_paragraph():
    pages = [page(1, "# A very long heading line\nBody.")]
    chunks = chunk_pages(pages, max_chars=10)
    assert all(len(c.text) <= 10 for c in chunks)
    assert {c.heading for c in chunks} == {"A very long heading line"}
    assert_offsets_exact(pages, chunks)

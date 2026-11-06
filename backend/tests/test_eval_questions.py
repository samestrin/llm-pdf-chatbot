import json
from collections import Counter
from pathlib import Path

import pymupdf
import pytest

from evals.questions import (
    QUESTIONS,
    SKIPPED_KINDS,
    Question,
    load_questions,
    normalize,
    question_problems,
)
from evals.sources import PDF_DIR, SOURCES, read_sources

HEADER = "| File | Kind | URL | Licence | Retrieved | sha256 |\n|---|---|---|---|---|---|\n"


def write_case(tmp_path: Path, pages: list[str], kind: str = "report") -> tuple[Path, Path]:
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    doc.save(pdfs / "a.pdf")
    sources = tmp_path / "SOURCES.md"
    sources.write_text(
        "# Sources\n\n" + HEADER + f"| a.pdf | {kind} | https://example.org/a.pdf | CC0 | "
        "2026-11-05 | 00 |\n"
    )
    return pdfs, sources


def ask(**fields) -> Question:
    return Question(
        **{
            "id": "a-01",
            "pdf": "a.pdf",
            "question": "What is on page two?",
            "answerable": True,
            "expected_page": 2,
            "key_facts": ("947",),
        }
        | fields
    )


def test_the_committed_questions_are_40_with_10_unanswerable():
    questions = load_questions(QUESTIONS)
    assert len(questions) == 40
    assert sum(not q.answerable for q in questions) == 10


def test_the_committed_questions_have_no_problems_on_the_text_pdfs():
    assert question_problems(load_questions(QUESTIONS), PDF_DIR, SOURCES) == []


def test_every_fixture_pdf_has_questions_and_only_the_scan_is_skipped():
    kinds = {r.file: r.kind for r in read_sources(SOURCES)}
    asked = Counter(q.pdf for q in load_questions(QUESTIONS) if q.answerable)
    assert set(asked) == set(kinds)
    assert SKIPPED_KINDS == {"scanned"}
    text_pdfs = [f for f, k in kinds.items() if k not in SKIPPED_KINDS]
    assert len(text_pdfs) == 4
    assert all(asked[f] > 0 for f in text_pdfs)


def test_a_question_on_the_right_page_has_no_problems(tmp_path):
    pdfs, sources = write_case(tmp_path, ["nothing", "CBO completed 947 estimates"])
    assert question_problems([ask()], pdfs, sources) == []


def test_a_key_fact_missing_from_the_expected_page_fails(tmp_path):
    pdfs, sources = write_case(tmp_path, ["CBO completed 947 estimates", "nothing"])
    assert question_problems([ask()], pdfs, sources) == [
        "a-01: key fact '947' not on page 2 of a.pdf"
    ]


def test_an_expected_page_past_the_end_fails(tmp_path):
    pdfs, sources = write_case(tmp_path, ["947"])
    assert question_problems([ask()], pdfs, sources) == ["a-01: a.pdf has no page 2 (1 pages)"]


def test_an_answerable_question_needs_a_page_and_key_facts(tmp_path):
    pdfs, sources = write_case(tmp_path, ["947"])
    assert question_problems([ask(expected_page=None, key_facts=())], pdfs, sources) == [
        "a-01: answerable but has no expected page",
        "a-01: answerable but has no key facts",
    ]


def test_an_unanswerable_question_has_no_page_or_key_facts(tmp_path):
    pdfs, sources = write_case(tmp_path, ["947"])
    unanswerable = ask(answerable=False, expected_page=None, key_facts=())
    assert question_problems([unanswerable], pdfs, sources) == []
    assert question_problems([ask(answerable=False, expected_page=1)], pdfs, sources) == [
        "a-01: unanswerable but has an expected page",
        "a-01: unanswerable but has key facts",
    ]


def test_a_pdf_not_in_sources_fails(tmp_path):
    pdfs, sources = write_case(tmp_path, ["947"])
    assert question_problems([ask(pdf="b.pdf")], pdfs, sources) == [
        "a-01: b.pdf is not listed in SOURCES.md"
    ]


def test_duplicate_ids_and_empty_questions_fail(tmp_path):
    pdfs, sources = write_case(tmp_path, ["x", "947"])
    assert question_problems([ask(), ask(question=" ")], pdfs, sources) == [
        "a-01: id used more than once",
        "a-01: question is empty",
    ]


def test_a_scanned_pdf_is_skipped_unless_its_pages_are_read_another_way(tmp_path):
    pdfs, sources = write_case(tmp_path, ["no text layer", "here either"], kind="scanned")
    assert question_problems([ask()], pdfs, sources) == []

    def ocr(path: Path) -> list[str]:
        return ["", "OCR found 947"]

    assert question_problems([ask()], pdfs, sources, read_pages=ocr, skip_kinds=()) == []
    assert question_problems([ask(key_facts=("948",))], pdfs, sources, skip_kinds=()) == [
        "a-01: key fact '948' not on page 2 of a.pdf"
    ]


def test_normalize_joins_soft_hyphens_and_folds_case_space_and_punctuation():
    assert normalize("45 per\xad\ncent") == "45 percent"
    assert normalize("Grantee\u2019s   Fiscal\nYear") == "grantee's fiscal year"
    assert normalize("\u201cWMF\u201d \u2013225 \u2212317") == '"wmf" -225 -317'


def test_load_questions_reads_every_field(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "a-01",
                    "pdf": "a.pdf",
                    "question": "Q?",
                    "answerable": True,
                    "expected_page": 2,
                    "key_facts": ["947"],
                }
            ]
        )
    )
    assert load_questions(path) == [ask(question="Q?")]


def test_load_questions_rejects_unknown_or_missing_fields(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps([{"id": "a-01", "page": 2}]))
    with pytest.raises(TypeError):
        load_questions(path)

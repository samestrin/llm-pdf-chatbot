"""The eval's questions: each names its PDF, whether it can be answered from it, and, when it
can, the page that answers it and the key facts a correct answer must contain.

`question_problems` checks every answerable question's key facts against its expected page,
read live from the PDF. PDFs of a kind in `SKIPPED_KINDS` are skipped by default: the scanned
PDF has no text layer and needs OCR, which CI lacks. The owner's full run checks it by passing
`read_pages` that OCRs it and `skip_kinds=()`.
"""

import json
import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from evals.sources import EVALS, read_sources

QUESTIONS = EVALS / "questions.json"
SKIPPED_KINDS = frozenset({"scanned"})

# Characters PDFs and answers spell differently; folded so a key fact matches either.
_FOLD = str.maketrans(
    {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'}
    | {dash: "-" for dash in "\u2010\u2011\u2012\u2013\u2014\u2212"}
)


@dataclass(frozen=True)
class Question:
    id: str
    pdf: str
    question: str
    answerable: bool
    expected_page: int | None
    """1-based; None when unanswerable."""
    key_facts: tuple[str, ...]
    """Strings a correct answer contains; each is on the expected page. Empty when
    unanswerable."""


def load_questions(path: Path = QUESTIONS) -> list[Question]:
    return [
        Question(**(row | {"key_facts": tuple(row.get("key_facts", ()))}))
        for row in json.loads(path.read_text())
    ]


def normalize(text: str) -> str:
    """Lower case, curly quotes and dashes folded, soft hyphens joined, whitespace collapsed."""
    text = re.sub(r"\xad\s*", "", text).translate(_FOLD)
    return " ".join(text.split()).lower()


def text_layer_pages(path: Path) -> list[str]:
    with pymupdf.open(path, filetype="pdf") as pdf:
        return [page.get_text() for page in pdf]


def question_problems(
    questions: Iterable[Question],
    pdf_dir: Path,
    sources: Path,
    read_pages: Callable[[Path], list[str]] = text_layer_pages,
    skip_kinds: Collection[str] = SKIPPED_KINDS,
) -> list[str]:
    """Every way the questions disagree with their PDFs; empty when they agree."""
    questions = list(questions)
    kinds = {r.file: r.kind for r in read_sources(sources)}
    ids = Counter(q.id for q in questions)
    pages: dict[str, list[str]] = {}
    problems: list[str] = []
    for q in questions:
        if ids[q.id] > 1:
            problems.append(f"{q.id}: id used more than once")
            ids[q.id] = 1
        if not q.question.strip():
            problems.append(f"{q.id}: question is empty")
        if q.pdf not in kinds:
            problems.append(f"{q.id}: {q.pdf} is not listed in SOURCES.md")
            continue
        if not q.answerable:
            if q.expected_page is not None:
                problems.append(f"{q.id}: unanswerable but has an expected page")
            if q.key_facts:
                problems.append(f"{q.id}: unanswerable but has key facts")
            continue
        if q.expected_page is None:
            problems.append(f"{q.id}: answerable but has no expected page")
        if not q.key_facts:
            problems.append(f"{q.id}: answerable but has no key facts")
        if q.expected_page is None or kinds[q.pdf] in skip_kinds:
            continue
        if q.pdf not in pages:
            pages[q.pdf] = [normalize(t) for t in read_pages(pdf_dir / q.pdf)]
        doc = pages[q.pdf]
        if not 1 <= q.expected_page <= len(doc):
            problems.append(f"{q.id}: {q.pdf} has no page {q.expected_page} ({len(doc)} pages)")
            continue
        for fact in q.key_facts:
            if normalize(fact) not in doc[q.expected_page - 1]:
                problems.append(
                    f"{q.id}: key fact {fact!r} not on page {q.expected_page} of {q.pdf}"
                )
    return problems

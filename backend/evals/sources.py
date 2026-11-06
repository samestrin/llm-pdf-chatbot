"""The eval's fixture PDFs and their record in SOURCES.md: URL, licence, date and sha256."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

EVALS = Path(__file__).resolve().parent
PDF_DIR = EVALS / "pdfs"
SOURCES = EVALS / "SOURCES.md"
MAX_BYTES = 5 * 1024 * 1024
COLUMNS = ["file", "kind", "url", "licence", "retrieved", "sha256"]


@dataclass(frozen=True)
class Source:
    file: str
    kind: str
    url: str
    licence: str
    retrieved: str
    sha256: str


def read_sources(path: Path) -> list[Source]:
    """The rows of the first table in SOURCES.md whose header is COLUMNS."""
    rows: list[Source] = []
    in_table = False
    for line in path.read_text().splitlines():
        if not line.startswith("|"):
            if in_table:
                break
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not in_table:
            in_table = [c.lower() for c in cells] == COLUMNS
        elif not set("".join(cells)) <= set("-: "):
            rows.append(Source(*(cells + [""] * len(COLUMNS))[: len(COLUMNS)]))
    return rows


def fixture_problems(pdf_dir: Path, sources: Path) -> list[str]:
    """Every way the PDFs and SOURCES.md disagree; empty when they match."""
    rows = read_sources(sources)
    listed = [r.file for r in rows]
    problems = [
        f"{p.name}: not listed in SOURCES.md"
        for p in sorted(pdf_dir.glob("*.pdf"))
        if p.name not in listed
    ]
    seen: set[str] = set()
    for r in rows:
        if r.file in seen:
            problems.append(f"{r.file}: listed more than once in SOURCES.md")
            continue
        seen.add(r.file)
        pdf = pdf_dir / r.file
        if not pdf.is_file():
            problems.append(f"{r.file}: listed in SOURCES.md but not found")
            continue
        if not r.url:
            problems.append(f"{r.file}: URL is empty")
        if not r.licence:
            problems.append(f"{r.file}: licence is empty")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r.retrieved):
            problems.append(f"{r.file}: retrieval date is not YYYY-MM-DD")
        size = pdf.stat().st_size
        if size > MAX_BYTES:
            problems.append(f"{r.file}: over 5 MB ({size} bytes)")
        if hashlib.sha256(pdf.read_bytes()).hexdigest() != r.sha256.lower():
            problems.append(f"{r.file}: sha256 differs from SOURCES.md")
    return problems

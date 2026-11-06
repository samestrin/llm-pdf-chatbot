import hashlib
from pathlib import Path

from evals.sources import MAX_BYTES, PDF_DIR, SOURCES, fixture_problems, read_sources

KINDS = {"contract", "manual", "report", "scanned", "table"}

HEADER = "| File | Kind | URL | Licence | Retrieved | sha256 |\n|---|---|---|---|---|---|\n"


def write_case(tmp_path: Path, data: bytes = b"%PDF-1.4 fake", **row: str) -> tuple[Path, Path]:
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    (pdfs / "a.pdf").write_bytes(data)
    cells = {
        "file": "a.pdf",
        "kind": "report",
        "url": "https://example.org/a.pdf",
        "licence": "CC0",
        "retrieved": "2026-11-05",
        "sha256": hashlib.sha256(data).hexdigest(),
    } | row
    sources = tmp_path / "SOURCES.md"
    sources.write_text("# Sources\n\n" + HEADER + "| " + " | ".join(cells.values()) + " |\n")
    return pdfs, sources


def test_the_committed_fixtures_have_no_problems():
    assert fixture_problems(PDF_DIR, SOURCES) == []


def test_there_are_five_pdfs_one_of_each_kind():
    rows = read_sources(SOURCES)
    assert sorted(p.name for p in PDF_DIR.glob("*.pdf")) == sorted(r.file for r in rows)
    assert len(rows) == 5
    assert {r.kind for r in rows} == KINDS


def test_a_valid_case_has_no_problems(tmp_path):
    assert fixture_problems(*write_case(tmp_path)) == []


def test_a_pdf_missing_from_sources_fails(tmp_path):
    pdfs, sources = write_case(tmp_path)
    (pdfs / "b.pdf").write_bytes(b"%PDF-1.4 other")
    assert fixture_problems(pdfs, sources) == ["b.pdf: not listed in SOURCES.md"]


def test_a_listed_pdf_missing_from_disk_fails(tmp_path):
    pdfs, sources = write_case(tmp_path)
    (pdfs / "a.pdf").unlink()
    assert fixture_problems(pdfs, sources) == ["a.pdf: listed in SOURCES.md but not found"]


def test_a_different_sha256_fails(tmp_path):
    pdfs, sources = write_case(tmp_path, sha256="0" * 64)
    assert fixture_problems(pdfs, sources) == ["a.pdf: sha256 differs from SOURCES.md"]


def test_a_pdf_over_5_mb_fails(tmp_path):
    assert MAX_BYTES == 5 * 1024 * 1024
    pdfs, sources = write_case(tmp_path, data=b"x" * (MAX_BYTES + 1))
    assert fixture_problems(pdfs, sources) == ["a.pdf: over 5 MB (5242881 bytes)"]


def test_a_pdf_of_exactly_5_mb_passes(tmp_path):
    assert fixture_problems(*write_case(tmp_path, data=b"x" * MAX_BYTES)) == []


def test_an_empty_licence_fails(tmp_path):
    pdfs, sources = write_case(tmp_path, licence=" ")
    assert fixture_problems(pdfs, sources) == ["a.pdf: licence is empty"]


def test_an_empty_url_or_date_fails(tmp_path):
    pdfs, sources = write_case(tmp_path, url="", retrieved="last week")
    assert fixture_problems(pdfs, sources) == [
        "a.pdf: URL is empty",
        "a.pdf: retrieval date is not YYYY-MM-DD",
    ]


def test_a_pdf_listed_twice_fails(tmp_path):
    pdfs, sources = write_case(tmp_path)
    lines = sources.read_text().splitlines()
    sources.write_text("\n".join([*lines, lines[-1]]) + "\n")
    assert fixture_problems(pdfs, sources) == ["a.pdf: listed more than once in SOURCES.md"]


def test_sources_without_a_table_fails(tmp_path):
    pdfs, sources = write_case(tmp_path)
    sources.write_text("# Sources\n\nnothing here\n")
    assert fixture_problems(pdfs, sources) == ["a.pdf: not listed in SOURCES.md"]

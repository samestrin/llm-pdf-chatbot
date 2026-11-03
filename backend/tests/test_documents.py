from contextlib import ExitStack

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError

from app.main import create_app


def make_pdf(pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"page {i + 1}")
    return doc.tobytes()


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    path = tmp_path / "uploads"
    monkeypatch.setenv("UPLOAD_DIR", str(path))
    return path


@pytest.fixture
def engine(tmp_path):
    # A file, not ":memory:": the routes run on worker threads and must share one database.
    return create_engine(f"sqlite:///{tmp_path}/test.db")


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, float(len(t))] for t in texts]


@pytest.fixture
def start(engine, upload_dir, monkeypatch):
    """Start the app after a test has set its environment; entering runs startup (schema)."""
    monkeypatch.delenv("OCR_URL", raising=False)
    with ExitStack() as stack:
        yield lambda: stack.enter_context(TestClient(create_app(engine, embedder=FakeEmbedder())))


@pytest.fixture
def client(start):
    return start()


def upload(client, data: bytes, filename: str = "report.pdf", collection: str = "papers"):
    return client.post(
        f"/collections/{collection}/documents",
        files={"file": (filename, data, "application/pdf")},
    )


def document_rows(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT id, filename, page_count FROM documents")).all()


def test_upload_stores_pdf_under_generated_id(client, engine, upload_dir):
    data = make_pdf(pages=3)
    resp = upload(client, data)

    assert resp.status_code == 201
    body = resp.json()
    assert body["collection"] == "papers"
    assert body["filename"] == "report.pdf"
    assert body["pages"] == 3
    assert body["size_bytes"] == len(data)
    assert [p.name for p in upload_dir.iterdir()] == [f"{body['id']}.pdf"]
    assert (upload_dir / f"{body['id']}.pdf").read_bytes() == data
    assert document_rows(engine) == [(body["id"], "report.pdf", 3)]


def test_uploads_to_same_collection_share_it(client, engine):
    a = upload(client, make_pdf()).json()
    b = upload(client, make_pdf(), filename="report.pdf").json()

    assert a["id"] != b["id"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name FROM collections")).scalars().all() == ["papers"]


@pytest.mark.parametrize(
    "data",
    [b"hello, not a pdf", b"", b"<html>%PDF-1.7</html>"],
    ids=["text", "empty", "pdf-marker-not-at-start"],
)
def test_non_pdf_rejected_with_422(client, engine, upload_dir, data):
    resp = upload(client, data)

    assert resp.status_code == 422
    assert document_rows(engine) == []
    assert list(upload_dir.iterdir()) == []


def test_valid_pdf_with_junk_before_header_rejected_with_422(client, engine, upload_dir):
    # PyMuPDF opens this happily, so only the %PDF header check rejects it.
    resp = upload(client, b"JUNKJUNK" + make_pdf())

    assert resp.status_code == 422
    assert resp.json()["detail"] == "file is not a PDF"
    assert document_rows(engine) == []
    assert list(upload_dir.iterdir()) == []


def test_unreadable_pdf_rejected_with_422(client, engine, upload_dir):
    resp = upload(client, b"%PDF-1.7\nthis is not really a pdf\n")

    assert resp.status_code == 422
    assert document_rows(engine) == []
    assert list(upload_dir.iterdir()) == []


ZERO_PAGE_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj
trailer<</Root 1 0 R>>
%%EOF
"""


def test_pdf_without_pages_rejected_with_422(client, engine, upload_dir):
    resp = upload(client, ZERO_PAGE_PDF)

    assert resp.status_code == 422
    assert document_rows(engine) == []
    assert list(upload_dir.iterdir()) == []


def test_failed_insert_leaves_no_file(client, engine, upload_dir):
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE documents"))

    with pytest.raises(OperationalError):
        upload(client, make_pdf())

    assert list(upload_dir.iterdir()) == []


def test_failed_commit_leaves_no_file(client, engine, upload_dir):
    upload(client, make_pdf())
    # Let the first document's ingestion commit before the hook below starts failing commits.
    client.app.state.worker.join()

    def fail_commit(conn):
        # Fail only the commit that follows the second file's move into place.
        if len(list(upload_dir.glob("*.pdf"))) == 2:
            raise RuntimeError("commit failed")

    event.listen(engine, "commit", fail_commit)
    with pytest.raises(RuntimeError):
        upload(client, make_pdf())
    event.remove(engine, "commit", fail_commit)

    assert len(document_rows(engine)) == 1
    assert len(list(upload_dir.iterdir())) == 1


@pytest.mark.parametrize(
    "filename",
    ["../../escape.pdf", "..\\..\\escape.pdf", "/etc/escape.pdf", "C:\\Windows\\escape.pdf"],
)
def test_path_like_filename_is_never_used_as_a_path(client, engine, tmp_path, upload_dir, filename):
    resp = upload(client, make_pdf(), filename=filename)

    assert resp.status_code == 201
    body = resp.json()
    assert body["filename"] == "escape.pdf"
    assert [p.name for p in upload_dir.iterdir()] == [f"{body['id']}.pdf"]
    assert not any(p.name == "escape.pdf" for p in tmp_path.rglob("*"))
    assert document_rows(engine) == [(body["id"], "escape.pdf", 1)]


def test_size_limit_returns_413(start, engine, upload_dir, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    client = start()
    data = make_pdf() + b"%" + b"0" * (1024 * 1024)

    resp = upload(client, data)

    assert resp.status_code == 413
    assert document_rows(engine) == []
    assert list(upload_dir.iterdir()) == []


def test_file_at_size_limit_is_accepted(start, engine, upload_dir, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    client = start()
    pdf = make_pdf()
    data = pdf + b"%" + b"0" * (1024 * 1024 - len(pdf) - 1)
    assert len(data) == 1024 * 1024

    assert upload(client, data).status_code == 201


def test_page_limit_returns_422(start, engine, upload_dir, monkeypatch):
    monkeypatch.setenv("MAX_PDF_PAGES", "2")
    client = start()

    assert upload(client, make_pdf(pages=2)).status_code == 201
    resp = upload(client, make_pdf(pages=3))

    assert resp.status_code == 422
    assert "3 pages" in resp.json()["detail"]
    assert len(document_rows(engine)) == 1
    assert len(list(upload_dir.iterdir())) == 1


def test_default_limits(client):
    assert client.app.state.limits.max_upload_bytes == 50 * 1024 * 1024
    assert client.app.state.limits.max_pdf_pages == 500


def test_delete_removes_file_and_row(client, engine, upload_dir):
    doc_id = upload(client, make_pdf()).json()["id"]
    assert (upload_dir / f"{doc_id}.pdf").exists()

    resp = client.delete(f"/documents/{doc_id}")

    assert resp.status_code == 204
    assert document_rows(engine) == []
    assert list(upload_dir.iterdir()) == []


def test_delete_cascades_to_dependent_rows(client, engine):
    # A stand-in child table proves FKs are enforced; test_ingest.py checks the real ones.
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE child (id INTEGER PRIMARY KEY, document_id TEXT NOT NULL "
                "REFERENCES documents(id) ON DELETE CASCADE)"
            )
        )
    doc_id = upload(client, make_pdf()).json()["id"]
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO child (document_id) VALUES (:d)"), {"d": doc_id})

    assert client.delete(f"/documents/{doc_id}").status_code == 204

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM child")).scalar() == 0


def test_delete_unknown_document_returns_404(client):
    assert client.delete("/documents/does-not-exist").status_code == 404


def test_delete_succeeds_when_file_already_gone(client, engine, upload_dir):
    doc_id = upload(client, make_pdf()).json()["id"]
    (upload_dir / f"{doc_id}.pdf").unlink()

    assert client.delete(f"/documents/{doc_id}").status_code == 204
    assert document_rows(engine) == []


def test_route_docs_state_there_is_no_auth(client):
    schema = client.get("/openapi.json").json()
    for path, method in [
        ("/collections/{collection}/documents", "post"),
        ("/documents/{document_id}", "delete"),
    ]:
        assert "no auth" in schema["paths"][path][method]["description"].lower()

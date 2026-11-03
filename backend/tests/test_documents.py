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


# Read API: the collection list, the stored PDF, and page text.


@pytest.fixture
def any_db_client(db_engine, upload_dir, monkeypatch):
    """`client`, on SQLite and on Postgres."""
    monkeypatch.delenv("OCR_URL", raising=False)
    with TestClient(create_app(db_engine, embedder=FakeEmbedder())) as client:
        yield client


STATUS_FIELDS = {
    "state",
    "error",
    "pages_total",
    "pages_read",
    "pages_failed",
    "chunks_total",
    "chunks_embedded",
    "updated_at",
    "pages",
    "failures",
}


def test_list_collections_with_their_documents_and_status(any_db_client):
    client = any_db_client
    a = upload(client, make_pdf(pages=2), filename="a.pdf", collection="papers").json()
    b = upload(client, make_pdf(), filename="b.pdf", collection="papers").json()
    c = upload(client, make_pdf(), filename="c.pdf", collection="notes").json()
    client.app.state.worker.join()

    resp = client.get("/collections")

    assert resp.status_code == 200
    body = resp.json()
    assert [col["name"] for col in body] == ["notes", "papers"]
    notes, papers = body
    assert [d["id"] for d in notes["documents"]] == [c["id"]]
    assert {d["id"] for d in papers["documents"]} == {a["id"], b["id"]}
    doc_a = next(d for d in papers["documents"] if d["id"] == a["id"])
    assert (doc_a["filename"], doc_a["pages"], doc_a["size_bytes"]) == ("a.pdf", 2, a["size_bytes"])
    # The same status fields as GET /documents/{id}/status, under `status`.
    assert set(doc_a["status"]) == STATUS_FIELDS
    expected = client.get(f"/documents/{a['id']}/status").json()
    assert doc_a["status"] == {k: expected[k] for k in STATUS_FIELDS}
    assert doc_a["status"]["state"] == "ready"
    assert [p["method"] for p in doc_a["status"]["pages"]] == ["pymupdf", "pymupdf"]


def test_list_shows_failed_pages_per_document(client):
    good = upload(client, make_pdf()).json()
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "readable")
    doc.new_page()  # No text layer and no OCR: nothing reads it.
    partly = upload(client, doc.tobytes(), filename="partly.pdf").json()
    client.app.state.worker.join()

    (papers,) = client.get("/collections").json()

    by_id = {d["id"]: d["status"] for d in papers["documents"]}
    assert by_id[good["id"]]["failures"] == []
    assert by_id[partly["id"]]["state"] == "ready"
    assert by_id[partly["id"]]["pages_failed"] == 1
    assert [f["page"] for f in by_id[partly["id"]]["failures"]] == [2]


def test_list_keeps_a_collection_whose_documents_were_deleted(client):
    doc_id = upload(client, make_pdf()).json()["id"]
    client.app.state.worker.join()
    client.delete(f"/documents/{doc_id}")

    (papers,) = client.get("/collections").json()

    assert papers["name"] == "papers"
    assert papers["documents"] == []


def test_list_is_empty_with_no_collections(client):
    assert client.get("/collections").json() == []


def test_pdf_is_served_inline_with_its_bytes_and_headers(client):
    data = make_pdf(pages=2)
    doc_id = upload(client, data, filename="report.pdf").json()["id"]

    resp = client.get(f"/documents/{doc_id}/pdf")

    assert resp.status_code == 200
    assert resp.content == data
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-disposition"].startswith("inline")
    assert 'filename="report.pdf"' in resp.headers["content-disposition"]


def test_pdf_of_unknown_document_is_404(client):
    upload(client, make_pdf())

    resp = client.get(f"/documents/{'0' * 32}/pdf")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "document not found"}


@pytest.mark.parametrize(
    "document_id",
    [
        "..",
        "..%2F..%2Fetc%2Fpasswd",
        "%2E%2E%2Fsecret",
        "..%5Csecret",
        "%2Fetc%2Fpasswd",
        "secret",
        "secret.pdf",
    ],
)
def test_pdf_path_like_id_is_404_and_never_reads_the_path(client, upload_dir, document_id):
    upload_dir.mkdir(parents=True, exist_ok=True)
    # Files a path-like id could reach if it were ever joined onto the upload directory.
    (upload_dir / "secret.pdf").write_bytes(b"%PDF-secret")
    (upload_dir / "secret").write_bytes(b"%PDF-secret")
    (upload_dir.parent / "secret.pdf").write_bytes(b"%PDF-secret")

    resp = client.get(f"/documents/{document_id}/pdf")

    assert resp.status_code == 404
    assert b"secret" not in resp.content


def test_pdf_whose_file_is_gone_is_404(client, upload_dir):
    doc_id = upload(client, make_pdf()).json()["id"]
    client.app.state.worker.join()
    (upload_dir / f"{doc_id}.pdf").unlink()

    resp = client.get(f"/documents/{doc_id}/pdf")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "file not found"}


def test_page_text_is_the_stored_text_chunk_offsets_index(client, engine):
    doc_id = upload(client, make_pdf(pages=2)).json()["id"]
    client.app.state.worker.join()

    resp = client.get(f"/documents/{doc_id}/pages/2")

    assert resp.status_code == 200
    with engine.connect() as conn:
        stored = conn.execute(
            text("SELECT text FROM pages WHERE document_id = :d AND page = 2"), {"d": doc_id}
        ).scalar_one()
    assert "page 2" in stored
    assert resp.json() == {
        "document_id": doc_id,
        "page": 2,
        "text": stored,
        "method": "pymupdf",
        "error": None,
    }


def test_failed_page_has_null_text_and_its_reason(client):
    doc = pymupdf.open()
    doc.new_page()
    doc_id = upload(client, doc.tobytes()).json()["id"]
    client.app.state.worker.join()

    body = client.get(f"/documents/{doc_id}/pages/1").json()

    assert body["text"] is None
    assert body["error"] == "page has no text layer"


@pytest.mark.parametrize("page", [0, 3, -1])
def test_page_outside_the_document_is_404(client, page):
    doc_id = upload(client, make_pdf(pages=2)).json()["id"]
    client.app.state.worker.join()

    resp = client.get(f"/documents/{doc_id}/pages/{page}")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "page not found"}


def test_page_of_unknown_document_is_404(client):
    resp = client.get(f"/documents/{'0' * 32}/pages/1")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "document not found"}


def test_page_not_read_yet_is_409(client, engine):
    doc_id = upload(client, make_pdf()).json()["id"]
    client.app.state.worker.join()
    # As before the worker reads the document: no page rows yet.
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM pages WHERE document_id = :d"), {"d": doc_id})

    resp = client.get(f"/documents/{doc_id}/pages/1")

    assert resp.status_code == 409
    assert resp.json() == {"detail": "page has not been read yet"}


def test_read_route_docs_state_there_is_no_auth(client):
    schema = client.get("/openapi.json").json()
    for path in [
        "/collections",
        "/documents/{document_id}/pdf",
        "/documents/{document_id}/pages/{page}",
    ]:
        assert "no auth" in schema["paths"][path]["get"]["description"].lower()

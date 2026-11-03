from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient
from test_ingest import FakeEmbedder, make_pdf, upload

from app.main import create_app

NO_TEXT_LAYER = "page has no text layer"


@pytest.fixture
def embedder(db_engine):
    return FakeEmbedder(db_engine)


@pytest.fixture
def start(db_engine, tmp_path, monkeypatch, embedder):
    """Start the app with OCR at `ocr_url` (or none)."""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("EMBED_BATCH_SIZE", "4")
    with ExitStack() as stack:

        def run(ocr_url: str | None = None):
            if ocr_url:
                monkeypatch.setenv("OCR_URL", ocr_url)
            else:
                monkeypatch.delenv("OCR_URL", raising=False)
            return stack.enter_context(TestClient(create_app(db_engine, embedder=embedder)))

        yield run


def test_partly_failed_document_reports_each_page_and_its_failure(start, ocr):
    # Page 1 is read by OCR; OCR fails on page 2, which falls back to the text layer; page 3
    # is a scan OCR fails on and has no text layer, so nothing reads it; OCR skips page 4.
    ocr.body = {
        "pages": [
            {"page": 1, "text": "Scanned page one.", "method": "tesseract", "error": None},
            {"page": 2, "text": None, "method": "tesseract", "error": "low contrast"},
            {"page": 3, "text": None, "method": "tesseract", "error": "blank image"},
        ]
    }
    client = start(ocr.url)
    pdf = make_pdf(["SECTION 1\nPage one.", "SECTION 2\nPage two.", "", "SECTION 4\nPage four."])
    doc_id = upload(client, pdf)["id"]
    client.app.state.worker.join()

    resp = client.get(f"/documents/{doc_id}/status")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == doc_id
    assert body["filename"] == "doc.pdf"
    assert body["state"] == "ready"
    assert body["error"] is None
    assert (body["pages_total"], body["pages_read"], body["pages_failed"]) == (4, 3, 1)
    assert body["chunks_total"] == body["chunks_embedded"] > 0
    assert body["pages"] == [
        {"page": 1, "method": "tesseract", "ocr_error": None, "error": None},
        {
            "page": 2,
            "method": "pymupdf",
            "ocr_error": "OCR failed on this page: low contrast",
            "error": None,
        },
        {
            "page": 3,
            "method": None,
            "ocr_error": "OCR failed on this page: blank image",
            "error": NO_TEXT_LAYER,
        },
        {
            "page": 4,
            "method": "pymupdf",
            "ocr_error": "OCR response had no entry for this page",
            "error": None,
        },
    ]
    assert body["failures"] == [
        {
            "page": 3,
            "reason": NO_TEXT_LAYER,
            "ocr_error": "OCR failed on this page: blank image",
        }
    ]


def test_text_pdf_without_ocr_states_the_text_layer_on_every_page(start):
    client = start()
    doc_id = upload(client, make_pdf(["SECTION 1\nOne.", "SECTION 2\nTwo."]))["id"]
    client.app.state.worker.join()

    body = client.get(f"/documents/{doc_id}/status").json()

    assert body["state"] == "ready"
    assert [p["method"] for p in body["pages"]] == ["pymupdf", "pymupdf"]
    assert body["failures"] == []
    assert body["pages_failed"] == 0


def test_document_no_page_could_be_read_is_failed_with_every_page_listed(start):
    client = start()
    doc_id = upload(client, make_pdf(["", ""]))["id"]
    client.app.state.worker.join()

    body = client.get(f"/documents/{doc_id}/status").json()

    assert body["state"] == "failed"
    assert body["error"] == "no page could be read"
    assert (body["pages_total"], body["pages_read"], body["pages_failed"]) == (2, 0, 2)
    assert body["failures"] == [
        {"page": 1, "reason": NO_TEXT_LAYER, "ocr_error": None},
        {"page": 2, "reason": NO_TEXT_LAYER, "ocr_error": None},
    ]


def test_queued_document_has_no_pages_yet(start, embedder):
    embedder.release.clear()
    client = start()
    first = upload(client)["id"]
    embedder.entered.wait(10)
    # The worker is held on the first document, so the second has not been started.
    second = upload(client, make_pdf(["SECTION 1\nWaiting."]))["id"]

    body = client.get(f"/documents/{second}/status").json()

    assert body["state"] == "queued"
    assert (body["pages_total"], body["pages_read"], body["pages_failed"]) == (1, 0, 0)
    assert body["pages"] == []
    assert body["failures"] == []
    processing = client.get(f"/documents/{first}/status").json()
    assert processing["state"] == "processing"
    embedder.release.set()
    client.app.state.worker.join()


def test_status_of_unknown_document_is_404(start):
    client = start()

    resp = client.get("/documents/nope/status")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "document not found"}


def test_status_of_deleted_document_is_404(start):
    client = start()
    doc_id = upload(client, make_pdf(["SECTION 1\nGone soon."]))["id"]
    client.app.state.worker.join()
    assert client.delete(f"/documents/{doc_id}").status_code == 204

    resp = client.get(f"/documents/{doc_id}/status")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "document not found"}

import threading
from contextlib import ExitStack

import pymupdf
import pytest
from conftest import require_database_url
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, text

from app.db import (
    chunk_embeddings,
    chunk_text_search,
    chunks,
    collections,
    documents,
    ingestion_status,
    init_db,
    pages,
)
from app.llm import LLMConnectionError, LLMHTTPError
from app.main import create_app

VOCABULARY = ["zebras", "glacier", "budget", "section"]


class FakeEmbedder:
    """One dimension per vocabulary word, plus a constant so no vector is all zeros.

    Each call records the document's status row as the worker left it, which shows
    the progress the worker reported between batches.
    """

    def __init__(self, engine):
        self.engine = engine
        self.calls: list[list[str]] = []
        self.progress: list[dict] = []
        self.release = threading.Event()
        self.release.set()
        self.entered = threading.Event()
        self.error: Exception | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.entered.set()
        self.release.wait(10)
        self.calls.append(texts)
        with self.engine.connect() as conn:
            self.progress.extend(dict(r._mapping) for r in conn.execute(select(ingestion_status)))
        if self.error:
            raise self.error
        return [vector(t) for t in texts]


def vector(text: str) -> list[float]:
    lower = text.lower()
    return [1.0 if word in lower else 0.0 for word in VOCABULARY] + [0.5]


def page_text(n: int) -> str:
    extra = {7: " Several zebras graze on page seven.", 13: " A glacier moves slowly."}
    return (
        f"SECTION {n}\n"
        f"Page {n} opens with a short paragraph about topic {n}.{extra.get(n, '')}\n\n"
        f"A second paragraph on page {n} closes it."
    )


def make_pdf(texts: list[str]) -> bytes:
    doc = pymupdf.open()
    for t in texts:
        page = doc.new_page()
        if t:
            page.insert_text((72, 72), t)
    return doc.tobytes()


TWENTY_PAGES = make_pdf([page_text(n) for n in range(1, 21)])


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("OCR_URL", raising=False)
    for name in ["LLM_BASE_URL", "LLM_FAST_MODEL", "LLM_REASONING_MODEL", "LLM_EMBEDDING_MODEL"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EMBED_BATCH_SIZE", "4")


@pytest.fixture
def embedder(db_engine):
    return FakeEmbedder(db_engine)


@pytest.fixture
def start(db_engine, env, embedder):
    """Start the app (entering runs startup: schema, recovery, worker)."""
    with ExitStack() as stack:
        yield lambda embedder=embedder: stack.enter_context(
            TestClient(create_app(db_engine, embedder=embedder))
        )


def upload(client, data: bytes = TWENTY_PAGES) -> dict:
    resp = client.post(
        "/collections/papers/documents", files={"file": ("doc.pdf", data, "application/pdf")}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def status(engine, document_id: str) -> dict | None:
    query = select(ingestion_status).where(ingestion_status.c.document_id == document_id)
    with engine.connect() as conn:
        row = conn.execute(query).first()
    return dict(row._mapping) if row else None


def count(engine, table, document_id: str) -> int:
    query = select(func.count()).select_from(table).where(table.c.document_id == document_id)
    with engine.connect() as conn:
        return conn.execute(query).scalar_one()


def test_upload_queues_the_document(start, db_engine, embedder):
    embedder.release.clear()
    client = start()

    body = upload(client)

    assert body["status"] == "queued"
    embedder.entered.wait(10)
    embedder.release.set()
    client.app.state.worker.join()


def test_ingest_twenty_pages_reports_progress_and_stores_vectors_and_full_text(
    start, db_engine, embedder
):
    client = start()
    doc_id = upload(client)["id"]
    client.app.state.worker.join()

    final = status(db_engine, doc_id)
    assert final["state"] == "ready", final["error"]
    assert final["error"] is None
    assert (final["pages_total"], final["pages_read"]) == (20, 20)
    n_chunks = count(db_engine, chunks, doc_id)
    assert n_chunks >= 20
    assert final["chunks_total"] == final["chunks_embedded"] == n_chunks
    assert count(db_engine, chunk_embeddings, doc_id) == n_chunks
    assert count(db_engine, chunk_text_search, doc_id) == n_chunks

    # Progress was written between batches, before the document was ready.
    assert len(embedder.calls) == -(-n_chunks // 4)
    assert all(len(batch) <= 4 for batch in embedder.calls)
    seen = [(p["state"], p["pages_read"], p["chunks_embedded"]) for p in embedder.progress]
    assert seen == [("processing", 20, 4 * i) for i in range(len(embedder.calls))]
    assert all(p["chunks_total"] == n_chunks for p in embedder.progress)

    # Each chunk's vector is the one the model returned for its text.
    query = select(chunks.c.text, chunk_embeddings.c.embedding).join(
        chunk_embeddings, chunk_embeddings.c.chunk_id == chunks.c.id
    )
    with db_engine.connect() as conn:
        for chunk_text, embedding in conn.execute(query):
            assert list(embedding) == pytest.approx(vector(chunk_text))


def test_postgres_finds_chunks_by_keyword_and_by_vector(postgres_engine, env):
    embedder = FakeEmbedder(postgres_engine)
    with TestClient(create_app(postgres_engine, embedder=embedder)) as client:
        doc_id = upload(client)["id"]
        client.app.state.worker.join()
    assert status(postgres_engine, doc_id)["state"] == "ready"

    with postgres_engine.connect() as conn:
        # English stemming: "zebra" finds "zebras".
        keyword = conn.execute(
            text(
                "SELECT c.page FROM chunk_text_search s JOIN chunks c ON c.id = s.chunk_id "
                "WHERE s.tsv @@ plainto_tsquery('english', 'zebra')"
            )
        ).scalars()
        assert list(keyword) == [7]
        nearest = conn.execute(
            select(chunks.c.page)
            .join(chunk_embeddings, chunk_embeddings.c.chunk_id == chunks.c.id)
            .order_by(chunk_embeddings.c.embedding.cosine_distance(vector("glacier")))
            .limit(1)
        ).scalar_one()
        assert nearest == 13
        index = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE tablename = 'chunk_text_search'")
        ).scalars()
        assert any("USING gin (tsv)" in d for d in index)


def test_delete_leaves_no_chunk_vector_or_full_text_row(start, db_engine, embedder):
    client = start()
    doc_id = upload(client)["id"]
    keep_id = upload(client, make_pdf(["SECTION 1\nAnother document."]))["id"]
    client.app.state.worker.join()
    assert status(db_engine, doc_id)["state"] == "ready"
    for table in [pages, chunks, chunk_embeddings, chunk_text_search]:
        assert count(db_engine, table, doc_id) > 0

    assert client.delete(f"/documents/{doc_id}").status_code == 204

    for table in [pages, chunks, chunk_embeddings, chunk_text_search, ingestion_status]:
        assert count(db_engine, table, doc_id) == 0, table.name
    # Only the deleted document's rows went.
    assert status(db_engine, keep_id)["state"] == "ready"
    assert count(db_engine, chunk_embeddings, keep_id) == count(db_engine, chunks, keep_id) > 0


@pytest.mark.parametrize("model_fails", [False, True], ids=["model-answers", "model-fails"])
def test_delete_while_ingesting_leaves_nothing_behind(start, db_engine, embedder, model_fails):
    if model_fails:
        embedder.error = LLMHTTPError(500, "lost")
    embedder.release.clear()
    client = start()
    doc_id = upload(client)["id"]
    assert embedder.entered.wait(10)

    assert client.delete(f"/documents/{doc_id}").status_code == 204
    embedder.release.set()
    client.app.state.worker.join()

    for table in [pages, chunks, chunk_embeddings, chunk_text_search, ingestion_status]:
        assert count(db_engine, table, doc_id) == 0, table.name
    # The worker stopped at the first batch instead of embedding the rest.
    assert len(embedder.calls) == 1


def test_embedding_failure_fails_the_document_with_the_reason(start, db_engine, embedder):
    embedder.error = LLMHTTPError(503, "overloaded")
    client = start()
    doc_id = upload(client)["id"]
    client.app.state.worker.join()

    final = status(db_engine, doc_id)
    assert final["state"] == "failed"
    assert "HTTP 503" in final["error"]
    # The pages read before the failure stay, so their per-page results can be shown.
    assert count(db_engine, pages, doc_id) == 20


def test_unreachable_embedding_endpoint_names_llm_base_url(start, db_engine, embedder):
    embedder.error = LLMConnectionError("model endpoint unreachable: refused")
    client = start()
    doc_id = upload(client)["id"]
    client.app.state.worker.join()

    final = status(db_engine, doc_id)
    assert final["state"] == "failed"
    assert "LLM_BASE_URL" in final["error"]


def test_missing_model_settings_fail_the_document(start, db_engine):
    client = start(None)
    doc_id = upload(client)["id"]
    client.app.state.worker.join()

    final = status(db_engine, doc_id)
    assert final["state"] == "failed"
    assert "LLM_BASE_URL is not set" in final["error"]


def test_document_with_no_readable_page_fails(start, db_engine, embedder):
    client = start()
    doc_id = upload(client, make_pdf(["", ""]))["id"]
    client.app.state.worker.join()

    final = status(db_engine, doc_id)
    assert final["state"] == "failed"
    # No text layer on any page and OCR off (`env` unsets OCR_URL): it is a scan.
    assert final["error"] == SCAN_WITH_OCR_OFF
    assert (final["pages_total"], final["pages_read"]) == (2, 0)
    assert count(db_engine, pages, doc_id) == 2
    assert embedder.calls == []


SCAN_WITH_OCR_OFF = (
    "This PDF has no text layer (a scan or blank pages); OCR is off (set OCR_URL to read scans)"
)


def test_a_scan_with_ocr_off_says_it_is_a_scan_and_ocr_is_off(start, db_engine):
    client = start()
    image_only = pymupdf.open()
    page = image_only.new_page()
    # A picture of text and no text layer, the way a scanner writes a page.
    pixmap = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 40, 20), False)
    pixmap.clear_with(200)
    page.insert_image(pymupdf.Rect(72, 72, 272, 172), pixmap=pixmap)
    doc_id = upload(client, image_only.tobytes())["id"]
    client.app.state.worker.join()

    final = status(db_engine, doc_id)
    assert final["state"] == "failed"
    assert final["error"] == SCAN_WITH_OCR_OFF


def test_a_scan_with_ocr_unreachable_keeps_the_ocr_error_not_the_scan_text(
    start, db_engine, monkeypatch
):
    # Nothing listens on port 9 (discard) here, so the OCR call fails to connect.
    monkeypatch.setenv("OCR_URL", "http://127.0.0.1:9")
    client = start()
    doc_id = upload(client, make_pdf(["", ""]))["id"]
    client.app.state.worker.join()

    final = status(db_engine, doc_id)
    assert final["state"] == "failed"
    assert final["error"] == "no page could be read"
    with db_engine.connect() as conn:
        ocr_errors = conn.execute(
            select(pages.c.ocr_error).where(pages.c.document_id == doc_id)
        ).scalars()
        assert all(e and e.startswith("OCR service unreachable") for e in ocr_errors)


def add_document(engine, document_id: str, state: str) -> None:
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
                page_count=20,
            )
        )
        conn.execute(
            insert(ingestion_status).values(document_id=document_id, state=state, pages_total=20)
        )


def test_document_left_processing_is_failed_on_restart(start, db_engine, embedder, tmp_path):
    init_db(db_engine)
    add_document(db_engine, "interrupted", "processing")
    add_document(db_engine, "finished", "ready")
    add_document(db_engine, "broken", "failed")

    client = start()
    client.app.state.worker.join()

    interrupted = status(db_engine, "interrupted")
    assert interrupted["state"] == "failed"
    assert "interrupted" in interrupted["error"]
    assert status(db_engine, "finished")["state"] == "ready"
    assert status(db_engine, "broken")["state"] == "failed"
    assert embedder.calls == []


def test_queued_document_is_ingested_on_restart(start, db_engine, embedder, tmp_path):
    init_db(db_engine)
    add_document(db_engine, "waiting", "queued")
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "waiting.pdf").write_bytes(make_pdf(["SECTION 1\nStill here."]))

    client = start()
    client.app.state.worker.join()

    assert status(db_engine, "waiting")["state"] == "ready"
    assert count(db_engine, chunk_embeddings, "waiting") == 1


def test_shutdown_waits_for_the_running_document(db_engine, env, embedder):
    embedder.release.clear()
    with TestClient(create_app(db_engine, embedder=embedder)) as client:
        doc_id = upload(client)["id"]
        assert embedder.entered.wait(10)
        threading.Timer(0.2, embedder.release.set).start()

    assert status(db_engine, doc_id)["state"] == "ready"


def test_shutdown_leaves_documents_not_yet_started_queued(db_engine, env, embedder):
    embedder.release.clear()
    with TestClient(create_app(db_engine, embedder=embedder)) as client:
        first = upload(client)["id"]
        assert embedder.entered.wait(10)
        second = upload(client)["id"]
        threading.Timer(0.2, embedder.release.set).start()

    assert status(db_engine, first)["state"] == "ready"
    assert status(db_engine, second)["state"] == "queued"


@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_bad_batch_size_is_rejected(db_engine, env, monkeypatch, value):
    monkeypatch.setenv("EMBED_BATCH_SIZE", value)
    with pytest.raises(ValueError, match="EMBED_BATCH_SIZE"):
        create_app(db_engine)


def test_postgres_tests_fail_without_database_url():
    for env in [{}, {"DATABASE_URL": "  "}, {"DATABASE_URL": "sqlite:///x.db"}]:
        with pytest.raises(pytest.fail.Exception, match="DATABASE_URL"):
            require_database_url(env)
    url = "postgresql+psycopg://u:p@h/db"
    assert require_database_url({"DATABASE_URL": url}) == url

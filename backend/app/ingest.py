"""Ingest uploaded documents in the background: read, chunk, index for keywords, embed.

One worker thread runs inside the app process and takes documents one at a time. Each
document has an `ingestion_status` row: `queued` at upload, `processing` while the
worker has it, then `ready` or `failed` with the reason. The worker writes progress
(pages read, chunks embedded) as it goes, after the pages are read and after every
embedding batch of `EMBED_BATCH_SIZE` chunks (default 64).

A document uploaded with a visitor's preset and key (`app.credentials`) is embedded with
them. They travel with the document in the worker's queue, in memory only: never in a row,
a file or a log line.

Nothing survives a stop but the status rows. On startup a document left `processing`
was cut off part way, so it is marked failed; a document still `queued` had not been
started, so it is queued again. In a public demo (`PUBLIC_DEMO`) the key a document was
uploaded with is gone after a stop, so both are marked failed and the visitor uploads it
again. This assumes one app process per database.

A document deleted while it is being ingested is dropped quietly: its rows cascade
away, and the worker stops at its next write.
"""

import logging
import os
import queue
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import ColumnElement, Engine, exists, func, insert, literal_column, select, update
from sqlalchemy.engine import Connection

from app.credentials import Credentials, client_for, plain_model_error
from app.db import (
    TEXT_SEARCH_CONFIG,
    chunk_embeddings,
    chunk_text_search,
    chunks,
    documents,
    ingestion_status,
)
from app.documents import Limits, stored_path
from app.llm import LLMClient, LLMConfig, LLMConnectionError, public_demo
from app.pages import store_pages
from app.reader import NO_TEXT_LAYER, PageRead, read_pdf

DEFAULT_EMBED_BATCH_SIZE = 64
INTERRUPTED = "ingestion was interrupted: the server stopped while this document was processing"
DEMO_INTERRUPTED = "ingestion was interrupted when the server restarted: upload the document again"
NO_PAGE_READ = "no page could be read"
SCAN_WITH_OCR_OFF = (
    "This PDF has no text layer (a scan or blank pages); OCR is off (set OCR_URL to read scans)"
)

log = logging.getLogger(__name__)


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def batch_size_from_env() -> int:
    raw = os.environ.get("EMBED_BATCH_SIZE", "").strip()
    if not raw:
        return DEFAULT_EMBED_BATCH_SIZE
    try:
        size = int(raw)
    except ValueError:
        size = 0
    if size < 1:
        raise ValueError(f"EMBED_BATCH_SIZE must be a positive integer, got {raw!r}")
    return size


class _Gone(Exception):
    """The document was deleted while it was being ingested."""


@dataclass(frozen=True)
class _Job:
    document_id: str
    # The visitor's preset and key, held in memory only; `None` embeds with the default.
    credentials: Credentials | None = field(default=None, repr=False)


_STOP = object()


class IngestWorker:
    def __init__(
        self,
        engine: Engine,
        limits: Limits,
        embedder: Embedder | None = None,
        batch_size: int = DEFAULT_EMBED_BATCH_SIZE,
    ):
        self.engine = engine
        self.limits = limits
        self.embedder = embedder
        self.batch_size = batch_size
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def start(self) -> None:
        """Recover what the last run left behind, then start taking documents."""
        with self.engine.begin() as conn:
            if public_demo():
                # The keys these documents were uploaded with did not survive the stop.
                conn.execute(
                    update(ingestion_status)
                    .where(ingestion_status.c.state.in_(["queued", "processing"]))
                    .values(state="failed", error=DEMO_INTERRUPTED)
                )
            conn.execute(
                update(ingestion_status)
                .where(ingestion_status.c.state == "processing")
                .values(state="failed", error=INTERRUPTED)
            )
            waiting = conn.execute(
                select(ingestion_status.c.document_id)
                .where(ingestion_status.c.state == "queued")
                .order_by(ingestion_status.c.updated_at)
            ).scalars()
            for document_id in waiting:
                self.submit(document_id)
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="ingest-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Finish the document in hand. Documents still queued stay `queued` for next start."""
        if self._thread is None:
            return
        self._stopping.set()
        # Wakes the worker if it is waiting on an empty queue.
        self._queue.put(_STOP)
        self._thread.join()
        self._thread = None

    def submit(self, document_id: str, credentials: Credentials | None = None) -> None:
        """Queue the document; `credentials`, when given, are the ones it is embedded with."""
        self._queue.put(_Job(document_id, credentials))

    def join(self) -> None:
        """Wait until every submitted document has been handled."""
        self._queue.join()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP or self._stopping.is_set():
                    self._drain()
                    return
                self.ingest(item.document_id, item.credentials)
            except Exception:
                log.exception("ingestion of %s could not record its result", item.document_id)
            finally:
                self._queue.task_done()

    def _drain(self) -> None:
        """Drop what is left in the queue; those documents are still `queued` in the database."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
            self._queue.task_done()

    def ingest(self, document_id: str, credentials: Credentials | None = None) -> None:
        with self.engine.begin() as conn:
            started = conn.execute(
                update(ingestion_status)
                .where(
                    ingestion_status.c.document_id == document_id,
                    ingestion_status.c.state == "queued",
                )
                .values(state="processing", error=None)
            ).rowcount
        if not started:
            return
        try:
            self._ingest(document_id, credentials)
        except _Gone:
            return
        except Exception as exc:  # noqa: BLE001 -- any failure is recorded as the reason.
            if not self._exists(document_id):
                return
            log.warning("ingestion of %s failed: %s", document_id, exc)
            # A key or code failure says the shared plain wording; nothing from the
            # endpoint's body goes into the reason the client reads.
            reason = plain_model_error(exc, credentials)
            if reason is None:
                if isinstance(exc, LLMConnectionError):
                    reason = "the model endpoint is unreachable (check LLM_BASE_URL)"
                else:
                    reason = str(exc) or type(exc).__name__
            with self.engine.begin() as conn:
                _set(conn, document_id, state="failed", error=reason)

    def _ingest(self, document_id: str, credentials: Credentials | None) -> None:
        read = read_pdf(stored_path(self.limits, document_id))
        pages_read = sum(1 for page in read if not page.failed)
        store_pages(self.engine, document_id, read)

        with self.engine.begin() as conn:
            conn.execute(
                insert(chunk_text_search).from_select(
                    ["chunk_id", "document_id", "tsv"],
                    select(
                        chunks.c.id, chunks.c.document_id, _to_tsvector(self.engine, chunks.c.text)
                    ).where(chunks.c.document_id == document_id),
                )
            )
            rows = conn.execute(
                select(chunks.c.id, chunks.c.text)
                .where(chunks.c.document_id == document_id)
                .order_by(chunks.c.seq)
            ).all()
            _set(
                conn,
                document_id,
                pages_read=pages_read,
                chunks_total=len(rows),
                chunks_embedded=0,
            )
        if pages_read == 0:
            raise RuntimeError(_no_page_read_reason(read))

        with self._embedder(credentials) as embedder:
            for start in range(0, len(rows), self.batch_size):
                batch = rows[start : start + self.batch_size]
                # No transaction is open while the model works, so a delete is never blocked.
                vectors = embedder.embed([row.text for row in batch])
                with self.engine.begin() as conn:
                    _set(conn, document_id, chunks_embedded=start + len(batch))
                    conn.execute(
                        insert(chunk_embeddings),
                        [
                            {"chunk_id": row.id, "document_id": document_id, "embedding": vector}
                            for row, vector in zip(batch, vectors, strict=True)
                        ],
                    )

        with self.engine.begin() as conn:
            _set(conn, document_id, state="ready")

    @contextmanager
    def _embedder(self, credentials: Credentials | None) -> Iterator[Embedder]:
        """A client for the upload's preset and key, else the injected embedder, else a
        model client from the environment; a client made here is closed after use."""
        if credentials is None and self.embedder is not None:
            yield self.embedder
            return
        client = client_for(credentials) if credentials else LLMClient(LLMConfig.from_env())
        try:
            yield client
        finally:
            client.close()

    def _exists(self, document_id: str) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(select(exists().where(documents.c.id == document_id))).scalar()


def _no_page_read_reason(read: list[PageRead]) -> str:
    """Why a document with no readable page failed. With OCR off (no page carries an OCR
    error) and no text layer on any page, it is a scan that only OCR could read."""
    if all(page.error == NO_TEXT_LAYER and page.ocr_error is None for page in read):
        return SCAN_WITH_OCR_OFF
    return NO_PAGE_READ


def _to_tsvector(engine: Engine, column: ColumnElement) -> ColumnElement:
    if engine.dialect.name == "postgresql":
        return func.to_tsvector(literal_column(f"'{TEXT_SEARCH_CONFIG}'::regconfig"), column)
    return column


def _set(conn: Connection, document_id: str, **values) -> None:
    updated = conn.execute(
        update(ingestion_status)
        .where(ingestion_status.c.document_id == document_id)
        .values(**values)
    ).rowcount
    if not updated:
        raise _Gone(document_id)

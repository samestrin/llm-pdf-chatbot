"""Database schema.

Every table that hangs off a document references it with `ON DELETE CASCADE`,
so deleting the document row removes everything derived from it.
"""

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Engine,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR

# Postgres is the real store. SQLite keeps the same tables for quick local tests: a vector
# is a JSON list there, and the "tsvector" is the plain chunk text, with no keyword index.
Embedding = VECTOR().with_variant(JSON(), "sqlite")
SearchVector = TSVECTOR().with_variant(Text(), "sqlite")
TEXT_SEARCH_CONFIG = "english"

INGESTION_STATES = ("queued", "processing", "ready", "failed")

metadata = MetaData()

collections = Table(
    "collections",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(200), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

documents = Table(
    "documents",
    metadata,
    Column("id", String(32), primary_key=True),
    Column(
        "collection_id",
        Integer,
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    # The client's file name, for display only. It is never used as a path.
    Column("filename", String(255), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("page_count", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


# One row per page of a document, as the reader returned it. `text` is the exact page
# text that chunk offsets index into, so a citation can be resolved back to it.
pages = Table(
    "pages",
    metadata,
    Column(
        "document_id",
        String(32),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("page", Integer, primary_key=True),
    Column("text", Text),
    Column("method", String(32)),
    Column("ocr_error", Text),
    Column("error", Text),
)

chunks = Table(
    "chunks",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("document_id", String(32), nullable=False, index=True),
    Column("page", Integer, nullable=False),
    # Order of the chunk within its document.
    Column("seq", Integer, nullable=False),
    # `pages.text[start_offset:end_offset] == text`, in characters (code points).
    Column("start_offset", Integer, nullable=False),
    Column("end_offset", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("heading", Text),
    # `[x0, y0, x1, y1]` in PDF points, only when the reader returned boxes.
    Column("bbox", JSON(none_as_null=True)),
    ForeignKeyConstraint(
        ["document_id", "page"], ["pages.document_id", "pages.page"], ondelete="CASCADE"
    ),
    CheckConstraint("start_offset >= 0 AND end_offset > start_offset", name="chunk_offsets"),
)


# One row per uploaded document: where its ingestion is, and how far it has got. The worker
# updates it after reading the pages and after every embedding batch.
ingestion_status = Table(
    "ingestion_status",
    metadata,
    Column(
        "document_id",
        String(32),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("state", String(16), nullable=False),
    Column("pages_total", Integer, nullable=False),
    # Pages some method read text from; the rest are failed pages, listed in `pages`.
    Column("pages_read", Integer, nullable=False, server_default="0"),
    Column("chunks_total", Integer, nullable=False, server_default="0"),
    Column("chunks_embedded", Integer, nullable=False, server_default="0"),
    # Why the document failed, when `state` is failed.
    Column("error", Text),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    ),
    CheckConstraint(
        "state IN (" + ", ".join(f"'{s}'" for s in INGESTION_STATES) + ")",
        name="ingestion_state",
    ),
)

# A chunk's vector from the embedding model, for semantic search (pgvector).
chunk_embeddings = Table(
    "chunk_embeddings",
    metadata,
    Column("chunk_id", Integer, ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True),
    Column("document_id", String(32), nullable=False, index=True),
    # No fixed dimension: it is whatever the configured embedding model returns.
    Column("embedding", Embedding, nullable=False),
)

# A chunk's text as a tsvector, for keyword search through the GIN index.
chunk_text_search = Table(
    "chunk_text_search",
    metadata,
    Column("chunk_id", Integer, ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True),
    Column("document_id", String(32), nullable=False, index=True),
    Column("tsv", SearchVector, nullable=False),
)
Index("chunk_text_search_tsv", chunk_text_search.c.tsv, postgresql_using="gin").ddl_if(
    dialect="postgresql"
)


def _enable_sqlite_foreign_keys(dbapi_conn, _record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db(engine: Engine) -> None:
    # SQLite ignores foreign keys, and so ON DELETE CASCADE, unless each connection opts in.
    if engine.dialect.name == "sqlite" and not event.contains(
        engine, "connect", _enable_sqlite_foreign_keys
    ):
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        engine.dispose()
    if engine.dialect.name == "postgresql":
        # Compose creates it in db/init; a bare Postgres (as in CI) needs it here.
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    metadata.create_all(engine)

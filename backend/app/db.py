"""Database schema.

Every table that hangs off a document references it with `ON DELETE CASCADE`,
so deleting the document row removes everything derived from it.
"""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Engine,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    event,
    func,
)

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
    metadata.create_all(engine)

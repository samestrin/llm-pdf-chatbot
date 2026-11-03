"""Database schema.

Every table that hangs off a document references it with `ON DELETE CASCADE`,
so deleting the document row removes everything derived from it.
"""

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
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

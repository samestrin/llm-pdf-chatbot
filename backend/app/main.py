import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import documents
from app.db import init_db
from app.ingest import Embedder, IngestWorker, batch_size_from_env

DEFAULT_DATABASE_URL = "sqlite:///./app.db"


def make_engine() -> Engine:
    return create_engine(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))


def create_app(engine: Engine | None = None, embedder: Embedder | None = None) -> FastAPI:
    """`embedder` replaces the model client from `backend/app/llm.py` (for tests)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(app.state.engine)
        app.state.worker.start()
        try:
            yield
        finally:
            app.state.worker.stop()

    app = FastAPI(title="llm-pdf-chatbot", lifespan=lifespan)
    app.state.engine = engine or make_engine()
    app.state.limits = documents.Limits.from_env()
    app.state.worker = IngestWorker(
        app.state.engine, app.state.limits, embedder, batch_size=batch_size_from_env()
    )
    app.include_router(documents.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        try:
            with app.state.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {"status": "ok"}

    return app


app = create_app()

import os
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import answer, documents
from app.db import init_db
from app.ingest import Embedder, IngestWorker, batch_size_from_env
from app.llm import LLMClient

DEFAULT_DATABASE_URL = "sqlite:///./app.db"


def make_engine() -> Engine:
    return create_engine(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))


def package_version() -> str:
    """Read pyproject.toml beside app/: the project is never installed, so metadata has no version."""
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text())["project"]["version"]


def create_app(
    engine: Engine | None = None,
    embedder: Embedder | None = None,
    llm: LLMClient | None = None,
) -> FastAPI:
    """`embedder` and `llm` replace the model clients made from the environment (for tests)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(app.state.engine)
        app.state.worker.start()
        try:
            yield
        finally:
            app.state.worker.stop()

    app = FastAPI(title="llm-pdf-chatbot", version=package_version(), lifespan=lifespan)
    app.state.engine = engine or make_engine()
    app.state.limits = documents.Limits.from_env()
    app.state.embedder = embedder
    app.state.llm = llm
    app.state.worker = IngestWorker(
        app.state.engine, app.state.limits, embedder, batch_size=batch_size_from_env()
    )
    app.include_router(documents.router)
    app.include_router(answer.router)

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

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import documents
from app.db import init_db

DEFAULT_DATABASE_URL = "sqlite:///./app.db"


def make_engine() -> Engine:
    return create_engine(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))


def create_app(engine: Engine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(app.state.engine)
        yield

    app = FastAPI(title="llm-pdf-chatbot", lifespan=lifespan)
    app.state.engine = engine or make_engine()
    app.state.limits = documents.Limits.from_env()
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

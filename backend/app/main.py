import os

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

DEFAULT_DATABASE_URL = "sqlite:///./app.db"


def make_engine() -> Engine:
    return create_engine(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))


def create_app(engine: Engine | None = None) -> FastAPI:
    app = FastAPI(title="llm-pdf-chatbot")
    app.state.engine = engine or make_engine()

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

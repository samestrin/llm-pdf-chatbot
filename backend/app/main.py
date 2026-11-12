import os
import re
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import answer, documents
from app.db import init_db
from app.ingest import Embedder, IngestWorker, batch_size_from_env
from app.llm import LLMClient

DEFAULT_DATABASE_URL = "sqlite:///./app.db"
# The API reference page and the vendored Scalar script it loads (see static/SOURCES.md).
STATIC_DIR = Path(__file__).parent / "static"
# A path prefix the app is served under, as the frontend's nginx and Vite's dev proxy send it
# in X-Forwarded-Prefix: one or more plain segments, no scheme, host, query or trailing slash.
FORWARDED_PREFIX = re.compile(r"(/[A-Za-z0-9._~-]+)+")


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

    app = FastAPI(
        title="llm-pdf-chatbot",
        version=package_version(),
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
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

    # Scalar's "Test Request" and code samples call the spec's server URL plus the path, or
    # the page's origin when the spec names no server. Through the frontend the API is under
    # /api, which nginx says in X-Forwarded-Prefix, so that spec names /api; called directly
    # (port 8000, `uvicorn` on its own) it names no server and the origin is right. The
    # cached schema is never changed, so docs/openapi.json and its `paths` stay as they are.
    @app.get("/openapi.json", include_in_schema=False)
    def openapi(request: Request) -> JSONResponse:
        spec = app.openapi()
        prefix = request.headers.get("x-forwarded-prefix", "")
        if FORWARDED_PREFIX.fullmatch(prefix):
            spec = {**spec, "servers": [{"url": prefix}]}
        return JSONResponse(spec)

    # The reference is a page, not an endpoint: both routes stay out of the schema, and
    # /openapi.json is unchanged. The page names the spec and the script by relative URL,
    # so it works at /docs on port 8000 and at /api/docs behind the frontend's nginx.
    @app.get("/docs", include_in_schema=False)
    def api_reference() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "docs.html").read_text(encoding="utf-8"))

    @app.get("/docs/scalar.js", include_in_schema=False)
    def scalar_script() -> FileResponse:
        return FileResponse(STATIC_DIR / "scalar.js", media_type="text/javascript")

    return app


app = create_app()

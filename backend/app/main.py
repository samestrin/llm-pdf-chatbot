import os
import re
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import answer, documents, session
from app.credentials import PRESETS, public_presets, request_credentials
from app.db import init_db
from app.ingest import Embedder, IngestWorker, batch_size_from_env
from app.llm import LLMClient, public_demo

DEFAULT_DATABASE_URL = "sqlite:///./app.db"
# The API reference page and the vendored Scalar script it loads (see static/SOURCES.md).
STATIC_DIR = Path(__file__).parent / "static"
# A path prefix the app is served under, as the frontend's nginx and Vite's dev proxy send it
# in X-Forwarded-Prefix: one or more plain segments, no scheme, host, query or trailing slash.
FORWARDED_PREFIX = re.compile(r"(/[A-Za-z0-9._~-]+)+")


class PresetInfo(BaseModel):
    """What the page may know of a preset: no base URL, gateway or key."""

    id: str
    label: str
    kind: Literal["key", "demo"]
    chat_model: str
    # `null` when the preset cannot embed: it cannot take uploads.
    embedding_model: str | None


class PresetList(BaseModel):
    # Whether every model call needs a demo code or the visitor's own key.
    public_demo: bool
    presets: list[PresetInfo]


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
        sweeper = None
        if public_demo():
            sweeper = session.UploadSweeper(
                app.state.engine, app.state.limits.upload_dir, session.upload_ttl()
            )
            sweeper.sweep()
            sweeper.start()
        app.state.worker.start()
        try:
            yield
        finally:
            app.state.worker.stop()
            if sweeper is not None:
                sweeper.stop()

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
    # A visitor session's caps; outside a public demo there are none.
    app.state.session_caps = session.SessionCaps.from_env() if public_demo() else None
    app.state.embedder = embedder
    app.state.llm = llm
    app.state.presets = PRESETS
    app.state.worker = IngestWorker(
        app.state.engine, app.state.limits, embedder, batch_size=batch_size_from_env()
    )
    # In a public demo every request has a session (`app.session`); its collections are its own.
    app.add_middleware(session.SessionMiddleware, secret=session.session_secret())
    # Every route reads the model headers, so a bad preset or key is a 400 before any work.
    credentials = [Depends(request_credentials)]
    app.include_router(documents.router, dependencies=credentials)
    app.include_router(answer.router, dependencies=credentials)

    @app.get("/presets", summary="The model presets a visitor can pick")
    def presets() -> PresetList:
        return PresetList(
            public_demo=public_demo(),
            presets=[PresetInfo(**p) for p in public_presets(app.state.presets)],
        )

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

"""Who a collection belongs to: the visitor's session in a public demo, one shared owner
otherwise.

In a public demo (`PUBLIC_DEMO`) each browser holds a signed, HttpOnly session cookie, with
no account behind it. A collection belongs to the session that made it, and every route
answers 404 for another session's collection or document, exactly as for one that does not
exist. Collections of the shared owner (`SHARED_OWNER`, made with `PUBLIC_DEMO` off) are the
read-only sample: every session can read and ask them, none can change or delete them.
A visitor's collection is deleted, with its documents, chunks and files, once it is older
than `DEMO_UPLOAD_TTL_MINUTES`.

The cookie goes with any request the browser sends here, so every write it allows (an
upload, either delete) is refused (403) when it comes from another site: its `Origin` is not
the app's, or a browser sent none. A session has caps on how many collections it keeps and
how many bytes its documents hold (`DEMO_SESSION_MAX_COLLECTIONS`,
`DEMO_SESSION_MAX_UPLOAD_MB`); the upload past either is refused (409) until a delete or the
TTL frees room.

Outside a public demo there is no cookie: every request is the shared owner, as before.
The cookie is signed with `SESSION_SECRET`; without it, with a secret made at startup, so a
restart starts every visitor on a new session.
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from sqlalchemy import Engine, delete, exists, func, select, text
from sqlalchemy.engine import Connection, Row
from starlette.requests import cookie_parser

from app.db import SHARED_OWNER, collections, documents
from app.llm import public_demo

log = logging.getLogger(__name__)

SHARED = SHARED_OWNER
COOKIE = "pdfchat_session"
SESSION_ID = re.compile(r"[0-9a-f]{32}")
DEFAULT_UPLOAD_TTL_MINUTES = 60
SWEEP_SECONDS = 60.0
READ_ONLY = "The sample collection is read-only"
CROSS_SITE = "This request came from another site and was refused"
DEFAULT_SESSION_MAX_COLLECTIONS = 5
DEFAULT_SESSION_MAX_UPLOAD_MB = 100


def session_secret(env: Mapping[str, str] | None = None) -> bytes:
    env = os.environ if env is None else env
    configured = env.get("SESSION_SECRET", "")
    return configured.encode() if configured else secrets.token_bytes(32)


def _signature(secret: bytes, sid: str) -> str:
    return hmac.new(secret, sid.encode(), hashlib.sha256).hexdigest()


def sign(secret: bytes, sid: str) -> str:
    return f"{sid}.{_signature(secret, sid)}"


def verify(secret: bytes, value: str) -> str | None:
    """The session id in a cookie value, or `None` when it is not one this server signed."""
    sid, _, signature = value.partition(".")
    if not SESSION_ID.fullmatch(sid):
        return None
    return sid if hmac.compare_digest(signature, _signature(secret, sid)) else None


class SessionMiddleware:
    """In a public demo, puts the request's session id in `request.state.session_id`, and
    sets the cookie when the request had no valid one. Outside a demo it does nothing."""

    def __init__(self, app, secret: bytes):
        self.app = app
        self.secret = secret

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not public_demo():
            await self.app(scope, receive, send)
            return
        headers: dict[str, list[str]] = {}
        for key, value in scope["headers"]:
            headers.setdefault(key.decode("latin-1").lower(), []).append(value.decode("latin-1"))
        # HTTP/2 may send the cookies as several headers. Starlette's parser skips a malformed
        # cookie where the standard library's drops every cookie in the header.
        jar = cookie_parser("; ".join(headers.get("cookie", [])))
        sid = verify(self.secret, jar[COOKIE]) if COOKIE in jar else None
        new = sid is None
        if new:
            sid = uuid.uuid4().hex
        scope.setdefault("state", {})["session_id"] = sid
        secure = scope.get("scheme") == "https" or headers.get("x-forwarded-proto") == ["https"]

        async def send_with_cookie(message):
            if new and message["type"] == "http.response.start":
                cookie = f"{COOKIE}={sign(self.secret, sid)}; Path=/; HttpOnly; SameSite=Lax"
                if secure:
                    cookie += "; Secure"
                message = {
                    **message,
                    "headers": [*message.get("headers", []), (b"set-cookie", cookie.encode())],
                }
            await send(message)

        await self.app(scope, receive, send_with_cookie)


def current_session(request: Request) -> str:
    """The request's owner: its session in a public demo, the shared owner otherwise."""
    return getattr(request.state, "session_id", SHARED)


def visible_owners(session: str) -> tuple[str, ...]:
    """Whose collections a session may read: its own and the shared sample."""
    return (session,) if session == SHARED else (session, SHARED)


def find_collection(conn: Connection, session: str, name: str) -> Row | None:
    """The named collection this session may read, its own before the sample."""
    return conn.execute(
        select(
            collections.c.id,
            collections.c.session_id,
            collections.c.embedding_preset,
            collections.c.embedding_model,
        )
        .where(collections.c.name == name, collections.c.session_id.in_(visible_owners(session)))
        .order_by((collections.c.session_id == SHARED).asc())
        .limit(1)
    ).first()


def require_owner(session: str, owner: str) -> None:
    """A 403 when a session would change the sample, the one collection it may read and not
    write. Another session's collection never gets here: it is not found."""
    if owner != session:
        raise HTTPException(403, READ_ONLY)


def app_origins(env: Mapping[str, str] | None = None) -> set[str]:
    """The origins set in `DEMO_ORIGIN` (comma-separated), as a browser writes them."""
    env = os.environ if env is None else env
    return {o.strip().rstrip("/").lower() for o in env.get("DEMO_ORIGIN", "").split(",")} - {""}


def _same_host(origin: str, host: str) -> bool:
    """Whether `origin` names the host the request was sent to. The port is not compared:
    the frontend's nginx forwards the host without it."""
    try:
        parts = urlsplit(origin)
        return (
            parts.scheme in {"http", "https"} and parts.hostname == urlsplit(f"//{host}").hostname
        )
    except ValueError:
        return False


def same_origin(request: Request) -> None:
    """In a public demo, a 403 for a write from another site: one whose `Origin` is not the
    app's (`DEMO_ORIGIN` when set, else the request's own host), or a browser's with none
    (a browser sends `Sec-Fetch-Site`, and always `Origin` with a POST or DELETE). A request
    with neither is not a browser's, so holds no visitor's cookie, and goes through."""
    if not public_demo():
        return
    origin = request.headers.get("origin")
    if origin is None:
        if "sec-fetch-site" in request.headers:
            raise HTTPException(403, CROSS_SITE)
        return
    configured = app_origins()
    if configured:
        allowed = origin.rstrip("/").lower() in configured
    else:
        allowed = _same_host(origin, request.headers.get("host", ""))
    if not allowed:
        raise HTTPException(403, CROSS_SITE)


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"{name} must be a whole number above 0, got {raw!r}")
    return int(raw)


@dataclass(frozen=True)
class SessionCaps:
    """What one visitor session may hold: collections, and bytes of uploaded documents."""

    collections: int
    upload_bytes: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SessionCaps":
        env = os.environ if env is None else env
        return cls(
            collections=_positive_int(
                env, "DEMO_SESSION_MAX_COLLECTIONS", DEFAULT_SESSION_MAX_COLLECTIONS
            ),
            upload_bytes=_positive_int(
                env, "DEMO_SESSION_MAX_UPLOAD_MB", DEFAULT_SESSION_MAX_UPLOAD_MB
            )
            * 1024
            * 1024,
        )


def cap_refusal(
    conn: Connection, session: str, collection_id: int, size: int, caps: SessionCaps
) -> str | None:
    """Why adding `size` bytes to the session's collection `collection_id` would pass a cap,
    or `None` when it would not. Run it in the transaction that adds the document: on
    Postgres it holds the session's lock until then, so two uploads cannot both fit."""
    if conn.dialect.name == "postgresql":
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:session))"), {"session": session})
    # An empty collection is not counted: it is the one being made, or one about to go.
    kept = set(
        conn.execute(
            select(collections.c.id).where(
                collections.c.session_id == session,
                exists().where(documents.c.collection_id == collections.c.id),
            )
        ).scalars()
    )
    if collection_id not in kept and len(kept) >= caps.collections:
        return (
            f"This session has reached its limit of {caps.collections} collections; "
            "delete one to make another"
        )
    used = conn.execute(
        select(func.coalesce(func.sum(documents.c.size_bytes), 0))
        .join(collections, collections.c.id == documents.c.collection_id)
        .where(collections.c.session_id == session)
    ).scalar_one()
    if used + size > caps.upload_bytes:
        return (
            f"This session has reached its upload limit of {_megabytes(caps.upload_bytes)}; "
            "delete a document to upload more"
        )
    return None


def _megabytes(size: int) -> str:
    mb = size / (1024 * 1024)
    return f"{mb:g} MB" if mb >= 1 else f"{size} bytes"


def upload_ttl(env: Mapping[str, str] | None = None) -> timedelta:
    env = os.environ if env is None else env
    raw = env.get("DEMO_UPLOAD_TTL_MINUTES", "").strip()
    if not raw:
        return timedelta(minutes=DEFAULT_UPLOAD_TTL_MINUTES)
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"DEMO_UPLOAD_TTL_MINUTES must be a whole number of minutes, got {raw!r}")
    return timedelta(minutes=int(raw))


def expire_uploads(
    engine: Engine, upload_dir: Path, ttl: timedelta, now: datetime | None = None
) -> int:
    """Delete every visitor collection older than `ttl`, with its documents, every row
    derived from them (by foreign-key cascade) and their files. The sample never expires.
    Returns how many collections were deleted."""
    cutoff = (now or datetime.now(UTC)) - ttl
    if engine.dialect.name == "sqlite":
        # SQLite keeps the timestamp as naive UTC text.
        cutoff = cutoff.astimezone(UTC).replace(tzinfo=None)
    expired = select(collections.c.id).where(
        collections.c.session_id != SHARED, collections.c.created_at < cutoff
    )
    with engine.begin() as conn:
        # The lock holds off an upload into these collections until they are gone, so no
        # document can be added between reading their documents and deleting them.
        ids = list(conn.execute(expired.with_for_update()).scalars())
        if not ids:
            return 0
        document_ids = list(
            conn.execute(select(documents.c.id).where(documents.c.collection_id.in_(ids))).scalars()
        )
        conn.execute(delete(collections).where(collections.c.id.in_(ids)))
    for document_id in document_ids:
        # Ids the server generated, so safe in a path; see `documents.stored_path`.
        (upload_dir / f"{document_id}.pdf").unlink(missing_ok=True)
    return len(ids)


class UploadSweeper:
    """Runs `expire_uploads` every `interval` seconds on a thread of its own."""

    def __init__(self, engine: Engine, upload_dir: Path, ttl: timedelta, interval=SWEEP_SECONDS):
        self.engine = engine
        self.upload_dir = upload_dir
        self.ttl = ttl
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sweep(self) -> None:
        removed = expire_uploads(self.engine, self.upload_dir, self.ttl)
        if removed:
            log.info("deleted %d expired visitor collection(s)", removed)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="upload-sweeper", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.sweep()
            except Exception:
                log.exception("deleting expired visitor collections failed")

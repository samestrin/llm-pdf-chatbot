import json
import os
import threading
import time
import uuid
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import create_engine, text


def require_database_url(env: Mapping[str, str] | None = None) -> str:
    """The Postgres URL for the Postgres tests. They fail, never skip, without one."""
    env = os.environ if env is None else env
    url = env.get("DATABASE_URL", "").strip()
    if not url.startswith("postgresql"):
        pytest.fail(
            "DATABASE_URL must point at a Postgres database with pgvector for the Postgres "
            f"tests (got {url!r}); see 'Backend tests' in the README",
            pytrace=False,
        )
    return url


@pytest.fixture(scope="session")
def postgres_url() -> str:
    url = require_database_url()
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    engine.dispose()
    return url


@pytest.fixture
def postgres_engine(postgres_url):
    """An engine on a schema of its own, dropped after the test."""
    schema = f"test_{uuid.uuid4().hex}"
    admin = create_engine(postgres_url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(postgres_url, connect_args={"options": f"-csearch_path={schema},public"})
    yield engine
    engine.dispose()
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture(params=["sqlite", "postgres"])
def db_engine(request, tmp_path):
    """The same test on SQLite and on Postgres."""
    if request.param == "postgres":
        return request.getfixturevalue("postgres_engine")
    # A file, not ":memory:": the routes and the worker run on other threads.
    return create_engine(f"sqlite:///{tmp_path}/test.db")


class FakeOcr:
    """A fake llm-pdf-ocr-api on a real socket. Set `body`/`status`/`delay` per test."""

    def __init__(self):
        self.body: object = []
        self.status = 200
        self.delay = 0.0
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                fake.requests.append(
                    {
                        "path": self.path,
                        "content_type": self.headers.get("Content-Type", ""),
                        "body": self.rfile.read(length),
                    }
                )
                time.sleep(fake.delay)
                payload = fake.body if isinstance(fake.body, bytes) else json.dumps(fake.body)
                payload = payload.encode() if isinstance(payload, str) else payload
                try:
                    self.send_response(fake.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass  # The client gave up (timeout test).

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ocr():
    fake = FakeOcr()
    yield fake
    fake.close()

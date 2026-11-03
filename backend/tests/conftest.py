import os
import uuid
from collections.abc import Mapping

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

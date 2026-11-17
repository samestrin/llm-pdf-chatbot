"""Session-scoped data in a public demo.

Each visitor's browser holds a signed, HttpOnly session cookie, and a collection belongs to
the session that made it: another session gets 404 on every route, as for an id that does
not exist. One shared owner, outside every visitor session, holds the read-only sample. A
collection is questioned only with the preset and embedding model it was embedded with.
"""

import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, insert, select, text, update
from test_credentials import FAKE_CODE, FAKE_KEY
from test_ingest import make_pdf
from test_llm import FakeServer
from test_retrieval import add_document as add_ready_document
from test_retrieval import embed_text

from app import credentials, session
from app.db import (
    chunk_embeddings,
    chunk_text_search,
    chunks,
    collections,
    documents,
    ingestion_status,
    init_db,
    pages,
)
from app.main import create_app
from app.retrieval import retrieve

KEY_HEADERS = {"X-Model-Preset": "openai", "X-Model-Key": FAKE_KEY}
CODE_HEADERS = {"X-Demo-Code": FAKE_CODE}
PDF = make_pdf(["SECTION 1\nThe glacier retreated forty metres."])
MISMATCH = (
    "This collection was embedded with text-embedding-3-small; "
    "switch back to OpenAI to ask about it"
)


def embeddings(payload: dict) -> tuple[int, bytes, str]:
    data = [{"index": i, "embedding": embed_text(t)} for i, t in enumerate(payload["input"])]
    return 200, json.dumps({"data": data}).encode(), "application/json"


@pytest.fixture
def provider(monkeypatch):
    """A fake model endpoint standing in for every preset's base URL and the demo gateway."""
    s = FakeServer()
    s.responder = embeddings
    monkeypatch.setattr(credentials, "_base_url", lambda preset: s.url)
    monkeypatch.setenv("DEMO_GATEWAY_URL", s.url)
    yield s
    s.close()


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("OCR_URL", raising=False)
    monkeypatch.delenv("RERANK_URL", raising=False)
    for name in ("DEMO_UPLOAD_TTL_MINUTES", "PUBLIC_DEMO", "SESSION_SECRET"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "uploads"


@pytest.fixture
def demo(monkeypatch, provider, uploads):
    monkeypatch.setenv("PUBLIC_DEMO", "true")


def upload(client, collection="papers", headers=KEY_HEADERS):
    return client.post(
        f"/collections/{collection}/documents",
        files={"file": ("doc.pdf", PDF, "application/pdf")},
        headers=headers,
    )


def uploaded(client, collection="papers", headers=KEY_HEADERS) -> dict:
    resp = upload(client, collection, headers)
    assert resp.status_code == 201, resp.text
    client.app.state.worker.join()
    return resp.json()


def chat(client, collection="papers", headers=KEY_HEADERS):
    return client.post(
        f"/collections/{collection}/chat",
        json={"question": "How far did the glacier retreat?"},
        headers=headers,
    )


def names(client) -> list[str]:
    return [c["name"] for c in client.get("/collections").json()]


def count(engine, table) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


# --- the cookie ---


def test_a_visitor_gets_a_signed_httponly_session_cookie_once(db_engine, demo):
    with TestClient(create_app(db_engine)) as client:
        first = client.get("/collections")
        second = client.get("/collections")

    cookie = first.headers["set-cookie"]
    name, value = cookie.split(";")[0].split("=", 1)
    assert name == session.COOKIE
    attributes = {a.strip().lower() for a in cookie.split(";")[1:]}
    assert {"httponly", "path=/", "samesite=lax"} <= attributes
    sid, signature = value.split(".")
    assert session.SESSION_ID.fullmatch(sid) and signature
    assert "set-cookie" not in second.headers


def test_the_cookie_is_secure_behind_an_https_proxy(db_engine, demo):
    with TestClient(create_app(db_engine)) as client:
        resp = client.get("/collections", headers={"X-Forwarded-Proto": "https"})
    assert "secure" in {a.strip().lower() for a in resp.headers["set-cookie"].split(";")}


@pytest.mark.parametrize(
    "forge",
    [
        lambda sid, sig: sid,
        lambda sid, sig: f"{sid}.{'0' * len(sig)}",
        lambda sid, sig: f"{session.SHARED}.{sig}",
        lambda sid, sig: f"{sid}.{sig}.{sig}",
    ],
    ids=["unsigned", "a wrong signature", "the shared owner", "extra parts"],
)
def test_a_forged_or_unsigned_cookie_is_a_new_session(db_engine, demo, forge):
    app = create_app(db_engine)
    with TestClient(app) as a:
        uploaded(a)
        sid, sig = a.cookies[session.COOKIE].split(".")
        intruder = TestClient(app)
        intruder.cookies.set(session.COOKIE, forge(sid, sig))
        resp = intruder.get("/collections")
    assert resp.json() == []
    assert resp.cookies[session.COOKIE] != a.cookies[session.COOKIE]


def test_a_cookie_signed_with_another_secret_is_a_new_session(db_engine, demo, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "one secret")
    with TestClient(create_app(db_engine)) as a:
        uploaded(a)
        cookie = a.cookies[session.COOKIE]
    monkeypatch.setenv("SESSION_SECRET", "another secret")
    other = TestClient(create_app(db_engine))
    other.cookies.set(session.COOKIE, cookie)
    assert other.get("/collections").json() == []


def test_the_same_secret_keeps_the_session_across_a_restart(db_engine, demo, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "kept secret")
    with TestClient(create_app(db_engine)) as a:
        uploaded(a)
        cookie = a.cookies[session.COOKIE]
    again = TestClient(create_app(db_engine))
    again.cookies.set(session.COOKIE, cookie)
    assert names(again) == ["papers"]


def test_outside_a_demo_there_is_no_cookie_and_one_shared_owner(db_engine, provider, uploads):
    app = create_app(db_engine)
    with TestClient(app) as a:
        uploaded(a)
        resp = a.get("/collections")
        b = TestClient(app)
        assert names(b) == ["papers"]
    assert "set-cookie" not in resp.headers
    with db_engine.connect() as conn:
        assert conn.execute(select(collections.c.session_id)).scalars().all() == [session.SHARED]


# --- another session's collection is not found ---


def test_another_session_gets_404_on_every_route(db_engine, demo):
    app = create_app(db_engine)
    with TestClient(app) as a:
        document = uploaded(a)
        doc = document["id"]
        b = TestClient(app)
        b.get("/presets")  # B has a session of its own

        assert b.get("/collections").json() == []
        refused = {
            "status": b.get(f"/documents/{doc}/status"),
            "page": b.get(f"/documents/{doc}/pages/1"),
            "pdf": b.get(f"/documents/{doc}/pdf"),
            "ask": chat(b),
            "delete document": b.delete(f"/documents/{doc}"),
            "delete collection": b.delete("/collections/papers"),
        }
        unknown = {
            "status": b.get("/documents/nope/status"),
            "page": b.get("/documents/nope/pages/1"),
            "pdf": b.get("/documents/nope/pdf"),
            "ask": chat(b, "nowhere"),
            "delete document": b.delete("/documents/nope"),
            "delete collection": b.delete("/collections/nowhere"),
        }
        for route, resp in refused.items():
            assert resp.status_code == 404, (route, resp.text)
            # The same answer as for a resource that does not exist: nothing leaks.
            assert resp.json() == unknown[route].json(), route

        # A still has all of it.
        assert names(a) == ["papers"]
        assert a.get(f"/documents/{doc}/status").json()["state"] == "ready"
        assert a.get(f"/documents/{doc}/pages/1").status_code == 200
        assert a.get(f"/documents/{doc}/pdf").content == PDF
        assert a.delete(f"/documents/{doc}").status_code == 204
    assert count(db_engine, collections) == 0


def test_another_session_cannot_upload_into_a_collection_it_does_not_own(db_engine, demo):
    app = create_app(db_engine)
    with TestClient(app) as a:
        uploaded(a)
        b = TestClient(app)
        uploaded(b)
        (mine,) = a.get("/collections").json()
        (theirs,) = b.get("/collections").json()
    assert mine["id"] != theirs["id"]
    assert len(mine["documents"]) == 1 and len(theirs["documents"]) == 1


def test_two_sessions_can_use_one_collection_name(db_engine, demo):
    app = create_app(db_engine)
    with TestClient(app) as a:
        doc_a = uploaded(a)["id"]
        b = TestClient(app)
        doc_b = uploaded(b)["id"]

        ([listed_a], [listed_b]) = (a.get("/collections").json(), b.get("/collections").json())
        assert listed_a["name"] == listed_b["name"] == "papers"
        assert [d["id"] for d in listed_a["documents"]] == [doc_a]
        assert [d["id"] for d in listed_b["documents"]] == [doc_b]

        # Deleting A's last document removes A's collection, never B's.
        assert a.delete(f"/documents/{doc_a}").status_code == 204
        assert names(a) == []
        assert names(b) == ["papers"]
        assert b.get(f"/documents/{doc_b}/status").json()["state"] == "ready"


def test_retrieval_by_collection_id_reads_only_that_collection(postgres_engine):
    init_db(postgres_engine)
    add_ready_document(postgres_engine, "papers", ["Zebras migrate across the plain."])
    with postgres_engine.begin() as conn:
        other = conn.execute(
            insert(collections)
            .values(name="papers", session_id="b" * 32)
            .returning(collections.c.id)
        ).scalar_one()
    with postgres_engine.begin() as conn:
        shared = conn.execute(
            select(collections.c.id).where(collections.c.session_id == session.SHARED)
        ).scalar_one()

    class Embedder:
        def embed(self, texts):
            return [embed_text(t) for t in texts]

    assert retrieve(postgres_engine, Embedder(), other, "zebras").passages == []
    assert retrieve(postgres_engine, Embedder(), shared, "zebras").passages
    # By name, only the shared owner's collection is read.
    assert retrieve(postgres_engine, Embedder(), "papers", "zebras").passages


# --- the TTL ---


def backdate(engine, minutes: int, session_id: str | None = None) -> None:
    query = update(collections).values(created_at=datetime.now(UTC) - timedelta(minutes=minutes))
    if session_id is not None:
        query = query.where(collections.c.session_id == session_id)
    with engine.begin() as conn:
        conn.execute(query)


def test_the_ttl_job_removes_expired_collections_their_rows_and_files(db_engine, demo, uploads):
    app = create_app(db_engine)
    with TestClient(app) as a:
        old = uploaded(a)["id"]
        sid_a = a.cookies[session.COOKIE].split(".")[0]
        b = TestClient(app)
        fresh = uploaded(b)["id"]
    backdate(db_engine, 61, sid_a)
    assert count(db_engine, chunk_embeddings) == 2

    removed = session.expire_uploads(db_engine, uploads, timedelta(minutes=60))

    assert removed == 1
    with db_engine.connect() as conn:
        assert conn.execute(select(documents.c.id)).scalars().all() == [fresh]
        for table in (pages, chunks, ingestion_status, chunk_embeddings, chunk_text_search):
            left = conn.execute(select(table.c.document_id).distinct()).scalars().all()
            assert left == [fresh], table.name
    assert not (uploads / f"{old}.pdf").exists()
    assert (uploads / f"{fresh}.pdf").exists()


def test_the_ttl_job_keeps_the_sample_however_old(db_engine, provider, uploads):
    with TestClient(create_app(db_engine)) as owner:
        sample = uploaded(owner)["id"]
    backdate(db_engine, 10_000)
    assert session.expire_uploads(db_engine, uploads, timedelta(minutes=60)) == 0
    assert (uploads / f"{sample}.pdf").exists()
    assert count(db_engine, documents) == 1


def test_the_ttl_job_runs_at_startup_in_a_demo(db_engine, demo, uploads, monkeypatch):
    monkeypatch.setenv("DEMO_UPLOAD_TTL_MINUTES", "5")
    app = create_app(db_engine)
    with TestClient(app) as a:
        doc = uploaded(a)["id"]
    backdate(db_engine, 6)
    with TestClient(create_app(db_engine)):
        pass
    assert count(db_engine, collections) == 0
    assert not (uploads / f"{doc}.pdf").exists()


def test_outside_a_demo_nothing_expires(db_engine, provider, uploads, monkeypatch):
    monkeypatch.setenv("DEMO_UPLOAD_TTL_MINUTES", "1")
    with TestClient(create_app(db_engine)) as client:
        uploaded(client)
    backdate(db_engine, 600)
    with TestClient(create_app(db_engine)):
        pass
    assert count(db_engine, collections) == 1


def wait_for(condition, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


def test_the_sweeper_repeats_until_stopped(db_engine, uploads):
    sweeper = session.UploadSweeper(db_engine, uploads, timedelta(minutes=60), interval=0.01)
    calls = []
    sweeper.sweep = lambda: calls.append(1)
    sweeper.start()
    wait_for(lambda: len(calls) >= 3)
    sweeper.stop()
    stopped = len(calls)
    time.sleep(0.05)
    assert len(calls) == stopped


def test_a_failed_sweep_is_logged_and_the_sweeper_carries_on(db_engine, uploads, caplog):
    sweeper = session.UploadSweeper(db_engine, uploads, timedelta(minutes=60), interval=0.01)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database away")

    sweeper.sweep = flaky
    sweeper.start()
    wait_for(lambda: len(calls) >= 2)
    sweeper.stop()
    assert "database away" in caplog.text


@pytest.mark.parametrize(
    ("value", "minutes"), [(None, 60), ("", 60), ("5", 5), (" 90 ", 90)], ids=str
)
def test_the_ttl_setting(value, minutes):
    env = {} if value is None else {"DEMO_UPLOAD_TTL_MINUTES": value}
    assert session.upload_ttl(env) == timedelta(minutes=minutes)


@pytest.mark.parametrize("value", ["0", "-5", "an hour", "1.5"])
def test_a_bad_ttl_setting_is_rejected(value):
    with pytest.raises(ValueError, match="DEMO_UPLOAD_TTL_MINUTES"):
        session.upload_ttl({"DEMO_UPLOAD_TTL_MINUTES": value})


# --- the read-only sample ---


@pytest.fixture
def sample(db_engine, provider, uploads, monkeypatch) -> dict:
    """The sample, uploaded by the operator with `PUBLIC_DEMO` off, then the demo on."""
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    with TestClient(create_app(db_engine)) as owner:
        document = uploaded(owner, "sample")
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    return document


def test_every_session_sees_and_reads_the_sample(db_engine, sample, uploads):
    app = create_app(db_engine)
    with TestClient(app) as a:
        uploaded(a)
        b = TestClient(app)
        for visitor, mine in ((a, ["papers"]), (b, [])):
            listed = visitor.get("/collections").json()
            assert sorted(c["name"] for c in listed) == sorted(["sample", *mine])
            (shown,) = [c for c in listed if c["name"] == "sample"]
            assert shown["read_only"] is True
            assert shown["embedding_preset"] == "openai"
            assert shown["embedding_model"] == "text-embedding-3-small"
            assert all(c["read_only"] is False for c in listed if c["name"] != "sample")
            doc = sample["id"]
            assert visitor.get(f"/documents/{doc}/status").json()["state"] == "ready"
            assert visitor.get(f"/documents/{doc}/pages/1").status_code == 200
            assert visitor.get(f"/documents/{doc}/pdf").content == PDF
            assert chat(visitor, "sample").status_code == 200


def test_the_sample_cannot_be_changed_or_deleted(db_engine, sample, uploads):
    with TestClient(create_app(db_engine)) as visitor:
        refused = [
            upload(visitor, "sample"),
            visitor.delete(f"/documents/{sample['id']}"),
            visitor.delete("/collections/sample"),
        ]
    for resp in refused:
        assert resp.status_code == 403, resp.text
        assert resp.json() == {"detail": session.READ_ONLY}
    assert count(db_engine, documents) == 1
    assert count(db_engine, collections) == 1
    assert (uploads / f"{sample['id']}.pdf").exists()
    assert [p.name for p in uploads.glob("*.pdf")] == [f"{sample['id']}.pdf"]


# --- a collection is questioned with its own preset and embedding model ---


def test_a_collection_records_its_preset_and_embedding_model(db_engine, demo):
    with TestClient(create_app(db_engine)) as client:
        uploaded(client, "keyed", KEY_HEADERS)
        uploaded(client, "coded", CODE_HEADERS)
        listed = {c["name"]: c for c in client.get("/collections").json()}
    assert (listed["keyed"]["embedding_preset"], listed["keyed"]["embedding_model"]) == (
        "openai",
        "text-embedding-3-small",
    )
    assert listed["coded"]["embedding_preset"] == "demo"


def test_another_preset_is_refused_with_the_plain_message(db_engine, demo, provider):
    with TestClient(create_app(db_engine)) as client:
        uploaded(client)
        before = len(provider.requests)
        resp = chat(client, headers=CODE_HEADERS)
    assert resp.status_code == 409
    assert resp.json() == {"detail": MISMATCH}
    assert len(provider.requests) == before  # refused before any model call


def test_another_embedding_model_is_refused_with_the_plain_message(db_engine, demo, provider):
    app = create_app(db_engine)
    with TestClient(app) as client:
        uploaded(client)
        openai = app.state.presets["openai"]
        app.state.presets = {
            **app.state.presets,
            "openai": replace(openai, embedding_model="text-embedding-3-large"),
        }
        resp = chat(client)
    assert resp.status_code == 409
    assert resp.json() == {"detail": MISMATCH}


def test_the_same_preset_and_model_may_ask(db_engine, demo):
    with TestClient(create_app(db_engine)) as client:
        uploaded(client)
        resp = chat(client)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")


def test_an_upload_with_another_preset_is_refused_and_stores_nothing(db_engine, demo, uploads):
    with TestClient(create_app(db_engine)) as client:
        first = uploaded(client)
        resp = upload(client, headers=CODE_HEADERS)
    assert resp.status_code == 409
    assert resp.json() == {
        "detail": "This collection was embedded with text-embedding-3-small; "
        "switch back to OpenAI to add to it"
    }
    assert count(db_engine, documents) == 1
    assert [p.name for p in uploads.iterdir()] == [f"{first['id']}.pdf"]


def test_without_a_recorded_preset_a_collection_is_not_checked(db_engine, provider, uploads):
    """A collection embedded with the server's own model (no preset) is asked as before."""
    with TestClient(create_app(db_engine)) as client:
        with db_engine.begin() as conn:
            conn.execute(insert(collections).values(name="papers"))
        resp = chat(client)
    assert resp.status_code == 200


# --- the schema ---


def test_a_database_from_before_sessions_is_refused_with_how_to_fix_it(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/old.db")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE collections (id INTEGER PRIMARY KEY, name TEXT UNIQUE)"))
    with pytest.raises(RuntimeError, match="fresh, empty database"):
        init_db(engine)

"""The request's credentials on the chat and upload routes, and a public demo's restart.

An upload is embedded with the visitor's preset and key, carried to the ingest worker in
memory only; a question is answered with them, deep mode on the preset's one chat model and
with no rerank in a public demo. A restart in a public demo fails the documents it cannot
finish, since their keys are gone.
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from test_answer import post_chat
from test_credentials import FAKE_CODE, FAKE_KEY, LLM_ENV, assert_no_secret, dump_rows
from test_deep import REPORT_2023, REPORT_2024, deep_model, two_reports
from test_ingest import FakeEmbedder, add_document, make_pdf, status, upload
from test_llm import FakeServer
from test_retrieval import embed_text

from app import answer, credentials, ingest
from app.db import collections, init_db
from app.main import create_app

OPENAI = credentials.load_presets()["openai"]
KEY_HEADERS = {"X-Model-Preset": "openai", "X-Model-Key": FAKE_KEY}
CODE_HEADERS = {"X-Demo-Code": FAKE_CODE}


@pytest.fixture
def provider(monkeypatch):
    """A fake model endpoint standing in for every preset's base URL and the demo gateway."""
    s = FakeServer()
    monkeypatch.setattr(credentials, "_base_url", lambda preset: s.url)
    monkeypatch.setenv("DEMO_GATEWAY_URL", s.url)
    yield s
    s.close()


@pytest.fixture
def env_llm(monkeypatch):
    """The owner's endpoint from `LLM_*`: a visitor with credentials never reaches it."""
    s = FakeServer()
    for name, value in {**LLM_ENV, "LLM_BASE_URL": s.url}.items():
        monkeypatch.setenv(name, value)
    yield s
    s.close()


@pytest.fixture
def rerank(monkeypatch):
    s = FakeServer()
    monkeypatch.setenv("RERANK_URL", s.url + "/rerank")
    yield s
    s.close()


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("OCR_URL", raising=False)
    monkeypatch.setenv("EMBED_BATCH_SIZE", "8")
    return tmp_path / "uploads"


def embeddings(payload: dict) -> tuple[int, bytes, str]:
    data = [{"index": i, "embedding": embed_text(t)} for i, t in enumerate(payload["input"])]
    return 200, json.dumps({"data": data}).encode(), "application/json"


def upload_with(client, headers: dict[str, str]) -> dict:
    resp = client.post(
        "/collections/papers/documents",
        files={"file": ("doc.pdf", make_pdf(["SECTION 1\nThe glacier retreated."]))},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- upload: the visitor's preset and key embed the document ---


@pytest.mark.parametrize("demo", ["true", ""], ids=["public demo", "not a demo"])
@pytest.mark.parametrize(
    ("headers", "secret", "model"),
    [
        (KEY_HEADERS, FAKE_KEY, "text-embedding-3-small"),
        (CODE_HEADERS, FAKE_CODE, "demo-embedding"),
    ],
    ids=["key", "demo code"],
)
def test_an_upload_embeds_with_the_visitors_preset_and_key(
    db_engine, provider, env_llm, uploads, monkeypatch, caplog, demo, headers, secret, model
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("PUBLIC_DEMO", demo)
    provider.responder = embeddings
    app = create_app(db_engine)
    with TestClient(app) as client:
        document = upload_with(client, headers)
        app.state.worker.join()
        state = client.get(f"/documents/{document['id']}/status")

    assert state.json()["state"] == "ready", state.text
    assert provider.requests, "the preset's endpoint was not called"
    for request in provider.requests:
        assert request["path"] == "/v1/embeddings"
        assert request["auth"] == f"Bearer {secret}"
        assert request["json"]["model"] == model
    assert env_llm.requests == []
    assert_no_secret(json.dumps(document), state.text, dump_rows(db_engine), caplog.text)
    for path in uploads.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


def test_an_upload_with_credentials_uses_them_over_an_injected_embedder(
    db_engine, provider, uploads, monkeypatch
):
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    provider.responder = embeddings
    injected = FakeEmbedder(db_engine)
    app = create_app(db_engine, embedder=injected)
    with TestClient(app) as client:
        document = upload_with(client, KEY_HEADERS)
        app.state.worker.join()
    assert status(db_engine, document["id"])["state"] == "ready"
    assert injected.calls == []
    assert [r["auth"] for r in provider.requests] == [f"Bearer {FAKE_KEY}"]


def test_an_upload_without_credentials_outside_a_demo_is_unchanged(
    db_engine, provider, uploads, monkeypatch
):
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    injected = FakeEmbedder(db_engine)
    app = create_app(db_engine, embedder=injected)
    with TestClient(app) as client:
        document = upload(client)
        app.state.worker.join()
    assert status(db_engine, document["id"])["state"] == "ready"
    assert injected.calls
    assert provider.requests == []


# --- restart in a public demo ---


def test_a_restart_in_demo_mode_fails_queued_and_processing_documents_without_a_model_call(
    db_engine, provider, env_llm, uploads, monkeypatch
):
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    init_db(db_engine)
    add_document(db_engine, "waiting", "queued")
    add_document(db_engine, "cut-off", "processing")
    add_document(db_engine, "finished", "ready")
    add_document(db_engine, "broken", "failed")
    uploads.mkdir()
    (uploads / "waiting.pdf").write_bytes(make_pdf(["SECTION 1\nStill here."]))
    injected = FakeEmbedder(db_engine)

    app = create_app(db_engine, embedder=injected)
    with TestClient(app):
        app.state.worker.join()

    for document_id in ("waiting", "cut-off"):
        row = status(db_engine, document_id)
        assert row["state"] == "failed"
        assert row["error"] == ingest.DEMO_INTERRUPTED
    assert "interrupted" in ingest.DEMO_INTERRUPTED and "upload" in ingest.DEMO_INTERRUPTED
    assert status(db_engine, "finished")["state"] == "ready"
    assert status(db_engine, "broken")["state"] == "failed"
    assert injected.calls == []
    assert provider.requests == [] and env_llm.requests == []


def test_a_restart_outside_a_demo_still_requeues_queued_documents(db_engine, uploads, monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "false")
    init_db(db_engine)
    add_document(db_engine, "waiting", "queued")
    uploads.mkdir()
    (uploads / "waiting.pdf").write_bytes(make_pdf(["SECTION 1\nStill here."]))
    injected = FakeEmbedder(db_engine)

    app = create_app(db_engine, embedder=injected)
    with TestClient(app):
        app.state.worker.join()

    assert status(db_engine, "waiting")["state"] == "ready"
    assert injected.calls


# --- chat: the visitor's preset answers, deep mode on its chat model, no rerank ---


class Spy:
    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1


def test_in_demo_mode_the_chat_route_never_reads_the_rerank_settings(
    tmp_path, provider, monkeypatch
):
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    spy = Spy()
    monkeypatch.setattr(answer.Reranker, "from_env", spy)
    with TestClient(create_app(create_engine(f"sqlite:///{tmp_path}/t.db"))) as client:
        for mode in ("fast", "deep"):
            client.post(
                "/collections/papers/chat",
                json={"question": "How far?", "mode": mode},
                headers=KEY_HEADERS,
            )
    assert spy.calls == 0


def test_outside_a_demo_the_chat_route_still_reads_the_rerank_settings(
    tmp_path, env_llm, monkeypatch
):
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    spy = Spy()
    monkeypatch.setattr(answer.Reranker, "from_env", spy)
    with TestClient(create_app(create_engine(f"sqlite:///{tmp_path}/t.db"))) as client:
        client.post("/collections/papers/chat", json={"question": "How far?"})
    assert spy.calls == 1


@pytest.fixture
def engine(postgres_engine):
    init_db(postgres_engine)
    return postgres_engine


@pytest.fixture
def clean_settings(monkeypatch):
    for name in (
        "DEEP_MAX_SUBQUESTIONS",
        "DEEP_MAX_TOKENS",
        "DEEP_TIMEOUT_SECONDS",
        "REFUSAL_MIN_SIMILARITY",
        "REFUSAL_MIN_KEYWORD_COVERAGE",
        "CHAT_HISTORY_TURNS",
    ):
        monkeypatch.delenv(name, raising=False)


def embedded_with(engine, preset_id: str) -> None:
    """Mark the collection as embedded with the preset it is asked with: a collection is
    questioned only with its own preset and embedding model."""
    preset = credentials.load_presets()[preset_id]
    with engine.begin() as conn:
        conn.execute(
            update(collections).values(
                embedding_preset=preset.id, embedding_model=preset.embedding_model
            )
        )


def preset_model(payload: dict) -> tuple[int, bytes, str]:
    """Embeddings for an embedding call, the deep model's plan or answer for a chat call."""
    if "input" in payload:
        return embeddings(payload)
    return deep_model()(payload)


@pytest.mark.parametrize("headers", [KEY_HEADERS, CODE_HEADERS], ids=["key", "demo code"])
def test_deep_mode_in_demo_mode_calls_the_preset_chat_model_and_never_the_rerank_url(
    engine, provider, env_llm, rerank, clean_settings, monkeypatch, caplog, headers
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    two_reports(engine)
    embedded_with(engine, headers.get("X-Model-Preset", "demo"))
    provider.responder = preset_model
    secret = headers.get("X-Model-Key") or headers["X-Demo-Code"]
    preset = credentials.load_presets()[headers.get("X-Model-Preset", "demo")]

    with TestClient(create_app(engine)) as client:
        resp = client.post(
            "/collections/papers/chat",
            json={
                "question": "How does the glacier's retreat in 2023 compare with 2024?",
                "mode": "deep",
            },
            headers=headers,
        )
    events = [json.loads(line) for line in resp.text.splitlines() if line.strip()]

    types = [e["type"] for e in events]
    assert types[-1] == "done", events
    assert "claim" in types
    quotes = {e["quote"] for e in events if e["type"] == "claim"}
    assert quotes == {REPORT_2023, REPORT_2024}
    chats = [r for r in provider.requests if r["path"] == "/v1/chat/completions"]
    assert len(chats) == 2  # the plan and the answer
    assert {r["json"]["model"] for r in chats} == {preset.chat_model}
    assert {r["auth"] for r in provider.requests} == {f"Bearer {secret}"}
    assert rerank.requests == []
    assert env_llm.requests == []
    assert_no_secret(resp.text, caplog.text)


def test_fast_mode_with_a_key_answers_with_the_preset_outside_a_demo_too(
    engine, provider, env_llm, clean_settings, monkeypatch
):
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    monkeypatch.delenv("RERANK_URL", raising=False)
    two_reports(engine)
    embedded_with(engine, "openai")
    provider.responder = preset_model

    with TestClient(create_app(engine)) as client:
        resp = client.post(
            "/collections/papers/chat",
            json={"question": "How far did the glacier retreat in 2023?"},
            headers=KEY_HEADERS,
        )
    events = [json.loads(line) for line in resp.text.splitlines() if line.strip()]

    assert events[-1]["type"] == "done", events
    chats = [r for r in provider.requests if r["path"] == "/v1/chat/completions"]
    assert [r["json"]["model"] for r in chats] == [OPENAI.chat_model]
    assert env_llm.requests == []


def test_chat_without_credentials_outside_a_demo_uses_the_env_endpoint(
    engine, env_llm, clean_settings, monkeypatch
):
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    monkeypatch.delenv("RERANK_URL", raising=False)
    with TestClient(create_app(engine)) as client:
        post_chat(client, {"question": "How far did the glacier retreat?"})
    assert env_llm.requests
    assert {r["auth"] for r in env_llm.requests} == {f"Bearer {LLM_ENV['LLM_API_KEY']}"}

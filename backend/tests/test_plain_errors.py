"""Plain errors for a key or demo code failure, in the chat stream and the status JSON.

A rejected key, a used-up or expired demo code, a provider rate limit and a preset with
no embedding model each say the shared plain sentence, with no upstream body in it: the
body can hold the key or account details, so it stays in the server's log alone.
"""

import json

import pytest
from fastapi.testclient import TestClient
from test_credentials import FAKE_CODE, FAKE_KEY
from test_deep import QUESTION, two_reports
from test_demo_wiring import CODE_HEADERS, KEY_HEADERS, embedded_with, embeddings, upload_with
from test_llm import FakeServer
from test_retrieval import GLACIER, add_document

from app import credentials
from app.db import init_db
from app.main import create_app

SYNTHETIC_HEADERS = {"X-Model-Preset": "synthetic", "X-Model-Key": FAKE_KEY}
# Account details a careless upstream puts in its error body; it must never reach the client.
UPSTREAM_MARKER = "acct-98765-PRIVATE-ACCOUNT-DETAIL"


@pytest.fixture
def engine(postgres_engine):
    """Retrieval needs Postgres (tsvector, pgvector), as in the retrieval tests."""
    init_db(postgres_engine)
    return postgres_engine


@pytest.fixture
def provider(monkeypatch):
    """A fake model endpoint standing in for every preset's base URL and the demo gateway."""
    s = FakeServer()
    monkeypatch.setattr(credentials, "_base_url", lambda preset: s.url)
    monkeypatch.setenv("DEMO_GATEWAY_URL", s.url)
    yield s
    s.close()


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("OCR_URL", raising=False)
    monkeypatch.setenv("EMBED_BATCH_SIZE", "8")
    return tmp_path / "uploads"


def upstream_error(status: int):
    """An endpoint reply that turns the call down and echoes account details in its body."""

    def respond(payload):
        body = json.dumps(
            {"error": {"message": f"Incorrect API key provided; account {UPSTREAM_MARKER}"}}
        )
        return status, body.encode(), "application/json"

    return respond


def chat_fails_with(status: int):
    """Embeddings answer (so retrieval works); the chat call is turned down."""

    def respond(payload):
        if "input" in payload:
            return embeddings(payload)
        return upstream_error(status)(payload)

    return respond


def chat(engine, headers, question="How far did the glacier retreat?", mode="fast"):
    with TestClient(create_app(engine)) as client:
        resp = client.post(
            "/collections/papers/chat",
            json={"question": question, "mode": mode},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    return resp, [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def failed_status(db_engine, provider, headers, uploads) -> tuple[dict, str]:
    """Upload with `headers`, let the worker fail, and read the status JSON back."""
    app = create_app(db_engine)
    with TestClient(app) as client:
        document = upload_with(client, headers)
        app.state.worker.join()
        resp = client.get(f"/documents/{document['id']}/status")
    return resp.json(), resp.text


# --- the chat stream's error event ---


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_says_the_key_was_rejected_by_the_provider(engine, provider, status):
    add_document(engine, "papers", [GLACIER])
    embedded_with(engine, "openai")
    provider.responder = chat_fails_with(status)
    _, events = chat(engine, KEY_HEADERS)
    assert events == [{"type": "error", "message": "The key was rejected by OpenAI"}]


@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_used_up_or_expired_demo_code_says_so(engine, provider, status):
    add_document(engine, "papers", [GLACIER])
    embedded_with(engine, "demo")
    provider.responder = chat_fails_with(status)
    _, events = chat(engine, CODE_HEADERS)
    assert events == [{"type": "error", "message": "This demo code is used up or expired"}]


def test_a_provider_rate_limit_says_to_try_again_shortly(engine, provider):
    add_document(engine, "papers", [GLACIER])
    embedded_with(engine, "openai")
    provider.responder = chat_fails_with(429)
    _, events = chat(engine, KEY_HEADERS)
    assert events == [
        {"type": "error", "message": "OpenAI is rate limiting this key. Try again shortly."}
    ]


def test_a_preset_with_no_embedding_model_says_so_in_the_chat_stream(engine, provider):
    add_document(engine, "papers", [GLACIER])
    embedded_with(engine, "synthetic")
    provider.responder = embeddings
    _, events = chat(engine, SYNTHETIC_HEADERS)
    assert events == [{"type": "error", "message": "this preset has no embedding model"}]
    assert provider.requests == []  # refused before any call


def test_a_deep_run_with_a_rejected_key_ends_in_the_plain_error(engine, provider):
    two_reports(engine)
    embedded_with(engine, "openai")
    provider.responder = chat_fails_with(401)
    _, events = chat(engine, KEY_HEADERS, question=QUESTION, mode="deep")
    assert events[-1] == {"type": "error", "message": "The key was rejected by OpenAI"}


# --- the ingest failure reason in the status JSON ---


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_fails_the_upload_with_the_plain_error(
    db_engine, provider, uploads, status
):
    provider.responder = upstream_error(status)
    status_body, _ = failed_status(db_engine, provider, KEY_HEADERS, uploads)
    assert status_body["state"] == "failed"
    assert status_body["error"] == "The key was rejected by OpenAI"


@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_used_up_or_expired_demo_code_fails_the_upload_with_the_plain_error(
    db_engine, provider, uploads, status
):
    provider.responder = upstream_error(status)
    status_body, _ = failed_status(db_engine, provider, CODE_HEADERS, uploads)
    assert status_body["state"] == "failed"
    assert status_body["error"] == "This demo code is used up or expired"


def test_a_provider_rate_limit_fails_the_upload_with_the_plain_error(
    db_engine, provider, uploads
):
    provider.responder = upstream_error(429)
    status_body, _ = failed_status(db_engine, provider, KEY_HEADERS, uploads)
    assert status_body["state"] == "failed"
    assert status_body["error"] == "OpenAI is rate limiting this key. Try again shortly."


def test_a_preset_with_no_embedding_model_fails_the_upload_with_the_plain_error(
    db_engine, provider, uploads
):
    status_body, _ = failed_status(db_engine, provider, SYNTHETIC_HEADERS, uploads)
    assert status_body["state"] == "failed"
    assert status_body["error"] == "this preset has no embedding model"
    assert provider.requests == []  # refused before any call


# --- no upstream body reaches the client ---


@pytest.mark.parametrize("headers", [KEY_HEADERS, CODE_HEADERS], ids=["key", "demo code"])
def test_no_upstream_body_reaches_the_chat_stream(engine, provider, headers):
    add_document(engine, "papers", [GLACIER])
    embedded_with(engine, headers.get("X-Model-Preset", "demo"))
    provider.responder = chat_fails_with(401)
    resp, events = chat(engine, headers)
    assert events[-1]["type"] == "error"
    assert UPSTREAM_MARKER not in resp.text
    assert FAKE_KEY not in resp.text and FAKE_CODE not in resp.text


@pytest.mark.parametrize("headers", [KEY_HEADERS, CODE_HEADERS], ids=["key", "demo code"])
def test_no_upstream_body_reaches_the_status_json(db_engine, provider, uploads, headers):
    provider.responder = upstream_error(401)
    status_body, text = failed_status(db_engine, provider, headers, uploads)
    assert status_body["state"] == "failed"
    assert UPSTREAM_MARKER not in text
    assert FAKE_KEY not in text and FAKE_CODE not in text

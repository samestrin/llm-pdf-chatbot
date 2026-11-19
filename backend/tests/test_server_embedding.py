"""In a public demo the server embeds every document and question itself.

It embeds with its own gateway key (`DEMO_EMBEDDING_KEY`) and the demo preset's embedding
model, so the read-only sample and every upload work with any preset, also one with no
embedding model (Synthetic), and with no demo code. The visitor's key or code goes to
the chat model alone. Each session may make `DEMO_SESSION_MAX_EMBED_CALLS` embedding
calls per `DEMO_UPLOAD_TTL_MINUTES`.
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from test_answer import claim, stream_json
from test_credentials import FAKE_KEY, LLM_ENV, assert_no_secret
from test_deep import REPORT_2023, chunk_id_of, two_reports
from test_demo_wiring import CODE_HEADERS, KEY_HEADERS, embeddings, upload_with
from test_ingest import make_pdf
from test_llm import FakeServer

from app import credentials, server_embedding
from app.db import collections, init_db
from app.main import create_app

SERVER_KEY = "sk-server-EMBEDDING-key-0011223344"
SYNTHETIC_HEADERS = {"X-Model-Preset": "synthetic", "X-Model-Key": FAKE_KEY}
SYNTHETIC = credentials.load_presets()["synthetic"]
QUESTION = "How far did the glacier retreat in 2023?"


@pytest.fixture
def gateway(monkeypatch):
    """The demo gateway: the server's own embedding calls, and a demo code's chat calls."""
    s = FakeServer()
    s.responder = model
    monkeypatch.setenv("DEMO_GATEWAY_URL", s.url)
    yield s
    s.close()


@pytest.fixture
def provider(monkeypatch, gateway):
    """Every key preset's base URL."""
    s = FakeServer()
    s.responder = model
    monkeypatch.setattr(
        credentials, "_base_url", lambda preset: gateway.url if preset.id == "demo" else s.url
    )
    yield s
    s.close()


@pytest.fixture
def env_llm(monkeypatch):
    """The owner's endpoint from `LLM_*`: never used in a public demo."""
    s = FakeServer()
    for name, value in {**LLM_ENV, "LLM_BASE_URL": s.url}.items():
        monkeypatch.setenv(name, value)
    yield s
    s.close()


@pytest.fixture
def demo(monkeypatch, provider, env_llm, tmp_path):
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    monkeypatch.setenv("DEMO_EMBEDDING_KEY", SERVER_KEY)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("EMBED_BATCH_SIZE", "8")
    for name in (
        "OCR_URL",
        "RERANK_URL",
        "DEMO_SESSION_MAX_EMBED_CALLS",
        "DEMO_UPLOAD_TTL_MINUTES",
        "REFUSAL_MIN_SIMILARITY",
        "REFUSAL_MIN_KEYWORD_COVERAGE",
        "CHAT_HISTORY_TURNS",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def sample(postgres_engine):
    """The read-only sample as the demo host has it: embedded with `demo-embedding`."""
    init_db(postgres_engine)
    two_reports(postgres_engine)
    with postgres_engine.begin() as conn:
        conn.execute(
            update(collections).values(
                embedding_preset="demo", embedding_model="demo-embedding", name="default"
            )
        )
    return postgres_engine


def answer_2023(request: dict) -> dict:
    return {
        "claims": [
            claim("It retreated forty metres.", chunk_id_of(request, REPORT_2023), REPORT_2023)
        ]
    }


def model(payload: dict) -> tuple[int, bytes, str]:
    """Embeddings for an embedding call, a streamed answer citing 2023 for a chat call."""
    if "input" in payload:
        return embeddings(payload)
    return 200, b"".join(stream_json(answer_2023(payload))), "text/event-stream"


def ask(client, collection="default", headers=SYNTHETIC_HEADERS, question=QUESTION) -> list:
    resp = client.post(
        f"/collections/{collection}/chat", json={"question": question}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def auths(server: FakeServer, path: str) -> set:
    return {r["auth"] for r in server.requests if r["path"] == path}


# --- the sample, asked with any preset ---


@pytest.mark.parametrize(
    "headers",
    [SYNTHETIC_HEADERS, KEY_HEADERS, CODE_HEADERS],
    ids=["synthetic (no embedding model)", "openai key", "demo code"],
)
def test_the_sample_answers_with_any_preset_and_the_server_embeds_the_question(
    sample, demo, gateway, provider, env_llm, caplog, headers
):
    caplog.set_level(logging.DEBUG)
    with TestClient(create_app(sample)) as client:
        events = ask(client, headers=headers)

    assert [e["type"] for e in events] == ["claim", "done"], events
    assert events[0]["quote"] == REPORT_2023
    # The question is embedded on the server, with its own key and the demo embedding model.
    embeds = [r for r in gateway.requests if r["path"] == "/v1/embeddings"]
    assert len(embeds) == 1
    assert embeds[0]["auth"] == f"Bearer {SERVER_KEY}"
    assert embeds[0]["json"]["model"] == "demo-embedding"
    assert auths(provider, "/v1/embeddings") == set()
    # The visitor's key or code writes the answer, on the preset's chat model.
    secret = headers.get("X-Model-Key") or headers["X-Demo-Code"]
    chat_server = gateway if "X-Demo-Code" in headers else provider
    chats = [r for r in chat_server.requests if r["path"] == "/v1/chat/completions"]
    assert [r["auth"] for r in chats] == [f"Bearer {secret}"]
    preset = credentials.load_presets()[headers.get("X-Model-Preset", "demo")]
    assert [r["json"]["model"] for r in chats] == [preset.chat_model]
    assert env_llm.requests == []
    assert_no_secret(caplog.text)
    assert SERVER_KEY not in caplog.text


def test_the_visitors_key_never_reaches_the_server_embedding(sample, demo, gateway):
    with TestClient(create_app(sample)) as client:
        ask(client, headers=SYNTHETIC_HEADERS)
    for request in gateway.requests:
        assert FAKE_KEY not in json.dumps(request)


def test_without_the_server_key_a_question_fails_plainly_and_no_key_embeds(
    sample, demo, gateway, provider, monkeypatch
):
    monkeypatch.delenv("DEMO_EMBEDDING_KEY")
    with TestClient(create_app(sample)) as client:
        events = ask(client, headers=KEY_HEADERS)
    assert events == [
        {"type": "error", "message": "the answer failed: DEMO_EMBEDDING_KEY is missing or invalid"}
    ]
    assert gateway.requests == []
    assert provider.requests == []  # never falls back to the visitor's own embedding model


def test_a_collection_of_another_embedding_model_is_refused_in_a_demo(sample, demo, gateway):
    with sample.begin() as conn:
        conn.execute(update(collections).values(embedding_model="text-embedding-3-small"))
    with TestClient(create_app(sample)) as client:
        resp = client.post(
            "/collections/default/chat", json={"question": QUESTION}, headers=KEY_HEADERS
        )
    assert resp.status_code == 409
    assert resp.json() == {
        "detail": "This collection was embedded with text-embedding-3-small; this server "
        "embeds with demo-embedding, so it cannot ask about it"
    }
    assert gateway.requests == []


# --- uploads, embedded on the server ---


@pytest.mark.parametrize(
    "headers",
    [SYNTHETIC_HEADERS, KEY_HEADERS, CODE_HEADERS, {}],
    ids=["synthetic (no embedding model)", "openai key", "demo code", "no credentials"],
)
def test_an_upload_is_embedded_on_the_server_with_any_preset(
    db_engine, demo, gateway, provider, env_llm, caplog, headers
):
    caplog.set_level(logging.DEBUG)
    app = create_app(db_engine)
    with TestClient(app) as client:
        document = upload_with(client, headers)
        app.state.worker.join()
        state = client.get(f"/documents/{document['id']}/status").json()
        (listed,) = client.get("/collections").json()

    assert state["state"] == "ready", state
    assert gateway.requests, "the server's embedding was not called"
    for request in gateway.requests:
        assert request["path"] == "/v1/embeddings"
        assert request["auth"] == f"Bearer {SERVER_KEY}"
        assert request["json"]["model"] == "demo-embedding"
    assert provider.requests == []
    assert env_llm.requests == []
    assert (listed["embedding_preset"], listed["embedding_model"]) == ("demo", "demo-embedding")
    assert_no_secret(caplog.text)


def test_a_collection_uploaded_with_one_preset_is_asked_with_another(
    postgres_engine, demo, gateway, provider
):
    init_db(postgres_engine)
    app = create_app(postgres_engine)
    with TestClient(app) as client:
        client.post(
            "/collections/papers/documents",
            files={"file": ("r.pdf", _pdf(REPORT_2023), "application/pdf")},
            headers=SYNTHETIC_HEADERS,
        )
        app.state.worker.join()
        second = client.post(
            "/collections/papers/documents",
            files={"file": ("r2.pdf", _pdf(REPORT_2023), "application/pdf")},
            headers=KEY_HEADERS,
        )
        app.state.worker.join()
        events = ask(client, "papers", headers=CODE_HEADERS)
    assert second.status_code == 201, second.text
    assert events[-1]["type"] == "done", events
    assert any(e["type"] == "claim" for e in events)


def _pdf(text: str) -> bytes:
    return make_pdf([text])


# --- the per-session cap on embedding calls ---


def test_a_session_past_its_embedding_cap_is_refused_plainly_and_others_are_not(
    sample, demo, gateway, monkeypatch
):
    monkeypatch.setenv("DEMO_SESSION_MAX_EMBED_CALLS", "2")
    app = create_app(sample)
    with TestClient(app) as a:
        first = [ask(a) for _ in range(2)]
        before = len(gateway.requests)
        refused = ask(a)
        b = TestClient(app)
        other = ask(b)

    assert all(events[-1]["type"] == "done" for events in first)
    assert refused == [
        {
            "type": "error",
            "message": "This session has reached its limit of 2 embedding calls in 60 "
            "minutes; try again later",
        }
    ]
    # Refused before any call: the gateway saw nothing from the third question.
    assert len(gateway.requests) == before
    assert other[-1]["type"] == "done"


def test_an_upload_past_the_embedding_cap_fails_with_the_plain_reason(
    db_engine, demo, gateway, monkeypatch
):
    monkeypatch.setenv("DEMO_SESSION_MAX_EMBED_CALLS", "1")
    monkeypatch.setenv("EMBED_BATCH_SIZE", "1")
    app = create_app(db_engine)
    with TestClient(app) as client:
        resp = client.post(
            "/collections/papers/documents",
            files={"file": ("two.pdf", _two_chunks(), "application/pdf")},
            headers=SYNTHETIC_HEADERS,
        )
        app.state.worker.join()
        state = client.get(f"/documents/{resp.json()['id']}/status").json()
    assert state["state"] == "failed"
    assert state["error"] == (
        "This session has reached its limit of 1 embedding calls in 60 minutes; try again later"
    )
    assert len(gateway.requests) == 1


def _two_chunks() -> bytes:
    return make_pdf(["SECTION 1\nThe glacier retreated.", "SECTION 2\nThe lake rose."])


def test_the_cap_window_restarts(monkeypatch):
    now = [1000.0]
    budget = server_embedding.EmbedBudget(limit=1, window_seconds=60, clock=lambda: now[0])
    budget.spend("a")
    with pytest.raises(server_embedding.EmbedCapError):
        budget.spend("a")
    budget.spend("b")
    now[0] += 60
    budget.spend("a")


def test_the_cap_forgets_sessions_whose_window_has_passed():
    now = [0.0]
    budget = server_embedding.EmbedBudget(limit=5, window_seconds=10, clock=lambda: now[0])
    for n in range(100):
        budget.spend(f"s{n}")
    now[0] += 10
    budget.spend("new")
    assert budget.tracked() == 1


@pytest.mark.parametrize(
    ("raw", "limit"), [("", server_embedding.DEFAULT_SESSION_MAX_EMBED_CALLS), (" 7 ", 7)]
)
def test_the_cap_comes_from_the_environment(raw, limit):
    budget = server_embedding.EmbedBudget.from_env({"DEMO_SESSION_MAX_EMBED_CALLS": raw})
    assert budget.limit == limit


@pytest.mark.parametrize("raw", ["0", "-1", "lots", "1.5"])
def test_a_bad_cap_setting_is_rejected(raw):
    with pytest.raises(ValueError, match="DEMO_SESSION_MAX_EMBED_CALLS"):
        server_embedding.EmbedBudget.from_env({"DEMO_SESSION_MAX_EMBED_CALLS": raw})


def test_presets_no_longer_mark_a_preset_unable_to_upload_in_a_demo(db_engine, demo):
    """Every preset can upload in a demo: the page is told the server's embedding model."""
    with TestClient(create_app(db_engine)) as client:
        body = client.get("/presets").json()
    assert body["embedding_model"] == "demo-embedding"
    synthetic = next(p for p in body["presets"] if p["id"] == "synthetic")
    assert synthetic["chat_model"] == SYNTHETIC.chat_model


def test_outside_a_demo_there_is_no_server_embedding_model(db_engine, monkeypatch):
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    with TestClient(create_app(db_engine)) as client:
        assert client.get("/presets").json()["embedding_model"] is None

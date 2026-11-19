"""Presets, the credentials a request carries, and the client built from them.

A planted fake key is sent through the request client on every path, success and failure,
and must not appear in a log line, an exception, an error body, a stored row or a line of
the chat stream.
"""

import json
import logging
import re
import socket
from contextlib import ExitStack
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, create_engine, insert, select
from test_answer import FILENAMES, GLACIER_CLAIM, PASSAGES, page_texts, resolver, stream_json
from test_ingest import TWENTY_PAGES, upload
from test_llm import USAGE, FakeServer, delta, sse

from app import credentials
from app.answer import answer_events
from app.credentials import (
    Credentials,
    client_for,
    load_presets,
    public_presets,
    request_credentials,
)
from app.db import collections
from app.llm import (
    LLMClient,
    LLMConfig,
    LLMConfigError,
    LLMCredentialsRequired,
    LLMError,
    NoEmbeddingModelError,
)
from app.main import create_app

FAKE_KEY = "sk-planted-FAKE-KEY-0123456789abcdef"
FAKE_CODE = "sk-demo-PLANTED-CODE-fedcba9876543210"
SECRETS = [FAKE_KEY, FAKE_CODE]
LLM_ENV = {
    "LLM_BASE_URL": "http://env-llm.example/v1",
    "LLM_API_KEY": "sk-env-owner-key",
    "LLM_FAST_MODEL": "small",
    "LLM_REASONING_MODEL": "big",
    "LLM_EMBEDDING_MODEL": "embed",
}


def assert_no_secret(*things: object) -> None:
    for thing in things:
        text = thing if isinstance(thing, str) else repr(thing)
        for secret in SECRETS:
            assert secret not in text, f"secret leaked into {text[:200]!r}"


@pytest.fixture
def gateway(monkeypatch):
    """A fake demo gateway; DEMO_GATEWAY_URL points at it."""
    s = FakeServer()
    monkeypatch.setenv("DEMO_GATEWAY_URL", s.url)
    yield s
    s.close()


@pytest.fixture
def no_gateway(monkeypatch):
    monkeypatch.delenv("DEMO_GATEWAY_URL", raising=False)


def probe_app() -> FastAPI:
    """An app with one route that reports the credentials the dependency built."""
    app = FastAPI()

    @app.get("/probe")
    def probe(request: Request, creds: Annotated[Credentials | None, Depends(request_credentials)]):
        if creds is None:
            return {"credentials": None}
        client = client_for(creds)
        try:
            return {
                "preset": creds.preset.id,
                "base_url": client.config.base_url,
                "query": dict(request.query_params),
            }
        finally:
            client.close()

    return app


def probe(headers: dict[str, str], params: dict[str, str] | None = None):
    return TestClient(probe_app()).get("/probe", headers=headers, params=params)


# --- presets.json, the allowlist ---


def test_the_allowlist_holds_the_four_providers_and_the_demo():
    presets = load_presets()
    assert list(presets) == ["openai", "openrouter", "synthetic", "alibaba", "demo"]
    for preset in presets.values():
        if preset.id == "demo":
            assert preset.base_url is None  # From DEMO_GATEWAY_URL, never from the file.
        else:
            assert preset.base_url.startswith("https://")
        assert preset.chat_model


def test_a_preset_with_no_embedding_model_has_none():
    assert load_presets()["synthetic"].embedding_model is None


GOOD = {
    "id": "x",
    "label": "X",
    "kind": "key",
    "base_url": "https://a/v1",
    "chat_model": "m",
    "embedding_model": None,
}
DEMO_GOOD = {**GOOD, "id": "demo", "kind": "demo", "base_url": None}


@pytest.mark.parametrize(
    "items, words",
    [
        ([], "holds no presets"),
        ([{k: v for k, v in GOOD.items() if k != "base_url"}], "exactly the fields"),
        ([{**GOOD, "base_url": "http://a/v1"}], "https:// URL"),
        ([{**DEMO_GOOD, "base_url": "https://a/v1"}], "DEMO_GATEWAY_URL"),
        ([GOOD, {**GOOD, "label": "Y"}], "appears twice"),
        ([{**GOOD, "id": ""}], "non-empty text"),
        ([{**GOOD, "label": ""}], "non-empty text"),
        ([{**GOOD, "chat_model": 7}], "non-empty text"),
        ([{**GOOD, "embedding_model": 3}], "embedding_model is text or null"),
        ([{**GOOD, "embedding_model": ""}], "embedding_model is text or null"),
        ([{**GOOD, "kind": "demo"}], "kind must be 'key'"),
        ([{**DEMO_GOOD, "kind": "key"}], "kind must be 'demo'"),
    ],
)
def test_a_malformed_preset_file_is_refused(tmp_path, items, words):
    path = tmp_path / "presets.json"
    path.write_text(json.dumps({"presets": items}))
    with pytest.raises(ValueError, match=re.escape(words)):
        load_presets(path)


def test_the_guard_cases_start_from_valid_presets(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text(json.dumps({"presets": [GOOD, DEMO_GOOD]}))
    assert list(load_presets(path)) == ["x", "demo"]


# --- request headers ---


def test_no_headers_is_no_credentials():
    assert probe({}).json() == {"credentials": None}


def test_a_preset_and_a_key_build_the_preset_client():
    resp = probe({"X-Model-Preset": "openrouter", "X-Model-Key": FAKE_KEY})
    assert resp.status_code == 200
    assert resp.json()["base_url"] == "https://openrouter.ai/api/v1"


@pytest.mark.parametrize("preset", ["bogus", "OPENAI", "http://169.254.169.254/v1", "demo2"])
def test_an_unknown_preset_is_400(preset):
    resp = probe({"X-Model-Preset": preset, "X-Model-Key": FAKE_KEY})
    assert resp.status_code == 400
    assert "unknown model preset" in resp.json()["detail"]
    assert preset not in resp.text


def test_a_key_pasted_into_the_preset_header_is_not_echoed():
    resp = probe({"X-Model-Preset": FAKE_KEY, "X-Model-Key": FAKE_KEY})
    assert resp.status_code == 400
    assert_no_secret(resp.text)


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Model-Base-Url": "http://127.0.0.1:1/v1"},
        {"X-Base-Url": "http://127.0.0.1:1/v1"},
        {"X-LLM-Base-URL": "http://127.0.0.1:1/v1"},
        {"Base-Url": "http://127.0.0.1:1/v1"},
    ],
)
def test_no_base_url_is_accepted_from_the_request(headers):
    resp = probe(
        {"X-Model-Preset": "openai", "X-Model-Key": FAKE_KEY, **headers},
        params={"base_url": "http://127.0.0.1:1/v1", "llm_base_url": "http://127.0.0.1:1/v1"},
    )
    assert resp.status_code == 200
    assert resp.json()["base_url"] == "https://api.openai.com/v1"


def test_the_dependency_takes_no_url_from_the_request():
    params = {"x_model_preset", "x_model_key", "x_demo_code"}
    deps = TestClient(create_app(create_engine("sqlite://"))).get("/openapi.json").json()
    headers = {
        p["name"].lower()
        for ops in deps["paths"].values()
        for op in ops.values()
        for p in op.get("parameters", [])
        if p["in"] == "header"
    }
    assert headers == {p.replace("_", "-") for p in params}


def test_a_demo_code_goes_to_the_gateway(gateway):
    resp = probe({"X-Demo-Code": FAKE_CODE})
    assert resp.status_code == 200
    assert resp.json() == {"preset": "demo", "base_url": gateway.url, "query": {}}
    assert probe({"X-Model-Preset": "demo", "X-Demo-Code": FAKE_CODE}).status_code == 200


def test_a_demo_client_without_a_gateway_is_503(no_gateway):
    # The dependency already refuses a code with no gateway; `client_for` checks again for
    # a caller that holds credentials after the setting is gone (an ingest job, in T2).
    with pytest.raises(HTTPException) as raised:
        client_for(Credentials(load_presets()["demo"], FAKE_CODE))
    assert raised.value.status_code == 503
    assert_no_secret(raised.value.detail, repr(raised.value))


def test_a_demo_code_without_a_gateway_is_503(no_gateway):
    resp = probe({"X-Demo-Code": FAKE_CODE})
    assert resp.status_code == 503
    assert "demo codes are not available" in resp.json()["detail"]
    assert_no_secret(resp.text)


@pytest.mark.parametrize(
    "headers, words",
    [
        ({"X-Model-Preset": "openai"}, "X-Model-Key is required"),
        ({"X-Model-Key": FAKE_KEY}, "X-Model-Preset is required"),
        ({"X-Model-Preset": "demo"}, "X-Demo-Code is required"),
        ({"X-Model-Preset": "demo", "X-Model-Key": FAKE_KEY}, "X-Demo-Code is required"),
        ({"X-Model-Preset": "openai", "X-Demo-Code": FAKE_CODE}, "only with the demo preset"),
        (
            {"X-Model-Preset": "openai", "X-Model-Key": FAKE_KEY, "X-Demo-Code": FAKE_CODE},
            "not both",
        ),
        ({"X-Model-Preset": "openai", "X-Model-Key": FAKE_KEY + " x"}, "not a valid key"),
        ({"X-Model-Preset": "openai", "X-Model-Key": "k" * 600}, "not a valid key"),
        ({"X-Demo-Code": FAKE_CODE + "\tx"}, "not a valid demo code"),
    ],
)
def test_a_bad_header_combination_is_400_and_echoes_no_secret(gateway, headers, words):
    resp = probe(headers)
    assert resp.status_code == 400
    assert words in resp.json()["detail"]
    assert_no_secret(resp.text)


def test_a_non_ascii_key_is_400_and_not_echoed():
    key = FAKE_KEY + "é"
    resp = TestClient(probe_app()).get(
        "/probe", headers=[(b"x-model-preset", b"openai"), (b"x-model-key", key.encode())]
    )
    assert resp.status_code == 400
    assert_no_secret(resp.text)


def test_unknown_preset_is_400_on_the_apps_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    with TestClient(create_app(create_engine(f"sqlite:///{tmp_path}/t.db"))) as client:
        bad = {"X-Model-Preset": "bogus", "X-Model-Key": FAKE_KEY}
        chat = client.post("/collections/papers/chat", json={"question": "hi"}, headers=bad)
        assert chat.status_code == 400
        files = {"file": ("doc.pdf", TWENTY_PAGES, "application/pdf")}
        up = client.post("/collections/papers/documents", files=files, headers=bad)
        assert up.status_code == 400
        assert_no_secret(chat.text, up.text)


# --- the credentials and the client hold the secret, and never show it ---


def test_credentials_and_config_do_not_show_the_secret(gateway):
    for creds in [
        Credentials(load_presets()["openai"], FAKE_KEY),
        Credentials(load_presets()["demo"], FAKE_CODE),
    ]:
        client = client_for(creds)
        try:
            assert_no_secret(repr(creds), str(creds), repr(client.config), str(client.config))
        finally:
            client.close()


def test_the_client_uses_the_preset_chat_model_for_both_roles():
    client = client_for(Credentials(load_presets()["openai"], FAKE_KEY))
    try:
        assert client.config.fast_model == client.config.reasoning_model == "gpt-5-nano"
        assert client.config.embedding_model == "text-embedding-3-small"
        assert client.config.api_key == FAKE_KEY
    finally:
        client.close()


def test_the_demo_preset_uses_the_gateways_model_names(gateway):
    # The demo gateway serves only `demo-chat` and `demo-embedding`; any other name is a 400.
    client = client_for(Credentials(load_presets()["demo"], FAKE_CODE))
    try:
        assert client.config.base_url == gateway.url
        assert client.config.fast_model == client.config.reasoning_model == "demo-chat"
        assert client.config.embedding_model == "demo-embedding"
    finally:
        client.close()


def test_the_key_presets_keep_their_providers_model_names():
    models = {
        p.id: (p.chat_model, p.embedding_model) for p in load_presets().values() if p.id != "demo"
    }
    assert models == {
        "openai": ("gpt-5-nano", "text-embedding-3-small"),
        "openrouter": ("qwen/qwen3.7-flash", "thenlper/gte-base"),
        "synthetic": ("hf:zai-org/GLM-5.3-Flash", None),
        "alibaba": ("qwen-flash", "text-embedding-v4"),
    }
    assert not any(name.startswith("demo-") for pair in models.values() for name in pair if name)


def test_the_client_ignores_the_env_endpoint(monkeypatch):
    for name, value in LLM_ENV.items():
        monkeypatch.setenv(name, value)
    client = client_for(Credentials(load_presets()["alibaba"], FAKE_KEY))
    try:
        assert client.config.base_url == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        assert client.config.fast_model == "qwen-flash"
    finally:
        client.close()


def test_embedding_with_a_preset_that_has_none_fails_without_a_call():
    client = client_for(Credentials(load_presets()["synthetic"], FAKE_KEY))
    try:
        with pytest.raises(NoEmbeddingModelError) as raised:
            client.embed(["text"])
        assert "no embedding model" in str(raised.value)
    finally:
        client.close()


# --- GET /presets ---


def presets_body(monkeypatch, **env) -> dict:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    resp = TestClient(create_app(create_engine("sqlite://"))).get("/presets")
    assert resp.status_code == 200
    return resp.json()


def test_get_presets_lists_metadata_only(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-env-owner-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://env-llm.internal:4000/v1")
    body = presets_body(monkeypatch, DEMO_GATEWAY_URL="http://litellm.internal:4000/v1")
    assert [p["id"] for p in body["presets"]] == [
        "openai",
        "openrouter",
        "synthetic",
        "alibaba",
        "demo",
    ]
    for preset in body["presets"]:
        assert set(preset) == {"id", "label", "kind", "chat_model", "embedding_model"}
    text = json.dumps(body)
    for secret in [
        "litellm.internal",
        "env-llm.internal",
        "sk-env-owner-key",
        "http",
        'key":',
        "api_key",
        "base_url",
    ]:
        assert secret not in text


def test_get_presets_omits_the_demo_without_a_gateway(monkeypatch, no_gateway):
    body = presets_body(monkeypatch)
    assert "demo" not in [p["id"] for p in body["presets"]]


@pytest.mark.parametrize("value, expected", [("true", True), ("1", True), ("", False)])
def test_get_presets_says_whether_the_app_is_a_public_demo(monkeypatch, value, expected):
    assert presets_body(monkeypatch, PUBLIC_DEMO=value)["public_demo"] is expected


def test_public_presets_matches_the_route(monkeypatch, no_gateway):
    assert presets_body(monkeypatch)["presets"] == public_presets(load_presets())


def test_get_presets_names_the_upload_ttl_in_a_public_demo(monkeypatch):
    """The page's public notice says uploads are deleted after N minutes; N is the server's."""
    body = presets_body(monkeypatch, PUBLIC_DEMO="true", DEMO_UPLOAD_TTL_MINUTES="45")
    assert body["upload_ttl_minutes"] == 45


def test_get_presets_ttl_defaults_to_60_minutes(monkeypatch):
    monkeypatch.delenv("DEMO_UPLOAD_TTL_MINUTES", raising=False)
    body = presets_body(monkeypatch, PUBLIC_DEMO="true")
    assert body["upload_ttl_minutes"] == 60


def test_get_presets_has_no_ttl_outside_a_public_demo(monkeypatch):
    body = presets_body(monkeypatch, PUBLIC_DEMO="", DEMO_UPLOAD_TTL_MINUTES="45")
    assert body["upload_ttl_minutes"] is None


# --- PUBLIC_DEMO: the env endpoint is not used for visitor actions ---


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes"])
def test_in_public_demo_the_env_endpoint_is_refused(value):
    with pytest.raises(LLMCredentialsRequired) as raised:
        LLMConfig.from_env({**LLM_ENV, "PUBLIC_DEMO": value})
    assert isinstance(raised.value, LLMConfigError)
    assert raised.value.setting == "X-Demo-Code or X-Model-Key"


@pytest.mark.parametrize("value", ["", "false", "0", "no"])
def test_outside_public_demo_the_env_endpoint_is_used(value):
    assert LLMConfig.from_env({**LLM_ENV, "PUBLIC_DEMO": value}).base_url == LLM_ENV["LLM_BASE_URL"]


def test_in_public_demo_the_chat_route_never_calls_the_env_endpoint(tmp_path, monkeypatch):
    env_llm = FakeServer()
    try:
        monkeypatch.setenv("PUBLIC_DEMO", "true")
        monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
        for name, value in {**LLM_ENV, "LLM_BASE_URL": env_llm.url}.items():
            monkeypatch.setenv(name, value)
        engine = create_engine(f"sqlite:///{tmp_path}/t.db")
        with TestClient(create_app(engine)) as client:
            # A collection every session can read (the sample): a session's unknown one is 404.
            # It is embedded with the model the demo's server embeds with.
            with engine.begin() as conn:
                conn.execute(
                    insert(collections).values(name="papers", embedding_model="demo-embedding")
                )
            resp = client.post("/collections/papers/chat", json={"question": "hi"})
        events = [json.loads(line) for line in resp.text.splitlines()]
        assert events[-1]["type"] == "error"
        assert "X-Demo-Code or X-Model-Key" in events[-1]["message"]
        assert env_llm.requests == []
    finally:
        env_llm.close()


def test_in_public_demo_ingestion_never_calls_the_env_endpoint(tmp_path, monkeypatch):
    env_llm = FakeServer()
    try:
        monkeypatch.setenv("PUBLIC_DEMO", "true")
        monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
        monkeypatch.delenv("OCR_URL", raising=False)
        for name, value in {**LLM_ENV, "LLM_BASE_URL": env_llm.url}.items():
            monkeypatch.setenv(name, value)
        app = create_app(create_engine(f"sqlite:///{tmp_path}/t.db"))
        with TestClient(app) as client:
            document = upload(client)
            app.state.worker.join()
            status = client.get(f"/documents/{document['id']}/status").json()
        assert status["state"] == "failed"
        assert env_llm.requests == []
    finally:
        env_llm.close()


# --- the planted key, through success and every failure path ---


def closed_port_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}/v1"


def echo(status: int, kind: str = "json"):
    """A reply that echoes the Authorization header back, as a careless upstream might."""

    def reply(server: FakeServer) -> tuple[int, bytes, str]:
        auth = server.requests[-1]["auth"] or ""
        if kind == "json":
            body = json.dumps({"error": {"message": f"Incorrect API key provided: {auth}"}})
            return status, body.encode(), "application/json"
        return status, f"upstream said: {auth}".encode(), "text/plain"

    return reply


def mid_stream_error(server: FakeServer):
    auth = server.requests[-1]["auth"]
    body = sse(delta('{"claims"'), {"error": {"message": f"bad key {auth}"}}, done=False)
    return 200, body, "text/event-stream"


CHAT_CASES = {
    "success": lambda s: (200, stream_json({"claims": [GLACIER_CLAIM]}), "text/event-stream"),
    "401 echo json": echo(401),
    "403 echo text": echo(403, "text"),
    "429 echo": echo(429),
    "500 echo": echo(500, "text"),
    "mid-stream error echo": mid_stream_error,
    "stream cut off": lambda s: (200, sse(delta("{"), done=False), "text/event-stream"),
    "not json": lambda s: (200, sse(delta("no json here"), {"usage": USAGE}), "text/event-stream"),
    "empty body": lambda s: (200, b"", "text/event-stream"),
}


@pytest.mark.parametrize("code", [False, True], ids=["key", "demo code"])
@pytest.mark.parametrize("case", list(CHAT_CASES) + ["unreachable", "timeout"])
def test_the_planted_secret_is_in_no_stream_line_log_or_error(
    gateway, monkeypatch, caplog, case, code
):
    caplog.set_level(logging.DEBUG)
    secret = FAKE_CODE if code else FAKE_KEY
    preset = "demo" if code else "openai"
    if not code:
        # The fake stands in for the provider; the client is still built from the preset.
        monkeypatch.setattr(
            credentials, "_base_url", lambda p: gateway.url if p.id == "openai" else None
        )
    if case == "unreachable":
        monkeypatch.setenv("DEMO_GATEWAY_URL", closed_port_url())
        monkeypatch.setattr(credentials, "_base_url", lambda p: closed_port_url())
    elif case == "timeout":
        gateway.delay = 1.0
    else:
        gateway.responder = lambda payload: CHAT_CASES[case](gateway)
    client = client_for(Credentials(load_presets()[preset], secret), timeout=0.3)
    try:
        resolve = resolver(page_texts(PASSAGES))
        events = list(answer_events(client, "How far?", PASSAGES, FILENAMES, resolve))
    finally:
        client.close()
    lines = "\n".join(json.dumps(e) for e in events)
    if case == "success":
        assert [e["type"] for e in events] == ["claim", "done"]
        assert gateway.requests[-1]["auth"] == f"Bearer {secret}"
    else:
        assert events[-1]["type"] == "error"
    assert_no_secret(lines, caplog.text)


EMBED_CASES = {
    "success": lambda payload: (
        200,
        json.dumps(
            {"data": [{"index": i, "embedding": [1.0, 0.5]} for i in range(len(payload["input"]))]}
        ).encode(),
        "application/json",
    ),
    "401 echo json": echo(401),
    "500 echo text": echo(500, "text"),
    "malformed": lambda payload: (200, b'{"data": 3}', "application/json"),
}


def dump_rows(engine) -> str:
    meta = MetaData()
    meta.reflect(engine)
    with engine.connect() as conn:
        return "\n".join(
            repr(row) for table in meta.sorted_tables for row in conn.execute(select(table))
        )


@pytest.mark.parametrize("case", list(EMBED_CASES) + ["unreachable"])
def test_the_planted_secret_is_in_no_stored_row_status_or_log(
    db_engine, gateway, monkeypatch, tmp_path, caplog, case
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("OCR_URL", raising=False)
    monkeypatch.setenv("EMBED_BATCH_SIZE", "8")
    if case == "unreachable":
        monkeypatch.setenv("DEMO_GATEWAY_URL", closed_port_url())
    else:
        reply = EMBED_CASES[case]
        gateway.responder = (lambda p: reply(gateway)) if "echo" in case else reply
    client = client_for(Credentials(load_presets()["demo"], FAKE_CODE), timeout=2.0)
    with ExitStack() as stack:
        stack.callback(client.close)
        app = create_app(db_engine, embedder=client)
        http = stack.enter_context(TestClient(app))
        document = upload(http)
        app.state.worker.join()
        status = http.get(f"/documents/{document['id']}/status")
    assert status.json()["state"] == ("ready" if case == "success" else "failed")
    assert_no_secret(status.text, dump_rows(db_engine), caplog.text)
    for path in (tmp_path / "uploads").rglob("*"):
        if path.is_file():
            assert FAKE_CODE.encode() not in path.read_bytes()


def test_an_http_error_from_the_client_keeps_no_secret(gateway):
    gateway.responder = lambda payload: echo(401)(gateway)
    client = client_for(Credentials(load_presets()["demo"], FAKE_CODE))
    try:
        with pytest.raises(LLMError) as raised:
            client.chat([{"role": "user", "content": "hi"}])
    finally:
        client.close()
    assert_no_secret(str(raised.value), repr(raised.value), raised.value.body)
    assert "[redacted]" in raised.value.body


def test_an_env_client_redacts_its_own_key_too():
    server = FakeServer()
    try:
        server.responder = lambda payload: echo(401, "text")(server)
        client = LLMClient(LLMConfig.from_env({**LLM_ENV, "LLM_BASE_URL": server.url}))
        with pytest.raises(LLMError) as raised:
            client.chat([{"role": "user", "content": "hi"}])
        client.close()
        assert "sk-env-owner-key" not in raised.value.body
    finally:
        server.close()

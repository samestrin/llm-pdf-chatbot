import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.llm import (
    DEFAULT_TIMEOUT_SECONDS,
    LLMClient,
    LLMConfig,
    LLMConfigError,
    LLMConnectionError,
    LLMHTTPError,
    LLMResponseError,
    LLMTimeoutError,
)

ENV = {
    "LLM_BASE_URL": "http://llm.example/v1",
    "LLM_API_KEY": "sk-test",
    "LLM_FAST_MODEL": "small",
    "LLM_REASONING_MODEL": "big",
    "LLM_EMBEDDING_MODEL": "embed",
}


class FakeServer:
    """A real HTTP server on localhost that answers with whatever a test sets."""

    def __init__(self):
        self.status = 200
        self.body: bytes = b"{}"
        self.delay = 0.0
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                fake.requests.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "json": json.loads(self.rfile.read(length) or b"null"),
                    }
                )
                time.sleep(fake.delay)
                try:
                    self.send_response(fake.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(fake.body)))
                    self.end_headers()
                    self.wfile.write(fake.body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def reply(self, payload, status=200):
        self.status = status
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


def make_client(server, timeout=5.0, api_key="sk-test"):
    return LLMClient(
        LLMConfig(
            base_url=server.url,
            api_key=api_key,
            fast_model="small",
            reasoning_model="big",
            embedding_model="embed",
            timeout=timeout,
        )
    )


def chat_reply(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


# --- config from the environment ---


def test_config_reads_environment():
    cfg = LLMConfig.from_env(ENV)
    assert cfg.base_url == "http://llm.example/v1"
    assert cfg.api_key == "sk-test"
    assert (cfg.fast_model, cfg.reasoning_model, cfg.embedding_model) == ("small", "big", "embed")
    assert cfg.timeout == DEFAULT_TIMEOUT_SECONDS == 60.0


def test_config_timeout_is_configurable():
    assert LLMConfig.from_env({**ENV, "LLM_TIMEOUT_SECONDS": "2.5"}).timeout == 2.5


@pytest.mark.parametrize("value", ["abc", "0", "-1", "nan", "inf"])
def test_config_rejects_bad_timeout(value):
    with pytest.raises(LLMConfigError, match="LLM_TIMEOUT_SECONDS"):
        LLMConfig.from_env({**ENV, "LLM_TIMEOUT_SECONDS": value})


@pytest.mark.parametrize(
    "name", ["LLM_BASE_URL", "LLM_FAST_MODEL", "LLM_REASONING_MODEL", "LLM_EMBEDDING_MODEL"]
)
def test_config_requires_setting(name):
    with pytest.raises(LLMConfigError, match=name):
        LLMConfig.from_env({k: v for k, v in ENV.items() if k != name})
    with pytest.raises(LLMConfigError, match=name):
        LLMConfig.from_env({**ENV, name: "  "})


def test_config_api_key_is_optional_for_local_endpoints():
    env = {k: v for k, v in ENV.items() if k != "LLM_API_KEY"}
    assert LLMConfig.from_env(env).api_key is None


# --- chat ---


def test_chat_fast_returns_content_and_sends_key(server):
    server.reply(chat_reply("hello"))
    assert make_client(server).chat([{"role": "user", "content": "hi"}]) == "hello"
    req = server.requests[0]
    assert req["path"] == "/v1/chat/completions"
    assert req["auth"] == "Bearer sk-test"
    assert req["json"]["model"] == "small"
    assert req["json"]["messages"] == [{"role": "user", "content": "hi"}]


def test_chat_reasoning_uses_reasoning_model(server):
    server.reply(chat_reply("deep"))
    assert make_client(server).chat([], model="reasoning") == "deep"
    assert server.requests[0]["json"]["model"] == "big"


def test_chat_without_key_sends_no_authorization(server):
    server.reply(chat_reply("ok"))
    make_client(server, api_key=None).chat([])
    assert server.requests[0]["auth"] is None


def test_chat_rejects_unknown_model_role(server):
    with pytest.raises(ValueError):
        make_client(server).chat([], model="embedding")


@pytest.mark.parametrize("status", [400, 401, 404, 429, 500, 503])
def test_non_2xx_is_http_error(server, status):
    server.reply({"error": {"message": "nope"}}, status=status)
    with pytest.raises(LLMHTTPError) as exc:
        make_client(server).chat([])
    assert exc.value.status_code == status
    assert "nope" in exc.value.body


def test_timeout_is_timeout_error(server):
    server.delay = 1.0
    server.reply(chat_reply("too late"))
    start = time.monotonic()
    with pytest.raises(LLMTimeoutError):
        make_client(server, timeout=0.2).chat([])
    assert time.monotonic() - start < 0.9


def test_unreachable_endpoint_is_connection_error(server):
    client = make_client(server)
    server.close()
    with pytest.raises(LLMConnectionError):
        client.chat([])


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"[]",
        b'{"choices": []}',
        b'{"choices": "x"}',
        b'{"choices": [{}]}',
        b'{"choices": [{"message": null}]}',
        b'{"choices": [{"message": {"content": null}}]}',
        b'{"choices": [{"message": {"content": 42}}]}',
        b'{"choices": [{"message": {"content": ""}}]}',
        b'{"choices": [{"message": {"content": "   "}}]}',
    ],
)
def test_empty_or_malformed_chat_body_is_response_error(server, body):
    server.reply(body)
    with pytest.raises(LLMResponseError):
        make_client(server).chat([])


# --- embeddings ---


def test_embed_returns_vectors_in_input_order(server):
    server.reply(
        {
            "data": [
                {"index": 1, "embedding": [0.3, 0.4]},
                {"index": 0, "embedding": [0.1, 2]},
            ]
        }
    )
    assert make_client(server).embed(["a", "b"]) == [[0.1, 2.0], [0.3, 0.4]]
    req = server.requests[0]
    assert req["path"] == "/v1/embeddings"
    assert req["json"] == {"model": "embed", "input": ["a", "b"]}


def test_embed_of_nothing_makes_no_call(server):
    assert make_client(server).embed([]) == []
    assert server.requests == []


def test_embed_non_2xx_is_http_error(server):
    server.reply(b"overloaded", status=503)
    with pytest.raises(LLMHTTPError) as exc:
        make_client(server).embed(["a"])
    assert exc.value.status_code == 503


def test_embed_timeout_is_timeout_error(server):
    server.delay = 1.0
    server.reply({"data": [{"index": 0, "embedding": [1.0]}]})
    with pytest.raises(LLMTimeoutError):
        make_client(server, timeout=0.2).embed(["a"])


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<html>",
        b'{"data": null}',
        b'{"data": []}',
        b'{"data": [{"index": 0, "embedding": []}]}',
        b'{"data": [{"index": 0, "embedding": ["x"]}]}',
        b'{"data": [{"index": 0, "embedding": [true]}]}',
        b'{"data": [{"index": 0}]}',
        b'{"data": [{"index": 5, "embedding": [1.0]}]}',
        b'{"data": [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [2.0]}]}',
    ],
)
def test_empty_or_malformed_embedding_body_is_response_error(server, body):
    server.reply(body)
    with pytest.raises(LLMResponseError):
        make_client(server).embed(["a"])


def test_embed_count_mismatch_is_response_error(server):
    server.reply({"data": [{"index": 0, "embedding": [1.0]}]})
    with pytest.raises(LLMResponseError):
        make_client(server).embed(["a", "b"])


@pytest.mark.parametrize(
    "body",
    [
        b'{"data": [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [2.0]}]}',
        b'{"data": [{"index": 0, "embedding": [1.0]}, {"index": true, "embedding": [2.0]}]}',
        b'{"data": [{"index": 0, "embedding": [1.0]}, "x"]}',
    ],
)
def test_embed_bad_items_for_two_texts_are_response_errors(server, body):
    server.reply(body)
    with pytest.raises(LLMResponseError):
        make_client(server).embed(["a", "b"])


def test_embed_without_index_keeps_response_order(server):
    server.reply({"data": [{"embedding": [1.0]}, {"embedding": [2.0]}]})
    assert make_client(server).embed(["a", "b"]) == [[1.0], [2.0]]


def test_closed_client_does_not_report_success(server):
    server.reply(chat_reply("hello"))
    client = make_client(server)
    client.close()
    with pytest.raises(RuntimeError):
        client.chat([])

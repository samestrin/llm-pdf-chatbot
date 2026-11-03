import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.llm import (
    DEFAULT_TIMEOUT_SECONDS,
    ChatResult,
    LLMClient,
    LLMConfig,
    LLMConfigError,
    LLMConnectionError,
    LLMError,
    LLMHTTPError,
    LLMResponseError,
    LLMStreamError,
    LLMTimeoutError,
    Usage,
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
        self.body: bytes | list[bytes] = b"{}"
        self.content_type = "application/json"
        self.delay = 0.0
        # Sent between the pieces of a body given as a list (a slow stream).
        self.piece_delay = 0.0
        # Bytes added to the declared Content-Length: the connection drops mid-body.
        self.missing_bytes = 0
        # Replies used one per request, before falling back to the fields above.
        self.queue: list[tuple[int, bytes | list[bytes], str]] = []
        # When set, answers every request: called with its JSON, returns (status, body, type).
        self.responder = None
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
                if fake.responder is not None:
                    status, body, content_type = fake.responder(fake.requests[-1]["json"])
                elif fake.queue:
                    status, body, content_type = fake.queue.pop(0)
                else:
                    status, body, content_type = fake.status, fake.body, fake.content_type
                pieces = body if isinstance(body, list) else [body]
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    length = sum(len(p) for p in pieces) + fake.missing_bytes
                    self.send_header("Content-Length", str(length))
                    self.end_headers()
                    for i, piece in enumerate(pieces):
                        if i:
                            time.sleep(fake.piece_delay)
                        self.wfile.write(piece)
                        self.wfile.flush()
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
        self.content_type = "application/json"

    def then(self, payload, status=200):
        """Queue a JSON reply for the next request not yet answered."""
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.queue.append((status, body, "application/json"))

    def stream(self, body, status=200):
        """Answer with a server-sent event stream (`body` bytes, or pieces sent apart)."""
        self.status = status
        self.body = body
        self.content_type = "text/event-stream"

    def then_stream(self, body, status=200):
        self.queue.append((status, body, "text/event-stream"))

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


def make_client(server, timeout=5.0, api_key="sk-test", price_in=None, price_out=None):
    return LLMClient(
        LLMConfig(
            base_url=server.url,
            api_key=api_key,
            fast_model="small",
            reasoning_model="big",
            embedding_model="embed",
            timeout=timeout,
            fast_price_in_per_mtok=price_in,
            fast_price_out_per_mtok=price_out,
        )
    )


def chat_reply(content, usage=None, finish_reason="stop"):
    body = {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ]
    }
    if usage is not None:
        body["usage"] = usage
    return body


def delta(text, finish_reason=None):
    return {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish_reason}]}


def sse(*events, done=True) -> bytes:
    """Encode chunks as an OpenAI-style event stream, ending in `[DONE]` unless `done` is false."""
    out = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
    return out + (b"data: [DONE]\n\n" if done else b"")


USAGE = {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}


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


def test_config_prices_are_unset_by_default():
    cfg = LLMConfig.from_env(ENV)
    assert cfg.fast_price_in_per_mtok is None
    assert cfg.fast_price_out_per_mtok is None


def test_config_reads_fast_prices():
    cfg = LLMConfig.from_env(
        {**ENV, "LLM_PRICE_FAST_PER_MTOK_IN": "0.15", "LLM_PRICE_FAST_PER_MTOK_OUT": " 0.6 "}
    )
    assert (cfg.fast_price_in_per_mtok, cfg.fast_price_out_per_mtok) == (0.15, 0.6)


def test_config_accepts_explicit_zero_prices():
    cfg = LLMConfig.from_env(
        {**ENV, "LLM_PRICE_FAST_PER_MTOK_IN": "0", "LLM_PRICE_FAST_PER_MTOK_OUT": "0"}
    )
    assert (cfg.fast_price_in_per_mtok, cfg.fast_price_out_per_mtok) == (0.0, 0.0)


@pytest.mark.parametrize("name", ["LLM_PRICE_FAST_PER_MTOK_IN", "LLM_PRICE_FAST_PER_MTOK_OUT"])
def test_config_needs_both_prices_or_neither(name):
    with pytest.raises(LLMConfigError, match="LLM_PRICE_FAST_PER_MTOK"):
        LLMConfig.from_env({**ENV, name: "1.0"})


@pytest.mark.parametrize("value", ["abc", "-1", "nan", "inf"])
def test_config_rejects_bad_price(value):
    env = {**ENV, "LLM_PRICE_FAST_PER_MTOK_IN": value, "LLM_PRICE_FAST_PER_MTOK_OUT": "1"}
    with pytest.raises(LLMConfigError, match="LLM_PRICE_FAST_PER_MTOK_IN"):
        LLMConfig.from_env(env)


# --- chat ---


def test_chat_fast_returns_content_and_sends_key(server):
    server.reply(chat_reply("hello"))
    assert make_client(server).chat([{"role": "user", "content": "hi"}]).text == "hello"
    req = server.requests[0]
    assert req["path"] == "/v1/chat/completions"
    assert req["auth"] == "Bearer sk-test"
    assert req["json"]["model"] == "small"
    assert req["json"]["messages"] == [{"role": "user", "content": "hi"}]


def test_chat_reasoning_uses_reasoning_model(server):
    server.reply(chat_reply("deep"))
    assert make_client(server).chat([], model="reasoning").text == "deep"
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


# --- usage, model and cost ---


def test_chat_returns_usage_and_model(server):
    server.reply(chat_reply("hello", usage=USAGE))
    result = make_client(server).chat([])
    assert isinstance(result, ChatResult)
    assert result.text == "hello"
    assert result.model == "small"
    assert result.usage == Usage(input_tokens=12, output_tokens=5)
    assert result.usage.total_tokens == 17
    assert result.data is None


def test_chat_cost_is_none_without_prices(server):
    server.reply(chat_reply("hello", usage=USAGE))
    result = make_client(server).chat([])
    assert result.usage is not None
    assert result.cost is None


def test_chat_cost_from_fast_prices(server):
    server.reply(
        chat_reply("hello", usage={"prompt_tokens": 1_000_000, "completion_tokens": 500_000})
    )
    result = make_client(server, price_in=2.0, price_out=8.0).chat([])
    assert result.cost == pytest.approx(6.0)


def test_chat_cost_with_explicit_zero_prices_is_zero(server):
    server.reply(chat_reply("hello", usage=USAGE))
    assert make_client(server, price_in=0.0, price_out=0.0).chat([]).cost == 0.0


def test_reasoning_cost_is_none_even_with_fast_prices(server):
    server.reply(chat_reply("deep", usage=USAGE))
    result = make_client(server, price_in=2.0, price_out=8.0).chat([], model="reasoning")
    assert result.model == "big"
    assert result.usage == Usage(12, 5)
    assert result.cost is None


@pytest.mark.parametrize(
    "usage",
    [
        None,
        "x",
        {},
        {"prompt_tokens": 1},
        {"prompt_tokens": "1", "completion_tokens": 2},
        {"prompt_tokens": -1, "completion_tokens": 2},
        {"prompt_tokens": True, "completion_tokens": 2},
    ],
)
def test_missing_or_malformed_usage_gives_no_usage_and_no_cost(server, usage):
    body = chat_reply("hello")
    if usage is not None:
        body["usage"] = usage
    server.reply(body)
    result = make_client(server, price_in=2.0, price_out=8.0).chat([])
    assert result.text == "hello"
    assert result.usage is None
    assert result.cost is None


@pytest.mark.parametrize("reason", ["length", "content_filter"])
def test_chat_cut_off_by_finish_reason_is_response_error(server, reason):
    server.reply(chat_reply('{"claims": [', finish_reason=reason))
    with pytest.raises(LLMResponseError, match=reason):
        make_client(server).chat([])


# --- streaming ---


def test_stream_yields_deltas_then_result_with_usage(server):
    server.stream(
        sse(
            {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
            delta("Hel"),
            delta("lo"),
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": USAGE},
        )
    )
    stream = make_client(server, price_in=1.0, price_out=2.0).chat_stream(
        [{"role": "user", "content": "hi"}]
    )
    assert list(stream) == ["Hel", "lo"]
    result = stream.result
    assert result.text == "Hello"
    assert result.model == "small"
    assert result.usage == Usage(12, 5)
    assert result.cost == pytest.approx((12 * 1.0 + 5 * 2.0) / 1_000_000)
    req = server.requests[0]
    assert req["path"] == "/v1/chat/completions"
    assert req["json"]["stream"] is True
    assert req["json"]["stream_options"] == {"include_usage": True}
    assert req["json"]["messages"] == [{"role": "user", "content": "hi"}]


def test_stream_without_usage_or_prices_has_none_cost(server):
    server.stream(sse(delta("ok", finish_reason="stop")))
    stream = make_client(server).chat_stream([])
    assert list(stream) == ["ok"]
    assert stream.result.usage is None
    assert stream.result.cost is None


def test_stream_ignores_comments_blank_lines_and_crlf(server):
    body = (
        b": keep-alive\r\n\r\n"
        b"event: message\r\n"
        b"data: " + json.dumps(delta("a")).encode() + b"\r\n\r\n"
        b"data:" + json.dumps(delta("b", finish_reason="stop")).encode() + b"\r\n\r\n"
        b"data: [DONE]\r\n\r\n"
    )
    server.stream(body)
    stream = make_client(server).chat_stream([])
    assert list(stream) == ["a", "b"]
    assert stream.result.text == "ab"


def test_stream_result_before_the_end_is_an_error(server):
    server.stream(sse(delta("a"), delta("b", finish_reason="stop")))
    stream = make_client(server).chat_stream([])
    with pytest.raises(LLMStreamError):
        _ = stream.result
    it = iter(stream)
    assert next(it) == "a"
    with pytest.raises(LLMStreamError):
        _ = stream.result
    stream.close()


def test_stream_cut_off_without_done_raises_and_has_no_result(server):
    server.stream(sse(delta("Hel"), delta("lo"), done=False))
    stream = make_client(server).chat_stream([])
    seen = []
    with pytest.raises(LLMStreamError, match="DONE"):
        seen.extend(stream)  # keeps the pieces that came before the error
    assert seen == ["Hel", "lo"]
    with pytest.raises(LLMStreamError):
        _ = stream.result


def test_stream_dropped_connection_raises(server):
    server.missing_bytes = 100
    server.stream(sse(delta("Hel"), done=False))
    stream = make_client(server).chat_stream([])
    with pytest.raises(LLMError):
        list(stream)
    with pytest.raises(LLMStreamError):
        _ = stream.result


def test_stream_not_answering_is_timeout_error(server):
    server.delay = 1.0
    server.stream(sse(delta("too late", finish_reason="stop")))
    stream = make_client(server, timeout=0.2).chat_stream([])
    start = time.monotonic()
    with pytest.raises(LLMTimeoutError):
        list(stream)
    assert time.monotonic() - start < 0.9
    with pytest.raises(LLMStreamError):
        _ = stream.result


def test_stream_stalling_mid_way_is_timeout_error(server):
    server.piece_delay = 1.0
    server.stream([sse(delta("a"), done=False), sse(delta("b", finish_reason="stop"))])
    stream = make_client(server, timeout=0.2).chat_stream([])
    start = time.monotonic()
    with pytest.raises(LLMTimeoutError):
        list(stream)
    assert time.monotonic() - start < 0.9
    with pytest.raises(LLMStreamError):
        _ = stream.result


@pytest.mark.parametrize(
    "line",
    [
        b"data: not json",
        b"data: []",
        b'data: {"choices": "x"}',
        b'data: {"choices": ["x"]}',
        b'data: {"choices": [{"delta": "x"}]}',
        b'data: {"choices": [{"delta": {"content": 42}}]}',
        b'data: {"error": {"message": "overloaded"}}',
    ],
)
def test_malformed_stream_chunk_is_stream_error(server, line):
    server.stream(sse(delta("a"), done=False) + line + b"\n\n" + b"data: [DONE]\n\n")
    stream = make_client(server).chat_stream([])
    with pytest.raises(LLMStreamError):
        list(stream)
    with pytest.raises(LLMStreamError):
        _ = stream.result


@pytest.mark.parametrize("reason", ["length", "content_filter"])
def test_stream_cut_off_by_finish_reason_is_stream_error(server, reason):
    server.stream(sse(delta("a"), delta("b", finish_reason=reason)))
    stream = make_client(server).chat_stream([])
    with pytest.raises(LLMStreamError, match=reason):
        list(stream)


def test_empty_stream_is_stream_error(server):
    server.stream(sse({"choices": [{"delta": {"content": "  "}, "finish_reason": "stop"}]}))
    with pytest.raises(LLMStreamError, match="empty"):
        list(make_client(server).chat_stream([]))


def test_stream_non_2xx_is_http_error(server):
    server.reply({"error": {"message": "nope"}}, status=503)
    with pytest.raises(LLMHTTPError) as exc:
        list(make_client(server).chat_stream([]))
    assert exc.value.status_code == 503
    assert "nope" in exc.value.body


def test_stream_unreachable_endpoint_is_connection_error(server):
    client = make_client(server)
    server.close()
    with pytest.raises(LLMConnectionError):
        list(client.chat_stream([]))


def test_stream_errors_are_llm_errors():
    assert issubclass(LLMStreamError, LLMResponseError)


# --- structured output ---

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def test_json_schema_is_sent_as_response_format(server):
    server.reply(chat_reply('{"answer": "42"}', usage=USAGE))
    result = make_client(server).chat([{"role": "user", "content": "q"}], json_schema=SCHEMA)
    assert result.data == {"answer": "42"}
    assert result.text == '{"answer": "42"}'
    assert result.usage == Usage(12, 5)
    req = server.requests[0]["json"]
    assert req["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "response", "schema": SCHEMA, "strict": True},
    }
    assert req["messages"] == [{"role": "user", "content": "q"}]


def test_no_response_format_without_schema(server):
    server.reply(chat_reply("plain"))
    make_client(server).chat([])
    assert "response_format" not in server.requests[0]["json"]


@pytest.mark.parametrize("status", [400, 422])
def test_rejected_response_format_falls_back_to_prompt_and_parse(server, status):
    server.then({"error": {"message": "response_format is not supported"}}, status=status)
    server.then(chat_reply('Here it is:\n```json\n{"answer": "42"}\n```', usage=USAGE))
    messages = [{"role": "user", "content": "q"}]
    result = make_client(server).chat(messages, json_schema=SCHEMA)
    assert result.data == {"answer": "42"}
    assert result.usage == Usage(12, 5)
    assert len(server.requests) == 2
    retry = server.requests[1]["json"]
    assert "response_format" not in retry
    system, user = retry["messages"]
    assert system["role"] == "system"
    assert json.dumps(SCHEMA) in system["content"]
    assert user == {"role": "user", "content": "q"}
    assert messages == [{"role": "user", "content": "q"}]  # the caller's list is untouched


def test_fallback_extends_an_existing_system_message(server):
    server.then({"error": "unsupported"}, status=400)
    server.then(chat_reply('{"answer": "42"}'))
    make_client(server).chat(
        [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "q"}],
        json_schema=SCHEMA,
    )
    system, user = server.requests[1]["json"]["messages"]
    assert system["role"] == "system"
    assert system["content"].startswith("Be brief.")
    assert json.dumps(SCHEMA) in system["content"]
    assert user["content"] == "q"


def test_client_remembers_response_format_is_unsupported(server):
    server.then({"error": "unsupported"}, status=400)
    server.then(chat_reply('{"answer": "1"}'))
    server.then(chat_reply('{"answer": "2"}'))
    client = make_client(server)
    assert client.chat([], json_schema=SCHEMA).data == {"answer": "1"}
    assert client.chat([], json_schema=SCHEMA).data == {"answer": "2"}
    assert len(server.requests) == 3
    assert "response_format" not in server.requests[2]["json"]


def test_bad_request_that_is_not_about_the_format_is_not_remembered(server):
    server.then({"error": "context too long"}, status=400)
    server.then({"error": "context too long"}, status=400)
    server.then(chat_reply('{"answer": "1"}'))
    client = make_client(server)
    with pytest.raises(LLMHTTPError) as exc:
        client.chat([], json_schema=SCHEMA)
    assert exc.value.status_code == 400
    client.chat([], json_schema=SCHEMA)
    assert "response_format" in server.requests[2]["json"]


def test_server_error_does_not_fall_back(server):
    server.reply({"error": "down"}, status=500)
    with pytest.raises(LLMHTTPError):
        make_client(server).chat([], json_schema=SCHEMA)
    assert len(server.requests) == 1


@pytest.mark.parametrize("content", ["not json", '{"answer": ', "```json\n{oops}\n```"])
def test_unparsable_structured_reply_is_response_error(server, content):
    server.reply(chat_reply(content))
    with pytest.raises(LLMResponseError, match="JSON"):
        make_client(server).chat([], json_schema=SCHEMA)


def test_unparsable_fallback_reply_is_response_error(server):
    server.then({"error": "unsupported"}, status=400)
    server.then(chat_reply("Sorry, I cannot do that."))
    with pytest.raises(LLMResponseError, match="JSON"):
        make_client(server).chat([], json_schema=SCHEMA)


def test_stream_with_json_schema_parses_the_whole_reply(server):
    server.stream(sse(delta('{"answer": '), delta('"42"}', finish_reason="stop")))
    stream = make_client(server).chat_stream([], json_schema=SCHEMA)
    assert list(stream) == ['{"answer": ', '"42"}']
    assert stream.result.data == {"answer": "42"}
    assert server.requests[0]["json"]["response_format"]["type"] == "json_schema"


def test_stream_with_rejected_response_format_falls_back(server):
    server.then({"error": "unsupported"}, status=400)
    server.then_stream(sse(delta('{"answer": "42"}', finish_reason="stop")))
    stream = make_client(server).chat_stream([], json_schema=SCHEMA)
    assert list(stream) == ['{"answer": "42"}']
    assert stream.result.data == {"answer": "42"}
    assert len(server.requests) == 2
    assert "response_format" not in server.requests[1]["json"]
    assert server.requests[1]["json"]["stream"] is True


def test_stream_with_unparsable_structured_reply_is_stream_error(server):
    server.stream(sse(delta('{"answer": '), delta("oops", finish_reason="stop")))
    stream = make_client(server).chat_stream([], json_schema=SCHEMA)
    with pytest.raises(LLMResponseError, match="JSON"):
        list(stream)
    with pytest.raises(LLMStreamError):
        _ = stream.result

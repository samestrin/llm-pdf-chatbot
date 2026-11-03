"""One client for any OpenAI-compatible endpoint.

A failed model call raises an `LLMError` subclass. It never returns an empty
string or an empty vector, so a caller cannot mistake a failure for an answer.
A stream that is malformed or stops before its end raises too: its `result` is
only ever set by a stream that finished cleanly.
"""

import json
import math
import os
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx

DEFAULT_TIMEOUT_SECONDS = 60.0

ChatModel = Literal["fast", "reasoning"]

# How an endpoint without structured output turns down `response_format`.
RESPONSE_FORMAT_REJECTED = frozenset({400, 422})
# A reply that ended for one of these reasons is incomplete.
CUT_OFF_FINISH_REASONS = frozenset({"length", "content_filter"})
JSON_INSTRUCTION = (
    "Reply with a single JSON value that matches this JSON Schema, and nothing else:\n"
)


class LLMError(Exception):
    """Base class for every model call failure."""


class LLMConfigError(LLMError):
    """The environment does not describe a usable endpoint."""


class LLMConnectionError(LLMError):
    """The endpoint could not be reached."""


class LLMTimeoutError(LLMError):
    """The endpoint did not answer within the configured timeout."""


class LLMHTTPError(LLMError):
    """The endpoint answered with a non-2xx status."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"model endpoint returned HTTP {status_code}: {body[:500]}")


class LLMResponseError(LLMError):
    """The endpoint answered 2xx with an empty, malformed or cut-off body."""


class LLMStreamError(LLMResponseError):
    """A streamed reply was malformed, broke off, or was read before it finished."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ChatResult:
    """A whole reply. `usage` is `None` when the endpoint did not report it, and
    `cost` is `None` when usage or prices are missing; neither is ever a stand-in 0."""

    text: str
    model: str
    usage: Usage | None
    cost: float | None
    # The parsed reply when the call asked for JSON, otherwise `None`.
    data: Any = None


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str | None
    fast_model: str
    reasoning_model: str
    embedding_model: str
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    # Prices of the fast model per million tokens; both set or both `None`.
    fast_price_in_per_mtok: float | None = None
    fast_price_out_per_mtok: float | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LLMConfig":
        env = os.environ if env is None else env

        def required(name: str) -> str:
            value = env.get(name, "").strip()
            if not value:
                raise LLMConfigError(f"{name} is not set")
            return value

        raw_timeout = env.get("LLM_TIMEOUT_SECONDS", "").strip()
        timeout = DEFAULT_TIMEOUT_SECONDS
        if raw_timeout:
            try:
                timeout = float(raw_timeout)
            except ValueError:
                timeout = math.nan
            if not math.isfinite(timeout) or timeout <= 0:
                raise LLMConfigError(
                    f"LLM_TIMEOUT_SECONDS must be a positive number of seconds, got {raw_timeout!r}"
                )

        def price(name: str) -> float | None:
            raw = env.get(name, "").strip()
            if not raw:
                return None
            try:
                value = float(raw)
            except ValueError:
                value = math.nan
            if not math.isfinite(value) or value < 0:
                raise LLMConfigError(f"{name} must be a price of zero or more, got {raw!r}")
            return value

        price_in = price("LLM_PRICE_FAST_PER_MTOK_IN")
        price_out = price("LLM_PRICE_FAST_PER_MTOK_OUT")
        if (price_in is None) != (price_out is None):
            raise LLMConfigError(
                "LLM_PRICE_FAST_PER_MTOK_IN and LLM_PRICE_FAST_PER_MTOK_OUT must be set together"
            )

        return cls(
            base_url=required("LLM_BASE_URL"),
            api_key=env.get("LLM_API_KEY", "").strip() or None,
            fast_model=required("LLM_FAST_MODEL"),
            reasoning_model=required("LLM_REASONING_MODEL"),
            embedding_model=required("LLM_EMBEDDING_MODEL"),
            timeout=timeout,
            fast_price_in_per_mtok=price_in,
            fast_price_out_per_mtok=price_out,
        )


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config
        headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
        self._http = httpx.Client(
            base_url=config.base_url.rstrip("/") + "/",
            headers=headers,
            timeout=config.timeout,
        )
        # Until the endpoint turns `response_format` down; then JSON is asked for in the prompt.
        self._response_format_ok = True

    def close(self) -> None:
        self._http.close()

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: ChatModel = "fast",
        json_schema: Mapping[str, Any] | None = None,
    ) -> ChatResult:
        """Return the whole reply, or raise an `LLMError`.

        With `json_schema` the reply is asked for as JSON and parsed into `data`.
        """
        name = self._model_name(model)
        body = self._send_chat(
            lambda payload: self._post("chat/completions", payload),
            {"model": name, "messages": messages},
            json_schema,
        )
        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("chat response has no choices[0].message.content") from exc
        reason = choice.get("finish_reason")
        if reason in CUT_OFF_FINISH_REASONS:
            raise LLMResponseError(f"chat reply was cut off (finish_reason {reason!r})")
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("chat response content is empty")
        return self._result(content, model, body.get("usage"), json_schema)

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: ChatModel = "fast",
        json_schema: Mapping[str, Any] | None = None,
    ) -> "ChatStream":
        """Stream the reply: iterate for text pieces, then read `result`.

        The request is sent on the first iteration. Iteration raises an `LLMError`
        when the call fails or the stream is malformed or cut off.
        """
        self._model_name(model)
        return ChatStream(self._stream_pieces(messages, model, json_schema))

    def _stream_pieces(
        self,
        messages: list[dict[str, Any]],
        model: ChatModel,
        json_schema: Mapping[str, Any] | None,
    ) -> Iterator[str | ChatResult]:
        payload = {
            "model": self._model_name(model),
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        resp = self._send_chat(self._open_stream, payload, json_schema)
        parts: list[str] = []
        usage: Any = None
        reason: Any = None
        done = False
        try:
            for line in resp.iter_lines():
                if not line or line.startswith(":"):
                    continue
                field, _, value = line.partition(":")
                if field != "data":
                    continue
                value = value.removeprefix(" ")
                if value == "[DONE]":
                    done = True
                    break
                chunk = _stream_chunk(value)
                if chunk.get("usage") is not None:
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    if not isinstance(choice, dict):
                        raise LLMStreamError("stream choice is not an object")
                    piece = _stream_delta(choice)
                    if piece:
                        parts.append(piece)
                        yield piece
                    if choice.get("finish_reason") is not None:
                        reason = choice["finish_reason"]
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"model stream stalled for more than {self.config.timeout:g} s"
            ) from exc
        except httpx.TransportError as exc:
            raise LLMStreamError(f"model stream broke off: {exc}") from exc
        finally:
            resp.close()

        if not done:
            raise LLMStreamError("model stream ended before [DONE]")
        if reason in CUT_OFF_FINISH_REASONS:
            raise LLMStreamError(f"streamed reply was cut off (finish_reason {reason!r})")
        text = "".join(parts)
        if not text.strip():
            raise LLMStreamError("streamed reply is empty")
        yield self._result(text, model, usage, json_schema)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per text, in input order, or raise an `LLMError`."""
        if not texts:
            return []

        body = self._post("embeddings", {"model": self.config.embedding_model, "input": texts})
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise LLMResponseError(f"embedding response does not hold {len(texts)} vectors")

        vectors: list[list[float] | None] = [None] * len(texts)
        for position, item in enumerate(data):
            if not isinstance(item, dict):
                raise LLMResponseError("embedding item is not an object")
            index = item.get("index", position)
            vector = item.get("embedding")
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(texts):
                raise LLMResponseError(f"embedding index {index!r} is out of range")
            if vectors[index] is not None:
                raise LLMResponseError(f"embedding index {index} appears twice")
            if (
                not isinstance(vector, list)
                or not vector
                or not all(isinstance(x, int | float) and not isinstance(x, bool) for x in vector)
            ):
                raise LLMResponseError(f"embedding {index} is not a non-empty list of numbers")
            vectors[index] = [float(x) for x in vector]
        return vectors  # type: ignore[return-value]  # every slot was filled above

    def _model_name(self, model: ChatModel) -> str:
        if model == "fast":
            return self.config.fast_model
        if model == "reasoning":
            return self.config.reasoning_model
        raise ValueError(f"chat model must be 'fast' or 'reasoning', got {model!r}")

    def _send_chat(
        self,
        send: Callable[[dict[str, Any]], Any],
        payload: dict[str, Any],
        json_schema: Mapping[str, Any] | None,
    ) -> Any:
        """Send a chat call, asking for JSON through `response_format` while the
        endpoint accepts it, and through the prompt once it has turned it down."""
        if json_schema is None:
            return send(payload)
        prompted = {**payload, "messages": _with_json_instruction(payload["messages"], json_schema)}
        if not self._response_format_ok:
            return send(prompted)
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": json_schema, "strict": True},
        }
        try:
            return send({**payload, "response_format": response_format})
        except LLMHTTPError as exc:
            if exc.status_code not in RESPONSE_FORMAT_REJECTED:
                raise
        reply = send(prompted)
        # Remembered only now: a 400 the prompt retry also gets was not about the format.
        self._response_format_ok = False
        return reply

    def _result(
        self, text: str, model: ChatModel, raw_usage: Any, json_schema: Mapping[str, Any] | None
    ) -> ChatResult:
        usage = _usage(raw_usage)
        cost = None
        price_in = self.config.fast_price_in_per_mtok
        price_out = self.config.fast_price_out_per_mtok
        if model == "fast" and usage and price_in is not None and price_out is not None:
            cost = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1_000_000
        return ChatResult(
            text=text,
            model=self._model_name(model),
            usage=usage,
            cost=cost,
            data=_parse_json(text) if json_schema is not None else None,
        )

    def _open_stream(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            resp = self._http.send(
                self._http.build_request("POST", "chat/completions", json=payload), stream=True
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"model endpoint did not answer within {self.config.timeout:g} s"
            ) from exc
        except httpx.TransportError as exc:
            raise LLMConnectionError(f"model endpoint unreachable: {exc}") from exc
        if not resp.is_success:
            try:
                body = resp.read().decode(errors="replace")
            except httpx.HTTPError:
                body = ""
            finally:
                resp.close()
            raise LLMHTTPError(resp.status_code, body)
        return resp

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            resp = self._http.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"model endpoint did not answer within {self.config.timeout:g} s"
            ) from exc
        except httpx.TransportError as exc:
            raise LLMConnectionError(f"model endpoint unreachable: {exc}") from exc

        if not resp.is_success:
            raise LLMHTTPError(resp.status_code, resp.text)
        if not resp.content.strip():
            raise LLMResponseError("model endpoint returned an empty body")
        try:
            return resp.json()
        except ValueError as exc:
            raise LLMResponseError("model endpoint returned a body that is not JSON") from exc


class ChatStream:
    """The pieces of a streamed reply, then the whole reply in `result`.

    `result` raises `LLMStreamError` until the stream has finished cleanly, so a
    stream that failed part way never yields a result that looks complete.
    """

    def __init__(self, items: Iterator[str | ChatResult]):
        self._items = items
        self._result: ChatResult | None = None

    def __iter__(self) -> Iterator[str]:
        for item in self._items:
            if isinstance(item, ChatResult):
                self._result = item
            else:
                yield item

    @property
    def result(self) -> ChatResult:
        if self._result is None:
            raise LLMStreamError("the stream has not finished cleanly")
        return self._result

    def close(self) -> None:
        """Stop reading and release the connection; safe at any point."""
        self._items.close()  # type: ignore[attr-defined]  # a generator


def _stream_chunk(value: str) -> dict[str, Any]:
    try:
        chunk = json.loads(value)
    except ValueError as exc:
        raise LLMStreamError("stream chunk is not JSON") from exc
    if not isinstance(chunk, dict):
        raise LLMStreamError("stream chunk is not an object")
    if "error" in chunk:
        raise LLMStreamError(f"model endpoint reported an error mid-stream: {chunk['error']}"[:500])
    if not isinstance(chunk.get("choices") or [], list):
        raise LLMStreamError("stream chunk choices is not a list")
    return chunk


def _stream_delta(choice: dict[str, Any]) -> str:
    delta = choice.get("delta") or {}
    if not isinstance(delta, dict):
        raise LLMStreamError("stream delta is not an object")
    content = delta.get("content")
    if content is None:
        return ""
    if not isinstance(content, str):
        raise LLMStreamError("stream delta content is not text")
    return content


def _usage(raw: Any) -> Usage | None:
    """Token counts as the endpoint reported them, or `None` when missing or malformed."""
    if not isinstance(raw, dict):
        return None
    counts = raw.get("prompt_tokens"), raw.get("completion_tokens")
    if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in counts):
        return None
    return Usage(input_tokens=counts[0], output_tokens=counts[1])


def _with_json_instruction(
    messages: list[dict[str, Any]], json_schema: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """A copy of `messages` whose system message asks for JSON matching the schema."""
    instruction = JSON_INSTRUCTION + json.dumps(json_schema)
    first = messages[0] if messages else None
    if first and first.get("role") == "system" and isinstance(first.get("content"), str):
        return [{**first, "content": f"{first['content']}\n\n{instruction}"}, *messages[1:]]
    return [{"role": "system", "content": instruction}, *messages]


def _parse_json(text: str) -> Any:
    """Parse a JSON reply, also when a model wrapped it in a code fence or a sentence."""
    candidates = [text.strip()]
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        candidates.append(fence.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    raise LLMResponseError("model reply is not valid JSON")

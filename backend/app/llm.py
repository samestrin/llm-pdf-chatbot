"""One client for any OpenAI-compatible endpoint.

A failed model call raises an `LLMError` subclass. It never returns an empty
string or an empty vector, so a caller cannot mistake a failure for an answer.
"""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx

DEFAULT_TIMEOUT_SECONDS = 60.0

ChatModel = Literal["fast", "reasoning"]


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
    """The endpoint answered 2xx with an empty or malformed body."""


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str | None
    fast_model: str
    reasoning_model: str
    embedding_model: str
    timeout: float = DEFAULT_TIMEOUT_SECONDS

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

        return cls(
            base_url=required("LLM_BASE_URL"),
            api_key=env.get("LLM_API_KEY", "").strip() or None,
            fast_model=required("LLM_FAST_MODEL"),
            reasoning_model=required("LLM_REASONING_MODEL"),
            embedding_model=required("LLM_EMBEDDING_MODEL"),
            timeout=timeout,
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

    def close(self) -> None:
        self._http.close()

    def chat(self, messages: list[dict[str, Any]], model: ChatModel = "fast") -> str:
        """Return the assistant's reply text, or raise an `LLMError`."""
        if model == "fast":
            name = self.config.fast_model
        elif model == "reasoning":
            name = self.config.reasoning_model
        else:
            raise ValueError(f"chat model must be 'fast' or 'reasoning', got {model!r}")

        body = self._post("chat/completions", {"model": name, "messages": messages})
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("chat response has no choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("chat response content is empty")
        return content

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

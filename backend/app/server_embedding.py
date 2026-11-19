"""In a public demo the server embeds every document and every question itself.

It sends them to the demo gateway (`DEMO_GATEWAY_URL`) with a key of its own
(`DEMO_EMBEDDING_KEY`), on the demo preset's embedding model, so every collection, the
read-only sample included, is embedded with one model. A visitor's key or demo code goes
to the chat model alone: any preset can upload and ask, one with no embedding model too,
and no demo code is needed.

Each session may make `DEMO_SESSION_MAX_EMBED_CALLS` embedding calls (default 200) in
each `DEMO_UPLOAD_TTL_MINUTES` window; the call past it is refused before it is sent, with
a plain message, until the window ends. The count is held in memory, per process.
"""

import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager

from app.credentials import demo_gateway_url
from app.ingest import Embedder
from app.llm import EmbedCapError, LLMClient, LLMConfig, LLMConfigError

DEFAULT_SESSION_MAX_EMBED_CALLS = 200
KEY_SETTING = "DEMO_EMBEDDING_KEY"


class EmbedBudget:
    """How many embedding calls each session has made in its current window."""

    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ):
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        # session -> (start of its window, calls made in it)
        self._spent: dict[str, tuple[float, int]] = {}

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, window_seconds: float = 3600.0
    ) -> "EmbedBudget":
        env = os.environ if env is None else env
        raw = env.get("DEMO_SESSION_MAX_EMBED_CALLS", "").strip()
        if not raw:
            return cls(DEFAULT_SESSION_MAX_EMBED_CALLS, window_seconds)
        if not raw.isdigit() or int(raw) < 1:
            raise ValueError(
                f"DEMO_SESSION_MAX_EMBED_CALLS must be a whole number above 0, got {raw!r}"
            )
        return cls(int(raw), window_seconds)

    def spend(self, session: str) -> None:
        """Count one call for `session`, or raise `EmbedCapError` when it has none left."""
        now = self._clock()
        with self._lock:
            # Forget every window that has ended, so the map holds recent sessions only.
            self._spent = {s: v for s, v in self._spent.items() if now - v[0] < self.window_seconds}
            start, calls = self._spent.get(session, (now, 0))
            if calls >= self.limit:
                minutes = round(self.window_seconds / 60)
                raise EmbedCapError(
                    f"This session has reached its limit of {self.limit} embedding calls in "
                    f"{minutes} minutes; try again later"
                )
            self._spent[session] = (start, calls + 1)

    def tracked(self) -> int:
        """How many sessions are counted now."""
        with self._lock:
            return len(self._spent)


class _CappedEmbedder:
    def __init__(self, inner: Embedder, budget: EmbedBudget, session: str):
        self.inner = inner
        self.budget = budget
        self.session = session

    def embed(self, texts: list[str]) -> list[list[float]]:
        if texts:
            self.budget.spend(self.session)
        return self.inner.embed(texts)


class ServerEmbedding:
    """The demo's one embedder. `embedder`, when given, replaces the gateway (for tests)."""

    def __init__(self, model: str, budget: EmbedBudget, embedder: Embedder | None = None):
        self.model = model
        self.budget = budget
        self._injected = embedder

    @contextmanager
    def embedder(self, session: str) -> Iterator[Embedder]:
        """An embedder whose every call counts against `session`'s cap. A missing
        `DEMO_GATEWAY_URL` or `DEMO_EMBEDDING_KEY` raises `LLMConfigError`."""
        if self._injected is not None:
            yield _CappedEmbedder(self._injected, self.budget, session)
            return
        client = LLMClient(self._config())
        try:
            yield _CappedEmbedder(client, self.budget, session)
        finally:
            client.close()

    def _config(self) -> LLMConfig:
        base_url = demo_gateway_url()
        if base_url is None:
            raise LLMConfigError("DEMO_GATEWAY_URL", "DEMO_GATEWAY_URL is not set")
        key = os.environ.get(KEY_SETTING, "").strip()
        if not key:
            raise LLMConfigError(KEY_SETTING, f"{KEY_SETTING} is not set")
        # The chat models are never called through this client.
        return LLMConfig(
            base_url=base_url,
            api_key=key,
            fast_model=self.model,
            reasoning_model=self.model,
            embedding_model=self.model,
        )

"""The parts of deep mode that are not the answer itself: its settings, its plan and its limits.

In deep mode the reasoning model first plans the question as sub-questions, at most
`DEEP_MAX_SUBQUESTIONS` (default 5); each is retrieved on its own, and the answer is then
written from all their passages (`app.answer`). A run has two limits:

- time: `DEEP_TIMEOUT_SECONDS` (default 300) for the whole run, checked before and after
  each step and on each streamed piece of the answer, on top of each call's own
  `LLM_TIMEOUT_SECONDS`;
- tokens: `DEEP_MAX_TOKENS` (default 50000) over the plan and the answer together, as the
  endpoint reports them, or estimated at four characters a token when it reports none.

Going over either raises, and the run ends in an error, never a partial answer.
"""

import logging
import math
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.llm import ChatResult, LLMClient, LLMResponseError

DEFAULT_DEEP_MAX_SUBQUESTIONS = 5
DEFAULT_DEEP_MAX_TOKENS = 50_000
DEFAULT_DEEP_TIMEOUT_SECONDS = 300.0

# Characters per token when the endpoint reports no usage: a rough rate for English text.
CHARS_PER_TOKEN = 4

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"sub_questions": {"type": "array", "items": {"type": "string"}}},
    "required": ["sub_questions"],
    "additionalProperties": False,
}

PLAN_PROMPT = """\
You plan research in the user's documents. Split the user's question into at most \
{limit} sub-questions. Each sub-question is searched for on its own, so make each one \
self-contained: name what it is about instead of saying "it" or "they". A question \
that compares several things gets one sub-question per thing. A simple question needs \
only one sub-question.

Any messages between these rules and the question are the conversation so far. Use them \
only to understand the question.

Reply with the sub-questions only; do not answer them."""

log = logging.getLogger(__name__)


class DeepConfigError(Exception):
    """A deep-mode setting is not valid; `setting` names the variable."""

    def __init__(self, setting: str, message: str):
        self.setting = setting
        super().__init__(message)


class DeepBudgetError(Exception):
    """The run used more tokens than `DEEP_MAX_TOKENS`."""

    def __init__(self, limit: int, spent: int):
        self.limit = limit
        self.spent = spent
        super().__init__(f"deep run used {spent} tokens, over DEEP_MAX_TOKENS ({limit})")


class DeepTimeoutError(Exception):
    """The run took longer than `DEEP_TIMEOUT_SECONDS`."""

    def __init__(self, limit: float):
        self.limit = limit
        super().__init__(f"deep run took longer than DEEP_TIMEOUT_SECONDS ({limit:g} s)")


class PlanFormatError(LLMResponseError):
    """The model's plan does not match `PLAN_SCHEMA`."""


@dataclass(frozen=True)
class DeepSettings:
    max_subquestions: int = DEFAULT_DEEP_MAX_SUBQUESTIONS
    max_tokens: int = DEFAULT_DEEP_MAX_TOKENS
    timeout: float = DEFAULT_DEEP_TIMEOUT_SECONDS


def deep_settings(env: Mapping[str, str] | None = None) -> DeepSettings:
    """The deep-mode settings from `DEEP_*`, each one's default when unset."""
    env = os.environ if env is None else env
    return DeepSettings(
        max_subquestions=_whole(env, "DEEP_MAX_SUBQUESTIONS", DEFAULT_DEEP_MAX_SUBQUESTIONS),
        max_tokens=_whole(env, "DEEP_MAX_TOKENS", DEFAULT_DEEP_MAX_TOKENS),
        timeout=_seconds(env, "DEEP_TIMEOUT_SECONDS", DEFAULT_DEEP_TIMEOUT_SECONDS),
    )


def _whole(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    if not raw.isdigit() or int(raw) < 1:
        raise DeepConfigError(name, f"{name} must be a whole number of 1 or more, got {raw!r}")
    return int(raw)


def _seconds(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        raise DeepConfigError(name, f"{name} must be a positive number of seconds, got {raw!r}")
    return value


class RunLimits:
    """The time and tokens one deep run has left. The clock starts when it is made."""

    def __init__(self, settings: DeepSettings, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self._clock = clock
        self._deadline = clock() + settings.timeout
        self.spent = 0

    def check_time(self) -> None:
        """Raise `DeepTimeoutError` once the run is past its deadline."""
        if self._clock() > self._deadline:
            raise DeepTimeoutError(self.settings.timeout)

    def spend(self, messages: Iterable[Mapping[str, Any]], result: ChatResult) -> None:
        """Count one call's tokens; raise `DeepBudgetError` when the run is over budget."""
        if result.usage is not None:
            tokens = result.usage.total_tokens
        else:
            chars = sum(len(str(m.get("content", ""))) for m in messages) + len(result.text)
            tokens = math.ceil(chars / CHARS_PER_TOKEN)
        self.spent += tokens
        if self.spent > self.settings.max_tokens:
            raise DeepBudgetError(self.settings.max_tokens, self.spent)


def plan(
    llm: LLMClient,
    question: str,
    history: list[dict[str, str]],
    limits: RunLimits,
) -> tuple[list[str], ChatResult]:
    """The sub-questions the reasoning model plans for `question`, at most
    `max_subquestions`, blank and repeated ones dropped; the question itself when the
    plan has none. `history` is the earlier turns as chat messages."""
    limit = limits.settings.max_subquestions
    messages = [
        {"role": "system", "content": PLAN_PROMPT.format(limit=limit)},
        *history,
        {"role": "user", "content": question},
    ]
    limits.check_time()
    result = llm.chat(messages, model="reasoning", json_schema=PLAN_SCHEMA)
    limits.spend(messages, result)
    limits.check_time()

    raw = result.data.get("sub_questions") if isinstance(result.data, dict) else None
    if not isinstance(raw, list) or not all(isinstance(q, str) for q in raw):
        raise PlanFormatError("model plan has no list of sub-question strings")
    sub_questions: list[str] = []
    for q in raw:
        q = q.strip()
        if q and q not in sub_questions:
            sub_questions.append(q)
    if len(sub_questions) > limit:
        log.info("plan cut from %d to %d sub-questions", len(sub_questions), limit)
        sub_questions = sub_questions[:limit]
    return sub_questions or [question], result

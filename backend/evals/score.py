"""Score the answers to the eval questions.

- Answer accuracy: of the answerable questions, the share whose answer (the text of its
  claims, when it did not end in an error) holds every key fact, compared as `evals.questions.normalize` folds them. With a
  `Judge`, the model judge decides instead; the key-fact check is still recorded.
- Citation precision: of the claims shown for answerable questions, the share citing the
  question's PDF on its expected page.
- Refusal accuracy: of all questions, the share refused exactly when they are
  unanswerable. An answer that failed is not a refusal.
- Cost: the sum of the answers' costs, or `None` (unknown) when any answer that reached
  the model has none (no prices, or no usage reported); never a stand-in 0. A question
  that never reached the model costs 0 (`evals.run.ask_all` records that).
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.llm import LLMResponseError
from evals.questions import Question, normalize


@dataclass(frozen=True)
class Claim:
    text: str
    pdf: str | None
    """The cited document's file name."""
    page: int


@dataclass(frozen=True)
class Outcome:
    """What the chat API did with one question."""

    question_id: str
    claims: tuple[Claim, ...]
    refused: bool
    error: str | None
    cost: float | None

    @property
    def answer(self) -> str:
        return "\n".join(c.text for c in self.claims)


@dataclass(frozen=True)
class Ratio:
    hits: int
    total: int

    @property
    def value(self) -> float | None:
        """None when there is nothing to count."""
        return self.hits / self.total if self.total else None

    def text(self) -> str:
        if self.value is None:
            return "n/a"
        return f"{self.value:.3f} ({self.hits}/{self.total})"

    def as_json(self) -> dict[str, Any]:
        return {"score": self.value, "hits": self.hits, "total": self.total}


@dataclass(frozen=True)
class Scores:
    accuracy: Ratio
    citation_precision: Ratio
    refusal_accuracy: Ratio
    cost: float | None
    errors: int
    results: list[dict[str, Any]] = field(default_factory=list)
    """One entry per question, in question order."""

    def as_json(self) -> dict[str, Any]:
        return {
            "answer_accuracy": self.accuracy.as_json(),
            "citation_precision": self.citation_precision.as_json(),
            "refusal_accuracy": self.refusal_accuracy.as_json(),
            "cost_usd": self.cost,
            "errors": self.errors,
        }


def key_facts_present(q: Question, answer: str) -> bool:
    text = normalize(answer)
    return all(normalize(fact) in text for fact in q.key_facts)


def score(
    questions: Iterable[Question],
    outcomes: Iterable[Outcome],
    judge: Callable[[Question, str], bool] | None = None,
) -> Scores:
    """Score every question by its outcome; each question must have one."""
    by_id = {o.question_id: o for o in outcomes}
    questions = list(questions)
    missing = [q.id for q in questions if q.id not in by_id]
    if missing:
        raise ValueError(f"no outcome for {', '.join(missing)}")

    accurate = answerable = cited_right = cited = refusals_right = errors = 0
    costs: list[float | None] = []
    results = []
    for q in questions:
        o = by_id[q.id]
        costs.append(o.cost)
        errors += o.error is not None
        refusal_correct = o.refused == (not q.answerable)
        refusals_right += refusal_correct
        result: dict[str, Any] = {
            "id": q.id,
            "answerable": q.answerable,
            "refused": o.refused,
            "error": o.error,
            "refusal_correct": refusal_correct,
            "correct": None,
            "key_facts_present": None,
            "cited": [{"pdf": c.pdf, "page": c.page} for c in o.claims],
            "cost": o.cost,
        }
        if q.answerable:
            answerable += 1
            # An answer that ended in an error is partial: never counted correct.
            whole = bool(o.claims) and o.error is None
            present = whole and key_facts_present(q, o.answer)
            correct = present
            if judge is not None and whole:
                correct = judge(q, o.answer)
            accurate += correct
            cited += len(o.claims)
            cited_right += sum(c.pdf == q.pdf and c.page == q.expected_page for c in o.claims)
            result |= {"correct": correct, "key_facts_present": present}
        results.append(result)

    return Scores(
        accuracy=Ratio(accurate, answerable),
        citation_precision=Ratio(cited_right, cited),
        refusal_accuracy=Ratio(refusals_right, len(questions)),
        cost=_total(costs),
        errors=errors,
        results=results,
    )


def _total(costs: list[float | None]) -> float | None:
    return None if None in costs else sum(costs)  # type: ignore[arg-type]  # no None left


JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"correct": {"type": "boolean"}},
    "required": ["correct"],
    "additionalProperties": False,
}

JUDGE_PROMPT = """\
You grade an answer to a question about a document. The answer is correct when it states \
every expected key fact, in any wording, and says nothing that contradicts them. Reply \
with {"correct": true} or {"correct": false}."""


class Judge:
    """A model that decides whether an answer is correct, for full runs outside CI.

    `llm` is an `app.llm.LLMClient` (anything with its `chat`). `cost` is the judge's own
    spend, kept apart from the answers' cost.
    """

    def __init__(self, llm: Any):
        self.llm = llm
        self._costs: list[float | None] = []

    def __call__(self, q: Question, answer: str) -> bool:
        facts = "\n".join(f"- {fact}" for fact in q.key_facts)
        user = f"Question: {q.question}\n\nExpected key facts:\n{facts}\n\nAnswer:\n{answer}"
        result = self.llm.chat(
            [{"role": "system", "content": JUDGE_PROMPT}, {"role": "user", "content": user}],
            json_schema=JUDGE_SCHEMA,
        )
        self._costs.append(result.cost)
        correct = result.data.get("correct") if isinstance(result.data, dict) else None
        if not isinstance(correct, bool):
            raise LLMResponseError("the judge's reply has no true or false verdict")
        return correct

    @property
    def cost(self) -> float | None:
        return _total(self._costs)

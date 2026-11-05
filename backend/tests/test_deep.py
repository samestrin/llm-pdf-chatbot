import json
import re
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from test_answer import claim, post_chat, stream_json, types
from test_llm import USAGE, FakeServer, chat_reply, make_client
from test_retrieval import PAPERS, TopicEmbedder, add_document

from app.db import documents, init_db
from app.deep import (
    DEFAULT_DEEP_MAX_SUBQUESTIONS,
    DEFAULT_DEEP_MAX_TOKENS,
    DEFAULT_DEEP_TIMEOUT_SECONDS,
    PLAN_SCHEMA,
    DeepConfigError,
    deep_settings,
)
from app.llm import LLMHTTPError
from app.main import create_app

REPORT_2023 = "In 2023 the glacier retreated forty metres from the lake shore."
REPORT_2024 = "In 2024 the glacier retreated twelve metres from the lake shore."
QUESTION = "How does the glacier's retreat in 2023 compare with 2024?"
SUB_QUESTIONS = [
    "How far did the glacier retreat in 2023?",
    "How far did the glacier retreat in 2024?",
]
# Each fake call reports USAGE: 17 tokens.
CALL_TOKENS = USAGE["total_tokens"]


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


@pytest.fixture
def engine(postgres_engine):
    init_db(postgres_engine)
    return postgres_engine


@pytest.fixture(autouse=True)
def default_settings(monkeypatch):
    for name in (
        "DEEP_MAX_SUBQUESTIONS",
        "DEEP_MAX_TOKENS",
        "DEEP_TIMEOUT_SECONDS",
        "REFUSAL_MIN_SIMILARITY",
        "REFUSAL_MIN_KEYWORD_COVERAGE",
        "CHAT_HISTORY_TURNS",
        "RERANK_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def two_reports(engine) -> tuple[str, str]:
    """A collection of two documents that answer one comparison between them."""
    ids = (
        add_document(engine, "papers", [REPORT_2023]),
        add_document(engine, "papers", [REPORT_2024]),
    )
    with engine.begin() as conn:
        for doc_id, name in zip(ids, ("report-2023.pdf", "report-2024.pdf"), strict=True):
            conn.execute(update(documents).where(documents.c.id == doc_id).values(filename=name))
    return ids


def chunk_id_of(request: dict, text: str) -> str:
    user = request["messages"][-1]["content"]
    return re.search(r"chunk_id=(\d+)>>>\n" + re.escape(text), user).group(1)


def compare_both(request: dict) -> dict:
    """A comparison that cites the passage of each report it was given."""
    return {
        "claims": [
            claim(
                "In 2023 it retreated forty metres.", chunk_id_of(request, REPORT_2023), REPORT_2023
            ),
            claim(
                "In 2024 it retreated twelve metres.",
                chunk_id_of(request, REPORT_2024),
                REPORT_2024,
            ),
        ]
    }


def is_plan(request: dict) -> bool:
    return not request.get("stream")


def deep_model(sub_questions=SUB_QUESTIONS, answer=compare_both, usage=USAGE, stream=None):
    """A fake reasoning model: a plan for the non-streamed call, `answer` for the stream."""

    def respond(request):
        if is_plan(request):
            plan = json.dumps({"sub_questions": sub_questions})
            return 200, json.dumps(chat_reply(plan, usage=usage)).encode(), "application/json"
        body = stream(request) if stream else b"".join(stream_json(answer(request)))
        return 200, body, "text/event-stream"

    return respond


def deep(engine, server, question=QUESTION, embedder=None, **body):
    llm = make_client(server)
    app = create_app(engine, embedder=embedder or TopicEmbedder(), llm=llm)
    try:
        with TestClient(app) as client:
            return post_chat(client, {"question": question, "mode": "deep", **body})
    finally:
        llm.close()


# --- The two-document comparison ---


def test_deep_mode_cites_both_documents_of_a_comparison(engine, server):
    doc_2023, doc_2024 = two_reports(engine)
    server.responder = deep_model()

    resp, events = deep(engine, server)

    assert resp.status_code == 200
    assert types(events) == ["step", "step", "claim", "claim", "done"]
    cited = {e["document"]["id"] for e in events if e["type"] == "claim"}
    assert cited == {doc_2023, doc_2024}
    assert [e["document"]["filename"] for e in events if e["type"] == "claim"] == [
        "report-2023.pdf",
        "report-2024.pdf",
    ]
    assert events[2]["quote"] == REPORT_2023
    assert events[-1] == {"type": "done", "mode": "deep", "model": "big", "cost": None}


def test_each_step_is_a_sub_question_and_the_pages_it_retrieved(engine, server):
    doc_2023, doc_2024 = two_reports(engine)
    server.responder = deep_model()

    _, events = deep(engine, server)

    steps = [e for e in events if e["type"] == "step"]
    assert [s["question"] for s in steps] == SUB_QUESTIONS
    for step in steps:
        pages = {(p["document"]["id"], p["page"]) for p in step["pages"]}
        assert pages == {(doc_2023, 1), (doc_2024, 1)}
        filenames = {p["document"]["filename"] for p in step["pages"]}
        assert filenames == {"report-2023.pdf", "report-2024.pdf"}


def test_both_calls_ask_the_reasoning_model(engine, server):
    two_reports(engine)
    server.responder = deep_model()

    deep(engine, server)

    plan, answer = [r["json"] for r in server.requests]
    assert plan["model"] == answer["model"] == "big"
    assert plan["response_format"]["json_schema"]["schema"] == PLAN_SCHEMA
    assert QUESTION in plan["messages"][-1]["content"]
    assert answer["stream"] is True
    assert answer["messages"][-1]["content"].endswith(f"Question: {QUESTION}")


def test_the_answer_gets_the_passages_of_every_sub_question_once(engine, server):
    two_reports(engine)
    server.responder = deep_model()

    deep(engine, server)

    user = server.requests[1]["json"]["messages"][-1]["content"]
    assert user.count(REPORT_2023) == 1
    assert user.count(REPORT_2024) == 1


def test_steps_come_before_the_claims(engine, server):
    two_reports(engine)
    server.responder = deep_model()

    _, events = deep(engine, server)

    kinds = types(events)
    assert max(i for i, k in enumerate(kinds) if k == "step") < kinds.index("claim")


# --- The sub-question cap ---


def test_the_sub_question_cap_has_a_committed_default_of_five():
    assert DEFAULT_DEEP_MAX_SUBQUESTIONS == 5
    assert deep_settings({}).max_subquestions == 5


def test_no_more_than_deep_max_subquestions_are_retrieved(engine, server, monkeypatch):
    two_reports(engine)
    monkeypatch.setenv("DEEP_MAX_SUBQUESTIONS", "2")
    server.responder = deep_model(sub_questions=[f"Glacier question {i}?" for i in range(7)])

    _, events = deep(engine, server)

    steps = [e["question"] for e in events if e["type"] == "step"]
    assert steps == ["Glacier question 0?", "Glacier question 1?"]
    assert "at most 2" in server.requests[0]["json"]["messages"][0]["content"]


def test_the_default_cap_keeps_five_of_seven_sub_questions(engine, server):
    two_reports(engine)
    server.responder = deep_model(sub_questions=[f"Glacier question {i}?" for i in range(7)])

    _, events = deep(engine, server)

    assert types(events).count("step") == 5


def test_blank_and_repeated_sub_questions_are_dropped(engine, server):
    two_reports(engine)
    first, second = SUB_QUESTIONS
    server.responder = deep_model(sub_questions=[first, "  ", first, second])

    _, events = deep(engine, server)

    assert [e["question"] for e in events if e["type"] == "step"] == SUB_QUESTIONS


def test_a_plan_without_sub_questions_retrieves_for_the_question_itself(engine, server):
    two_reports(engine)
    server.responder = deep_model(sub_questions=[])

    _, events = deep(engine, server)

    assert [e["question"] for e in events if e["type"] == "step"] == [QUESTION]
    assert types(events)[-1] == "done"


@pytest.mark.parametrize(
    "plan",
    [{"steps": ["a"]}, {"sub_questions": "a"}, {"sub_questions": [1, 2]}, ["a"]],
    ids=["no list", "a string", "not strings", "not an object"],
)
def test_a_plan_outside_its_schema_ends_in_an_error(engine, server, plan):
    two_reports(engine)
    server.reply(chat_reply(json.dumps(plan), usage=USAGE))

    _, events = deep(engine, server)

    assert types(events) == ["error"]
    assert events[0]["message"] == "the answer failed: the model's plan does not match its format"
    assert len(server.requests) == 1


# --- A failure ends in an error, never a partial answer ---


class FailingEmbedder(TopicEmbedder):
    """Embeds as `TopicEmbedder`, but the embedding endpoint fails on one text."""

    def __init__(self, failing: str):
        super().__init__()
        self.failing = failing

    def embed(self, texts):
        if self.failing in texts:
            raise LLMHTTPError(503, "embedding endpoint overloaded")
        return super().embed(texts)


def test_a_failed_sub_question_ends_in_an_error_with_no_claims(engine, server):
    two_reports(engine)
    server.responder = deep_model()

    _, events = deep(engine, server, embedder=FailingEmbedder(SUB_QUESTIONS[1]))

    assert types(events) == ["step", "error"]
    assert "claim" not in types(events)
    assert events[-1]["message"] == "the answer failed: the model endpoint returned HTTP 503"
    assert len(server.requests) == 1, "the answer is not asked for after a failed sub-question"


def test_a_failed_plan_ends_in_an_error(engine, server):
    two_reports(engine)
    server.reply({"error": "down"}, status=500)

    _, events = deep(engine, server)

    assert types(events) == ["error"]
    assert events[0]["message"] == "the answer failed: the model endpoint returned HTTP 500"


def test_a_cut_off_answer_stream_ends_in_an_error_with_no_claims(engine, server):
    two_reports(engine)
    # The claims arrive whole, then the stream stops before its end marker.
    server.responder = deep_model(
        stream=lambda request: b"".join(stream_json(compare_both(request), done=False))
    )

    _, events = deep(engine, server)

    assert types(events) == ["step", "step", "error"]
    assert events[-1]["message"] == "the answer failed: the model stream failed"


def test_an_answer_cut_off_at_the_token_limit_ends_in_an_error_with_no_claims(engine, server):
    two_reports(engine)
    server.responder = deep_model(
        stream=lambda request: b"".join(stream_json(compare_both(request), finish_reason="length"))
    )

    _, events = deep(engine, server)

    assert types(events) == ["step", "step", "error"]


@pytest.mark.parametrize("reply", [{"claims": None}, {"claims": "x"}, ["x"]])
def test_an_answer_without_a_list_of_claims_ends_in_an_answer_format_error(engine, server, reply):
    two_reports(engine)
    server.responder = deep_model(answer=lambda request: reply)

    _, events = deep(engine, server)

    assert types(events) == ["step", "step", "error"]
    assert events[-1]["message"] == (
        "the answer failed: the model reply does not match the answer format"
    )


def test_an_answer_outside_the_schema_ends_in_an_error_with_no_claims(engine, server):
    two_reports(engine)

    def one_good_then_bad(request):
        good = compare_both(request)["claims"][0]
        return {"claims": [good, {"text": "no quote"}]}

    server.responder = deep_model(answer=one_good_then_bad)

    _, events = deep(engine, server)

    assert types(events) == ["step", "step", "error"]


def test_an_answer_whose_claims_all_fail_the_citation_check_is_a_refusal(engine, server):
    two_reports(engine)
    server.responder = deep_model(
        answer=lambda request: {
            "claims": [
                claim("Made up.", chunk_id_of(request, REPORT_2023), "not in the chunk at all")
            ]
        }
    )

    _, events = deep(engine, server)

    assert types(events) == ["step", "step", "refusal", "done"]
    assert {p["text"] for p in events[2]["passages"]} == {REPORT_2023, REPORT_2024}


# --- The token budget ---


def test_the_token_budget_and_timeout_have_committed_defaults():
    settings = deep_settings({})
    assert settings.max_tokens == DEFAULT_DEEP_MAX_TOKENS > 0
    assert settings.timeout == DEFAULT_DEEP_TIMEOUT_SECONDS > 0


def test_a_run_over_the_token_budget_ends_in_an_error_with_no_claims(engine, server, monkeypatch):
    two_reports(engine)
    # The plan fits (17 tokens); the plan and the answer together (34) do not.
    monkeypatch.setenv("DEEP_MAX_TOKENS", str(2 * CALL_TOKENS - 1))
    server.responder = deep_model()

    _, events = deep(engine, server)

    assert types(events) == ["step", "step", "error"]
    assert events[-1]["message"] == (
        f"the answer failed: the deep run used more than DEEP_MAX_TOKENS ({2 * CALL_TOKENS - 1}) "
        "tokens"
    )


def test_a_run_exactly_at_the_token_budget_is_answered(engine, server, monkeypatch):
    two_reports(engine)
    monkeypatch.setenv("DEEP_MAX_TOKENS", str(2 * CALL_TOKENS))
    server.responder = deep_model()

    _, events = deep(engine, server)

    assert types(events)[-1] == "done"


def test_a_plan_over_the_token_budget_ends_before_any_retrieval(engine, server, monkeypatch):
    two_reports(engine)
    monkeypatch.setenv("DEEP_MAX_TOKENS", str(CALL_TOKENS - 1))
    server.responder = deep_model()
    embedder = TopicEmbedder()

    _, events = deep(engine, server, embedder=embedder)

    assert types(events) == ["error"]
    assert embedder.calls == []
    assert len(server.requests) == 1


def test_without_reported_usage_the_tokens_are_estimated_from_the_text(engine, server, monkeypatch):
    two_reports(engine)
    monkeypatch.setenv("DEEP_MAX_TOKENS", "10")
    server.responder = deep_model(usage=None)

    _, events = deep(engine, server)

    assert types(events) == ["error"], "a plan call with no usage still counts against the budget"
    assert "DEEP_MAX_TOKENS" in events[0]["message"]


# --- The per-run timeout ---


def test_a_run_over_its_timeout_ends_in_an_error_with_no_claims(engine, server, monkeypatch):
    two_reports(engine)
    monkeypatch.setenv("DEEP_TIMEOUT_SECONDS", "0.3")
    server.responder = deep_model()
    server.delay = 0.2  # Each call: the plan fits, the plan and the answer do not.

    _, events = deep(engine, server)

    assert "claim" not in types(events)
    assert types(events)[-1] == "error"
    assert events[-1]["message"] == (
        "the answer failed: the deep run took longer than DEEP_TIMEOUT_SECONDS (0.3 s)"
    )


def test_a_stalling_answer_stream_is_cut_at_the_run_timeout(engine, server, monkeypatch):
    two_reports(engine)
    monkeypatch.setenv("DEEP_TIMEOUT_SECONDS", "0.5")
    server.responder = deep_model(stream=lambda request: stream_json(compare_both(request), 200))
    # 200+ pieces 0.05 s apart: the whole stream takes over 10 s.
    server.piece_delay = 0.05

    started = time.monotonic()
    _, events = deep(engine, server)
    took = time.monotonic() - started

    assert types(events)[-1] == "error"
    assert "claim" not in types(events)
    assert "DEEP_TIMEOUT_SECONDS" in events[-1]["message"]
    assert took < 5, f"the stream was read to its end ({took:.1f} s), not cut at the deadline"


# --- Refusal ---


def test_an_off_topic_deep_question_is_refused_without_asking_for_an_answer(engine, server):
    add_document(engine, "papers", PAPERS)
    server.responder = deep_model(sub_questions=["What is the capital of France?"])

    _, events = deep(engine, server, question="What is the capital of France?")

    assert types(events) == ["step", "refusal", "done"]
    assert events[-1] == {"type": "done", "mode": "deep", "model": "big", "cost": None}
    assert len(server.requests) == 1, "only the plan was asked for"


def test_one_on_topic_sub_question_is_enough_to_answer(engine, server):
    two_reports(engine)
    server.responder = deep_model(
        sub_questions=["What is the capital of France?", SUB_QUESTIONS[0]]
    )

    _, events = deep(engine, server)

    assert types(events)[-1] == "done"
    assert "claim" in types(events)


# --- Settings ---


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("DEEP_MAX_SUBQUESTIONS", "0"),
        ("DEEP_MAX_SUBQUESTIONS", "-1"),
        ("DEEP_MAX_SUBQUESTIONS", "two"),
        ("DEEP_MAX_SUBQUESTIONS", "2.5"),
        ("DEEP_MAX_TOKENS", "0"),
        ("DEEP_MAX_TOKENS", "lots"),
        ("DEEP_TIMEOUT_SECONDS", "0"),
        ("DEEP_TIMEOUT_SECONDS", "-5"),
        ("DEEP_TIMEOUT_SECONDS", "nan"),
        ("DEEP_TIMEOUT_SECONDS", "inf"),
    ],
)
def test_a_bad_deep_setting_is_a_config_error_naming_it(name, raw):
    with pytest.raises(DeepConfigError, match=name) as info:
        deep_settings({name: raw})
    assert info.value.setting == name


def test_deep_settings_are_read_from_the_environment():
    settings = deep_settings(
        {"DEEP_MAX_SUBQUESTIONS": "3", "DEEP_MAX_TOKENS": "9000", "DEEP_TIMEOUT_SECONDS": "45.5"}
    )
    assert (settings.max_subquestions, settings.max_tokens, settings.timeout) == (3, 9000, 45.5)


def test_a_bad_deep_setting_ends_in_an_error_naming_it_not_its_value(engine, server, monkeypatch):
    two_reports(engine)
    monkeypatch.setenv("DEEP_MAX_TOKENS", "secret-ish")
    server.responder = deep_model()

    _, events = deep(engine, server)

    assert events == [
        {"type": "error", "message": "the answer failed: DEEP_MAX_TOKENS is missing or invalid"}
    ]
    assert server.requests == []


def test_a_bad_deep_setting_does_not_break_fast_mode(engine, server, monkeypatch):
    add_document(engine, "papers", PAPERS)
    monkeypatch.setenv("DEEP_MAX_TOKENS", "lots")
    server.responder = lambda request: (
        200,
        b"".join(
            stream_json(
                {
                    "claims": [
                        claim(
                            "It retreated.",
                            chunk_id_of(request, PAPERS[1]),
                            "retreated forty metres",
                        )
                    ]
                }
            )
        ),
        "text/event-stream",
    )
    llm = make_client(server)
    with TestClient(create_app(engine, embedder=TopicEmbedder(), llm=llm)) as client:
        _, events = post_chat(client, {"question": "How far did the glacier retreat?"})
    llm.close()

    assert types(events) == ["claim", "done"]


# --- History ---


def test_history_goes_to_the_plan_and_the_answer(engine, server):
    two_reports(engine)
    server.responder = deep_model()
    history = [{"question": "Which glacier?", "answer": "The one by the lake."}]

    deep(engine, server, history=history)

    for request in (r["json"] for r in server.requests):
        contents = [m["content"] for m in request["messages"]]
        assert "Which glacier?" in contents
        assert "The one by the lake." in contents

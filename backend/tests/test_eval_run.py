import json
import re
from dataclasses import replace
from datetime import date

import pymupdf
import pytest
from sqlalchemy import create_engine, func, insert, inspect, select, text

from app.db import chunks, collections, documents, init_db, metadata, pages
from app.llm import ChatResult, LLMResponseError
from evals import run as eval_run
from evals.fake import FakeEmbedder, FakeModel
from evals.questions import Question, load_questions
from evals.run import ask_all, ingest, main, outcome_from_events, results_file, table
from evals.score import Claim, Judge, Outcome, Ratio, score

CONTRACT = "contract-wmf-grant-agreement.pdf"
OTHER = "report-cbo-work-in-2018.pdf"


def answerable(qid="q-1", pdf=CONTRACT, page=1, facts=("90 days",)) -> Question:
    return Question(qid, pdf, f"Question {qid}?", True, page, tuple(facts))


def unanswerable(qid="u-1", pdf=CONTRACT) -> Question:
    return Question(qid, pdf, f"Question {qid}?", False, None, ())


def answered(qid, *claims, cost=None) -> Outcome:
    return Outcome(qid, tuple(claims), refused=False, error=None, cost=cost)


def refused(qid, cost=None) -> Outcome:
    return Outcome(qid, (), refused=True, error=None, cost=cost)


def failed(qid) -> Outcome:
    return Outcome(qid, (), refused=False, error="the answer failed: x", cost=None)


# --- Scoring ---


def test_accuracy_is_the_share_of_answerable_questions_with_every_key_fact():
    qs = [
        answerable("a", facts=("90 days", "unspent")),
        answerable("b", facts=("90 days", "unspent")),
        unanswerable("u"),
    ]
    outcomes = [
        answered("a", Claim("Return UNSPENT funds within 90 days.", CONTRACT, 1)),
        answered("b", Claim("Return unspent funds within ninety days.", CONTRACT, 1)),
        refused("u"),
    ]

    scores = score(qs, outcomes)

    assert scores.accuracy == Ratio(1, 2)
    assert [r["correct"] for r in scores.results] == [True, False, None]


def test_key_facts_are_read_from_every_claim_of_the_answer():
    q = answerable(facts=("90 days", "Grant Term"))
    out = answered(
        "q-1", Claim("Within 90 days", CONTRACT, 1), Claim("of the Grant Term", CONTRACT, 1)
    )

    assert score([q], [out]).accuracy == Ratio(1, 1)


def test_a_refused_or_failed_answerable_question_is_not_accurate():
    qs = [answerable("a"), answerable("b")]

    scores = score(qs, [refused("a"), failed("b")])

    assert scores.accuracy == Ratio(0, 2)
    assert scores.errors == 1


def test_citation_precision_is_the_share_of_claims_on_the_expected_page_of_the_expected_pdf():
    qs = [answerable("a", page=2), answerable("b", page=5)]
    outcomes = [
        answered("a", Claim("x", CONTRACT, 2), Claim("y", CONTRACT, 3)),
        answered("b", Claim("z", OTHER, 5), Claim("w", CONTRACT, 5)),
    ]

    assert score(qs, outcomes).citation_precision == Ratio(2, 4)


def test_citation_precision_does_not_count_claims_on_unanswerable_questions():
    qs = [answerable("a", page=2), unanswerable("u")]
    outcomes = [answered("a", Claim("x", CONTRACT, 2)), answered("u", Claim("y", CONTRACT, 9))]

    assert score(qs, outcomes).citation_precision == Ratio(1, 1)


def test_refusal_accuracy_is_exact_refused_if_and_only_if_unanswerable():
    qs = [answerable("a"), answerable("b"), unanswerable("u"), unanswerable("v"), unanswerable("w")]
    outcomes = [
        answered("a", Claim("90 days", CONTRACT, 1)),
        refused("b"),  # wrong: it could be answered
        refused("u"),
        answered("v", Claim("made up", CONTRACT, 1)),  # wrong: a refusal miss
        failed("w"),  # wrong: an error is not a refusal
    ]

    scores = score(qs, outcomes)

    assert scores.refusal_accuracy == Ratio(2, 5)
    assert [r["refusal_correct"] for r in scores.results] == [True, False, True, False, False]


def test_cost_is_the_sum_of_the_answers_costs():
    qs = [answerable("a"), unanswerable("u")]

    assert score(qs, [answered("a", cost=0.25), refused("u", cost=0.5)]).cost == 0.75


def test_cost_is_unknown_when_any_answer_has_no_cost_never_zero():
    qs = [answerable("a"), unanswerable("u")]

    scores = score(qs, [answered("a", cost=0.25), refused("u", cost=None)])

    assert scores.cost is None
    assert "| Cost | unknown |" in table(scores)


def test_a_question_without_an_outcome_is_an_error_not_a_skip():
    with pytest.raises(ValueError, match="b"):
        score([answerable("a"), answerable("b")], [answered("a")])


def test_a_ratio_with_nothing_to_count_has_no_score():
    assert Ratio(0, 0).value is None
    assert Ratio(1, 4).value == 0.25
    assert Ratio(0, 0).text() == "n/a"
    assert Ratio(2, 3).text() == "0.667 (2/3)"


def test_the_table_has_a_row_per_measure():
    qs = [answerable("a"), unanswerable("u")]
    scores = score(qs, [answered("a", Claim("90 days", CONTRACT, 1), cost=0.0123), refused("u")])

    rows = table(scores).splitlines()

    assert rows[0] == "| Measure | Score |"
    assert "| Answer accuracy | 1.000 (1/1) |" in rows
    assert "| Citation precision | 1.000 (1/1) |" in rows
    assert "| Refusal accuracy | 1.000 (2/2) |" in rows
    assert "| Cost | unknown |" in rows
    assert "| Errors | 0 |" in rows


# --- The judge ---


class StubChat:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, model="fast", json_schema=None):
        self.calls.append({"messages": messages, "model": model, "schema": json_schema})
        data, cost = self.replies.pop(0)
        return ChatResult(json.dumps(data), "judge-model", None, cost, data=data)


def test_without_a_judge_no_model_is_asked_and_key_facts_decide():
    q = answerable(facts=("90 days",))

    assert score([q], [answered("q-1", Claim("ninety days", CONTRACT, 1))]).accuracy == Ratio(0, 1)


def test_with_a_judge_the_judge_decides_accuracy():
    stub = StubChat(({"correct": True}, 0.001), ({"correct": False}, 0.002))
    judge = Judge(stub)
    qs = [answerable("a", facts=("90 days",)), answerable("b", facts=("30 days",))]
    outcomes = [
        answered("a", Claim("Ninety days after the term.", CONTRACT, 1)),
        answered("b", Claim("30 days", CONTRACT, 1)),
    ]

    scores = score(qs, outcomes, judge=judge)

    assert scores.accuracy == Ratio(1, 2)
    assert [r["key_facts_present"] for r in scores.results] == [False, True]
    assert judge.cost == pytest.approx(0.003)
    sent = stub.calls[0]["messages"][-1]["content"]
    assert "Question a?" in sent and "90 days" in sent and "Ninety days after the term." in sent
    assert stub.calls[0]["schema"]["required"] == ["correct"]


def test_the_judge_is_not_asked_about_a_refused_answer():
    stub = StubChat()

    scores = score([answerable("a")], [refused("a")], judge=Judge(stub))

    assert scores.accuracy == Ratio(0, 1)
    assert stub.calls == []


def test_a_judge_reply_without_a_verdict_is_an_error():
    judge = Judge(StubChat(({"verdict": "yes"}, None)))

    with pytest.raises(LLMResponseError):
        judge(answerable(), "90 days")


def test_the_judge_cost_is_unknown_when_a_call_has_none():
    judge = Judge(StubChat(({"correct": True}, 0.1), ({"correct": True}, None)))
    judge(answerable(), "a")
    judge(answerable(), "b")

    assert judge.cost is None


# --- Events to outcomes ---


def test_outcome_of_claims_names_each_cited_pdf_and_page_and_the_cost():
    events = [
        {"type": "claim", "text": "A", "document": {"id": "d", "filename": CONTRACT}, "page": 2},
        {"type": "claim", "text": "B", "document": {"id": "e", "filename": OTHER}, "page": 4},
        {"type": "done", "mode": "fast", "model": "m", "cost": 0.5},
    ]

    out = outcome_from_events("q", events)

    assert out == Outcome("q", (Claim("A", CONTRACT, 2), Claim("B", OTHER, 4)), False, None, 0.5)


def test_outcome_of_a_refusal_is_refused():
    events = [{"type": "refusal", "passages": []}, {"type": "done", "cost": None}]

    assert outcome_from_events("q", events) == Outcome("q", (), True, None, None)


def test_outcome_of_an_error_keeps_its_message_and_is_not_refused():
    events = [{"type": "error", "message": "the answer failed: boom"}]

    assert outcome_from_events("q", events) == Outcome(
        "q", (), False, "the answer failed: boom", None
    )


# --- The fake model ---


def test_the_fake_embedder_is_deterministic_and_never_a_zero_vector():
    e = FakeEmbedder()

    a, b, empty = e.embed(["Grant funds", "grant FUNDS", ""])

    assert a == b
    assert any(empty)


# --- Asking the questions (Postgres) ---


class WrongModel(FakeModel):
    """Always answers, with a claim that holds no key fact, cited to the first passage."""

    def answer(self, question, passages):
        chunk_id, passage_text = passages[0]
        return [{"text": "Nothing to see.", "chunk_id": chunk_id, "quote": passage_text[:80]}]


def test_ask_all_ingests_the_pdfs_and_asks_each_question(postgres_engine, monkeypatch):
    monkeypatch.delenv("OCR_URL", raising=False)
    q = Question(
        "c1",
        CONTRACT,
        "Within how many days must unspent grant funds be returned?",
        True,
        1,
        ("90 days",),
    )
    model = FakeModel()

    outcomes = ask_all(postgres_engine, [q], llm=model, embedder=model)

    assert len(outcomes) == 1
    assert outcomes[0].error is None
    assert {c.pdf for c in outcomes[0].claims} <= {CONTRACT}


def test_a_fake_model_that_answers_wrongly_scores_below_one_on_accuracy(
    postgres_engine, monkeypatch
):
    monkeypatch.delenv("OCR_URL", raising=False)
    qs = [q for q in load_questions() if q.pdf == CONTRACT and q.answerable][:3]
    model = WrongModel()

    scores = score(qs, ask_all(postgres_engine, qs, llm=model, embedder=model))

    assert scores.errors == 0
    assert scores.accuracy.total == 3
    assert scores.accuracy.value < 1.0


def test_a_fake_model_that_answers_an_unanswerable_question_scores_a_refusal_miss(
    postgres_engine, monkeypatch
):
    monkeypatch.delenv("OCR_URL", raising=False)
    # Every question reaches the model, so the refusal is the model's to make.
    monkeypatch.setenv("REFUSAL_MIN_SIMILARITY", "0")
    qs = [q for q in load_questions() if q.pdf == CONTRACT and not q.answerable][:1]
    model = WrongModel()

    scores = score(qs, ask_all(postgres_engine, qs, llm=model, embedder=model))

    assert scores.results[0]["refused"] is False
    assert scores.results[0]["refusal_correct"] is False
    assert scores.refusal_accuracy == Ratio(0, 1)


# --- The command ---


def test_results_file_is_named_by_date(tmp_path):
    assert results_file(tmp_path, date(2026, 11, 6)) == tmp_path / "2026-11-06.json"


def test_subset_run_prints_the_table_and_writes_the_dated_results(
    postgres_url, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    monkeypatch.delenv("OCR_URL", raising=False)
    monkeypatch.setattr(eval_run, "today", lambda: date(2026, 11, 6))

    assert main(["--subset", "--out", str(tmp_path)]) == 0

    printed = capsys.readouterr().out
    assert "| Measure | Score |" in printed
    for row in ("Answer accuracy", "Citation precision", "Refusal accuracy", "Cost"):
        assert re.search(rf"^\| {row} \| ", printed, re.MULTILINE), row
    saved = json.loads((tmp_path / "2026-11-06.json").read_text())
    assert saved["date"] == "2026-11-06"
    assert saved["run"] == "subset"
    assert saved["judge"] is False
    assert saved["model"] == "fake-extractive"
    assert "scanned-lbj-desk-diary-1954-10-13.pdf" not in saved["pdfs"]
    subset = [q for q in load_questions() if not q.pdf.startswith("scanned-")]
    assert [r["id"] for r in saved["results"]] == [q.id for q in subset]
    assert set(saved["scores"]) == {
        "answer_accuracy",
        "citation_precision",
        "refusal_accuracy",
        "cost_usd",
        "errors",
    }
    assert saved["scores"]["refusal_accuracy"]["total"] == len(subset)
    assert saved["scores"]["cost_usd"] is None
    assert saved["scores"]["errors"] == 0


def test_the_run_leaves_no_tables_behind(postgres_url, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    monkeypatch.delenv("OCR_URL", raising=False)
    monkeypatch.setattr(eval_run, "load_questions", lambda: load_questions()[:1])
    engine = create_engine(postgres_url)
    with engine.connect() as conn:
        before = set(inspect(conn).get_schema_names())

    assert main(["--subset", "--out", str(tmp_path)]) == 0

    with engine.connect() as conn:
        assert set(inspect(conn).get_schema_names()) == before
        assert conn.execute(text("SELECT to_regclass('public.documents')")).scalar() is None
    engine.dispose()


def test_judge_is_refused_with_the_subset(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["--subset", "--judge", "--out", str(tmp_path)])

    assert exit_.value.code == 2
    assert "--judge" in capsys.readouterr().err


@pytest.mark.parametrize("url", ["", "sqlite:///x.db"])
def test_the_runner_needs_postgres(tmp_path, monkeypatch, capsys, url):
    monkeypatch.setenv("DATABASE_URL", url)

    assert main(["--subset", "--out", str(tmp_path)]) == 2

    assert "DATABASE_URL" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_an_answer_that_ended_in_an_error_is_not_accurate_even_with_the_key_facts():
    stub = StubChat()
    out = Outcome("q-1", (Claim("90 days", CONTRACT, 1),), False, "the answer failed: x", None)

    assert score([answerable()], [out]).accuracy == Ratio(0, 1)
    assert score([answerable()], [out], judge=Judge(stub)).accuracy == Ratio(0, 1)
    assert stub.calls == []


def test_a_pdf_that_does_not_ingest_stops_the_run(postgres_engine, tmp_path, monkeypatch):
    monkeypatch.delenv("OCR_URL", raising=False)
    blank = pymupdf.open()
    blank.new_page()
    blank.save(tmp_path / "blank.pdf")
    init_db(postgres_engine)

    with pytest.raises(RuntimeError, match="blank.pdf did not ingest: no page could be read"):
        ingest(postgres_engine, ["blank.pdf"], tmp_path, FakeEmbedder())


def test_a_full_run_without_model_settings_names_the_setting(
    postgres_url, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    for name in ("LLM_BASE_URL", "LLM_FAST_MODEL", "LLM_REASONING_MODEL", "LLM_EMBEDDING_MODEL"):
        monkeypatch.delenv(name, raising=False)

    assert main(["--out", str(tmp_path)]) == 2

    assert "LLM_BASE_URL" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


# --- Review findings: isolation from the database's own tables, and cost ---


@pytest.fixture
def public_app_tables(postgres_url):
    """The app's tables in `public`, as in the compose database, with one document; dropped after."""
    engine = create_engine(postgres_url)
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(collections).values(name="mine"))
    yield engine
    metadata.drop_all(engine)
    engine.dispose()


def test_a_run_against_a_database_with_the_apps_tables_leaves_them_untouched(
    public_app_tables, postgres_url, tmp_path, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    monkeypatch.delenv("OCR_URL", raising=False)
    monkeypatch.setattr(eval_run, "load_questions", lambda: load_questions()[:1])

    assert main(["--subset", "--out", str(tmp_path)]) == 0

    with public_app_tables.connect() as conn:
        assert conn.execute(select(collections.c.name)).scalars().all() == ["mine"]
        for t in (documents, pages, chunks):
            assert conn.execute(select(func.count()).select_from(t)).scalar() == 0, t.name
    saved = json.loads(next(tmp_path.iterdir()).read_text())
    assert saved["scores"]["errors"] == 0


class PricedModel(FakeModel):
    """The fake model with a price: every call costs 0.01."""

    def _pieces(self, messages):
        for item in super()._pieces(messages):
            yield replace(item, cost=0.01) if isinstance(item, ChatResult) else item


NONSENSE = Question("off", CONTRACT, "Quokka marmalade zeppelin?", False, None, ())


def test_a_question_refused_before_any_model_call_costs_zero_not_unknown(
    postgres_engine, monkeypatch
):
    monkeypatch.delenv("OCR_URL", raising=False)
    monkeypatch.setenv("REFUSAL_MIN_SIMILARITY", "1")
    model = PricedModel()

    [out] = ask_all(postgres_engine, [NONSENSE], llm=model, embedder=model)

    assert out.refused is True
    assert out.cost == 0.0


def test_cost_is_known_when_the_model_is_priced_and_some_questions_never_reach_it(
    postgres_engine, monkeypatch
):
    monkeypatch.delenv("OCR_URL", raising=False)
    monkeypatch.setenv("REFUSAL_MIN_SIMILARITY", "0.99")
    answered_q = Question(
        "c1",
        CONTRACT,
        "Within how many days after the end of the Grant Term must the grantee return "
        "unspent grant funds?",
        True,
        1,
        ("90 days",),
    )
    monkeypatch.setenv("REFUSAL_MIN_KEYWORD_COVERAGE", "0.5")
    model = PricedModel()

    outcomes = ask_all(postgres_engine, [answered_q, NONSENSE], llm=model, embedder=model)
    scores = score([answered_q, NONSENSE], outcomes)

    assert [o.cost for o in outcomes] == [0.01, 0.0]
    assert scores.cost == pytest.approx(0.01)


def test_a_model_call_without_a_price_still_makes_the_cost_unknown(postgres_engine, monkeypatch):
    monkeypatch.delenv("OCR_URL", raising=False)
    monkeypatch.setenv("REFUSAL_MIN_SIMILARITY", "0")
    model = FakeModel()

    [out] = ask_all(postgres_engine, [NONSENSE], llm=model, embedder=model)

    assert out.cost is None

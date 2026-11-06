"""Run the eval: ingest the fixture PDFs, ask every question through the chat API, score
the answers (`evals.score`), write `evals/results/<date>.json` and print the table.

    cd backend && uv run python -m evals.run            # full run: real models, OCR
    cd backend && uv run python -m evals.run --judge    # full run, a model judges accuracy
    cd backend && uv run python -m evals.run --subset   # CI: no scanned PDF, fake model

The full run uses the models and `OCR_URL` from the environment, as the app does; the
scanned PDF fails to ingest without OCR, and the run stops. `--subset` leaves out the PDFs
of a kind in `evals.questions.SKIPPED_KINDS` and answers with `evals.fake.FakeModel`, so it
needs no endpoint and no key. Either way it needs `DATABASE_URL` to point at Postgres with
pgvector: the run creates its tables in a schema of its own, dropped at the end, so it
leaves nothing in the database, even one that already holds the app's tables.

The date is today's in UTC; a second run on one day replaces that day's file. The owner
commits the full run's file.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pymupdf
from sqlalchemy import Engine, create_engine, insert, select, text

from app.answer import chat_events
from app.db import documents, ingestion_status, init_db, metadata
from app.documents import Limits, get_or_create_collection, stored_path
from app.ingest import Embedder, IngestWorker
from app.llm import LLMClient, LLMConfig, LLMConfigError
from evals.fake import FakeModel
from evals.questions import SKIPPED_KINDS, Question, load_questions
from evals.score import Claim, Judge, Outcome, Scores, score
from evals.sources import EVALS, PDF_DIR, SOURCES, read_sources

COLLECTION = "evals"
RESULTS = EVALS / "results"


def today() -> date:
    return datetime.now(UTC).date()


def results_file(out: Path, day: date) -> Path:
    return out / f"{day.isoformat()}.json"


def outcome_from_events(question_id: str, events: Iterable[dict[str, Any]]) -> Outcome:
    """The outcome of one answer's events (`app.answer`): its claims, whether it was
    refused, the error it ended in and its cost."""
    events = list(events)
    claims = tuple(
        Claim(e["text"], (e.get("document") or {}).get("filename"), e["page"])
        for e in events
        if e["type"] == "claim"
    )
    error = next((e["message"] for e in events if e["type"] == "error"), None)
    refused = error is None and not claims and any(e["type"] == "refusal" for e in events)
    cost = next((e.get("cost") for e in events if e["type"] == "done"), None)
    return Outcome(question_id, claims, refused, error, cost)


def ingest(engine: Engine, pdfs: Iterable[str], pdf_dir: Path, embedder: Embedder | None) -> None:
    """Ingest each PDF into `COLLECTION`, as an upload would; raise if one is not ready."""
    with tempfile.TemporaryDirectory() as upload_dir:
        limits = replace(Limits.from_env(), upload_dir=Path(upload_dir))
        worker = IngestWorker(engine, limits, embedder)
        collection_id = get_or_create_collection(engine, COLLECTION)
        for pdf in pdfs:
            document_id = uuid.uuid4().hex
            path = stored_path(limits, document_id)
            shutil.copyfile(pdf_dir / pdf, path)
            with pymupdf.open(path, filetype="pdf") as doc:
                page_count = doc.page_count
            with engine.begin() as conn:
                conn.execute(
                    insert(documents).values(
                        id=document_id,
                        collection_id=collection_id,
                        filename=pdf,
                        size_bytes=path.stat().st_size,
                        page_count=page_count,
                    )
                )
                conn.execute(
                    insert(ingestion_status).values(
                        document_id=document_id, state="queued", pages_total=page_count
                    )
                )
            worker.ingest(document_id)
            with engine.connect() as conn:
                state, error = conn.execute(
                    select(ingestion_status.c.state, ingestion_status.c.error).where(
                        ingestion_status.c.document_id == document_id
                    )
                ).one()
            if state != "ready":
                raise RuntimeError(f"{pdf} did not ingest: {error}")


def ask_all(
    engine: Engine,
    questions: Sequence[Question],
    pdf_dir: Path = PDF_DIR,
    *,
    llm: Any,
    embedder: Embedder | None = None,
) -> list[Outcome]:
    """Ingest the questions' PDFs, then ask each question in fast mode, as the chat route
    does. `llm` is an `app.llm.LLMClient` or a stand-in; it embeds too unless `embedder`
    is given.

    A question that never reached the model (refused at retrieval, or failed before the
    call) cost nothing, so its cost is 0, not unknown."""
    init_db(engine)
    ingest(engine, sorted({q.pdf for q in questions}), pdf_dir, embedder or llm)
    counted = _CountedCalls(llm)
    outcomes = []
    for q in questions:
        before = counted.calls
        events = chat_events(engine, COLLECTION, q.question, llm=counted, embedder=embedder)
        outcome = outcome_from_events(q.id, events)
        if counted.calls == before:
            outcome = replace(outcome, cost=0.0)
        outcomes.append(outcome)
    return outcomes


class _CountedCalls:
    """The model client, counting its chat calls."""

    def __init__(self, llm: Any):
        self._llm = llm
        self.calls = 0

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self._llm.chat(*args, **kwargs)

    def chat_stream(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self._llm.chat_stream(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)


@contextmanager
def scratch_engine(url: str) -> Iterator[Engine]:
    """An engine on a schema of its own in the database at `url`, dropped afterwards."""
    schema = f"eval_{uuid.uuid4().hex}"
    admin = create_engine(url)
    try:
        with admin.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        # `public` stays on the path for the vector type. The tables are created without
        # looking first, so they land in the eval schema even where `public` already has
        # the app's tables, and every unqualified name then finds the eval schema's first.
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema},public"})
        try:
            metadata.create_all(engine, checkfirst=False)
            yield engine
        finally:
            engine.dispose()
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        admin.dispose()


def table(scores: Scores) -> str:
    cost = "unknown" if scores.cost is None else f"${scores.cost:.4f}"
    rows = [
        ("Answer accuracy", scores.accuracy.text()),
        ("Citation precision", scores.citation_precision.text()),
        ("Refusal accuracy", scores.refusal_accuracy.text()),
        ("Cost", cost),
        ("Errors", str(scores.errors)),
    ]
    return "\n".join(
        ["| Measure | Score |", "|---|---|", *(f"| {name} | {value} |" for name, value in rows)]
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--subset",
        action="store_true",
        help="leave out the scanned PDF and answer with the fake model (what CI runs)",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="have the fast model judge answer accuracy (full runs only)",
    )
    parser.add_argument("--out", type=Path, default=RESULTS, help="where the results file goes")
    args = parser.parse_args(argv)
    if args.subset and args.judge:
        parser.error("--judge needs a real model; the subset answers with the fake one")

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url.startswith("postgresql"):
        print(
            f"DATABASE_URL must point at a Postgres database with pgvector (got {url!r})",
            file=sys.stderr,
        )
        return 2

    questions = load_questions()
    if args.subset:
        kinds = {r.file: r.kind for r in read_sources(SOURCES)}
        questions = [q for q in questions if kinds.get(q.pdf) not in SKIPPED_KINDS]
        llm: Any = FakeModel()
        embedder: Embedder | None = llm
    else:
        try:
            llm = LLMClient(LLMConfig.from_env())
        except LLMConfigError as exc:
            print(f"{exc} (a full run needs the model settings)", file=sys.stderr)
            return 2
        embedder = None
    judge = Judge(llm) if args.judge else None
    try:
        with scratch_engine(url) as engine:
            outcomes = ask_all(engine, questions, llm=llm, embedder=embedder)
        scores = score(questions, outcomes, judge=judge)
    finally:
        llm.close()

    day = today()
    pdfs = sorted({q.pdf for q in questions})
    run = "subset" if args.subset else "full"
    results = {
        "date": day.isoformat(),
        "run": run,
        "judge": judge is not None,
        "model": llm.config.fast_model,
        "embedding_model": llm.config.embedding_model,
        "pdfs": pdfs,
        "questions": len(questions),
        "scores": scores.as_json(),
        "judge_cost_usd": judge.cost if judge else None,
        "results": scores.results,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = results_file(args.out, day)
    path.write_text(json.dumps(results, indent=2) + "\n")

    print(f"Eval, {run} run of {day.isoformat()}: {len(questions)} questions on {len(pdfs)} PDFs")
    print(f"Model: {llm.config.fast_model}" + (", judged by the same model" if judge else ""))
    print()
    print(table(scores))
    print()
    print(f"Results: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The sample's suggested questions, in `frontend/public/samples/questions.json`.

The page shows them as chips over the chat box when the collection holds the sample PDF.
Each must be answered from the sample with a citation, here by the eval's fake model.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_answer import post_chat

from app.db import init_db
from app.main import create_app
from evals.fake import FakeEmbedder, FakeModel

SAMPLES = Path(__file__).resolve().parents[2] / "frontend" / "public" / "samples"
QUESTIONS = json.loads((SAMPLES / "questions.json").read_text(encoding="utf-8"))


def test_every_sample_pdf_has_three_to_five_suggested_questions():
    pdfs = sorted(p.name for p in SAMPLES.glob("*.pdf"))
    assert pdfs and sorted(QUESTIONS) == pdfs
    for name, questions in QUESTIONS.items():
        assert 3 <= len(questions) <= 5, name
        assert all(isinstance(q, str) and q.strip() == q and q for q in questions), name
        assert len(set(questions)) == len(questions), name


@pytest.fixture
def sample_app(postgres_engine, tmp_path, monkeypatch):
    """Each sample PDF uploaded into a collection of its own, read and embedded."""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    for name in ("PUBLIC_DEMO", "OCR_URL", "RERANK_URL", "REFUSAL_MIN_SIMILARITY"):
        monkeypatch.delenv(name, raising=False)
    init_db(postgres_engine)
    app = create_app(postgres_engine, embedder=FakeEmbedder(), llm=FakeModel())
    with TestClient(app) as client:
        for name in QUESTIONS:
            resp = client.post(
                f"/collections/{name}/documents",
                files={"file": (name, (SAMPLES / name).read_bytes(), "application/pdf")},
            )
            assert resp.status_code == 201, resp.text
        app.state.worker.join()
        yield client


@pytest.mark.parametrize(("name", "question"), [(n, q) for n, qs in QUESTIONS.items() for q in qs])
def test_each_suggested_question_is_answered_with_a_citation_from_the_sample(
    sample_app, name, question
):
    resp, events = post_chat(sample_app, {"question": question}, collection=name)
    assert resp.status_code == 200
    claims = [e for e in events if e["type"] == "claim"]
    assert claims, events
    assert events[-1]["type"] == "done"
    for claim in claims:
        assert claim["document"]["filename"] == name
        page = sample_app.get(f"/documents/{claim['document']['id']}/pages/{claim['page']}")
        assert claim["quote"] in page.json()["text"]

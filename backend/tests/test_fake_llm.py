"""The fake model of the compose smoke (`scripts/fake-llm/`), against the real client and
answer code, and the smoke's compose override and script."""

import importlib.util
import json
import math
import re
import threading
from pathlib import Path

import httpx
import pytest
import yaml

from app.answer import (
    ANSWER_SCHEMA,
    DEFAULT_REFUSAL_MIN_SIMILARITY,
    _error,
    answer_events,
    build_messages,
)
from app.llm import LLMClient, LLMConfig, LLMConnectionError
from app.retrieval import Passage

REPO = Path(__file__).resolve().parents[2]
FAKE_DIR = REPO / "scripts" / "fake-llm"
SMOKE = REPO / "scripts" / "compose-smoke.sh"
OVERRIDE = REPO / "docker-compose.smoke.yml"
SAMPLE = "frontend/public/samples/sample.pdf"

# The text of the T1 sample PDF, one page each.
PAGES = [
    "The quick brown fox jumps over the lazy dog.",
    "Revenue grew twelve percent in the third quarter.",
]
# The question the smoke asks; it is answered from page 2.
QUESTION = "How much did revenue grow in the third quarter?"


def load_fake():
    spec = importlib.util.spec_from_file_location("fake_llm_server", FAKE_DIR / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def base_url():
    server = load_fake().make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def llm(base_url):
    client = LLMClient(
        LLMConfig(
            base_url=base_url,
            api_key=None,
            fast_model="fake-fast",
            reasoning_model="fake-reasoning",
            embedding_model="fake-embed",
            timeout=5,
        )
    )
    yield client
    client.close()


def passage(chunk_id, page, text):
    return Passage(
        chunk_id=chunk_id,
        document_id="doc",
        page=page,
        start_offset=0,
        end_offset=len(text),
        text=text,
        heading=None,
        similarity=None,
        vector_rank=None,
        keyword_rank=None,
        fusion_score=0.0,
    )


def sample_passages():
    return [passage(11, 1, PAGES[0]), passage(42, 2, PAGES[1])]


def resolve(document_id, page, start, end):
    return PAGES[page - 1][start:end]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_the_answer_is_a_claim_citing_the_sample_page_it_quotes(llm):
    events = list(answer_events(llm, QUESTION, sample_passages(), {"doc": "sample.pdf"}, resolve))
    assert [e["type"] for e in events] == ["claim", "done"]
    claim = events[0]
    assert claim["page"] == 2
    assert claim["chunk_id"] == "42"
    assert claim["quote"] == PAGES[1]
    assert claim["document"] == {"id": "doc", "filename": "sample.pdf"}
    assert events[1]["model"] == "fake-fast"


def test_the_reply_streams_in_more_than_one_piece(llm):
    stream = llm.chat_stream(build_messages(QUESTION, sample_passages()), json_schema=ANSWER_SCHEMA)
    pieces = list(stream)
    assert len(pieces) > 1
    assert stream.result.data["claims"][0]["chunk_id"] == "42"
    assert stream.result.usage is not None


def test_the_quote_is_at_least_fifteen_characters_copied_from_the_passage(llm):
    result = llm.chat(build_messages(QUESTION, sample_passages()), json_schema=ANSWER_SCHEMA)
    [claim] = result.data["claims"]
    assert len(claim["quote"]) >= 15
    assert claim["quote"] in PAGES[1]


def test_a_question_no_passage_answers_gets_no_claims_and_so_a_refusal(llm):
    events = list(
        answer_events(llm, "Who painted the ceiling?", sample_passages(), {"doc": "x"}, resolve)
    )
    assert [e["type"] for e in events] == ["refusal", "done"]


def test_a_matching_sentence_shorter_than_fifteen_characters_is_not_quoted(llm):
    # "Tax rate up." shares two words with the question but is 12 characters: the
    # backend would drop that quote, so the fake must not offer it.
    passages = [passage(7, 1, "Tax rate up. Nothing else happened at the meeting.")]
    result = llm.chat(build_messages("What was the tax rate?", passages), json_schema=ANSWER_SCHEMA)
    assert result.data == {"claims": []}


def test_the_question_embeds_within_the_refusal_threshold_of_its_page(llm):
    question, page1, page2 = llm.embed([QUESTION, *PAGES])
    assert cosine(question, page2) >= DEFAULT_REFUSAL_MIN_SIMILARITY
    assert cosine(question, page2) > cosine(question, page1)


def test_embeddings_are_one_vector_per_input_in_order(llm):
    vectors = llm.embed(["a b c", "revenue", "a b c"])
    assert len(vectors) == 3
    assert vectors[0] == vectors[2] != vectors[1]


def test_the_fake_reports_healthy_and_turns_down_unknown_paths(base_url):
    root = base_url.removesuffix("/v1")
    assert httpx.get(f"{root}/health").status_code == 200
    assert httpx.post(f"{base_url}/nothing", json={}).status_code == 404
    assert httpx.post(f"{base_url}/embeddings", content=b"not json").status_code == 400


# The compose override and the smoke script.


def test_the_override_runs_the_fake_and_points_the_backend_at_it():
    services = yaml.safe_load(OVERRIDE.read_text())["services"]
    fake = services["fake-llm"]
    assert fake["build"] == "./scripts/fake-llm"
    assert "healthcheck" in fake
    backend = services["backend"]
    env = backend["environment"]
    assert env["LLM_BASE_URL"] == "http://fake-llm:8080/v1"
    for name in ("LLM_FAST_MODEL", "LLM_REASONING_MODEL", "LLM_EMBEDDING_MODEL"):
        assert env[name], name
    assert backend["depends_on"]["fake-llm"]["condition"] == "service_healthy"


def test_the_fake_image_runs_the_server():
    dockerfile = (FAKE_DIR / "Dockerfile").read_text()
    assert "COPY server.py" in dockerfile
    assert "server.py" in dockerfile.split("CMD", 1)[1]


def test_the_real_compose_file_has_no_fake():
    assert "fake-llm" not in (REPO / "docker-compose.yml").read_text()


def test_the_smoke_selects_the_override_for_every_compose_call():
    script = SMOKE.read_text()
    assert 'export COMPOSE_FILE="docker-compose.yml:docker-compose.smoke.yml"' in script
    # Set before the cleanup trap, so `docker compose down` uses the same files.
    assert script.index("export COMPOSE_FILE=") < script.index("trap cleanup EXIT")
    assert "docker compose -f" not in script


def test_the_smoke_asks_about_the_sample_and_then_without_the_fake():
    script = SMOKE.read_text()
    assert SAMPLE in script
    assert json.dumps(QUESTION) in script or QUESTION in script
    stop = script.index("docker compose stop fake-llm")
    assert script.index("the model endpoint is unreachable", stop) > stop


def test_the_smoke_expects_the_backends_own_unreachable_message():
    # The smoke matches a pattern, `*"..."*`, in the error message; the backend's real
    # message for an unreachable endpoint must hold it.
    script = SMOKE.read_text()
    stop = script.index("docker compose stop fake-llm")
    [expected] = re.findall(r'\*"([^"]+)"\*\)', script[stop:])
    message = _error(LLMConnectionError("connection refused"))["message"]
    assert expected == "the model endpoint is unreachable"
    assert expected in message

import json
import math
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import create_engine, func, insert, literal_column, select

from app.db import (
    TEXT_SEARCH_CONFIG,
    chunk_embeddings,
    chunk_text_search,
    chunks,
    collections,
    documents,
    ingestion_status,
    init_db,
    pages,
)
from app.llm import LLMHTTPError
from app.retrieval import (
    RRF_K,
    RerankConfig,
    RerankConfigError,
    Reranker,
    RerankError,
    RerankHTTPError,
    fuse,
    retrieve,
)

# Each topic word adds to one dimension; the last dimension is a constant, so no vector
# is all zeros and an off-topic text still has a small similarity to everything. No
# document mentions the topic of dimension 3.
TOPICS = {
    "zebra": 0,
    "savanna": 0,
    "glacier": 1,
    "ice": 1,
    "budget": 2,
    "money": 2,
    "capital": 3,
    "france": 3,
}


def embed_text(text: str) -> list[float]:
    v = [0.0, 0.0, 0.0, 0.0, 0.2]
    for word in re.findall(r"[a-z]+", text.lower()):
        for stem, dim in TOPICS.items():
            if word.startswith(stem):
                v[dim] += 1.0
    return v


class TopicEmbedder:
    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [embed_text(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


ZEBRAS = "Zebras migrate across the savanna each dry season."
GLACIER = "The glacier retreated forty metres last year."
BUDGET = "The budget for the field station was cut in half."
PART = "Replace part XK42 before the next inspection."
STYLE = "Headings follow the house style guide."
PAPERS = [ZEBRAS, GLACIER, BUDGET, PART, STYLE]


@pytest.fixture
def engine(postgres_engine):
    init_db(postgres_engine)
    return postgres_engine


@pytest.fixture
def embedder():
    return TopicEmbedder()


def add_document(
    engine,
    collection: str,
    texts: list[str],
    state: str = "ready",
    vectors: list[list[float]] | None = None,
) -> str:
    """One page and one chunk per text, indexed for keywords and embedded."""
    vectors = vectors or [embed_text(t) for t in texts]
    doc_id = uuid.uuid4().hex
    with engine.begin() as conn:
        collection_id = conn.execute(
            select(collections.c.id).where(collections.c.name == collection)
        ).scalar()
        if collection_id is None:
            collection_id = conn.execute(
                insert(collections).values(name=collection).returning(collections.c.id)
            ).scalar_one()
        conn.execute(
            insert(documents).values(
                id=doc_id,
                collection_id=collection_id,
                filename="doc.pdf",
                size_bytes=1,
                page_count=len(texts),
            )
        )
        conn.execute(
            insert(ingestion_status).values(
                document_id=doc_id, state=state, pages_total=len(texts), pages_read=len(texts)
            )
        )
        for n, (t, v) in enumerate(zip(texts, vectors, strict=True), start=1):
            conn.execute(
                insert(pages).values(document_id=doc_id, page=n, text=t, method="text_layer")
            )
            chunk_id = conn.execute(
                insert(chunks)
                .values(
                    document_id=doc_id, page=n, seq=n, start_offset=0, end_offset=len(t), text=t
                )
                .returning(chunks.c.id)
            ).scalar_one()
            conn.execute(
                insert(chunk_text_search).values(
                    chunk_id=chunk_id,
                    document_id=doc_id,
                    tsv=func.to_tsvector(literal_column(f"'{TEXT_SEARCH_CONFIG}'::regconfig"), t),
                )
            )
            conn.execute(
                insert(chunk_embeddings).values(chunk_id=chunk_id, document_id=doc_id, embedding=v)
            )
    return doc_id


def texts(result) -> list[str]:
    return [p.text for p in result.passages]


# --- Reciprocal rank fusion, on its own ---


def test_fusion_scores_are_the_sum_of_reciprocal_ranks():
    scores = fuse([[10, 20, 30], [30, 40]])

    assert scores[10] == pytest.approx(1 / (RRF_K + 1))
    assert scores[20] == pytest.approx(1 / (RRF_K + 2))
    assert scores[30] == pytest.approx(1 / (RRF_K + 3) + 1 / (RRF_K + 1))
    assert scores[40] == pytest.approx(1 / (RRF_K + 2))


def test_fusion_ranks_a_passage_second_in_both_lists_above_one_first_in_only_one():
    scores = fuse([[1, 2, 3], [4, 2, 5]])

    assert max(scores, key=scores.get) == 2


def test_fusion_of_no_lists_is_empty():
    assert fuse([[], []]) == {}


# --- Hybrid retrieval on Postgres, with known best passages ---


def test_best_passage_found_by_both_searches_comes_first(engine, embedder):
    add_document(engine, "papers", PAPERS)

    result = retrieve(engine, embedder, "papers", "Where do zebras migrate?")

    best = result.passages[0]
    assert best.text == ZEBRAS
    assert best.vector_rank == 1
    assert best.keyword_rank == 1
    assert (best.page, best.start_offset, best.end_offset) == (1, 0, len(ZEBRAS))
    assert result.reranked is False
    assert embedder.calls == [["Where do zebras migrate?"]]


def test_keyword_search_finds_a_passage_the_vectors_cannot_tell_apart(engine, embedder):
    add_document(engine, "papers", PAPERS)

    # "XK42" means nothing to the embedder; only the keyword index knows it.
    result = retrieve(engine, embedder, "papers", "When is XK42 replaced?")

    best = result.passages[0]
    assert best.text == PART
    assert best.keyword_rank == 1


def test_vector_search_finds_a_passage_that_shares_no_word_with_the_question(engine, embedder):
    add_document(engine, "papers", PAPERS)

    result = retrieve(engine, embedder, "papers", "How much money was there?")

    best = result.passages[0]
    assert best.text == BUDGET
    assert best.keyword_rank is None
    assert best.vector_rank == 1


def test_results_are_ordered_by_fusion_score(engine, embedder):
    add_document(engine, "papers", PAPERS)

    result = retrieve(engine, embedder, "papers", "zebras on the glacier")

    scores = [p.fusion_score for p in result.passages]
    assert scores == sorted(scores, reverse=True)
    assert set(texts(result)[:2]) == {ZEBRAS, GLACIER}
    for p in result.passages:
        expected = sum(1 / (RRF_K + r) for r in (p.vector_rank, p.keyword_rank) if r)
        assert p.fusion_score == pytest.approx(expected)


def test_limit_caps_the_passages(engine, embedder):
    add_document(engine, "papers", PAPERS)

    result = retrieve(engine, embedder, "papers", "zebras", limit=2)

    assert len(result.passages) == 2


def test_only_the_named_collection_is_searched(engine, embedder):
    add_document(engine, "papers", PAPERS)
    add_document(engine, "other", ["Zebras zebras zebras on the savanna."])

    result = retrieve(engine, embedder, "papers", "zebras")

    assert "Zebras zebras zebras on the savanna." not in texts(result)
    other = retrieve(engine, embedder, "other", "zebras")
    assert texts(other) == ["Zebras zebras zebras on the savanna."]


@pytest.mark.parametrize("state", ["queued", "processing", "failed"])
def test_documents_not_ready_are_not_searched(engine, embedder, state):
    add_document(engine, "papers", [GLACIER])
    add_document(engine, "papers", ["Zebras zebras zebras on the savanna."], state=state)

    result = retrieve(engine, embedder, "papers", "zebras")

    assert texts(result) == [GLACIER]


def test_unknown_or_empty_collection_finds_nothing(engine, embedder):
    result = retrieve(engine, embedder, "nowhere", "zebras")

    assert result.passages == []
    assert result.top_similarity == 0.0


def test_a_question_of_stop_words_only_still_searches_by_vector(engine, embedder):
    add_document(engine, "papers", PAPERS)

    result = retrieve(engine, embedder, "papers", "what is the")

    assert result.passages
    assert all(p.keyword_rank is None for p in result.passages)


@pytest.mark.parametrize(
    "question", ["it's XK42?", "XK42 & | ! ( ) : * '' \\", "zebras:* | !glacier", "<->"]
)
def test_query_syntax_in_the_question_is_searched_as_plain_words(engine, embedder, question):
    add_document(engine, "papers", PAPERS)

    result = retrieve(engine, embedder, "papers", question)

    if "XK42" in question:
        assert result.passages[0].text == PART


def test_vectors_of_another_dimension_are_ignored(engine, embedder):
    add_document(engine, "papers", PAPERS)
    # Left by an earlier embedding model with a different dimension.
    add_document(engine, "papers", ["Zebras from an old model."], vectors=[[1.0, 0.0]])

    result = retrieve(engine, embedder, "papers", "zebras")

    old = [p for p in result.passages if p.text == "Zebras from an old model."]
    assert old and old[0].vector_rank is None and old[0].similarity is None
    assert result.passages[0].text == ZEBRAS


@pytest.mark.parametrize("question", ["", "   \n"])
def test_an_empty_question_is_rejected(engine, embedder, question):
    with pytest.raises(ValueError):
        retrieve(engine, embedder, "papers", question)
    assert embedder.calls == []


def test_retrieval_needs_postgres(tmp_path, embedder):
    sqlite = create_engine(f"sqlite:///{tmp_path}/test.db")
    init_db(sqlite)

    with pytest.raises(RuntimeError, match="Postgres"):
        retrieve(sqlite, embedder, "papers", "zebras")


# --- Normalised top vector similarity, for the refusal threshold ---


def test_top_similarity_is_the_best_cosine_similarity_in_the_collection(engine, embedder):
    add_document(engine, "papers", PAPERS)
    question = "Where do zebras migrate?"

    result = retrieve(engine, embedder, "papers", question)

    q = embed_text(question)
    best = max(cosine(q, embed_text(t)) for t in PAPERS)
    assert result.top_similarity == pytest.approx(best, abs=1e-5)
    for p in result.passages:
        assert p.similarity == pytest.approx(cosine(q, embed_text(p.text)), abs=1e-5)


def test_an_off_topic_question_has_a_low_top_similarity(engine, embedder):
    add_document(engine, "papers", PAPERS)

    on_topic = retrieve(engine, embedder, "papers", "Where do zebras migrate?")
    off_topic = retrieve(engine, embedder, "papers", "What is the capital of France?")

    assert on_topic.top_similarity > 0.9
    assert off_topic.top_similarity < 0.3


def test_similarity_is_clamped_to_zero_for_opposite_vectors(engine):
    class Fixed:
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    add_document(engine, "papers", ["Opposite."], vectors=[[-1.0, 0.0]])

    result = retrieve(engine, Fixed(), "papers", "opposite")

    assert result.top_similarity == 0.0
    assert result.passages[0].similarity == 0.0


def test_top_similarity_is_not_changed_by_the_rerank(engine, embedder, rerank):
    add_document(engine, "papers", PAPERS)
    rerank.score = lambda doc: 1.0 if doc == STYLE else 0.0
    question = "Where do zebras migrate?"

    plain = retrieve(engine, embedder, "papers", question)
    reranked = retrieve(engine, embedder, "papers", question, reranker=rerank.client())

    assert reranked.passages[0].text == STYLE
    assert reranked.top_similarity == plain.top_similarity


# --- Optional rerank through RERANK_URL ---


class FakeRerank:
    """A rerank endpoint on a real socket, answering in the Cohere/Jina shape."""

    def __init__(self):
        self.score = lambda doc: 0.0
        self.status = 200
        self.body: bytes | None = None  # Set to send this instead of the scores.
        self.delay = 0.0
        self.requests: list[dict] = []
        self.clients: list[Reranker] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                fake.requests.append(
                    {"path": self.path, "auth": self.headers.get("Authorization"), "json": body}
                )
                time.sleep(fake.delay)
                if fake.body is not None:
                    payload = fake.body
                else:
                    results = [
                        {"index": i, "relevance_score": fake.score(d)}
                        for i, d in enumerate(body["documents"])
                    ]
                    results.sort(key=lambda r: -r["relevance_score"])
                    payload = json.dumps({"results": results}).encode()
                try:
                    self.send_response(fake.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass  # The client gave up (timeout test).

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/rerank"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def client(self, **env) -> Reranker:
        reranker = Reranker.from_env({"RERANK_URL": self.url, **env})
        self.clients.append(reranker)
        return reranker

    def close(self):
        for c in self.clients:
            c.close()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def rerank():
    fake = FakeRerank()
    yield fake
    fake.close()


def test_rerank_endpoint_reorders_the_fused_passages(engine, embedder, rerank):
    add_document(engine, "papers", PAPERS)
    # The endpoint prefers the glacier passage, which fusion ranks below the zebra one.
    rerank.score = lambda doc: {GLACIER: 0.9, ZEBRAS: 0.5}.get(doc, 0.1)

    plain = retrieve(engine, embedder, "papers", "zebras and the glacier", limit=5)
    result = retrieve(
        engine, embedder, "papers", "zebras and the glacier", reranker=rerank.client(), limit=5
    )

    assert texts(plain)[0] == ZEBRAS
    assert texts(result)[:2] == [GLACIER, ZEBRAS]
    assert result.reranked is True
    assert [p.rerank_score for p in result.passages[:2]] == [0.9, 0.5]
    scores = [p.rerank_score for p in result.passages]
    assert scores == sorted(scores, reverse=True)
    # The fusion score and ranks are kept alongside the rerank score.
    assert {p.chunk_id: p.fusion_score for p in result.passages} == {
        p.chunk_id: p.fusion_score for p in plain.passages
    }


def test_rerank_request_sends_the_question_and_the_fused_candidates(engine, embedder, rerank):
    add_document(engine, "papers", PAPERS)

    plain = retrieve(engine, embedder, "papers", "zebras", candidates=3)
    retrieve(
        engine,
        embedder,
        "papers",
        "zebras",
        reranker=rerank.client(RERANK_MODEL="bge-reranker", RERANK_API_KEY="rk-1"),
        candidates=3,
    )

    (request,) = rerank.requests
    assert request["path"] == "/v1/rerank"
    assert request["auth"] == "Bearer rk-1"
    assert request["json"]["query"] == "zebras"
    assert request["json"]["model"] == "bge-reranker"
    # Every fused candidate, in fusion order.
    assert request["json"]["documents"] == texts(plain)


def test_rerank_candidates_are_every_fused_passage_and_limit_applies_after(
    engine, embedder, rerank
):
    add_document(engine, "papers", PAPERS)
    rerank.score = lambda doc: 1.0 if doc == STYLE else 0.0

    result = retrieve(engine, embedder, "papers", "zebras", reranker=rerank.client(), limit=1)

    # STYLE is last by fusion; it can only win if the endpoint saw more than `limit` passages.
    assert texts(result) == [STYLE]
    assert len(rerank.requests[0]["json"]["documents"]) == len(PAPERS)


def test_rerank_request_has_no_key_or_model_when_unset(engine, embedder, rerank):
    add_document(engine, "papers", PAPERS)

    retrieve(engine, embedder, "papers", "zebras", reranker=rerank.client())

    (request,) = rerank.requests
    assert request["auth"] is None
    assert "model" not in request["json"]


def test_rerank_also_accepts_the_bare_list_shape(engine, embedder, rerank):
    add_document(engine, "papers", [ZEBRAS, GLACIER])
    # Text Embeddings Inference answers with a bare list of {index, score}.
    rerank.body = json.dumps([{"index": 1, "score": 0.8}, {"index": 0, "score": 0.2}]).encode()

    result = retrieve(engine, embedder, "papers", "zebras", reranker=rerank.client())

    assert texts(result) == [GLACIER, ZEBRAS]


def test_nothing_found_makes_no_rerank_call(engine, embedder, rerank):
    result = retrieve(engine, embedder, "nowhere", "zebras", reranker=rerank.client())

    assert result.passages == []
    assert rerank.requests == []


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (500, b'{"error": "model not loaded"}', "HTTP 500"),
        (200, b"", "empty"),
        (200, b"not json", "not JSON"),
        (200, b'{"data": []}', "results"),
        (200, b'{"results": [{"index": 0, "relevance_score": 0.5}]}', "2 documents"),
        (
            200,
            (
                b'{"results": [{"index": 0, "relevance_score": 0.5},'
                b' {"index": 0, "relevance_score": 0.4}]}'
            ),
            "twice",
        ),
        (
            200,
            (
                b'{"results": [{"index": 0, "relevance_score": 0.5},'
                b' {"index": 7, "relevance_score": 0.4}]}'
            ),
            "out of range",
        ),
        (
            200,
            (
                b'{"results": [{"index": 0, "relevance_score": "high"},'
                b' {"index": 1, "relevance_score": 0.4}]}'
            ),
            "score",
        ),
        (
            200,
            (
                b'{"results": [{"index": 0, "relevance_score": NaN},'
                b' {"index": 1, "relevance_score": 0.4}]}'
            ),
            "score",
        ),
    ],
)
def test_a_failed_rerank_is_reported_not_skipped(engine, embedder, rerank, status, body, reason):
    add_document(engine, "papers", [ZEBRAS, GLACIER])
    rerank.status = status
    rerank.body = body

    with pytest.raises(RerankError, match=reason):
        retrieve(engine, embedder, "papers", "zebras", reranker=rerank.client())


def test_a_rerank_timeout_is_reported(engine, embedder, rerank):
    add_document(engine, "papers", [ZEBRAS, GLACIER])
    rerank.delay = 2

    with pytest.raises(RerankError, match="did not answer within 0.2 s"):
        retrieve(
            engine,
            embedder,
            "papers",
            "zebras",
            reranker=rerank.client(RERANK_TIMEOUT_SECONDS="0.2"),
        )


def test_an_unreachable_rerank_endpoint_is_reported(engine, embedder):
    add_document(engine, "papers", [ZEBRAS, GLACIER])
    reranker = Reranker.from_env({"RERANK_URL": "http://127.0.0.1:9/rerank"})

    try:
        with pytest.raises(RerankError, match="unreachable"):
            retrieve(engine, embedder, "papers", "zebras", reranker=reranker)
    finally:
        reranker.close()


# --- Rerank configuration ---


@pytest.mark.parametrize("env", [{}, {"RERANK_URL": ""}, {"RERANK_URL": "  "}])
def test_no_reranker_when_rerank_url_is_unset(env):
    assert RerankConfig.from_env(env) is None
    assert Reranker.from_env(env) is None


def test_rerank_config_from_env():
    config = RerankConfig.from_env(
        {
            "RERANK_URL": " http://rerank:7997/rerank ",
            "RERANK_MODEL": "bge-reranker",
            "RERANK_API_KEY": "rk-1",
            "RERANK_TIMEOUT_SECONDS": "5",
        }
    )

    assert config == RerankConfig(
        url="http://rerank:7997/rerank", model="bge-reranker", api_key="rk-1", timeout=5.0
    )


def test_rerank_config_defaults():
    config = RerankConfig.from_env({"RERANK_URL": "https://rerank.example/v1/rerank"})

    assert config.model is None
    assert config.api_key is None
    assert config.timeout == 30.0


@pytest.mark.parametrize("url", ["rerank:7997", "ftp://rerank/x", "http://"])
def test_a_bad_rerank_url_is_rejected(url):
    with pytest.raises(RerankConfigError, match="RERANK_URL"):
        RerankConfig.from_env({"RERANK_URL": url})


def test_a_failed_rerank_keeps_its_body_out_of_the_message(engine, embedder, rerank):
    add_document(engine, "papers", [ZEBRAS, GLACIER])
    rerank.status = 503
    rerank.body = b"internal-host:5432 is down"

    with pytest.raises(RerankHTTPError) as exc:
        retrieve(engine, embedder, "papers", "zebras", reranker=rerank.client())

    assert exc.value.status_code == 503
    assert "internal-host" in exc.value.body
    assert "internal-host" not in str(exc.value)
    assert "HTTP 503" in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://admin:hunter2@rerank/x",
        "admin:hunter2@rerank",
        "http://admin:hunter2@",
        "https://:hunter2@/x",
    ],
)
def test_a_bad_rerank_url_is_reported_without_its_userinfo(url):
    with pytest.raises(RerankConfigError) as exc:
        RerankConfig.from_env({"RERANK_URL": url})

    assert "RERANK_URL" in str(exc.value)
    assert "hunter2" not in str(exc.value)
    assert "admin" not in str(exc.value)


@pytest.mark.parametrize("value", ["0", "-1", "soon", "nan", "inf"])
def test_a_bad_rerank_timeout_is_rejected(value):
    with pytest.raises(RerankConfigError, match="RERANK_TIMEOUT_SECONDS"):
        RerankConfig.from_env({"RERANK_URL": "http://rerank/x", "RERANK_TIMEOUT_SECONDS": value})


def test_a_failed_question_embedding_is_raised(engine):
    class Failing:
        def embed(self, texts):
            raise LLMHTTPError(503, "overloaded")

    add_document(engine, "papers", PAPERS)

    with pytest.raises(LLMHTTPError):
        retrieve(engine, Failing(), "papers", "zebras")

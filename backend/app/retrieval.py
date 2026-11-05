"""Find the passages of a collection that answer a question.

Two searches run over the chunks of the collection's `ready` documents: by vector
(cosine distance to the embedded question, pgvector) and by keyword (Postgres full-text,
any word of the question). Their rankings are combined by reciprocal rank fusion: a
chunk scores `1 / (RRF_K + rank)` for each list it is in, so one found by both searches
outranks one found near the top of only one.

When `RERANK_URL` is set, the fused candidates are sent to that HTTP rerank endpoint and
reordered by its scores. A rerank that fails raises a `RerankError`; it never falls back
to the fusion order quietly. Without `RERANK_URL` the fusion order is the answer.

Fusion and rerank scores are not comparable from one question to the next, so each
result also carries `top_similarity`: the cosine similarity of the collection's nearest
chunk, clamped to [0, 1]. That is what a refusal threshold should compare. It misses a
question asked by an ID or a rare term the embedding model does not know, so each result
also carries `top_keyword_coverage`: the largest share of the question's distinct words
(lexemes, as the keyword search reads them) that one keyword-matched chunk holds, in
[0, 1], 0.0 when the keyword search found nothing.
"""

import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import httpx
from sqlalchemy import Engine, case, cast, func, literal_column, select
from sqlalchemy.dialects.postgresql import TSQUERY

from app.db import (
    TEXT_SEARCH_CONFIG,
    chunk_embeddings,
    chunk_text_search,
    chunks,
    collections,
    documents,
    ingestion_status,
)
from app.ingest import Embedder

# The usual constant from the RRF paper (Cormack et al., 2009); it damps the head of each list.
RRF_K = 60
DEFAULT_LIMIT = 8
DEFAULT_CANDIDATES = 20
DEFAULT_RERANK_TIMEOUT_SECONDS = 30.0


class RerankError(Exception):
    """The rerank endpoint failed; the retrieval is not returned without it."""


class RerankConfigError(RerankError):
    """The environment does not describe a usable rerank endpoint; `setting` names the variable."""

    def __init__(self, setting: str, message: str):
        self.setting = setting
        super().__init__(message)


class RerankHTTPError(RerankError):
    """The rerank endpoint answered with a non-2xx status; the body is kept apart from the message."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"rerank endpoint returned HTTP {status_code}")


# The userinfo of a URL, with or without a scheme: everything up to the last "@" before
# the path.
_USERINFO = re.compile(r"^((?:[^:/?#]+:)?//)?[^/?#]*@")


def redact_url(url: str) -> str:
    """The URL with any user name and password replaced by `***`."""
    return _USERINFO.sub(r"\1***@", url, count=1)


@dataclass(frozen=True)
class Passage:
    chunk_id: int
    document_id: str
    page: int
    # `pages.text[start_offset:end_offset] == text`, as stored for the chunk.
    start_offset: int
    end_offset: int
    text: str
    heading: str | None
    # Cosine similarity to the question, clamped to [0, 1]; None when the chunk has no
    # vector of the question's dimension.
    similarity: float | None
    # 1-based rank in each search; None when that search did not return the chunk.
    vector_rank: int | None
    keyword_rank: int | None
    fusion_score: float
    rerank_score: float | None = None


@dataclass(frozen=True)
class Retrieval:
    passages: list[Passage]
    # Similarity of the nearest chunk in the collection, 0.0 when there is none.
    top_similarity: float
    # Largest share of the question's lexemes one keyword-matched chunk holds, 0.0 when
    # the keyword search found nothing.
    top_keyword_coverage: float
    reranked: bool


def fuse(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal rank fusion: each id scores the sum of `1 / (k + rank)` over the lists."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1 / (k + rank)
    return scores


def retrieve(
    engine: Engine,
    embedder: Embedder,
    collection: str,
    question: str,
    *,
    reranker: "Reranker | None" = None,
    limit: int = DEFAULT_LIMIT,
    candidates: int = DEFAULT_CANDIDATES,
) -> Retrieval:
    """The `limit` best passages of the named collection for `question`.

    Each search returns up to `candidates` chunks; every fused chunk is reranked, and
    `limit` applies last. Raises `ValueError` for an empty question, an `LLMError` when
    the question cannot be embedded, and a `RerankError` when the rerank fails.
    """
    if not question.strip():
        raise ValueError("question is empty")
    if engine.dialect.name != "postgresql":
        raise RuntimeError("retrieval needs Postgres with pgvector and full-text search")

    (query_vector,) = embedder.embed([question])
    dims = len(query_vector)

    ready = (
        select(chunks.c.id)
        .join(documents, documents.c.id == chunks.c.document_id)
        .join(collections, collections.c.id == documents.c.collection_id)
        .join(ingestion_status, ingestion_status.c.document_id == documents.c.id)
        .where(collections.c.name == collection, ingestion_status.c.state == "ready")
    )
    distance = chunk_embeddings.c.embedding.cosine_distance(query_vector)
    # A vector from an earlier embedding model with another dimension cannot be compared.
    same_dims = func.vector_dims(chunk_embeddings.c.embedding) == dims

    with engine.connect() as conn:
        vector_ids = list(
            conn.execute(
                select(chunk_embeddings.c.chunk_id)
                .where(chunk_embeddings.c.chunk_id.in_(ready), same_dims)
                .order_by(distance, chunk_embeddings.c.chunk_id)
                .limit(candidates)
            ).scalars()
        )

        keyword_ids: list[int] = []
        top_keyword_coverage = 0.0
        lexemes = _lexemes(conn, question)
        if lexemes:
            tsquery = _any_word_query(lexemes)
            query = cast(tsquery, TSQUERY)
            keyword_ids = list(
                conn.execute(
                    select(chunk_text_search.c.chunk_id)
                    .where(
                        chunk_text_search.c.chunk_id.in_(ready),
                        chunk_text_search.c.tsv.op("@@")(query),
                    )
                    .order_by(
                        func.ts_rank_cd(chunk_text_search.c.tsv, query).desc(),
                        chunk_text_search.c.chunk_id,
                    )
                    .limit(candidates)
                ).scalars()
            )
            for held in conn.execute(
                select(func.tsvector_to_array(chunk_text_search.c.tsv)).where(
                    chunk_text_search.c.chunk_id.in_(keyword_ids)
                )
            ).scalars():
                share = len(lexemes & set(held)) / len(lexemes)
                top_keyword_coverage = max(top_keyword_coverage, share)

        scores = fuse([vector_ids, keyword_ids])
        rows = {
            row.id: row
            for row in conn.execute(
                select(
                    chunks.c.id,
                    chunks.c.document_id,
                    chunks.c.page,
                    chunks.c.start_offset,
                    chunks.c.end_offset,
                    chunks.c.text,
                    chunks.c.heading,
                    # NULL for a vector of another dimension (or no vector).
                    case((same_dims, distance)).label("distance"),
                )
                .outerjoin(chunk_embeddings, chunk_embeddings.c.chunk_id == chunks.c.id)
                .where(chunks.c.id.in_(list(scores)))
            )
        }

    vector_rank = {chunk_id: rank for rank, chunk_id in enumerate(vector_ids, start=1)}
    keyword_rank = {chunk_id: rank for rank, chunk_id in enumerate(keyword_ids, start=1)}
    passages = [
        Passage(
            chunk_id=row.id,
            document_id=row.document_id,
            page=row.page,
            start_offset=row.start_offset,
            end_offset=row.end_offset,
            text=row.text,
            heading=row.heading,
            similarity=_similarity(row.distance),
            vector_rank=vector_rank.get(row.id),
            keyword_rank=keyword_rank.get(row.id),
            fusion_score=scores[row.id],
        )
        for row in rows.values()
    ]
    passages.sort(key=lambda p: (-p.fusion_score, p.chunk_id))
    top_similarity = rows[vector_ids[0]].distance if vector_ids else None
    top_similarity = _similarity(top_similarity) or 0.0

    if reranker is not None and passages:
        rerank_scores = reranker.rerank(question, [p.text for p in passages])
        passages = [
            replace(p, rerank_score=s) for p, s in zip(passages, rerank_scores, strict=True)
        ]
        # A stable sort: equal rerank scores keep their fusion order.
        passages.sort(key=lambda p: -p.rerank_score)

    return Retrieval(
        passages=passages[:limit],
        top_similarity=top_similarity,
        top_keyword_coverage=top_keyword_coverage,
        reranked=reranker is not None,
    )


def _lexemes(conn, question: str) -> set[str]:
    """The distinct words of the question, normalised as the chunks were (stemmed, stop
    words dropped); empty when no word is left."""
    return set(
        conn.execute(
            select(
                func.tsvector_to_array(
                    func.to_tsvector(literal_column(f"'{TEXT_SEARCH_CONFIG}'::regconfig"), question)
                )
            )
        ).scalar_one()
    )


def _any_word_query(lexemes: set[str]) -> str:
    """A tsquery, as text, that matches a chunk holding any of the lexemes.

    The lexemes are joined with OR. Each is quoted, so nothing the user typed is read as
    query syntax.
    """
    return " | ".join(
        "'" + lex.replace("\\", "\\\\").replace("'", "''") + "'" for lex in sorted(lexemes)
    )


def _similarity(distance: float | None) -> float | None:
    """Cosine distance to similarity in [0, 1]. Opposite vectors score 0, not below."""
    if distance is None or not math.isfinite(distance):
        return None
    return min(1.0, max(0.0, 1.0 - distance))


@dataclass(frozen=True)
class RerankConfig:
    url: str
    model: str | None = None
    api_key: str | None = None
    timeout: float = DEFAULT_RERANK_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RerankConfig | None":
        """The rerank endpoint from `RERANK_*`, or None when `RERANK_URL` is unset."""
        env = os.environ if env is None else env
        url = env.get("RERANK_URL", "").strip()
        if not url:
            return None
        try:
            parsed = httpx.URL(url)
        except httpx.InvalidURL:
            parsed = None
        if parsed is None or parsed.scheme not in ("http", "https") or not parsed.host:
            raise RerankConfigError(
                "RERANK_URL", f"RERANK_URL must be an http(s) URL, got {redact_url(url)!r}"
            )

        raw_timeout = env.get("RERANK_TIMEOUT_SECONDS", "").strip()
        timeout = DEFAULT_RERANK_TIMEOUT_SECONDS
        if raw_timeout:
            try:
                timeout = float(raw_timeout)
            except ValueError:
                timeout = math.nan
            if not math.isfinite(timeout) or timeout <= 0:
                raise RerankConfigError(
                    "RERANK_TIMEOUT_SECONDS",
                    "RERANK_TIMEOUT_SECONDS must be a positive number of seconds, "
                    f"got {raw_timeout!r}",
                )

        return cls(
            url=url,
            model=env.get("RERANK_MODEL", "").strip() or None,
            api_key=env.get("RERANK_API_KEY", "").strip() or None,
            timeout=timeout,
        )


class Reranker:
    """A client for an HTTP rerank endpoint in the Cohere/Jina shape.

    It posts `{"query", "documents", "model"?}` and reads `{"results": [{"index",
    "relevance_score"}]}`; a bare list of `{"index", "score"}` (Text Embeddings
    Inference) is read too.
    """

    def __init__(self, config: RerankConfig):
        self.config = config
        headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
        self._http = httpx.Client(headers=headers, timeout=config.timeout)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Reranker | None":
        config = RerankConfig.from_env(env)
        return None if config is None else cls(config)

    def close(self) -> None:
        self._http.close()

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        """One relevance score per document, in input order, or raise a `RerankError`."""
        if not docs:
            return []
        payload: dict[str, Any] = {"query": query, "documents": docs}
        if self.config.model:
            payload["model"] = self.config.model
        try:
            resp = self._http.post(self.config.url, json=payload)
        except httpx.TimeoutException as exc:
            raise RerankError(
                f"rerank endpoint did not answer within {self.config.timeout:g} s"
            ) from exc
        except httpx.TransportError as exc:
            raise RerankError(f"rerank endpoint unreachable: {exc}") from exc

        if not resp.is_success:
            raise RerankHTTPError(resp.status_code, resp.text)
        if not resp.content.strip():
            raise RerankError("rerank endpoint returned an empty body")
        try:
            body = resp.json()
        except ValueError as exc:
            raise RerankError("rerank endpoint returned a body that is not JSON") from exc

        results = body.get("results") if isinstance(body, dict) else body
        if not isinstance(results, list):
            raise RerankError("rerank response has no results list")
        if len(results) != len(docs):
            raise RerankError(f"rerank response scores {len(results)} of {len(docs)} documents")

        scores: list[float | None] = [None] * len(docs)
        for item in results:
            if not isinstance(item, dict):
                raise RerankError("rerank result is not an object")
            index = item.get("index")
            score = item.get("relevance_score", item.get("score"))
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(docs):
                raise RerankError(f"rerank index {index!r} is out of range")
            if scores[index] is not None:
                raise RerankError(f"rerank index {index} appears twice")
            if (
                not isinstance(score, int | float)
                or isinstance(score, bool)
                or not math.isfinite(score)
            ):
                raise RerankError(f"rerank score for index {index} is not a finite number")
            scores[index] = float(score)
        return scores  # type: ignore[return-value]  # every slot was filled above

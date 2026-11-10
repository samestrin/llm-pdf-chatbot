# How it works

The stack is three containers: Postgres with pgvector (`db`), a FastAPI backend (`backend/`, Python, managed with uv) and a React + TypeScript UI built with Vite and served by nginx (`frontend/`). The browser talks only to nginx, which forwards `/api/...` to the backend.

## Ingestion

An uploaded PDF is queued and ingested in the background by a worker inside the backend: its pages are read, chunked, indexed for keyword search (Postgres full-text) and embedded through the configured embedding model into pgvector, `EMBED_BATCH_SIZE` chunks (default 64) per call. The model client needs `LLM_BASE_URL`, `LLM_FAST_MODEL`, `LLM_REASONING_MODEL` and `LLM_EMBEDDING_MODEL`, plus `LLM_API_KEY` when the endpoint wants one.

Each page is read from the PDF's text layer, or, when `OCR_URL` is set, by the OCR service, which receives the whole PDF at `POST /v1/extract`; any page OCR did not read falls back to the PDF's own text layer.

Each document's state (`queued`, `processing`, `ready`, `failed` with the reason) and progress are kept in the `ingestion_status` table and reported by `GET /documents/{id}/status` (see [the API](api.md#document-status)). A document left `processing` when the backend stopped is marked failed on the next start; one still `queued` is ingested then.

## Retrieval

A question is answered from the passages of one collection, found in `backend/app/retrieval.py` by two searches over the chunks of its `ready` documents: by vector (cosine distance to the embedded question) and by keyword (Postgres full-text, any word of the question). The two rankings are combined by reciprocal rank fusion, so a passage both searches find ranks above one only a single search finds. Retrieval needs Postgres; it does not run on SQLite.

An optional rerank service can reorder the fused passages (see [Reranking](configuration.md#reranking)). Without `RERANK_URL` the fusion order is used as is. When the endpoint fails (an error status, a timeout, or an answer that does not score every passage), retrieval raises an error; it never falls back to the fusion order without saying so.

Each retrieval also reports `top_similarity`: the cosine similarity of the collection's nearest passage to the question, clamped to 0..1. Fusion and rerank scores cannot be compared from one question to the next, so this is the number the `REFUSAL_MIN_SIMILARITY` threshold compares. It also reports `top_keyword_coverage`: the largest share of the question's distinct words (stemmed, stop words dropped) that one passage found by the keyword search holds, from 0 to 1 (0 when the keyword search found nothing). `REFUSAL_MIN_KEYWORD_COVERAGE` compares it.

## Answers

`POST /collections/{name}/chat` retrieves passages from the collection for the question (`backend/app/answer.py`).

When the nearest passage's `top_similarity` is below `REFUSAL_MIN_SIMILARITY` (default 0.3; a similarity equal to it is answered), the question is off topic: the answer is a `refusal` event with the closest passages, and the model is not asked. The exception is a strong keyword hit: when one passage holds at least `REFUSAL_MIN_KEYWORD_COVERAGE` of the question's words (default 1.0, every word; a coverage equal to it counts), the question is answered however low its similarity, so a question asked by an ID or a rare term the embedding model does not know still reaches the model. Such a question is still refused when no claim survives the check below.

Otherwise the backend streams the answer as NDJSON, one event per line. The fast model is asked for JSON claims, each with the `chunk_id` of the passage it rests on and a `quote` copied from it, and each claim is sent as a `claim` event (with the document, page, chunk id and quote) as soon as it has streamed in whole.

Every claim is checked before it is sent (`backend/app/citation.py`):

- it must cite a chunk that was retrieved;
- its quote must be at least 15 characters long and a substring of that chunk once both are normalised (unicode compatibility forms such as ligatures and non-breaking spaces, typographic quotes, case, hyphens between letters including line-break hyphens, and runs of whitespace);
- the chunk's offsets must still resolve to the chunk's text on the stored page.

A claim that fails any of these is dropped, never shown as cited, and the `quote` sent is the exact page text the quote matched. When no claim is left, a `refusal` event lists the passages instead. An answer that finished ends in a `done` event with the mode, the model's name and the cost in the price's currency, or `null` when no prices are set, the endpoint reported no token usage, or the model was not asked (never 0 as a stand-in).

A failed model call, a stream that breaks off, or a reply that is not in the claims schema ends the answer in an `error` event, also after some claims were sent, so a partial answer is never marked done.

Retrieved text is passed to the model as data: each passage sits between markers holding a random value made for that request and found in no passage, so text in a PDF cannot close its marker and pass itself off as instructions.

## Deep mode

With `"mode": "deep"` the reasoning model (`LLM_REASONING_MODEL`) answers, for questions that need several searches, such as a comparison across documents (`backend/app/deep.py`).

1. It first plans the question as sub-questions, at most `DEEP_MAX_SUBQUESTIONS` (default 5); blank and repeated ones are dropped, and a plan with none searches for the question itself.
2. Each sub-question is retrieved on its own and sent as a `step` event with the sub-question (`question`) and the pages its retrieval found (`pages`, each a `document` and a `page`, once each), before any claim.
3. The answer is then written from the passages of every sub-question together, with the same citation check as a fast answer.

It is refused with the closest passages when every sub-question is off topic (by the same thresholds), and when no claim survives the check.

A deep answer's claims are sent only after the whole reply has arrived and checked out. Any failure ends the run in an `error` event with no claim: a failed plan or a plan outside its format, a sub-question whose retrieval fails, a stream that breaks off or is cut off, a reply outside the claims schema, more than `DEEP_MAX_TOKENS` tokens (default 50000) over the plan and the answer together, or more than `DEEP_TIMEOUT_SECONDS` (default 300) for the run. Tokens are counted as the endpoint reports them, or estimated at four characters a token when it reports none. The time limit is checked between steps and on each streamed piece; a single model call can still run for its own `LLM_TIMEOUT_SECONDS`. The `done` event names the reasoning model; its cost is `null` until reasoning-model prices can be set.

## Citation viewer

Clicking a citation chip opens that page of the stored PDF with pdf.js and highlights the quote. The quote is found by searching the page's pdf.js text layer, comparing letters and digits only (case, spacing, line breaks, hyphens, punctuation, ligatures and accents are ignored), because the stored page text the quote comes from may have been read by another extractor or by OCR. When the quote is not found, the page still opens, with a "Quote not located on this page." notice and the quote text beside it. If a claim carries `boxes` (rectangles in PDF points, origin at the page's bottom-left), those are drawn instead and no search is made.

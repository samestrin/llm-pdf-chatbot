# llm-pdf-chatbot

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/img/logo-dark.svg">
    <img src="docs/img/logo-light.svg" alt="LLM PDF Chatbot logo" width="180">
  </picture>
</p>

A self-hosted web app for anyone who wants to chat with their own PDFs and get answers they can check: every answer cites the quote and page it came from, and says plainly when the answer is not in your documents.

<p>
  <a href="https://github.com/samestrin/llm-pdf-chatbot/actions/workflows/ci.yml"><img src="https://github.com/samestrin/llm-pdf-chatbot/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/samestrin/llm-pdf-chatbot/releases/latest"><img src="https://img.shields.io/github/v/release/samestrin/llm-pdf-chatbot" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="License: MIT"></a>
</p>

## Demo

![A real session on eight dog-training guides: a fast answer, a deep answer with its plan, and a question the guides do not answer](docs/img/demo.gif)

![A streamed answer with citation chips](docs/img/answer.png)

![The same page in dark mode](docs/img/dark.png)

## Why

- Model answers about documents are hard to trust. Each claim here carries a citation chip; click it to open the stored PDF at that page with the quote highlighted.
- A model can invent a citation. Every claim is checked before it is shown: it must quote a passage that was retrieved, and that passage must still match the stored page; a claim that fails is dropped.
- A model will answer questions your documents do not cover. A question with no close enough passage is answered "Not in your documents", with the closest passages, and the model is not asked.
- Some questions need several searches, such as a comparison across documents. Deep mode plans sub-questions, searches each one and shows the steps before the answer.
- You choose where the model runs. It works with local models (Ollama by default) or any OpenAI-compatible endpoint; the PDFs and their index stay in your own Postgres, and scanned PDFs can be read by an OCR service you run.

See [VISION.md](VISION.md) for what this project is and what it will become.

## Install

You need Docker with the Compose plugin (`docker compose version`) and a model endpoint: [Ollama](https://ollama.com) running on the same machine for the default settings, or any OpenAI-compatible endpoint.

```bash
git clone https://github.com/samestrin/llm-pdf-chatbot.git
cd llm-pdf-chatbot
cp .env.example .env   # then edit the model settings, see Configuration
docker compose up --build
```

Then open <http://localhost:8080>. The images are built from source; none are published by default.

No local models? Use the cloud block instead: in `.env`, replace the local block with the commented cloud block (`LLM_BASE_URL`, `LLM_API_KEY` and the three model names), then run `docker compose up --build`. See [docs/configuration.md](docs/configuration.md#cloud-models).

### Upgrade

```bash
git pull
docker compose up --build
```

### Uninstall

`docker compose down` stops and removes the containers and keeps the database and uploads volumes. `docker compose down -v` also deletes those volumes, with every stored document.

## Quick start

With Ollama running on the host:

```bash
ollama pull llama3.2 && ollama pull qwen3:14b && ollama pull nomic-embed-text
git clone https://github.com/samestrin/llm-pdf-chatbot.git && cd llm-pdf-chatbot
cp .env.example .env
docker compose up --build -d --wait
curl http://localhost:8000/health
```

```text
{"status":"ok"}
```

1. Open <http://localhost:8080>. In the empty documents panel, click **Try a sample PDF**; it uploads a small sample PDF into the `default` collection.
2. When the document shows "Ready", ask a question about it and press **Ask**.

You should see the answer stream in, each claim followed by a citation chip with the page it came from (the file is named once, above the claims); click a chip to open the PDF at that page with the quote highlighted. A question the sample does not cover is answered "Not in your documents". If the model cannot be reached, the page says so and names `LLM_BASE_URL` as the setting to check.

## Usage

| Task | How |
| --- | --- |
| Upload a PDF | Pick the collection once in the documents panel, then drop a PDF on the panel or choose one with the file button. Each one is read page by page (the text layer, or OCR for scanned pages), chunked, and indexed for keyword and vector search. |
| Ask a question | Type it and press **Ask** or Enter. It is asked in the same collection; the answer streams in under a short line that says where it was found. |
| Check a claim | Click its citation chip to open the stored PDF at that page with the quote highlighted. |
| Ask a harder question | Turn on the "Deep mode" switch: the reasoning model plans sub-questions and shows each search as a step. |
| Switch theme | The theme button in the header overrides the system light or dark scheme. |

The document is named once per answer, or, when an answer cites several documents, listed as numbered sources that the chips refer to. When the answer is not in your documents, it says so instead of guessing. The full guide to the page, including keyboard and screen-reader use, is [docs/web-app.md](docs/web-app.md).

## HTTP API

The page talks to a FastAPI backend, which you can call directly on port 8000. The browser reaches it at same-origin `/api/...` through the frontend's nginx, so <http://localhost:8080/api/health> is the same as <http://localhost:8000/health>. Interactive API docs are at <http://localhost:8000/docs>.

```bash
curl -F file=@lease.pdf http://localhost:8000/collections/default/documents
curl -N http://localhost:8000/collections/default/chat \
  -H 'Content-Type: application/json' -d '{"question": "What is the notice period?"}'
```

A chat answer streams as NDJSON, one event per line: `step` (deep mode), `claim`, `refusal`, `done` or `error`. A failure after the stream starts arrives as an `error` event, not as a status code.

| Status | Meaning |
| --- | --- |
| 200 | OK; for chat, the stream started |
| 201 | Document uploaded and queued for ingestion |
| 204 | Document or empty collection deleted |
| 404 | No such document, page or collection |
| 409 | Collection is not empty, or the page has not been read yet |
| 413 | Upload larger than the size limit (50 MB) |
| 422 | Not a PDF, unreadable, no pages, over the page limit (500), or an invalid chat request |
| 503 | `GET /health`: the database is unavailable |

[docs/api.md](docs/api.md) links the interactive reference and the OpenAPI spec, and documents the chat events and behaviour the spec does not carry.

## Configuration

Everything is an environment variable that `docker compose` reads from your shell or from `.env`; `.env.example` is the template. An empty value means the default.

| Name | Default | Meaning |
| --- | --- | --- |
| `LLM_BASE_URL` | Ollama on the host in `.env.example` | Base URL of an OpenAI-compatible endpoint |
| `LLM_API_KEY` | empty | API key; leave empty for local servers |
| `LLM_FAST_MODEL` | `llama3.2` in `.env.example` | Model for quick steps and fast answers |
| `LLM_REASONING_MODEL` | `qwen3:14b` in `.env.example` | Model for deep mode |
| `LLM_EMBEDDING_MODEL` | `nomic-embed-text` in `.env.example` | Model for embeddings |
| `LLM_TIMEOUT_SECONDS` | 60 | Seconds to wait for a model call |
| `EMBED_BATCH_SIZE` | 64 | Chunks per embedding call |
| `OCR_URL` | empty (no OCR) | URL of an `llm-pdf-ocr-api` service for scanned pages |
| `RERANK_URL`, `RERANK_MODEL`, `RERANK_API_KEY` | empty (no rerank) | Optional rerank endpoint, its model and key |
| `RERANK_TIMEOUT_SECONDS` | 30 | Seconds to wait for the rerank endpoint |
| `REFUSAL_MIN_SIMILARITY` | 0.3 | Below this cosine similarity (0 to 1) of the nearest passage, a question is refused without asking the model |
| `REFUSAL_MIN_KEYWORD_COVERAGE` | 1.0 | A question below that similarity is still answered when one passage holds this share (0 to 1) of its words |
| `CHAT_HISTORY_TURNS` | 6 | Earlier turns sent to the model with each question (0 sends none) |
| `DEEP_MAX_SUBQUESTIONS` | 5 | Most sub-questions deep mode plans |
| `DEEP_MAX_TOKENS` | 50000 | Tokens one deep run may use before it ends in an error |
| `DEEP_TIMEOUT_SECONDS` | 300 | Seconds one deep run may take before it ends in an error |
| `LLM_PRICE_FAST_PER_MTOK_IN`, `LLM_PRICE_FAST_PER_MTOK_OUT` | empty (cost `null`) | Fast-model prices per million input and output tokens, for the cost of each answer |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | `postgres`, `postgres`, `chatbot` | Database credentials |
| `BACKEND_PORT`, `FRONTEND_PORT` | 8000, 8080 | Published ports |

Local and cloud model setups, OCR, reranking, prices and the settings compose does not pass are in [docs/configuration.md](docs/configuration.md).

## Performance

Answer quality, measured by the evaluation in `backend/evals/` (method and measures in [docs/evaluation.md](docs/evaluation.md)). The table is generated from the newest committed results file, never typed by hand.

<!-- eval-score:begin (generated by `cd backend && uv run python -m evals.readme --write`) -->
From `backend/evals/results/2026-11-06.json`, the full run of 2026-11-06: 40 questions on 5 PDFs, model `deepseek-v4.1-flash`.

| Measure | Score |
|---|---|
| Answer accuracy | 0.833 (25/30) |
| Citation precision | 0.964 (27/28) |
| Refusal accuracy | 0.950 (38/40) |
| Cost | unknown |
| Errors | 0 |
<!-- eval-score:end -->

## How it works

1. An uploaded PDF is read page by page in the background (its text layer, or the OCR service), chunked, indexed for Postgres full-text search and embedded into pgvector.
2. A question is searched in one collection by vector and by keyword; the two rankings are merged by reciprocal rank fusion, then optionally reranked.
3. When no passage is close enough, the question is refused with the closest passages. Otherwise the model is asked for claims, each citing a passage and quoting it.
4. Each claim is checked against the passage and the stored page text before it is streamed; a claim that fails is dropped.

Details, including deep mode and how the viewer finds the quote on the page, are in [docs/how-it-works.md](docs/how-it-works.md).

## Limits

- There is no auth: the app is local and single-user. Do not expose it to a network you do not trust without an auth proxy in front.
- Uploads are limited to 50 MB and 500 pages (see [docs/configuration.md](docs/configuration.md#settings-the-backend-reads-but-docker-composeyml-does-not-pass)).
- A question is answered from one collection at a time.
- Scanned PDFs need a separate `llm-pdf-ocr-api` service; without `OCR_URL`, a PDF with no text layer fails to ingest.
- Retrieval, and so chat, needs Postgres with pgvector; it does not run on SQLite. Keyword search uses Postgres's English text search configuration.
- Reranking needs a separate rerank service; no rerank model is built into the backend image.
- Only fast-model prices can be set, so a deep answer's cost is always `null` (unknown).

## Documentation

- [docs/web-app.md](docs/web-app.md): using the page: collections, asking, deep mode, the citation viewer, themes, keyboard and screen readers
- [docs/api.md](docs/api.md): links to the interactive reference and spec, plus chat events and behaviour the spec does not carry
- [docs/configuration.md](docs/configuration.md): every setting, local and cloud models, OCR, reranking, prices
- [docs/how-it-works.md](docs/how-it-works.md): ingestion, retrieval, the citation check, deep mode
- [docs/evaluation.md](docs/evaluation.md): the evaluation, its measures and how to run it
- [docs/development.md](docs/development.md): layout, tests, the smoke test, README images, releases
- [docs/fixture-licences.md](docs/fixture-licences.md): licences of the committed test fixtures
- [VISION.md](VISION.md): what this project is and what it will become

## Development

### Backend tests

The backend tests run on SQLite and on Postgres with pgvector. The Postgres tests fail (they do not skip) unless `DATABASE_URL` points at one:

```bash
docker run -d --name pdf-chatbot-test-db -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=test pgvector/pgvector:pg17
cd backend
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/test uv run pytest
uv run ruff check .
```

### Frontend tests

```bash
cd frontend
npm ci --include=dev
npm test           # unit tests (vitest)
npx playwright install chromium
npm run test:e2e   # browser tests (Playwright)
```

### Smoke test and README images

`./scripts/compose-smoke.sh` builds the whole stack on throwaway ports with a fake model and checks a cited answer end to end; it needs Docker, `curl` and `jq`, and no model key. The screenshots and GIF in `docs/img/` are generated by `npm run capture:readme` in `frontend/`.

The rest (the dev server, lint, the layout, releases) is in [docs/development.md](docs/development.md).

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

MIT; see [LICENSE](LICENSE).

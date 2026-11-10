# llm-pdf-chatbot

Chat with your own PDFs and get answers you can trust: every answer cites the quote and page it came from, and says plainly when the answer is not in your documents. See [VISION.md](VISION.md) for what this project is and what it will become.

## What it does

- Upload PDFs into a collection. Each one is read page by page (the text layer, or OCR for scanned pages), chunked, and indexed for keyword and vector search.
- Ask a question in the chat. The answer streams in, and every claim carries a citation chip showing the document and page it came from.
- Click a chip to open the stored PDF at that page with the quote highlighted (see [Citation viewer](#citation-viewer)). When the answer is not in your documents, it says so instead of guessing.

![Uploading a PDF and watching it ingest](docs/img/upload.png)

![A streamed answer with citation chips](docs/img/answer.png)

![The cited answer, each claim with its citation chip](docs/img/citation.png)

![The whole flow: upload, ask, read the cited answer](docs/img/demo.gif)

![The same page in dark mode](docs/img/dark.png)

![The page at phone width](docs/img/phone.png)

## Try it

You need Docker with the Compose plugin (`docker compose version`) and [Ollama](https://ollama.com) running on the same machine.

1. Pull the three local models the default settings use:

   ```bash
   ollama pull llama3.2
   ollama pull qwen3:14b
   ollama pull nomic-embed-text
   ```

2. Start the stack:

   ```bash
   cp .env.example .env
   docker compose up --build
   ```

3. Open <http://localhost:8080>. In the empty documents panel, click **Try a sample PDF**; it uploads a small sample PDF into the `default` collection.
4. When the document shows "Ready", ask a question about it and press **Ask**.

You should see the answer stream in, each claim followed by a citation chip with the file and page it came from; click a chip to open the PDF at that page with the quote highlighted. A question the sample does not cover is answered "Not in your documents". If the model cannot be reached, the page says so and names `LLM_BASE_URL` as the setting to check.

No local models? Use the cloud block instead: in `.env`, replace the local block with the commented cloud block (`LLM_BASE_URL`, `LLM_API_KEY` and the three model names), then run `docker compose up --build`. See [Configuration](#configuration).

## Run it

You need Docker with the Compose plugin (`docker compose version`).

```bash
cp .env.example .env   # then edit the model settings, see Configuration
docker compose up --build
```

Then open <http://localhost:8080>.

Useful checks once it is up:

- Backend health: <http://localhost:8000/health> returns `{"status":"ok"}`
- The same through the frontend: <http://localhost:8080/api/health>. The browser reaches the backend only at same-origin `/api/...`; nginx strips `/api` and forwards to the backend, so no CORS is needed.
- API docs: <http://localhost:8000/docs>

To stop and remove the containers (keeping the database volume):

```bash
docker compose down
```

## Configuration

Everything is an environment variable that `docker compose` reads from your shell or from `.env`. `.env.example` is the template.

| Variable | Meaning |
| --- | --- |
| `LLM_BASE_URL` | Base URL of an OpenAI-compatible endpoint |
| `LLM_API_KEY` | API key; leave empty for local servers |
| `LLM_FAST_MODEL` | Model for quick steps |
| `LLM_REASONING_MODEL` | Model for answering |
| `LLM_EMBEDDING_MODEL` | Model for embeddings |
| `LLM_TIMEOUT_SECONDS` | Seconds to wait for a model call; empty means 60 |
| `EMBED_BATCH_SIZE` | Chunks per embedding call; empty means 64 |
| `OCR_URL` | URL of an `llm-pdf-ocr-api` service for scanned pages; empty means no OCR |
| `RERANK_URL`, `RERANK_MODEL`, `RERANK_API_KEY`, `RERANK_TIMEOUT_SECONDS` | Optional rerank endpoint, its model and key, and its timeout (default 30 seconds); empty `RERANK_URL` means no rerank (see [Retrieval](#retrieval)) |
| `REFUSAL_MIN_SIMILARITY` | A question whose nearest passage has a lower cosine similarity (0 to 1) is refused as "not in your documents" without asking the model; empty means 0.3 |
| `REFUSAL_MIN_KEYWORD_COVERAGE` | A question below `REFUSAL_MIN_SIMILARITY` is still answered when one passage holds at least this share (0 to 1) of its words, as for an ID or a rare term the embedding model does not know; empty means 1.0 (every word) |
| `CHAT_HISTORY_TURNS` | How many earlier turns of the conversation go to the model with each question (0 sends none); empty means 6 |
| `DEEP_MAX_SUBQUESTIONS`, `DEEP_MAX_TOKENS`, `DEEP_TIMEOUT_SECONDS` | Deep mode's limits: the most sub-questions the reasoning model plans (empty means 5), and the tokens (empty means 50000) and seconds (empty means 300) one deep run may use before it ends in an error (see [Deep mode](#deep-mode)) |
| `LLM_PRICE_FAST_PER_MTOK_IN`, `LLM_PRICE_FAST_PER_MTOK_OUT` | Fast-model prices per million input and output tokens, for the cost of each answer (see [Model prices](#model-prices)); empty means the cost is `null` |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Database credentials (default `postgres`, `postgres`, `chatbot`) |
| `BACKEND_PORT`, `FRONTEND_PORT` | Published ports (default 8000 and 8080) |

**Local models.** The defaults in `.env.example` point at Ollama on the host (`http://host.docker.internal:11434/v1`) with `llama3.2`, `qwen3:14b` and `nomic-embed-text`; pull those models first. No key is needed.

**Cloud models.** Replace the block with the commented cloud block in `.env.example`: set `LLM_BASE_URL` to `https://api.openai.com/v1`, `LLM_API_KEY` to your key, and the three model names.

**OCR.** Set `OCR_URL` to a running `llm-pdf-ocr-api`; the backend sends it the whole PDF at `POST /v1/extract` and falls back to the PDF's own text layer for any page OCR did not read.

## Evaluation

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

The table is the newest results file in `backend/evals/results/`, and the backend tests fail when it is not: after committing a results file, run `cd backend && uv run python -m evals.readme --write` (`uv run python -m evals.readme` alone only checks). A local `--subset` run writes a newer results file too, so delete it before running the tests.

The eval asks the 40 questions in `backend/evals/questions.json` about the five PDFs in `backend/evals/pdfs/` (sources and licences in `backend/evals/SOURCES.md`) through the chat API in fast mode, and scores:

- **Answer accuracy**: of the answerable questions, the share whose answer holds every expected key fact. With `--judge`, the fast model judges each answer against the key facts instead (for full runs, never in CI).
- **Citation precision**: of the claims given for answerable questions, the share that cite the expected PDF on the expected page.
- **Refusal accuracy**: of all questions, the share refused exactly when they cannot be answered from the PDFs.
- **Cost**: the answers' total cost, or `unknown` when any answer that reached the model has no cost (no fast-model prices, or no token usage reported; see [Model prices](#model-prices)); never 0 for a missing price. A question refused before any model call costs 0.

```bash
cd backend && uv run python -m evals.run            # full run: the models and OCR_URL from the environment
cd backend && uv run python -m evals.run --judge    # the same, with the model judging accuracy
cd backend && uv run python -m evals.run --subset   # what CI runs: no scanned PDF, a fake model
```

Each run needs `DATABASE_URL` pointing at Postgres with pgvector (see [Backend tests](#backend-tests)); it creates its tables in a schema of its own and drops it at the end, so the database's own documents are never touched. It prints the score table and writes `backend/evals/results/<date>.json` (today's UTC date; a second run that day replaces it) with every question's result. The full run needs `OCR_URL`: without it the scanned PDF does not ingest and the run stops. The `--subset` run answers with a fake model that quotes the passage sentence sharing most words with the question, so its scores show the pipeline runs end to end, not how good a real model is; its results file says `"run": "subset"`. The owner makes the full runs and commits their results files.

## Backend tests

The backend tests run on SQLite and on Postgres with pgvector. The Postgres tests fail (they do not skip) unless `DATABASE_URL` points at one:

```bash
docker run -d --name pdf-chatbot-test-db -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=test pgvector/pgvector:pg17
cd backend
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/test uv run pytest
```

Each test works in a schema of its own and drops it afterwards.

## Ingestion

An uploaded PDF is queued and ingested in the background by a worker inside the backend: its pages are read, chunked, indexed for keyword search (Postgres full-text) and embedded through the configured embedding model (the model client needs `LLM_BASE_URL`, `LLM_FAST_MODEL`, `LLM_REASONING_MODEL` and `LLM_EMBEDDING_MODEL`, plus `LLM_API_KEY` when the endpoint wants one) into pgvector, `EMBED_BATCH_SIZE` chunks (default 64) per call. Each document's state (`queued`, `processing`, `ready`, `failed` with the reason) and progress are kept in the `ingestion_status` table. A document left `processing` when the backend stopped is marked failed on the next start; one still `queued` is ingested then.

`GET /documents/{id}/status` reports a document's state and progress (pages read, chunks embedded), each page with the method that read it (`text_layer` or `tesseract` from OCR, `pymupdf` from the text-layer fallback) and why OCR failed on it, and under `failures` every page no method could read, with the reason. A document can be `ready` with some pages failed.

In the page, the documents panel shows a spinner while it loads the collections. Before any document is uploaded, both the panel and the chat say what to do first: upload a PDF, then ask about it. A document still queued or being read shows its progress and a note that it can be asked about once it is ready, and the chat names the documents of the chosen collection that answers still leave out. A failed load or upload shows the reason with a Retry button; Retry upload sends the same file to the same collection again.

## Model prices

To have the cost of a fast-model call worked out, set `LLM_PRICE_FAST_PER_MTOK_IN` and `LLM_PRICE_FAST_PER_MTOK_OUT` (prices per million input and output tokens, both or neither). Without them, or when the endpoint reports no token usage, the cost is `null`, never 0. Set both to `0` for a free local model.

## Reading documents back

- `GET /collections` lists every collection with its documents, each with the same status fields under `status`.
- `GET /documents/{id}/pdf` serves the stored PDF inline (`application/pdf`, `X-Content-Type-Options: nosniff`). Only an id the server generated at upload works; any other id returns 404, and the client's file name is never used as a path.
- `GET /documents/{id}/pages/{page}` returns a page's stored text, which is the exact text that citation offsets index into. A page no method could read has `text` null and the reason in `error`.

## Retrieval

A question is answered from the passages of one collection, found in `backend/app/retrieval.py` by two searches over the chunks of its `ready` documents: by vector (cosine distance to the embedded question) and by keyword (Postgres full-text, any word of the question). The two rankings are combined by reciprocal rank fusion, so a passage both searches find ranks above one only a single search finds. Retrieval needs Postgres; it does not run on SQLite.

Reranking is optional and runs on a separate HTTP service; no model is added to the backend image. Set `RERANK_URL` to the full URL of a rerank endpoint in the Cohere/Jina shape (it receives `{"query", "documents", "model"}` and answers `{"results": [{"index", "relevance_score"}]}`; a bare list of `{"index", "score"}`, as Text Embeddings Inference returns, works too). `RERANK_MODEL` and `RERANK_API_KEY` are sent when set, and `RERANK_TIMEOUT_SECONDS` defaults to 30. Without `RERANK_URL` the fusion order is used as is. When the endpoint fails (an error status, a timeout, or an answer that does not score every passage), retrieval raises an error; it never falls back to the fusion order without saying so.

Each retrieval also reports `top_similarity`: the cosine similarity of the collection's nearest passage to the question, clamped to 0..1. Fusion and rerank scores cannot be compared from one question to the next, so this is the number the `REFUSAL_MIN_SIMILARITY` threshold compares. It also reports `top_keyword_coverage`: the largest share of the question's distinct words (stemmed, stop words dropped) that one passage found by the keyword search holds, from 0 to 1 (0 when the keyword search found nothing). `REFUSAL_MIN_KEYWORD_COVERAGE` compares it.

## Answers

An answer the documents do not support is shown as "Not in your documents", with the closest passages the search found (file, page and text), or a note that none came close. An answer is never left blank: an error from the backend, a request that fails, a stream that ends before its final event and an answer that finishes with nothing in it are each shown as an error, and an answer stopped with the Stop button says it was stopped. A failed answer has a Retry button that asks the same question again in its place, in the collection and mode it was first asked in. A finished answer shows its mode, model and cost; a cost the backend could not work out is shown as "cost unknown", never as $0. Model, quote, passage and file-name text is always shown as plain text, never as markup.

`POST /collections/{name}/chat` with `{"question": "...", "mode": "fast", "history": [{"question": "...", "answer": "..."}]}` retrieves passages from the collection for the question. `mode` is `fast` (the default) or `deep` (see [Deep mode](#deep-mode)); any other value is turned down with 422 before the model is asked. `history` is optional: the conversation so far, oldest first, which the client keeps (an `answer` may be empty for a refused or failed turn). Only its last `CHAT_HISTORY_TURNS` turns (default 6) go to the model, as earlier messages before the passages and the question; they help the model read a follow-up question, and claims are still checked against the retrieved passages only. When the nearest passage's `top_similarity` is below `REFUSAL_MIN_SIMILARITY` (default 0.3; a similarity equal to it is answered), the question is off topic: the answer is a `refusal` event with the closest passages, and the model is not asked. The exception is a strong keyword hit: when one passage holds at least `REFUSAL_MIN_KEYWORD_COVERAGE` of the question's words (default 1.0, every word; a coverage equal to it counts), the question is answered however low its similarity, so a question asked by an ID or a rare term the embedding model does not know still reaches the model. Such a question is still refused when no claim survives the check below. Otherwise the backend streams the answer as NDJSON, one event per line (`backend/app/answer.py`). The fast model is asked for JSON claims, each with the `chunk_id` of the passage it rests on and a `quote` copied from it, and each claim is sent as a `claim` event (with the document, page, chunk id and quote) as soon as it has streamed in whole. Every claim is checked before it is sent: it must cite a chunk that was retrieved, its quote must be at least 15 characters long and a substring of that chunk once both are normalised (unicode compatibility forms such as ligatures and non-breaking spaces, typographic quotes, case, hyphens between letters including line-break hyphens, and runs of whitespace), and the chunk's offsets must still resolve to the chunk's text on the stored page (`backend/app/citation.py`). A claim that fails any of these is dropped, never shown as cited, and the `quote` sent is the exact page text the quote matched. When no claim is left, a `refusal` event lists the passages instead. An answer that finished ends in a `done` event with the mode, the model's name and the cost in the price's currency, or `null` when no prices are set, the endpoint reported no token usage, or the model was not asked (never 0 as a stand-in).

A failed model call, a stream that breaks off, or a reply that is not in the claims schema ends the answer in an `error` event, also after some claims were sent, so a partial answer is never marked done. Retrieved text is passed to the model as data: each passage sits between markers holding a random value made for that request and found in no passage, so text in a PDF cannot close its marker and pass itself off as instructions.

### Deep mode

With `"mode": "deep"` the reasoning model (`LLM_REASONING_MODEL`) answers, for questions that need several searches, such as a comparison across documents (`backend/app/deep.py`). It first plans the question as sub-questions, at most `DEEP_MAX_SUBQUESTIONS` (default 5); blank and repeated ones are dropped, and a plan with none searches for the question itself. Each sub-question is retrieved on its own and sent as a `step` event with the sub-question (`question`) and the pages its retrieval found (`pages`, each a `document` and a `page`, once each), before any claim. The answer is then written from the passages of every sub-question together, with the same citation check as a fast answer. It is refused with the closest passages when every sub-question is off topic (by the same thresholds), and when no claim survives the check.

A deep answer's claims are sent only after the whole reply has arrived and checked out. Any failure ends the run in an `error` event with no claim: a failed plan or a plan outside its format, a sub-question whose retrieval fails, a stream that breaks off or is cut off, a reply outside the claims schema, more than `DEEP_MAX_TOKENS` tokens (default 50000) over the plan and the answer together, or more than `DEEP_TIMEOUT_SECONDS` (default 300) for the run. Tokens are counted as the endpoint reports them, or estimated at four characters a token when it reports none. The time limit is checked between steps and on each streamed piece; a single model call can still run for its own `LLM_TIMEOUT_SECONDS`. The `done` event names the reasoning model; its cost is `null` until reasoning-model prices can be set.

In the page, the "Deep mode" switch beside the question box asks in deep mode. While it is on, a note says before the run that deep mode uses the reasoning model and is slower and can cost more. Each `step` is listed under the answer as it arrives, in order, with the pages its search found (or "no pages found"), and the steps stay after the answer. When the run is done, its cost is shown with its mode and model, or "cost unknown" when the cost is `null`, never $0; a run that ends in an error shows the error and no cost.

## Citation viewer

Each claim in an answer has a citation chip (document, page). Clicking it opens that page of the stored PDF with pdf.js and highlights the quote. The quote is found by searching the page's pdf.js text layer, comparing letters and digits only (case, spacing, line breaks, hyphens, punctuation, ligatures and accents are ignored), because the stored page text the quote comes from may have been read by another extractor or by OCR. When the quote is not found, the page still opens, with a "Quote not located on this page." notice and the quote text beside it. If a claim carries `boxes` (rectangles in PDF points, origin at the page's bottom-left), those are drawn instead and no search is made. While the page opens, the viewer says so; a PDF that cannot be opened shows the reason with a Retry button.

## Themes

The page follows the system light or dark scheme (`prefers-color-scheme`). The "Dark theme" button overrides it; the choice is saved in `localStorage` (key `theme`) and kept on reload. The colours, type sizes, spacing, radii, shadows and focus ring all come from one set of design tokens in `frontend/src/theme.css`, and text passes WCAG AA contrast in both themes. The page fits 360 px phones to desktops without sideways scrolling: on a desktop the documents panel sits beside the chat; on a phone it stacks above it, the question box stays at the bottom of the screen and the PDF viewer fills the screen.

## Keyboard and screen readers

The page works from the keyboard alone. The first Tab reaches a "Skip to chat" link. The page has a header, a documents sidebar (`aside`) and the chat in `main`. Enter asks the question and Shift+Enter starts a new line. A streamed answer is read out as it arrives (`aria-live="polite"`). A citation opens the viewer with focus inside it. Tab and Shift+Tab stay in the viewer, and Escape closes it and returns focus to the chip. Every control shows the focus ring, and with `prefers-reduced-motion: reduce` nothing animates. `frontend/e2e/a11y.spec.ts` runs axe over the empty, loaded-chat and viewer-open screens in both themes and fails on any serious or critical violation. It also runs a keyboard-only upload, ask, open and close.

## Verify the stack

```bash
./scripts/compose-smoke.sh
```

Builds the full stack on throwaway ports, then checks the backend health endpoint, the pgvector extension, that the frontend serves its page, and that the frontend proxies `/api` to the backend. It needs Docker, `curl` and `jq`, and no model key: `docker-compose.smoke.yml` adds a fake OpenAI-compatible model (`scripts/fake-llm/`) and points the backend at it. With the fake, the smoke uploads the sample PDF, asks a question it answers, prints the cited answer and checks it cites the sample's page; then it stops the fake, asks again and checks the answer fails with "the model endpoint is unreachable". The fake matches words, it does not read: never point a real stack at it.

The frontend's nginx forwards `/api` to the host `llm-pdf-chatbot-backend`: the backend's network alias in `docker-compose.yml` and its container name in `docker-compose.proxy.yml`. A compose file of your own that runs the frontend image needs that name on the backend too.

## Private review deploy

`docker-compose.proxy.yml` runs the stack on proxy host behind Traefik and Authelia, with no published ports and no model server; [docs/deploy/reverse-proxy.md](docs/deploy/reverse-proxy.md) lists its settings, the backup and the rollback. `./scripts/proxy-compose-check.sh` checks the file without starting anything.

## Frontend development and tests

```bash
cd frontend
npm ci --include=dev
npm run dev        # Vite on :5173; /api is proxied to API_PROXY_TARGET (default http://localhost:8000)
npm test           # unit tests (vitest, jsdom)
npx playwright install chromium
npm run test:e2e   # browser tests (Playwright, chromium)
```

The browser tests start their own Vite dev server and never call a real backend: every `/api` request from the page is answered by a route fixture in `frontend/e2e/fixtures.ts`, and a request with no fixture fails the test. A streamed chat answer cannot be sent in pieces by a route fixture, so the chat test lets its request through the dev server's `/api` proxy to `frontend/e2e/fake-backend.mjs`, which streams an answer the test scripts and releases one part at a time. `frontend/e2e/fixtures/sample.pdf` is a committed two-page PDF for viewer tests.

## Releases

Changes are listed in [CHANGELOG.md](CHANGELOG.md). Pushing a `v*` tag (for example `v1.0.0`) runs `.github/workflows/release.yml`: it builds the backend and frontend images, checks that every check run on the tagged commit finished green (`scripts/check_runs_green.py`), and only then pushes both to `ghcr.io/samestrin/llm-pdf-chatbot-backend` and `ghcr.io/samestrin/llm-pdf-chatbot-frontend`, tagged with the version and `latest`. A pull request, or a manual run with `publish` left off, only builds the two images.

## Layout

- `backend/` — FastAPI API (Python, managed with uv)
- `frontend/` — React + TypeScript UI (Vite), served by nginx
- `db/init/` — database bootstrap (pgvector extension)
- `scripts/compose-smoke.sh` — end-to-end smoke test
- `scripts/fake-llm/` — the smoke's fake model (`docker-compose.smoke.yml` runs it)
- `scripts/proxy-compose-check.sh` — checks `docker-compose.proxy.yml` (no host port, no model server)
- `scripts/check_runs_green.py` — release gate: fails unless a commit's check runs are green

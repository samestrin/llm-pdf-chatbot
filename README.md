# llm-pdf-chatbot

Chat with your own PDFs and get answers you can trust: every answer cites the quote and page it came from, and says plainly when the answer is not in your documents. See [VISION.md](VISION.md) for what this project is and what it will become.

## Status

Early foundation. The stack — FastAPI backend, React frontend, Postgres with pgvector — runs end to end today; document upload, retrieval, and grounded answering are being built on top of it.

## Requirements

- Docker with the Compose plugin (`docker compose version`)

## Run it

```bash
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

Configuration is through environment variables (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `BACKEND_PORT`, `FRONTEND_PORT`); see `docker-compose.yml` for the defaults.

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

## Reading documents back

- `GET /collections` lists every collection with its documents, each with the same status fields under `status`.
- `GET /documents/{id}/pdf` serves the stored PDF inline (`application/pdf`, `X-Content-Type-Options: nosniff`). Only an id the server generated at upload works; any other id returns 404, and the client's file name is never used as a path.
- `GET /documents/{id}/pages/{page}` returns a page's stored text, which is the exact text that citation offsets index into. A page no method could read has `text` null and the reason in `error`.

## Verify the stack

```bash
./scripts/compose-smoke.sh
```

Builds the full stack on throwaway ports, then checks the backend health endpoint, the pgvector extension, that the frontend serves its page, and that the frontend proxies `/api` to the backend.

## Frontend development and tests

```bash
cd frontend
npm ci --include=dev
npm run dev        # Vite on :5173; /api is proxied to API_PROXY_TARGET (default http://localhost:8000)
npm test           # unit tests (vitest, jsdom)
npx playwright install chromium
npm run test:e2e   # browser tests (Playwright, chromium)
```

The browser tests start their own Vite dev server and never call a real backend: every `/api` request from the page is answered by a route fixture in `frontend/e2e/fixtures.ts`, and a request with no fixture fails the test. `frontend/e2e/fixtures/sample.pdf` is a committed two-page PDF for viewer tests.

## Layout

- `backend/` — FastAPI API (Python, managed with uv)
- `frontend/` — React + TypeScript UI (Vite), served by nginx
- `db/init/` — database bootstrap (pgvector extension)
- `scripts/compose-smoke.sh` — end-to-end smoke test

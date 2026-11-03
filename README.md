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

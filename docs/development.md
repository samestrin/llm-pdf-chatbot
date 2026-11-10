# Development

You need Docker with the Compose plugin, [uv](https://docs.astral.sh/uv/) with Python 3.11 or later for the backend, and Node.js 22 (the version CI uses) for the frontend.

## Layout

- `backend/`: FastAPI API (Python, managed with uv)
- `backend/evals/`: the evaluation (see [Evaluation](evaluation.md))
- `frontend/`: React + TypeScript UI (Vite), served by nginx
- `db/init/`: database bootstrap (pgvector extension)
- `scripts/compose-smoke.sh`: end-to-end smoke test
- `scripts/fake-llm/`: the smoke's fake model (`docker-compose.smoke.yml` runs it)
- `scripts/proxy-compose-check.sh`: checks `docker-compose.proxy.yml` (no host port, no model server)
- `scripts/check_runs_green.py`: release gate; fails unless a commit's check runs are green
- `scripts/changelog_section.py`: prints a version's `CHANGELOG.md` section for the release notes

## Backend tests

The backend tests run on SQLite and on Postgres with pgvector. The Postgres tests fail (they do not skip) unless `DATABASE_URL` points at one:

```bash
docker run -d --name pdf-chatbot-test-db -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=test pgvector/pgvector:pg17
cd backend
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/test uv run pytest
```

Each test works in a schema of its own and drops it afterwards. Lint and format checks, as CI runs them:

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
```

Some backend tests read the README: every environment variable it names in backticks must exist in the code and reach the backend through `docker-compose.yml` (except the `POSTGRES_*` and port variables), every `./scripts/...` and `npm run ...` it shows must exist, its image links must be local files, and its evaluation table must match the newest results file (see [Evaluation](evaluation.md#the-readme-table)).

## Frontend development and tests

```bash
cd frontend
npm ci --include=dev
npm run dev        # Vite on :5173; /api is proxied to API_PROXY_TARGET (default http://localhost:8000)
npm run lint       # eslint
npm test           # unit tests (vitest, jsdom)
npm run build      # type check and production build
npx playwright install chromium
npm run test:e2e   # browser tests (Playwright, chromium)
```

The browser tests start their own Vite dev server and never call a real backend: every `/api` request from the page is answered by a route fixture in `frontend/e2e/fixtures.ts`, and a request with no fixture fails the test. A streamed chat answer cannot be sent in pieces by a route fixture, so the chat test lets its request through the dev server's `/api` proxy to `frontend/e2e/fake-backend.mjs`, which streams an answer the test scripts and releases one part at a time. `frontend/e2e/fixtures/sample.pdf` is a committed two-page PDF for viewer tests. Every file under a `fixtures/` directory must be listed in [fixture-licences.md](fixture-licences.md).

## README images

The screenshots and the GIF in `docs/img/` (`upload.png`, `answer.png`, `citation.png`, `dark.png`, `phone.png`, `demo.gif`) are generated, not drawn by hand:

```bash
cd frontend
npm run capture:readme
```

It runs `frontend/e2e/capture/` against the same fixture-backed page and servers as the browser tests, and writes the files to `docs/img/`. `frontend/e2e/readme-images.spec.ts` checks each file exists and is at most 5 MB. Run it again when the page changes in a way the images show.

## Verify the stack

```bash
./scripts/compose-smoke.sh
```

Builds the full stack on throwaway ports, then checks the backend health endpoint, the pgvector extension, that the frontend serves its page, and that the frontend proxies `/api` to the backend. It needs Docker, `curl` and `jq`, and no model key: `docker-compose.smoke.yml` adds a fake OpenAI-compatible model (`scripts/fake-llm/`) and points the backend at it. With the fake, the smoke uploads the sample PDF, asks a question it answers, prints the cited answer and checks it cites the sample's page; then it stops the fake, asks again and checks the answer fails with "the model endpoint is unreachable". The fake matches words, it does not read: never point a real stack at it.

The frontend's nginx forwards `/api` to the host `llm-pdf-chatbot-backend`: the backend's network alias in `docker-compose.yml` and its container name in `docker-compose.proxy.yml`. A compose file of your own that runs the frontend image needs that name on the backend too.

## Private review deploy

`docker-compose.proxy.yml` runs the stack on the owner's private review host behind Traefik and Authelia, with no published ports and no model server; [deploy/reverse-proxy.md](deploy/reverse-proxy.md) lists its settings, the backup and the rollback. `./scripts/proxy-compose-check.sh` checks the file without starting anything.

## Releases

Changes are listed in [CHANGELOG.md](../CHANGELOG.md); add new entries under Unreleased. Pushing a `v*` tag (for example `v1.0.0`) runs `.github/workflows/release.yml`: it builds the backend and frontend images, checks that every check run on the tagged commit finished green (`scripts/check_runs_green.py`), and then creates a GitHub release for the tag with the version's CHANGELOG.md section as its notes (`scripts/changelog_section.py`). The images are pushed to `ghcr.io/samestrin/llm-pdf-chatbot-backend` and `ghcr.io/samestrin/llm-pdf-chatbot-frontend` (tagged with the version and `latest`) only when the repository variable `vars.PUBLISH_IMAGES` is `true`; it is unset by default, so a tag alone does not publish images. A pull request, or a manual run with `publish` left off, only builds the two images.

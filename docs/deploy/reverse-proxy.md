# Private review stack on proxy host: https://chat.example.com

A private copy of the chatbot on the proxy host host for the owner's review, behind Traefik and Authelia. Nothing answers without the owner's Authelia login, the API included. The gateway routes, the Authelia rule, the deploy target and the apply steps live in `fleet-deploy` (epic 3 and 3.1, `docs/review-stack.md`); this page is what this repo provides.

## What runs

`docker-compose.proxy.yml`, compose project `llm-pdf-chatbot`, folder `~/docker/llm-pdf-chatbot/` on proxy host:

| Service | Container | Image | Role |
| --- | --- | --- | --- |
| `db` | `llm-pdf-chatbot-db` | `pgvector/pgvector:pg17` | Postgres with pgvector, data in the volume `db-data` |
| `backend` | `llm-pdf-chatbot-backend` | `llm-pdf-chatbot-backend`, built on proxy host from `backend/` | FastAPI on :8000, health check `GET /health`; uploads in the volume `uploads` |
| `frontend` | `llm-pdf-chatbot-frontend` | `llm-pdf-chatbot-frontend`, built on proxy host from `frontend/` | nginx on :80: the built page, and `/api/*` forwarded to the backend with the `/api` prefix removed |

- **No container publishes a host port.** Traefik reaches the frontend by its container name over the external `proxy` network.
- **No model server.** There is no `ollama` here: the backend calls the fleet litellm with its own key.
- **No source bind-mount.** The code is inside the images; the only mounts are the two named volumes.
- Every service has `restart: unless-stopped` and a health check.
- Networks: `db` is on the project's own network only. `backend` and `frontend` are on it and on `proxy`: the frontend so Traefik reaches it, the backend so it reaches the ocr API.
- Names that are unique on `proxy`: other apps there have services called `backend` or `db` too, so the frontend forwards `/api` to `llm-pdf-chatbot-backend` (`frontend/nginx.conf`) and `DATABASE_URL` names the host `llm-pdf-chatbot-db`, never the bare service names.

`scripts/proxy-compose-check.sh` (run in CI by `compose-smoke.yml`) runs `docker compose -f docker-compose.proxy.yml config` with a placeholder `.env.proxy` and fails on a host port, a model server, a bind-mount, a missing `restart: unless-stopped` or health check, or a renamed `db`, `db-data` or project.

## Settings: `.env.proxy`

`~/docker/llm-pdf-chatbot/.env.proxy` exists **only on proxy host**, mode 600. It is never in git (`.gitignore`), a card, a log or a comment; the deploy receiver refuses an archive that carries one and never overwrites the one on the host. Both `db` and `backend` read it. Names only:

| Variable | What it holds |
| --- | --- |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | The database's user, a random password, and the database name. Set once, before the first start: Postgres reads them only when `db-data` is empty. |
| `DATABASE_URL` | `postgresql+psycopg://<user>:<password>@llm-pdf-chatbot-db:5432/<db>`, the same three values. |
| `LLM_BASE_URL` | The fleet litellm on the build host host, port 4000: `http://<build host host>:4000/v1`. |
| `LLM_API_KEY` | This app's own litellm key (`key_alias` `review-llm-pdf-chatbot`), made by the owner. |
| `LLM_FAST_MODEL`, `LLM_REASONING_MODEL`, `LLM_EMBEDDING_MODEL` | Model names litellm serves to that key. All three are required. |
| `OCR_URL` | The ocr API on `proxy`, `http://ocr:5000` (fleet-deploy `docs/review-stack.md`, "How the chatbot reaches ocr"). Empty means no OCR: scanned pages are then read from the PDF's text layer only. |

Optional, as in the README's [Configuration](../../README.md#configuration) table: `LLM_TIMEOUT_SECONDS`, `EMBED_BATCH_SIZE`, `OCR_TIMEOUT_SECONDS`, `MAX_UPLOAD_MB`, `MAX_PDF_PAGES`, `RERANK_URL`, `RERANK_MODEL`, `RERANK_API_KEY`, `RERANK_TIMEOUT_SECONDS`, `REFUSAL_MIN_SIMILARITY`, `REFUSAL_MIN_KEYWORD_COVERAGE`, `CHAT_HISTORY_TURNS`, `DEEP_MAX_SUBQUESTIONS`, `DEEP_MAX_TOKENS`, `DEEP_TIMEOUT_SECONDS`, `LLM_PRICE_FAST_PER_MTOK_IN`, `LLM_PRICE_FAST_PER_MTOK_OUT`. `UPLOAD_DIR` is set by the compose file. `BACKEND_PORT` and `FRONTEND_PORT` do nothing here: nothing is published.

## Traefik

One router for `Host(chat.example.com)`, with `authelia@docker`, to `http://llm-pdf-chatbot-frontend:80`. The frontend's nginx sends `/api/*` on to the backend, removes the `/api` prefix, streams answers unbuffered and accepts uploads up to 64 MB. A separate `/api` router straight to the backend would need a strip-prefix middleware, since the backend's routes have no `/api` prefix; the single router needs none.

Health through Traefik, after the login: `GET https://chat.example.com/api/health` answers `{"status":"ok"}`, or 503 when the database is down. Without a login it must answer a redirect to Authelia or 401/403, never app content.

## Deploy, backup and rollback

The `llm-pdf-chatbot` target (kind `proxy-compose`, `service: backend`) in fleet-deploy's `deploy-targets.yaml` deploys a commit from a "Deploy llm-pdf-chatbot <sha>?" card. Agents never deploy and never connect to proxy host.

Before anything changes, the receiver takes a `pg_dump` of the database through the service `db` into `~/docker/llm-pdf-chatbot/backups/pre-<sha>-<time>.sql.gz`, and stops if the dump fails or is empty. It finds the data by the volume `llm-pdf-chatbot_db-data`. **So keep the project name `llm-pdf-chatbot`, the service name `db` and the volume name `db-data`;** renaming any of them makes the next deploy back up nothing, or start a new empty database. Then it tags the running `llm-pdf-chatbot-backend` and `llm-pdf-chatbot-frontend` images `:rollback`, builds new ones, starts the stack and waits for the backend's health check; an unhealthy start puts the previous source and images back by itself.

To roll back by hand, on proxy host:

```bash
cd ~/docker/llm-pdf-chatbot && docker tag llm-pdf-chatbot-backend:rollback llm-pdf-chatbot-backend:latest && docker tag llm-pdf-chatbot-frontend:rollback llm-pdf-chatbot-frontend:latest && docker compose -f docker-compose.proxy.yml up -d
```

The backend creates its tables and the `vector` extension on start and never drops anything, so a rollback keeps the data. A database change is not undone by a rollback; the dump in `backups/` is the way back.

## First start

1. `mkdir -p ~/docker/llm-pdf-chatbot` and write `.env.proxy` there (mode 600) with the settings above.
2. The external network `proxy` already exists on proxy host (Traefik's).
3. The first deploy card builds the images and starts the stack; with no `db-data` volume yet the receiver skips the dump.

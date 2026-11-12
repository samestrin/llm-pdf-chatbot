# Running behind a reverse proxy with a login

`docker-compose.proxy.yml` runs the chatbot behind a reverse proxy that has an authentication layer in front of it. The app has no login of its own, so the proxy must do it: nothing should answer without a login, the API included. This page uses Traefik with a forward-auth middleware (for example Authelia) as the example; any reverse proxy that can require a login and forward to a container by name works the same way.

## What runs

`docker-compose.proxy.yml`, compose project `llm-pdf-chatbot`, in a folder of your choice on the host (`<app-folder>` below):

| Service | Container | Image | Role |
| --- | --- | --- | --- |
| `db` | `llm-pdf-chatbot-db` | `pgvector/pgvector:pg17` | Postgres with pgvector, data in the volume `db-data` |
| `backend` | `llm-pdf-chatbot-backend` | `llm-pdf-chatbot-backend`, built on the host from `backend/` | FastAPI on :8000, health check `GET /health`; uploads in the volume `uploads` |
| `frontend` | `llm-pdf-chatbot-frontend` | `llm-pdf-chatbot-frontend`, built on the host from `frontend/` | nginx on :80: the built page, and `/api/*` forwarded to the backend with the `/api` prefix removed |

- **No container publishes a host port.** The reverse proxy reaches the frontend by its container name over the external `proxy` network.
- **No model server.** There is no `ollama` here: the backend calls an OpenAI-compatible model gateway with its own key.
- **No source bind-mount.** The code is inside the images; the only mounts are the two named volumes.
- Every service has `restart: unless-stopped` and a health check.
- Networks: `db` is on the project's own network only. `backend` and `frontend` are on it and on `proxy`: the frontend so the reverse proxy reaches it, the backend so it can reach an OCR API there.
- Names that are unique on `proxy`: other apps there may have services called `backend` or `db` too, so the frontend forwards `/api` to `llm-pdf-chatbot-backend` (`frontend/nginx.conf`) and `DATABASE_URL` names the host `llm-pdf-chatbot-db`, never the bare service names.

`scripts/proxy-compose-check.sh` (run in CI by `compose-smoke.yml`) runs `docker compose -f docker-compose.proxy.yml config` with a placeholder `.env.proxy` and fails on a host port, a model server, a bind-mount, a missing `restart: unless-stopped` or health check, or a renamed `db`, `db-data` or project.

## Settings: `.env.proxy`

`<app-folder>/.env.proxy` exists only on the host, mode 600. It never goes in git (`.gitignore`), a log or an issue; your deploy tooling should never ship or overwrite it. Both `db` and `backend` read it:

| Variable | What it holds |
| --- | --- |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | The database's user, a random password, and the database name. Set once, before the first start: Postgres reads them only when `db-data` is empty. |
| `DATABASE_URL` | `postgresql+psycopg://<user>:<password>@llm-pdf-chatbot-db:5432/<db>`, the same three values. |
| `LLM_BASE_URL` | Your OpenAI-compatible model gateway, for example `http://<model-gateway>:4000/v1`. |
| `LLM_API_KEY` | This app's own key for that gateway. |
| `LLM_FAST_MODEL`, `LLM_REASONING_MODEL`, `LLM_EMBEDDING_MODEL` | Model names the gateway serves to that key. All three are required. |
| `OCR_URL` | An OCR API the backend can reach, for example `http://<ocr-host>:5000` on the `proxy` network. Empty means no OCR: scanned pages are then read from the PDF's text layer only. |

Optional, as in the README's [Configuration](../../README.md#configuration) table: `LLM_TIMEOUT_SECONDS`, `EMBED_BATCH_SIZE`, `OCR_TIMEOUT_SECONDS`, `MAX_UPLOAD_MB`, `MAX_PDF_PAGES`, `RERANK_URL`, `RERANK_MODEL`, `RERANK_API_KEY`, `RERANK_TIMEOUT_SECONDS`, `REFUSAL_MIN_SIMILARITY`, `REFUSAL_MIN_KEYWORD_COVERAGE`, `CHAT_HISTORY_TURNS`, `DEEP_MAX_SUBQUESTIONS`, `DEEP_MAX_TOKENS`, `DEEP_TIMEOUT_SECONDS`, `LLM_PRICE_FAST_PER_MTOK_IN`, `LLM_PRICE_FAST_PER_MTOK_OUT`. `UPLOAD_DIR` is set by the compose file. `BACKEND_PORT` and `FRONTEND_PORT` do nothing here: nothing is published.

## Example: Traefik with forward-auth

One router for `Host(chat.example.com)`, with your auth middleware (for example `auth@docker`, a forward-auth middleware pointing at Authelia), to `http://llm-pdf-chatbot-frontend:80`. The frontend's nginx sends `/api/*` on to the backend, removes the `/api` prefix, streams answers unbuffered and accepts uploads up to 64 MB. A separate `/api` router straight to the backend would need a strip-prefix middleware, since the backend's routes have no `/api` prefix; the single router needs none.

For example, as labels on the frontend service in an override file of your own:

```yaml
services:
  frontend:
    labels:
      - traefik.enable=true
      - traefik.docker.network=proxy
      - traefik.http.routers.pdfchat.rule=Host(`chat.example.com`)
      - traefik.http.routers.pdfchat.middlewares=auth@docker
      - traefik.http.services.pdfchat.loadbalancer.server.port=80
```

Health through the proxy, after the login: `GET https://chat.example.com/api/health` answers `{"status":"ok"}`, or 503 when the database is down. Without a login it must answer a redirect to the login page or 401/403, never app content.

## Deploy, backup and rollback

Whatever your deploy tooling is, it should take a backup before anything changes. For example, a `pg_dump` of the database through the service `db` into `<app-folder>/backups/pre-<sha>-<time>.sql.gz`, stopping if the dump fails or is empty. The data lives in the volume `llm-pdf-chatbot_db-data`. **So keep the project name `llm-pdf-chatbot`, the service name `db` and the volume name `db-data`;** renaming any of them makes the next deploy back up nothing, or start a new empty database. Then tag the running `llm-pdf-chatbot-backend` and `llm-pdf-chatbot-frontend` images `:rollback`, build new ones, start the stack and wait for the backend's health check; on an unhealthy start, put the previous images back.

A pre-deploy backup by hand:

```bash
cd <app-folder> && mkdir -p backups && docker compose -f docker-compose.proxy.yml exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "backups/pre-$(date +%Y%m%d-%H%M%S).sql.gz"
```

To roll back by hand, on the host:

```bash
cd <app-folder> && docker tag llm-pdf-chatbot-backend:rollback llm-pdf-chatbot-backend:latest && docker tag llm-pdf-chatbot-frontend:rollback llm-pdf-chatbot-frontend:latest && docker compose -f docker-compose.proxy.yml up -d
```

The backend creates its tables and the `vector` extension on start and never drops anything, so a rollback keeps the data. A database change is not undone by a rollback; the dump in `backups/` is the way back.

## First start

1. `mkdir -p <app-folder>`, put this repo's files there, and write `.env.proxy` (mode 600) with the settings above.
2. Create the external network `proxy` if your reverse proxy does not already have one (`docker network create proxy`), and attach the reverse proxy to it.
3. `docker compose -f docker-compose.proxy.yml up -d --build` builds the images and starts the stack; with no `db-data` volume yet there is nothing to back up.

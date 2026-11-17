# Configuration

Everything is an environment variable that `docker compose` reads from your shell or from `.env`. `.env.example` is the template: copy it with `cp .env.example .env` and edit it. An empty value means the setting is unset and its default applies.

## Settings passed by `docker-compose.yml`

| Name | Default | Meaning |
| --- | --- | --- |
| `LLM_BASE_URL` | `http://host.docker.internal:11434/v1` in `.env.example` | Base URL of an OpenAI-compatible endpoint. Required. |
| `LLM_API_KEY` | empty | API key; leave it empty for local servers. Secret. |
| `LLM_FAST_MODEL` | `llama3.2` in `.env.example` | Model for quick steps: fast-mode answers. Required. |
| `LLM_REASONING_MODEL` | `qwen3:14b` in `.env.example` | Model for deep-mode planning and answers. Required. |
| `LLM_EMBEDDING_MODEL` | `nomic-embed-text` in `.env.example` | Model for embeddings. Required. |
| `LLM_TIMEOUT_SECONDS` | 60 | Seconds to wait for a model call. |
| `EMBED_BATCH_SIZE` | 64 | Chunks per embedding call. |
| `OCR_URL` | empty (no OCR) | URL of an `llm-pdf-ocr-api` service for scanned pages. With no OCR, a scanned PDF with no text layer fails with a message that says it has no text layer (a scan or blank pages) and OCR is off. |
| `RERANK_URL` | empty (no rerank) | Full URL of a rerank endpoint (see [Reranking](#reranking)). |
| `RERANK_MODEL` | empty | Model name sent to the rerank endpoint when set. |
| `RERANK_API_KEY` | empty | Key sent to the rerank endpoint when set. Secret. |
| `RERANK_TIMEOUT_SECONDS` | 30 | Seconds to wait for the rerank endpoint. |
| `REFUSAL_MIN_SIMILARITY` | 0.3 | A question whose nearest passage has a lower cosine similarity (0 to 1) is refused as "not in your documents" without asking the model. |
| `REFUSAL_MIN_KEYWORD_COVERAGE` | 1.0 (every word) | A question below `REFUSAL_MIN_SIMILARITY` is still answered when one passage holds at least this share (0 to 1) of its words, as for an ID or a rare term the embedding model does not know. |
| `CHAT_HISTORY_TURNS` | 6 | How many earlier turns of the conversation go to the model with each question (0 sends none). |
| `DEEP_MAX_SUBQUESTIONS` | 5 | The most sub-questions the reasoning model plans in deep mode. |
| `DEEP_MAX_TOKENS` | 50000 | Tokens one deep run may use before it ends in an error. |
| `DEEP_TIMEOUT_SECONDS` | 300 | Seconds one deep run may take before it ends in an error. |
| `LLM_PRICE_FAST_PER_MTOK_IN` | empty (cost `null`) | Fast-model price per million input tokens, for the cost of each answer (see [Model prices](#model-prices)). |
| `LLM_PRICE_FAST_PER_MTOK_OUT` | empty (cost `null`) | Fast-model price per million output tokens. |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | `postgres`, `postgres`, `chatbot` | Database credentials. Compose builds the backend's `DATABASE_URL` from them. |
| `BACKEND_PORT`, `FRONTEND_PORT` | 8000, 8080 | Published host ports. |

The backend checks its settings when it uses them. A value outside its range (for example a negative timeout, a threshold above 1, or a `RERANK_URL` that is not an http(s) URL) is reported as an error naming the variable.

## Local models

The defaults in `.env.example` point at Ollama on the host (`http://host.docker.internal:11434/v1`) with `llama3.2`, `qwen3:14b` and `nomic-embed-text`; pull those models first. No key is needed.

`host.docker.internal` resolves under Docker Desktop. On Docker Engine on Linux it does not by default: add `extra_hosts: ["host.docker.internal:host-gateway"]` to the `backend` service in a compose override file, or set `LLM_BASE_URL` to an address the container can reach.

## Cloud models

Replace the local block in `.env` with the commented cloud block in `.env.example`: set `LLM_BASE_URL` to `https://api.openai.com/v1`, `LLM_API_KEY` to your key, and the three model names (the template uses `gpt-4o-mini`, `o4-mini` and `text-embedding-3-small`). Then run `docker compose up --build`. Any OpenAI-compatible endpoint works.

## OCR

Set `OCR_URL` to a running `llm-pdf-ocr-api`; the backend sends it the whole PDF at `POST /v1/extract` and falls back to the PDF's own text layer for any page OCR did not read.

## Reranking

Reranking is optional and runs on a separate HTTP service; no model is added to the backend image. Set `RERANK_URL` to the full URL of a rerank endpoint in the Cohere/Jina shape: it receives `{"query", "documents", "model"}` and answers `{"results": [{"index", "relevance_score"}]}`. A bare list of `{"index", "score"}`, as Text Embeddings Inference returns, works too. `RERANK_MODEL` and `RERANK_API_KEY` are sent when set, and `RERANK_TIMEOUT_SECONDS` defaults to 30. Without `RERANK_URL` the fusion order is used as is. When the endpoint fails (an error status, a timeout, or an answer that does not score every passage), retrieval raises an error; it never falls back to the fusion order without saying so.

## Model prices

To have the cost of a fast-model call worked out, set `LLM_PRICE_FAST_PER_MTOK_IN` and `LLM_PRICE_FAST_PER_MTOK_OUT` (prices per million input and output tokens, both or neither). Without them, or when the endpoint reports no token usage, the cost is `null`, never 0. Set both to `0` for a free local model. Reasoning-model prices cannot be set yet, so a deep answer's cost is always `null`.

## Settings the backend reads but `docker-compose.yml` does not pass

These apply when you run the backend outside compose (for example `uv run uvicorn app.main:app` in `backend/`), or in a compose file of your own that passes them.

| Name | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./app.db` | SQLAlchemy database URL. Compose sets it to its Postgres service. Retrieval, and so chat, needs Postgres with pgvector. |
| `UPLOAD_DIR` | `./data/uploads` | Where stored PDFs are kept. Compose sets it to `/app/data/uploads` on the `uploads` volume. |
| `MAX_UPLOAD_MB` | 50 | Largest upload, in MB; a larger file returns 413. The frontend's nginx accepts request bodies up to 64 MB. |
| `MAX_PDF_PAGES` | 500 | Most pages in an uploaded PDF; more returns 422. |
| `OCR_TIMEOUT_SECONDS` | 120 | Seconds to wait for the OCR service. |
| `PUBLIC_DEMO` | empty (off) | Set it to `true`, `1`, `yes` or `on` to run the public demo: every model action then needs a demo code or the visitor's own key, the `LLM_*` endpoint is not used for a visitor's chat or upload, and the session, Origin and cap settings below apply. Empty or `false` keeps the single shared mode that uses `LLM_*` for everyone. See [Sessions in a public demo](#sessions-in-a-public-demo). |
| `SESSION_SECRET` | empty (a new secret at each start) | Signs the public demo's session cookie. Set it to a long random value so visitors keep their session across a restart and every backend process accepts the same cookie. Secret. |
| `DEMO_GATEWAY_URL` | empty (demo codes are off) | Base URL of the gateway that demo codes are sent to, for example `http://litellm:4000/v1`. The `demo` preset asks it for the models `demo-chat` and `demo-embedding`, so the gateway must serve those two names. |
| `DEMO_UPLOAD_TTL_MINUTES` | 60 | In a public demo, minutes after which a visitor's collection is deleted with its documents, chunks and files. A whole number above 0. |
| `DEMO_ORIGIN` | empty (the request's own host) | In a public demo, the app's origin as the browser sees it, for example `https://pdfchat.example.com`; several may be given, comma-separated. An upload or delete from any other origin is refused. When empty, the `Origin` header's host must be the request's `Host` (the port is not compared). Set it when a proxy changes the `Host` header, as Vite's dev server does. |
| `DEMO_SESSION_MAX_COLLECTIONS` | 5 | In a public demo, how many collections one session may keep. A whole number above 0. |
| `DEMO_SESSION_MAX_UPLOAD_MB` | 100 | In a public demo, how many megabytes of PDFs one session's documents may hold in all. A whole number above 0. |

## Sessions in a public demo

With `PUBLIC_DEMO=true` each browser gets a signed, HttpOnly session cookie (`pdfchat_session`; `Secure` when the request came over HTTPS or with `X-Forwarded-Proto: https`). There is no account behind it. A collection belongs to the session that made it, so two visitors can each have a collection called `papers`, and every collection, document, page, PDF, delete and chat route answers 404 for another session's collection or document, exactly as for one that does not exist. A job deletes a visitor's collection `DEMO_UPLOAD_TTL_MINUTES` after it was made (at startup and then every minute).

Writes from another site: the browser sends the session cookie with any request to the app, so in a public demo an upload, a document delete or a collection delete is refused (403 `This request came from another site and was refused`) when its `Origin` is not the app's, or when a browser sent it without an `Origin` (it sends `Sec-Fetch-Site`). A request with neither header, from a script or `curl`, holds no visitor's cookie and goes through. Reads and chat are not checked.

Per-session caps: an upload that would give a session more than `DEMO_SESSION_MAX_COLLECTIONS` collections, or more than `DEMO_SESSION_MAX_UPLOAD_MB` of documents, is refused (409, for example `This session has reached its limit of 5 collections; delete one to make another`) and stores nothing. Deleting a document, or the TTL, frees the room. The sample does not count.

The sample: a collection uploaded with `PUBLIC_DEMO` off belongs to the shared owner, outside every visitor session. In the demo every session sees it, reads it and asks it, and none can upload to it or delete it (403 `The sample collection is read-only`); it never expires. Upload it with a preset's headers (see [Model credentials in a public demo](api.md#model-credentials-in-a-public-demo)) so visitors can ask it with that preset.

A collection records the preset and embedding model it was embedded with (`embedding_preset` and `embedding_model` in `GET /collections`). A question or an upload with another preset or embedding model is refused with 409: "This collection was embedded with <model>; switch back to <preset> to ask about it". A collection embedded with the server's own `LLM_*` model records no preset and that model's name (`LLM_EMBEDDING_MODEL`); it is asked only without preset headers, and a question with a preset or demo code is refused ("switch back to the server's own model"). Upload the sample with a preset's headers, or visitors cannot ask it.

**A fresh, empty database is required.** There are no migrations: the backend refuses to start on a database made before collections had owners (no `collections.session_id`), and says so. This holds for every install, not only the public demo: point `DATABASE_URL` at a new database (or drop the old one, for compose `docker compose down -v`) and upload the documents again.

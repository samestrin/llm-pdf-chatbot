# HTTP API

The backend is a FastAPI app on port 8000 (`BACKEND_PORT`). Its interactive reference is at [`/api/docs`](http://localhost:8080/api/docs) on the running app (the frontend's address, `FRONTEND_PORT`, default 8080). The browser reaches the backend only at same-origin `/api/...`: the frontend's nginx strips `/api` and forwards to the backend, so no CORS is needed.

The app serves the OpenAPI spec at `/api/openapi.json`; the same spec is committed as [`docs/openapi.json`](openapi.json). After changing an endpoint, regenerate it with `cd backend && uv run python ../scripts/export_openapi.py`; a backend test fails when the committed file is stale.

Through the frontend, nginx tells the backend its `/api` prefix (`X-Forwarded-Prefix`), so the spec served at `/api/openapi.json` names `/api` as its server and the reference's "Test Request" and code samples call `/api/...`. Served directly on port 8000, the spec names no server and they call the backend's own origin.

## Behaviour the spec does not carry

### Ingestion and stored files

- Document status reports chunk progress in `chunks_total` and `chunks_embedded`, updated after the pages are read and after every embedding batch. A document can be `ready` with failed pages; each failure has its page, reason and OCR error under `failures`.
- If no page could be read, the document is `failed`. When OCR is off (`OCR_URL` unset) and no page has a text layer, the exact reason is `This PDF has no text layer (a scan or blank pages); OCR is off (set OCR_URL to read scans)`; otherwise it is `no page could be read`.
- The client's file name never touches the file system. It is kept for display only; the stored PDF uses the server-generated document id.

### Chat requests and history

- `history` is the conversation so far, oldest first, and the client keeps and sends it. An answer may be empty for a refused or failed turn. Only the last `CHAT_HISTORY_TURNS` turns (default 6) go to the model. History helps the model understand a follow-up question, but citations are checked against the retrieved passages only.
- A whitespace-only question is rejected with 422. An invalid `mode` is also rejected with 422, before any model call.

### NDJSON chat stream

OpenAPI cannot express the sequence of JSON objects streamed by `POST /api/collections/{collection}/chat`. The response is `application/x-ndjson`, one event per line. These are all the event types in `frontend/src/api.ts`'s `ChatEvent`, with one example line each:

```ndjson
{"type":"step","question":"What is the notice period?","pages":[{"document":{"id":"doc-1","filename":"lease.pdf"},"page":3}]}
{"type":"claim","text":"The notice period is 30 days.","document":{"id":"doc-1","filename":"lease.pdf"},"page":3,"chunk_id":"42","quote":"The notice period is 30 days."}
{"type":"refusal","passages":[{"document":{"id":"doc-1","filename":"lease.pdf"},"page":3,"chunk_id":"42","text":"The notice period is 30 days."}]}
{"type":"error","message":"the answer failed: the model endpoint is unreachable (check LLM_BASE_URL)"}
{"type":"done","mode":"fast","model":"llama3.2","cost":null}
```

- `step` is deep mode only: one per sub-question, before any claim, with each retrieved page once.
- `claim` is one claim that passed the citation check; `quote` is the exact matching page text.
- `refusal` returns the closest passages when the question is off topic or no claim survives the citation check.
- `error` reports a failure, including after claims were sent, so a partial answer is never marked done.
- `done` ends a completed answer. `cost` is `null` when it is unknown, never `0` as a stand-in.

How the backend decides between an answer and a refusal, and how each claim is checked, is in [How it works](how-it-works.md#answers).

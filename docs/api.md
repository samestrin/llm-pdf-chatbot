# HTTP API

The backend is a FastAPI app on port 8000 (`BACKEND_PORT`). Its interactive reference is at [`/api/docs`](http://localhost:8080/api/docs) on the running app (the frontend's address, `FRONTEND_PORT`, default 8080). The browser reaches the backend only at same-origin `/api/...`: the frontend's nginx strips `/api` and forwards to the backend, so no CORS is needed.

The app serves the OpenAPI spec at `/api/openapi.json`; the same spec is committed as [`docs/openapi.json`](openapi.json). After changing an endpoint, regenerate it with `cd backend && uv run python ../scripts/export_openapi.py`; a backend test fails when the committed file is stale.

Through the frontend, nginx tells the backend its `/api` prefix (`X-Forwarded-Prefix`), so the spec served at `/api/openapi.json` names `/api` as its server and the reference's "Test Request" and code samples call `/api/...`. Served directly on port 8000, the spec names no server and they call the backend's own origin.

## Behaviour the spec does not carry

### Model credentials in a public demo

Every collection, document and chat route accepts the visitor's model credentials as request headers. A request names a preset from `GET /presets`, never a base URL: the server reaches only that preset's own address, or `DEMO_GATEWAY_URL` for the demo preset.

| Header | Meaning |
| --- | --- |
| `X-Model-Preset` | A preset id from `GET /presets` (`openai`, `openrouter`, `synthetic`, `alibaba` or `demo`). Required with `X-Model-Key`; optional with `X-Demo-Code`, which implies `demo`. |
| `X-Model-Key` | The visitor's own key for that preset. It is sent to the preset's provider for that request only: never stored in a row or a file, and never written to a log line or an error body. |
| `X-Demo-Code` | A demo code for the `demo` preset. The app sends it to `DEMO_GATEWAY_URL` as the API key; it is handled like a key. |

A request with none of these headers uses the server's `LLM_*` endpoint, as in the single shared mode. With `PUBLIC_DEMO=true` the server's endpoint is not used for a visitor's chat or upload: a request without credentials fails with `PUBLIC_DEMO is on: a model call needs a demo code or a preset key`, in the chat stream's `error` event or as the document's failure reason.

A bad header combination is a 400 whose detail names headers, never a value: `X-Model-Key` and `X-Demo-Code` together; `X-Demo-Code` without the `demo` preset; `X-Model-Key` without `X-Model-Preset`; a preset without its key; the `demo` preset without a code; an unknown preset; or a key or code that is not in the expected shape. A demo code when `DEMO_GATEWAY_URL` is unset is 503 (`demo codes are not available here`).

`GET /presets` tells the page what it may offer. It holds no base URL, gateway address or key; the `demo` preset is listed only when `DEMO_GATEWAY_URL` is set, and a preset with no embedding model has `"embedding_model": null`:

```json
{
  "public_demo": true,
  "upload_ttl_minutes": 60,
  "presets": [
    {"id": "openai", "label": "OpenAI", "kind": "key", "chat_model": "gpt-5-nano", "embedding_model": "text-embedding-3-small"},
    {"id": "demo", "label": "Demo code", "kind": "demo", "chat_model": "demo-chat", "embedding_model": "demo-embedding"}
  ]
}
```

Outside a public demo `public_demo` is `false` and `upload_ttl_minutes` is `null`. The page's key panel, the notice and what is stored in the browser are described in [Configuration](configuration.md#sessions-in-a-public-demo).

### Rejections in a public demo

| Status | When |
| --- | --- |
| 400 | A bad model-header combination, as above. |
| 403 | An upload or either delete whose `Origin` is not the app's (`DEMO_ORIGIN`, or else the request's own host), or a browser request (one with `Sec-Fetch-Site`) that sent no `Origin`: `This request came from another site and was refused`; or a change to the read-only sample: `The sample collection is read-only`. Nothing is changed. |
| 404 | A collection, document, page or PDF of another session, exactly as for one that does not exist. |
| 409 | The upload past a session cap (`DEMO_SESSION_MAX_COLLECTIONS` or `DEMO_SESSION_MAX_UPLOAD_MB`), until a delete frees room; or a question or upload with a preset or embedding model other than the collection's: `This collection was embedded with <model>; switch back to <preset> to ask about it`. |

Reads and chat are not checked against `Origin`; only the session cookie writes are. Without `PUBLIC_DEMO` there is no Origin check, no cap and no sample rule.

### Ingestion and stored files

- Document status reports chunk progress in `chunks_total` and `chunks_embedded`, updated after the pages are read and after every embedding batch. A document can be `ready` with failed pages; each failure has its page, reason and OCR error under `failures`.
- If no page could be read, the document is `failed`. When OCR is off (`OCR_URL` unset) and no page has a text layer, the exact reason is `This PDF has no text layer (a scan or blank pages); OCR is off (set OCR_URL to read scans)`; otherwise it is `no page could be read`.
- A document embedded with a visitor's key or demo code that fails on it shows the same plain reason as the chat stream's `error` event (`The key was rejected by <provider>`, `This demo code is used up or expired`, `<provider> is rate limiting this key. Try again shortly.` or `this preset has no embedding model`), never the body the model endpoint sent.
- The client's file name never touches the file system. It is kept for display only; the stored PDF uses the server-generated document id.

### Chat requests and history

- `history` is the conversation so far, oldest first, and the client keeps and sends it. An answer may be empty for a refused or failed turn. Only the last `CHAT_HISTORY_TURNS` turns (default 6) go to the model. History helps the model understand a follow-up question, but citations are checked against the retrieved passages only.
- A whitespace-only question is rejected with 422. An invalid `mode` is also rejected with 422, before any model call.

### NDJSON chat stream

`POST /api/collections/{collection}/chat` answers with `application/x-ndjson`: one JSON event per line. The spec gives the schema of one line, `ChatEvent`, a `oneOf` on `type` over `StepEvent`, `ClaimEvent`, `RefusalEvent`, `ErrorEvent` and `DoneEvent`; OpenAPI 3.1 has no schema for the stream as a whole, so the order of the events is described here. These are all the event types, the same as `frontend/src/api.ts`'s `ChatEvent`, with one example line each:

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
- `error` reports a failure, including after claims were sent, so a partial answer is never marked done. A failure of the visitor's key or demo code says the plain sentence alone: `The key was rejected by <provider>`, `This demo code is used up or expired`, `<provider> is rate limiting this key. Try again shortly.` or `this preset has no embedding model`. No body the model endpoint sent is shown; it stays in the server's log.
- `done` ends a completed answer. `cost` is `null` when it is unknown, never `0` as a stand-in.

How the backend decides between an answer and a refusal, and how each claim is checked, is in [How it works](how-it-works.md#answers).

# HTTP API

The backend is a FastAPI app on port 8000 (`BACKEND_PORT`). Its interactive docs are at <http://localhost:8000/docs>. The browser reaches the backend only at same-origin `/api/...`: the frontend's nginx strips `/api` and forwards to the backend, so no CORS is needed. `http://localhost:8080/api/health` is the same as `http://localhost:8000/health`.

There is no auth: the app is local and single-user.

The spec the app serves at `/openapi.json` is also committed at [`openapi.json`](openapi.json) in this folder. After changing an endpoint, regenerate it with `cd backend && uv run python ../scripts/export_openapi.py`; a backend test fails when the committed file is stale.

## Endpoints

| Method and path | What it does | Status codes |
| --- | --- | --- |
| `GET /health` | Checks the database: `{"status":"ok"}`. | 200; 503 `database unavailable` |
| `POST /collections/{name}/documents` | Uploads a PDF (multipart field `file`) into the collection, creating it if it is new, and queues it for ingestion. Returns the document's `id`, `collection`, `filename`, `pages`, `size_bytes` and `status` (`queued`). | 201; 413 over the size limit; 422 not a PDF, unreadable, no pages, or over the page limit |
| `GET /collections` | Lists every collection with its documents, each with the same status fields as the status endpoint under `status`. | 200 |
| `DELETE /collections/{name}` | Deletes an empty collection, such as one left from before empty collections were removed. | 204 when it was empty; 404 unknown name; 409 `collection is not empty` (nothing is deleted) |
| `GET /documents/{id}/status` | A document's ingestion state and progress. | 200; 404 |
| `GET /documents/{id}/pdf` | The stored PDF, inline. | 200; 404 |
| `GET /documents/{id}/pages/{page}` | One page's stored text (pages count from 1). | 200; 404 unknown document or page outside it; 409 page not read yet |
| `DELETE /documents/{id}` | Deletes a document, its file and everything derived from it. | 204; 404 |
| `POST /collections/{name}/chat` | Answers a question from the collection, streamed as NDJSON. | 200 (failures arrive as an `error` event in the stream); 422 invalid body |

## Reading documents back

- `GET /collections` lists every collection with its documents, each with the same status fields under `status`.
- `DELETE /documents/{id}` deletes a document, its file and everything derived from it. When it was the last document of its collection, the collection is removed too, so the list never keeps an empty collection behind.
- `DELETE /collections/{name}` deletes an empty collection: 204 when it was empty, 404 for an unknown name, and 409 `collection is not empty` when it still has documents (nothing is deleted then).
- `GET /documents/{id}/pdf` serves the stored PDF inline (`application/pdf`, `X-Content-Type-Options: nosniff`). Only an id the server generated at upload works; any other id returns 404, and the client's file name is never used as a path.
- `GET /documents/{id}/pages/{page}` returns a page's stored text, which is the exact text that citation offsets index into, with the method that read it. A page no method could read has `text` null and the reason in `error`.

## Document status

`GET /documents/{id}/status` reports a document's state (`queued`, `processing`, `ready`, or `failed` with the reason) and progress (pages read, chunks embedded), each page with the method that read it (`text_layer`, or `tesseract` from OCR, or `pymupdf` from the text-layer fallback) and why OCR failed on it, and under `failures` every page no method could read, with the reason. A document can be `ready` with some pages failed.

A document no page of which could be read fails. When OCR is off (`OCR_URL` unset) and no page has a text layer, the reason is `This PDF has no text layer (a scan or blank pages); OCR is off (set OCR_URL to read scans)`; otherwise it is `no page could be read`, with each page's reason and OCR error under `failures`.

## Chat

```bash
curl -N http://localhost:8000/collections/default/chat \
  -H 'Content-Type: application/json' \
  -d '{"question": "What is the notice period?", "mode": "fast", "history": [{"question": "...", "answer": "..."}]}'
```

- `question` is required and must not be blank.
- `mode` is `fast` (the default) or `deep` (see [Deep mode](how-it-works.md#deep-mode)); any other value is turned down with 422 before the model is asked.
- `history` is optional: the conversation so far, oldest first, which the client keeps (an `answer` may be empty for a refused or failed turn). Only its last `CHAT_HISTORY_TURNS` turns (default 6) go to the model, as earlier messages before the passages and the question; they help the model read a follow-up question, and claims are still checked against the retrieved passages only.

The answer streams as NDJSON (`application/x-ndjson`), one event per line, each with a `type`. A `document` is `{"id", "filename"}`.

| Event | Fields | When |
| --- | --- | --- |
| `step` | `question`, `pages` (each a `document` and a `page`, once each) | Deep mode only: one per sub-question, before any claim. |
| `claim` | `text`, `document`, `page`, `chunk_id`, `quote` | One per claim that passed the citation check. The `quote` is the exact page text the quote matched. |
| `refusal` | `passages`: the closest passages, each with `document`, `page`, `chunk_id` and `text` | The question is off topic, or no claim survived the check. |
| `done` | `mode`, `model`, `cost` | The answer finished. `cost` is in the price's currency, or `null` when no prices are set, the endpoint reported no token usage, or the model was not asked (never 0 as a stand-in). |
| `error` | `message`, such as `the answer failed: the model endpoint is unreachable (check LLM_BASE_URL)` | A failure, also after some claims were sent, so a partial answer is never marked done. |

How the backend decides between an answer and a refusal, and how each claim is checked, is in [How it works](how-it-works.md#answers).

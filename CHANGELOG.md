# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/). Add new entries under Unreleased.

## [Unreleased]

### Changed

- Buttons use the shared demo shell's sizes: primary and secondary buttons are 42 px tall (now an explicit control-height token, not inherited padding), and small buttons are 32 px tall, growing to a 44 px touch target below 768 px. The distinct neutral disabled style is unchanged, and text-like buttons (document names, Delete, citation chips) keep their inline size.
- The page uses the shared demo shell's header and width: a sticky 56 px header bar across the page with the logo and the name **PDF Chat** (a link home), a `Main` nav with **Chat** and **API** (`/api/docs`) from 768 px up, and the theme button at the right. The title "LLM PDF Chatbot" and a one-line tagline now open the page under the bar, with "Backend is down" below them when the health check fails. The content is 76rem (1216 px) wide, with its 1rem side gutter outside that width (it was 1184 px).
- In a public demo the key entry is the shared demo shell's open **Model access** panel under the title, not a header button that opened it. The public notice ("Public demo: do not upload private files. …") is its first line, in muted text, and no longer a bar in the Documents sidebar. Then, in one row from 768 px up and stacked full width below: **Provider**, the key field (labelled **Demo code**, or **<provider> API key**), **Remember on this device** and **Clear**. The panel saves as you type, so there is no Save or Close button. A question, upload or sample asked for with no key scrolls the whole panel into view, its notice clear of the header bar, and moves the focus to the key field. The keys are stored as before: one per provider, in local storage with Remember and session storage without, and Clear wipes both.

### Fixed

- The demo key panel remembers one key per provider: switching the provider no longer forgets the demo code (or another provider's key). The browser now stores `{preset, keys, remember}` — each provider's key under its own id, in local storage with "remember on this device" and session storage without — and switching back to a provider shows its saved key again. A key is still sent only with its own preset's headers (`X-Demo-Code` for the demo, `X-Model-Key` otherwise), Clear still wipes every stored key from both stores, and a key saved by an older version is kept under its old provider on first load.

## [1.2.0] - 2026-11-26

### Added

- Suggested questions for the sample: `frontend/public/samples/questions.json` lists questions the sample PDF answers, shown as chips above the question box when the active collection holds it; a click asks. A backend test asks each one through the chat route and checks it is answered with a citation from the sample (by the eval's fake model).
- View a document: clicking a document's name opens the PDF in the viewer at its first page, on a phone full screen. The viewer now pages with **Previous page** and **Next page**; a citation still opens at its page with the quote highlighted.
- `DEMO_EMBEDDING_KEY` and `DEMO_SESSION_MAX_EMBED_CALLS` (default 200 per `DEMO_UPLOAD_TTL_MINUTES` window) for the public demo, and `embedding_model` in `GET /presets`: the model the server embeds with in a demo.
- Reasoning-model prices: set `LLM_PRICE_REASONING_PER_MTOK_IN` and `LLM_PRICE_REASONING_PER_MTOK_OUT` (both or neither) to price deep-mode planning and answers per million input and output tokens.
- Evaluation floors in `backend/evals/floors.json`: `--subset` now fails and names each metric below its floor, and `--subset --deep` evaluates every subset question in deep mode against the `deep` floors without writing a results file.

### Changed

- A deep answer now shows its cost when both reasoning-model prices are set: the plan's cost plus the answer's. It remains `null`, never 0, when either cost is unknown.
- In a public demo the server embeds every upload and every question itself, with `DEMO_EMBEDDING_KEY` at `DEMO_GATEWAY_URL` on `demo-embedding`; the visitor's key or demo code is used only for the chat model that writes the answer. The read-only sample now answers with any preset, every preset can upload (Synthetic too), and no demo code is needed. A session past its embedding cap is told so plainly ("This session has reached its limit of <n> embedding calls in <minutes> minutes; try again later"). In a demo a collection embedded with another model is refused with 409 ("…; this server embeds with demo-embedding, so it cannot ask about it"); the old "switch back to <preset>" refusal now applies outside a demo only. **Deploying needs `DEMO_EMBEDDING_KEY` set**, or no upload is read and no question answered in the demo.

### Removed

- The "<preset> has no embedding model, so it cannot take uploads" path in the page: the server embeds for every preset. The key panel now says the key only writes the answers and names the server's embedding model.

### Fixed

- The read-only sample no longer shows a Delete button; the API already refuses to change or delete it (403, the sample stays).

## [1.1.0] - 2026-11-18

### Added

- Model presets for the public demo: `backend/app/presets.json` lists the providers a visitor may pick (OpenAI, OpenRouter, Synthetic, Alibaba and the demo gateway), and `GET /presets` gives the page their ids, labels and models, never a base URL, gateway or key. The documents and chat routes check the `X-Model-Preset`, `X-Model-Key` and `X-Demo-Code` headers: an unknown preset or a bad combination is a 400 that echoes no header value, and no base URL is taken from a request. With `PUBLIC_DEMO=true` the `LLM_*` endpoint is not used for a model call, so chat and uploads fail until a later change sends them the visitor's credentials. A model client takes its own key out of any error body an endpoint sends back.
- Chat and uploads use the visitor's credentials: a request with a preset and key (or a demo code) embeds its upload and answers its questions with that preset's models, and the key goes to the ingest worker in memory only, never into a row, a file or a log line. With `PUBLIC_DEMO=true`, deep mode asks the preset's one chat model and the rerank endpoint (`RERANK_URL`) is not used; on restart, documents left `queued` or `processing` are marked failed ("upload the document again") instead of queued again, since their keys are gone. Without `PUBLIC_DEMO` a request with no credentials works as before.
- Session-scoped data in the public demo: with `PUBLIC_DEMO=true` each browser gets a signed, HttpOnly session cookie (no account; signed with `SESSION_SECRET`), a collection belongs to the session that made it, and every collection, document, page, PDF, delete and chat route returns 404 for another session's collection or document. Two sessions can each have a collection of the same name. A visitor's collection is deleted with its documents, chunks and files `DEMO_UPLOAD_TTL_MINUTES` (default 60) after it was made. Collections uploaded with `PUBLIC_DEMO` off are the read-only sample: every session can read and ask them, none can change or delete them (403). Each collection records its `embedding_preset` and `embedding_model` (shown by `GET /collections` with `read_only`), and a question or upload with another preset or embedding model is refused (409): "This collection was embedded with <model>; switch back to <preset> to ask about it". A collection embedded with the server's own model is asked only without preset headers. **Upgrading needs a fresh, empty database:** there is no migration, and the backend refuses to start on a database from before this change (see `docs/configuration.md`).

- Cross-site and abuse limits in the public demo: with `PUBLIC_DEMO=true`, an upload or either delete is refused (403 "This request came from another site and was refused") when its `Origin` is not the app's (`DEMO_ORIGIN`, or else the request's own host) or a browser sent none, and nothing is changed. Each session may keep `DEMO_SESSION_MAX_COLLECTIONS` collections (default 5) holding `DEMO_SESSION_MAX_UPLOAD_MB` of documents (default 100); the upload past either cap is refused (409) with a plain message until a delete frees room. Without `PUBLIC_DEMO` there is no check and no cap.
- Plain errors for a visitor's key or demo code failure, in the chat stream's `error` event (fast and deep) and in the document status's failure reason: a rejected key says "The key was rejected by <provider>", a used-up or expired demo code says "This demo code is used up or expired", a provider rate limit says "<provider> is rate limiting this key. Try again shortly.", and a preset with no embedding model says "this preset has no embedding model". No body the model endpoint sent reaches the client; it stays in the server's log with the key redacted.
- The key panel in the page for the public demo: with `PUBLIC_DEMO=true` the page loads `GET /presets` and shows a provider dropdown, a field for the visitor's own key or a demo code, a "remember on this device" box and a Clear button. No question is asked and no upload starts without a key or code — the panel opens instead, and the question or file is kept for the retry. The key lives in session storage (local storage only with "remember"), goes to the backend only in the `X-Model-Preset` and `X-Model-Key` or `X-Demo-Code` request headers, and Clear wipes it from both stores. The documents panel shows the public notice ("Public demo: do not upload private files. Uploads are deleted after N minutes.", N from the new `upload_ttl_minutes` field of `GET /presets`), explains that uploading needs a code or key, and hides the upload control for a preset with no embedding model, saying why. Each collection shows the embedding model it was built with, and the backend's plain refusal of a mismatched preset is shown as it is. Without `PUBLIC_DEMO` the page is unchanged.

### Changed

- The `demo` preset asks the demo gateway for `demo-chat` and `demo-embedding`, the only model names the gateway serves, instead of `gpt-5-nano` and `text-embedding-3-small`, which it refused with 400. A collection uploaded with a demo code before this change was embedded under the old name and is refused (409) until it is uploaded again. The other presets are unchanged.
- The docs cover the public demo: the README has a [Public demo](README.md#public-demo) section, `docs/configuration.md` documents `PUBLIC_DEMO` and the session, Origin, TTL and cap settings, and `docs/api.md` documents the `X-Model-Preset`, `X-Model-Key` and `X-Demo-Code` headers, `GET /presets`, and the 400, 403, 404 and 409 rejections of a public demo.
- The OpenAPI spec (`/api/openapi.json`, `docs/openapi.json`) gives a response schema for the four operations that had none: the chat stream's line is `ChatEvent`, a `oneOf` on `type` over `StepEvent`, `ClaimEvent`, `RefusalEvent`, `ErrorEvent` and `DoneEvent`; the PDF download is `application/pdf` binary; the two deletes are 204 with no body, and their 404 (and the collection's 409) is `{"detail": "..."}`. The backend builds each chat event from the same models, and what the API sends is unchanged. `docs/api.md` names the event schemas.

## [1.0.1] - 2026-11-12

### Added

- The OpenAPI spec is committed at `docs/openapi.json`, written by `scripts/export_openapi.py` (`cd backend && uv run python ../scripts/export_openapi.py`). A backend test compares the file with the app's schema and fails when it is stale, saying to rerun the export; CI's existing `uv run pytest` step runs it.

### Changed

- The release version is 1.0.1 in the backend (`pyproject.toml`, `uv.lock`) and the frontend (`package.json`, `package-lock.json`), so the version the OpenAPI spec shows is the version released.
- The interactive API reference at `/docs` (`/api/docs` through the frontend) is Scalar instead of Swagger UI, from a script vendored in the backend at a pinned version. The page loads nothing from outside — no CDN, no web fonts, no request proxy, no telemetry — and "try it" sends its request to this origin. It wears the app's accent in light and dark and the app's favicon; `/redoc` is gone. The spec at `/openapi.json` is unchanged.
- The API guide at `docs/api.md` now points to the interactive reference and committed OpenAPI spec for endpoint details, and documents the behaviour the spec cannot express: ingestion progress and failures, chat history, and the NDJSON chat stream with examples.
- The README demo GIF is a real session, recorded by `npm run capture:live` against a running instance (LIVE_URL) with real models: eight positive-reinforcement dog-training guides, a fast answer, a deep answer with its plan, and a refused question. The capture stops if a claim recommends an aversive training method or if the frames differ in size; the PDFs are not in the repo.

### Fixed

- The OpenAPI spec (`/api/openapi.json`) reports the package version from `backend/pyproject.toml` instead of FastAPI's default `0.1.0`.
- The API reference at `/api/docs` calls the API at `/api/...`: the spec served through the frontend names `/api` as its server (nginx and Vite's dev proxy send `X-Forwarded-Prefix: /api`), so "Test Request" and the code samples no longer reach the web page at the site root. Served directly on port 8000 the spec names no server, as before, and `docs/openapi.json` and the spec's paths are unchanged.
- The API reference no longer scrolls sideways on a 375 px screen: a long request-body schema name wraps and code samples scroll inside their own box.
- The API reference's links, buttons and active sidebar item are the chatbot's blue in light and dark, instead of Scalar's default black and grey.
- The OpenAPI spec declares the chat answer as `application/x-ndjson`, the stream the route sends, instead of `application/json`; `docs/openapi.json` is regenerated.
- `docs/api.md` links the API reference at the running app's address (http://localhost:8080/api/docs) instead of a root-relative `/api/docs`, which GitHub opened as github.com/api/docs.
- `npm run capture:readme` writes only the README's PNG screenshots, from the mocked page, and no longer overwrites the live demo GIF; `npm run capture:live` records the GIF. The README and `docs/development.md` say which command makes which.
- The demo capture's screen for aversive training advice reads each claim per clause: a warning in one clause ("Avoid choke chains") no longer excuses advice in the next ("use a prong collar"), and a negation covers only what follows it.

## [1.0.0] - 2026-11-11

The 2026 rebuild: a FastAPI backend, a React frontend and Postgres with pgvector, replacing the Create React App prototype.

### Added

- Upload PDFs into named collections, with PDF, size and page-count checks; deleting a collection or document deletes its pages, chunks and file.
- Ingestion in the background: each page is read from its text layer, or by an OCR service for scanned pages, then chunked by headings and paragraphs and indexed for vector and keyword search. A status API reports pages read, the method per page and failures with reasons.
- Hybrid retrieval: vector and keyword search merged by reciprocal rank fusion, with an optional rerank service.
- Streamed answers made of claims, each citing a chunk and a quote. A citation is kept only when the quote is in its chunk and the chunk resolves to its page text.
- Refusals: a question with no close enough passage is refused, and the closest passages are shown.
- Chat history, the model used and the cost of each answer.
- Deep mode: the question is split into planned sub-questions shown as steps, then answered by the reasoning model within a time and token budget.
- The web page: documents panel with upload progress and ingestion status, chat with citation chips, a PDF viewer that opens the cited page and highlights the quote, light and dark themes, and a phone layout.
- Any OpenAI-compatible model provider, set by environment variables.
- Docker Compose for the whole stack, with a smoke test.
- First-run empty states in the documents panel and the chat that say what to do next; a spinner while the documents load and while a cited page opens; a Retry button on a failed document load, upload, answer and PDF viewer open (an answer is asked again in the collection and mode it was first asked in); and a note on each document still being read, and in the chat for the chosen collection, that answers leave it out until it is ready.
- A "Try a sample PDF" button in the empty documents panel: one click uploads a small sample PDF, written for this repo and served by the frontend, into the selected collection.
- Keyboard and screen-reader support: a "Skip to chat" link; header, documents sidebar and main landmarks; streamed answers read out as they arrive; Enter asks and Shift+Enter starts a new line; the PDF viewer keeps focus inside it, closes on Escape and returns focus to the citation chip; and nothing animates when reduced motion is asked for.
- A logo mark in the header, and a "Thinking" indicator with bouncing dots shown while an answer has not started yet.
- A "Try it" section in the README, and refreshed screenshots including a dark-mode and a phone-width image.
- An evaluation: 40 questions (10 unanswerable) over 5 freely licensed PDFs, scored for accuracy, citation precision, refusal accuracy and cost.
- A release workflow: a `v*` tag builds the backend and frontend images and, once the tagged commit's checks are green, creates a GitHub release with the version's CHANGELOG.md section as its notes; the images are pushed to the GitHub Container Registry only when the repository variable `PUBLISH_IMAGES` is `true` (unset by default).
- The compose smoke test (`scripts/compose-smoke.sh`) now asks a question end to end: a fake OpenAI-compatible model (`scripts/fake-llm/`, added by `docker-compose.smoke.yml`) answers it, the smoke checks the streamed answer cites the sample PDF's page, then stops the fake and checks the answer fails as "the model endpoint is unreachable". `docker-compose.yml` is unchanged.
- `docker-compose.proxy.yml` and `docs/deploy/reverse-proxy.md`: run behind a reverse proxy with a login (for example Traefik with a forward-auth middleware), with no published ports, no model server and no source mounts, settings from an `.env.proxy` kept only on the host; `scripts/proxy-compose-check.sh` checks it in CI.

### Changed

- When the model endpoint cannot be reached, a chat answer and a failed document upload now say so and name `LLM_BASE_URL` as the setting to check.
- A refreshed page look built on one set of design tokens (colour, type, spacing, radius, shadow, focus ring) in `theme.css`; light and dark text pass WCAG AA contrast. On a desktop the documents panel is a sidebar beside the chat; on a phone it stacks above the chat, the question box stays at the bottom of the screen, and the PDF viewer fills the screen.
- Deleting a collection's last document removes the collection, and `DELETE /collections/{name}` removes an empty one (409 when it still has documents); the page offers "Remove empty collection" for one.
- One collection picker in the documents panel drives uploads, the sample button, dropped files and the chat, with "New collection…" for a new name; the chat's own collection list is gone.
- Drop a PDF on the documents panel to upload it; a file chosen with the file button uploads at once, so there is no separate Upload button.
- Long file names stay on one line with an ellipsis and the whole name in a tooltip; Delete is a small button that asks first, and each document shows its page count once.
- A scanned PDF with no text layer, read with OCR off, fails with "This PDF has no text layer (a scan or blank pages); OCR is off (set OCR_URL to read scans)" instead of "no page could be read".
- Citation chips show the page only (`p. 3`); the document is named once per answer, or listed as numbered sources (`2 · p. 1`) when an answer cites several. A line built from the claims ("Found in lease.pdf, pp. 1–3") opens each answer that has claims.
- The header mentions the backend only when it is down, and an answer shows its cost only when it is known.
- Buttons have a solid primary colour in both themes and a distinct grey disabled style, instead of a faded one; the small Delete button in the documents list gets the same disabled surface instead of staying transparent.

[Unreleased]: https://github.com/samestrin/llm-pdf-chatbot/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/samestrin/llm-pdf-chatbot/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/samestrin/llm-pdf-chatbot/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/samestrin/llm-pdf-chatbot/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/samestrin/llm-pdf-chatbot/releases/tag/v1.0.0
- The frontend's nginx forwards `/api` to `llm-pdf-chatbot-backend` instead of `backend`, a name no other app shares on a common Docker network; `docker-compose.yml` gives the backend that alias. A compose file of your own that runs the frontend image must give its backend that name.

### Fixed

- The answer's lead line reads `Found in lease.pdf, p. 2` with the comma flush against the name, and a one-page document reads `Ready: 1 page` (also in the reading and partly-read lines), not `1 pages`.

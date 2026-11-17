# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/). Add new entries under Unreleased.

## [Unreleased]

### Added

- Model presets for the public demo: `backend/app/presets.json` lists the providers a visitor may pick (OpenAI, OpenRouter, Synthetic, Alibaba and the demo gateway), and `GET /presets` gives the page their ids, labels and models, never a base URL, gateway or key. The documents and chat routes check the `X-Model-Preset`, `X-Model-Key` and `X-Demo-Code` headers: an unknown preset or a bad combination is a 400 that echoes no header value, and no base URL is taken from a request. With `PUBLIC_DEMO=true` the `LLM_*` endpoint is not used for a model call, so chat and uploads fail until a later change sends them the visitor's credentials. A model client takes its own key out of any error body an endpoint sends back.
- Chat and uploads use the visitor's credentials: a request with a preset and key (or a demo code) embeds its upload and answers its questions with that preset's models, and the key goes to the ingest worker in memory only, never into a row, a file or a log line. With `PUBLIC_DEMO=true`, deep mode asks the preset's one chat model and the rerank endpoint (`RERANK_URL`) is not used; on restart, documents left `queued` or `processing` are marked failed ("upload the document again") instead of queued again, since their keys are gone. Without `PUBLIC_DEMO` a request with no credentials works as before.
- Session-scoped data in the public demo: with `PUBLIC_DEMO=true` each browser gets a signed, HttpOnly session cookie (no account; signed with `SESSION_SECRET`), a collection belongs to the session that made it, and every collection, document, page, PDF, delete and chat route returns 404 for another session's collection or document. Two sessions can each have a collection of the same name. A visitor's collection is deleted with its documents, chunks and files `DEMO_UPLOAD_TTL_MINUTES` (default 60) after it was made. Collections uploaded with `PUBLIC_DEMO` off are the read-only sample: every session can read and ask them, none can change or delete them (403). Each collection records its `embedding_preset` and `embedding_model` (shown by `GET /collections` with `read_only`), and a question or upload with another preset or embedding model is refused (409): "This collection was embedded with <model>; switch back to <preset> to ask about it". **Upgrading needs a fresh, empty database:** there is no migration, and the backend refuses to start on a database from before this change (see `docs/configuration.md`).

### Changed

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

[Unreleased]: https://github.com/samestrin/llm-pdf-chatbot/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/samestrin/llm-pdf-chatbot/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/samestrin/llm-pdf-chatbot/releases/tag/v1.0.0
- The frontend's nginx forwards `/api` to `llm-pdf-chatbot-backend` instead of `backend`, a name no other app shares on a common Docker network; `docker-compose.yml` gives the backend that alias. A compose file of your own that runs the frontend image must give its backend that name.

### Fixed

- The answer's lead line reads `Found in lease.pdf, p. 2` with the comma flush against the name, and a one-page document reads `Ready: 1 page` (also in the reading and partly-read lines), not `1 pages`.

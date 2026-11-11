# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/). Add new entries under Unreleased.

## [Unreleased]

### Added

- The compose smoke test (`scripts/compose-smoke.sh`) now asks a question end to end: a fake OpenAI-compatible model (`scripts/fake-llm/`, added by `docker-compose.smoke.yml`) answers it, the smoke checks the streamed answer cites the sample PDF's page, then stops the fake and checks the answer fails as "the model endpoint is unreachable". `docker-compose.yml` is unchanged.
- `docker-compose.proxy.yml` and `docs/deploy/reverse-proxy.md`: a private review stack at `chat.example.com` behind Traefik and Authelia, with no published ports, no model server and no source mounts, settings from an `.env.proxy` kept only on the host; `scripts/proxy-compose-check.sh` checks it in CI.

### Changed

- The frontend's nginx forwards `/api` to `llm-pdf-chatbot-backend` instead of `backend`, a name no other app shares on a common Docker network; `docker-compose.yml` gives the backend that alias. A compose file of your own that runs the frontend image must give its backend that name.

### Fixed

- The answer's lead line reads `Found in lease.pdf, p. 2` with the comma flush against the name, and a one-page document reads `Ready: 1 page` (also in the reading and partly-read lines), not `1 pages`.

## [1.0.0] - 2026-11-06

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

[Unreleased]: https://github.com/samestrin/llm-pdf-chatbot/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/samestrin/llm-pdf-chatbot/releases/tag/v1.0.0

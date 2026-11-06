# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/). Add new entries under Unreleased.

## [Unreleased]

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
- An evaluation: 40 questions (10 unanswerable) over 5 freely licensed PDFs, scored for accuracy, citation precision, refusal accuracy and cost.
- A release workflow: a `v*` tag builds the backend and frontend images and, once the tagged commit's checks are green, publishes both to the GitHub Container Registry.

[Unreleased]: https://github.com/samestrin/llm-pdf-chatbot/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/samestrin/llm-pdf-chatbot/releases/tag/v1.0.0

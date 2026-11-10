# Evaluation

The eval asks the 40 questions in `backend/evals/questions.json` about the five PDFs in `backend/evals/pdfs/` (sources and licences in `backend/evals/SOURCES.md`) through the chat API in fast mode. The latest committed score table is in the README under [Performance](../README.md#performance).

## Measures

- **Answer accuracy**: of the answerable questions, the share whose answer holds every expected key fact. With `--judge`, the fast model judges each answer against the key facts instead (for full runs, never in CI).
- **Citation precision**: of the claims given for answerable questions, the share that cite the expected PDF on the expected page.
- **Refusal accuracy**: of all questions, the share refused exactly when they cannot be answered from the PDFs.
- **Cost**: the answers' total cost, or `unknown` when any answer that reached the model has no cost (no fast-model prices, or no token usage reported; see [Model prices](configuration.md#model-prices)); never 0 for a missing price. A question refused before any model call costs 0.

## Running it

```bash
cd backend && uv run python -m evals.run            # full run: the models and OCR_URL from the environment
cd backend && uv run python -m evals.run --judge    # the same, with the model judging accuracy
cd backend && uv run python -m evals.run --subset   # what CI runs: no scanned PDF, a fake model
```

Each run needs `DATABASE_URL` pointing at Postgres with pgvector (see [Backend tests](../README.md#backend-tests)); it creates its tables in a schema of its own and drops it at the end, so the database's own documents are never touched. It prints the score table and writes `backend/evals/results/<date>.json` (today's UTC date; a second run that day replaces it) with every question's result.

The full run needs `OCR_URL`: without it the scanned PDF does not ingest and the run stops.

The `--subset` run answers with a fake model that quotes the passage sentence sharing most words with the question, so its scores show the pipeline runs end to end, not how good a real model is; its results file says `"run": "subset"`.

The owner makes the full runs and commits their results files.

## The README table

The table in the README is the newest results file in `backend/evals/results/`, and the backend tests fail when it is not. After committing a results file, run `cd backend && uv run python -m evals.readme --write` (`uv run python -m evals.readme` alone only checks). A local `--subset` run writes a newer results file too, so delete it before running the tests.

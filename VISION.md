# Vision

`llm-pdf-chatbot` exists so that a person can ask questions of their own PDFs and trust the answer, because every answer shows exactly where it came from.
It serves people and teams with documents (contracts, manuals, reports, policies) who want answers, not a search result list.
It turns a folder of PDFs into a chat that answers with quotes and page links, and says plainly when the answer is not in the documents.
It owns exactly one thing: grounded question answering over a person's own PDFs.

## Every answer is grounded

Every claim in an answer cites a quote and a page from the uploaded documents.
Clicking a citation opens the PDF at that page with the quote highlighted.
When the documents do not hold the answer, it says so, and it does not fill the gap from the model's own knowledge.
An answer that cannot be tied to a source is a failure, not a style choice.

## Nothing reports success when it did not succeed

This rule sits above every other rule here.
A PDF that failed to read is reported as failed, with the pages affected, never silently skipped.
A failed model call is an error the person sees, never an empty or invented answer.

## Fast by default, careful when asked

A quick answer uses a fast model and the best matching passages.
A "deep research" mode uses a reasoning model for questions that span several documents, such as comparing two contracts, and shows the steps it took.
The person can see which mode and which model answered, and what it cost.

## Any model, no lock-in

Models and embeddings come from any OpenAI-compatible endpoint, local or cloud, set from the environment.
No vendor is special, and nothing requires a GPU.
PDF reading goes through `llm-pdf-ocr-api`, so scanned pages and tables work too.

## Measured, not claimed

A committed question set with known answers is scored on every change to retrieval or prompts.
The README shows the score, and the share of questions it correctly refuses.

## Scope

It is not a general chatbot, a document editor, a search engine for the web, or a document management system.
It does not train or fine-tune models on a person's documents.
It runs with one `docker compose up`, keeps documents on the host it runs on, and deletes them when the person deletes them.
It is a portfolio demo of how this should be built, so clarity of the code and the README matter as much as features.

A change aligns when it makes answers more grounded, refusals more honest, citations easier to check, or the project easier to run and understand.
A change should be resisted when it lets an answer go uncited, hides a failure, ties the project to one vendor, or adds features that are not question answering over PDFs.

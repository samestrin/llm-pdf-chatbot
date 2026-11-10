"""A fake OpenAI-compatible model for the compose smoke (`scripts/compose-smoke.sh`).

No key, no network, the same answer every run, and the Python standard library only.
It serves what the backend's `app.llm.LLMClient` calls:

- `POST /v1/embeddings`: each input as its hashed content words, so a question and the
  passage that shares its words are close (cosine well above the backend's refusal
  threshold) and unrelated texts are not.
- `POST /v1/chat/completions`, streamed (server-sent events) or not: it reads each
  passage and its `chunk_id` from the `<<<PASSAGE <nonce> chunk_id=N>>>` blocks of the
  last message and the question after `Question: `, and answers
  `{"claims": [...]}` with the one sentence that shares the most words with the
  question, quoted exactly from its passage. When no sentence shares at least
  `MIN_SHARED_WORDS`, the claims are empty and the backend refuses.
- `GET /health`: 200, for the compose health check.

`response_format` is accepted and ignored: the reply is always that JSON.

    python server.py            # listens on 0.0.0.0:$FAKE_LLM_PORT (default 8080)
"""

import json
import math
import os
import re
import sys
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIMENSIONS = 256
MIN_SHARED_WORDS = 2
# The backend drops a quote shorter than this (`app.citation.MIN_QUOTE_CHARS`).
MIN_QUOTE_CHARS = 15
# The reply is streamed in pieces this long, so the backend reads a real stream.
PIECE_CHARS = 16

_STOP_WORDS = frozenset(
    [
        "the",
        "and",
        "for",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "this",
        "that",
        "with",
        "from",
        "what",
        "which",
        "who",
        "how",
        "does",
        "did",
        "its",
        "not",
        "but",
        "all",
        "any",
        "can",
        "may",
        "into",
        "than",
        "then",
        "when",
        "where",
        "why",
        "about",
        "their",
        "there",
        "these",
        "those",
        "they",
        "them",
        "much",
        "many",
        "more",
        "most",
    ]
)
_PASSAGE = re.compile(r"<<<PASSAGE (\w+) chunk_id=(\d+)>>>\n(.*?)\n<<<END PASSAGE \1>>>", re.DOTALL)
_SENTENCE = re.compile(r"[^.!?]+[.!?]?")


def words(text: str) -> set[str]:
    """The text's content words: lower case, three characters or more, no stop words."""
    return {w for w in re.findall(r"[a-z0-9%$]{3,}", text.lower()) if w not in _STOP_WORDS}


def embed(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for w in words(text):
        vector[zlib.crc32(w.encode()) % DIMENSIONS] += 1.0
    # A small constant part, so no text (not even an empty one) is a zero vector.
    vector.append(0.01)
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector]


def claims(prompt: str) -> list[dict[str, str]]:
    """The claims answering the question in `prompt` from its passages."""
    question = prompt.rsplit("\n\nQuestion: ", 1)[-1]
    asked = words(question)
    best: tuple[int, str, str] | None = None
    for match in _PASSAGE.finditer(prompt):
        chunk_id, text = match.group(2), match.group(3)
        for found in _SENTENCE.finditer(text):
            sentence = found.group().strip()
            if len(sentence) < MIN_QUOTE_CHARS:
                continue
            shared = len(asked & words(sentence))
            if best is None or shared > best[0]:
                best = (shared, chunk_id, sentence)
    if best is None or best[0] < MIN_SHARED_WORDS:
        return []
    _, chunk_id, sentence = best
    return [{"text": " ".join(sentence.split()), "chunk_id": chunk_id, "quote": sentence}]


def reply(messages: list) -> str:
    last = messages[-1] if messages else {}
    prompt = last.get("content", "") if isinstance(last, dict) else ""
    return json.dumps({"claims": claims(prompt if isinstance(prompt, str) else "")})


def _usage(messages: list, text: str) -> dict[str, int]:
    prompt = sum(len(str(m.get("content", "")).split()) for m in messages if isinstance(m, dict))
    completion = len(text.split())
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "fake-llm"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": {"message": f"no route {self.path}"}})

    def do_POST(self) -> None:
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        except ValueError:
            self._json(400, {"error": {"message": "body is not JSON"}})
            return
        if not isinstance(body, dict):
            self._json(400, {"error": {"message": "body is not an object"}})
        elif self.path.endswith("/embeddings"):
            self._embeddings(body)
        elif self.path.endswith("/chat/completions"):
            self._chat(body)
        else:
            self._json(404, {"error": {"message": f"no route {self.path}"}})

    def _embeddings(self, body: dict) -> None:
        texts = body.get("input")
        if isinstance(texts, str):
            texts = [texts]
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            self._json(400, {"error": {"message": "input is not a string or a list of them"}})
            return
        data = [
            {"object": "embedding", "index": i, "embedding": embed(t)} for i, t in enumerate(texts)
        ]
        self._json(200, {"object": "list", "model": body.get("model"), "data": data})

    def _chat(self, body: dict) -> None:
        messages = body.get("messages")
        if not isinstance(messages, list):
            self._json(400, {"error": {"message": "messages is not a list"}})
            return
        model = body.get("model")
        text = reply(messages)
        usage = _usage(messages, text)
        if not body.get("stream"):
            choice = {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
            self._json(
                200,
                {
                    "object": "chat.completion",
                    "model": model,
                    "choices": [choice],
                    "usage": usage,
                },
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        pieces = [text[i : i + PIECE_CHARS] for i in range(0, len(text), PIECE_CHARS)]
        for piece in pieces:
            self._event(self._chunk(model, {"content": piece}, None))
        self._event(self._chunk(model, {}, "stop"))
        self._event(
            {"object": "chat.completion.chunk", "model": model, "choices": [], "usage": usage}
        )
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    @staticmethod
    def _chunk(model: object, delta: dict, finish_reason: str | None) -> dict:
        choice = {"index": 0, "delta": delta, "finish_reason": finish_reason}
        return {"object": "chat.completion.chunk", "model": model, "choices": [choice]}

    def _event(self, data: dict) -> None:
        self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def _json(self, status: int, data: dict) -> None:
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    server = make_server("0.0.0.0", int(os.environ.get("FAKE_LLM_PORT", "8080")))
    print(f"fake-llm listening on :{server.server_address[1]}", file=sys.stderr, flush=True)
    server.serve_forever()

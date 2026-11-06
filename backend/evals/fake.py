"""A fake model for the CI subset of the eval: no endpoint, no key, the same answer every run.

`FakeEmbedder` embeds a text as its hashed words. `FakeModel` stands in for
`app.llm.LLMClient` in `app.answer.chat_events`: it reads the passages and the question
out of the prompt and answers with the one sentence that shares the most words with the
question, quoted from its passage; when no sentence shares at least `MIN_SHARED_WORDS`,
it answers with no claims, which the chat API turns into a refusal. Its cost is unknown.

Its scores say the pipeline runs end to end; they say nothing of a real model's.
"""

import json
import re
import zlib
from collections.abc import Iterator
from typing import Any

from app.citation import MIN_QUOTE_CHARS, normalise
from app.llm import ChatModel, ChatResult, ChatStream, LLMConfig

DIMENSIONS = 256
MIN_SHARED_WORDS = 2
MODEL = "fake-extractive"

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
        "whom",
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
        "must",
        "will",
        "shall",
        "should",
        "would",
        "could",
        "into",
        "than",
        "then",
        "when",
        "where",
        "why",
        "under",
        "over",
        "about",
        "after",
        "before",
        "their",
        "there",
        "these",
        "those",
        "they",
        "them",
        "being",
        "been",
        "such",
        "each",
        "other",
        "more",
        "most",
        "many",
        "much",
    ]
)
_PASSAGE = re.compile(r"<<<PASSAGE (\w+) chunk_id=(\d+)>>>\n(.*?)\n<<<END PASSAGE \1>>>", re.DOTALL)
_SENTENCE = re.compile(r"[^.!?]+[.!?]?")


def words(text: str) -> set[str]:
    """The text's content words: lower case, three characters or more, no stop words."""
    return {w for w in re.findall(r"[a-z0-9%$]{3,}", text.lower()) if w not in _STOP_WORDS}


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            v = [0.0] * DIMENSIONS
            for w in words(text):
                v[zlib.crc32(w.encode()) % DIMENSIONS] += 1.0
            # A small constant part, so no text (not even an empty one) is a zero vector.
            vectors.append([*v, 0.01])
        return vectors


class FakeModel(FakeEmbedder):
    config = LLMConfig(
        base_url="fake:",
        api_key=None,
        fast_model=MODEL,
        reasoning_model=MODEL,
        embedding_model="fake-hashed-words",
    )

    def chat_stream(
        self, messages: list[dict[str, Any]], model: ChatModel = "fast", json_schema: Any = None
    ) -> ChatStream:
        return ChatStream(self._pieces(messages))

    def close(self) -> None:
        pass

    def answer(self, question: str, passages: list[tuple[str, str]]) -> list[dict[str, str]]:
        """The claims for `question` from `passages`, each a (chunk_id, text)."""
        asked = words(question)
        best: tuple[int, str, str] | None = None
        for chunk_id, text in passages:
            for match in _SENTENCE.finditer(text):
                sentence = match.group().strip()
                if len(normalise(sentence)) < MIN_QUOTE_CHARS:
                    continue
                shared = len(asked & words(sentence))
                if best is None or shared > best[0]:
                    best = (shared, chunk_id, sentence)
        if best is None or best[0] < MIN_SHARED_WORDS:
            return []
        _, chunk_id, sentence = best
        return [{"text": " ".join(sentence.split()), "chunk_id": chunk_id, "quote": sentence}]

    def _pieces(self, messages: list[dict[str, Any]]) -> Iterator[str | ChatResult]:
        user = messages[-1]["content"]
        question = user.rsplit("\n\nQuestion: ", 1)[-1]
        passages = [(m.group(2), m.group(3)) for m in _PASSAGE.finditer(user)]
        reply = {"claims": self.answer(question, passages)}
        text = json.dumps(reply)
        yield text
        yield ChatResult(text=text, model=MODEL, usage=None, cost=None, data=reply)

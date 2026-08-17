"""Bounded embedding batches and an offline deterministic embedding seam.

Embedding is the step that costs money and rate limit, so it is the step with limits
on it. Providers bound a request twice, by item count and by total tokens, and a
planner that respects only one of them works fine until the day a corpus of long
documents arrives. Both limits are enforced here, and a single chunk that cannot fit
is refused loudly rather than truncated quietly, because truncation is a data loss
that looks like a successful run.

`estimate_tokens` is deliberately conservative and deliberately not a tokenizer. In
production, count with the tokenizer of the model being called: an estimate that runs
low turns into provider errors mid-batch, which is the expensive place to find out.

The cache key is the model plus the content hash, in that order of importance. Content
alone would serve a vector from one model to a query embedded by another, which fails
in the worst way available: not an error, just quietly meaningless similarity scores.
`DeterministicEmbedder` folds its dimensions into its model name for that reason.

What this embedder is not is semantic. It reproduces the shape of the real thing,
batching, caching, dimensions, and cost accounting, so the data-engineering lesson runs
offline and identically on every machine. Similarity between its vectors means only
that two texts share words.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from .models import Chunk


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    chunks: tuple[Chunk, ...]
    estimated_tokens: int


def estimate_tokens(text: str) -> int:
    """A conservative teaching estimate; production uses the model tokenizer."""

    return max(1, (len(text.encode("utf-8")) + 2) // 3)


def plan_batches(
    chunks: tuple[Chunk, ...], *, max_items: int = 64, max_tokens: int = 8_000
) -> tuple[EmbeddingBatch, ...]:
    if max_items < 1 or max_tokens < 1:
        raise ValueError("batch limits must be positive")
    batches: list[EmbeddingBatch] = []
    current: list[Chunk] = []
    current_tokens = 0
    for chunk in chunks:
        tokens = estimate_tokens(chunk.text)
        if tokens > max_tokens:
            raise ValueError(f"chunk {chunk.chunk_id} exceeds the batch token limit")
        if current and (len(current) >= max_items or current_tokens + tokens > max_tokens):
            batches.append(EmbeddingBatch(tuple(current), current_tokens))
            current = []
            current_tokens = 0
        current.append(chunk)
        current_tokens += tokens
    if current:
        batches.append(EmbeddingBatch(tuple(current), current_tokens))
    return tuple(batches)


class DeterministicEmbedder:
    """A local replacement that preserves batching and cache behavior, not semantics."""

    def __init__(self, dimensions: int = 16) -> None:
        if dimensions < 2:
            raise ValueError("dimensions must be at least 2")
        self.dimensions = dimensions
        self.model = f"deterministic-hash-v1-{dimensions}d"
        self.calls = 0

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        return tuple(self._one(text) for text in texts)

    def _one(self, text: str) -> tuple[float, ...]:
        values = [0.0] * self.dimensions
        for token in text.casefold().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for index in range(self.dimensions):
                values[index] += (digest[index] - 127.5) / 127.5
        magnitude = math.sqrt(sum(value * value for value in values)) or 1.0
        return tuple(round(value / magnitude, 8) for value in values)

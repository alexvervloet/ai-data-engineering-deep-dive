"""Bounded embedding batches and an offline deterministic embedding seam."""

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

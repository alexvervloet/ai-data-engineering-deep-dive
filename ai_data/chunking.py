"""Deterministic chunking with explicit provenance and ACL propagation.

The RAG dive treats chunking as a retrieval-quality knob: how big, how much overlap,
where to cut. All of that still applies. This module is about the other half, the part
that only shows up once the corpus changes.

Chunking has to be a pure function of the text and the settings. If it is not, then
re-running it on an unchanged document produces different chunk IDs, every one of them
misses the embedding cache, the old rows do not match the new ones, and an ordinary
resync bills a full re-embed of a corpus that did not change. Determinism here is what
makes an incremental pipeline incremental.

Chunks are also where authorization is most easily lost. A chunk leaves this module
carrying its tenant, its ACL, its source URI, its source version, and its parser
version, because from here on it travels alone. Whatever it does not carry, the
retrieval path cannot check, and the retrieval path is the one talking to the model.
"""

from __future__ import annotations

from dataclasses import dataclass

from .identity import chunk_id, document_id, text_hash
from .models import Chunk, LineageEdge, ParsedDocument

CHUNKER_VERSION = "paragraph-v1"


@dataclass(frozen=True, slots=True)
class ChunkingResult:
    document_id: str
    chunks: tuple[Chunk, ...]
    lineage: tuple[LineageEdge, ...]


def _split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    words = paragraph.split()
    pieces: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and len(candidate) > max_chars:
            pieces.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def split_paragraphs(text: str, max_chars: int = 500) -> tuple[str, ...]:
    if max_chars < 50:
        raise ValueError("max_chars must be at least 50")
    paragraphs: list[str] = []
    for paragraph in (part.strip() for part in text.split("\n\n")):
        if not paragraph:
            continue
        paragraphs.extend(_split_long_paragraph(paragraph, max_chars))

    packed: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if current and len(candidate) > max_chars:
            packed.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        packed.append(current)
    return tuple(packed)


def chunk_document(document: ParsedDocument, max_chars: int = 500) -> ChunkingResult:
    doc_id = document_id(document.tenant_id, document.external_id)
    chunks: list[Chunk] = []
    lineage: list[LineageEdge] = []
    for ordinal, text in enumerate(split_paragraphs(document.text, max_chars)):
        key = chunk_id(doc_id, ordinal, text)
        chunks.append(
            Chunk(
                tenant_id=document.tenant_id,
                document_id=doc_id,
                external_id=document.external_id,
                chunk_id=key,
                ordinal=ordinal,
                text=text,
                content_hash=text_hash(text),
                source_uri=document.source_uri,
                source_version=document.version,
                acl=document.acl,
                parser_version=document.parser_version,
            )
        )
        lineage.append(
            LineageEdge(
                parent_id=doc_id,
                child_id=key,
                transform="paragraph_chunk",
                transform_version=CHUNKER_VERSION,
            )
        )
    return ChunkingResult(doc_id, tuple(chunks), tuple(lineage))

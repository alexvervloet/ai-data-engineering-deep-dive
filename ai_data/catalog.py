"""An atomic, tenant-aware index lifecycle used by the offline lessons."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .chunking import chunk_document
from .contracts import validate_source
from .embedding import DeterministicEmbedder, plan_batches
from .identity import document_id
from .models import (
    AccessControl,
    ChangeEvent,
    ChangeKind,
    IndexedChunk,
    LineageEdge,
    SourceRecord,
)
from .parsing import OCR, parse_document


@dataclass(frozen=True, slots=True)
class DocumentState:
    tenant_id: str
    external_id: str
    document_id: str
    source_version: int
    content_hash: str | None
    source_uri: str | None
    acl: AccessControl
    chunk_ids: tuple[str, ...]
    deleted: bool


@dataclass(frozen=True, slots=True)
class SyncReport:
    status: str
    tenant_id: str
    external_id: str
    source_version: int
    chunks_written: int = 0
    chunks_removed: int = 0
    embeddings_created: int = 0
    embeddings_reused: int = 0
    acl_changed: bool = False


@dataclass(frozen=True, slots=True)
class SearchHit:
    score: float
    entry: IndexedChunk


class InMemoryCatalog:
    """Reference behavior for a real transactional index backend."""

    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], DocumentState] = {}
        self.entries: dict[tuple[str, str], IndexedChunk] = {}
        self.lineage: dict[str, LineageEdge] = {}
        self.embedding_cache: dict[tuple[str, str], tuple[float, ...]] = {}

    def upsert(
        self,
        record: SourceRecord,
        embedder: DeterministicEmbedder,
        *,
        max_chars: int = 500,
        max_batch_items: int = 64,
        max_batch_tokens: int = 8_000,
        ocr: OCR | None = None,
        force: bool = False,
    ) -> SyncReport:
        validate_source(record)
        key = (record.tenant_id, record.external_id)
        previous = self.documents.get(key)
        stale_version = previous is not None and record.version < previous.source_version
        same_version = previous is not None and record.version == previous.source_version
        if stale_version or (same_version and not force):
            return SyncReport(
                "stale",
                record.tenant_id,
                record.external_id,
                record.version,
            )

        parsed = parse_document(record, ocr=ocr)
        result = chunk_document(parsed, max_chars=max_chars)
        missing = tuple(
            chunk
            for chunk in result.chunks
            if (embedder.model, chunk.content_hash) not in self.embedding_cache
        )
        embeddings_created = 0
        for batch in plan_batches(
            missing, max_items=max_batch_items, max_tokens=max_batch_tokens
        ):
            vectors = embedder.embed(tuple(chunk.text for chunk in batch.chunks))
            for chunk, vector in zip(batch.chunks, vectors, strict=True):
                self.embedding_cache[(embedder.model, chunk.content_hash)] = vector
                embeddings_created += 1

        now = datetime.now(timezone.utc)
        replacement_entries = {
            (chunk.tenant_id, chunk.chunk_id): IndexedChunk(
                chunk=chunk,
                embedding=self.embedding_cache[(embedder.model, chunk.content_hash)],
                embedding_model=embedder.model,
                indexed_at=now,
            )
            for chunk in result.chunks
        }
        old_ids = previous.chunk_ids if previous else ()
        next_entries = dict(self.entries)
        for old_id in old_ids:
            next_entries.pop((record.tenant_id, old_id), None)
        next_entries.update(replacement_entries)
        next_lineage = dict(self.lineage)
        for old_id in old_ids:
            next_lineage.pop(old_id, None)
        next_lineage.update({edge.child_id: edge for edge in result.lineage})

        # The visible state changes only after parsing and every embedding succeeded.
        self.entries = next_entries
        self.lineage = next_lineage
        self.documents[key] = DocumentState(
            tenant_id=record.tenant_id,
            external_id=record.external_id,
            document_id=result.document_id,
            source_version=record.version,
            content_hash=parsed.content_hash,
            source_uri=record.source_uri,
            acl=record.acl,
            chunk_ids=tuple(chunk.chunk_id for chunk in result.chunks),
            deleted=False,
        )
        acl_changed = previous is not None and previous.acl != record.acl
        return SyncReport(
            "backfilled" if previous and force else "updated" if previous else "created",
            record.tenant_id,
            record.external_id,
            record.version,
            chunks_written=len(result.chunks),
            chunks_removed=len(old_ids),
            embeddings_created=embeddings_created,
            embeddings_reused=len(result.chunks) - embeddings_created,
            acl_changed=acl_changed,
        )

    def delete(self, tenant_id: str, external_id: str, version: int) -> SyncReport:
        key = (tenant_id, external_id)
        previous = self.documents.get(key)
        if previous is not None and version <= previous.source_version:
            return SyncReport("stale", tenant_id, external_id, version)
        old_ids = previous.chunk_ids if previous else ()
        for chunk_key in old_ids:
            self.entries.pop((tenant_id, chunk_key), None)
            self.lineage.pop(chunk_key, None)
        self.documents[key] = DocumentState(
            tenant_id=tenant_id,
            external_id=external_id,
            document_id=document_id(tenant_id, external_id),
            source_version=version,
            content_hash=None,
            source_uri=None,
            acl=AccessControl(),
            chunk_ids=(),
            deleted=True,
        )
        return SyncReport(
            "deleted", tenant_id, external_id, version, chunks_removed=len(old_ids)
        )

    def apply_event(
        self, event: ChangeEvent, embedder: DeterministicEmbedder
    ) -> SyncReport:
        if event.kind is ChangeKind.DELETE:
            return self.delete(
                event.tenant_id, event.external_id, event.version
            )
        if event.record is None:
            raise ValueError("upsert event has no source record")
        return self.upsert(event.record, embedder)

    def search(
        self,
        *,
        tenant_id: str,
        principals: frozenset[str],
        query: str,
        embedder: DeterministicEmbedder,
        limit: int = 5,
    ) -> tuple[SearchHit, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        query_vector = embedder.embed((query,))[0]
        hits: list[SearchHit] = []
        for (entry_tenant, _), entry in self.entries.items():
            if entry_tenant != tenant_id:
                continue
            if not entry.chunk.acl.allows(principals):
                continue
            score = sum(
                left * right for left, right in zip(query_vector, entry.embedding, strict=True)
            )
            hits.append(SearchHit(score, entry))
        return tuple(sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit])

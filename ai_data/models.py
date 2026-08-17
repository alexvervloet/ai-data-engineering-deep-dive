"""Domain records shared by connectors, transforms, and index backends.

Every record here is frozen. That is not tidiness, it is the property the rest of the
pipeline is built on: a document that cannot be mutated in place can be replaced only
by writing a new version, so "which version is this?" always has an answer, and a
retry can be compared against what is already stored instead of overwriting it blind.

Read the types as a chain of custody. A `SourceRecord` is what the source system says
is true, at a version the source assigned. A `ParsedDocument` adds text and the parser
that produced it. A `Chunk` adds position, and carries the ACL and source version down
with it. An `IndexedChunk` adds the vector and the model that made it. A `LineageEdge`
records which parent produced which child, and by which transform.

Two fields travel the entire chain on purpose. The ACL travels because authorization
is a property of the data, not a filter someone remembers to apply at query time. The
source version travels because it is the only thing that can decide, later and out of
order, whether an arriving event is news or an echo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Mapping


@dataclass(frozen=True, slots=True)
class AccessControl:
    """A deny-by-default list of principals allowed to read a document."""

    readers: frozenset[str] = field(default_factory=frozenset)

    def allows(self, principals: frozenset[str]) -> bool:
        return bool(self.readers & principals)


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """One versioned source object, before parsing or chunking."""

    tenant_id: str
    external_id: str
    version: int
    updated_at: datetime
    source_uri: str
    mime_type: str
    content: bytes
    acl: AccessControl
    metadata: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def now(
        cls,
        *,
        tenant_id: str,
        external_id: str,
        version: int,
        source_uri: str,
        mime_type: str,
        content: bytes,
        readers: frozenset[str],
        metadata: Mapping[str, str] | None = None,
    ) -> SourceRecord:
        """Convenience constructor for examples; production connectors use source time."""

        return cls(
            tenant_id=tenant_id,
            external_id=external_id,
            version=version,
            updated_at=datetime.now(timezone.utc),
            source_uri=source_uri,
            mime_type=mime_type,
            content=content,
            acl=AccessControl(readers),
            metadata=metadata or {},
        )


class ChangeKind(StrEnum):
    UPSERT = "upsert"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    """An ordered CDC event. Delete events intentionally carry no old content."""

    sequence: int
    kind: ChangeKind
    tenant_id: str
    external_id: str
    version: int
    record: SourceRecord | None = None


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    tenant_id: str
    external_id: str
    version: int
    source_uri: str
    text: str
    content_hash: str
    acl: AccessControl
    metadata: Mapping[str, str]
    parser_version: str


@dataclass(frozen=True, slots=True)
class Chunk:
    tenant_id: str
    document_id: str
    external_id: str
    chunk_id: str
    ordinal: int
    text: str
    content_hash: str
    source_uri: str
    source_version: int
    acl: AccessControl
    parser_version: str


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    chunk: Chunk
    embedding: tuple[float, ...]
    embedding_model: str
    indexed_at: datetime


@dataclass(frozen=True, slots=True)
class LineageEdge:
    parent_id: str
    child_id: str
    transform: str
    transform_version: str

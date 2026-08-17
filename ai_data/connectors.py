"""Connector semantics: consistent snapshots followed by ordered change events.

A first crawl and an incremental feed look like two features. They are one protocol,
and the seam between them is where corpora quietly go wrong:

1. capture the source high-watermark;
2. read a snapshot at that same logical instant;
3. consume changes strictly after the watermark;
4. persist the new cursor only after the index write commits.

Get step 3 wrong in one direction and documents are processed twice, which costs money
and is otherwise harmless. Get it wrong in the other and the documents that changed
during the crawl are never seen again, by anything, until someone notices the answer
is out of date. The asymmetry is why the safe default is to overlap and rely on
idempotent writes rather than to trim the window.

`MemoryConnector` uses integer cursors so the arithmetic is visible in the lessons.
Treat a real provider's cursor as an opaque token: it may be a timestamp, a log
sequence number, a page token, or a blob of vendor JSON, and the moment code does
arithmetic on it, it has taken a dependency the provider never offered.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import validate_source
from .models import ChangeEvent, ChangeKind, SourceRecord


@dataclass(frozen=True, slots=True)
class ChangePage:
    events: tuple[ChangeEvent, ...]
    next_cursor: int
    has_more: bool


class MemoryConnector:
    """A deterministic source used to make snapshot/CDC handoff observable."""

    def __init__(self) -> None:
        self._events: list[ChangeEvent] = []
        self._latest_version: dict[tuple[str, str], int] = {}

    @property
    def high_watermark(self) -> int:
        return len(self._events)

    def upsert(self, record: SourceRecord) -> ChangeEvent:
        validate_source(record)
        self._check_version(record.tenant_id, record.external_id, record.version)
        event = ChangeEvent(
            sequence=self.high_watermark + 1,
            kind=ChangeKind.UPSERT,
            tenant_id=record.tenant_id,
            external_id=record.external_id,
            version=record.version,
            record=record,
        )
        self._events.append(event)
        self._latest_version[(record.tenant_id, record.external_id)] = record.version
        return event

    def delete(self, tenant_id: str, external_id: str, version: int) -> ChangeEvent:
        self._check_version(tenant_id, external_id, version)
        event = ChangeEvent(
            sequence=self.high_watermark + 1,
            kind=ChangeKind.DELETE,
            tenant_id=tenant_id,
            external_id=external_id,
            version=version,
        )
        self._events.append(event)
        self._latest_version[(tenant_id, external_id)] = version
        return event

    def capture_snapshot(self) -> tuple[tuple[SourceRecord, ...], int]:
        """Return state and the cursor from the same logical source instant."""

        watermark = self.high_watermark
        return self.snapshot_at(watermark), watermark

    def snapshot_at(self, watermark: int) -> tuple[SourceRecord, ...]:
        if watermark < 0 or watermark > self.high_watermark:
            raise ValueError("watermark is outside the connector history")
        state: dict[tuple[str, str], SourceRecord] = {}
        for event in self._events[:watermark]:
            key = (event.tenant_id, event.external_id)
            if event.kind is ChangeKind.DELETE:
                state.pop(key, None)
            else:
                assert event.record is not None
                state[key] = event.record
        return tuple(state[key] for key in sorted(state))

    def changes_after(self, cursor: int, limit: int = 100) -> ChangePage:
        if cursor < 0 or cursor > self.high_watermark:
            raise ValueError("cursor is outside the connector history")
        if limit < 1:
            raise ValueError("limit must be positive")
        events = tuple(self._events[cursor : cursor + limit])
        next_cursor = cursor + len(events)
        return ChangePage(events, next_cursor, next_cursor < self.high_watermark)

    def _check_version(self, tenant_id: str, external_id: str, version: int) -> None:
        previous = self._latest_version.get((tenant_id, external_id), 0)
        if version <= previous:
            raise ValueError(
                f"version {version} is not newer than {previous} for "
                f"{tenant_id}/{external_id}"
            )

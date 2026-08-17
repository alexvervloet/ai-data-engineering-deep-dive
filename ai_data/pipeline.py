"""Snapshot, CDC, checkpoint, and backfill orchestration.

Three jobs share one set of rules here, and the difference between them is worth
naming, because production teams routinely build the first and discover the other two
by incident.

`bootstrap` reads the source once and records the watermark it read at. `poll` applies
changes after the cursor and advances it. `backfill` re-derives current source state
after the code changed rather than the data, and it is the only mode allowed to rewrite
a document at its existing version.

The critical line in the whole module is the order of two operations: apply the change,
then persist the cursor. Reversed, a crash between them skips events forever, and
nothing reports it. In this order a crash replays events that were already applied, and
because the catalog compares versions, replay is a no-op. That is the practical shape
of exactly-once processing: at-least-once delivery plus effects that can be repeated
without changing the answer.

What this class deliberately does not have is concurrency. Workers, queues, and
parallel batches all multiply throughput and they also multiply whatever ordering bugs
already exist. Get idempotency, version comparison, and transactional replacement
tested first. Parallelizing an unsafe lifecycle only makes the corruption arrive sooner.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import InMemoryCatalog, SyncReport
from .connectors import MemoryConnector
from .embedding import DeterministicEmbedder


@dataclass(frozen=True, slots=True)
class PipelineRun:
    reports: tuple[SyncReport, ...]
    start_cursor: int
    end_cursor: int
    has_more: bool


class SyncPipeline:
    def __init__(
        self,
        connector: MemoryConnector,
        catalog: InMemoryCatalog,
        embedder: DeterministicEmbedder,
        *,
        cursor: int = 0,
    ) -> None:
        self.connector = connector
        self.catalog = catalog
        self.embedder = embedder
        self.cursor = cursor

    def bootstrap(self) -> PipelineRun:
        records, watermark = self.connector.capture_snapshot()
        reports = tuple(
            self.catalog.upsert(record, self.embedder) for record in records
        )
        start = self.cursor
        self.cursor = watermark
        return PipelineRun(reports, start, watermark, False)

    def poll(self, *, limit: int = 100) -> PipelineRun:
        start = self.cursor
        page = self.connector.changes_after(start, limit)
        reports = tuple(
            self.catalog.apply_event(event, self.embedder) for event in page.events
        )
        # A durable implementation persists this only after the index transaction commits.
        self.cursor = page.next_cursor
        return PipelineRun(reports, start, self.cursor, page.has_more)

    def drain(self, *, limit: int = 100) -> tuple[PipelineRun, ...]:
        runs: list[PipelineRun] = []
        while self.cursor < self.connector.high_watermark:
            run = self.poll(limit=limit)
            runs.append(run)
            if not run.has_more:
                break
        return tuple(runs)

    def backfill(self) -> tuple[SyncReport, ...]:
        """Re-run current source state after a parser/chunker/model migration."""

        records, _ = self.connector.capture_snapshot()
        return tuple(
            self.catalog.upsert(record, self.embedder, force=True) for record in records
        )

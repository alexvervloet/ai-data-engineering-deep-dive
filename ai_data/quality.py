"""Data-quality gates that fail a pipeline before retrieval quality degrades.

Retrieval evals answer "did the right chunk rank first?" They cannot answer "is this
corpus current, complete, and authorized?", because a stale or over-shared corpus can
score perfectly against a stale eval set. These checks run earlier and answer that.

Two design rules hold the file together. Each check measures one thing, so a failure
names the stage that broke instead of announcing that something, somewhere, is wrong.
And each check tolerates the state it is looking for: a gate that raises on corrupt
data is a gate that stops reporting exactly when the pipeline needs it most.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import InMemoryCatalog
from .models import SourceRecord
from .reconcile import reconcile


@dataclass(frozen=True, slots=True)
class QualityCheck:
    name: str
    passed: bool
    value: float
    expectation: str
    critical: bool = True


@dataclass(frozen=True, slots=True)
class QualityReport:
    checks: tuple[QualityCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.passed for check in self.checks if check.critical)


def assess_quality(
    source_records: tuple[SourceRecord, ...], catalog: InMemoryCatalog
) -> QualityReport:
    active_states = [state for state in catalog.documents.values() if not state.deleted]
    entries = list(catalog.entries.values())
    findings = reconcile(source_records, catalog)
    expected_count = len(source_records)
    indexed_count = sum(
        1
        for record in source_records
        if (state := catalog.documents.get((record.tenant_id, record.external_id)))
        and not state.deleted
    )
    coverage = indexed_count / expected_count if expected_count else 1.0
    # A gate that raises cannot fail a release, it can only crash the job that was
    # supposed to decide. Every lookup here tolerates the broken state it is checking
    # for: an indexed chunk whose document row is gone has no authoritative ACL to
    # compare against, so it is counted as its own failure rather than raising.
    acl_mismatches = 0
    unowned_chunks = 0
    for entry in entries:
        state = catalog.documents.get((entry.chunk.tenant_id, entry.chunk.external_id))
        if state is None:
            unowned_chunks += 1
        elif entry.chunk.acl != state.acl:
            acl_mismatches += 1
    empty_chunks = sum(not entry.chunk.text.strip() for entry in entries)
    lineage_coverage = (
        sum(entry.chunk.chunk_id in catalog.lineage for entry in entries) / len(entries)
        if entries
        else 1.0
    )
    dimensions = {len(entry.embedding) for entry in entries}
    unique_hashes = {entry.chunk.content_hash for entry in entries}
    duplicate_ratio = 1 - (len(unique_hashes) / len(entries)) if entries else 0.0

    return QualityReport(
        (
            QualityCheck("source coverage", coverage == 1.0, coverage, "equals 1.0"),
            QualityCheck(
                "reconciliation drift", not findings, float(len(findings)), "equals 0"
            ),
            QualityCheck("empty chunks", empty_chunks == 0, float(empty_chunks), "equals 0"),
            QualityCheck(
                "ACL propagation", acl_mismatches == 0, float(acl_mismatches), "equals 0"
            ),
            QualityCheck(
                "chunks have an owning document",
                unowned_chunks == 0,
                float(unowned_chunks),
                "equals 0",
            ),
            QualityCheck(
                "lineage coverage", lineage_coverage == 1.0, lineage_coverage, "equals 1.0"
            ),
            QualityCheck(
                "embedding dimensions", len(dimensions) <= 1, float(len(dimensions)), "at most 1"
            ),
            QualityCheck(
                "duplicate chunk ratio",
                duplicate_ratio <= 0.25,
                duplicate_ratio,
                "at most 0.25",
                critical=False,
            ),
            QualityCheck(
                "active documents have chunks",
                all(state.chunk_ids for state in active_states),
                float(sum(not state.chunk_ids for state in active_states)),
                "equals 0 empty documents",
            ),
        )
    )

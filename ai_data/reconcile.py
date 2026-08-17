"""Detect drift between authoritative source state and the retrieval index."""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import InMemoryCatalog
from .models import SourceRecord


@dataclass(frozen=True, slots=True)
class ReconciliationFinding:
    kind: str
    tenant_id: str
    external_id: str
    detail: str


def reconcile(
    source_records: tuple[SourceRecord, ...], catalog: InMemoryCatalog
) -> tuple[ReconciliationFinding, ...]:
    expected = {
        (record.tenant_id, record.external_id): record for record in source_records
    }
    findings: list[ReconciliationFinding] = []

    for key, record in expected.items():
        state = catalog.documents.get(key)
        if state is None or state.deleted:
            findings.append(
                ReconciliationFinding("missing", *key, "active source has no index state")
            )
            continue
        if state.source_version != record.version:
            findings.append(
                ReconciliationFinding(
                    "stale_version",
                    *key,
                    f"source={record.version}, index={state.source_version}",
                )
            )
        if state.acl != record.acl:
            findings.append(
                ReconciliationFinding("acl_drift", *key, "source and index ACL differ")
            )
        missing_chunks = tuple(
            chunk_id
            for chunk_id in state.chunk_ids
            if (record.tenant_id, chunk_id) not in catalog.entries
        )
        if missing_chunks:
            findings.append(
                ReconciliationFinding(
                    "missing_chunks", *key, f"missing {len(missing_chunks)} chunks"
                )
            )

    for key, state in catalog.documents.items():
        if not state.deleted and key not in expected:
            findings.append(
                ReconciliationFinding(
                    "orphan", *key, "index document no longer exists in source snapshot"
                )
            )

    known_chunk_keys = {
        (state.tenant_id, chunk_id)
        for state in catalog.documents.values()
        for chunk_id in state.chunk_ids
    }
    for tenant_id, chunk_id in catalog.entries:
        if (tenant_id, chunk_id) not in known_chunk_keys:
            external_id = catalog.entries[(tenant_id, chunk_id)].chunk.external_id
            findings.append(
                ReconciliationFinding(
                    "dangling_chunk", tenant_id, external_id, chunk_id
                )
            )

    return tuple(
        sorted(findings, key=lambda item: (item.tenant_id, item.external_id, item.kind))
    )

"""Lesson 8: deletions are durable events; reconciliation catches missed ones."""

from ai_data.catalog import InMemoryCatalog
from ai_data.connectors import MemoryConnector
from ai_data.embedding import DeterministicEmbedder
from ai_data.reconcile import reconcile

from _fixtures import record


def main() -> None:
    connector = MemoryConnector()
    connector.upsert(record("deploy.md"))
    connector.upsert(record("retired.md", text="This policy is obsolete."))
    snapshot, cursor = connector.capture_snapshot()
    catalog = InMemoryCatalog()
    embedder = DeterministicEmbedder()
    for item in snapshot:
        catalog.upsert(item, embedder)

    connector.delete("acme", "retired.md", version=2)
    current_source, _ = connector.capture_snapshot()
    findings = reconcile(current_source, catalog)
    print("MISSED DELETE")
    for finding in findings:
        print(f"  {finding.kind}: {finding.tenant_id}/{finding.external_id}")

    delete_event = connector.changes_after(cursor).events[0]
    report = catalog.apply_event(delete_event, embedder)
    print("\nAFTER TOMBSTONE")
    print(f"  status: {report.status}, removed chunks: {report.chunks_removed}")
    print(f"  reconciliation findings: {len(reconcile(current_source, catalog))}")

    late = catalog.upsert(record("retired.md", version=1), embedder)
    print(f"  late v1 replay: {late.status}")
    print("\nTakeaway: retain tombstone versions and reconcile source-to-index state continuously.")


if __name__ == "__main__":
    main()

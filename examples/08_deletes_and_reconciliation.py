"""
Lesson 8: deletions are durable events, and reconciliation catches the missed ones.

Removing a vector is not deleting a document. Without a record that the deletion
happened, and at which version, a late-arriving retry of an older upsert finds
nothing in the index, concludes the document is new, and puts it back. The chunks
return. The assistant cites them. Nothing errors.

So a delete writes a **tombstone**: the document row stays, marked deleted, holding
the version of the delete. Any event at or below that version is stale. Only a
strictly newer source event brings the document back, which is what makes "deleted"
survive replay, reordering, and backfills.

The second half is **reconciliation**, and it starts from the opposite assumption to
every other lesson. Everything else assumes events arrive and get applied.
Reconciliation assumes that over a long enough window, one did not. A webhook was
dropped during a deploy. A cursor was rolled back by a database restore. A connector
was down longer than the provider's change retention, which is a specific and common
way to lose data permanently: the events expired while you were away, so neither side
will ever mention them again.

None of those produce an error at the time, so the index is compared against source
truth on a schedule, in both directions. Walking the source finds documents that are
missing, stale, or ACL-drifted. Walking the index finds documents the source no
longer has, and chunks whose document is gone.

This example deliberately skips a delete event, lets reconciliation find the orphan,
applies the tombstone, and then confirms that a late v1 upsert still cannot resurrect
it.

A warning the output cannot show you. Findings are computed against a source
snapshot, and a snapshot taken while the source API was half-degraded looks exactly
like a source that deleted a great many documents. "Delete everything I did not see"
reads as correct and empties a tenant the first time a connector has a bad afternoon.
Bound the repair: a budget per run, an alert when it is hit, and a human above it.

Predict before running: which finding appears before the delete is applied, and what
status does the late v1 upsert get?

Previous: lesson 7 covered migrations. Next: lesson 9 gates on the result.
See README section 8 and TEXTBOOK.md section 19.7.

Run it:

    python examples/08_deletes_and_reconciliation.py
"""

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

    # The source deletes a document. Note what does NOT happen next: the pipeline
    # never consumes this event. That is the missed webhook, the restarted worker,
    # the expired change window.
    connector.delete("acme", "retired.md", version=2)

    # Reconciliation compares current source truth against index state and finds the
    # document the index still holds. This is the only mechanism in the dive that can
    # detect a change nobody delivered.
    current_source, _ = connector.capture_snapshot()
    findings = reconcile(current_source, catalog)
    print("MISSED DELETE")
    for finding in findings:
        print(f"  {finding.kind}: {finding.tenant_id}/{finding.external_id}")

    # Now the delete is applied properly. Chunks go, the tombstone stays, and
    # reconciliation goes quiet because source and index agree again.
    delete_event = connector.changes_after(cursor).events[0]
    report = catalog.apply_event(delete_event, embedder)
    print("\nAFTER TOMBSTONE")
    print(f"  status: {report.status}, removed chunks: {report.chunks_removed}")
    print(f"  reconciliation findings: {len(reconcile(current_source, catalog))}")

    # The resurrection attempt: an old v1 upsert arrives late, which is exactly what a
    # retry queue does. The tombstone holds v2, so v1 is not news.
    late = catalog.upsert(record("retired.md", version=1), embedder)
    print(f"  late v1 replay: {late.status}")
    print("\nTakeaway: retain tombstone versions and reconcile source-to-index state continuously.")


if __name__ == "__main__":
    main()

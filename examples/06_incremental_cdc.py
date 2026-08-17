"""
Lesson 6: incremental updates are ordered, checkpointed, and replay-safe.

This lesson contains the single most important line in the whole pipeline, and it is
an ordering, not an algorithm: **apply the change first, persist the cursor second.**

Reverse those two and a crash between them skips events forever. Nothing reports it,
because from the pipeline's point of view the work was done. That is the bug behind
the story in TEXTBOOK.md section 19.1: a deleted document that kept being retrieved
and cited for five months.

In the correct order, a crash replays events that were already applied. Replay is
only safe because every write compares versions: an arriving event whose version is
not newer than what is stored is reported as `stale` and does nothing. That comparison
is what makes at-least-once delivery survivable, and at-least-once is what you
actually get. Queues redeliver, workers die between an effect and its acknowledgment,
retries fire on requests that already succeeded, and partitions deliver Tuesday's
events on Wednesday. Exactly-once delivery is mostly unavailable end to end; the
practical substitute is at-least-once delivery plus effects that can be repeated
without changing the answer.

The example does three things in sequence:

  1. bootstraps from a snapshot and records the cursor;
  2. drains three changes (an update, a create, and a delete) two at a time;
  3. rewinds to the bootstrap checkpoint and replays everything, simulating a worker
     that committed its index writes and then crashed before saving its cursor.

The replay is the payoff. Every status comes back `stale`, the update stays at v2,
and the deleted document stays deleted. Nothing is duplicated and nothing is
resurrected, because the tombstone (lesson 8) holds a version of its own.

Predict before running: what statuses does the replay print, and what version is
`deploy.md` at when it finishes?

Previous: lesson 5 covered permissions. Next: lesson 7 bounds the expensive step.
See README section 6 and TEXTBOOK.md section 19.3.

Run it:

    python examples/06_incremental_cdc.py
"""

from ai_data.catalog import InMemoryCatalog
from ai_data.connectors import MemoryConnector
from ai_data.embedding import DeterministicEmbedder
from ai_data.pipeline import SyncPipeline

from _fixtures import record


def main() -> None:
    connector = MemoryConnector()
    connector.upsert(record("deploy.md"))
    catalog = InMemoryCatalog()
    embedder = DeterministicEmbedder()
    pipeline = SyncPipeline(connector, catalog, embedder)

    # The initial load, and the checkpoint it leaves behind. Everything after this
    # point is incremental.
    bootstrap = pipeline.bootstrap()
    checkpoint = bootstrap.end_cursor
    print(f"BOOTSTRAP: cursor {checkpoint}, documents={len(catalog.documents)}")

    # Three changes at the source: one document updated, one created, one deleted.
    # The delete carries version 2, because a deletion is a source fact with a
    # version of its own rather than the absence of a row.
    connector.upsert(record("deploy.md", version=2, text="Deploy with three reviewers."))
    connector.upsert(record("on-call.md", text="Escalate after five minutes."))
    connector.delete("acme", "on-call.md", version=2)

    # Draining in pages of two, because a real backlog does not arrive at once. Each
    # page advances the cursor only after its writes are applied.
    print("\nCDC PAGES")
    for run in pipeline.drain(limit=2):
        statuses = [report.status for report in run.reports]
        print(f"  {run.start_cursor}->{run.end_cursor}: {statuses}")

    # The crash simulation: a second worker starts from the old checkpoint, as if the
    # cursor write had been lost after the index writes committed. Every event is
    # delivered a second time.
    print("\nREPLAY AFTER CRASHED CHECKPOINT WRITE")
    replay = SyncPipeline(connector, catalog, embedder, cursor=checkpoint)
    replay_statuses = [
        report.status for run in replay.drain(limit=10) for report in run.reports
    ]
    print(f"  replay statuses: {replay_statuses}")
    print(f"  deploy version: {catalog.documents[('acme', 'deploy.md')].source_version}")
    print(f"  on-call deleted: {catalog.documents[('acme', 'on-call.md')].deleted}")
    print("\nTakeaway: persist the CDC cursor only after the atomic index write commits.")


if __name__ == "__main__":
    main()

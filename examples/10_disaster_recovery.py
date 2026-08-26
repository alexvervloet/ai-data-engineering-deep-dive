"""
Lesson 10: restore authoritative data, rebuild derivatives, resume from CDC.

Disaster recovery follows directly from the dive's one big idea:

    A retrieval index is a disposable, derived view of authoritative source data.

If that is true, then backing up the vector table is close to worthless. Vectors are
the one artifact you can recompute. What you cannot recompute is the **source
snapshot** (bytes, versions, ACLs, and metadata as they were) and the **CDC cursor**
(your exact position in the source's change history). Lose the cursor and you cannot
resume; you can only re-crawl and hope the source still has everything.

So the backup here holds records and a cursor in a checksummed envelope, and recovery
is four steps: verify the checksum, restore the snapshot, rebuild the index, replay
the events that happened after the cursor. The example loses the index completely
after a change has landed, and the rebuilt corpus still ends up at the current
version, because the replay covers the gap between the backup and now.

Two numbers come out of this, and they are numbers to state rather than guess:

  - **RPO**, recovery point objective: how much source and change history you can
    afford to lose. Your backup interval decides it.
  - **RTO**, recovery time objective: how long a full rebuild actually takes at your
    real corpus size. Parsing and embedding a corpus is measured in hours and provider
    rate limits, not in the seconds a database restore takes. A team that has never
    rebuilt does not have an RTO, it has an aspiration.

The checksum deserves its own note. Restore runs on the worst day of your quarter,
which is the worst time to discover a truncated file. It is also worth distinguishing
corruption from a backup that is intact but no longer satisfies the current contract:
the first sends you to another copy, the second means the contract moved while the
backup sat still, and you need a migration rather than a different tape. This
repository raises different exceptions for the two.

Predict before running: what version is `deploy.md` at right after the restore, and
what version after the replay?

Previous: lesson 9 gated the corpus. Next: the capstone in hands_on/sync_corpus.py
runs this whole lifecycle against Postgres.
See README section 10 and TEXTBOOK.md section 19.9.

Run it:

    python examples/10_disaster_recovery.py
"""

from ai_data.catalog import InMemoryCatalog
from ai_data.connectors import MemoryConnector
from ai_data.embedding import DeterministicEmbedder
from ai_data.recovery import create_backup, restore_backup

from _fixtures import record


def main() -> None:
    source = MemoryConnector()
    source.upsert(record("deploy.md"))
    source.upsert(record("on-call.md", text="Escalate in five minutes."))

    # The backup pairs source state with the cursor that state corresponds to. Either
    # one alone is not recoverable: records without a cursor cannot resume, and a
    # cursor without records has nothing to resume from.
    snapshot, backup_cursor = source.capture_snapshot()
    backup = create_backup(snapshot, cdc_cursor=backup_cursor)
    print(f"BACKUP: records={len(snapshot)}, cursor={backup_cursor}, bytes={len(backup)}")

    # A change lands after the backup, then the derived index is lost completely.
    source.upsert(record("deploy.md", version=2, text="Deployments require three reviewers."))
    restored_records, restored_cursor = restore_backup(backup)

    # Rebuild the derivatives from restored source state. Every embedding is
    # recomputed here, which is the part of an RTO people underestimate.
    rebuilt = InMemoryCatalog()
    embedder = DeterministicEmbedder()
    for item in restored_records:
        rebuilt.upsert(item, embedder)

    # Then close the gap between the backup and now. This is why the cursor is in the
    # envelope: without it there is no defensible place to resume from.
    for event in source.changes_after(restored_cursor).events:
        rebuilt.apply_event(event, embedder)

    print("\nRESTORE")
    print(f"  rebuilt documents: {len(rebuilt.documents)}")
    print(f"  resumed CDC events: {source.high_watermark - restored_cursor}")
    print(f"  deploy version: {rebuilt.documents[('acme', 'deploy.md')].source_version}")
    print(f"  chunks: {len(rebuilt.entries)}, quality can now be rechecked")
    print("\nTakeaway: the source snapshot + cursor is durable; the vector index is rebuildable.")


if __name__ == "__main__":
    main()

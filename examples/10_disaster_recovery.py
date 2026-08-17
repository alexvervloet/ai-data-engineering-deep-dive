"""Lesson 10: restore authoritative data, rebuild derivatives, resume from CDC."""

from ai_data.catalog import InMemoryCatalog
from ai_data.connectors import MemoryConnector
from ai_data.embedding import DeterministicEmbedder
from ai_data.recovery import create_backup, restore_backup

from _fixtures import record


def main() -> None:
    source = MemoryConnector()
    source.upsert(record("deploy.md"))
    source.upsert(record("on-call.md", text="Escalate in five minutes."))
    snapshot, backup_cursor = source.capture_snapshot()
    backup = create_backup(snapshot, cdc_cursor=backup_cursor)
    print(f"BACKUP: records={len(snapshot)}, cursor={backup_cursor}, bytes={len(backup)}")

    # A change lands after the backup, then the derived index is lost completely.
    source.upsert(record("deploy.md", version=2, text="Deployments require three reviewers."))
    restored_records, restored_cursor = restore_backup(backup)
    rebuilt = InMemoryCatalog()
    embedder = DeterministicEmbedder()
    for item in restored_records:
        rebuilt.upsert(item, embedder)
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

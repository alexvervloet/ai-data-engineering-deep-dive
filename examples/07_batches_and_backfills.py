"""Lesson 7: bound embedding requests and make migrations resumable."""

from ai_data.catalog import InMemoryCatalog
from ai_data.chunking import chunk_document
from ai_data.embedding import DeterministicEmbedder, plan_batches
from ai_data.parsing import parse_document

from _fixtures import record


def main() -> None:
    text = "\n\n".join(
        [
            "Deployments require two reviewers.",
            "Canaries receive five percent traffic.",
            "Watch latency for fifteen minutes.",
            "Rollback on an error-rate regression.",
            "Record the release evidence.",
            "Notify the service owner.",
        ]
    )
    source = record("release.md", text=text)
    chunks = chunk_document(parse_document(source), max_chars=50).chunks
    batches = plan_batches(chunks, max_items=2, max_tokens=30)
    print("BATCH PLAN")
    for number, batch in enumerate(batches, 1):
        print(f"  batch {number}: items={len(batch.chunks)}, tokens~={batch.estimated_tokens}")

    catalog = InMemoryCatalog()
    embedder = DeterministicEmbedder()
    catalog.upsert(source, embedder)  # old transform: one large chunk
    calls_before = embedder.calls
    migration = catalog.upsert(
        source,
        embedder,
        force=True,
        max_chars=50,
        max_batch_items=2,
        max_batch_tokens=30,
    )
    calls_after_migration = embedder.calls
    replay = catalog.upsert(
        source,
        embedder,
        force=True,
        max_chars=50,
        max_batch_items=2,
        max_batch_tokens=30,
    )

    print("\nBACKFILL")
    print(f"  old embedding calls: {calls_before}")
    print(f"  migration calls: {calls_after_migration - calls_before}")
    print(f"  status: {migration.status}, chunks={migration.chunks_written}")
    print(f"  replay reused: {replay.embeddings_reused}, new calls={embedder.calls - calls_after_migration}")
    print("\nTakeaway: backfills are versioned jobs with bounded batches and reusable work.")


if __name__ == "__main__":
    main()

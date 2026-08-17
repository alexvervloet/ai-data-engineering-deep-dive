"""
Lesson 7: bound embedding requests and make migrations resumable.

Embedding is the step that costs money and rate limit, so it is the step with limits
on it. Providers bound a request two ways at once, by item count and by total tokens,
and a planner that respects only one works fine until the day a corpus of long
documents arrives.

Watch the batch plan in the output. With `max_items=2` and `max_tokens=30`, some
batches close because they are full of items and others because they are full of
tokens. Which limit binds depends on the data, which is exactly why both have to be
enforced rather than whichever one was easier to implement.

Two production notes the offline run cannot show you:

  - `estimate_tokens` here is deliberately conservative and deliberately not a
    tokenizer. In production, count with the tokenizer of the model you are calling.
    An estimate that runs low turns into provider errors mid-batch, which is the
    expensive place to find out.
  - A single chunk that cannot fit is refused loudly rather than truncated. Silent
    truncation is data loss that looks like a successful run. Deciding what to do
    instead (rechunk, dead-letter, or fail the document) is a real design choice, and
    EXERCISES.md asks you to make it.

The second half is a **backfill**: a rerun of current source state after the *code*
changed rather than the data. A new parser, chunker, or embedding model invalidates
derived artifacts for documents whose source version has not moved, so backfill is
the one mode allowed to rewrite a document at its existing version. Here one large
chunk becomes six small ones in three bounded calls.

Then the job is replayed, and the replay costs zero embedding calls: the content
hashes already exist in the cache. Migrations get interrupted, and a migration you
cannot resume without paying twice is a migration that gets abandoned halfway.

One boundary this repository learned the hard way: backfill mode may rewrite a live
document, and may **never** lift a tombstone. Otherwise a routine migration, run
against a snapshot captured around a delete, republishes content the source removed.
That bug shipped here twice, once in the ordinary path and once inside the exception
carved out for this mode. See LESSONS.md.

Predict before running: how many chunks, how many batches, and how many embedding
calls does the migration make? Then set `max_items=1` and watch which limit binds.

Previous: lesson 6 applied changes. Next: lesson 8 handles the deletes.
See README section 7 and TEXTBOOK.md section 19.3.

Run it:

    python examples/07_batches_and_backfills.py
"""

from ai_data.catalog import InMemoryCatalog
from ai_data.chunking import chunk_document
from ai_data.embedding import DeterministicEmbedder, plan_batches
from ai_data.parsing import parse_document

from _fixtures import record


def main() -> None:
    # Six short paragraphs, so the chunker produces enough pieces for batching to be
    # visible at small limits.
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

    # Planning is separate from sending on purpose. A plan can be inspected, logged,
    # costed, and checkpointed between batches before a single provider call is made.
    chunks = chunk_document(parse_document(source), max_chars=50).chunks
    batches = plan_batches(chunks, max_items=2, max_tokens=30)
    print("BATCH PLAN")
    for number, batch in enumerate(batches, 1):
        print(f"  batch {number}: items={len(batch.chunks)}, tokens~={batch.estimated_tokens}")

    catalog = InMemoryCatalog()
    embedder = DeterministicEmbedder()

    # The corpus as it stands under the old transform settings: one large chunk.
    catalog.upsert(source, embedder)  # old transform: one large chunk
    calls_before = embedder.calls

    # The migration. Same source version, new chunking, so it needs force=True. This
    # is the only mode that may rewrite a document whose source did not change.
    migration = catalog.upsert(
        source,
        embedder,
        force=True,
        max_chars=50,
        max_batch_items=2,
        max_batch_tokens=30,
    )
    calls_after_migration = embedder.calls

    # The same job again, as if it had been interrupted and restarted. Content hashes
    # are already cached, so the rerun costs nothing.
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

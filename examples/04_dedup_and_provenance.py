"""
Lesson 4: reuse content-addressed work without merging document identity.

Identity in a retrieval pipeline answers two questions that look similar and must
never be merged:

  1. **"Which authorized thing is this?"** That is a document ID, and it includes the
     tenant. Two customers can both have a file called `handbook.md`. Derive the ID
     from the filename alone and one customer's update overwrites the other's.
  2. **"Have we already done this work?"** That is a content address: a hash of the
     bytes. If two tenants store an identical file, parsing and embedding it twice is
     pure waste.

This example builds exactly that situation. Acme and Beta hold byte-identical text
under the same external ID, with different readers. Watch what comes out equal and
what comes out different: one blob ID, two document IDs, one embedding computed, two
lineage records kept.

The trap is reusing the wrong one. Content-addressed reuse is an optimization on
**compute**. It must never become shared **identity**, because ACLs, source URIs,
lineage, and version history belong to the document, not to the bytes. Merge them and
one tenant's permissions change silently applies to another tenant's copy, which is
the kind of bug that gets discovered by a customer.

There is one more field the cache key needs, and leaving it out never raises an
error. An embedding cache keyed by content alone will serve a vector made by last
quarter's model to a query embedded by this quarter's. That is not a crash: it is
similarity scores that are quietly meaningless. The key is the model and its
dimensions plus the content hash, and this repository has a commit fixing exactly
that omission.

Predict before running: which of the printed IDs are equal, and which differ? Then
decide, for a cache you have shipped, whether it may be global or must be scoped.

Previous: lesson 3 produced the text. Next: lesson 5 carries permissions down to
every chunk.
See README section 4 and TEXTBOOK.md section 19.5.

Run it:

    python examples/04_dedup_and_provenance.py
"""

from ai_data.catalog import InMemoryCatalog
from ai_data.embedding import DeterministicEmbedder
from ai_data.identity import blob_id, document_id

from _fixtures import record


def main() -> None:
    # Same bytes, same external ID, two tenants, different readers. This is the
    # ordinary case in any multi-customer system: a shared template, a public
    # standard, a document one customer copied from another.
    shared_text = "Security keys rotate every ninety days."
    acme = record("security.md", text=shared_text)
    beta = record(
        "security.md",
        tenant="beta",
        text=shared_text,
        readers=frozenset({"user:bob"}),
    )

    # The content address is a fact about the bytes, so it is identical. That is what
    # makes work reuse possible.
    print("CONTENT ADDRESS")
    print(f"  acme blob: {blob_id(acme.content)[:22]}...")
    print(f"  beta blob: {blob_id(beta.content)[:22]}... (same bytes)")

    # The document identity includes the tenant, so it is not. That is what keeps
    # reuse from becoming a shared record.
    print("\nDOCUMENT IDENTITY")
    print(f"  acme doc: {document_id('acme', 'security.md')}")
    print(f"  beta doc: {document_id('beta', 'security.md')} (tenant-scoped)")

    catalog = InMemoryCatalog()
    embedder = DeterministicEmbedder()
    first = catalog.upsert(acme, embedder)
    second = catalog.upsert(beta, embedder)

    # The second sync pays nothing for embeddings and still gets its own chunks, its
    # own ACL, and its own lineage edges. Cheap where it is safe, separate where it
    # is not.
    print("\nWORK REUSE")
    print(f"  first sync created {first.embeddings_created} embedding")
    print(f"  second sync reused {second.embeddings_reused} embedding")
    print(f"  lineage edges retained: {len(catalog.lineage)}")
    print("\nTakeaway: deduplicate compute by content hash, never ACLs or provenance.")


if __name__ == "__main__":
    main()

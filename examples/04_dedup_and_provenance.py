"""Lesson 4: reuse content-addressed work without merging document identity."""

from ai_data.catalog import InMemoryCatalog
from ai_data.embedding import DeterministicEmbedder
from ai_data.identity import blob_id, document_id

from _fixtures import record


def main() -> None:
    shared_text = "Security keys rotate every ninety days."
    acme = record("security.md", text=shared_text)
    beta = record(
        "security.md",
        tenant="beta",
        text=shared_text,
        readers=frozenset({"user:bob"}),
    )
    print("CONTENT ADDRESS")
    print(f"  acme blob: {blob_id(acme.content)[:22]}...")
    print(f"  beta blob: {blob_id(beta.content)[:22]}... (same bytes)")
    print("\nDOCUMENT IDENTITY")
    print(f"  acme doc: {document_id('acme', 'security.md')}")
    print(f"  beta doc: {document_id('beta', 'security.md')} (tenant-scoped)")

    catalog = InMemoryCatalog()
    embedder = DeterministicEmbedder()
    first = catalog.upsert(acme, embedder)
    second = catalog.upsert(beta, embedder)
    print("\nWORK REUSE")
    print(f"  first sync created {first.embeddings_created} embedding")
    print(f"  second sync reused {second.embeddings_reused} embedding")
    print(f"  lineage edges retained: {len(catalog.lineage)}")
    print("\nTakeaway: deduplicate compute by content hash, never ACLs or provenance.")


if __name__ == "__main__":
    main()

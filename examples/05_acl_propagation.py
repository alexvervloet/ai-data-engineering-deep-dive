"""Lesson 5: authorization metadata travels with every derived chunk."""

from ai_data.catalog import InMemoryCatalog
from ai_data.embedding import DeterministicEmbedder

from _fixtures import record


def can_find(
    catalog: InMemoryCatalog,
    embedder: DeterministicEmbedder,
    *,
    tenant: str,
    principal: str,
) -> bool:
    return bool(
        catalog.search(
            tenant_id=tenant,
            principals=frozenset({principal}),
            query="rollback",
            embedder=embedder,
        )
    )


def main() -> None:
    catalog = InMemoryCatalog()
    embedder = DeterministicEmbedder()
    catalog.upsert(record("deploy.md", readers=frozenset({"user:alex"})), embedder)
    catalog.upsert(
        record("deploy.md", tenant="beta", readers=frozenset({"user:bob"})), embedder
    )

    print("BEFORE ACL CHANGE")
    print(f"  acme/alex: {can_find(catalog, embedder, tenant='acme', principal='user:alex')}")
    print(f"  acme/bob:  {can_find(catalog, embedder, tenant='acme', principal='user:bob')}")
    print(f"  beta/alex: {can_find(catalog, embedder, tenant='beta', principal='user:alex')}")

    report = catalog.upsert(
        record("deploy.md", version=2, readers=frozenset({"group:platform"})),
        embedder,
    )
    print("\nAFTER ACL-ONLY CHANGE")
    print(f"  acme/alex: {can_find(catalog, embedder, tenant='acme', principal='user:alex')}")
    print(f"  embeddings reused: {report.embeddings_reused}")
    print(f"  ACL changed: {report.acl_changed}")
    print("\nTakeaway: filter by trusted tenant + principals before ranking, every time.")


if __name__ == "__main__":
    main()

"""
Lesson 5: authorization metadata travels with every derived chunk.

This is where a retrieval bug stops being a quality problem and becomes an incident
with a lawyer attached.

The rule: the source document's access control list is copied onto every chunk cut
from it, and stored beside the vector. Permissions are not metadata to look up later.
They are part of the record, because at query time the chunk is what you have.

Then the query order matters, and the correct order is:

  1. derive the tenant and principals from trusted application context;
  2. filter to what that caller may read;
  3. rank only the authorized candidates;
  4. return provenance with each result.

The tempting alternative, rank first and filter the results, fails twice. It leaks,
because the protected content has already left the database and entered your
application, your logs, and possibly the model's context. And it silently
under-returns, because a top-ten that loses six unauthorized rows becomes a top-four
with no explanation to the user.

The word "trusted" in step 1 is load-bearing. If a tenant ID can arrive in a message,
then anything that can write a message chooses the tenant, and in an agentic system a
model writes the messages. A tenant selected by model output is not authorization, it
is a suggestion.

This example shows two things at once. Alex loses access through an ACL-only update,
and the update costs zero embedding calls because the text did not change: revocation
should be cheap, or teams will batch it, and batched revocation is delayed
revocation. Meanwhile a Beta document with the identical name stays invisible to
Acme's principals throughout.

Predict before running: access for acme/alex, acme/bob, and beta/alex, before and
after the update. Then move the ACL check after ranking and construct a case where a
user gets too few results.

Previous: lesson 4 established identity. Next: lesson 6 applies changes over time.
See README section 5 and TEXTBOOK.md section 19.6, which also covers the row-level
security trap in the Postgres capstone.

Run it:

    python examples/05_acl_propagation.py
"""

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
    """Ask the index the only question that matters: would this caller see it?

    Note that the tenant and the principal are arguments, not globals. In a real
    application both come from the authenticated session, and the value of writing
    the helper this way is that there is nowhere for a request-supplied tenant to
    sneak in.
    """

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

    # Two tenants, same document name, different readers. Beta's copy exists for the
    # whole run and should never appear in an Acme result, regardless of how well it
    # matches the query.
    catalog.upsert(record("deploy.md", readers=frozenset({"user:alex"})), embedder)
    catalog.upsert(
        record("deploy.md", tenant="beta", readers=frozenset({"user:bob"})), embedder
    )

    print("BEFORE ACL CHANGE")
    print(f"  acme/alex: {can_find(catalog, embedder, tenant='acme', principal='user:alex')}")
    print(f"  acme/bob:  {can_find(catalog, embedder, tenant='acme', principal='user:bob')}")
    print(f"  beta/alex: {can_find(catalog, embedder, tenant='beta', principal='user:alex')}")

    # A new version whose only change is the reader list. The text is identical, so
    # the content hashes are identical, so every embedding is served from cache. The
    # chunks are still rewritten, because they carry the ACL.
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

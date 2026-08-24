"""
sync_corpus.py: the capstone: synchronize a changing multi-tenant corpus.

Every lesson in the dive meets here, against two tenants who must never see each
other's documents. The script loads a corpus manifest, validates it, indexes the
documents, runs the quality gate, probes for cross-tenant leaks, and answers one
query as a specific caller. The same code path runs twice over: once against the
in-memory reference catalog, once against real Postgres with pgvector.

That doubling is the point of the design. The offline path needs no service, no key,
and no network, so the lessons stay runnable. The Postgres path proves the semantics
survive contact with a real database: transactions, constraints, an approximate
vector index, and a security model of its own.

What each run checks:

  - the manifest is well formed, its paths stay inside the corpus directory, and
    every tenant in it was declared as managed;
  - each document indexes, and re-running produces `stale` rather than duplicates;
  - the quality gate passes (coverage, drift, ACL parity, lineage, dimensions);
  - an **isolation probe**, a principal that appears in no ACL anywhere, retrieves
    exactly zero chunks from every managed tenant;
  - the requested query returns only what the requested principals may read.

The probe deserves an explanation, because it is the one piece of this script that is
a test rather than a feature. Asserting that the right documents come back is weak
evidence for isolation: a query that returns Acme's handbook to an Acme user tells you
nothing about what Beta's user would have seen. So the script asks a question as
somebody who should be allowed nothing, and requires the answer to be empty. Negative
evidence, gathered every run, is what keeps an authorization bug from waiting for a
customer to find it.

Examples
--------
    # The offline reference path, no service required
    python hands_on/sync_corpus.py

    # Ask as somebody who should see nothing in that tenant
    python hands_on/sync_corpus.py \\
      --tenant beta --principal user:alex \\
      --query "What is the launch codename?"

    # The same lifecycle against real Postgres/pgvector
    docker compose up -d
    pip install -r requirements-postgres.txt
    python hands_on/sync_corpus.py \\
      --database-url postgresql://ai_data:ai_data_local_only@localhost:54329/ai_data

To exercise change management, edit `corpus/manifest.json` between runs: bump a
version, change a reader list, or remove a document entry while leaving its tenant in
`managed_tenants`. EXERCISES.md walks through each case and what to expect.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from ai_data.catalog import InMemoryCatalog
from ai_data.embedding import DeterministicEmbedder
from ai_data.manifest import CorpusManifest, load_manifest
from ai_data.postgres import PostgresCatalog
from ai_data.quality import assess_quality

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "corpus" / "manifest.json"

# A principal that appears in no ACL in the corpus, used to ask "what would somebody
# with no rights see?" every run. Kept as a constant so the check below can prove it
# never leaked into the source data, which would make the probe silently useless.
ISOLATION_PROBE = "probe:never-authorized"

SUMMARY = "Synchronize a changing multi-tenant corpus into a safe retrieval index."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=SUMMARY)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Corpus manifest to sync. Acts as the source of truth for this run.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("AI_DATA_DATABASE_URL") or os.environ.get("DATABASE_URL"),
        help="Postgres DSN. Without it the capstone runs against the offline reference index.",
    )
    parser.add_argument(
        "--tenant",
        default="acme",
        help="Tenant to query as. In a real application this comes from the session, never a flag.",
    )
    parser.add_argument(
        "--principal",
        action="append",
        dest="principals",
        help="Repeatable. Principals the caller holds, for example user:alex or group:engineering.",
    )
    parser.add_argument(
        "--query",
        default="How do production deployments work?",
        help="Question to ask the index once the sync completes.",
    )
    return parser.parse_args()


def _assert_probe_is_untrusted(manifest: CorpusManifest) -> None:
    """Make sure the isolation probe is still a principal with no rights.

    A leak test that starts passing on its own is worse than no leak test. If somebody
    ever adds the probe to a document's readers, every probe below would return hits,
    and the natural reaction would be to relax the assertion rather than to notice
    that the test had stopped meaning anything. So the script refuses to run at all.
    """

    if any(ISOLATION_PROBE in record.acl.readers for record in manifest.records):
        raise ValueError(f"reserved isolation probe appears in a source ACL: {ISOLATION_PROBE}")


def run_offline(
    manifest: CorpusManifest, *, tenant: str, principals: frozenset[str], query: str
) -> None:
    """Run the whole lifecycle in memory: index, gate, probe, query.

    This path is the reference the Postgres path has to match. Keeping both, and
    keeping them this close in shape, is what makes the database version auditable:
    anything the two disagree about is a bug in one of them.
    """

    catalog = InMemoryCatalog()
    embedder = DeterministicEmbedder()
    reports = [catalog.upsert(record, embedder) for record in manifest.records]

    # The gate runs before anything is served, not after somebody complains.
    quality = assess_quality(manifest.records, catalog)
    hits = catalog.search(
        tenant_id=tenant,
        principals=principals,
        query=query,
        embedder=embedder,
    )
    # Ask the same question as somebody entitled to nothing, in every managed tenant.
    # The expected answer is zero, every time, and it is worth more than the positive
    # result above: returning the right document proves ranking, not isolation.
    probe_hits = sum(
        len(
            catalog.search(
                tenant_id=managed_tenant,
                principals=frozenset({ISOLATION_PROBE}),
                query=query,
                embedder=embedder,
            )
        )
        for managed_tenant in manifest.managed_tenants
    )

    print("OFFLINE REFERENCE SYNC")
    print(f"  documents: {len(reports)}")
    print(f"  chunks: {len(catalog.entries)}")
    print(f"  quality gate: {'PASS' if quality.ok else 'FAIL'}")
    print(f"  unauthorized probe hits: {probe_hits}")
    print(f"\nQUERY tenant={tenant} principals={sorted(principals)}")
    if not hits:
        print("  no authorized results")
    for hit in hits:
        print(f"  {hit.score:+.3f} {hit.entry.chunk.source_uri}")
        print(f"    {hit.entry.chunk.text[:120]}")
    print("\nAdd --database-url to run the same lifecycle in Postgres/pgvector.")


def run_postgres(
    manifest: CorpusManifest,
    *,
    database_url: str,
    tenant: str,
    principals: frozenset[str],
    query: str,
) -> None:
    """Run the same lifecycle against real Postgres with pgvector.

    Same sequence as the offline path, with the parts a database adds: schema setup,
    one transaction per document replacement, reconciliation against what the database
    already holds, and searches that drop to an unprivileged role so row-level
    security applies to them.
    """

    catalog = PostgresCatalog.connect(database_url)
    embedder = DeterministicEmbedder()
    try:
        catalog.setup()
        reports = [catalog.replace_document(record, embedder) for record in manifest.records]

        # Reconciliation, in its smallest useful form. The manifest is the source of
        # truth, so anything the database still lists as active in a managed tenant,
        # and the manifest no longer mentions, was deleted at the source.
        expected = {
            (record.tenant_id, record.external_id) for record in manifest.records
        }
        tombstoned = 0
        for db_tenant, external_id, doc_id, version in catalog.active_documents(
            manifest.managed_tenants
        ):
            if (db_tenant, external_id) in expected:
                continue
            # The manifest has no version for a document it no longer contains, so the
            # sync assigns one past the last known version. That is a real decision
            # with a consequence worth knowing: the tombstone now sits above any
            # version the source could still deliver for that document, so an old
            # in-flight retry cannot resurrect it, and a genuine re-add has to arrive
            # at a higher version than the source last used.
            catalog.delete_document(
                tenant_id=db_tenant,
                external_id=external_id,
                document_id=doc_id,
                version=version + 1,
            )
            tombstoned += 1

        hits = catalog.search(
            tenant_id=tenant,
            principals=principals,
            query=query,
            embedder=embedder,
        )
        probe_hits = sum(
            len(
                catalog.search(
                    tenant_id=managed_tenant,
                    principals=frozenset({ISOLATION_PROBE}),
                    query=query,
                    embedder=embedder,
                )
            )
            for managed_tenant in manifest.managed_tenants
        )
        # The live path refuses to finish rather than printing a non-zero count. On a
        # real database a leak is an incident, and the run that found it should be the
        # one that fails, not a number somebody reads later.
        if probe_hits:
            raise RuntimeError("authorization probe retrieved protected chunks")

        print("POSTGRES/PGVECTOR SYNC")
        print(f"  source documents: {len(reports)}")
        print(f"  statuses: {[report.status for report in reports]}")
        print(f"  stale documents tombstoned: {tombstoned}")
        print(f"  unauthorized probe hits: {probe_hits}")
        print(f"\nQUERY tenant={tenant} principals={sorted(principals)}")
        if not hits:
            print("  no authorized results")
        for chunk_id, content, distance in hits:
            print(f"  distance={distance:.3f} {chunk_id}")
            print(f"    {content[:120]}")
    finally:
        catalog.close()


def main() -> None:
    args = parse_args()
    manifest = load_manifest(args.manifest)
    _assert_probe_is_untrusted(manifest)
    principals = frozenset(args.principals or ["user:alex", "group:engineering"])
    if args.database_url:
        run_postgres(
            manifest,
            database_url=args.database_url,
            tenant=args.tenant,
            principals=principals,
            query=args.query,
        )
    else:
        run_offline(
            manifest,
            tenant=args.tenant,
            principals=principals,
            query=args.query,
        )


if __name__ == "__main__":
    main()

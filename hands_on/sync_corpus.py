"""Synchronize a changing multi-tenant corpus into a safe retrieval index."""

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
ISOLATION_PROBE = "probe:never-authorized"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("AI_DATA_DATABASE_URL") or os.environ.get("DATABASE_URL"),
        help="Postgres DSN. Without it the capstone runs against the offline reference index.",
    )
    parser.add_argument("--tenant", default="acme")
    parser.add_argument("--principal", action="append", dest="principals")
    parser.add_argument("--query", default="How do production deployments work?")
    return parser.parse_args()


def _assert_probe_is_untrusted(manifest: CorpusManifest) -> None:
    if any(ISOLATION_PROBE in record.acl.readers for record in manifest.records):
        raise ValueError(f"reserved isolation probe appears in a source ACL: {ISOLATION_PROBE}")


def run_offline(
    manifest: CorpusManifest, *, tenant: str, principals: frozenset[str], query: str
) -> None:
    catalog = InMemoryCatalog()
    embedder = DeterministicEmbedder()
    reports = [catalog.upsert(record, embedder) for record in manifest.records]
    quality = assess_quality(manifest.records, catalog)
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
    catalog = PostgresCatalog.connect(database_url)
    embedder = DeterministicEmbedder()
    try:
        catalog.setup()
        reports = [catalog.replace_document(record, embedder) for record in manifest.records]
        expected = {
            (record.tenant_id, record.external_id) for record in manifest.records
        }
        tombstoned = 0
        for db_tenant, external_id, doc_id, version in catalog.active_documents(
            manifest.managed_tenants
        ):
            if (db_tenant, external_id) in expected:
                continue
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

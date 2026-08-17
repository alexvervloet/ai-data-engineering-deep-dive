from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from ai_data.catalog import InMemoryCatalog
from ai_data.connectors import MemoryConnector
from ai_data.contracts import ContractViolation, source_from_mapping
from ai_data.embedding import DeterministicEmbedder, plan_batches
from ai_data.identity import chunk_id, document_id
from ai_data.models import AccessControl, SourceRecord
from ai_data.pipeline import SyncPipeline
from ai_data.quality import assess_quality
from ai_data.reconcile import reconcile
from ai_data.recovery import BackupCorrupt, create_backup, restore_backup


def source(
    external_id: str,
    *,
    tenant: str = "acme",
    version: int = 1,
    text: str = "Alpha handbook.\n\nOnly engineers may deploy.",
    readers: frozenset[str] = frozenset({"user:alex"}),
) -> SourceRecord:
    return SourceRecord(
        tenant_id=tenant,
        external_id=external_id,
        version=version,
        updated_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        source_uri=f"file:///{tenant}/{external_id}.md",
        mime_type="text/markdown",
        content=text.encode(),
        acl=AccessControl(readers),
        metadata={"owner": "docs"},
    )


class FailingEmbedder(DeterministicEmbedder):
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        raise RuntimeError("provider unavailable")


class PipelineTests(unittest.TestCase):
    def test_contract_rejects_unknown_fields(self) -> None:
        payload: dict[str, object] = {
            "contract_version": "2",
            "tenant_id": "acme",
            "external_id": "guide",
            "version": 1,
            "updated_at": "2026-08-17T00:00:00+00:00",
            "source_uri": "file:///guide.md",
            "mime_type": "text/markdown",
            "content": "hello",
            "readers": ["user:alex"],
            "model_can_choose_tenant": True,
        }
        with self.assertRaisesRegex(ContractViolation, "unknown field"):
            source_from_mapping(payload)

    def test_document_ids_are_tenant_scoped(self) -> None:
        self.assertNotEqual(document_id("acme", "guide"), document_id("beta", "guide"))

    def test_identity_scheme_is_pinned(self) -> None:
        """An ID scheme is a compatibility contract with every index already written.

        Refactoring the hash input is not a cosmetic change: it renames every document
        and chunk in production, orphans the rows under the old names, and re-embeds a
        corpus that did not change. These literals make that consequence impossible to
        cause by accident.
        """

        self.assertEqual(document_id("acme", "security.md"), "doc_27e1f9baa185421bec51809a")
        self.assertEqual(chunk_id("doc_abc", 0, "hello"), "chk_5137fa38d490275b1ff1c627")

    def test_identity_separator_prevents_field_boundary_collisions(self) -> None:
        self.assertNotEqual(document_id("acme", "bguide"), document_id("acmeb", "guide"))

    def test_snapshot_then_cdc_has_no_gap(self) -> None:
        connector = MemoryConnector()
        connector.upsert(source("one"))
        snapshot, cursor = connector.capture_snapshot()
        connector.upsert(source("two"))

        self.assertEqual([record.external_id for record in snapshot], ["one"])
        self.assertEqual(
            [event.external_id for event in connector.changes_after(cursor).events], ["two"]
        )

    def test_acl_only_update_reuses_embeddings_and_revokes_access(self) -> None:
        catalog = InMemoryCatalog()
        embedder = DeterministicEmbedder()
        catalog.upsert(source("guide"), embedder)
        calls_after_first = embedder.calls

        report = catalog.upsert(
            source("guide", version=2, readers=frozenset({"group:ops"})), embedder
        )

        self.assertTrue(report.acl_changed)
        self.assertGreater(report.embeddings_reused, 0)
        self.assertEqual(embedder.calls, calls_after_first)
        self.assertFalse(
            catalog.search(
                tenant_id="acme",
                principals=frozenset({"user:alex"}),
                query="deploy",
                embedder=embedder,
            )
        )

    def test_delete_tombstone_blocks_late_resurrection(self) -> None:
        catalog = InMemoryCatalog()
        embedder = DeterministicEmbedder()
        catalog.upsert(source("guide"), embedder)
        catalog.delete("acme", "guide", 3)

        late = catalog.upsert(source("guide", version=2), embedder)

        self.assertEqual(late.status, "stale")
        self.assertTrue(catalog.documents[("acme", "guide")].deleted)
        self.assertFalse(catalog.entries)

    def test_backfill_may_rewrite_a_live_document_at_the_same_version(self) -> None:
        catalog = InMemoryCatalog()
        embedder = DeterministicEmbedder()
        catalog.upsert(source("guide"), embedder)

        report = catalog.upsert(source("guide"), embedder, force=True, max_chars=50)

        self.assertEqual(report.status, "backfilled")
        self.assertTrue(catalog.entries)

    def test_backfill_may_not_resurrect_a_tombstoned_document(self) -> None:
        """A migration rerun must not undo a delete it happens to run alongside.

        Backfills read the current source snapshot, which was captured before or around
        the delete. Allowing an equal version to clear `deleted_at` would let a routine
        transform migration silently republish content the source removed.
        """

        catalog = InMemoryCatalog()
        embedder = DeterministicEmbedder()
        catalog.upsert(source("guide"), embedder)
        catalog.delete("acme", "guide", 2)

        replay = catalog.upsert(source("guide", version=2), embedder, force=True)

        self.assertEqual(replay.status, "stale")
        self.assertTrue(catalog.documents[("acme", "guide")].deleted)
        self.assertFalse(catalog.entries)

    def test_a_strictly_newer_source_event_does_lift_a_tombstone(self) -> None:
        catalog = InMemoryCatalog()
        embedder = DeterministicEmbedder()
        catalog.upsert(source("guide"), embedder)
        catalog.delete("acme", "guide", 2)

        revived = catalog.upsert(source("guide", version=3), embedder)

        self.assertEqual(revived.status, "updated")
        self.assertFalse(catalog.documents[("acme", "guide")].deleted)

    def test_search_enforces_tenant_and_acl_before_ranking(self) -> None:
        catalog = InMemoryCatalog()
        embedder = DeterministicEmbedder()
        catalog.upsert(source("secret", text="Acme launch code violet"), embedder)
        catalog.upsert(
            source(
                "secret",
                tenant="beta",
                text="Beta launch code orange",
                readers=frozenset({"user:bob"}),
            ),
            embedder,
        )

        acme_hits = catalog.search(
            tenant_id="acme",
            principals=frozenset({"user:alex"}),
            query="launch code",
            embedder=embedder,
        )
        denied = catalog.search(
            tenant_id="beta",
            principals=frozenset({"user:alex"}),
            query="launch code",
            embedder=embedder,
        )

        self.assertEqual(len(acme_hits), 1)
        self.assertIn("violet", acme_hits[0].entry.chunk.text)
        self.assertEqual(denied, ())

    def test_failed_embedding_leaves_previous_document_visible(self) -> None:
        catalog = InMemoryCatalog()
        embedder = DeterministicEmbedder()
        catalog.upsert(source("guide"), embedder)
        before = dict(catalog.entries)

        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            catalog.upsert(
                source("guide", version=2, text="Completely new content"),
                FailingEmbedder(),
            )

        self.assertEqual(catalog.entries, before)
        self.assertEqual(catalog.documents[("acme", "guide")].source_version, 1)

    def test_pipeline_bootstrap_and_change_drain(self) -> None:
        connector = MemoryConnector()
        connector.upsert(source("one"))
        pipeline = SyncPipeline(connector, InMemoryCatalog(), DeterministicEmbedder())
        pipeline.bootstrap()
        connector.upsert(source("one", version=2, text="updated"))
        connector.upsert(source("two"))
        connector.delete("acme", "two", 2)

        runs = pipeline.drain(limit=2)

        self.assertEqual(len(runs), 2)
        self.assertEqual(pipeline.cursor, connector.high_watermark)
        self.assertEqual(
            pipeline.catalog.documents[("acme", "one")].source_version, 2
        )
        self.assertTrue(pipeline.catalog.documents[("acme", "two")].deleted)

    def test_reconciliation_and_quality_expose_drift(self) -> None:
        catalog = InMemoryCatalog()
        embedder = DeterministicEmbedder()
        record = source("guide")
        catalog.upsert(record, embedder)
        chunk_key = next(iter(catalog.entries))
        catalog.entries.pop(chunk_key)

        findings = reconcile((record,), catalog)
        report = assess_quality((record,), catalog)

        self.assertIn("missing_chunks", {finding.kind for finding in findings})
        self.assertFalse(report.ok)

    def test_quality_gate_fails_instead_of_raising_on_a_chunk_with_no_document(
        self,
    ) -> None:
        """The gate has to survive the corruption it exists to detect.

        `reconcile` already reports this state as a dangling chunk, so it is a state the
        pipeline expects to meet. Reading the chunk's ACL through its missing document
        row used to raise KeyError, which turned a failed release into a failed job.
        """

        catalog = InMemoryCatalog()
        embedder = DeterministicEmbedder()
        record = source("guide")
        catalog.upsert(record, embedder)
        catalog.documents.pop(("acme", "guide"))

        report = assess_quality((record,), catalog)

        self.assertFalse(report.ok)
        failed = {check.name for check in report.checks if not check.passed}
        self.assertIn("chunks have an owning document", failed)

    def test_embedding_batches_respect_item_limits(self) -> None:
        catalog = InMemoryCatalog()
        embedder = DeterministicEmbedder()
        catalog.upsert(source("guide", text="One.\n\nTwo.\n\nThree."), embedder, max_chars=50)
        chunks = tuple(entry.chunk for entry in catalog.entries.values())

        batches = plan_batches(chunks, max_items=1, max_tokens=100)

        self.assertTrue(all(len(batch.chunks) == 1 for batch in batches))

    def test_embedding_cache_does_not_mix_dimensions(self) -> None:
        catalog = InMemoryCatalog()
        catalog.upsert(source("guide"), DeterministicEmbedder(dimensions=8))

        report = catalog.upsert(
            source("guide", tenant="beta", readers=frozenset({"user:bob"})),
            DeterministicEmbedder(dimensions=16),
        )

        self.assertEqual(report.embeddings_created, 1)
        beta_entry = next(
            entry
            for (tenant_id, _), entry in catalog.entries.items()
            if tenant_id == "beta"
        )
        self.assertEqual(len(beta_entry.embedding), 16)

    def test_backup_round_trip_and_tamper_detection(self) -> None:
        record = source("guide")
        serialized = create_backup((record,), cdc_cursor=7)

        restored, cursor = restore_backup(serialized)
        self.assertEqual(restored, (record,))
        self.assertEqual(cursor, 7)

        envelope = json.loads(serialized)
        envelope["body"]["records"][0]["content_base64"] = "dGFtcGVyZWQ="
        with self.assertRaises(BackupCorrupt):
            restore_backup(json.dumps(envelope))


if __name__ == "__main__":
    unittest.main()

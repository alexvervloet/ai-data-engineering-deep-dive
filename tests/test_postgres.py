from __future__ import annotations

import os
import unittest

from ai_data.embedding import DeterministicEmbedder
from ai_data.identity import document_id
from ai_data.postgres import (
    SCHEMA_STATEMENTS,
    PostgresCatalog,
    _vector_literal,
)

from .test_pipeline import source


class PostgresContractTests(unittest.TestCase):
    def test_schema_has_acl_rls_and_filtered_vector_indexes(self) -> None:
        schema = "\n".join(SCHEMA_STATEMENTS)

        self.assertIn("ENABLE ROW LEVEL SECURITY", schema)
        self.assertIn("current_setting('app.tenant_id'", schema)
        self.assertIn("acl &&", schema)
        self.assertIn("vector_cosine_ops", schema)
        self.assertIn("ai_chunks_tenant_idx", schema)

    def test_vector_literal_rejects_wrong_dimensions(self) -> None:
        with self.assertRaisesRegex(ValueError, "16 embedding dimensions"):
            _vector_literal((0.0, 1.0))


@unittest.skipUnless(
    os.environ.get("AI_DATA_TEST_DATABASE_URL"),
    "set AI_DATA_TEST_DATABASE_URL for the pgvector integration test",
)
class PostgresIntegrationTests(unittest.TestCase):
    def test_tenant_acl_update_and_delete_lifecycle(self) -> None:
        dsn = os.environ["AI_DATA_TEST_DATABASE_URL"]
        catalog = PostgresCatalog.connect(dsn)
        embedder = DeterministicEmbedder()
        tenant = "integration_test"
        try:
            catalog.setup()
            with catalog.connection.transaction():
                catalog.connection.execute(
                    "DELETE FROM ai_documents WHERE tenant_id = %s", (tenant,)
                )
            record = source(
                "integration-guide",
                tenant=tenant,
                text="Integration tenant secret violet",
            )
            catalog.replace_document(record, embedder)

            allowed = catalog.search(
                tenant_id=tenant,
                principals=frozenset({"user:alex"}),
                query="tenant secret",
                embedder=embedder,
            )
            denied = catalog.search(
                tenant_id=tenant,
                principals=frozenset({"user:mallory"}),
                query="tenant secret",
                embedder=embedder,
            )
            self.assertEqual(len(allowed), 1)
            self.assertEqual(denied, ())

            catalog.delete_document(
                tenant_id=tenant,
                external_id="integration-guide",
                document_id=document_id(tenant, "integration-guide"),
                version=2,
            )
            self.assertEqual(
                catalog.search(
                    tenant_id=tenant,
                    principals=frozenset({"user:alex"}),
                    query="tenant secret",
                    embedder=embedder,
                ),
                (),
            )
        finally:
            with catalog.connection.transaction():
                catalog.connection.execute(
                    "DELETE FROM ai_documents WHERE tenant_id = %s", (tenant,)
                )
            catalog.close()


if __name__ == "__main__":
    unittest.main()

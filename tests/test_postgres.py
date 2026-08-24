from __future__ import annotations

import os
import unittest

from ai_data.embedding import DeterministicEmbedder
from ai_data.identity import document_id
from ai_data.postgres import (
    READER_ROLE,
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

    def test_schema_creates_a_reader_role_the_policy_can_apply_to(self) -> None:
        schema = "\n".join(SCHEMA_STATEMENTS)

        self.assertIn(f"CREATE ROLE {READER_ROLE}", schema)
        self.assertIn(f"GRANT SELECT ON ai_chunks TO {READER_ROLE}", schema)
        # No write grants: the reader may read chunks and nothing else.
        self.assertNotIn("GRANT INSERT", schema)
        self.assertNotIn("GRANT ALL", schema)

    def test_vector_literal_rejects_wrong_dimensions(self) -> None:
        with self.assertRaisesRegex(ValueError, "16 embedding dimensions"):
            _vector_literal((0.0, 1.0))


@unittest.skipUnless(
    os.environ.get("AI_DATA_TEST_DATABASE_URL"),
    "set AI_DATA_TEST_DATABASE_URL for the pgvector integration test",
)
class PostgresIntegrationTests(unittest.TestCase):
    def catalog_for(self, tenant: str) -> PostgresCatalog:
        """Open a catalog whose tenant is empty before the test and after it.

        Each test owns a tenant of its own. Sharing one would make the suite
        order-dependent: a test that correctly leaves a v2 tombstone behind would make
        the next run's v1 insert fail as stale, which is the pipeline behaving properly
        and the fixture behaving badly.
        """

        catalog = PostgresCatalog.connect(os.environ["AI_DATA_TEST_DATABASE_URL"])
        catalog.setup()
        self.addCleanup(catalog.close)

        def purge() -> None:
            with catalog.connection.transaction():
                catalog.connection.execute(
                    "DELETE FROM ai_documents WHERE tenant_id = %s", (tenant,)
                )

        purge()
        self.addCleanup(purge)
        return catalog

    def test_tenant_acl_update_and_delete_lifecycle(self) -> None:
        tenant = "integration_test"
        catalog = self.catalog_for(tenant)
        embedder = DeterministicEmbedder()
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

    def test_row_level_security_catches_a_query_that_lost_its_predicates(self) -> None:
        """The point of the second layer: survive a bug in the first one.

        This is the query an application writes by accident, with the tenant and ACL
        filters missing entirely. Under the reader role the policy answers instead.
        """

        tenant = "integration_rls"
        catalog = self.catalog_for(tenant)
        embedder = DeterministicEmbedder()
        catalog.replace_document(
            source("rls-guide", tenant=tenant, text="Protected tenant secret indigo"),
            embedder,
        )

        with catalog.connection.transaction():
            catalog.connection.execute(
                "SELECT set_config('app.tenant_id', %s, true)", ("some_other_tenant",)
            )
            catalog.connection.execute(
                "SELECT set_config('app.principals', %s, true)", ("user:mallory",)
            )
            catalog.connection.execute(f"SET LOCAL ROLE {READER_ROLE}")
            leaked = catalog.connection.execute(
                "SELECT tenant_id, content FROM ai_chunks"
            ).fetchall()

        self.assertEqual(leaked, [])

    def test_the_table_owner_is_exempt_from_the_policy(self) -> None:
        """The gotcha, kept as a test so it cannot come back unnoticed.

        Postgres skips a table's policies for its owner unless the table is declared
        FORCE ROW LEVEL SECURITY. An application that connects as the owner, which is
        what happens when the migration user is also the runtime user, gets a policy
        that is present, correct, and enforcing nothing. Dropping to a role that owns
        nothing is what turns the third layer on.
        """

        tenant = "integration_owner"
        catalog = self.catalog_for(tenant)
        embedder = DeterministicEmbedder()
        catalog.replace_document(
            source("owner-guide", tenant=tenant, text="Owner visible secret amber"),
            embedder,
        )

        with catalog.connection.transaction():
            catalog.connection.execute(
                "SELECT set_config('app.tenant_id', %s, true)", ("some_other_tenant",)
            )
            catalog.connection.execute(
                "SELECT set_config('app.principals', %s, true)", ("user:mallory",)
            )
            visible = catalog.connection.execute(
                "SELECT tenant_id FROM ai_chunks WHERE tenant_id = %s", (tenant,)
            ).fetchall()

        self.assertEqual(len(visible), 1)

    def test_the_reader_role_cannot_write_or_read_the_document_table(self) -> None:
        """Least privilege is the layer that does not depend on getting a policy right."""

        # Imported here, not at module scope: unittest discovery imports this file even
        # when the optional driver is absent, and the offline suite must still run.
        import psycopg

        tenant = "integration_privilege"
        catalog = self.catalog_for(tenant)
        catalog.replace_document(
            source("privilege-guide", tenant=tenant), DeterministicEmbedder()
        )

        for statement in (
            "SELECT * FROM ai_documents",
            "DELETE FROM ai_chunks",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with catalog.connection.transaction():
                        catalog.connection.execute(f"SET LOCAL ROLE {READER_ROLE}")
                        catalog.connection.execute(statement)

    def test_backfill_mode_does_not_clear_a_tombstone(self) -> None:
        """The database, not the application, is the last line of version ordering.

        Two workers can race here: one applying a delete, one rerunning a backfill from
        a snapshot taken before it. The upsert's WHERE clause has to decide the outcome,
        because whichever worker loses the race still gets to run its statement.
        """

        tenant = "integration_backfill"
        external_id = "migrating-guide"
        catalog = self.catalog_for(tenant)
        embedder = DeterministicEmbedder()
        record = source(external_id, tenant=tenant, text="Original transform output")
        catalog.replace_document(record, embedder)

        # A transform migration reruns the same source version. That is allowed.
        rewritten = catalog.replace_document(record, embedder, force=True)
        self.assertEqual(rewritten.status, "indexed")

        catalog.delete_document(
            tenant_id=tenant,
            external_id=external_id,
            document_id=document_id(tenant, external_id),
            version=2,
        )
        # The same migration reruns at the deleted version. That is not.
        resurrected = catalog.replace_document(
            source(external_id, tenant=tenant, version=2), embedder, force=True
        )

        self.assertEqual(resurrected.status, "stale")
        self.assertEqual(
            catalog.search(
                tenant_id=tenant,
                principals=frozenset({"user:alex"}),
                query="transform output",
                embedder=embedder,
            ),
            (),
        )


if __name__ == "__main__":
    unittest.main()

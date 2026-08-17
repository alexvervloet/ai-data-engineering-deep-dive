"""A small, real Postgres/pgvector backend for the capstone.

Three layers guard a read here, and they are deliberately not redundant:

1. the application query filters on tenant and ACL before ranking;
2. the reader role holds `SELECT` on chunks and nothing else, so a bug cannot reach
   the document table, let alone write;
3. row-level security re-checks tenant and ACL inside the database.

Layer 3 only works because searches run as a role that does not own the tables.
Postgres exempts a table's owner from its own policies unless the table is declared
`FORCE ROW LEVEL SECURITY`, so an application that connects as the owner, which is
the default for anything created by a migration, gets a policy that is syntactically
present and functionally inert. `RLS_DEMONSTRATION` and its tests show both halves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .chunking import chunk_document
from .embedding import DeterministicEmbedder, plan_batches
from .models import SourceRecord
from .parsing import parse_document

VECTOR_DIMENSIONS = 16

# Searches switch to this role for the duration of the query. It owns nothing, so the
# policy below actually applies to it, and it holds SELECT on the chunk table only.
READER_ROLE = "ai_data_reader"

SCHEMA_STATEMENTS = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS ai_documents (
        tenant_id text NOT NULL,
        document_id text NOT NULL,
        external_id text NOT NULL,
        source_uri text,
        source_version bigint NOT NULL,
        content_hash text,
        acl text[] NOT NULL,
        parser_version text,
        updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz,
        PRIMARY KEY (tenant_id, document_id),
        UNIQUE (tenant_id, external_id),
        CHECK (cardinality(acl) > 0 OR deleted_at IS NOT NULL)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_chunks (
        tenant_id text NOT NULL,
        document_id text NOT NULL,
        chunk_id text NOT NULL,
        ordinal integer NOT NULL,
        content text NOT NULL,
        content_hash text NOT NULL,
        source_uri text NOT NULL,
        source_version bigint NOT NULL,
        acl text[] NOT NULL CHECK (cardinality(acl) > 0),
        embedding_model text NOT NULL,
        embedding vector(16) NOT NULL,
        indexed_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, chunk_id),
        FOREIGN KEY (tenant_id, document_id)
            REFERENCES ai_documents (tenant_id, document_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS ai_chunks_tenant_idx ON ai_chunks (tenant_id)",
    "CREATE INDEX IF NOT EXISTS ai_chunks_acl_idx ON ai_chunks USING gin (acl)",
    """
    CREATE INDEX IF NOT EXISTS ai_chunks_embedding_hnsw_idx
    ON ai_chunks USING hnsw (embedding vector_cosine_ops)
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ai_data_reader') THEN
            CREATE ROLE ai_data_reader NOLOGIN;
        END IF;
        EXECUTE format('GRANT ai_data_reader TO %I', current_user);
    END
    $$
    """,
    "GRANT USAGE ON SCHEMA public TO ai_data_reader",
    "GRANT SELECT ON ai_chunks TO ai_data_reader",
    "ALTER TABLE ai_chunks ENABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS ai_chunks_reader_policy ON ai_chunks",
    """
    CREATE POLICY ai_chunks_reader_policy ON ai_chunks FOR SELECT USING (
        tenant_id = current_setting('app.tenant_id', true)
        AND acl && string_to_array(
            coalesce(current_setting('app.principals', true), ''), ','
        )
    )
    """,
)

RLS_DEMONSTRATION = """
Row-level security protects the reader role, not the owner.

    SET LOCAL ROLE ai_data_reader;
    SELECT set_config('app.tenant_id', 'acme', true);
    SELECT set_config('app.principals', 'user:alex', true);
    SELECT tenant_id, chunk_id FROM ai_chunks;   -- only Acme rows Alex may read

Run the same three statements without the SET LOCAL ROLE and every row in every
tenant comes back, because the connection owns the table. That is the failure mode
worth remembering: a policy can be present, correct, and doing nothing.
"""


@dataclass(frozen=True, slots=True)
class PostgresSyncReport:
    status: str
    tenant_id: str
    external_id: str
    source_version: int
    chunks_written: int


def _vector_literal(values: tuple[float, ...]) -> str:
    if len(values) != VECTOR_DIMENSIONS:
        raise ValueError(f"expected {VECTOR_DIMENSIONS} embedding dimensions")
    return "[" + ",".join(str(value) for value in values) + "]"


class PostgresCatalog:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    @classmethod
    def connect(cls, dsn: str) -> PostgresCatalog:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "Install the Postgres extra: pip install -r requirements-postgres.txt"
            ) from exc
        return cls(psycopg.connect(dsn))

    def close(self) -> None:
        self.connection.close()

    def setup(self) -> None:
        with self.connection.transaction():
            for statement in SCHEMA_STATEMENTS:
                self.connection.execute(statement)

    def active_documents(
        self, managed_tenants: tuple[str, ...]
    ) -> tuple[tuple[str, str, str, int], ...]:
        """Return service-side state for stale-document reconciliation."""

        if not managed_tenants:
            return ()
        with self.connection.transaction():
            rows = self.connection.execute(
                """
                SELECT tenant_id, external_id, document_id, source_version
                FROM ai_documents
                WHERE tenant_id = ANY(%s) AND deleted_at IS NULL
                ORDER BY tenant_id, external_id
                """,
                (list(managed_tenants),),
            ).fetchall()
        return tuple(
            (tenant_id, external_id, doc_id, int(version))
            for tenant_id, external_id, doc_id, version in rows
        )

    def replace_document(
        self,
        record: SourceRecord,
        embedder: DeterministicEmbedder,
        *,
        force: bool = False,
    ) -> PostgresSyncReport:
        parsed = parse_document(record)
        result = chunk_document(parsed)
        vectors: dict[str, tuple[float, ...]] = {}
        for batch in plan_batches(result.chunks):
            batch_vectors = embedder.embed(tuple(chunk.text for chunk in batch.chunks))
            vectors.update(
                (chunk.chunk_id, vector)
                for chunk, vector in zip(batch.chunks, batch_vectors, strict=True)
            )

        # The same rule the in-memory catalog documents, expressed as the WHERE clause
        # of the upsert so the database enforces it under concurrency. An ordinary
        # event must be strictly newer. A backfill may also rewrite a live document at
        # its existing version, but never a deleted one: clearing a tombstone requires
        # a strictly newer source event, not a rerun of an old snapshot.
        version_guard = "ai_documents.source_version < EXCLUDED.source_version"
        if force:
            version_guard += (
                " OR (ai_documents.source_version = EXCLUDED.source_version"
                " AND ai_documents.deleted_at IS NULL)"
            )
        with self.connection.transaction():
            updated = self.connection.execute(
                f"""
                INSERT INTO ai_documents (
                    tenant_id, document_id, external_id, source_uri, source_version,
                    content_hash, acl, parser_version, updated_at, deleted_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
                ON CONFLICT (tenant_id, document_id) DO UPDATE SET
                    source_uri = EXCLUDED.source_uri,
                    source_version = EXCLUDED.source_version,
                    content_hash = EXCLUDED.content_hash,
                    acl = EXCLUDED.acl,
                    parser_version = EXCLUDED.parser_version,
                    updated_at = EXCLUDED.updated_at,
                    deleted_at = NULL
                WHERE {version_guard}
                RETURNING source_version
                """,
                (
                    record.tenant_id,
                    result.document_id,
                    record.external_id,
                    record.source_uri,
                    record.version,
                    parsed.content_hash,
                    sorted(record.acl.readers),
                    parsed.parser_version,
                    record.updated_at,
                ),
            ).fetchone()
            if updated is None:
                return PostgresSyncReport(
                    "stale", record.tenant_id, record.external_id, record.version, 0
                )

            self.connection.execute(
                "DELETE FROM ai_chunks WHERE tenant_id = %s AND document_id = %s",
                (record.tenant_id, result.document_id),
            )
            for chunk in result.chunks:
                self.connection.execute(
                    """
                    INSERT INTO ai_chunks (
                        tenant_id, document_id, chunk_id, ordinal, content,
                        content_hash, source_uri, source_version, acl,
                        embedding_model, embedding
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                    """,
                    (
                        chunk.tenant_id,
                        chunk.document_id,
                        chunk.chunk_id,
                        chunk.ordinal,
                        chunk.text,
                        chunk.content_hash,
                        chunk.source_uri,
                        chunk.source_version,
                        sorted(chunk.acl.readers),
                        embedder.model,
                        _vector_literal(vectors[chunk.chunk_id]),
                    ),
                )
        return PostgresSyncReport(
            "indexed", record.tenant_id, record.external_id, record.version, len(result.chunks)
        )

    def delete_document(
        self, *, tenant_id: str, external_id: str, document_id: str, version: int
    ) -> PostgresSyncReport:
        with self.connection.transaction():
            updated = self.connection.execute(
                """
                INSERT INTO ai_documents (
                    tenant_id, document_id, external_id, source_version, acl, deleted_at
                ) VALUES (%s, %s, %s, %s, ARRAY[]::text[], now())
                ON CONFLICT (tenant_id, document_id) DO UPDATE SET
                    source_version = EXCLUDED.source_version,
                    acl = ARRAY[]::text[],
                    deleted_at = now(),
                    updated_at = now()
                WHERE ai_documents.source_version < EXCLUDED.source_version
                RETURNING source_version
                """,
                (tenant_id, document_id, external_id, version),
            ).fetchone()
            if updated is None:
                return PostgresSyncReport("stale", tenant_id, external_id, version, 0)
            removed = self.connection.execute(
                """
                DELETE FROM ai_chunks
                WHERE tenant_id = %s AND document_id = %s
                RETURNING chunk_id
                """,
                (tenant_id, document_id),
            ).fetchall()
        return PostgresSyncReport(
            "deleted", tenant_id, external_id, version, -len(removed)
        )

    def search(
        self,
        *,
        tenant_id: str,
        principals: frozenset[str],
        query: str,
        embedder: DeterministicEmbedder,
        limit: int = 5,
    ) -> tuple[tuple[str, str, float], ...]:
        query_vector = _vector_literal(embedder.embed((query,))[0])
        readers = sorted(principals)
        with self.connection.transaction():
            self.connection.execute(
                "SELECT set_config('app.tenant_id', %s, true)", (tenant_id,)
            )
            self.connection.execute(
                "SELECT set_config('app.principals', %s, true)",
                (",".join(readers),),
            )
            # Approximate scans stop early once they have enough candidates, which for a
            # filtered query can be too few authorized ones. Iterative scan keeps going.
            self.connection.execute("SET LOCAL hnsw.iterative_scan = strict_order")
            # Drop to the unprivileged role for the read. Both SET LOCALs and the role
            # revert when this transaction ends, so the next write runs as the owner.
            self.connection.execute(f"SET LOCAL ROLE {READER_ROLE}")
            rows = self.connection.execute(
                """
                SELECT chunk_id, content, embedding <=> %s::vector AS distance
                FROM ai_chunks
                WHERE tenant_id = %s AND acl && %s::text[]
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (query_vector, tenant_id, readers, query_vector, limit),
            ).fetchall()
        return tuple((chunk_id, content, float(distance)) for chunk_id, content, distance in rows)

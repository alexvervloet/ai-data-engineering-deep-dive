# AI Data Engineering: A Guided Deep Dive

RAG starts after the hardest data work has supposedly already happened. The document is
parsed, current, authorized, chunked, embedded, and present exactly once. Production
systems get to assume none of that.

This course builds the machinery that makes retrieval data trustworthy. It starts with an
untrusted connector payload and ends with a changing, multi-tenant corpus synchronized into
Postgres and pgvector without returning deleted or unauthorized documents. Every conceptual
lesson runs offline with deterministic data. The capstone runs the same lifecycle against a
real database.

The one big idea:

> **A retrieval index is a disposable, derived view of authoritative source data.**

That framing changes the design. Source versions beat arrival order. ACLs are data rather
than query decorations. Deletes become durable tombstones. Every chunk keeps its lineage.
Reconciliation compares source truth against index state. Backups preserve the source
snapshot and the CDC cursor, and you rebuild the vector index.

This is a bonus dive that slots after [RAG](https://github.com/alexvervloet/rag-deep-dive)
and before [Production](https://github.com/alexvervloet/ai-in-production-deep-dive). RAG
teaches retrieval quality. This repository teaches whether the corpus being retrieved is
the right corpus at all.

This README is the lab manual: what to run, in what order, and what each run proves.
[TEXTBOOK.md](TEXTBOOK.md) is the lecture that goes with it, covering where this machinery
came from, why each rule exists, and when the whole apparatus is more than a corpus needs.
Either order works. [EXERCISES.md](EXERCISES.md) turns each lesson into a prediction you
make before running it.

---

## What you will build

The path follows a document through its complete lifecycle:

```text
connector payload
      │ strict contract + source version
      ▼
authoritative source record ───────────────┐
      │ parse / OCR                        │ backup + CDC cursor
      ▼                                    │
normalized document                       │
      │ content hash + provenance          │
      ▼                                    │
ACL-bearing chunks                         │
      │ bounded embedding batches          │
      ▼                                    │
transactional tenant index                 │
      │                                    │
      ├── query: tenant + ACL before rank  │
      ├── CDC: update / delete / replay    │
      ├── reconcile against source truth   │
      └── rebuild after loss ◀──────────────┘
```

Coverage includes:

- connectors, snapshot watermarks, opaque cursor semantics, and CDC;
- strict runtime data contracts and schema evolution boundaries;
- text/HTML parsing plus an explicit PDF/image OCR adapter;
- content hashes, compute deduplication, stable IDs, provenance, and lineage;
- tenant isolation and ACL propagation into every derived chunk;
- incremental indexing, checkpoint replay, backfills, and embedding batches;
- deletion tombstones, stale-index reconciliation, and data-quality gates;
- checksummed backups, recovery points, and index rebuilds;
- a transactional Postgres/pgvector capstone with RLS as defense in depth.

---

## Setup

You need Python 3.11 or newer. The ten lessons and the default capstone path need no API
key, no network service, and no third-party runtime dependency.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python check_setup.py
```

Run the full offline verification at any time:

```bash
python -m unittest discover -v
```

The deterministic hash embedder preserves the production control flow, meaning batching,
cache keys, dimensions, atomic writes, and filtered search, and it is deliberately not a
semantic model. It keeps the data-engineering lesson local and repeatable.

---

## 1. Enforce the connector contract

```bash
python examples/01_data_contracts.py
```

A type hint, or a schema you showed to another system, is not enforcement. The boundary has
to reject unknown fields, unsupported MIME types, naive timestamps, oversized content,
invalid tenant identifiers, and empty ACLs before any state changes.

The example accepts a valid v2 record, rejects a model-supplied tenant field, and
shows the deny-by-default ACL. See [ai_data/contracts.py](ai_data/contracts.py).

The rule to keep: normalize identifiers at the boundary, and derive authorization from
trusted source or session context. Never accept a tenant selected by model output.

## 2. Join snapshots to CDC without a gap

```bash
python examples/02_connectors_and_cursors.py
```

A full crawl and an incremental feed are not two separate conveniences. They form one
consistency protocol.

1. capture a source high-watermark;
2. read a snapshot at that same logical instant;
3. consume changes strictly after the watermark;
4. persist the new cursor only after index writes commit.

Start CDC too early and you duplicate work. Start too late and documents disappear forever.
The memory connector makes both the snapshot and the page boundaries visible. Treat real
provider cursors as opaque tokens, even though the example uses readable integers.

## 3. Version parsing and OCR

```bash
python examples/03_parsing_and_ocr.py
```

Parsing changes data. An HTML cleanup release, a PDF library upgrade, an OCR model swap, or
a Unicode normalization fix can change every downstream chunk and embedding. So parsed text
carries a content hash and a `parser_version`.

Text, Markdown, and HTML work locally. PDFs and images fail closed until you supply an OCR
adapter. That boundary is deliberate. A pipeline must never index empty text, with nothing
said about it, because an optional parser was missing.

The HTML path also drops `script`, `style`, `template`, and `noscript` bodies. That
is partly retrieval hygiene, since minified CSS makes poor context. It is also the
ingest end of prompt injection: script text is arbitrary text on a page you did not
write, and whatever the parser keeps eventually reaches a model's context window.

## 4. Deduplicate compute, never identity

```bash
python examples/04_dedup_and_provenance.py
```

Two tenants can store the same bytes. Reusing parsing or embedding work by content hash is
safe. Merging their document IDs, ACLs, source URIs, or lineage is not.

The example shows equal blob IDs, distinct tenant-scoped document IDs, one reused embedding,
and two lineage edges. Content-addressed work is an optimization. The authorization boundary
stays document-addressed.

## 5. Propagate ACLs into every derivative

```bash
python examples/05_acl_propagation.py
```

The source ACL gets copied onto each chunk and updated even when the content has not
changed. The example revokes Alex's access without paying to embed the unchanged text
again. It also proves that an Acme principal cannot retrieve the identically named Beta
document.

The safe query order runs like this.

1. derive tenant and principals from trusted application context;
2. filter rows by tenant and ACL;
3. rank only the authorized candidate set;
4. return provenance with each result.

Filtering model output after retrieval is too late. Protected content has already entered
the application, and possibly the model context.

## 6. Apply CDC incrementally and replay safely

```bash
python examples/06_incremental_cdc.py
```

Each change carries a monotonically increasing source version. Updates replace a document
atomically. Deletes remove chunks and keep a tombstone version. If a worker commits the
index write and crashes before its checkpoint, replayed events come back as stale instead
of duplicating or resurrecting data.

Exactly-once delivery is rarely available end to end. Idempotent, version-aware effects plus
at-least-once delivery is the practical design.

## 7. Bound batches and run controlled backfills

```bash
python examples/07_batches_and_backfills.py
```

Embedding APIs constrain both item count and tokens. A safe batch planner enforces both,
rejects an individually oversized chunk, and checkpoints between batches. Production should
use the selected model's tokenizer. The offline estimate here is deliberately
conservative.

A backfill re-runs current source state after a parser, chunker, or embedding-model
migration. Equal source versions are permitted only in this explicit mode, and only
for documents that are not deleted: a tombstone is lifted by a strictly newer source
event or not at all. Otherwise a routine migration, run against a snapshot captured
around a delete, republishes content the source removed. The example migrates one old
chunk to six new chunks in three bounded calls, then replays the job with zero new
embedding calls.

## 8. Tombstone deletes and reconcile drift

```bash
python examples/08_deletes_and_reconciliation.py
```

Removing a vector is not enough. Without a versioned tombstone, an old retry can recreate
the deleted content. The example deliberately misses a delete event. Source-to-index
reconciliation finds the orphan, the tombstone removes it, and a late v1 upsert stays
stale.

Reconciliation also detects missing documents, stale versions, ACL drift, missing chunks,
and dangling chunks. Repair should be observable and bounded, never a blind "delete
everything not seen" run against an incomplete source snapshot.

## 9. Gate on lineage and data quality

```bash
python examples/09_lineage_and_quality.py
```

Retrieval evals cannot explain a stale or unauthorized corpus. The pipeline needs earlier
gates.

- source coverage and reconciliation drift;
- empty chunks and inconsistent embedding dimensions;
- ACL parity between documents and chunks;
- lineage coverage for every derivative;
- unusual duplicate ratios;
- active documents with no chunks.

The example removes one lineage edge, and the release gate fails while every other metric
stays green. Pinpointing it that precisely is the point.

## 10. Recover source state, then rebuild derivatives

```bash
python examples/10_disaster_recovery.py
```

The backup holds source records, ACLs, versions, metadata, bytes, and the CDC cursor in a
checksummed envelope. Recovery verifies the checksum, restores that snapshot, rebuilds the
index, and replays events after the cursor.

Define and test both of these.

- **RPO**: how much source/CDC history can be lost;
- **RTO**: how long parsing, chunking, embedding, index creation, and reconciliation
  take at full corpus size.

Back up only a vector table and you lose the evidence you would need to explain it, or to
rebuild it safely.

---

## Capstone: synchronize a multi-tenant corpus

The capstone reads [corpus/manifest.json](corpus/manifest.json), validates its file
paths and managed-tenant scope, indexes the documents, runs quality checks, and
queries with trusted tenant/principal filters.

### Offline reference path

```bash
python hands_on/sync_corpus.py

# Try an unauthorized or cross-tenant identity:
python hands_on/sync_corpus.py \
  --tenant beta \
  --principal user:alex \
  --query "What is the launch codename?"
```

The default verified run indexes three documents and prints:

```text
quality gate: PASS
unauthorized probe hits: 0
```

Only Acme's engineering handbook is returned for the default Acme principals.

### Real Postgres/pgvector path

The included service pins pgvector 0.8.6 on Postgres 18. Its local credentials belong to
this disposable development container and nowhere else.

```bash
docker compose up -d
pip install -r requirements-postgres.txt

python hands_on/sync_corpus.py \
  --database-url postgresql://ai_data:ai_data_local_only@localhost:54329/ai_data
```

Optional live integration test:

```bash
AI_DATA_TEST_DATABASE_URL=postgresql://ai_data:ai_data_local_only@localhost:54329/ai_data \
  python -m unittest tests.test_postgres.PostgresIntegrationTests -v
```

Stop the local service when finished:

```bash
docker compose down
```

The live path was verified against the pinned container with these outcomes:

- first sync: three `indexed` documents;
- repeat sync: three `stale` (idempotent) versions;
- injected orphan: detected and tombstoned on the next sync;
- unauthorized probe: zero hits before and after reconciliation;
- authorized query: only the Acme engineering chunk.

To exercise change management:

1. edit a corpus file and increment its manifest `version`;
2. change `readers` and increment the version to test revocation;
3. remove a document entry while keeping its tenant in `managed_tenants` to create
   a tombstone on the next sync;
4. try reintroducing an old/equal version and observe that it stays stale.

### Why the database schema looks this way

[ai_data/postgres.py](ai_data/postgres.py) deliberately uses relational and vector features
together.

- document replacement and chunk writes share one transaction;
- tenant, external ID, source version, hash, ACL, parser version, and deletion state
  live beside the vectors;
- B-tree tenant and GIN ACL indexes support filtering;
- HNSW uses cosine distance and iterative scans for filtered ANN queries;
- the application query still includes tenant and ACL predicates explicitly;
- searches run as an unprivileged reader role, so row-level security repeats the
  check as defense in depth and the read path cannot reach the document table;
- deletes cascade through chunks but retain the document tombstone.

That reader role is not ceremony, and the reason behind it is the most useful thing in this
section. Postgres exempts a table's owner from that table's row-level security policies
unless the table is declared `FORCE ROW LEVEL SECURITY`. An application that connects as the
role which ran its migrations, which is the common case, gets a policy that is present,
correct, and enforcing nothing. This repository shipped exactly that for a while. A probe
with the wrong tenant and no query predicates read every chunk in every tenant. Dropping to
a role that owns nothing is what turns the second layer on. See
`test_the_table_owner_is_exempt_from_the_policy`, which asserts the bypass itself so the
exemption stays visible instead of becoming folklore.

The general form is worth keeping. Test that a control denies something, not that it
exists. An inert security layer is worse than an absent one, because nobody audits the
layer that is already there.

Approximate indexes trade recall for speed. pgvector applies filters during an approximate
scan and can otherwise return too few results, and iterative scans search further until
enough filtered candidates turn up. Shared approximate indexes can also create cross-tenant
recall interference. At larger scale, measure filtered recall and consider list
partitioning or separate tables for strong tenant isolation. See the official
[pgvector filtering and multitenancy guidance](https://github.com/pgvector/pgvector#filtering).

The Postgres 18 image also changed its durable-data layout. The compose file mounts
`/var/lib/postgresql` rather than the pre-18 `/var/lib/postgresql/data`, so major-version
directories and `pg_upgrade --link` stay inside one mount.

---

## Production boundaries

The repository keeps the important semantics real and several integrations small. Replace
these joins without weakening their contracts.

| Teaching implementation | Production replacement |
|---|---|
| `MemoryConnector` | SaaS/object-store connectors with snapshot tokens, rate limits, and durable cursors |
| UTF-8/HTML parser + injected OCR callback | sandboxed parsers, malware checks, OCR service, page coordinates, parser-version registry |
| deterministic hash embedding | provider/local embeddings with tokenizer-aware limits, retries, budgets, and model-version cache keys |
| in-process embedding cache | durable content-addressed cache with retention and invalidation policy |
| one transaction per document | bulk staging/COPY, bounded workers, dead-letter queue, retry taxonomy, and checkpoint store |
| local backup string | encrypted object storage, retention policy, restore drills, and immutable audit evidence |
| one HNSW index | measured exact/ANN recall, tenant partitioning, vacuum/reindex plans, capacity tests, and replicas |

Do not add concurrency until you have tested the idempotency, versioning, and transaction
semantics. Parallelizing an unsafe lifecycle only makes corruption arrive faster.

---

## File map

```text
ai_data/
  models.py       immutable source, change, chunk, index, and lineage records
  contracts.py    strict runtime connector validation
  connectors.py   consistent snapshot + CDC reference connector
  parsing.py      text/HTML parsing and explicit OCR seam
  identity.py     normalization, hashes, and tenant-safe stable IDs
  chunking.py     deterministic chunks with ACL and lineage propagation
  embedding.py    bounded batch planner and offline embedder
  catalog.py      atomic in-memory lifecycle and authorized search
  pipeline.py     bootstrap, checkpointed CDC, replay, and backfill
  reconcile.py    missing, stale, orphan, ACL, and chunk drift detection
  quality.py      release-oriented data-quality gates
  recovery.py     checksummed source backup and restore
  manifest.py     strict filesystem corpus connector
  postgres.py     transactional Postgres/pgvector backend
examples/         ten offline, inspectable lessons
hands_on/
  sync_corpus.py  offline + live multi-tenant synchronization capstone
corpus/           two-tenant sample corpus and ACL manifest
tests/            lifecycle, isolation, recovery, and optional integration tests
compose.yaml      pinned local pgvector 0.8.6 / Postgres 18 service
```

Then use [EXERCISES.md](EXERCISES.md) to predict each failure before you run it. The tests
are course material too. Each one names an invariant the production pipeline has to
keep.

---

## Further reading

- [pgvector: indexing, filtering, multitenancy, and maintenance](https://github.com/pgvector/pgvector)
- [Psycopg 3 basic usage and transaction contexts](https://www.psycopg.org/psycopg3/docs/basic/usage.html)
- [PostgreSQL row security policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html),
  and in particular the owner exemption and `FORCE ROW LEVEL SECURITY`
- [PostgreSQL logical decoding concepts](https://www.postgresql.org/docs/current/logicaldecoding-explanation.html)
- [OpenLineage specification](https://openlineage.io/docs/spec/)

---

## Where this sits in the series

The lecture chapter is [Chapter 19](TEXTBOOK.md) of the
[AI Engineering Textbook](https://github.com/alexvervloet/ai-engineering-deep-dive).
The dives it depends on most:

- [RAG](https://github.com/alexvervloet/rag-deep-dive): the retrieval pipeline whose corpus
  this one keeps current and correct. Read it first.
- [Prompt Injection](https://github.com/alexvervloet/prompt-injection-deep-dive):
  what happens downstream when untrusted text reaches a model. Parsing is the first
  place to filter it.
- [Evals](https://github.com/alexvervloet/evals-deep-dive) and
  [Observability](https://github.com/alexvervloet/observability-deep-dive): both
  measure answers. A stale corpus scores perfectly against a stale eval set, which is
  why the quality gates here run earlier.
- [Production](https://github.com/alexvervloet/ai-in-production-deep-dive): where
  this pipeline goes once it has to stay up.

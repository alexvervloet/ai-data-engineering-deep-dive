# Exercises: predict, run, explain

Do each exercise in this order:

1. write down what you think will happen;
2. run the unchanged example;
3. make the requested change;
4. explain the result in terms of a production invariant.

The goal is not to memorize APIs. It is to learn which data failure each control
prevents.

## 1. Data contracts

Run:

```bash
python examples/01_data_contracts.py
```

- Predict whether `tenant_from_model` will be ignored or rejected.
- Change `updated_at` to omit its UTC offset. Why is local time ambiguous during a
  backfill?
- Set `readers` to `[]`. Compare deny-by-default with treating an empty list as
  public.
- Add a v3-only field without changing `contract_version`. Design the migration
  that would let old and new connectors coexist.

## 2. Snapshot and CDC handoff

Run:

```bash
python examples/02_connectors_and_cursors.py
```

- Predict which events appear after cursor 1.
- Start changes at cursor 0. Which record is duplicated?
- Start at cursor 2. Which record can be lost?
- Request pages of size 1, crash after applying the first event, and do not persist
  the new cursor. Explain why replay safety belongs in the sink as well as the
  connector.

## 3. Parsing and OCR

Run:

```bash
python examples/03_parsing_and_ocr.py
```

- Predict the parsed HTML text and hash stability.
- Make the OCR adapter return whitespace. Where should that failure be caught?
- Change `PARSER_VERSION` without changing output. Should the corpus be backfilled?
- Change normalization so CRLF and LF hash differently. What unnecessary work does
  that create?

## 4. Deduplication and provenance

Run:

```bash
python examples/04_dedup_and_provenance.py
```

- Predict which IDs are equal and which differ.
- Give the Beta document a different ACL while keeping identical bytes. Verify that
  the embedding is reused but its derived chunk ACL is not.
- Remove `tenant_id` from `document_id()`. Write the failing isolation test first.
- Decide which caches may be global by content hash and which must remain tenant or
  document scoped.

## 5. ACL propagation

Run:

```bash
python examples/05_acl_propagation.py
```

- Predict access for Acme/Alex, Acme/Bob, and Beta/Alex before the update.
- Add `group:platform` to the caller's trusted principals after revocation.
- Move the ACL check until after top-k ranking. Construct a case where all top-k
  candidates are unauthorized and the user receives too few results.
- Explain why a model-provided `tenant_id` must never reach the search call.

## 6. Incremental CDC

Run:

```bash
python examples/06_incremental_cdc.py
```

- Predict the page boundaries with `limit=2`.
- Deliver the update events in reverse order. Which source version remains visible?
- Remove the tombstone state after deleting chunks, then replay an old upsert.
- Identify the exact operation after which a durable checkpoint may advance.

## 7. Batches and backfills

Run:

```bash
python examples/07_batches_and_backfills.py
```

- Predict the number of chunks, batches, and embedding calls.
- Set `max_items=1`, then increase `max_tokens`. Which limit controls each batch?
- Make one chunk exceed `max_tokens`. Decide whether to truncate, rechunk, dead-letter,
  or fail the entire document.
- Change the embedding model name and rerun the backfill. Why must the cache miss?

## 8. Deletes and reconciliation

Run:

```bash
python examples/08_deletes_and_reconciliation.py
```

- Predict the finding before the missed delete is applied.
- Remove a chunk directly while keeping its document state. Which finding appears?
- Try to resurrect the tombstone with an equal version as well as an older version.
- Design a repair budget that prevents one incomplete source snapshot from deleting
  a whole tenant.

## 9. Lineage and quality

Run:

```bash
python examples/09_lineage_and_quality.py
```

- Predict which single check fails after the lineage edge is removed.
- Corrupt a chunk ACL instead. Which check localizes the problem?
- Duplicate half the chunks. The duplicate check is non-critical here; decide when
  it should block a production release.
- Add a metric for parser failures by MIME type and owner.

## 10. Disaster recovery

Run:

```bash
python examples/10_disaster_recovery.py
```

- Predict the restored source version before and after CDC replay.
- Modify one byte inside the serialized backup without updating its checksum.
- Measure rebuild time after increasing the corpus by 100x; use the result to state
  an RTO rather than guessing one.
- Decide how long the source snapshot, CDC log, tombstones, and derived vectors each
  need retention.

## Capstone: prove the lifecycle

Start offline:

```bash
python hands_on/sync_corpus.py
```

Then run Postgres/pgvector:

```bash
docker compose up -d
pip install -r requirements-postgres.txt
python hands_on/sync_corpus.py \
  --database-url postgresql://ai_data:ai_data_local_only@localhost:54329/ai_data
```

Complete these changes one at a time:

1. Edit `corpus/acme/engineering.md` without bumping its version. Confirm that the
   live sink refuses the equal version. Explain why source systems must version
   meaningful changes.
2. Increment the version and resync. Confirm that chunks replace atomically.
3. Remove Alex from the engineering ACL, increment the version, and query again.
   Confirm revocation without deleting the document.
4. Remove `benefits-guide` from the manifest while leaving `acme` in
   `managed_tenants`. Confirm one stale document is tombstoned.
5. Re-add the guide with its old version. Confirm that the tombstone wins.
6. Query Beta as Alex and Acme as Bob. Capture the zero-result evidence.
7. Run the integration test with `AI_DATA_TEST_DATABASE_URL` set.
8. Inspect `EXPLAIN (ANALYZE, BUFFERS)` for a tenant-filtered query at larger scale.
   Compare exact recall with HNSW recall before tuning `ef_search`.

When finished, restore the sample manifest and stop the service:

```bash
docker compose down
```

Your final artifact should state the data contract, checkpoint rule, idempotency
key, version semantics, deletion semantics, ACL source, quality gates, RPO, RTO, and
measured filtered-recall target. If any one is only "whatever the code currently
does," the pipeline is not yet operable at senior level.

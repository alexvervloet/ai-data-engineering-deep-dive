# Lessons

## Default `unittest` discovery needs an importable test directory

- **Expected:** `python -m unittest discover` would recurse into `tests/` and run
  `test_pipeline.py`.
- **Actual:** it exited successfully with `Ran 0 tests`, which is a dangerous false
  green.
- **Next time:** create `tests/__init__.py` with the first test module and assert
  the expected test count in CI output, not only the command's exit status.

## Idempotent replacement must not weaken tombstones

- **Expected:** accepting an equal source version in the Postgres upsert would make
  repeat synchronization harmless.
- **Actual:** the same comparison also allowed an equal-version late upsert to clear
  `deleted_at` and resurrect a tombstoned document.
- **Next time:** require strictly newer versions for ordinary source events. Permit
  equal-version replacement only behind an explicit, controlled backfill mode.

## Postgres 18 changed the container data mount

- **Expected:** the long-standing `/var/lib/postgresql/data` volume mount would
  initialize the pinned Postgres 18 pgvector image.
- **Actual:** the container exited immediately. Postgres 18 images store data in a
  major-version-specific directory and require the volume at `/var/lib/postgresql`
  so `pg_upgrade --link` can work without crossing a mount boundary.
- **Next time:** validate compose files against the pinned database major version;
  for Postgres 18+, mount the parent `/var/lib/postgresql` directory.

## Integration fixtures must respect their own tombstones

- **Expected:** the pgvector lifecycle test would be repeatable against one local
  development database.
- **Actual:** its first run correctly left a v2 tombstone; the next run tried to
  insert v1 and was correctly rejected as stale, making the test order-dependent.
- **Next time:** give integration fixtures a dedicated tenant and remove only that
  tenant's rows before and after the test. Run the test twice during verification.

## Verify API authentication before building a new remote repository

- **Expected:** the existing GitHub setup that pushes curriculum repositories over
  SSH would also let `gh repo create` publish this new module.
- **Actual:** SSH access was available, but the GitHub CLI's API token was invalid;
  Git can push an existing repository but cannot create the missing remote.
- **Next time:** run `gh auth status` before starting a new-repository task and
  create the empty remote early, while keeping the first push gated on green tests.

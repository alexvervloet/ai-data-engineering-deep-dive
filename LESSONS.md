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

## The exception carved out for backfills carried the same bug

- **Expected:** the fix above had closed tombstone resurrection, because ordinary
  events now require a strictly newer version.
- **Actual:** the backfill mode created by that fix kept the old comparison, so a
  transform migration rerun at the deleted version cleared `deleted_at` again. The
  same defect, one layer down, hiding inside the legitimate exception to the rule.
- **Next time:** when a rule gets an exception, re-test the original failure against
  the exception. Write the invariant as one predicate used by every path, rather than
  as a comparison operator chosen per call site.

## A row-level security policy can be correct and enforce nothing

- **Expected:** enabling row-level security on the chunk table and writing a tenant
  and ACL policy gave the capstone a real second layer behind its query predicates.
- **Actual:** it enforced nothing. Postgres exempts a table's owner from that table's
  policies unless the table is declared `FORCE ROW LEVEL SECURITY`, and the capstone
  connects as the owner that ran the migrations. A probe with the wrong tenant and no
  query predicates read every chunk in every tenant.
- **Next time:** test that a control denies something, never only that it exists. For
  row-level security specifically, run reads as a role that owns nothing, and keep a
  test asserting the owner bypass so the exemption stays visible.

## Claiming a Python floor means compiling against it

- **Expected:** code written and tested on 3.13 would satisfy the declared
  `requires-python = ">=3.11"`, since nothing used a recent library feature.
- **Actual:** a nested f-string containing an escape is a syntax error before 3.12,
  so the package did not import at all on the oldest supported version. CI caught it
  on the 3.11 leg and the failure sat on `main`, because the local run was green.
- **Next time:** treat a red CI leg as blocking even when it is the older-version
  matrix entry, and compile against the declared floor locally
  (`docker run --rm -v "$PWD":/w -w /w python:3.11-slim python -m compileall -q .`)
  before claiming support for it.

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

## Keyring-backed GitHub authentication may be invisible in a sandbox

- **Expected:** after signing in with `gh auth login`, the same authentication
  status would be visible to every command environment.
- **Actual:** sandboxed `gh auth status` continued to report an invalid token while
  the host environment correctly found the new keyring credential.
- **Next time:** when GitHub CLI authentication uses the system keyring, verify and
  run API operations with the approved host-level command environment.

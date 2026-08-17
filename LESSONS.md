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

# Lessons

## Default `unittest` discovery needs an importable test directory

- **Expected:** `python -m unittest discover` would recurse into `tests/` and run
  `test_pipeline.py`.
- **Actual:** it exited successfully with `Ran 0 tests`, which is a dangerous false
  green.
- **Next time:** create `tests/__init__.py` with the first test module and assert
  the expected test count in CI output, not only the command's exit status.

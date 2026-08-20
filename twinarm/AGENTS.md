# AGENTS.md — twinarm

The installable TwinArm library. Repository-wide instructions are in [`../AGENTS.md`](../AGENTS.md).

Read @README.md for what this package is, its current state, and its layout.

## Rules for this directory

- Keep the package importable with no hardware attached: no serial connections or device access at
  import time.
- The version lives in `src/twinarm/__init__.py` and nowhere else — there is no second copy to keep in
  sync.
- This is the only directory that builds (uv_build backend, src layout). Code that only makes sense
  with arms attached belongs in [`../descovery/`](../descovery/AGENTS.md) instead.
- After changing any Python code here you must run `mise run check` and make it pass before you
  finish; report its real output. If it reports formatting violations, run `mise run format` and
  re-run it. The tasks are listed in [README.md](README.md) — use them rather than bare
  `uv run ruff` / `uv run ty` in this directory.
- `mise run check` does not come back clean today: `ty` reports one `unresolved-import` for `pytest`
  in `tests/test_package.py`, because pytest is not a declared dependency. That single diagnostic is
  the baseline — do not add pytest to silence it — and your change must introduce no others.
  `mise run type src` checks the package on its own. Check the testing status in
  [`../AGENTS.md`](../AGENTS.md) before running or claiming tests.

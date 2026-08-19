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
- Development commands are in [`../README.md`](../README.md); check the testing status in
  [`../AGENTS.md`](../AGENTS.md) before running or claiming tests.

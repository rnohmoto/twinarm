# AGENTS.md — twinarm

The installable TwinArm library. Repository-wide instructions are in [`../AGENTS.md`](../AGENTS.md).

Read @README.md for what this package is, its current state, and its layout.

## Rules for this directory

- Keep the package importable with no hardware attached: no serial connections or device access at
  import time. The `api` package extends this to runtime — `create_app()` and every request handler
  must work with no arms attached.
- `src/twinarm/api/` is vertical-slice: one folder per feature under `api/features/`, each owning its
  `router.py`, `schemas.py`, and any slice-local logic. Slices are wired together only in
  `api/app.py`; `twinarm.api:create_app` is the factory target `mise run serve` hands to uvicorn.
  Intra-slice imports are relative, everything else absolute.
- The `/stream` and `/ctl` wire contract is owned by the zod schemas in
  [`../twinarm-web-ui/src/shared/api/`](../twinarm-web-ui/src/shared/api/README.md), derived from
  [`../descovery/koch_web_panel.py`](../descovery/koch_web_panel.py). Reconcile there first before
  implementing the `telemetry` or `control` placeholder slices.
- The version lives in `pyproject.toml` and nowhere else — `src/twinarm/__init__.py` re-exports it
  from the installed package metadata, so there is no second copy to keep in sync.
- This is the only directory that builds (uv_build backend, src layout). Code that only makes sense
  with arms attached belongs in [`../descovery/`](../descovery/AGENTS.md) instead.
- Use the mise tasks listed in [README.md](README.md) rather than bare `uv run ruff` / `uv run ty`
  in this directory.

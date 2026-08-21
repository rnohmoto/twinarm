# twinarm

Python library for controlling twin Koch v1.1 arms, built on
[lerobot](https://github.com/huggingface/lerobot).

Part of the TwinArm monorepo; see [`../README.md`](../README.md) for requirements and setup.

## Status

An early skeleton. `src/twinarm/__init__.py` currently holds only the package docstring and
`__version__`, and the public API is not defined yet. The working hardware code still lives in
[`../descovery/`](../descovery/README.md).

## Development

Checks run as [mise](https://mise.jdx.dev/) tasks, defined in `mise.toml`:

```bash
mise run format   # ruff check --fix-only, then ruff format (-c/--check to verify only)
mise run type     # ty check (-f/--fix to apply fixes)
mise run test     # pytest
mise run check    # format --check + type + test; writes nothing
```

Run them from this directory. From the repository root, use the monorepo path instead:
`mise run //twinarm:check`.

## Structure

- `src/twinarm/` — package source (src layout)
- `tests/` — tests
- `pyproject.toml` — dependencies (`lerobot[dynamixel]`), dev tools (ruff, ty, pytest), the pytest
  configuration, and the uv_build backend
- `mise.toml` — the `format` / `type` / `test` / `check` tasks

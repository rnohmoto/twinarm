# twinarm

Python library for controlling twin Koch v1.1 arms, built on
[lerobot](https://github.com/huggingface/lerobot).

Part of the TwinArm monorepo; see [`../README.md`](../README.md) for requirements, setup, and the
development commands.

## Status

An early skeleton. `src/twinarm/__init__.py` currently holds only the package docstring and
`__version__`, and the public API is not defined yet. The working hardware code still lives in
[`../descovery/`](../descovery/README.md).

## Structure

- `src/twinarm/` — package source (src layout)
- `tests/` — tests
- `pyproject.toml` — dependencies (`lerobot[dynamixel]`), dev tools (ruff, ty), and the uv_build backend

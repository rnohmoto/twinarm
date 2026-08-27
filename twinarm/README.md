# twinarm

Python library for controlling twin Koch v1.1 arms, built on
[lerobot](https://github.com/huggingface/lerobot).

Part of the TwinArm monorepo; see [`../README.md`](../README.md) for requirements and setup.

## Status

An early skeleton. `src/twinarm/__init__.py` holds the package docstring and `__version__`, and the
library's public API is not defined yet. `src/twinarm/domain/` and `src/twinarm/infrastructure/` are
importable but empty — each holds only the docstring and README stating its layer's rules.
`src/twinarm/api/` holds a FastAPI app skeleton whose only live endpoint is `GET /health`; the
`telemetry` and `control` slices are README-only placeholders waiting on the provisional contract in
[`../twinarm-web-ui/src/shared/api/`](../twinarm-web-ui/src/shared/api/README.md). Nothing here
talks to an arm — the working hardware code still lives in [`../descovery/`](../descovery/README.md).

## Development

Checks run as [mise](https://mise.jdx.dev/) tasks, defined in `mise.toml`:

```bash
mise run format   # ruff check --fix-only, then ruff format (-c/--check to verify only)
mise run type     # ty check (-f/--fix to apply fixes)
mise run test     # pytest (-c/--coverage for a line-coverage report)
mise run check    # format --check + type + test; writes nothing
mise run serve    # uvicorn dev server on 127.0.0.1:8780 (-p/--port to override)
```

Run them from this directory. From the repository root, use the monorepo path instead:
`mise run //twinarm:check`. `serve` is not part of `check`: it runs until you stop it, and it
defaults to the same port as [`../descovery/koch_web_panel.py`](../descovery/README.md), so pass
`--port` when that panel is already running.

## Structure

- `src/twinarm/` — package source (src layout)
- `src/twinarm/domain/` — the teleoperation domain model and its ports (`typing.Protocol`),
  stdlib-only by rule; its [README](src/twinarm/domain/README.md) defines the layer
- `src/twinarm/infrastructure/` — adapters implementing the domain ports over real drivers
  (lerobot, serial); its [README](src/twinarm/infrastructure/README.md) defines the layer
- `src/twinarm/api/` — the FastAPI app: `app.py` assembles the vertical slices under
  `api/features/` and will bind adapters to domain ports; one folder per slice owning its router,
  schemas, and use cases. `health` is the worked example; `telemetry` and `control` are
  placeholders.
- tests sit in a `tests/` package inside the folder holding the code they cover:
  `src/twinarm/tests/` for the package itself, `src/twinarm/api/features/health/tests/` for the
  health slice. There is no separate top-level test tree.
- `pyproject.toml` — dependencies (`lerobot[dynamixel]`, `fastapi`, `uvicorn`), dev tools (ruff, ty,
  pytest, httpx), the pytest configuration, and the uv_build backend
- `mise.toml` — the `format` / `type` / `test` / `check` / `serve` tasks

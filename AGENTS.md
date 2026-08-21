# AGENTS.md

Operating instructions for AI coding agents working in this repository.

## Project overview

Read @README.md for the project overview, repository layout, requirements, and all setup and
development commands.

## Working in this repo

- Run `uv` commands inside the subproject you are changing (`twinarm/` or `descovery/`), and `npm`
  commands inside `twinarm-web-ui/`. The root project is configuration only — never run `uv sync` or
  `npm install` at the root.
- After changing code, run the affected subproject's checks before you finish: `mise run check` in
  `twinarm/` and `twinarm-web-ui/`, or the ruff and ty commands directly in `descovery/`, which has
  no tasks. From the root, `mise run check` runs both task-based subprojects at once.
- Do not add dependencies, entry points, or top-level directories without an explicit request.

## Hardware safety

The scripts in `descovery/` drive real robot arms.

- Never run anything that moves motors, releases torque, or writes motor configuration unless the user
  explicitly asked for it in the current session.
- Never guess a serial port or pick one by enumerating devices. The user supplies the port, and the
  leader and follower ports are not interchangeable.
- Never state that hardware behaves a certain way unless the user ran it and reported the result — you
  cannot observe the arms from here.

## Testing and CI

Both task-based subprojects run their tests as part of `mise run check`:

- `twinarm/` — pytest, declared in its dev dependencies. `tests/` holds one smoke test.
- `twinarm-web-ui/` — Vitest for units and components. Playwright covers end to end and is
  deliberately **not** part of `check`; run `mise run e2e` in that directory when it matters.

`descovery/` has no tests and no tasks: its scripts need arms attached.

CI is GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)), running the same mise
tasks a developer runs locally, so there is one definition of green. It covers `twinarm` and
`twinarm-web-ui`; `descovery/` is excluded because it needs real hardware.

Report what actually ran. Never claim tests passed on output you did not see.

## Rules auto-loading

Claude Code loads `.claude/rules/common/` in every session, and `.claude/rules/python/` when Python
files are involved. Do not import those files and do not restate their content here or in any other
AGENTS.md — they would then be loaded twice. Other agents should read
[`.claude/rules/common/`](.claude/rules/common/) and [`.claude/rules/python/`](.claude/rules/python/)
directly.

The same applies to this file. Imports in this repository are one per directory, pointing from that
directory's AGENTS.md to its README.md; everything else is a plain link. Keep it that way when editing.

## Subprojects

When working inside a subdirectory, read its AGENTS.md first:

- [`twinarm/AGENTS.md`](twinarm/AGENTS.md)
- [`descovery/AGENTS.md`](descovery/AGENTS.md)
- [`twinarm-web-ui/AGENTS.md`](twinarm-web-ui/AGENTS.md)

## Known quirks

- The root `pyproject.toml` has a `[tool.lint.isort]` table that has no effect; ruff would need
  `[tool.ruff.lint.isort]`. Known — leave it unless the user asks.
- All three `pyproject.toml` files still carry the placeholder `description = "Add your description
  here"`, and `descovery/` reuses the `twinarm` package name. Known — leave them unless the user asks.

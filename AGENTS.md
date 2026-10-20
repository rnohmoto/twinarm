# AGENTS.md

Operating instructions for AI coding agents working in this repository.

## Project overview

Read @README.md for the project overview, repository layout, requirements, and all setup and
development commands.

## Working in this repo

- Run `uv` commands inside the subproject you are changing (`twinarm/`, `discovery/koch/` or
  `discovery/dobot/`), and `npm`
  commands inside `twinarm-web-ui/`. The root project is configuration only — never run `uv sync` or
  `npm install` at the root.
- After changing code, run the affected subproject's checks before you finish and make them pass:
  `mise run check` in `twinarm/`, `twinarm-web-ui/` and `discovery/koch/`, or the ruff and ty
  commands directly in `discovery/dobot/`, which has no tasks. If it reports formatting violations,
  run `mise run format` and re-run it. From the root, `mise run check` runs the three task-based
  subprojects at once.
- Do not add dependencies, entry points, or top-level directories without an explicit request.

## Hardware safety

The scripts in `discovery/` drive real robot arms.

- Never run anything that moves motors, releases torque, or writes motor configuration unless the user
  explicitly asked for it in the current session.
- Never guess a serial port or pick one by enumerating devices. The user supplies the port, and the
  leader and follower ports are not interchangeable.
- Never state that hardware behaves a certain way unless the user ran it and reported the result — you
  cannot observe the arms from here.

## Testing and CI

The task-based subprojects run their tests as part of `mise run check`. Inside any of them,
`mise run test --coverage` reports line coverage. Playwright is deliberately **not** part of
`check`; run `mise run e2e` in `twinarm-web-ui/` when it matters. `discovery/koch/` runs its
offline tests in `check`; its simbus rehearsal is `mise run rehearse` (about three minutes, run it
alone, not part of `check`). `discovery/dobot/` has no tasks: its scripts need hardware.

CI is GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)), running the same mise
tasks a developer runs locally. `discovery/dobot/` is excluded because it needs real hardware.

Report what actually ran. Never claim tests passed on output you did not see.

## Rules auto-loading

Claude Code loads `.claude/rules/common/` in every session, and `.claude/rules/python/` when Python
files are involved. Do not import those files and do not restate their content here or in any other
AGENTS.md — they would then be loaded twice. Other agents should read
[`.claude/rules/common/`](.claude/rules/common/) and [`.claude/rules/python/`](.claude/rules/python/)
directly. Imports in this repository are one per directory, pointing from that directory's AGENTS.md
to its README.md; everything else is a plain link. Keep it that way when editing.

## Subprojects

When working inside a subdirectory, read its AGENTS.md first:

- [`twinarm/AGENTS.md`](twinarm/AGENTS.md)
- [`discovery/AGENTS.md`](discovery/AGENTS.md)
- [`dobot/AGENTS.md`](dobot/AGENTS.md)
- [`twinarm-web-ui/AGENTS.md`](twinarm-web-ui/AGENTS.md)

## Known quirks

- The root `pyproject.toml` has a `[tool.lint.isort]` table that has no effect; ruff would need
  `[tool.ruff.lint.isort]`. Known — leave it unless the user asks.
- The root, `twinarm/` and `discovery/` `pyproject.toml` files still carry the placeholder
  `description = "Add your description here"`, and `discovery/` (now configuration-only) reuses the
  `twinarm` package name. Known — leave them unless the user asks.

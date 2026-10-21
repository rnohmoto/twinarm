# TwinArm

Dual-arm robot teleoperation with Koch v1.1 leader/follower arms. The arms use Dynamixel servos
(XL330 on the leader, XL430 shoulders on the follower) and are driven through
[Hugging Face lerobot](https://github.com/huggingface/lerobot) (`lerobot[dynamixel]`), developed on
macOS over USB serial.

## Status

Early development. The `twinarm` library is still a skeleton, and every line that drives an arm lives
in the `discovery/` sandbox as a standalone script. `twinarm-web-ui/` is scaffolding plus one worked
example slice; it has no backend and runs against mocks.

## Repository layout

| Path | What it is |
| ---- | ---------- |
| [`twinarm/`](twinarm/README.md) | The installable Python library (src layout). |
| [`discovery/`](discovery/README.md) | Hardware experiment sandboxes. `discovery/koch/` is the Koch arms bench and the Learning Fest setup (start at its `docs/HANDOFF.md`); `discovery/dobot/` the Dobot bench. |
| [`discovery/dobot/`](discovery/dobot/README.md) | Dobot Magician pick-and-place sandbox: overhead camera → homography → pydobot, plus the first-time setup manual. |
| [`twinarm-web-ui/`](twinarm-web-ui/README.md) | The web teleoperation UI: React and Vite, mock-backed. |
| `docs/` | Documentation. `fest_start_prompts.md`: the Learning Fest bench sessions, one Mac per role (handshake A, VR leader B, VR follower C, desktop robot D), with the prompt to paste into Claude Code on each Mac. |
| `pyproject.toml` | Configuration-only root project. Holds the shared ruff configuration. |

`twinarm/`, `discovery/koch/` and `discovery/dobot/` are independent uv projects, each with its own
`pyproject.toml` and `uv.lock` — this is not a uv workspace, so sync and run commands are issued inside
a subproject rather than at the root (`discovery/` itself is configuration-only, like the root). `twinarm-web-ui/` is a separate npm project with its own `package-lock.json`, and
follows the same rule. The root project declares `package = false` and no dependencies; it exists to
hold shared tool configuration and the mise tasks that fan out across subprojects.

## Requirements

- macOS
- Python 3.13 (pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/)
- [mise](https://mise.jdx.dev/) — task runner, and the source of the pinned uv and Node versions
- Node 24 for `twinarm-web-ui/` — `mise install` provides it; nothing else needs Node
- Koch v1.1 leader and follower arms connected over USB. Serial ports appear as `/dev/tty.usbmodem*`.
  The web UI needs none of this: it runs against mocks.

## Setup

Each subproject is synced separately:

```bash
cd twinarm && uv sync           # the library
cd discovery/koch && uv sync    # the Koch arms bench
cd discovery/dobot && uv sync   # the Dobot bench
cd twinarm-web-ui && mise run install   # the web UI (npm ci; run `mise install` first for Node)
```

## Hardware scripts

Everything that talks to the Koch arms lives in `discovery/koch/` (and the Dobot in `discovery/dobot/`).
Most of those commands move motors, release torque, or rewrite motor configuration, so start from
[`discovery/README.md`](discovery/README.md) for the risk classes and the safe read-only bus scan, then
[`discovery/koch/README.md`](discovery/koch/README.md) for every console script with its purpose and risk.

## Development

`twinarm/`, `twinarm-web-ui/` and `discovery/koch/` run their checks as mise tasks under the same
names: `format`, `type`, `test`, `check` (plus `build`, `fsd` and `e2e` in the web UI, and `rehearse`
in `discovery/koch/`: three minutes against simulated servos, not part of `check`). See
[`twinarm/README.md`](twinarm/README.md), [`twinarm-web-ui/README.md`](twinarm-web-ui/README.md) and
[`discovery/koch/README.md`](discovery/koch/README.md) for the details.

From the repository root, aggregate tasks fan out to the three:

```bash
mise run check     # //twinarm:check, //twinarm-web-ui:check and //discovery/koch:check
mise run format    # the same, for format
```

`discovery/dobot/` is not part of those: it needs real hardware. It also has no tasks; run its tools
directly from that directory:

```bash
uv run ruff check .    # lint
uv run ruff format .   # format
uv run ty check        # type check
```

Lint and format settings are shared: the root `pyproject.toml` holds the `[tool.ruff]` configuration and
each subproject inherits it through `extend` (`../pyproject.toml`, or `../../pyproject.toml` from
`discovery/koch/`). Change shared rules in the root file.

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to `main` and every pull
request. It runs the same mise tasks as a developer does — `mise run check` at the root, plus the web
UI's Playwright suite as a separate job. `discovery/` is excluded because its scripts need arms
attached.

## Documentation for AI coding agents

Instructions for AI coding agents live in [`AGENTS.md`](AGENTS.md) for the repository as a whole, in one
`AGENTS.md` per subproject, and in [`.claude/rules/`](.claude/rules/).
[`CLAUDE.md`](CLAUDE.md) points Claude Code at `AGENTS.md`.

# TwinArm

Dual-arm robot teleoperation with Koch v1.1 leader/follower arms. The arms use Dynamixel servos
(XL330 on the leader, XL430 shoulders on the follower) and are driven through
[Hugging Face lerobot](https://github.com/huggingface/lerobot) (`lerobot[dynamixel]`), developed on
macOS over USB serial.

## Status

Early development. The `twinarm` library is still a skeleton, and every line that drives an arm lives
in the `descovery/` sandbox as a standalone script. `twinarm-web-ui/` is scaffolding plus one worked
example slice; it has no backend and runs against mocks.

## Repository layout

| Path | What it is |
| ---- | ---------- |
| [`twinarm/`](twinarm/README.md) | The installable Python library (src layout). A skeleton for now. |
| [`descovery/`](descovery/README.md) | Hardware experiment sandbox: standalone Koch-arm scripts. |
| [`twinarm-web-ui/`](twinarm-web-ui/README.md) | The web teleoperation UI: React and Vite, mock-backed. |
| `docs/` | Reserved for documentation. Empty. |
| `pyproject.toml` | Configuration-only root project. Holds the shared ruff configuration. |

`twinarm/` and `descovery/` are independent uv projects, each with its own `pyproject.toml` and
`uv.lock` — this is not a uv workspace, so sync and run commands are issued inside a subproject rather
than at the root. `twinarm-web-ui/` is a separate npm project with its own `package-lock.json`, and
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
cd descovery && uv sync         # the hardware sandbox
cd twinarm-web-ui && mise run install   # the web UI (npm ci; run `mise install` first for Node)
```

## Hardware scripts

Everything that talks to the arms lives in `descovery/`. Most of those scripts move motors, release
torque, or rewrite motor configuration, so start from [`descovery/README.md`](descovery/README.md): it
lists every script with its purpose and hardware risk, and shows the safe read-only bus scan to begin
with.

## Development

`twinarm/` and `twinarm-web-ui/` run their checks as mise tasks. Both use the same task names, so
one vocabulary covers both: `format`, `type`, `test`, `check` (plus `build`, `fsd` and `e2e` in
the web UI).
See [`twinarm/README.md`](twinarm/README.md) and
[`twinarm-web-ui/README.md`](twinarm-web-ui/README.md) for the details.

From the repository root, aggregate tasks fan out to both:

```bash
mise run check     # //twinarm:check and //twinarm-web-ui:check
mise run format    # the same, for format
```

`descovery/` is not part of those: it needs real hardware. It also has no tasks; run its tools
directly from that directory:

```bash
uv run ruff check .    # lint
uv run ruff format .   # format
uv run ty check        # type check
```

Lint and format settings are shared: the root `pyproject.toml` holds the `[tool.ruff]` configuration and
each subproject inherits it through `extend = "../pyproject.toml"`. Change shared rules in the root file.

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to `main` and every pull
request. It runs the same mise tasks as a developer does — `mise run check` at the root, plus the web
UI's Playwright suite as a separate job — so "green" means the same thing locally and in CI.
`descovery/` is excluded because its scripts need arms attached.

## Documentation for AI coding agents

Instructions for AI coding agents live in [`AGENTS.md`](AGENTS.md) for the repository as a whole, in one
`AGENTS.md` per subproject, and in [`.claude/rules/`](.claude/rules/).
[`CLAUDE.md`](CLAUDE.md) points Claude Code at `AGENTS.md`.

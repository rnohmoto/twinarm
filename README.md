# TwinArm

Dual-arm robot teleoperation with Koch v1.1 leader/follower arms. The arms use Dynamixel servos
(XL330 on the leader, XL430 shoulders on the follower) and are driven through
[Hugging Face lerobot](https://github.com/huggingface/lerobot) (`lerobot[dynamixel]`), developed on
macOS over USB serial.

## Status

Early development. The `twinarm` library is still a skeleton; all working code currently lives in the
`descovery/` sandbox as standalone hardware scripts.

## Repository layout

| Path | What it is |
| ---- | ---------- |
| [`twinarm/`](twinarm/README.md) | The installable Python library (src layout). A skeleton for now. |
| [`descovery/`](descovery/README.md) | Hardware experiment sandbox: standalone Koch-arm scripts. |
| [`twinarm-web-ui/`](twinarm-web-ui/README.md) | Placeholder for a future web UI. No code yet. |
| `docs/` | Reserved for documentation. Empty. |
| `pyproject.toml` | Configuration-only root project. Holds the shared ruff configuration. |

`twinarm/` and `descovery/` are independent uv projects, each with its own `pyproject.toml` and
`uv.lock` — this is not a uv workspace, so sync and run commands are issued inside a subproject rather
than at the root. The root project declares `package = false` and no dependencies; it exists to hold
shared tool configuration.

## Requirements

- macOS
- Python 3.13 (pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/)
- Koch v1.1 leader and follower arms connected over USB. Serial ports appear as `/dev/tty.usbmodem*`.

## Setup

Each subproject is synced separately:

```bash
cd twinarm && uv sync      # the library
cd descovery && uv sync    # the hardware sandbox
```

## Hardware scripts

Everything that talks to the arms lives in `descovery/`. Most of those scripts move motors, release
torque, or rewrite motor configuration, so start from [`descovery/README.md`](descovery/README.md): it
lists every script with its purpose and hardware risk, and shows the safe read-only bus scan to begin
with.

## Development

Run these inside the subproject you are changing (`twinarm/` or `descovery/`):

```bash
uv run ruff check .    # lint
uv run ruff format .   # format
uv run ty check        # type check
```

Lint and format settings are shared: the root `pyproject.toml` holds the `[tool.ruff]` configuration and
each subproject inherits it through `extend = "../pyproject.toml"`. Change shared rules in the root file.

## Documentation for AI coding agents

Instructions for AI coding agents live in [`AGENTS.md`](AGENTS.md) for the repository as a whole, in one
`AGENTS.md` per subproject, and in [`.claude/rules/`](.claude/rules/).
[`CLAUDE.md`](CLAUDE.md) points Claude Code at `AGENTS.md`.

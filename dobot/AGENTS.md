# AGENTS.md — dobot

Dobot Magician pick-and-place sandbox (overhead camera → homography → pydobot, with optional
voice/LLM). Repository-wide instructions are in [`../AGENTS.md`](../AGENTS.md), and the hardware
safety rules there apply to everything here: the Magician is a real arm.

Read @README.md for the script inventory, each script's hardware risk, the quick start, and the
first-time setup manual.

## Rules for this directory

- Run `uv` commands here (`uv sync`, `uv run python …`, `uv run pytest`). This is an independent uv
  project like `descovery/`; nothing is built or published from it.
- **Never pick a serial port for the user.** `check_robot.py --list` only lists candidates;
  `--port` (or `robot.port` in `config.json`) must be given explicitly, and `PydobotRobot.connect()`
  refuses to guess. Leader/follower confusion does not exist here, but the Magician still moves.
- Scripts that move the arm do so only behind explicit flags (`--home`, `--round-trip`, `--robot
  pydobot`) and only when the user asked for that run in the current session. `--dry-run` and
  `--dry` never touch hardware and are the default rehearsal path.
- Do not claim the arm or camera behaved a certain way unless the user ran it and reported the
  result. `check_camera.py` and `check_robot.py` print what they measured; quote that.
- Keep the flat-module layout (`config.py`, `camera.py`, … imported by bare name and run from this
  directory). Do not refactor these into the `twinarm/` library; the design source of truth is
  `robotics/TacitCapture/91_…` and `93_…` (the planning repo), and this directory is the runnable
  port of `robotics/scripts/magician_pnp/`.
- Tests live in `tests/` and must pass with no hardware attached (`uv run pytest`). Add a test
  for any new guard or pure function before wiring it to hardware.
- Ruff docstring rules (`D`) are switched off for this sandbox in `pyproject.toml`; `F` and import
  order still apply (`uv run ruff check .`).

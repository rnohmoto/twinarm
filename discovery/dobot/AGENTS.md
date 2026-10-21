# AGENTS.md — dobot

Dobot Magician pick-and-place sandbox (overhead camera → homography → pydobot, with optional
voice/LLM). Repository-wide instructions are in [`../AGENTS.md`](../AGENTS.md), and the hardware
safety rules there apply to everything here: the Magician is a real arm.

Read @README.md for the script inventory, each script's hardware risk, the quick start, and the
first-time setup manual.

When the task is to run or re-set-up the bench demo, read [`docs/HANDOFF.md`](docs/HANDOFF.md) next: it holds
the verified configuration (port, camera lock, heights, zones, calibration) and the exact commands to
redo each step.

## Rules for this directory

- Run `uv` commands here (`uv sync`, `uv run <console script>`, `uv run pytest`). This is an
  independent uv project; it is packaged (`uv_build`) so the scripts get entry points, but nothing
  is published from it.
- **Never pick a serial port for the user.** `dobot-check-robot --list` only lists candidates;
  `--port` (or `robot.port` in `config.json`) must be given explicitly, and `PydobotRobot.connect()`
  refuses to guess. Leader/follower confusion does not exist here, but the Magician still moves.
- Scripts that move the arm do so only behind explicit flags (`--home`, `--round-trip`, `--robot
  pydobot`) and only when the user asked for that run in the current session. `--dry-run` and
  `--dry` never touch hardware and are the default rehearsal path.
- Do not claim the arm or camera behaved a certain way unless the user ran it and reported the
  result. `dobot-check-camera` and `dobot-check-robot` print what they measured; quote that.
- The modules live flat inside one package, `src/dobot/` (`config.py`, `camera.py`, …), imported
  absolutely (`from dobot.config import AppConfig`) and run through the console scripts in
  `pyproject.toml` (`dobot-demo`, `dobot-calibrate`, …). Keep that shape: add a module beside the
  others rather than nesting sub-packages, and give a new entry point a `main(argv=None)` and a
  `[project.scripts]` line. Do not refactor these into the `twinarm/` library; the design source of
  truth is `robotics/TacitCapture/91_…` and `93_…` (the planning repo), and this directory is the
  runnable port of `robotics/scripts/magician_pnp/`.
- `config.json` lives in `src/`; `assets/` and `logs/` stay in this directory. Reach all three
  through `dobot.paths` (`CONFIG_PATH`, `ASSETS_DIR`, `LOGS_DIR`, `from_root()` for the relative
  paths stored in `config.json`) so a console script works from any current directory. Never
  resolve them against the cwd or against `__file__`.
- Tests live in `src/dobot/tests/` and must pass with no hardware attached (`uv run pytest`). Add a
  test for any new guard or pure function before wiring it to hardware.
- `demo.py` (`dobot-demo`) is the only long-running process that owns the camera and the arm, and it
  does so on one worker thread. `panel_web.py` never touches hardware: browser buttons queue
  commands. Keep that split when adding UI features. `dobot-suction panel` (pump only) and
  `dobot-jog` (step moves plus pump) also open the Magician's port, with the same split: one worker thread owns the serial
  port and HTTP handlers only submit jobs to it. Only one of these processes can run at a time.
- `launcher.py` (`dobot-launcher`) is the operator's entry point. It must never move the arm on its own: the arm moves only through
  its start / restart / calibrate buttons, each labelled as such, and it stops the demo before opening any tool that
  needs the camera or the serial port. Keep the settings it exposes to the few knobs an operator tunes on site.
- `config.json` is generated from `config.py` defaults (`uv run dobot-config src/config.json`)
  and then edited by `dobot-setup` on the real bench. Do not hand-write coordinates into it
  from here; they come from the arm's pose on site.
- Ruff docstring rules (`D`) are switched off for this sandbox in `pyproject.toml`; `F` and import
  order still apply (`uv run ruff check .`). Note that `uv run ruff format .` would rewrite most of
  this sandbox, which has never been formatted — do not fold that into an unrelated change.

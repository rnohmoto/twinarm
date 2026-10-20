# AGENTS.md — koch

The Koch v1.1 arms bench: a uv project with the `koch` package and console scripts. Repository-wide
instructions are in [`../../AGENTS.md`](../../AGENTS.md); the hardware safety rules there apply to
everything here.

Read @README.md for the layout, the console scripts, each one's hardware risk, and how to run them.
A new session starts at [`docs/HANDOFF.md`](docs/HANDOFF.md).

## Rules for this directory

- Check a script's risk class in the inventory before running it. Anything marked *moves motors*,
  *energizes*, *torque off*, or *writes motor config* needs an explicit request from the user in the
  current session: `koch-set-id` rewrites EEPROM, `koch-calib` and `torque_off` make a raised arm
  drop, every teleop entry moves motors.
- Never guess a serial port. `koch-launch --list` shows what is plugged in; the user says which is
  the leader and which is the follower.
- Run `uv` here (`cd discovery/koch && uv sync`), never in `discovery/`. `mise run check` runs
  ruff, ty and the offline pytest suite; `./start.sh rehearse` (or `mise run rehearse`) drives the
  launch paths against simulated servos for about three minutes. Run both after changing anything
  under `src/koch/`. Run the rehearsal alone: its frame-rate checks sit a couple of fps above what a
  busy machine delivers.
- `src/koch/feedback.py` and `src/koch/link.py` are verbatim mirrors of
  `twinarm/src/twinarm/domain/`; `tests/test_mirror.py` compares them by syntax tree. Change the
  library first, then copy.
- Importing any `koch` module must stay free of side effects (no port opened, no logger touched);
  `koch.console` is called from `main()`. `tests/test_entry_points.py` and `tests/test_tools.py`
  enforce it.
- `simbus/` is the rehearsal harness and stays outside the package: its fake `dynamixel_sdk` is put
  on `PYTHONPATH` to shadow the real one. Child processes are spawned as `python -m koch.<module>`
  so that shadowing is inherited.
- `main()` and the frame loop in `teleop.py` keep their hardware-tested shape on purpose; extract pure
  logic into modules, do not restructure the loop without an arm session to verify it.

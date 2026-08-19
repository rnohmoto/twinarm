# Descovery

Hardware experiment sandbox for the Koch v1.1 arms. (The directory name is spelled "descovery" on
purpose.) The scripts here are flat and standalone — no package structure, no shared modules, and some
deliberate duplication between them.

Part of the TwinArm monorepo; see [`../README.md`](../README.md) for requirements and setup.

## Quick start

```bash
uv sync
uv run python koch_scan.py /dev/tty.usbmodem<XXXX>
```

`koch_scan.py` is the safe place to start: it only broadcast-pings the bus and prints the motors it
finds. Everything else needs more care — check the risk column below first.

## Script inventory

Risk classes:

- **read-only** — reads registers only.
- **torque off** — releases the motors; an arm held up by torque will drop when it runs.
- **energizes** — turns torque on; motors hold position and draw current.
- **moves motors** — commands motion.
- **writes motor config** — writes EEPROM (motor IDs and similar); undoing it is a manual job.
- **network only** — no serial access of its own.

| Script | Purpose | Hardware risk | Extra I/O |
| ------ | ------- | ------------- | --------- |
| `koch_scan.py` | Broadcast-ping an assembled arm at 1 Mbps and 57600 bps; prints ID, model and firmware for every motor found. | read-only | — |
| `koch_read_loop.py` | Read the positions of IDs 1–6 for 30 s and report success/failure counts — a comms stability test. | read-only | — |
| `koch_calib_offset.py` | Compare leader and follower calibration: raw ticks and normalized position per joint, to quantify a zero-point or range mismatch. Both arms must be held in the same pose by hand. | torque off (both arms) | reads calibration JSON |
| `koch_current_monitor.py` | Real-time current/load plots for one or two arms, with per-axis statistics on exit. Cannot share a port with a running teleop session. | read-only by default; `--torque on` energizes | CSV out (`--csv`) |
| `koch_set_id.py` | Change one motor's Dynamixel ID. Refuses to run unless exactly one motor is on the bus. | writes motor config | — |
| `setup_follower_id.py` | Run lerobot's `KochFollower.setup_motors()` for the follower arm. The port is hard-coded — edit it before use. | writes motor config | — |
| `koch_teleop_robust.py` | Wrapper around `lerobot-teleoperate` that retries `sync_read` 10× and reconnects up to 20 times after a comms loss. Takes the same arguments as `lerobot-teleoperate`. | moves motors | — |
| `koch_teleop_plus.py` | Successor to the above: a custom teleop loop built on lerobot classes, with optional force feedback (`--ff gripper`, `--ff arm`), telemetry, and auto-reconnect. Can launch the two viewers below via `--plot` and `--panel`. | moves motors; force feedback puts leader joints into current control | UDP telemetry out :8765, control in :8766, CSV out (`--csv`) |
| `koch_live_plot.py` | matplotlib viewer for teleop telemetry: leader command, follower position, the L−F error, and currents. | network only | UDP in :8765 |
| `koch_web_panel.py` | Stdlib-only browser control panel with Canvas graphs. Switches force-feedback mode, resyncs, or stops a running teleop session. | network only, but it commands a live teleop session | HTTP :8780 (SSE), UDP in :8765, control out :8766 |

## Notes

- Some docstrings still say `conda activate twinarm`. That is stale — this project uses uv.
- `pyproject.toml` and `uv.lock` here are a copy of the ones in `twinarm/`, package name included. They
  exist only to pin dependencies for `uv run`; nothing is built or published from this directory.
- The force-feedback design in `koch_teleop_plus.py` — gain caps, current limits, and the thermal
  cut-off — is documented in that script's module docstring. Read it before changing feedback gains.

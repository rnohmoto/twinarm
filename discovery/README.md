# Discovery (sandbox)

Hardware experiment sandboxes for the robot arms. Each one is its own uv project; this directory
only holds the ruff configuration they share (`pyproject.toml`, no dependencies — never `uv sync`
here). Part of the TwinArm monorepo; see [`../README.md`](../README.md) for requirements and setup.

| Folder | What it is |
| ------ | ---------- |
| [`koch/`](koch/README.md) | The Koch v1.1 arms bench: the `koch` package and console scripts (teleop with force feedback, two-pair launcher, VR bridge, panel, wireless follower host, bus tools), the Learning Fest runbooks under `koch/docs/`, and a rehearsal harness that needs no arms. A new session starts at [`koch/docs/HANDOFF.md`](koch/docs/HANDOFF.md). Successor of the `mock/v0/` scripts and of `koch4/` (both in git history). |
| [`dobot/`](dobot/README.md) | Dobot Magician pick-and-place sandbox: overhead camera → homography → pydobot. |
| [`mock/v1/ui/`](mock/v1/ui/README.md) | A static HTML mock of a next-generation dual-arm teleoperation cockpit — the design successor to the panel in `koch/`. Simulated data only (open `index.html` in a browser; no hardware, no risk). |

## Risk classes

Every script inventory in these folders uses the same classes:

- **read-only** — reads registers only.
- **torque off** — releases the motors; an arm held up by torque will drop when it runs.
- **energizes** — turns torque on; motors hold position and draw current.
- **moves motors** — commands motion.
- **writes motor config** — writes EEPROM (motor IDs and similar); undoing it is a manual job.
- **network only** — no serial access of its own.

The safe place to start on an unknown arm is `koch-scan` (read-only: it pings the bus at both bauds
and prints the motors it finds):

```bash
cd koch && uv sync
uv run koch-scan /dev/tty.usbmodem<XXXX>
```

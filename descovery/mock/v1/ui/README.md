# TwinArm Cockpit — UI Mock v1

A static HTML mock of a next-generation teleoperation cockpit for TwinArm: two Koch v1.1
leader/follower pairs monitored and operated from one screen. It is the design successor to the
embedded panel in [`../../v0/koch_web_panel.py`](../../v0/koch_web_panel.py) — same data contract, "cockpit"
levels of functional coverage. Nothing here talks to hardware or to a backend; every value on screen
comes from a built-in fake telemetry engine, and the page permanently wears a
**MOCK · SIMULATED DATA** badge so screenshots cannot pass as real telemetry.

## Opening it

No build step, no dependencies, works offline:

```bash
open index.html                # file:// works — scripts are plain, no ES modules
# or, if you prefer a server:
python3 -m http.server 8901    # from this directory, then http://127.0.0.1:8901/
```

## What is on screen

| Region | Contents |
| ------ | -------- |
| Masthead | Session clock, annunciator strip (per-arm COMM / HEAT / TRACK, hardware VOLT / ENC / SHOCK / LOAD), REC indicator with CSV filename, guarded MASTER STOP (flip the hazard cover, then confirm; it re-arms itself after 3 s). |
| Wings (ARM L / ARM R) | Link chip and serial ports, mode/temp/FF/frames/reconnect chips, 2D posture view (dashed ghost = leader command, solid = follower actual), six joint gauges (bar = follower, ▲ bug = leader command), four strip charts (leader pos / follower pos / delta L−F / current with dashed FF command), force-feedback console (off / gripper / arm segment, the six sliders from `PARAM_SPECS`, RESYNC, STOP ARM). |
| Center | Gripper camera viewports (one per arm — today's hardware has a single camera on the follower gripper, so the left one shows NO SIGNAL), startup sequence checklist (PORT → CALIB → LINK → RESYNC → ENGAGE per arm), event log (newest first, capped at 200, every control click logs the exact `/ctl` JSON it would send), and the SIM panel. |

## The SIM panel (mock-only)

The dashed amber panel drives the fake telemetry; nothing in it exists on the real panel. Scenarios:

- **R only** (default) — right pair live, left pair absent, matching today's hardware.
- **Dual nominal** — the left pair joins: its checklist runs, then it engages.
- **Heat caution** — the gripper motor climbs past the real firmware guards: at 60 °C the grip gain
  halves itself (watch the slider move), at 65 °C force feedback shuts off; the temperature then
  settles in the caution band.
- **Reconnect storm** — the right link drops and recovers repeatedly: chart gaps, reconnect
  counter, TRACK caution from the accumulated offset (RESYNC clears it), LOAD flashes.
- **E-stop** — the frozen end state after a master stop. Recover by picking another scenario.

Plus PAUSE (freezes the sim clock — charts, REC elapsed, everything) and RECORDING.

## Fidelity notes

- Telemetry frames follow the shape of the real contract
  (`twinarm-web-ui/src/shared/api/telemetry.ts`): `{t, pos, fpos, cur, ff, mode, temp, n, rec,
  params}` at 15 Hz. Parameter names, labels, and ranges are verbatim from `commands.ts`.
- `temp` is a single value (the leader gripper motor), as in the real system — per-joint
  temperatures are not invented.
- Shoulder joints are XL430s with no current sensor; like the original panel, the current chart
  plots their load % ×10 and says so in its caption.
- The 60/65 °C behavior, the 1.5 s soft-resync merge, and the reconnect counter mirror
  `koch_teleop_plus.py`. The VOLT / ENC / SHOCK / LOAD lamps map to `Hardware_Error_Status` bits;
  only LOAD is exercised by a scenario (reconnect storm), the others stay dark fixtures. The HEAT
  lamp shows the software temperature guard, which is distinct from the hardware overheat bit.
- The posture view is **illustrative, not kinematic**: normalized joint values scale to drawing
  angles with fixed segment lengths (see the constants in `js/posture.js`).
- The right arm's serial ports are the real ones from the descovery docstrings; the left pair's are
  invented siblings.

## Files

```
index.html        markup; both wings instantiate from one <template>
css/tokens.css    design tokens — palette (dataviz-validated), type, spacing, motion
css/cockpit.css   layout and components
js/sim.js         fake telemetry engine + scenario state machine
js/charts.js      canvas strip charts (15 s window, dirty-flag redraw, dropout gaps)
js/posture.js     SVG posture schematic
js/wing.js        wing instantiation and per-arm bindings
js/app.js         masthead, annunciator, log, checklist, SIM panel, master stop wiring
```

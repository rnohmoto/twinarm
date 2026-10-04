# simbus — rehearse the koch4 scripts with simulated servos

`dynamixel_sdk/` is a stand-in for the Dynamixel SDK. With this folder first on `PYTHONPATH`,
lerobot's `DynamixelMotorsBus` talks to simulated Koch servos, so the launcher, the teleop, the
VR bridge, the panel and the follower host run end to end with no arms attached. `rehearse.py`
drives them through seven scenarios and checks what they did.

```bash
./koch4/start.sh rehearse            # every scenario, about 3 minutes
./koch4/start.sh rehearse vr2        # one scenario
./koch4/start.sh rehearse --list
```

Run it after `uv sync` on a new machine, and after changing any koch4 script, before the arms.
It uses the fixed localhost ports of a real session (8765-8773, 8444, 8780, 9101), so stop a
live session first. Logs and the register trace of each scenario stay in
`koch4/work/rehearsal/<scenario>/`.

## Scenarios

| Scenario | What runs | What is checked |
| --- | --- | --- |
| `handshake` | launcher, two wired pairs, the panel | both pairs reach the panel; the grip feedback rises above its floor and stays under the cap; the follower grip cap and the leader P gain are written; torque off at exit |
| `vr` | launcher `--vr B --vw --follower none`, bridge | starts with the follower unplugged; the page is served; squeezing engages the wall; weight currents on shoulder and elbow within the cap; nothing is written to the follower |
| `vr2` | the same with `--vr2` | two twins; the hand-moved follower's shoulder runs in PWM mode: zero duty right after the mode change and before every torque-on, duty within the cap, position mode and the PWM limit restored at exit; wall cap scaled by 0.45 |
| `wireless` | `koch4_follower_host.py` plus a leader-side teleop over UDP | 25 fps or more; link statistics in the telemetry; the host holds the pose when targets stop; no per-frame clamp warning in the host log |
| `mismatch` | launcher with a leader whose EEPROM differs from the calibration file | the start stops with the reason on the launcher's screen and a non-zero exit; nothing is written to the calibration registers; torque off |
| `dropout` | launcher with a 1.5 s bus dropout, then a latched overload | the teleop reconnects once and keeps running; the grip anchor is carried over; the overload alert reaches the telemetry |
| `fest` | two launchers: handshake on pair A, VR on pair B | both run together; stopping the VR launcher leaves the handshake running |

Every scenario also checks that no write to the EEPROM area was refused (a write with torque
on is an ordering bug that the real servo answers with an Access Error) and that the servos
are left with torque off.

## What it is, and what it is not

- It runs the **real** lerobot (`KochLeader`, `KochFollower`, `DynamixelMotorsBus`) and the real
  koch4 scripts as separate processes; only the SDK underneath is replaced.
- The simulated servo keeps a control table per model (addresses and lengths from lerobot's own
  tables), refuses EEPROM writes while torque is on, resets gains / `Goal_PWM` / `Goal_Current`
  when `Operating_Mode` is written, tracks its goal position, lets a blocked follower gripper
  draw current, and follows a scripted hand on leader joints.
- It does **not** model forces, friction, heat or real timing. A pass here says the software
  path holds together; it says nothing about how the arms behave. Hardware results come only
  from the user running CHECKLIST.md on the arms.
- The stand-in refuses to load unless `KOCH4_SIMBUS` names a scenario file, and `rehearse.py`
  gives the scripts plain files as "ports". Never put this folder on `PYTHONPATH` for a session
  with arms.

## Scenario file

`rehearse.py` writes one per scenario (`work/rehearsal/<name>/scenario.json`):

```json
{"ports": {"<port string given to the script>": {
    "kind": "leader",                 // or "follower": the servo models behind the port
    "calibration": "<calibration json>",
    "hand": true,                     // joints follow a scripted hand (leader, or a hand-moved follower)
    "object": 0.5,                    // a follower gripper cannot close below this fraction of its range
    "eeprom_mismatch": false,         // EEPROM differs from the file: lerobot would ask to write it
    "drop_at_s": null, "drop_for_s": 1.0,   // bus dropout window
    "hw_error_at_s": null             // latch an overload error on the gripper
  }},
 "trace": "<path>"}                    // register writes and final dumps, one file per process
```

To add a scenario, write a function in `rehearse.py` that declares the arms, starts the scripts
with `r.launch(...)` / `r.start(...)`, records `r.check(label, ok, detail)` and ends with
`r.finish(ctl_ports)`, then add it to `SCENARIOS`.

# koch4 — two Koch pairs (four arms), dedicated config/work folders, VR virtual wall

**Starting fresh (a new session, a new machine)? Read [HANDOFF.md](HANDOFF.md) first:** it holds
the state of the work, the decisions already made, the open ones, and the prompt to start a
new conversation with.

Successor of the `mock/v0/` scripts for the Learning Fest setup (2026-10-26/27): two
leader/follower pairs run side by side as the rule (one panel, one set of gains),
everything machine-specific lives in this folder, and — as a separate track — a single
leader can "grasp" a virtual object seen in a Quest Pro, feeling its stiffness on the
trigger and its weight on the arm. Part of [descovery](../README.md); the repository's
hardware safety rules apply to every script here.

## Layout

```
koch4/
├── start.sh               one entry point: check / handshake / host / vr / vr2 / sim / rehearse / quest / wifi / manual
├── manual/                HTML manuals for the fest: index.html (entry), handshake.html, vr.html, quest_setup.html (Meta account → developer mode → adb)
├── koch4_teleop.py        one pair: teleop + telemetry + leader force feedback (3 styles + virtual wall/weight)
├── koch4_dual_launch.py   both pairs by default (--pair A|B for staging), one panel, fixed ports, logs
├── koch4_web_panel.py     one browser panel for every pair: shared sliders broadcast to all pairs
├── koch4_live_plot.py     matplotlib telemetry viewer (unchanged copy of mock/v0)
├── koch4_calib_offset.py  leader/follower pose-offset check (torque off) — after overloads / drift
├── koch4_vr_bridge.py     serves webxr/ to the headset, /state /config /contact for one or two arms, relays to each teleop
├── koch4_quest_usb.py     USB route: adb reverse + open http://localhost:<port>/ in Quest Browser (+ scrcpy mirror)
├── webxr/index.html       three.js page: twins (1-2 arms), objects with physics (grab / drop / throw / bounce), in-headset calibration, editor (+ setup_assets.py)
├── simbus/                rehearsal with no arms: a virtual Dynamixel bus (dynamixel_sdk/) and rehearse.py, seven scenarios with checks
├── config/                koch4_config.json (git-ignored; see .example), koch4_twin.json (saved by the editor)
│   └── calibration/koch_follower/<id>.json, koch_leader/<id>.json   ← lerobot calibration
├── work/                  logs/ csv/ _certs/  (git-ignored)
└── CHECKLIST.md           Mac test procedure TEST 0–6 + VR V0–V3 (+ weight, editor) with record fields
```

`--config-dir` / `--work-dir` (defaults: the two folders above) are passed to every process by
the launcher. lerobot's `calibration_dir` is set explicitly, so calibration JSON is read and
written under `config/calibration/` instead of `~/.cache/huggingface/lerobot/calibration/`.
Copy the existing files there (`--lerobot-cache` keeps the old location if you prefer).

## Script inventory and hardware risk

Risk classes are the ones defined in [`../README.md`](../README.md).

| Script | Purpose | Hardware risk | Extra I/O |
| ------ | ------- | ------------- | --------- |
| `koch4_teleop.py` | One pair. `--ff gripper --ff-style spring` (default; the 2026-09-04 fixes) / `--ff-style error` / `--ff arm` (untested on hardware) / `--ff vwall` (virtual wall, follower optional: `--follower-port none`) / `--vw` (virtual weight on `shoulder_lift` + `elbow_flex`, only while an object is grasped; `--vw-scale`, `--vw-cap` 120 mA, `--vw-invert`) / `--follower-grip-ma` (Goal_Current cap on the follower gripper against overload shutdowns). `--leader-type koch_follower` uses a Koch follower *moved by hand* as the input device (second player: arm joints torque-free, gripper wall only, weight on the elbow by current and on the XL430 shoulder by PWM mode with `--vw-pwm-scale` / `--vw-pwm-cap`, M288 current scaling, `--arm-label`). `--selftest` runs the laws with no hardware. | moves motors; force feedback and `--vw` put leader joints into current control; `vwall` holds the leader gripper in current-based position mode | UDP telemetry out (`--viz-port`, comma list), control in (`--ctl-port`), CSV in `work/csv/` |
| `koch4_dual_launch.py` | `--list` ports+serials (read-only), `--init` config template, `--doctor` readiness report (config, port names present, calibration files, VR asset → what can start; opens nothing), `--follower wireless` / `wired` / `none` (`none` = leader only, the follower need not be plugged in: one-player VR), `--pair both` (default) / `A` / `B`, `--vr A|B` (+`--vw`) adds the bridge and forces that pair to `vwall`, `--vr2` also starts that pair's follower as a hand-moved second input (ports 8771/8772/8773) and gives the bridge both arms, `--grip-ma`, `--dry-run`. Ports: A 8765/8766 (+8769/8443), B 8767/8768 (+8770/8444), panel 8780. | launches `koch4_teleop.py` → moves motors | logs in `work/logs/dual_*.log` |
| `koch4_web_panel.py` | One page for all launched pairs: status chips and 4 graphs per pair, one row of sliders and mode buttons (OFF / gripper / arm / vwall) that go to every pair. | network only, but it commands live teleop sessions | HTTP `--http`, UDP in `--telemetry` (list), out `--ctl-port` (list) |
| `koch4_live_plot.py` | matplotlib telemetry viewer. | network only | UDP in |
| `koch4_calib_offset.py` | Torque off both arms, hold the same pose by hand, print raw ticks and normalized % per joint with the JSON fix to apply. Reads `config/calibration/`. | torque off (both arms) | reads calibration JSON |
| `koch4_follower_host.py` | **Wireless follower.** Runs on the computer next to the follower (Raspberry Pi 4, or any Windows/Mac/Linux PC with lerobot): receives joint targets over UDP from the Mac's teleop (`--follower-port udp://<host>:9101`), drives lerobot's KochFollower, streams currents/positions/temperature/errors back. Holds the pose after 0.5 s without targets (torque kept), ramps back in after a gap; `--sim` needs no hardware. Same role as lerobot's LeKiwi host. | moves motors | UDP `--listen` (A 9101, B 9102) |
| `koch4_vr_bridge.py` | Serves `webxr/`, republishes telemetry as `/state` (every arm; `--arms B,F` with one telemetry/control port per arm), keeps `config/koch4_twin.json` (v2: per-arm joints/base, objects with bounce, desk) behind `/config`, forwards `POST /contact {"arm","vwall"}` to that arm's teleop and the twin joint map to it (for the weight FK). `--sim` needs no teleop. | network only, but it commands the leader's virtual wall and weight (clamped by the teleop; dropped after 3 s without a refresh) | HTTP(S) `--port`, UDP in `--telemetry`, out `--ctl-port` |
| `koch4_quest_usb.py` | USB route for the headset: finds adb, `adb reverse tcp:<port> tcp:<port>`, opens `http://localhost:<port>/` in Quest Browser (`--spectator`, `--mirror` = scrcpy, `--wifi` = move adb to Wi-Fi so the headset's USB port can take a 45 W charger, `--check` = status and battery level only). Needs developer mode once (CHECKLIST U0). | none (network only) | adb over USB |
| `webxr/setup_assets.py` | Downloads `three.module.js` (r160) once; the file is git-ignored. `start.sh check` runs it. | none | network (once) |
| `simbus/rehearse.py` | Runs the launcher, teleop, bridge, panel and follower host against simulated servos (`simbus/dynamixel_sdk`, a stand-in for the SDK that lerobot loads from `PYTHONPATH`) and checks frame rate, feedback commands, register write order, refused EEPROM writes and torque at exit. Scenarios: `handshake` `vr` `vr2` `wireless` `mismatch` `dropout` `fest`. See [`simbus/README.md`](simbus/README.md). | none (no port is opened; fixed localhost ports, so not next to a live session) | logs in `work/rehearsal/` |

## Wireless follower (leader wired to the Mac, follower on its own computer)

```
Mac ── USB ── leader                 handshake table: host PC ── USB ── follower ── 12 V
 koch4_teleop.py --follower-port udp://<host>:9101     koch4_follower_host.py --port <serial> --listen 9101
                     └── own 5 GHz router (Mac, host PC, Quest only) ──┘
```

- The Dynamixel bus stays local on each side; only joint targets (30 fps, with sequence
  numbers) and state go over UDP. A transparent "wireless serial cable" would put the radio
  inside the bus's request/response timeout and fail; this design does not.
- Link watchdog (`twinarm/domain/link_watchdog.py`, mirrored in both scripts): 0.3 s late
  → alert on the panel/VR; 0.5 s → the host holds the pose (torque kept, so the arm does not
  drop); 3 s → the Mac treats it as a dropout and reconnects; on resume the host ramps in
  over 1.5 s. The Mac shows `age_ms` / `rtt_ms` / loss in the telemetry.
- Backup: `koch4_dual_launch.py --follower wired` uses the USB `follower_port` again with the
  same config. A Raspberry Pi is not required — any computer that runs lerobot works.

## Quick start (Mac, from `descovery/`)

The shortest path is `./koch4/start.sh` (`check` / `handshake` / `host` / `vr` / `vr2` / `sim` /
`rehearse` / `quest` / `wifi` / `manual`); the HTML manuals in [`manual/`](manual/index.html) walk
through each with diagrams. Arguments after the mode go to the launcher
(`./koch4/start.sh handshake --pair A`, `./koch4/start.sh vr --extra "--vw-cap 60"`). Handshake on
pair A and VR on pair B can run at the same time from two terminals; stopping one leaves the
other running. The commands below are what it runs.

```bash
uv sync
uv run python koch4/koch4_teleop.py --selftest          # no hardware: control laws
uv run python koch4/koch4_dual_launch.py --list         # read-only: ports + USB serials
uv run python koch4/koch4_dual_launch.py --init         # → config/koch4_config.json, fill it in
uv run python koch4/koch4_dual_launch.py --pair A --ff gripper        # TEST 2 (staging)
uv run python koch4/koch4_dual_launch.py --ff gripper --grip-ma 500   # TEST 4: both pairs (default)
# VR is a separate track: one leader, wall + weight. USB route (no venue Wi-Fi): bridge over http + adb reverse
python koch4/webxr/setup_assets.py
uv run python koch4/koch4_dual_launch.py --pair B --vr B --vw --vr-http      # page on http://localhost:8444/ in the headset
uv run python koch4/koch4_quest_usb.py --port 8444                           # other terminal: adb reverse + open Quest Browser
uv run python koch4/koch4_dual_launch.py --pair B --vr B --vw --vr-http --vr2  # two players: the pair's follower moved by hand
```

Ports are supplied by the user (never guessed); leader and follower ports are not
interchangeable. Every command above that starts a teleop moves motors — run it only when
asked. The full procedure with pass criteria and record fields is in [CHECKLIST.md](CHECKLIST.md).

## Force-feedback styles (leader arm, XL330-M077)

| `--ff` | Servo mode | Law | Where it comes from |
| --- | --- | --- | --- |
| `gripper` + `spring` | 5 (current-based position), `Goal_Position` = calibrated open end, `Position_P_Gain` 800 | `Goal_Current = floor + gain × EMA(|I_follower| − deadband)`, capped | mock/v0 + branch `rn/fix/gripper-force-feedback` (measured on hardware 2026-09-04) |
| `gripper` + `error` | 0 (current) | current ∝ (leader command − follower position) beyond a deadband, only after the follower has stalled, slew-limited | robotics 2026-08-04 (hardware session S23) |
| `arm` | 0 on elbow/wrist joints | `−gain × EMA(I_follower)` per joint | FACTR-style; not yet verified on hardware |
| `vwall` | 5, `Goal_Position` = wall tick, `Goal_Current` = 0 outside / cap inside, `Position_P_Gain` = object stiffness | host only decides *engaged* (opening ≤ width, with hysteresis); the servo's own loop renders the wall | this folder; robotics `TacitCapture/94_` |
| `vwall --vw` | 0 on `shoulder_lift` + `elbow_flex` | `I = scale × (m·g·lever) / Kt`, lever from the twin's planar FK, capped, EMA; zero unless an object is grasped | this folder; direction must be checked on hardware (CHECKLIST V-w) |
| `vwall --vw` with `--leader-type koch_follower` | 0 on `elbow_flex` (XL330-M288), 16 (PWM) on `shoulder_lift` (XL430-W250, no current control) | elbow as above with Kt 0.354; shoulder `Goal_PWM = pwm_scale × torque / 1.5 N·m × 885`, capped by `--vw-pwm-cap`. Torque is on only while an object is grasped; `Goal_PWM` is zeroed before torque-on (in position mode the register is the PWM limit, 885) and mode 4 + the limit are restored on exit | this folder (`PwmWeightJoints`, write order checked by `--selftest`); direction and strength must be checked on hardware (CHECKLIST V5) |

The laws are pure functions mirrored from
[`twinarm/src/twinarm/domain/gripper_feedback.py`](../../twinarm/src/twinarm/domain/gripper_feedback.py),
which is unit-tested (`mise run test` in `twinarm/`). descovery scripts may not import the
library (separate uv projects, standalone-script rule), so the two copies are kept in sync by
hand; `koch4_teleop.py --selftest` checks the mirrored copy against the same vectors.

## VR page (`webxr/index.html`)

- One twin per arm from `config/koch4_twin.json` v2 (`arms: {B: {...}, F: {...}}`; a v1 file with
  top-level `joints`/`base` is migrated to the first arm). Objects (`objects`, in the first arm's
  frame) carry touch width, `p_gain`, `cap_ma`, `release`, `mass_g`, `bounce`, colour, kind, spot;
  `scene` holds the desk size, sound and the AR ghost mode.
- Physics: a grasped object follows the fingertips; on release it leaves with the fingertip
  velocity of the last ~110 ms (capped at 5 m/s), falls under gravity with per-object drag, bounces
  on the desk / floor with its `bounce` and friction, collides with the other objects, and sleeps
  when slow. Impacts make a synthesized sound (S toggles). **R** / 「配置リセット」 puts every
  object back on its spot.
- **C** / 「位置合わせ」 (in the headset): touch the real fingertip midpoint with the controller tip
  (or the index fingertip with hand tracking) and pull the trigger in 2-3 arm poses; the page
  solves the base x/y/z/yaw by least squares (1 point = position, 2+ = yaw), shows the residual,
  and 「確定」 saves it. `koch4SelfTest()` in the desktop console checks the solver.
- **G** cycles how the twin is drawn in AR (solid / half-transparent / fingertip ring only).
  **V** toggles the desktop view between the first-person preview (default, for the monitor) and
  the orbit view. **Tab** selects the arm for keys 1/2/3 (force an object), 0 (clear), E (editor).
- **E** / 「編集」 opens the editor: per-arm joint span / offset / sign and base, per-object
  width / gain / cap / mass / bounce and 「ここに置く」, desk size, ghost mode, sound. 「保存」
  writes the file and sends each arm's joint map to its teleop.
- `?spectator=1` shows the same scene without sending contacts (desktop mirror next to the
  headset; MQDH Cast or scrcpy show the headset's own view instead).
- Alerts from every teleop (prefixed `[B]` / `[F]` with two arms) appear as a red banner in the
  page and as red chips on the panel; CHECKLIST.md lists what to do for each.
- `window.koch4` exposes objects / arms / config for console checks.

## Graduating to `twinarm/`

What can move into the library is what needs no arm attached: the control laws (already in
`domain/gripper_feedback.py`), the config schema and port resolution (`infrastructure/`), the
virtual-wall/weight state machines and the `/stream`–`/ctl` contract (`api/features/telemetry`,
`control`). What stays here is the bus-driving frame loop and anything that only makes sense
with hardware. Move a piece only after it has been exercised on the arms and recorded in
CHECKLIST.md; write the test first (TDD rule in `../../.claude/rules/common/testing.md`).

## Known limits

- `koch4_live_plot.py` is a copy of the v0 script and carries its lint findings; the other
  scripts pass `ruff check` and `ruff format --check`, and `ty` reports only the imports that
  need the hardware environment (`lerobot`, `serial`, `dynamixel_sdk`).
- `koch4_teleop.py` is ~1,300 lines after `ruff format`, above the 800-line guideline in
  `.claude/rules/common/coding-style.md`. It keeps the hardware-tested frame-loop shape of
  `mock/v0/koch_teleop_plus.py` plus the mirrored laws on purpose; split it only after the
  loop has been exercised on the arms (the laws already live in `twinarm/domain`).
- The digital twin is the primitive model of robotics `84_`; joint spans are nominal until the
  editor's offsets are set against the real arm. Object widths are in lerobot's normalized
  0–100 gripper units; the weight lever arms use the twin's link lengths.
- Checked in a desktop browser (inline mode, `--sim`), with the launcher's `--dry-run`, and end to
  end against simulated servos on lerobot 0.6.1 (`simbus/`, 2026-10-04); the first-person VR,
  the second player and the wireless follower have not run on the arms or on the headset yet.
- When started by the launcher (output in a log file), a teleop whose calibration file differs
  from what the servos hold stops with the reason instead of waiting on lerobot's question; run
  `koch4_teleop.py` directly in a terminal to answer it.

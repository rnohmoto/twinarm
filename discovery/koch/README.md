# koch — the Koch v1.1 arms bench (two pairs, VR virtual objects, wireless follower)

**Starting fresh (a new session, a new machine)? Read [docs/HANDOFF.md](docs/HANDOFF.md) first:**
it holds the state of the work, the decisions already made, the open ones, and the prompt to start
a new conversation with.

Successor of the `mock/v0/` scripts and of `koch4/` for the Learning Fest setup (2026-10-26/27):
two leader/follower pairs run side by side as the rule (one panel, one set of gains), everything
machine-specific lives in this folder, and — as a separate track — a single leader can "grasp" a
virtual object seen in a Quest Pro, feeling its stiffness on the trigger and its weight on the arm.
Part of [discovery](../README.md); the repository's hardware safety rules apply to every command
here.

## Layout

```
koch/                      a uv project: `cd discovery/koch && uv sync`
├── scripts/               start.sh, one entry point: check / handshake / host / vr / vr2 / sim / rehearse / quest / wifi / manual;
│                          koch.sh (the launcher of record for VR) and the *.command files for Finder
├── src/koch/              the package behind the console scripts
│   ├── teleop.py          one pair: teleop + telemetry + leader force feedback (3 styles + virtual wall/weight)
│   ├── launch.py          both pairs by default (--pair A|B for staging), one panel, fixed ports, logs
│   ├── bridge.py          serves src/webxr/ to the headset, /state /config /contact for one or two arms
│   ├── panel.py (+ .html) one browser panel for every pair: shared sliders broadcast to all pairs
│   ├── follower_host.py   wireless follower: the computer next to the follower, driven over UDP
│   ├── live_plot.py       matplotlib telemetry viewer
│   ├── calib_offset.py    leader/follower pose-offset check (torque off) — after overloads / drift
│   ├── quest_usb.py       USB route: adb reverse + open http://localhost:<port>/ in Quest Browser
│   ├── scan.py / set_id.py / current_monitor.py   bus tools from mock/v0 (survey, IDs, current recorder)
│   ├── feedback.py, link.py    verbatim mirrors of twinarm/domain (control laws, link watchdog)
│   ├── motors.py, dxl.py, paths.py, control.py, telemetry.py, remote_follower.py, sim_follower.py,
│   │   lerobot_compat.py, console.py, selfcheck.py   the shared pieces the entry points import
│   ├── tools/             bench diagnostics, `python -m koch.tools.<name>` (preflight, snapshot, watchdog, ...)
│   ├── tests/             offline pytest suite (`mise run test`)
│   └── simbus/            rehearsal with no arms: a virtual Dynamixel bus (dynamixel_sdk/) and rehearse.py
├── src/webxr/index.html   three.js page: twins (1-2 arms), objects with physics, grab-to-move alignment, editor
├── config/                config.json (git-ignored; see config.example.json / config.fest.json), twin.json, solo_*/twin.json
│   └── calibration/koch_follower/<id>.json, koch_leader/<id>.json   ← lerobot calibration
├── docs/                  HANDOFF.md, CHECKLIST.md, REQUIREMENTS_VR.md, LOG_261019_VR.md, manual/*.html
└── work/                  logs/ csv/ _certs/ rehearsal/  (git-ignored)
```

`--config-dir` / `--work-dir` (defaults: `config/` and `work/` here, from `koch.paths`) are passed
to every process by the launcher. lerobot's `calibration_dir` is set explicitly, so calibration JSON
is read and written under `config/calibration/` instead of `~/.cache/huggingface/lerobot/calibration/`
(`--lerobot-cache` keeps the old location if you prefer).

## Setup and the console scripts

```bash
cd discovery/koch
uv sync                 # one venv for everything here (lerobot, pyserial, matplotlib, dynamixel-sdk)
./scripts/start.sh check        # no hardware: self-test, readiness report, port list, Quest check
mise run check          # ruff + ty + the offline tests
./scripts/start.sh rehearse     # about 3 minutes against simulated servos; run it alone
```

| Console script | Module | What it is |
| --- | --- | --- |
| `koch-teleop` | `koch.teleop` | one pair (`--selftest` needs no hardware) |
| `koch-launch` | `koch.launch` | both pairs, the panel and the bridge; `--list`, `--init`, `--doctor`, `--dry-run` are read-only |
| `koch-bridge` | `koch.bridge` | VR bridge (`--sim` needs no teleop) |
| `koch-panel` | `koch.panel` | browser panel |
| `koch-host` | `koch.follower_host` | wireless follower host (`--sim` needs no hardware) |
| `koch-plot` | `koch.live_plot` | matplotlib viewer |
| `koch-calib` | `koch.calib_offset` | pose-offset check (torque off) |
| `koch-quest` | `koch.quest_usb` | Quest USB route |
| `koch-scan` | `koch.scan` | bus survey at 1 Mbps and 57600 bps, `--loop` stability test |
| `koch-set-id` | `koch.set_id` | motor ID write, or lerobot's `--setup` |
| `koch-current` | `koch.current_monitor` | standalone current recorder |

Python-internal spawns use `python -m koch.<module>` (same venv, inherits the simbus `PYTHONPATH`);
`scripts/koch.sh` does the same so `status` / `stop` see one process per session.

## Entry points for the fest

`./scripts/start.sh <mode>` from this folder — `check` / `handshake [...]` / `host A|B <port>` / `vr [...]`
/ `vr2 [...]` / `sim` / `rehearse [name]` / `quest` / `wifi` / `manual`. Arguments after the mode go
to the launcher (`./scripts/start.sh handshake --pair A`, `./scripts/start.sh vr --extra "--vw-cap 60"`).
Environment: `VR_PAIR=A|B` (default B), `GRIP_MA` (default 500), `VR_MIRROR=1`.

`./scripts/koch.sh <mode>` is the launcher of record for VR and what the `scripts/*.command` files run from
Finder: `handshake` / `vr-leader` / `vr-follower` / `quest` / `all` / `status` / `stop`, with
`PAIR=A|B` (default B). It creates `config/config.json` from `config/config.fest.json` when missing,
resolves ports by USB serial, opens the Mac operator page (`?spectator=1&info=1`), waits for the
headset's USB-debugging allow and keeps the `adb reverse` alive. The HTML manuals in
[`docs/manual/`](docs/manual/index.html) walk through each with diagrams. Since 2026-10-25 `handshake`
runs with the **roles swapped** by default (the operator holds the follower machine, the leader machine
follows; `SWAP=0` restores the usual direction): see
[Swapped roles](#swapped-roles-the-handshake-held-by-the-follower-machine).

## Script inventory and hardware risk

Risk classes are the ones defined in [`../README.md`](../README.md).

| Script | Purpose | Hardware risk | Extra I/O |
| ------ | ------- | ------------- | --------- |
| `koch-teleop` | One pair. `--ff gripper --ff-style spring` (default; the 2026-09-04 fixes) / `--ff-style error` / `--ff arm` (untested on hardware) / `--ff vwall` (virtual wall, follower optional: `--follower-port none`) / `--vw` (virtual weight on `shoulder_lift` + `elbow_flex`, only while an object is grasped; `--vw-scale`, `--vw-cap` 120 mA, `--vw-invert`) / `--follower-grip-ma` (Goal_Current cap on the follower gripper against overload shutdowns). `--leader-type koch_follower` uses a Koch follower *moved by hand* as the input device (second player: arm joints torque-free, gripper wall only, weight on the elbow by current and on the XL430 shoulder by PWM mode with `--vw-pwm-scale` / `--vw-pwm-cap`, M288 current scaling, `--arm-label`). `--selftest` runs the laws with no hardware. | moves motors; force feedback and `--vw` put leader joints into current control; `vwall` holds the leader gripper in current-based position mode | UDP telemetry out (`--viz-port`, comma list), control in (`--ctl-port`), CSV in `work/csv/` |
| `koch-launch` | `--list` ports+serials (read-only), `--init` config template, `--doctor` readiness report (config, port names present, calibration files, VR asset → what can start; opens nothing), `--follower wireless` / `wired` / `none` (`none` = leader only, the follower need not be plugged in: one-player VR), `--pair both` (default) / `A` / `B`, `--vr A|B` (+`--vw`) adds the bridge and forces that pair to `vwall`, `--vr2` also starts that pair's follower as a hand-moved second input (ports 8771/8772/8773) and gives the bridge both arms, `--swap-roles` exchanges the machines of a wired pair (the follower machine is the input device, the leader machine follows; the panel's pair name says so), `--grip-ma`, `--dry-run`. Ports: A 8765/8766 (+8769/8443), B 8767/8768 (+8770/8444), panel 8780. | launches `koch-teleop` → moves motors | logs in `work/logs/dual_*.log` |
| `scripts/koch.sh` and `scripts/*.command` | Mac launchers and the launcher of record for VR (the `.command` files start from Finder with a double click): `handshake` (pair A, `--ff-style error`; roles swapped by default since 10/25, `SWAP=0` for the usual direction, `FF_SIGN=-1` flips the grip feedback, `GRIP_MA` caps the following gripper), `vr-leader` (leader by hand, page on 8444/8445), `vr-follower` (follower by hand in its own scene, page on 8443/8446, twin config in `config/solo_F/`), `quest` (re-add the reverse and reopen the page), `all`, `status`, `stop`. `PAIR=A|B`. Creates `config/config.json` from `config/config.fest.json` when missing, resolves ports by USB serial, opens the Mac operator page (`?spectator=1&info=1`), waits for the headset's USB-debugging allow, adds the reverse and reopens it whenever the USB link re-enumerates (10/19). | moves motors, torque off (`vr-follower` at start and stop, `stop`) | serial, UDP, HTTP |
| `koch-panel` | One page for all launched pairs: status chips and 4 graphs per pair, one row of sliders and mode buttons (OFF / gripper / arm / vwall) that go to every pair. | network only, but it commands live teleop sessions | HTTP `--http`, UDP in `--telemetry` (list), out `--ctl-port` (list) |
| `koch-plot` | matplotlib telemetry viewer. | network only | UDP in |
| `koch-calib` | Torque off both arms, hold the same pose by hand, print raw ticks and normalized % per joint with the JSON fix to apply. Reads `config/calibration/`. | torque off (both arms) | reads calibration JSON |
| `koch-host` | **Wireless follower.** Runs on the computer next to the follower (Raspberry Pi 4, or any Windows/Mac/Linux PC with lerobot): receives joint targets over UDP from the Mac's teleop (`--follower-port udp://<host>:9101`), drives lerobot's KochFollower, streams currents/positions/temperature/errors back. Holds the pose after 0.5 s without targets (torque kept), ramps back in after a gap; `--sim` needs no hardware. Same role as lerobot's LeKiwi host. | moves motors | UDP `--listen` (A 9101, B 9102) |
| `koch-bridge` | Serves `src/webxr/`, republishes telemetry as `/state` (every arm; `--arms B,F` with one telemetry/control port per arm), keeps `config/twin.json` (v2: per-arm joints/base, objects with bounce, desk) behind `/config`, forwards `POST /contact {"arm","vwall"}` to that arm's teleop and the twin joint map to it (for the weight FK). `--sim` needs no teleop. | network only, but it commands the leader's virtual wall and weight (clamped by the teleop; dropped after 3 s without a refresh) | HTTP(S) `--port`, UDP in `--telemetry`, out `--ctl-port` |
| `koch-quest` | USB route for the headset: finds adb, `adb reverse tcp:<port> tcp:<port>`, opens `http://localhost:<port>/` in Quest Browser (`--spectator`, `--mirror` = scrcpy, `--wifi` = move adb to Wi-Fi so the headset's USB port can take a 45 W charger, `--check` = status and battery level only, `--wait N` = wait for the USB-debugging allow, `--watch` = keep running and re-add the reverse when the USB link re-enumerates, `--devtools` = also forward Chrome DevTools on 9222). Needs developer mode once (CHECKLIST U0). | none (network only) | adb over USB |
| `koch-scan` | Broadcast-ping an assembled arm at 1 Mbps and 57600 bps (a motor fresh out of the box answers only there); prints ID, model and firmware. `--loop SECONDS` reads positions in a loop and counts failures (comms stability test). | read-only | — |
| `koch-set-id` | `<port> <current-id> <new-id>`: change one motor's ID; refuses unless exactly that motor is on the bus. `--setup --port <port> [--id] [--type]`: lerobot's `setup_motors()` (IDs and baud, interactively). | writes motor config | — |
| `koch-current` | Real-time current/load plots for one or two arms straight from the bus, with per-axis statistics on exit and a CSV under `work/csv/`. Cannot share a port with a running teleop. | **torque off** on every listed ID at start and at exit; `--torque on` energizes instead | CSV out |
| `python -m koch.tools.<name>` | Bench diagnostics promoted from the old `work/tools/`: `preflight` (read-only go/no-go), `register_snapshot`, `reg_diff`, `pos_trace` (read-only), `match_leader` and `fit_twin` (rewrite calibration / twin JSON; no motor writes), `watchdog` (network only), `spawn_detached`, `torque_off` (**torque off**, cleanup after a crash). | read-only except `torque_off` | serial, files |
| `src/webxr/setup_assets.py` | Downloads `three.module.js` (r160) once; the file is git-ignored. `scripts/start.sh check` runs it. | none | network (once) |
| `src/koch/simbus/rehearse.py` | Runs the launcher, teleop, bridge, panel and follower host against simulated servos (`src/koch/simbus/dynamixel_sdk`, a stand-in for the SDK that lerobot loads from `PYTHONPATH`) and checks frame rate, feedback commands, register write order, refused EEPROM writes and torque at exit. Scenarios: `handshake` `swap` `vr` `vr2` `wireless` `mismatch` `dropout` `fest`. See [`src/koch/simbus/README.md`](src/koch/simbus/README.md). | none (no port is opened; fixed localhost ports, so not next to a live session) | logs in `work/rehearsal/` |

## Wireless follower (leader wired to the Mac, follower on its own computer)

```
Mac ── USB ── leader                 handshake table: host PC ── USB ── follower ── 12 V
 koch-teleop --follower-port udp://<host>:9101           koch-host --port <serial> --listen 9101
                     └── own 5 GHz router (Mac, host PC, Quest only) ──┘
```

- The Dynamixel bus stays local on each side; only joint targets (30 fps, with sequence
  numbers) and state go over UDP. A transparent "wireless serial cable" would put the radio
  inside the bus's request/response timeout and fail; this design does not.
- Link watchdog (`koch.link`, a verbatim mirror of `twinarm/domain/link_watchdog.py`): 0.3 s late
  → alert on the panel/VR; 0.5 s → the host holds the pose (torque kept, so the arm does not
  drop); 3 s → the Mac treats it as a dropout and reconnects; on resume the host ramps in
  over 1.5 s. The Mac shows `age_ms` / `rtt_ms` / loss in the telemetry.
- Backup: `koch-launch --follower wired` uses the USB `follower_port` again with the
  same config. A Raspberry Pi is not required — any computer that runs lerobot works.

## Quick start (Mac, from `discovery/koch/`)

```bash
uv sync
uv run koch-teleop --selftest                 # no hardware: control laws
uv run koch-launch --list                     # read-only: ports + USB serials
uv run koch-launch --init                     # → config/config.json, fill it in
uv run koch-launch --pair A --ff gripper      # TEST 2 (staging)
uv run koch-launch --ff gripper --grip-ma 500 # TEST 4: both pairs (default)
# VR is a separate track: one leader, wall + weight. USB route (no venue Wi-Fi): bridge over http + adb reverse
uv run python src/webxr/setup_assets.py
uv run koch-launch --pair B --vr B --vw --vr-http      # page on http://localhost:8444/ in the headset
uv run koch-quest --port 8444                           # other terminal: adb reverse + open Quest Browser
uv run koch-launch --pair B --vr B --vw --vr-http --vr2 # two players: the pair's follower moved by hand
```

Ports are supplied by the user (never guessed); leader and follower ports are not
interchangeable. Every command above that starts a teleop moves motors — run it only when
asked. The full procedure with pass criteria and record fields is in
[docs/CHECKLIST.md](docs/CHECKLIST.md).

## Force-feedback styles (leader arm, XL330-M077)

| `--ff` | Servo mode | Law | Where it comes from |
| --- | --- | --- | --- |
| `gripper` + `spring` | 5 (current-based position), `Goal_Position` = calibrated open end, `Position_P_Gain` 800 | `Goal_Current = floor + gain × EMA(|I_follower| − deadband)`, capped | mock/v0 + branch `rn/fix/gripper-force-feedback` (measured on hardware 2026-09-04) |
| `gripper` + `error` | 0 (current) | current ∝ (leader command − follower position) beyond a deadband, only after the follower has stalled, slew-limited | robotics 2026-08-04 (hardware session S23) |
| `arm` | 0 on elbow/wrist joints | `−gain × EMA(I_follower)` per joint | FACTR-style; not yet verified on hardware |
| `vwall` | 5, `Goal_Position` = wall tick, `Goal_Current` = 0 outside / cap inside, `Position_P_Gain` = object stiffness | host only decides *engaged* (opening ≤ width, with hysteresis); the servo's own loop renders the wall | this folder; robotics `TacitCapture/94_` |
| `vwall --vw` | 0 on `shoulder_lift` + `elbow_flex` | `I = scale × (m·g·lever) / Kt`, lever from the twin's planar FK, capped, EMA; zero unless an object is grasped | this folder; direction must be checked on hardware (CHECKLIST V-w) |
| `vwall --vw` with `--leader-type koch_follower` | 0 on `elbow_flex` (XL330-M288), 16 (PWM) on `shoulder_lift` (XL430-W250, no current control) | elbow as above with Kt 0.354; shoulder `Goal_PWM = pwm_scale × torque / 1.5 N·m × 885`, capped by `--vw-pwm-cap`. Torque is on only while an object is grasped; `Goal_PWM` is zeroed before torque-on (in position mode the register is the PWM limit, 885) and mode 4 + the limit are restored on exit | this folder (`PwmWeightJoints`, write order checked by `--selftest` and `tests/test_pwm_weight_joints.py`); direction and strength must be checked on hardware (CHECKLIST V5) |

## Swapped roles (the handshake held by the follower machine)

Added 2026-10-25 on branch `rn/feat/koch-handshake-swap-roles`, after the grip feedback of the leader's
XL330-M077 felt weak at its current cap: the follower machine's gripper is an XL330-M288 (same motor,
288:1 instead of 77:1), so the same milliamps give about 2.4 times the torque, at the same heating.
`koch-launch --swap-roles` (what `scripts/koch.sh handshake` passes unless `SWAP=0`) exchanges the two
machines of a wired pair:

- the **follower machine** becomes the input device the operator holds: `koch-teleop --leader-type
  koch_follower` with the follower's port and calibration (`HandFollower`: the five arm joints are left
  torque-free so the arm can be moved by hand, which makes it heavy and makes it drop when let go, so
  support it at start and stop; the gripper renders the error-style feedback in current mode);
- the **leader machine** follows and is the arm offered to the visitor: `--follower-type koch_leader`
  wraps lerobot's `KochLeader` in `DrivenLeader` (`src/koch/driven_leader.py`), which enables torque on
  the arm joints after lerobot's configure and writes `Goal_Position` like `KochFollower` does, with the
  same `--max-rel` limiter; `--grip-ma` caps its M077 gripper (the same 500 mA is about 1/2.4 of the
  grip force the M288 gave the visitor);
- the driven arm's **position gains**: with the servo defaults the M077 arm lost to gravity and
  friction on the bench (10/25 18:50: elbow 9 and wrist 12 normalized units behind, stepwise
  motion, slow return), so `DrivenLeader` writes P 1500 / I 0 / D 600 (lerobot's values for the
  follower's elbow) on the five arm joints after lerobot's configure (writing `Operating_Mode`
  resets them) and before torque goes on, and reads them back (`--follower-pgain` /
  `--follower-igain` / `--follower-dgain`, `FOLLOW_P` / `FOLLOW_I` / `FOLLOW_D` in `koch.sh`;
  a negative value leaves the servo default). The teleop then reads one of its arm joints'
  temperature every 2 s: a panel alert at 60 °C, ⛔ at 65 °C (stop and let it cool; the servo
  shuts itself off at 70 °C and the arm drops).

Wired only (`udp://` followers and VR pairs are refused), the config file is unchanged (ports and
calibration ids keep their names; the launcher exchanges them), and the panel is unchanged: the pair's
name on it becomes `A（役割入替: 操作側=フォロワー機／相手側=リーダー機）` through the existing
`--labels` option, and the teleop's alerts name the machine (`リーダー役(フォロワー機)`). The panel's
current graph still multiplies the two shoulder traces by 10 (XL430 load %), which over-reads the M077
shoulders of a swapped pair; the gripper trace is unaffected. **On the arms** (10/25 18:50, first
run): both machines connected and the M077 arm followed, but with the servo's default gains it
lagged as described above; the gains are the fix under test. Still to confirm on the arms
(CHECKLIST TEST 2c): the tracking error and any oscillation or heating with the gains, the direction
of the grip feedback on the M288 (`FF_SIGN=-1` if the gripper is pulled closed instead of pushed
open), and the heaviness of the hand-moved follower machine. A wrist roll that reads −100 on the
panel sits past its one-turn calibration (the shoulder-pan phenomenon in HANDOFF): turn it back one
turn. `./scripts/start.sh rehearse swap` covers the software path. `git switch main` removes all of it.

The laws are pure functions in `src/koch/feedback.py`, a verbatim mirror of
[`twinarm/src/twinarm/domain/gripper_feedback.py`](../../twinarm/src/twinarm/domain/gripper_feedback.py)
(unit-tested there with `mise run test`). This project cannot import the library (separate uv
projects), so `tests/test_mirror.py` compares the two by syntax tree and `koch-teleop --selftest`
runs the mirrored copy on the same vectors.

## VR page (`src/webxr/index.html`)

- One twin per arm from `config/twin.json` v2 (`arms: {B: {...}, F: {...}}`; a v1 file with
  top-level `joints`/`base` is migrated to the first arm). Objects (`objects`, in the first arm's
  frame) carry touch width, `p_gain`, `cap_ma`, `release`, `mass_g`, `bounce`, colour, kind, spot;
  `scene` holds the desk size, sound and the AR ghost mode.
- Physics: a grasped object follows the fingertips; on release it leaves with the fingertip
  velocity of the last ~110 ms (capped at 5 m/s), falls under gravity with per-object drag, bounces
  on the desk / floor with its `bounce` and friction, collides with the other objects, and sleeps
  when slow. Impacts make a synthesized sound (S toggles). **R** / 「配置リセット」 puts every
  object back on its spot.
- Alignment in AR (10/19): Quest Browser draws no DOM overlay, so grab the twin within 35 cm and move
  it (translation + yaw); letting go for 1.5 s saves it and registers a spatial anchor. The older
  point-based **C** / 「位置合わせ」 remains for the desktop and for `koch4.startCalib()` over DevTools.
- **G** cycles how the twin is drawn in AR (solid / half-transparent / fingertip ring only).
  **V** toggles the desktop view between the first-person preview (default, for the monitor) and
  the orbit view. **Tab** selects the arm for keys 1/2/3 (force an object), 0 (clear), E (editor).
- **E** / 「編集」 opens the editor: per-arm joint span / offset / sign and base, per-object
  width / gain / cap / mass / bounce and 「ここに置く」, desk size, ghost mode, sound. 「保存」
  writes the file and sends each arm's joint map to its teleop.
- `?spectator=1&info=1` is the Mac operator page: the same scene without sending contacts, with the
  text the headset cannot show (MQDH Cast or scrcpy show the headset's own view instead).
- Alerts from every teleop (prefixed `[B]` / `[F]` with two arms) appear as a red banner in the
  page and as red chips on the panel; CHECKLIST.md lists what to do for each.
- `window.koch4` exposes objects / arms / config for console checks (the JS globals and the
  `koch4.anchor.*` storage keys keep their old name: renaming them would orphan the anchors already
  saved on the headsets).

## Graduating to `twinarm/`

What can move into the library is what needs no arm attached: the control laws (already in
`domain/gripper_feedback.py`), the config schema and port resolution (`infrastructure/`), the
virtual-wall/weight state machines and the `/stream`–`/ctl` contract (`api/features/telemetry`,
`control`). What stays here is the bus-driving frame loop and anything that only makes sense
with hardware. Move a piece only after it has been exercised on the arms and recorded in
docs/CHECKLIST.md; write the test first (TDD rule in `../../.claude/rules/common/testing.md`).

## Known limits

- `src/koch/teleop.py` is about 1,500 lines, above the 800-line guideline in
  `.claude/rules/common/coding-style.md`. Its `main()` keeps the hardware-tested frame-loop shape of
  `mock/v0/koch_teleop_plus.py` on purpose; the pure parts (laws, link, motors, control, telemetry,
  remote follower) have been extracted, the loop has not. Split it only after the loop has been
  exercised on the arms again.
- `ruff check`, `ruff format --check`, `ty check` and the offline pytest suite pass (`mise run check`);
  the rehearsal's 25 fps checks sit a couple of fps below what a busy Mac delivers, so run it alone.
- Line coverage is about 23 % overall: the extracted and ported modules sit at 88–100 %, but the
  entry points' `main()` functions (`teleop`, `follower_host`, `launch`, `bridge`, `panel`, the
  viewers, the tools) are only exercised by the rehearsal and by hand. `launch.py`'s pure helpers
  and `follower_host`'s hold/ramp loop are the next pieces to lift and test.
- The digital twin is the primitive model of robotics `84_`; joint spans are nominal until the
  editor's offsets are set against the real arm. Object widths are in lerobot's normalized
  0–100 gripper units; the weight lever arms use the twin's link lengths.
- Checked in a desktop browser (inline mode, `--sim`), with the launcher's `--dry-run`, and end to
  end against simulated servos on lerobot 0.6.1 (`src/koch/simbus/`); the second player and the wireless
  follower have not run on the arms, the VR alignment ran on a Quest Pro on 10/19.
- When started by the launcher (output in a log file), a teleop whose calibration file differs
  from what the servos hold stops with the reason instead of waiting on lerobot's question; run
  `koch-teleop` directly in a terminal to answer it.

## Migrating a Mac from `discovery/koch4/` (temporary, until after the fest)

`git pull` renames the tracked files but leaves the git-ignored ones behind in the old folder.
Run `./scripts/start.sh check` (that is what `scripts/0_最初の確認.command` runs) **before** any other
`.command`: those create a fresh `config/config.json` from the fest file, after which the old
per-Mac config is only warned about.
`./scripts/start.sh check` moves the files once: `koch4/config/koch4_config.json` → `config/config.json`,
`koch4/webxr/three.module.js` → `src/webxr/`, `koch4/work/` → `work/`, then removes the empty
`koch4/`. The venv is new (`discovery/koch/.venv`); `uv sync` reuses the download cache, and
`discovery/.venv` can be deleted. Remove `migrate_from_koch4` from `scripts/start.sh` and this section
after the fest.

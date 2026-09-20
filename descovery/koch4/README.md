# koch4 — two Koch pairs (four arms), dedicated config/work folders, VR virtual wall

Successor of the `mock/v0/` scripts for the Learning Fest setup (2026-10-26/27): two
leader/follower pairs run side by side, everything machine-specific lives in this folder,
and the leader gripper can "grasp" a virtual object seen in a Quest Pro. Part of
[descovery](../README.md); the repository's hardware safety rules apply to every script here.

## Layout

```
koch4/
├── koch4_teleop.py        one pair: teleop + telemetry + leader force feedback (3 styles)
├── koch4_dual_launch.py   start pair A / B / both as parallel processes (fixed ports, logs)
├── koch4_web_panel.py     browser panel (copy of the robotics panel: --http/--cams/--no-browser)
├── koch4_live_plot.py     matplotlib telemetry viewer (unchanged copy of mock/v0)
├── koch4_vr_bridge.py     serves webxr/ to the headset, relays contacts to the teleop
├── webxr/index.html       three.js page: digital twin + virtual objects (+ setup_assets.py)
├── config/                koch4_config.json (git-ignored; see .example) + calibration/
│   └── calibration/koch_follower/<id>.json, koch_leader/<id>.json   ← lerobot calibration
├── work/                  logs/ csv/ _certs/  (git-ignored)
└── CHECKLIST.md           Mac test procedure TEST 0–6 + VR V0–V3 with record fields
```

`--config-dir` / `--work-dir` (defaults: the two folders above) are passed to every process by
the launcher. lerobot's `calibration_dir` is set explicitly, so calibration JSON is read and
written under `config/calibration/` instead of `~/.cache/huggingface/lerobot/calibration/`.
Copy the existing files there (`--lerobot-cache` keeps the old location if you prefer).

## Script inventory and hardware risk

Risk classes are the ones defined in [`../README.md`](../README.md).

| Script | Purpose | Hardware risk | Extra I/O |
| ------ | ------- | ------------- | --------- |
| `koch4_teleop.py` | One pair. `--ff gripper --ff-style spring` (default; the 2026-09-04 fixes: `Position_P_Gain` 800 written after the mode, anchor inherited across reconnects, anchor at the calibrated open end) / `--ff-style error` (error reflection, mode 0) / `--ff arm` (arm joints, untested on hardware) / `--ff vwall` (virtual wall, follower optional: `--follower-port none`). `--selftest` runs the control laws with no hardware. | moves motors; force feedback puts leader joints into current control; `vwall` holds the leader gripper in current-based position mode | UDP telemetry out (`--viz-port`, comma list), control in (`--ctl-port`), CSV in `work/csv/` |
| `koch4_dual_launch.py` | `--list` ports+serials (read-only), `--init` config template, `--pair A|B|both`, `--vr A|B` adds the bridge and forces that pair to `vwall`, `--dry-run` prints the commands. Ports: A 8765/8766/8780/8443, B 8767/8768/8781/8444. | launches `koch4_teleop.py` → moves motors | logs in `work/logs/dual_<pair>_*.log` |
| `koch4_web_panel.py` | Stdlib browser panel with sliders, mode switch, resync, stop, optional camera tiles. | network only, but it commands a live teleop session | HTTP `--http`, UDP in `--telemetry`, out `--ctl-port` |
| `koch4_live_plot.py` | matplotlib telemetry viewer. | network only | UDP in |
| `koch4_vr_bridge.py` | Serves `webxr/`, republishes telemetry as `/state`, forwards `POST /contact` to the teleop as `{"vwall": ...}`. `--sim` needs no teleop. | network only, but it commands the leader gripper's virtual wall (the teleop clamps every field and drops the wall after 3 s without a refresh) | HTTP(S) `--port`, UDP in `--telemetry`, out `--ctl-port` |
| `webxr/setup_assets.py` | Downloads `three.module.js` (r160) once; the file is git-ignored. | none | network (once) |

## Quick start (Mac, from `descovery/`)

```bash
uv sync
uv run python koch4/koch4_teleop.py --selftest          # no hardware: control laws
uv run python koch4/koch4_dual_launch.py --list         # read-only: ports + USB serials
uv run python koch4/koch4_dual_launch.py --init         # → config/koch4_config.json, fill it in
uv run python koch4/koch4_dual_launch.py --pair A --ff gripper        # TEST 2
uv run python koch4/koch4_dual_launch.py --pair both --ff gripper     # TEST 4
# VR: pair B leader-only (follower_port "none" in the config), page on https://<mac-ip>:8444/
python koch4/webxr/setup_assets.py
uv run python koch4/koch4_dual_launch.py --pair both --vr B
```

Ports are supplied by the user (never guessed); leader and follower ports are not
interchangeable. Every command above that starts a teleop moves motors — run it only when
asked. The full procedure with pass criteria and record fields is in [CHECKLIST.md](CHECKLIST.md).

## Force-feedback styles (leader gripper, XL330-M077)

| `--ff` | Servo mode | Law | Where it comes from |
| --- | --- | --- | --- |
| `gripper` + `spring` | 5 (current-based position), `Goal_Position` = calibrated open end, `Position_P_Gain` 800 | `Goal_Current = floor + gain × EMA(|I_follower| − deadband)`, capped | mock/v0 + branch `rn/fix/gripper-force-feedback` (measured on hardware 2026-09-04) |
| `gripper` + `error` | 0 (current) | current ∝ (leader command − follower position) beyond a deadband, only after the follower has stalled, slew-limited | robotics 2026-08-04 (hardware session S23) |
| `arm` | 0 on elbow/wrist joints | `−gain × EMA(I_follower)` per joint | FACTR-style; not yet verified on hardware |
| `vwall` | 5, `Goal_Position` = wall tick, `Goal_Current` = 0 outside / cap inside, `Position_P_Gain` = object stiffness | host only decides *engaged* (opening ≤ width, with hysteresis); the servo's own loop renders the wall | this folder; see robotics `TacitCapture/94_` for the rationale |

The laws are pure functions mirrored from
[`twinarm/src/twinarm/domain/gripper_feedback.py`](../../twinarm/src/twinarm/domain/gripper_feedback.py),
which is unit-tested (`mise run test` in `twinarm/`). descovery scripts may not import the
library (separate uv projects, standalone-script rule), so the two copies are kept in sync by
hand; `koch4_teleop.py --selftest` checks the mirrored copy against the same vectors.

## Graduating to `twinarm/`

What can move into the library is what needs no arm attached: the control laws (already in
`domain/gripper_feedback.py`), the config schema and port resolution (`infrastructure/`), the
virtual-wall state machine and the `/stream`–`/ctl` contract (`api/features/telemetry`,
`control`). What stays here is the bus-driving frame loop and anything that only makes sense
with hardware. Move a piece only after it has been exercised on the arms and recorded in
CHECKLIST.md; write the test first (TDD rule in `../../.claude/rules/common/testing.md`).

## Known limits

- `koch4_web_panel.py` / `koch4_live_plot.py` are copies of the v0 scripts and carry their
  lint findings; the four new scripts pass `ruff check` and `ruff format --check`, and `ty`
  reports only the imports that need the hardware environment (`lerobot`, `serial`).
- `koch4_teleop.py` is ~1,160 lines after `ruff format`, above the 800-line guideline in
  `.claude/rules/common/coding-style.md`. It keeps the hardware-tested frame-loop shape of
  `mock/v0/koch_teleop_plus.py` plus the mirrored laws on purpose; split it only after the
  loop has been exercised on the arms (the laws already live in `twinarm/domain`).
- The digital twin is the primitive model of robotics `84_`; joint spans for the display are
  nominal, not calibrated. Object widths are in lerobot's normalized 0–100 gripper units.
- The WebXR page was checked in a desktop browser (inline mode, `--sim`); AR/VR mode needs the
  headset (CHECKLIST V1).

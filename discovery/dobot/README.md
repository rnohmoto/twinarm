# dobot — Magician pick-and-place sandbox

Runnable port of `robotics/scripts/magician_pnp/` (skeleton v1, 2026-09-11) for the Dobot
Magician (original, 4-axis, USB serial via CP210x), extended on 2026-09-20 (v2) with an object
model (colour + shape), a setup wizard, a browser panel and a demo runner. Pipeline: overhead UVC
camera → HSV + shape detection → homography (pixels → robot XY) → `pydobot` pick-and-place, with
optional voice (faster-whisper) and LLM intent parsing (Claude tool use; a rule parser is the
offline fallback).

Part of the TwinArm monorepo; see [`../README.md`](../README.md) for the repository layout. Design
and purchasing rationale live in the planning repo: `robotics/TacitCapture/91_Magician_P&P構成…`
(architecture, tests M0–M7, safety) and `93_Magicianカメラ…` (camera compatibility, buy list,
object sizes).

**Manuals (Japanese, with photo slots):** [`manual/index.html`](manual/index.html) first-time setup ·
[`manual/connection.html`](manual/connection.html) wiring and ports (pump → SW1/GP1, Temp pin, what the
Magician can and cannot report) · [`manual/purchase.html`](manual/purchase.html) purchase list with prices.

## Status

Offline tests pass with no hardware (`uv run pytest`). `dobot-demo --dry-run` runs the whole
pipeline against a synthetic frame and serves the browser panel. On 2026-10-05 the Magician was
connected to the Mac for the first time: it answers on a CH9102 USB-serial port (not CP210x) as
serial number `DT1426040100`, firmware 4.0.6, and accepts the suction command (`dobot-suction pulse`: read-back
ON, then OFF). On 2026-10-15 (fest Mac D bench, C922 overhead) the user confirmed on the real arm: `dobot-jog` step
moves, the workspace guard refusing x > 300, Esc stopping a held move, the cup holding a wood block;
`dobot-demo --robot magician` homed at start, and the stop lane (往復（試運転） → 非常停止 → 停止解除 →
ホーム) was accepted within the same second in the panel events. On 2026-10-16 the
bench was completed end to end: C922 locked through Logi Options+, eye-to-hand calibration with `dobot-calibrate --drive`
(RMS 1.91 mm), zones placed on the camera image with `dobot-zones`, pick/place heights measured, and red blocks
picked and placed into both trays by text command on the `magician` backend. The resulting `config.json` is
committed; `assets/homography.json` is per bench (git-ignored). **Start from [`docs/HANDOFF.md`](docs/HANDOFF.md)** (Japanese)
to run or redo the setup. On 2026-10-19 the bench was moved and recalibrated (`--drive`, RMS 3.1 mm), motion
was sped up (one pick-and-place 20 s → 7 s), the camera stream got its own thread, and the operator side was
finished: `dobot-launcher` (start/stop/adjust page), `scripts/setup.command` for a fresh Mac, and the documents under
[`docs/`](docs/) (要件定義書・操作マニュアル・導入手順・作業記録). The 20-cycle run is still open.

## Quick start

Bench session with Claude Code on the fest Mac (role D): paste the prompt in
[`../../docs/fest_start_prompts.md`](../../docs/fest_start_prompts.md) section 4 D. It walks M0 (link-up and
stop), M1/M2 (camera lock and calibration), M3 (20 cycles), then voice, and asks you for the port.

Operator path (no terminal knowledge needed): open the `scripts/` folder and double-click
**`setup.command`** once on a new Mac (installs uv, the dependencies, the Whisper model, runs the offline
tests), then **`start.command`** every day: it opens the launcher
page (http://127.0.0.1:8789) with start / stop / restart, recalibrate, zone placement, the settings form and the log.
The manuals are in [`docs/`](docs/): [要件定義書](docs/requirements.md), [操作マニュアル](docs/operation-manual.md),
[導入手順](docs/setup-manual.md), [作業記録](docs/worklog-2026-10.md).

```bash
cd dobot
./scripts/start.sh launcher   # the same launcher page from a terminal (--dry: practice without hardware)
./scripts/start.sh            # Mac/Linux: wizard if not configured yet, otherwise the demo panel directly (.\scripts\start.bat on Windows)
./scripts/start.sh dry        # no hardware: synthetic camera + recording robot, panel at http://127.0.0.1:8790
./scripts/start.sh check      # read-only: cameras, candidate ports, offline tests
./scripts/start.sh suction --port /dev/cu.usbserial-XXXX   # suction ON/OFF panel only, at http://127.0.0.1:8791
```

Suction alone (for filming; the arm does not move, homing is not needed):

```bash
uv run dobot-suction --port /dev/cu.usbserial-XXXX panel    # two big buttons; Space toggles, O = on, F = off
uv run dobot-suction --port /dev/cu.usbserial-XXXX on       # also: off / status / pulse --seconds 3
```

The panel switches the pump off by itself after `--max-on-s` (300 s; 0 disables) and on exit.

Controller for filming (**moves the arm**: forward/back, left/right, up/down in fixed steps, plus suction):

```bash
./scripts/start.sh jog --port /dev/cu.usbserial-XXXX   # or: uv run dobot-jog --port ... ; --dry for no hardware
```

One press is one straight step of 1/5/10/20 mm; holding a button or key repeats steps one at a time, so
the arm never travels more than one step after release. Keys: arrows = forward/back/left/right, W/S =
up/down, Space = suction, Esc = stop. Targets go through the workspace guard in `config.json` and a
joint-range check (J2 0–85°, J3 −10–95°, computed from the pose; the model matches the angles the arm
reports); `--speed` (default 30 %) sets the PTP velocity ratio. Directions are in the robot frame and
assume the arm was homed after power-on. If the firmware still stops a move with a range alarm (for
example 0x22, inverse-kinematics limit), the panel stops the queue, clears the alarm and says so; other
alarms stay until "アラーム解除" is pressed. Every step is logged to `logs/jog_YYYYMMDD.jsonl` (pose,
joint angles, target, result, alarm codes).

Or step by step:

```bash
uv sync                                   # first time; creates .venv with Python 3.13
uv run pytest                             # offline tests — must pass before touching hardware
uv run dobot-demo --dry-run --no-llm  # synthetic camera + recording robot, panel at http://127.0.0.1:8790
```

With hardware, the wizard walks through the first-time setup and writes `config.json` +
`assets/homography.json` (every step is resumable with `--from N`; the arm moves only after you
answer `y`):

```bash
uv run dobot-setup             # 1 camera · 2 robot · 3 markers · 4 calibrate · 5 zones · 6 heights · 7 detect · 8 smoke test
uv run dobot-demo --config src/config.json --robot pydobot --port /dev/tty.usbserial-XXXX --camera-index 0 --no-llm
uv run dobot-demo --config src/config.json --robot magician --port /dev/tty.usbserial-XXXX --camera-index 0 --no-llm   # direct protocol + stop lane
```

`--robot magician` (`robot_magician.py`) talks the Dobot protocol directly instead of pydobot: the e-stop button sets
the stop event on the HTTP thread at once, and the worker thread, polling the queue index inside its own motion wait,
sends ForceStop → Clear → pump-off itself. Motion speed is `robot.speed_pct` (percent, default 30). First run on the
real arm on 2026-10-15 (home at start, e-stop during 往復 accepted at once); the two-stage suction
release still needs confirming with an object.

Then open http://127.0.0.1:8790 (camera with detection boxes, the voice card, what was heard → intent → reply,
robot state, buttons: send text / home / tidy / e-stop / resume / attract loop).

Voice UI (`voice_front.py`, robotics T13 Step 2/3/5): hold the big mic button or the Space key while you speak;
on release the panel shows what was heard and the interpretation, then runs it after `demo.confirm_auto_run_s`
(default 2 s; Enter runs at once, Esc cancels). An ambiguous utterance (negation, two objects, "that one",
"reset") is shown with 「これですか？」 and waits for Enter/Esc. Stop words (「止まって」「待って」 …) trigger the
e-stop lane at once. Needs the `voice` extras (faster-whisper + sounddevice); without a mic, type into the
聞き取り box and press 「文字で試す」 to rehearse the same flow in `--dry-run`.

Read-only checks used inside the wizard are also standalone:

```bash
uv run dobot-check-camera --list ; uv run dobot-check-camera --index 0
uv run dobot-check-robot --list  ; uv run dobot-check-robot --port /dev/tty.usbserial-XXXX
```

## Demo modes (`dobot-demo`)

| Mode | What happens | Ends when |
| ---- | ------------ | --------- |
| Dialog (default) | Visitor says "〜を右に置いて" (text box, or the mic button = push-to-talk on the PC) → intent (Claude tool use, or the rule parser offline) → pick and place → spoken/printed reply. "片付けて" returns everything to the start pad (`start` zone slots) for the next visitor. | — |
| Loop on request ("ループして", "3回繰り返して", the panel button) | Runs a bounded number of short cycles (default 3; each = carry `attract_count` objects to `attract_zone`, pause, tidy back, ≈20 s) and then **stops**. | After the count, or earlier on any utterance/button, e-stop, the hourly cap, or the thermal budget. |
| Attract (`--attract` or the panel button; default OFF) | After `demo.idle_s` (120 s) without input, runs one short cycle, rests `pause_s` (45 s), repeats. | Any command or utterance switches back to dialog; e-stop disables it. |
| `--once "text"` | Handle one utterance and exit (smoke test). | immediately |

**Thermal budget** (`DemoConfig.motion_budget_s` / `budget_window_s` / `cooldown_s`): the runner adds up the
seconds the arm actually moved (utterances, tidy, loops); once motion exceeds 240 s in any 600 s window,
automatic motion (attract / loop) pauses for at least 120 s. Dialog commands still run so the explainer can
decide; the panel shows the duty (%) and the remaining rest. `max_cycles_per_hour` (20) caps loops as well.
Magician steppers warm up under continuous motion — these defaults keep the duty near 40%; tune on site.

Objects (`config.json` → `objects[]`): red/green/blue/yellow cubes (colour, aspect ≤ 1.6), `ball`
(orange table-tennis ball: hue + circularity ≥ 0.82), `eraser` (MONO blue band: elongated), `golf_ball`
(white: low saturation + circularity ≥ 0.85, which rejects ArUco's white squares). Each object carries
its own `z_pick`. Zones have a radius (objects already inside are not moved again) and optional
`slots` (place positions, first free one is used).

## Script inventory

Risk classes: **read-only** (no motion), **moves arm** (commands motion), **camera only**.

| Script | Purpose | Hardware risk |
| ------ | ------- | ------------- |
| `dobot-launcher` (`launcher.py`) | Operator page on port 8789: starts `demo.py` as a child process, stops it through the panel's `quit` (the arm homes first), restarts, opens the `--drive` calibration in a Terminal window, opens `zones_view.py`, takes a camera snapshot while the demo is stopped, edits the common settings in `config.json` with validation and a backup, tails the demo log. | **moves arm** only through its start / restart / calibrate buttons (each labelled) |
| `scripts/setup.sh` / `scripts/setup.command` / `scripts/start.command` | One-time preparation of a fresh Mac (uv → `uv sync --extra voice --extra llm` → Whisper model → offline tests → port and calibration check) and the double-click launcher. | none |
| `dobot-setup` (`setup_wizard.py`) | Guided first-time setup: camera + exposure lock, port + homing check, ArUco markers, calibration, zone teaching (hand-guide with the forearm unlock key, or click on the image with `zones_view.py`), per-object `z_pick`, detection tuning, smoke test. Saves `config.json` after each step. | reads only by default; **moves arm** only after `y` (home / smoke test) |
| `dobot-demo` (`demo.py`) | Demo runner: browser panel + dialog mode + attract loop. Pre-loads the Whisper model at start (the first load downloads ~480 MB; the panel says 準備しています / できました). The mic follows `asr.engine` in `config.json`; `--keyboard` switches to text input only (until 2026-10-16 the demo always forced text input, so the panel mic never worked). One worker thread owns the robot; a separate thread grabs frames, detects and publishes the MJPEG stream at `stream_fps` so the picture keeps moving while the arm does (`panel_mirror` flips the shown picture only); the panel only queues commands (the e-stop button additionally sets the stop event on the HTTP thread). | **moves arm** with `--robot pydobot` / `--robot magician`; `--dry-run` never does |
| `panel_web.py` | stdlib HTTP server: `/` page, `/stream` MJPEG, `/status` JSON, `POST /cmd`. | none |
| `dobot-check-camera` (`check_camera.py`) | Enumerate cameras; open one, lock exposure/WB and autofocus (read back), measure brightness jitter, save a snapshot to `logs/`. | camera only |
| `dobot-suction` (`suction.py`) | Suction pump ON/OFF only: `on` / `off` / `status` / `pulse` commands and a two-button browser panel (port 8791). Talks the Dobot protocol directly (immediate command, no queue, no pydobot). | **runs the pump**; never moves the arm; `--dry` touches nothing |
| `dobot-jog` (`jog.py`) | Filming controller: browser panel (port 8791) with step moves on X/Y/Z, stop, alarm clear and suction ON/OFF. Reuses `suction.py`'s worker and panel; targets pass the workspace guard. | **moves arm** on every button press; `--dry` never does |
| `dobot-check-robot` (`check_robot.py`) | List candidate ports (`--list`); connect and print pose; `--home` / `--round-trip` move the arm; `--dry` rehearses without hardware. | read-only by default; **moves arm** with flags |
| `dobot-calibrate` (`calibrate.py`) | Eye-to-hand calibration: print ArUco markers (`--make-markers`) or use an object as the marker (`--object blue_cube`), then fit pixels→robot XY into `assets/homography.json` while *you* move the arm. Each recorded point is saved at once to `assets/homography.pairs.json`, so a crash loses nothing: rerun to continue, `--fresh` to start over. `--drive --object blue_cube` instead lets the **robot** visit a 3×3 grid, stop just above the block height, wait for you to put the block under the cup, park aside and read the camera — pairs are then commanded-position → pixel, which removes the hand-teaching bias (the 2026-10-16 picks aimed a few mm outward). | read-only; **moves arm** with `--drive` |
| `dobot-zones` (`zones_view.py`) | Zone placement on the camera image (needs the calibration): draws the arm workspace and every zone in `config.json` over the live frame; pick a zone with `1`–`9`/Tab, click where it should be and `s` saves (`config.bak_*` kept). Clicks outside the arm's workspace are refused. Replaces the hand-guided step 5 of the wizard; `--image file.jpg` previews on a still. | camera only; never connects to the robot |
| `dobot-pnp` (`main.py`) | CLI app (OpenCV window): `--tune` (camera only), `--dry-run`, or live with `--robot pydobot`. `demo.py` reuses its `build()`. | **moves arm** when `--robot pydobot` |
| `robot_dobot.py` | pydobot wrapper with workspace guard (AABB + reach annulus), emergency stop, dry-run robot; refuses to guess a port. | **moves arm** with `--live` |
| `robot_magician.py` | Direct-protocol backend (`--robot magician`, built on `jog.py`/`suction.py`): queue-index motion wait that watches the e-stop event, ForceStop → Clear → pump-off halt lane, two-stage suction release (both immediate commands), alarm read before and during motion; joint-interpolated travel (MOVJ) with straight-line descents (`travel_mode`). No pydobot. | **moves arm** with `--robot magician` |
| `camera.py` / `detect.py` / `planner.py` | UVC/RealSense/file camera with exposure lock and read-back; HSV + shape detection; TaskExecutor (observe → select → pick and place, tidy up; placement avoids every detected object using its size in mm and refuses when the zone is full). | camera / via robot |
| `rule_parser.py` / `llm_agent.py` | Japanese rule parser (offline; flags ambiguous utterances and takes the last-named object after a negation; wider stop words; home before tidy) / Claude tool-use agent (enum-only arguments, no coordinates from the LLM; tools: list_objects, pick_and_place, tidy_up, go_home, stop). | none |
| `asr.py` / `tts.py` | Hold-to-talk recorder (ring buffer with pre/post-roll, no Enter) + faster-whisper with per-segment confidence and a hallucination/confidence gate / VOICEVOX-say-SAPI. Optional extras. | none |
| `voice_front.py` | Voice dialog state machine on its own ASR thread: record → transcribe → rule parse → show heard text + interpretation → countdown run, 「これですか？」 for ambiguous ones, stop words → e-stop lane. Hands only confirmed intents to the worker. | none (serial untouched) |
| `dobot-config` (`config.py`) | Dataclasses + JSON (`uv run dobot-config src/config.json` writes the defaults). | none |
| `src/dobot/tests/` | Offline tests: detection incl. shapes, homography, parsing, guards, executor, panel HTTP, demo runner, wizard helpers, stop lane (fake arm + dry-run panel e-stop), voice UI (fake mic stream, transcript gate, 99-utterance parser regression in `src/dobot/tests/data/`, dialog state machine, panel round trip). | none |

## Layout

```
dobot/
  AGENTS.md  README.md  pyproject.toml
  scripts/        start.sh  start.command  start.bat  setup.sh  setup.command
  src/
    config.json   the bench configuration the operator edits (committed)
    dobot/        the package; every console script in pyproject.toml points into it
      paths.py         config.json / assets/ / logs/ resolved from the project, not the cwd
      launcher.py  setup_wizard.py  demo.py  panel_web.py  main.py
      check_camera.py  check_robot.py  calibrate.py  suction.py  jog.py  zones_view.py
      camera.py  detect.py  planner.py  robot_dobot.py  robot_magician.py
      rule_parser.py  llm_agent.py  asr.py  tts.py  voice_front.py  config.py
      tests/           offline tests (pytest), with data/ for the parser regression
  docs/            HANDOFF.md + 要件定義書・操作マニュアル・導入手順・作業記録
  manual/          first-time setup manual (index.html) + img/ photo slots
  assets/          homography.json, printed markers (git-ignored)
  logs/            snapshots, JSONL run logs (git-ignored)
```

`config.json` sits in `src/`, and `assets/` and `logs/` beside `pyproject.toml`; the code reaches
all three through `dobot.paths`, so a console script works from any directory.

## Model caveat

`robot_dobot.PydobotRobot` targets the original Magician. Magician Lite mostly works with pydobot
too (community reports); Magician E6 is TCP/IP and needs a different adapter (four methods on
`RobotBase`). Identify the model first: 4 axes + built-in controller + USB = original; a separate
"Magic Box" = Lite; 6 axes + Ethernet = E6.

## Optional extras

```bash
uv sync --extra llm      # anthropic (Claude tool use); set ANTHROPIC_API_KEY
uv sync --extra voice    # faster-whisper + sounddevice (push-to-talk)
```

`./scripts/start.sh` / `.\scripts\start.bat` run `uv sync --inexact`, so extras installed this way stay in `.venv`
(a plain `uv sync` removes them again).

LLM defaults (`config.json` → `llm`): `claude-sonnet-5`, adaptive thinking, `effort: low`, five strict
enum-only tools. Switch to `claude-opus-5` by editing `llm.model`; `claude-haiku-4-5` would need the call in
`llm_agent.py` changed (no `effort` / adaptive thinking). Without a key or network the rule parser takes over.

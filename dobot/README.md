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

Offline tests pass with no hardware (`uv run pytest`, 23 tests). `demo.py --dry-run` runs the whole
pipeline against a synthetic frame and serves the browser panel. Real-arm and real-camera steps have
**not** been run yet: the Magician is on hand but not connected, and the camera is not bought.

## Quick start

```bash
cd dobot
./start.sh            # Mac/Linux: wizard if not configured yet, otherwise the demo panel (.\start.bat on Windows)
./start.sh dry        # no hardware: synthetic camera + recording robot, panel at http://127.0.0.1:8790
./start.sh check      # read-only: cameras, candidate ports, offline tests
```

Or step by step:

```bash
uv sync                                   # first time; creates .venv with Python 3.13
uv run pytest                             # offline tests — must pass before touching hardware
uv run python demo.py --dry-run --no-llm  # synthetic camera + recording robot, panel at http://127.0.0.1:8790
```

With hardware, the wizard walks through the first-time setup and writes `config.json` +
`assets/homography.json` (every step is resumable with `--from N`; the arm moves only after you
answer `y`):

```bash
uv run python setup_wizard.py             # 1 camera · 2 robot · 3 markers · 4 calibrate · 5 zones · 6 heights · 7 detect · 8 smoke test
uv run python demo.py --config config.json --robot pydobot --port /dev/tty.usbserial-XXXX --camera-index 0 --no-llm
```

Then open http://127.0.0.1:8790 (camera with detection boxes, what was heard → intent → reply,
robot state, buttons: send text / mic / home / tidy / e-stop / resume / attract loop).

Read-only checks used inside the wizard are also standalone:

```bash
uv run python check_camera.py --list ; uv run python check_camera.py --index 0
uv run python check_robot.py --list  ; uv run python check_robot.py --port /dev/tty.usbserial-XXXX
```

## Demo modes (`demo.py`)

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
| `setup_wizard.py` | Guided first-time setup: camera + exposure lock, port + homing check, ArUco markers, calibration, zone teaching (hand-guide with the forearm unlock key), per-object `z_pick`, detection tuning, smoke test. Saves `config.json` after each step. | reads only by default; **moves arm** only after `y` (home / smoke test) |
| `demo.py` | Demo runner: browser panel + dialog mode + attract loop. One worker thread owns camera and robot; the panel only queues commands. | **moves arm** with `--robot pydobot`; `--dry-run` never does |
| `panel_web.py` | stdlib HTTP server: `/` page, `/stream` MJPEG, `/status` JSON, `POST /cmd`. | none |
| `check_camera.py` | Enumerate cameras; open one, lock exposure/WB, measure brightness jitter, save a snapshot to `logs/`. | camera only |
| `check_robot.py` | List candidate ports (`--list`); connect and print pose; `--home` / `--round-trip` move the arm; `--dry` rehearses without hardware. | read-only by default; **moves arm** with flags |
| `calibrate.py` | Eye-to-hand calibration: print ArUco markers (`--make-markers`), then fit pixels→robot XY into `assets/homography.json` while *you* jog the arm. | read-only |
| `main.py` | CLI app (OpenCV window): `--tune` (camera only), `--dry-run`, or live with `--robot pydobot`. `demo.py` reuses its `build()`. | **moves arm** when `--robot pydobot` |
| `robot_dobot.py` | pydobot wrapper with workspace guard (AABB + reach annulus), emergency stop, dry-run robot; refuses to guess a port. | **moves arm** with `--live` |
| `camera.py` / `detect.py` / `planner.py` | UVC/RealSense/file camera with exposure lock and read-back; HSV + shape detection; TaskExecutor (observe → select → pick and place, tidy up, zone slots). | camera / via robot |
| `rule_parser.py` / `llm_agent.py` | Japanese rule parser (offline) / Claude tool-use agent (enum-only arguments, no coordinates from the LLM; tools: list_objects, pick_and_place, tidy_up, go_home, stop). | none |
| `asr.py` / `tts.py` | Push-to-talk faster-whisper / VOICEVOX-say-SAPI. Optional extras. | none |
| `config.py` | Dataclasses + JSON (`python config.py config.json` writes the defaults). | none |
| `tests/` | Offline tests: detection incl. shapes, homography, parsing, guards, executor, panel HTTP, demo runner, wizard helpers. | none |

## Layout

```
dobot/
  AGENTS.md  README.md  pyproject.toml  config.json
  setup_wizard.py  demo.py  panel_web.py  main.py  check_camera.py  check_robot.py  calibrate.py
  camera.py  detect.py  planner.py  robot_dobot.py  rule_parser.py  llm_agent.py  asr.py  tts.py  config.py
  tests/           offline tests (pytest)
  manual/          first-time setup manual (index.html) + img/ photo slots
  assets/          homography.json, printed markers (git-ignored)
  logs/            snapshots, JSONL run logs (git-ignored)
```

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

LLM defaults (`config.json` → `llm`): `claude-sonnet-5`, adaptive thinking, `effort: low`, five strict
enum-only tools. Switch to `claude-opus-5` by editing `llm.model`; `claude-haiku-4-5` would need the call in
`llm_agent.py` changed (no `effort` / adaptive thinking). Without a key or network the rule parser takes over.

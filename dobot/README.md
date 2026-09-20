# dobot — Magician pick-and-place sandbox

Runnable port of `robotics/scripts/magician_pnp/` (skeleton v1, 2026-09-11) for the Dobot
Magician (original, 4-axis, USB serial via CP210x). Pipeline: overhead UVC camera → HSV colour
detection → homography (pixels → robot XY) → `pydobot` pick-and-place, with optional voice
(faster-whisper) and LLM intent parsing (Claude tool use; a rule parser is the offline fallback).

Part of the TwinArm monorepo; see [`../README.md`](../README.md) for the repository layout. Design
and purchasing rationale live in the planning repo: `robotics/TacitCapture/91_Magician_P&P構成…`
(architecture, tests M0–M7, safety) and `93_Magicianカメラ…` (camera compatibility, buy list,
object sizes).

**First-time setup manual (Japanese, with photo slots): [`manual/index.html`](manual/index.html).**

## Status

Offline tests pass with no hardware (`uv run pytest`). The `--dry-run` path (synthetic frame,
recording robot) exercises the whole pipeline. Real-arm and real-camera steps have **not** been run
yet: the Magician model/location and the camera purchase are still pending on the robotics side.

## Quick start

```bash
cd dobot
uv sync                                   # first time; creates .venv with Python 3.13
uv run pytest                             # offline tests — must pass before touching hardware
uv run python main.py --dry-run --no-llm --text "赤いブロックを右のトレイに置いて"
```

Then with hardware, in this order (each step is a gate for the next; details in the manual):

```bash
uv run python check_camera.py --list                      # read-only
uv run python check_camera.py --index 0                   # read-only: exposure/WB lock + brightness jitter report
uv run python check_robot.py --list                       # read-only: candidate serial ports
uv run python check_robot.py --port /dev/tty.usbserial-XXXX          # read-only: connect, print pose
uv run python check_robot.py --port /dev/tty.usbserial-XXXX --home   # MOVES the arm
uv run python calibrate.py --make-markers assets/markers  # print the 4 ArUco markers
uv run python calibrate.py --config config.json           # jog the suction cup to each marker (moves on your command)
uv run python main.py --tune --camera-index 0             # HSV/area tuning view (camera only)
uv run python main.py --config config.json --robot pydobot --port ... --no-llm --keyboard   # first real pick-and-place
```

`config.json` is the committed default (1280×720 @ 30 fps, manual exposure, suction cup, 3 cm colour
blocks). Put your camera index and serial port in it, or pass `--camera-index` / `--port`.

## Script inventory

Risk classes: **read-only** (no motion), **moves arm** (commands motion), **camera only**.

| Script | Purpose | Hardware risk |
| ------ | ------- | ------------- |
| `check_camera.py` | Enumerate cameras; open one, lock exposure/WB, measure brightness jitter, save a snapshot to `logs/`. | camera only |
| `check_robot.py` | List candidate ports (`--list`); connect and print pose; `--home` / `--round-trip` move the arm; `--dry` rehearses without hardware. | read-only by default; **moves arm** with `--home` / `--round-trip` |
| `calibrate.py` | Eye-to-hand calibration: print ArUco markers (`--make-markers`), then fit pixels→robot XY from 4+ correspondences into `assets/homography.json`. Reads the arm pose while *you* jog it. | read-only (arm pose is read, not commanded) |
| `main.py` | The application: `--tune` (camera view only), `--dry-run` (synthetic frame + recording robot), or live with `--robot pydobot`. | **moves arm** when `--robot pydobot` |
| `robot_dobot.py` | pydobot wrapper with workspace guard (AABB + reach annulus), emergency stop, dry-run robot. `python robot_dobot.py --live` runs home → one pick/place → home. | **moves arm** with `--live` |
| `camera.py` | OpenCV UVC / RealSense / file camera with exposure & WB locking and read-back. | camera only |
| `detect.py` | HSV colour detection, synthetic test frame, panel drawing, YOLO-World hook. | none |
| `planner.py` | TaskExecutor (observe → choose → pick and place), JSONL log. | via robot |
| `rule_parser.py` / `llm_agent.py` | Japanese rule parser (offline) / Claude tool-use agent (enum-only arguments, no coordinates from the LLM). | none |
| `asr.py` / `tts.py` | Push-to-talk faster-whisper / VOICEVOX-say-SAPI. Optional extras. | none |
| `config.py` | Dataclasses + JSON (`python config.py out.json` writes the defaults). | none |
| `tests/` | Offline tests: detection, homography, parsing, guards, end-to-end dry run. | none |

## Layout

```
dobot/
  AGENTS.md  README.md  pyproject.toml  config.json
  main.py check_camera.py check_robot.py calibrate.py camera.py detect.py planner.py
  robot_dobot.py rule_parser.py llm_agent.py asr.py tts.py config.py
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

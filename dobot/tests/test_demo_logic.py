"""demo.py / panel_web.py / setup_wizard.py のハード無しテスト。"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from camera import FileCamera
from config import AppConfig, CameraConfig
from demo import DemoRunner, SharedCamera, attract_due, parse_args
from detect import make_synthetic_frame
from panel_web import PanelServer, PanelState
from setup_wizard import grid_slots, summarize


def test_attract_due_rules():
    now = 1000.0
    assert attract_due(now, now - 100, 90, 0, 0, 40, True, False)
    assert not attract_due(now, now - 10, 90, 0, 0, 40, True, False)          # まだ idle 足りない
    assert not attract_due(now, now - 100, 90, now + 5, 0, 40, True, False)   # 休み中
    assert not attract_due(now, now - 100, 90, 0, 40, 40, True, False)        # 上限
    assert not attract_due(now, now - 100, 90, 0, 0, 40, False, False)        # OFF
    assert not attract_due(now, now - 100, 90, 0, 0, 40, True, True)          # 非常停止中


def test_shared_camera_serves_latest_frame():
    frame = make_synthetic_frame(320, 180, [("red", 100, 90, 30)])
    inner = FileCamera(CameraConfig(backend="file"), frame)
    inner.open()
    cam = SharedCamera(inner, hz=60)
    cam.open()
    try:
        time.sleep(0.1)
        f = cam.read()
        assert f.shape == frame.shape and cam.frames > 0
        f[:] = 0                      # コピーなので内部は汚れない
        assert cam.read().max() > 0
    finally:
        cam.close()


def test_panel_state_and_http_roundtrip():
    state = PanelState()
    got: list[dict] = []
    srv = PanelServer(state, got.append, port=0)
    port = srv.start()
    try:
        state.set_frame(b"\xff\xd8fake\xff\xd9")
        state.update(state="idle", reply="こんにちは")
        state.push_event("起動")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=3) as r:
            s = json.loads(r.read().decode("utf-8"))
        assert s["state"] == "idle" and s["reply"] == "こんにちは" and s["events"][-1]["text"] == "起動" and s["frame_seq"] == 1
        req = urllib.request.Request(f"http://127.0.0.1:{port}/cmd", data=json.dumps({"type": "say", "text": "赤を右に"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            assert json.loads(r.read())["ok"] is True
        assert got == [{"type": "say", "text": "赤を右に"}]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            assert "Magician" in r.read().decode("utf-8")
    finally:
        srv.stop()


def test_demo_runner_dry_run_handles_commands(tmp_path):
    cfg = AppConfig.default()
    cfg.log_dir = str(tmp_path / "logs")
    cfg.save(tmp_path / "config.json")
    args = parse_args(["--dry-run", "--no-llm", "--no-panel", "--config", str(tmp_path / "config.json")])
    runner = DemoRunner(args)
    try:
        reply = runner.handle_utterance("赤いブロックを右のトレイに置いて")
        assert "赤いブロック" in reply and "右" in reply, reply
        snap = runner.state.snapshot()
        assert snap["utterance"].startswith("赤い") and snap["intent"]["object"] == "red_cube" and snap["last_pick"]["物"] == "red_cube"
        assert runner.executor.stats["picks_ok"] == 1
        runner.handle_command({"type": "estop"})
        assert runner.state.snapshot()["state"] == "estop" and runner.attract_on is False
        runner.handle_command({"type": "resume"})
        runner.handle_command({"type": "attract", "on": True})
        assert runner.state.snapshot()["mode"] == "attract"
        runner._tick_frame("test")
        seq, jpeg = runner.state.frame()
        assert seq >= 1 and jpeg and jpeg[:2] == b"\xff\xd8"
        assert runner.state.snapshot()["objects_seen"].get("red_cube", 0) >= 1
    finally:
        runner.shutdown()


def test_demo_runner_attract_cycle_dry(tmp_path):
    cfg = AppConfig.default()
    cfg.log_dir = str(tmp_path / "logs")
    cfg.demo.pause_s = 0.2
    cfg.save(tmp_path / "config.json")
    args = parse_args(["--dry-run", "--no-llm", "--no-panel", "--attract", "--config", str(tmp_path / "config.json")])
    runner = DemoRunner(args)
    try:
        runner.attract_cycle()
        st = runner.executor.stats
        assert st["picks_ok"] >= 1 and st["tidy"] == 1, st
        assert len(runner.cycle_times) == 1 and runner.paused_until > time.time()
        assert runner.state.snapshot()["state"] == "idle"
    finally:
        runner.shutdown()


def test_wizard_helpers():
    slots = grid_slots(235.0, 0.0, 45.0, 40.0, 2, 3)
    assert len(slots) == 6 and [212.5, -40.0] in slots and [257.5, 40.0] in slots
    cfg = AppConfig.default()
    s = summarize(cfg, Path("assets/homography.json"))
    assert "カメラ" in s and "置き場" in s and "start" in s


def test_parse_args_shapes_for_main_build():
    a = parse_args(["--dry-run", "--camera-index", "1"])
    assert isinstance(a, SimpleNamespace) and a.camera_index == 1 and a.keyboard is True and a.text is None and a.tune is False

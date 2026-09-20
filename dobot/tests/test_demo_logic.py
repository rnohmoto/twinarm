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
from demo import DemoRunner, SharedCamera, ThermalGuard, attract_due, parse_args
from detect import make_synthetic_frame
from panel_web import PanelServer, PanelState
from rule_parser import loop_count, parse
from setup_wizard import grid_slots, summarize


def test_attract_due_rules():
    now = 1000.0
    assert attract_due(now, now - 100, 90, 0, 0, 40, True, False)
    assert not attract_due(now, now - 10, 90, 0, 0, 40, True, False)          # まだ idle 足りない
    assert not attract_due(now, now - 100, 90, now + 5, 0, 40, True, False)   # 休み中
    assert not attract_due(now, now - 100, 90, 0, 40, 40, True, False)        # 上限
    assert not attract_due(now, now - 100, 90, 0, 0, 40, False, False)        # OFF
    assert not attract_due(now, now - 100, 90, 0, 0, 40, True, True)          # 非常停止中


def test_thermal_guard_budget_and_cooldown():
    g = ThermalGuard(budget_s=100, window_s=600, cooldown_s=120)
    assert g.can_run(0) and g.duty(0) == 0
    g.record(0, 60)                       # 60 秒動いた
    assert g.can_run(60) and round(g.duty(60) * 100) == 10
    g.record(100, 150)                    # 合計 110 秒 → 予算超え → 休憩
    assert not g.can_run(151) and g.resting_s(151) > 100
    assert not g.can_run(200)             # 休憩中
    assert g.can_run(150 + 120 + 1) is False or g.motion_s(271) >= 100   # 休憩明けでも窓内の動作が多ければまだ止まる
    assert g.can_run(700)                 # 窓（600 秒）が流れれば復帰


def test_rule_parser_loop_intent():
    cfg = AppConfig.default()
    assert parse("ループして", cfg).action == "loop" and parse("ループして", cfg).count == cfg.demo.loop_cycles
    assert parse("3回繰り返して", cfg).count == 3
    assert parse("二回まわして", cfg).count == 2
    assert parse("デモして", cfg).action == "loop"
    assert parse("元に戻して", cfg).action == "tidy"      # 「戻して」は片付け
    assert loop_count("１０回", 3) == 5 and loop_count("回数なし", 3) == 3


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
            html = r.read().decode("utf-8")
        assert "Magician" in html and "ループ" in html
    finally:
        srv.stop()


def _runner(tmp_path, *extra):
    cfg = AppConfig.default()
    cfg.log_dir = str(tmp_path / "logs")
    cfg.demo.pause_s = 0.2
    cfg.save(tmp_path / "config.json")
    args = parse_args(["--dry-run", "--no-llm", "--no-panel", "--config", str(tmp_path / "config.json"), *extra])
    return DemoRunner(args)


def test_demo_runner_dry_run_handles_commands(tmp_path):
    runner = _runner(tmp_path)
    try:
        reply = runner.handle_utterance("赤いブロックを右のトレイに置いて")
        assert "赤いブロック" in reply and "右" in reply, reply
        snap = runner.state.snapshot()
        assert snap["utterance"].startswith("赤い") and snap["intent"]["object"] == "red_cube" and snap["last_pick"]["物"] == "red_cube"
        assert runner.executor.stats["picks_ok"] == 1
        assert runner.guard.motion_s(time.time()) > 0          # 動いた時間が熱の予算に積まれる
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


def test_demo_runner_attract_cycle_moves_one_object(tmp_path):
    runner = _runner(tmp_path, "--attract")
    try:
        assert runner.attract_cycle() is True
        st = runner.executor.stats
        # 合成画像は静止しているので「運んだ物」は戻し先に現れない＝運ぶ 1 回・片付け 1 回（0 個）で完走する
        assert st["picks_ok"] == 1 and st["tidy"] == 1, st
        assert len(runner.cycle_times) == 1 and runner.paused_until > time.time()
        assert runner.state.snapshot()["state"] == "idle"
    finally:
        runner.shutdown()


def test_demo_runner_loop_command_and_thermal_stop(tmp_path):
    runner = _runner(tmp_path)
    try:
        runner.handle_command({"type": "loop", "cycles": 2})
        st = runner.executor.stats
        assert st["tidy"] == 2 and st["picks_ok"] == 2, st     # 静止画なので 1 サイクル＝運ぶ 1 個（片付けは 0 個）
        assert "2 回" in runner.state.snapshot()["reply"]
        # 「ループして」の発話でも同じ経路（ルール解析 → executor.run_loop → runner）
        reply = runner.handle_utterance("ループして")
        assert "回繰り返しました" in reply, reply
        # 熱の予算を使い切ると止まる
        now = time.time()
        runner.guard.record(now - 300, now)          # 300 秒動いたことにする（予算 240 秒）
        r = runner.run_loop(3)
        assert not r.ok and "休憩" in r.message and r.data["cycles"] == 0
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

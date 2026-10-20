"""映像の配信: 腕が動いている間も止まらない（専用スレッド）、表示だけ左右反転できる。"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from config import AppConfig
from demo import DemoRunner, parse_args
from detect import Detection, make_synthetic_frame, mirror_view


def wait_for(pred, timeout_s: float = 5.0, step: float = 0.01) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


def test_mirror_view_flips_the_frame_and_the_detections_but_not_the_robot_coordinates():
    frame = make_synthetic_frame(blocks=[("red", 300, 200, 60)])            # 左寄りに赤
    det = Detection("red_cube", 300.0, 200.0, 3600.0, (270, 170, 60, 60), x_mm=210.0, y_mm=-80.0, size_mm=40.0)
    vis, dets = mirror_view(frame, [det])
    assert vis.shape == frame.shape and vis.flags["C_CONTIGUOUS"]
    assert np.array_equal(vis[:, 1280 - 1 - 300], frame[:, 300])             # 画素は左右入れ替え
    d = dets[0]
    assert d.cx == 1280 - 1 - 300 and d.cy == 200.0 and d.bbox == (1280 - 270 - 60, 170, 60, 60)
    assert (d.x_mm, d.y_mm, d.size_mm) == (210.0, -80.0, 40.0)              # ロボット座標はそのまま
    assert det.cx == 300.0 and frame[:, 300].tolist() != vis[:, 300].tolist()       # 元は変えない


def _runner(tmp_path, mirror: bool) -> DemoRunner:
    cfg = AppConfig.default()
    cfg.log_dir = str(tmp_path / "logs")
    cfg.demo.stream_fps = 15
    cfg.demo.panel_mirror = mirror
    cfg.save(tmp_path / "config.json")
    return DemoRunner(parse_args(["--dry-run", "--no-llm", "--no-panel", "--keyboard", "--config", str(tmp_path / "config.json")]))


def test_stream_keeps_updating_while_the_arm_is_moving(tmp_path):
    # 2026-10-19: 映像の更新がロボットと同じワーカースレッドにあり、腕が動く 7 秒間は画面が止まっていた。
    runner = _runner(tmp_path, mirror=False)
    runner.robot.sim_move_s = 0.25                                           # 模擬の移動に時間をかける
    th = threading.Thread(target=runner.run, kwargs={"seconds": 10}, daemon=True)
    th.start()
    try:
        assert wait_for(lambda: runner.state.frame()[0] >= 2)                 # 配信スレッドが動いている
        assert wait_for(lambda: "home" in runner.robot.log and runner.state.snapshot()["state"] == "idle")
        runner.queue.put({"type": "round_trip"})                              # 4 区間 × 0.25 s 動く
        assert wait_for(lambda: runner.state.snapshot()["state"] == "moving")
        seq0 = runner.state.frame()[0]
        seen = 0
        while runner.state.snapshot()["state"] == "moving" and seen < 4:
            time.sleep(0.1)
            if runner.state.frame()[0] > seq0:
                seen += 1
                seq0 = runner.state.frame()[0]
        assert seen >= 3, "frames must keep flowing while the robot moves"
        assert wait_for(lambda: runner.state.snapshot()["state"] == "idle")
        assert runner.state.snapshot()["objects_seen"].get("red_cube", 0) >= 1
        assert wait_for(lambda: runner.state.snapshot()["fps"] > 0, 4.0)      # fps は 2 秒ごとに更新
    finally:
        runner.shutdown()
        th.join(timeout=3)


def test_panel_mirror_flips_only_the_published_picture(tmp_path):
    runner = _runner(tmp_path, mirror=True)
    try:
        runner._tick_frame("test")
        seq, jpeg = runner.state.frame()
        assert seq >= 1 and jpeg[:2] == b"\xff\xd8"
        import cv2
        shown = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        raw = runner.camera.read()
        # 合成画像の赤ブロック (400,300) は表示では右側に写る。検出のロボット座標は変わらない
        assert shown[300, 1280 - 1 - 400, 2] > 150 and raw[300, 400, 2] > 150 and shown[300, 400, 2] < 100
        det = next(d for d in runner.executor.detect(raw) if d.name == "red_cube")
        assert abs(det.cx - 400) < 3 and det.x_mm is not None
    finally:
        runner.shutdown()

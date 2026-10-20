"""操作画面（jog.py）のハード無しテスト: 目標の計算と作業範囲・フレーム・ワーカー・パネル HTTP。"""
from __future__ import annotations

import json
import struct
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from config import RobotConfig
from jog import (
    ID_PTP_CMD,
    ID_QUEUE_CLEAR,
    ID_QUEUE_FORCE_STOP,
    ID_QUEUE_START,
    PTP_MOVL_XYZ,
    RANGE_ALARMS,
    ArmWorker,
    DryArm,
    MagicianArm,
    MotionStopped,
    decode_alarms,
    jog_target,
    joint_angles,
    make_panel,
    violation,
)
from robot_dobot import OutOfWorkspace
from suction import build_frame, parse_frame

CFG = RobotConfig()


class FakeArmSerial:
    """どのコマンドにも空の応答を返し、GetPose だけ決まった位置を返す。"""

    def __init__(self, pose=(200.0, 10.0, 20.0, 0.0)):
        self.sent: list[tuple[int, int, bytes]] = []
        self._rx = b""
        self._pose = pose

    def write(self, data: bytes) -> None:
        cmd_id, ctrl, params = parse_frame(bytes(data))
        self.sent.append((cmd_id, ctrl, params))
        reply = struct.pack("<8f", *self._pose, 0, 0, 0, 0) if cmd_id == 10 else b""
        self._rx += build_frame(cmd_id, ctrl, reply)

    def read(self, n: int = 1) -> bytes:
        out, self._rx = self._rx[:n], self._rx[n:]
        return out

    def reset_input_buffer(self) -> None:
        self._rx = b""

    def close(self) -> None:
        pass


def test_jog_target_moves_one_axis_inside_the_workspace():
    assert jog_target(CFG, (200.0, 0.0, 50.0, 0.0), "y", -10) == (200.0, -10.0, 50.0)
    assert jog_target(CFG, (200.0, 0.0, 50.0, 0.0), "z", 5) == (200.0, 0.0, 55.0)


def test_jog_target_refuses_to_leave_the_workspace():
    with pytest.raises(OutOfWorkspace):
        jog_target(CFG, (200.0, 0.0, CFG.z_min + 2, 0.0), "z", -5)      # 箱の下へ
    with pytest.raises(OutOfWorkspace):
        jog_target(CFG, (CFG.x_max - 5, 100.0, 0.0, 0.0), "y", 20)      # リーチ環の外へ


def test_jog_target_lets_an_outside_arm_come_back_but_not_go_further():
    below = (200.0, 0.0, CFG.z_min - 30, 0.0)
    assert violation(CFG, *below[:3]) == 30
    assert jog_target(CFG, below, "z", 10)[2] == CFG.z_min - 20         # まだ外だが近づく
    with pytest.raises(OutOfWorkspace):
        jog_target(CFG, below, "z", -10)
    with pytest.raises(OutOfWorkspace):
        jog_target(CFG, below, "x", 200)                                # 別の軸ではみ出しを増やす


def test_magician_arm_frames():
    ser = FakeArmSerial()
    arm = MagicianArm(ser)
    assert arm.pose() == (200.0, 10.0, 20.0, 0.0)
    arm.move_linear(210.0, 10.0, 20.0, 0.0)
    assert ser.sent[-2][:2] == (ID_QUEUE_START, 1)
    cmd_id, ctrl, params = ser.sent[-1]
    assert (cmd_id, ctrl) == (ID_PTP_CMD, 3)                            # 書き込み・キュー
    assert struct.unpack("<B4f", params) == (PTP_MOVL_XYZ, 210.0, 10.0, 20.0, 0.0)
    arm.stop()
    assert [s[0] for s in ser.sent[-2:]] == [ID_QUEUE_FORCE_STOP, ID_QUEUE_CLEAR]
    arm.clear_alarms()
    assert ser.sent[-1][:2] == (20, 1)                                  # 解除は ID 20 への書き込み（21 では消えない）


def test_decode_alarms_reads_bit_positions():
    # 2026-10-05 に実機が返した値: 5 バイト目の bit2 = 0x22（動作中: 逆運動学の限界）
    assert decode_alarms(bytes.fromhex("00000000040000000000000000000000")) == [0x22]
    assert decode_alarms(bytes(16)) == []
    assert 0x22 in RANGE_ALARMS and 0x50 not in RANGE_ALARMS


def test_joint_angles_match_the_real_arm():
    # 実機の GetPose が同時に返した位置と関節角（吸盤オフセット 59.7 mm）
    for xyz, measured in (((266.9, 0.0, -39.6), (0.0, 57.0, 50.3)), ((228.4, 10.7, -11.9), (2.7, 37.6, 53.9))):
        j = joint_angles(*xyz)
        assert [round(j[k], 1) for k in ("J1", "J2", "J3")] == pytest.approx(measured, abs=0.11)
    assert joint_angles(400.0, 0.0, 0.0) is None                        # 腕の長さでは届かない


def test_jog_target_refuses_joint_range_inside_the_workspace_box():
    close_to_base = (160.0, 0.0, 20.0, 0.0)                             # 箱の中だが後腕がほぼ鉛直
    with pytest.raises(OutOfWorkspace, match="J2"):
        jog_target(CFG, close_to_base, "x", -10)
    with pytest.raises(OutOfWorkspace, match="J2"):
        jog_target(CFG, close_to_base, "z", 20)
    assert jog_target(CFG, close_to_base, "x", 10)[0] == 170.0


def test_worker_jogs_one_step_and_sets_speed_once():
    dev = DryArm()
    w = ArmWorker(dev, CFG, speed_pct=25)
    w.start()
    try:
        w.jog("x", 10)
        w.jog("z", -5)
    finally:
        w.shutdown()
    assert dev.moves == [(210.0, 0.0, 50.0, 0.0), (210.0, 0.0, 45.0, 0.0)]
    assert dev.speed == 25
    assert w.snapshot()["pose"] == {"x": 210.0, "y": 0.0, "z": 45.0, "r": 0.0}


def test_worker_refuses_out_of_workspace_without_moving():
    dev = DryArm(pose=(200.0, 0.0, CFG.z_max, 0.0))
    w = ArmWorker(dev, CFG)
    w.start()
    try:
        with pytest.raises(OutOfWorkspace):
            w.jog("z", 5)
    finally:
        w.shutdown()
    assert dev.moves == []


def test_worker_reports_a_move_that_never_arrives():
    class Stuck(DryArm):
        def move_linear(self, x, y, z, r):
            self.moves.append((x, y, z, r))  # 位置は変わらない

    now = [0.0]
    dev = Stuck()
    w = ArmWorker(dev, CFG, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
    w.start()
    try:
        with pytest.raises(RuntimeError, match="届かなかった"):
            w.jog("x", 5)
        assert dev.stops == 1 and "届かなかった" in w.snapshot()["error"]
    finally:
        w.shutdown()


def test_worker_recovers_from_a_range_alarm_and_logs_it(tmp_path):
    class Limited(DryArm):
        def move_linear(self, x, y, z, r):
            self.moves.append((x, y, z, r))  # 本体が途中で止めた
            self.alarm_codes = [0x22]

    dev = Limited()
    log = tmp_path / "jog.jsonl"
    w = ArmWorker(dev, CFG, log_path=log)
    w.start()
    try:
        with pytest.raises(RuntimeError, match="可動範囲の端"):
            w.jog("x", 5)
        snap = w.snapshot()
        assert dev.stops == 1 and dev.alarm_codes == [] and snap["alarms"] == "" and "0x22" in snap["error"]
    finally:
        w.shutdown()
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    jog_event = next(e for e in events if e["event"] == "jog")
    assert jog_event["from"][:3] == [200.0, 0.0, 50.0] and jog_event["target"] == [205.0, 0.0, 50.0]
    assert "0x22" in jog_event["result"]
    assert any(e["event"] == "alarms" and e["codes"] == ["0x22"] for e in events)


def test_worker_keeps_a_serious_alarm_until_the_user_clears_it():
    dev = DryArm()
    dev.alarm_codes = [0x51]                                            # J2 脱調: 自動では解除しない
    w = ArmWorker(dev, CFG)
    w.start()
    try:
        with pytest.raises(RuntimeError, match="脱調"):
            w.jog("x", 5)
        assert dev.moves == [] and dev.alarm_codes == [0x51]
        w.clear_alarms()
        w.jog("x", 5)
    finally:
        w.shutdown()
    assert len(dev.moves) == 1


def test_stop_interrupts_a_move_and_drops_earlier_requests():
    class Slow(DryArm):
        def move_linear(self, x, y, z, r):
            self.moves.append((x, y, z, r))  # 到着しない → 停止されるまで待ち続ける

    now = [0.0]
    dev = Slow()
    w = ArmWorker(dev, CFG, clock=lambda: now[0])
    w._stop.set()                                    # 移動の待ちに入った直後に停止が来た状況
    with pytest.raises(MotionStopped):
        w._jog("x", 5, asked_at=0.0)
    assert dev.stops == 1 and not w._stop.is_set() and w.snapshot()["moving"] is False
    with pytest.raises(MotionStopped):
        w._jog("x", 5, asked_at=0.0)                 # 停止より前に押された操作は捨てる
    assert len(dev.moves) == 1


def test_panel_http_jog_stop_and_validation():
    dev = DryArm()
    worker = ArmWorker(dev, CFG, max_on_s=0)
    worker.start()
    panel = make_panel(worker, port=0)
    base = f"http://127.0.0.1:{panel.start()}"

    def post(path: str, body: dict) -> dict:
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    try:
        assert "前後" in urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8")
        assert post("/jog", {"axis": "y", "mm": -5})["pose"]["y"] == -5.0
        assert post("/suction", {"on": True})["on"] is True
        assert post("/stop", {})["note"] == "停止しました" and dev.stops == 1
        for bad in ({"axis": "r", "mm": 5}, {"axis": "x", "mm": 300}, {"axis": "x", "mm": True}, {"axis": "x"}):
            with pytest.raises(urllib.error.HTTPError) as e:
                post("/jog", bad)
            assert e.value.code == 400
        dev._pose = (200.0, 0.0, CFG.z_max, 0.0)
        with pytest.raises(urllib.error.HTTPError) as e:
            post("/jog", {"axis": "z", "mm": 5})
        assert e.value.code == 502 and "作業範囲" in json.loads(e.value.read())["error"]
    finally:
        panel.stop()
        worker.shutdown()
    assert dev.on is False and len(dev.moves) == 1

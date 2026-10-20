"""停止レーン（T13 Step 1）: 動作中でも停止が即座に効くこと。実機なし（フェイクの腕とドライラン）。"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import AppConfig, RobotConfig
from demo import DemoRunner, parse_args
from robot_dobot import DryRunRobot, EmergencyStop, make_robot
from robot_magician import IndexedArm, MagicianRobot, MotionAlarm, MotionTimeout


class FakeArm:
    """IndexedArm と同じ呼び出し面。index は polls_to_finish 回の読み取りで目標に達する（0 なら進まない＝動作中のまま）。"""

    def __init__(self, polls_to_finish: int = 2):
        self.polls_to_finish = polls_to_finish
        self.sent: list[tuple] = []
        self.index = 0
        self._target = 0
        self._polls = 0
        self.alarm_codes: list[int] = []
        self.alarm_after_polls: int | None = None
        self.alarm_codes_during: list[int] = [0x22]
        self._pose = (200.0, 0.0, 50.0, 0.0)
        self.on_poll = None
        self.closed = False

    def device_sn(self) -> str:
        return "FAKE"

    def pose(self):
        return self._pose

    def alarms(self) -> list[int]:
        return list(self.alarm_codes)

    def clear_alarms(self) -> None:
        self.sent.append(("clear_alarms",))
        self.alarm_codes = [c for c in self.alarm_codes if c >= 0x50]     # 脱調以外は解除で消える

    def set_speed(self, pct: float) -> None:
        self.sent.append(("speed", pct))

    def queue_index(self) -> int:
        self._polls += 1
        if self.on_poll:
            self.on_poll(self._polls)
        if self.alarm_after_polls is not None and self._polls >= self.alarm_after_polls:
            self.alarm_codes = list(self.alarm_codes_during)
        if self.polls_to_finish > 0 and self._polls >= self.polls_to_finish:
            self.index = self._target
        return self.index

    def start_queue(self) -> None:
        self.sent.append(("start",))

    def clear_queue(self) -> None:
        self.sent.append(("clear",))

    def force_stop(self) -> None:
        self.sent.append(("force_stop",))

    def move_linear_queued(self, x, y, z, r) -> int:
        self._target += 1
        self._polls = 0
        self._pose = (x, y, z, r)
        self.sent.append(("ptp", x, y, z, r))
        return self._target

    def set_suction(self, enable_ctrl: int, suck: int) -> None:
        self.sent.append(("suction", enable_ctrl, suck))

    def io_multiplexing(self, eio, function) -> None:
        self.sent.append(("io_mux", eio, function))

    def io_adc(self, eio) -> int:
        return 3900

    def close(self) -> None:
        self.closed = True


def _robot(arm: FakeArm, **over) -> MagicianRobot:
    cfg = replace(RobotConfig(backend="magician", port="FAKE", poll_s=0.001, alarm_poll_s=0.0, move_timeout_s=0.2,
                              ee_settle_s=0.0), **over)
    rb = MagicianRobot(cfg, arm=cast(IndexedArm, arm))      # FakeArm は IndexedArm と同じ呼び出し面
    rb.connect()
    return rb


def kinds(arm: FakeArm) -> list[str]:
    return [s[0] for s in arm.sent]


HALT = ["force_stop", "clear", "suction"]


def test_connect_clears_and_starts_queue_and_sets_speed_percent():
    arm = FakeArm()
    _robot(arm)
    assert kinds(arm) == ["clear", "start", "speed"] and arm.sent[2] == ("speed", 30.0)


def test_move_waits_until_index_reaches_target():
    arm = FakeArm(polls_to_finish=3)
    rb = _robot(arm)
    rb.move_to(220, 10, 40)
    assert arm.sent[-1] == ("ptp", 220.0, 10.0, 40.0, 0.0) and arm.index == 1 and arm._polls == 3
    assert "force_stop" not in kinds(arm)


def test_estop_during_motion_halts_within_a_poll_and_raises():
    arm = FakeArm(polls_to_finish=0)                 # index が進まない＝動作中
    rb = _robot(arm, move_timeout_s=5.0)
    arm.on_poll = lambda n: rb.estop.set() if n == 2 else None
    t0 = time.monotonic()
    with pytest.raises(EmergencyStop):
        rb.move_to(220, 10, 40)
    assert time.monotonic() - t0 < 0.5
    assert kinds(arm)[-3:] == HALT and arm.sent[-1] == ("suction", 0, 0)
    assert any(s.startswith("halt(estop)") for s in rb.log)


def test_timeout_when_index_never_advances_then_queue_restarts():
    arm = FakeArm(polls_to_finish=0)
    rb = _robot(arm, move_timeout_s=0.05)
    with pytest.raises(MotionTimeout):
        rb.move_to(220, 10, 40)
    assert kinds(arm)[-3:] == HALT
    arm.polls_to_finish = 1
    rb.move_to(220, 10, 50)                          # 次の移動の前に StartExec を送る
    assert kinds(arm)[-2:] == ["start", "ptp"]


def test_alarm_during_motion_halts_and_reports_codes():
    arm = FakeArm(polls_to_finish=0)
    arm.alarm_after_polls = 1
    rb = _robot(arm, move_timeout_s=5.0)
    with pytest.raises(MotionAlarm) as e:
        rb.move_to(220, 10, 40)
    assert 0x22 in e.value.codes and kinds(arm)[-3:] == HALT and rb.last_alarms == [0x22]


def test_range_alarm_before_move_is_cleared_once_then_moves():
    arm = FakeArm(polls_to_finish=1)
    arm.alarm_codes = [0x22]
    rb = _robot(arm)
    rb.move_to(220, 10, 40)
    assert "clear_alarms" in kinds(arm) and kinds(arm)[-1] == "ptp"


def test_non_range_alarm_before_move_refuses_to_move():
    arm = FakeArm(polls_to_finish=1)
    arm.alarm_codes = [0x50]                         # 脱調: 解除では消えない
    rb = _robot(arm)
    with pytest.raises(MotionAlarm):
        rb.move_to(220, 10, 40)
    assert "ptp" not in kinds(arm)


def test_ee_off_is_two_stage_and_immediate():
    arm = FakeArm()
    rb = _robot(arm, ee_release_blow_s=0.01)
    rb.ee_on()
    rb.ee_off()
    assert [s for s in arm.sent if s[0] == "suction"] == [("suction", 1, 1), ("suction", 1, 0), ("suction", 0, 0)]
    arm2 = FakeArm()
    rb2 = _robot(arm2, ee_release_blow_s=0.0)
    rb2.ee_off()
    assert [s for s in arm2.sent if s[0] == "suction"] == [("suction", 0, 0)]


def test_stop_and_resume_from_worker_thread():
    arm = FakeArm(polls_to_finish=1)
    rb = _robot(arm)
    rb.stop()
    assert rb.estop.is_set() and kinds(arm)[-3:] == HALT
    with pytest.raises(EmergencyStop):
        rb.move_to(220, 10, 40)                      # 停止中は動かない
    assert kinds(arm)[-1] == "suction"               # 何も送っていない
    rb.clear_stop()
    assert kinds(arm)[-1] == "start"
    rb.move_to(220, 10, 40)
    assert kinds(arm)[-1] == "ptp"


def test_disconnect_turns_pump_off_immediately_and_closes():
    arm = FakeArm()
    rb = _robot(arm)
    rb.disconnect()
    assert arm.sent[-1] == ("suction", 0, 0) and arm.closed and rb.arm is None


def test_make_robot_magician_refuses_to_guess_port():
    rb = make_robot(RobotConfig(backend="magician", port=None))
    assert isinstance(rb, MagicianRobot)
    with pytest.raises(RuntimeError) as e:
        rb.connect()
    assert "推測しません" in str(e.value)


def test_dry_run_move_is_interruptible_by_estop():
    rb = DryRunRobot(RobotConfig(), sim_move_s=0.5)
    threading.Timer(0.05, rb.estop.set).start()
    t0 = time.monotonic()
    with pytest.raises(EmergencyStop):
        rb.move_to(220, 10, 40)
    assert time.monotonic() - t0 < 0.3


def _runner(tmp_path) -> DemoRunner:
    cfg = AppConfig.default()
    cfg.log_dir = str(tmp_path / "logs")
    cfg.save(tmp_path / "config.json")
    args = parse_args(["--dry-run", "--no-llm", "--no-panel", "--config", str(tmp_path / "config.json")])
    return DemoRunner(args)


def test_panel_estop_takes_effect_while_worker_is_moving(tmp_path):
    """パネルの非常停止（POST /cmd）は HTTP スレッドで Event を立て、動作は 1 区間以内に止まる。"""
    runner = _runner(tmp_path)
    runner.robot.sim_move_s = 0.3
    port = runner.start_panel(port=0)
    th = threading.Thread(target=runner.run, kwargs={"seconds": 20}, daemon=True)
    th.start()
    try:
        t_end = time.monotonic() + 5
        while "home" not in runner.robot.log:
            assert time.monotonic() < t_end, "起動時のホーム移動が始まらない"
            time.sleep(0.01)
        time.sleep(runner.robot.sim_move_s * 2 + 0.2)                     # ホーム移動（最大 2 区間）の完了を待つ
        runner.queue.put({"type": "say", "text": "赤いブロックを右のトレイに置いて"})
        t_end = time.monotonic() + 5
        while not runner.busy.is_set():
            assert time.monotonic() < t_end, "ワーカーが発話を取り出さない"
            time.sleep(0.005)
        time.sleep(0.35)                                                  # 2 区間目に入ったところで停止
        moves_before = sum(1 for m in runner.robot.log if m.startswith("move_to"))
        req = urllib.request.Request(f"http://127.0.0.1:{port}/cmd", data=json.dumps({"type": "estop"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        t_push = time.monotonic()
        with urllib.request.urlopen(req, timeout=3) as r:
            assert json.loads(r.read())["ok"] is True
        assert runner.robot.estop.is_set(), "HTTP スレッドで Event が立っていない"
        assert time.monotonic() - t_push < 0.2
        assert runner.state.snapshot()["state"] == "estop"
        t_end = time.monotonic() + 3
        while runner.busy.is_set():
            assert time.monotonic() < t_end, "動作が止まらない"
            time.sleep(0.005)
        assert time.monotonic() - t_push < runner.robot.sim_move_s + 0.3   # 1 区間以内
        moves_after = sum(1 for m in runner.robot.log if m.startswith("move_to"))
        assert moves_after - moves_before <= 1 and runner.executor.stats["picks_ok"] == 0
        t_end = time.monotonic() + 3
        while "ESTOP" not in runner.robot.log:                            # ワーカー側の stop() も走る
            assert time.monotonic() < t_end
            time.sleep(0.01)
    finally:
        runner.shutdown()
        runner._panel.stop()

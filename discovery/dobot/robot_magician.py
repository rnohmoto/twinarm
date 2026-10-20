"""Dobot Magician 制御（公式プロトコルを直接・pydobot 不使用）— 停止レーン対応の実機バックエンド。

robotics T13 Step 1（停止レーン）の実装。設計の根拠は robotics `TacitCapture/113_…_v1.md` §6 と `114_…_v1.md` §3・§6。

- `jog.py`／`suction.py` の直接プロトコル層を使う（2026-10-05 に実機で確認済み: GetPose・ID 20 の書き込みによる
  アラーム解除・ID 62 の吸引）。pydobot は使わない: 移動待ちが無期限で停止を見ない、`suck(False)` がキュー命令で
  停止中は実行されない、index の unpack が 'L' で環境依存、の 3 点のため。
- 移動: PTP MOVL をキューに積み、応答の 64 ビット index を目標にして GetQueuedCmdCurrentIndex（ID 246）を `poll_s` ごとに
  読む。完了は「現在の index ≥ 目標の index」（index は直前に完了した命令の番号）。
- 停止レーン: 待っている間に `estop` Event が立てば、**このスレッドで** ForceStop(242) → Clear(245) → ポンプ停止(0,0) を
  送ってから EmergencyStop を上げる。index が `move_timeout_s` の間進まなければ同じ停止手順のあと MotionTimeout。
  アラームが立てば同じ手順のあと MotionAlarm。停止後はキューが止まっているので、次の移動の前に StartExec(240) を送る。
- 吸着の OFF は 2 段（排気 (1,0) → ポンプ停止 (0,0)）。どちらも即時命令なので、キュー停止中でも効く。
- シリアルを触るのはワーカースレッドだけ。HTTP スレッドは `estop` Event を立てるだけ（`demo.py`）。`stop()` もワーカーから呼ぶ。
- 実機の挙動（ForceStop で止まるまでの時間・index の進み方・排気の効き・アラームの出方）は未確認。
  ユーザーが実行して報告した結果だけを事実にする。
"""
from __future__ import annotations

import struct
import time
from collections.abc import Callable

from config import RobotConfig
from jog import (
    ID_PTP_CMD,
    ID_QUEUE_CLEAR,
    ID_QUEUE_FORCE_STOP,
    ID_QUEUE_START,
    PTP_MOVL_XYZ,
    RANGE_ALARMS,
    MagicianArm,
    describe_alarms,
)
from robot_dobot import (
    ID_GET_IO_ADC,
    ID_SET_IO_MULTIPLEXING,
    IO_FUNCTION,
    EmergencyStop,
    MotionError,
    RobotBase,
)
from suction import CTRL_READ, CTRL_WRITE_IMMEDIATE, ID_SUCTION_CUP

ID_QUEUE_CURRENT_INDEX = 246   # GetQueuedCmdCurrentIndex → uint64（直前に完了した命令の index）
CTRL_WRITE_QUEUED = 3          # rw=1, isQueued=1


class MotionTimeout(MotionError):
    """index が move_timeout_s の間進まなかった（ForceStop→Clear→ポンプ停止は送信済み）。"""


class MotionAlarm(MotionError):
    """移動の前か最中にアラームが出た（最中なら ForceStop→Clear→ポンプ停止は送信済み）。"""

    def __init__(self, codes: list[int], when: str):
        self.codes = list(codes)
        super().__init__(f"アラーム（{when}）: {describe_alarms(self.codes)}")


class IndexedArm(MagicianArm):
    """MagicianArm に、キュー index・キュー制御・吸盤の引数指定・I/O を足したもの。"""

    @classmethod
    def open(cls, port: str) -> IndexedArm:
        arm = super().open(port)
        assert isinstance(arm, IndexedArm)
        return arm

    def queue_index(self) -> int:
        params = self._command(ID_QUEUE_CURRENT_INDEX, CTRL_READ)
        if len(params) < 8:
            raise RuntimeError(f"GetQueuedCmdCurrentIndex: 応答が短い ({len(params)} bytes)")
        return struct.unpack_from("<Q", params, 0)[0]

    def start_queue(self) -> None:
        self._command(ID_QUEUE_START, CTRL_WRITE_IMMEDIATE)

    def clear_queue(self) -> None:
        self._command(ID_QUEUE_CLEAR, CTRL_WRITE_IMMEDIATE)

    def force_stop(self) -> None:
        self._command(ID_QUEUE_FORCE_STOP, CTRL_WRITE_IMMEDIATE)

    def move_linear_queued(self, x: float, y: float, z: float, r: float) -> int:
        """直線移動をキューに積み、その命令の index を返す（到着は待たない・StartExec は送らない）。"""
        params = self._command(ID_PTP_CMD, CTRL_WRITE_QUEUED, struct.pack("<B4f", PTP_MOVL_XYZ, x, y, z, r))
        if len(params) < 8:
            raise RuntimeError(f"SetPTPCmd: 応答に index がない ({len(params)} bytes)")
        return struct.unpack_from("<Q", params, 0)[0]

    def set_suction(self, enable_ctrl: int, suck: int) -> None:
        """SetEndEffectorSuctionCup を即時で送る。(1,1)=吸引・(1,0)=排気・(0,0)=ポンプ停止。"""
        self._command(ID_SUCTION_CUP, CTRL_WRITE_IMMEDIATE, bytes([enable_ctrl & 1, suck & 1]))

    def io_multiplexing(self, eio: int, function: str) -> None:
        self._command(ID_SET_IO_MULTIPLEXING, CTRL_WRITE_IMMEDIATE, bytes([int(eio), IO_FUNCTION[function]]))

    def io_adc(self, eio: int) -> int:
        params = self._command(ID_GET_IO_ADC, CTRL_READ, bytes([int(eio)]))
        if len(params) < 3:
            raise RuntimeError(f"GetIOADC({eio}): 応答が短い ({len(params)} bytes)")
        _addr, value = struct.unpack_from("<BH", params, 0)
        return int(value)


class MagicianRobot(RobotBase):
    """実機（公式プロトコル直接）。`connect()`・`_move`・`_ee`・`stop()`・`clear_stop()` はワーカースレッドからだけ呼ぶ。

    `arm` を渡すと接続を開かない（テスト用のフェイク）。
    """

    def __init__(self, cfg: RobotConfig, arm: IndexedArm | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        super().__init__(cfg)
        self.arm = arm
        self._clock = clock
        self._sleep = sleep
        self._queue_stopped = True      # connect() で StartExec を送るまでは止まっている扱い
        self.last_alarms: list[int] = []

    @property
    def dev(self) -> IndexedArm:
        if self.arm is None:
            raise RuntimeError("Magician に接続していません（connect() が先）")
        return self.arm

    # ------------------------------------------------------------ connect
    def connect(self) -> None:
        if self.cfg.end_effector != "suction":
            raise NotImplementedError("magician バックエンドは吸盤のみ対応（グリッパは pydobot バックエンドを使う）")
        if self.arm is None:
            port = self.cfg.port
            if not port:
                raise RuntimeError("Dobot のポートが指定されていません（--port か config の robot.port に書く）。"
                                   "ポートは推測しません。")
            self.arm = IndexedArm.open(port)
            self.log.append(f"connected {port} sn={self.arm.device_sn()}")
        self.dev.clear_queue()
        self.dev.start_queue()
        self._queue_stopped = False
        self.dev.set_speed(float(self.cfg.speed_pct))
        self.last_alarms = self.dev.alarms()
        if self.last_alarms:
            self.log.append(f"alarms at connect: {describe_alarms(self.last_alarms)}")

    def disconnect(self) -> None:
        if self.arm is None:
            return
        try:
            self._pump_off()
        finally:
            try:
                self.arm.close()
            finally:
                self.arm = None

    def pose(self) -> tuple[float, float, float, float]:
        x, y, z, r = self.dev.pose()
        return float(x), float(y), float(z), float(r)

    # ------------------------------------------------------------- motion
    def _move(self, x: float, y: float, z: float, r: float, wait: bool) -> None:
        self._before_move()
        target = self.dev.move_linear_queued(x, y, z, r)
        if wait:
            self._wait_motion(target)

    def _before_move(self) -> None:
        if self.estop.is_set():
            raise EmergencyStop("estop is set")
        codes = self.dev.alarms()
        if codes and set(codes) <= RANGE_ALARMS:
            # 範囲系（計画・逆運動学・リミット）は jog.py と同じく 1 回だけ解除を試みる。ほかは人が原因を見る
            self.dev.clear_alarms()
            codes = self.dev.alarms()
            self.log.append("alarms cleared" if not codes else f"alarms remain: {describe_alarms(codes)}")
        self.last_alarms = codes
        if codes:
            raise MotionAlarm(codes, "移動前")
        if self._queue_stopped:
            self.dev.start_queue()
            self._queue_stopped = False

    def _wait_motion(self, target: int) -> None:
        deadline = self._clock() + float(self.cfg.move_timeout_s)
        next_alarm_check = self._clock() + float(self.cfg.alarm_poll_s)
        while True:
            if self.estop.is_set():
                self._halt("estop")
                raise EmergencyStop("estop during motion")
            if self.dev.queue_index() >= target:
                return
            now = self._clock()
            if now >= next_alarm_check:
                next_alarm_check = now + float(self.cfg.alarm_poll_s)
                codes = self.dev.alarms()
                if codes:
                    self.last_alarms = codes
                    self._halt("alarm")
                    raise MotionAlarm(codes, "動作中")
            if now >= deadline:
                self._halt("timeout")
                raise MotionTimeout(f"移動が {self.cfg.move_timeout_s} 秒で完了しません（index {target} に届かない）")
            self._sleep(float(self.cfg.poll_s))

    def _halt(self, reason: str) -> None:
        """ForceStop → Clear → ポンプ停止。途中で失敗しても残りを送る（失敗は log に残す）。"""
        self.log.append(f"halt({reason})")
        self._queue_stopped = True
        for name, fn in (("force_stop", self.dev.force_stop), ("clear_queue", self.dev.clear_queue)):
            try:
                fn()
            except Exception as e:  # noqa: BLE001  停止手順は最後まで送る
                self.log.append(f"{name} failed: {e!r}")
        self._pump_off()

    def _pump_off(self) -> None:
        try:
            self.dev.set_suction(0, 0)
        except Exception as e:  # noqa: BLE001
            self.log.append(f"pump off failed: {e!r}")

    # ------------------------------------------------------- end effector
    def _ee(self, on: bool) -> None:
        if on:
            self.dev.set_suction(1, 1)
            return
        blow = float(self.cfg.ee_release_blow_s)
        if blow > 0:
            self.dev.set_suction(1, 0)      # 排気で短く放す
            self._sleep(blow)
        self.dev.set_suction(0, 0)          # ポンプ停止

    # ---------------------------------------------------------- stop lane
    def stop(self) -> None:
        """ワーカースレッドから。HTTP スレッドは `estop.set()` だけにする（シリアルを触らない）。"""
        super().stop()
        self._halt("stop")

    def clear_stop(self) -> None:
        super().clear_stop()
        self.dev.start_queue()
        self._queue_stopped = False
        self.last_alarms = self.dev.alarms()
        self.log.append("resume" + (f" (alarms: {describe_alarms(self.last_alarms)})" if self.last_alarms else ""))

    def alarms(self) -> list[int]:
        self.last_alarms = self.dev.alarms()
        return list(self.last_alarms)

    def clear_alarms(self) -> list[int]:
        """解除（ID 20 への書き込み）を送り、読み返して残ったアラームを返す。"""
        self.dev.clear_alarms()
        return self.alarms()

    # ------------------------------------------------------ I/O（温度センサ）
    def set_io_multiplexing(self, eio: int, function: str = "adc") -> None:
        self.dev.io_multiplexing(eio, function)
        self.log.append(f"io_mux({eio},{function})")

    def read_adc(self, eio: int) -> int:
        return self.dev.io_adc(eio)

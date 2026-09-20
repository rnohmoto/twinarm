"""Dobot Magician 制御（pydobot）と、実機なしで動くドライラン。

★一次情報（2026-09-11 確認）
- pydobot README: `pip install pydobot`、Silicon Labs CP210x ドライバ、`Dobot(port=port, verbose=True)`、
  `.pose()`→(x,y,z,r,j1..j4)、`.move_to(x,y,z,r,wait=False)`、`.speed(velocity, acceleration)`、
  `.suck(enable)`、`.grip(enable)`、`.wait(ms)`。（https://github.com/luismesas/pydobot）
- Dobot 公式: Magician = 4軸・可搬 500g・リーチ 320mm・繰返し ±0.2mm・吸盤 φ20mm/-35kPa・グリッパ 27.5mm/8N
  （https://www.dobot-robots.com/products/education/magician.html）

安全設計
- すべての移動は WorkspaceGuard（AABB＋リーチ環）を通す。範囲外は動かさず例外。
- 上下は「退避高さ z_safe ↔ 作業高さ」の直線動作だけ（JUMP 相当を自前で組む）。
- 非常停止フラグ（threading.Event）を各ステップの間で確認。
- Magician Lite / E6 は API が別系統（DobotLink / TCP-IP）。本モジュールは初代 Magician 用。
"""
from __future__ import annotations

import math
import struct
import threading
import time
from dataclasses import dataclass

from config import RobotConfig

# Dobot Communication Protocol V1.1.5（公式）: I/O 関連のコマンド ID と多重化の機能番号
ID_SET_IO_MULTIPLEXING = 130   # params: address(uint8), multiplex(uint8)
ID_GET_IO_ADC = 134            # params: address(uint8) → response: address(uint8), value(uint16, 0..4095)
IO_FUNCTION = {"dummy": 0, "pwm": 1, "do": 2, "di": 3, "adc": 4}
ADC_MAX = 4095


def thermistor_c(adc: int, r_pullup: float = 4700.0, r25: float = 100000.0, beta: float = 3950.0,
                 v_pullup: float = 3.3, adc_fullscale_v: float = 3.3) -> float | None:
    """Temp ピン（内蔵プルアップ r_pullup → v_pullup・サーミスタは GND 側）の ADC 値を ℃ にする。

    V = adc / 4095 × adc_fullscale_v、R = r_pullup × V / (v_pullup − V)、β 式で温度。範囲外なら None。
    adc_fullscale_v は 3.3 か 5.0（何も繋がずに読んだ値で決める: 4095 付近なら 3.3、2700 付近なら 5.0）。
    """
    if adc <= 0 or adc >= ADC_MAX:
        return None
    v = adc_fullscale_v * adc / ADC_MAX
    if v >= v_pullup - 1e-6:
        return None
    r = r_pullup * v / (v_pullup - v)
    if r <= 0:
        return None
    inv_t = 1.0 / 298.15 + math.log(r / r25) / beta
    return 1.0 / inv_t - 273.15


class OutOfWorkspace(Exception):
    pass


class EmergencyStop(Exception):
    pass


@dataclass
class WorkspaceGuard:
    cfg: RobotConfig

    def check(self, x: float, y: float, z: float) -> None:
        c = self.cfg
        r = math.hypot(x, y)
        if not (c.x_min <= x <= c.x_max and c.y_min <= y <= c.y_max and c.z_min <= z <= c.z_max):
            raise OutOfWorkspace(f"({x:.1f},{y:.1f},{z:.1f}) outside AABB "
                                 f"x[{c.x_min},{c.x_max}] y[{c.y_min},{c.y_max}] z[{c.z_min},{c.z_max}]")
        if not (c.reach_min <= r <= c.reach_max):
            raise OutOfWorkspace(f"radius {r:.1f} outside [{c.reach_min},{c.reach_max}]")

    def inside(self, x: float, y: float, z: float) -> bool:
        try:
            self.check(x, y, z)
            return True
        except OutOfWorkspace:
            return False


class RobotBase:
    """最小インターフェース。PickPlaceController がこれだけを使う。"""
    def __init__(self, cfg: RobotConfig):
        self.cfg = cfg
        self.guard = WorkspaceGuard(cfg)
        self.estop = threading.Event()
        self.log: list[str] = []

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def pose(self) -> tuple[float, float, float, float]:
        raise NotImplementedError
    def _move(self, x: float, y: float, z: float, r: float, wait: bool) -> None:
        raise NotImplementedError
    def _ee(self, on: bool) -> None:
        raise NotImplementedError

    # --- 共通（ガード付き） ---
    def move_to(self, x: float, y: float, z: float, r: float | None = None, wait: bool = True) -> None:
        if self.estop.is_set():
            raise EmergencyStop("estop is set")
        self.guard.check(x, y, z)
        rr = self.cfg.r if r is None else r
        self.log.append(f"move_to({x:.1f},{y:.1f},{z:.1f},{rr:.1f})")
        self._move(x, y, z, rr, wait)

    def ee_on(self) -> None:
        self.log.append("ee_on")
        self._ee(True)
        time.sleep(self.cfg.ee_settle_s)

    def ee_off(self) -> None:
        self.log.append("ee_off")
        self._ee(False)
        time.sleep(self.cfg.ee_settle_s * 0.5)

    def home(self) -> None:
        x, y, z, r = self.cfg.home_xyzr
        self.log.append("home")
        # まず現在位置の真上へ退避してから home へ（斜め降下で物にぶつからない）
        try:
            cx, cy, cz, _ = self.pose()
            if cz < self.cfg.z_safe and self.guard.inside(cx, cy, self.cfg.z_safe):
                self.move_to(cx, cy, self.cfg.z_safe)
        except NotImplementedError:
            pass
        self.move_to(x, y, z, r)

    def stop(self) -> None:
        self.estop.set()
        self.log.append("ESTOP")

    def clear_stop(self) -> None:
        self.estop.clear()


class DryRunRobot(RobotBase):
    """実機なし。移動を記録し、pose は最後の目標を返す。move の時間を模擬（既定 0）。"""
    def __init__(self, cfg: RobotConfig, sim_move_s: float = 0.0):
        super().__init__(cfg)
        self._pose = tuple(cfg.home_xyzr)
        self.sim_move_s = sim_move_s
        self.ee_state = False

    def pose(self):
        return self._pose

    def _move(self, x, y, z, r, wait):
        if self.sim_move_s:
            time.sleep(self.sim_move_s)
        self._pose = (x, y, z, r)

    def _ee(self, on):
        self.ee_state = on

    # 温度センサの模擬（3900 ≈ 25℃・100kΩ サーミスタ・3.3V フルスケール）
    sim_adc: int = 3900

    def set_io_multiplexing(self, eio: int, function: str = "adc") -> None:
        self.log.append(f"io_mux({eio},{function})")

    def read_adc(self, eio: int) -> int:
        return int(self.sim_adc)


class PydobotRobot(RobotBase):
    def __init__(self, cfg: RobotConfig):
        super().__init__(cfg)
        self.dev = None

    @staticmethod
    def list_candidate_ports() -> list[str]:
        """CP210x（Silicon Labs）または説明に Dobot を含むポートを列挙する（選びはしない）。

        twinarm の実機規則: ポートは推測せず、ユーザーが渡す。ここは候補を見せるだけ。
        """
        from serial.tools import list_ports  # pyserial
        cands = []
        for p in list_ports.comports():
            desc = f"{p.description} {p.manufacturer} {p.hwid}".lower()
            if "cp210" in desc or "silicon labs" in desc or "dobot" in desc:
                cands.append(p.device)
        return cands

    @classmethod
    def find_port(cls) -> str | None:
        """後方互換: 候補が 1 つだけのときにその名前を返す（2 つ以上なら None＝選ばない）。"""
        cands = cls.list_candidate_ports()
        return cands[0] if len(cands) == 1 else None

    def connect(self) -> None:
        port = self.cfg.port
        if not port:
            cands = ", ".join(self.list_candidate_ports()) or "なし"
            raise RuntimeError(
                "Dobot のポートが指定されていません（--port か config の robot.port に書く）。"
                f"ポートは推測しません。候補: {cands}"
            )
        import pydobot  # type: ignore
        self.dev = pydobot.Dobot(port=port, verbose=False)
        self.dev.speed(self.cfg.velocity, self.cfg.acceleration)
        self.log.append(f"connected {port}")

    def disconnect(self) -> None:
        if self.dev is not None:
            try:
                self._ee(False)
                self.dev.close()
            finally:
                self.dev = None

    def pose(self):
        x, y, z, r, *_ = self.dev.pose()
        return float(x), float(y), float(z), float(r)

    def _move(self, x, y, z, r, wait):
        self.dev.move_to(x, y, z, r, wait=wait)

    def _ee(self, on):
        if self.cfg.end_effector == "gripper":
            self.dev.grip(on)
        else:
            self.dev.suck(on)

    # --- I/O（温度センサ等・読み取り系。pydobot 1.3.2 に無いので公式プロトコルの ID を直接送る） ---
    def _io_command(self, cmd_id: int, params: bytes, write: bool):
        from pydobot.message import Message  # type: ignore
        msg = Message()
        msg.id = cmd_id
        msg.ctrl = 1 if write else 0            # bit0 = rw（1=書き込み）、bit1 = isQueued（0=即時）
        msg.params = bytes(params)
        return self.dev._send_command(msg)

    def set_io_multiplexing(self, eio: int, function: str = "adc") -> None:
        """EIO ピンの機能を切り替える（adc/di/do/pwm/dummy）。SetIOMultiplexing（ID 130）。"""
        self._io_command(ID_SET_IO_MULTIPLEXING, bytes([int(eio), IO_FUNCTION[function]]), write=True)
        self.log.append(f"io_mux({eio},{function})")

    def read_adc(self, eio: int) -> int:
        """EIO ピンの ADC 値（0〜4095）。GetIOADC（ID 134）。事前に set_io_multiplexing(eio, "adc")。"""
        resp = self._io_command(ID_GET_IO_ADC, bytes([int(eio)]), write=False)
        params = bytes(getattr(resp, "params", b"") or b"")
        if len(params) < 3:
            raise RuntimeError(f"GetIOADC({eio}): 応答が短い ({len(params)} bytes)")
        _addr, value = struct.unpack_from("<BH", params, 0)
        return int(value)

    # pydobot 1.3.2 は home（SetHOMECmd ID=31）とアラーム解除を公開していない（★ソース確認 2026-09-11）。
    # 電源投入ごとのホーミングは本体キー長押し 2 秒 or DobotStudio で行う（初代はインクリメンタルエンコーダ）。
    # キュー制御はプライベートメソッドとして存在するので、非常停止では best-effort で呼ぶ。
    def stop(self) -> None:
        super().stop()
        for name in ("_set_queued_cmd_stop_exec", "_set_queued_cmd_clear"):
            fn = getattr(self.dev, name, None)
            if fn is not None:
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    self.log.append(f"{name} failed: {e!r}")
        try:
            self._ee(False)
        except Exception:
            pass

    def clear_stop(self) -> None:
        super().clear_stop()
        fn = getattr(self.dev, "_set_queued_cmd_start_exec", None)
        if fn is not None:
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.log.append(f"_set_queued_cmd_start_exec failed: {e!r}")


def make_robot(cfg: RobotConfig) -> RobotBase:
    if cfg.backend == "dry":
        return DryRunRobot(cfg)
    if cfg.backend == "pydobot":
        return PydobotRobot(cfg)
    raise ValueError(f"unknown robot backend: {cfg.backend}")


class PickPlaceController:
    """1 個のピック＆プレースを安全な段取りで実行する。

    pick:  (x,y,z_safe) → (x,y,z_pick) → ee_on → (x,y,z_safe)
    place: (px,py,z_safe) → (px,py,z_place) → ee_off → (px,py,z_safe)
    途中で OutOfWorkspace / EmergencyStop が出たら、そこで止めて例外を上げる（呼び出し側が home へ戻す）。
    """
    def __init__(self, robot: RobotBase):
        self.robot = robot
        self.c = robot.cfg

    def pick(self, x: float, y: float, r: float | None = None, z_pick: float | None = None) -> None:
        zp = self.c.z_pick if z_pick is None else z_pick
        self.robot.move_to(x, y, self.c.z_safe, r)
        self.robot.move_to(x, y, zp, r)
        self.robot.ee_on()
        self.robot.move_to(x, y, self.c.z_safe, r)

    def place(self, x: float, y: float, z_place: float | None = None) -> None:
        zp = self.c.z_place if z_place is None else z_place
        self.robot.move_to(x, y, self.c.z_safe)
        self.robot.move_to(x, y, zp)
        self.robot.ee_off()
        self.robot.move_to(x, y, self.c.z_safe)

    def pick_and_place(self, x: float, y: float, px: float, py: float,
                       r: float | None = None, z_place: float | None = None,
                       z_pick: float | None = None) -> None:
        # 先に両方の座標をガードに通す（拾ってから置けないと分かるのを防ぐ）
        self.robot.guard.check(x, y, self.c.z_pick if z_pick is None else z_pick)
        self.robot.guard.check(px, py, self.c.z_place if z_place is None else z_place)
        self.pick(x, y, r, z_pick)
        self.place(px, py, z_place)


if __name__ == "__main__":  # 疎通テスト: python robot_dobot.py [--live]
    import sys
    cfg = RobotConfig(backend="pydobot" if "--live" in sys.argv else "dry")
    rb = make_robot(cfg)
    rb.connect()
    print("pose:", rb.pose() if cfg.backend == "pydobot" else "(dry)")
    rb.home()
    PickPlaceController(rb).pick_and_place(220, 40, 230, -110)
    rb.home()
    rb.disconnect()
    print("\n".join(rb.log))

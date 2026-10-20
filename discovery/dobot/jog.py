"""撮影用の操作画面: 前後・左右・上下に少しずつ動かすコントローラーと、吸引 ON/OFF。★腕が動く。

使い方（このフォルダで）:
  uv run python jog.py --port /dev/cu.usbserial-XXXX     # 画面 http://127.0.0.1:8791
  uv run python jog.py --dry                             # 実機なしで画面だけ確かめる

設計
- 動かし方は「1 回の操作 = 決まった歩幅（1/5/10/20 mm）の直線移動」。押し続けると 1 歩ずつ繰り返す。
  連続ジョグ（SetJOGCmd）は使わない: 画面が固まっても腕は 1 歩ぶんで必ず止まる。
- 目標は config の作業範囲（WorkspaceGuard と同じ箱とリーチ環）に通す。範囲外へは動かさない。
  すでに範囲外にいるときは、範囲に近づく向きだけ許す。
- 送る前に関節角も計算し、公式の可動範囲（後腕 0〜85°・前腕 -10〜95°・旋回 ±90°）を出る目標は送らない。
  それでも本体が「逆運動学の限界」などのアラームで止めたときは、止めてアラームを解除し、理由を画面に出す。
- 1 歩ごとの記録（位置・関節角・目標・結果・アラーム）を logs/jog_日付.jsonl に残す。
- 座標はロボット基準（X+ = 前、Y+ = 後ろから見て左、Z+ = 上）。電源投入後にホーミングしていないと
  旋回の原点がずれているので、左右の向きと範囲の判定もそのぶんずれる。
- 画面はシリアルに触れない。suction.py と同じく 1 本のワーカースレッドだけが機器を持つ。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import struct
import sys
import threading
import time
from pathlib import Path

from config import AppConfig, RobotConfig
from robot_dobot import OutOfWorkspace, WorkspaceGuard
from suction import (
    CTRL_WRITE_IMMEDIATE,
    DEFAULT_HTTP_PORT,
    DEFAULT_MAX_ON_S,
    DrySuction,
    MagicianSuction,
    SuctionPanel,
    SuctionWorker,
    resolve_port,
    serve,
)

HERE = Path(__file__).resolve().parent

ID_POSE = 10                   # GetPose → x, y, z, r, j1..j4（float32 × 8）
ID_ALARMS = 20                 # 読み取り = GetAlarmsState（16 バイトのビット列）／書き込み = ClearAllAlarmsState
                               # （ID 21 への書き込みは応答は返るが解除されない。2026-10-05 実機で確認）
ID_END_EFFECTOR_PARAMS = 60    # GetEndEffectorParams → xBias, yBias, zBias（float32・mm）
ID_PTP_COMMON_PARAMS = 83      # velocityRatio, accelerationRatio（float32・%）
ID_PTP_CMD = 84                # ptpMode(uint8), x, y, z, r（float32）
ID_QUEUE_START = 240           # SetQueuedCmdStartExec
ID_QUEUE_FORCE_STOP = 242      # SetQueuedCmdForceStopExec（動作中でもすぐ止める）
ID_QUEUE_CLEAR = 245           # SetQueuedCmdClear
CTRL_WRITE_QUEUED = 3
PTP_MOVL_XYZ = 2               # 直交座標の直線移動

AXES = ("x", "y", "z")
STEPS_MM = (1, 5, 10, 20)
DEFAULT_SPEED_PCT = 30.0
ARRIVE_TOL_MM = 0.8            # Magician の繰返し精度 ±0.2 mm より十分大きく
MOVE_TIMEOUT_S = 6.0
POLL_S = 0.05

REAR_ARM_MM = 135.0            # 後腕（肩〜肘）
FOREARM_MM = 147.0             # 前腕（肘〜手首）
DEFAULT_TOOL_X_MM = 59.7       # 吸盤の前方オフセット（実機の GetEndEffectorParams の値）
# 公式仕様の可動範囲（度）。J2 = 後腕の鉛直からの倒れ、J3 = 前腕の水平からの下向き角
JOINT_LIMITS_DEG = {"J1": (-90.0, 90.0), "J2": (0.0, 85.0), "J3": (-10.0, 95.0)}
JOINT_MARGIN_DEG = 1.0

# アラーム番号（= ビット位置）→ 意味。Dobot Magician の ALARM 一覧より
ALARM_NAMES = {
    0x00: "リセット", 0x01: "未定義の命令", 0x02: "ファイルシステム", 0x03: "MCU-FPGA 通信", 0x04: "角度センサ",
    0x10: "計画: 特異点", 0x11: "計画: 逆運動学の計算", 0x12: "計画: 逆運動学の限界", 0x13: "計画: 同じ点の繰り返し",
    0x14: "計画: 円弧の入力", 0x15: "計画: JUMP の設定",
    0x20: "動作中: 特異点", 0x21: "動作中: 逆運動学の計算", 0x22: "動作中: 逆運動学の限界（腕の届く範囲の外）",
    0x30: "J1 速度超過", 0x31: "J2 速度超過", 0x32: "J3 速度超過", 0x33: "J4 速度超過",
    0x40: "J1 の＋側の限界", 0x41: "J1 の−側の限界", 0x42: "J2 の＋側の限界", 0x43: "J2 の−側の限界",
    0x44: "J3 の＋側の限界", 0x45: "J3 の−側の限界", 0x46: "J4 の＋側の限界", 0x47: "J4 の−側の限界",
    0x48: "平行リンクの＋側の限界", 0x49: "平行リンクの−側の限界",
    0x50: "J1 脱調", 0x51: "J2 脱調", 0x52: "J3 脱調", 0x53: "J4 脱調",
}
# 「その目標へは行けない」という種類。止めたうえで解除してよい（脱調・速度超過などは人が確認してから）
RANGE_ALARMS = frozenset(range(0x10, 0x16)) | frozenset(range(0x20, 0x23)) | frozenset(range(0x40, 0x4A))

Pose = tuple[float, float, float, float]


class MotionStopped(Exception):
    pass


def violation(cfg: RobotConfig, x: float, y: float, z: float) -> float:
    """作業範囲からのはみ出し量（mm の合計）。範囲内なら 0。"""
    def outside(v: float, lo: float, hi: float) -> float:
        return max(lo - v, 0.0, v - hi)

    return (outside(x, cfg.x_min, cfg.x_max) + outside(y, cfg.y_min, cfg.y_max) + outside(z, cfg.z_min, cfg.z_max)
            + outside(math.hypot(x, y), cfg.reach_min, cfg.reach_max))


def jog_target(cfg: RobotConfig, pose: Pose, axis: str, delta_mm: float,
               tool_x: float = DEFAULT_TOOL_X_MM) -> tuple[float, float, float]:
    """今の位置から 1 歩ぶん動いた目標。範囲外に出る（または範囲外でさらに離れる）なら OutOfWorkspace。

    範囲は 2 つ: config の作業範囲（箱とリーチ環）と、関節の可動範囲。
    """
    xyz = list(pose[:3])
    xyz[AXES.index(axis)] += delta_mm
    after = violation(cfg, *xyz)
    if after > 0 and after >= violation(cfg, *pose[:3]):
        WorkspaceGuard(cfg).check(*xyz)  # 理由つきの OutOfWorkspace を上げる
    joint_after, reason = joint_violation(*xyz, tool_x)
    if joint_after > 0 and joint_after >= joint_violation(*pose[:3], tool_x)[0]:
        raise OutOfWorkspace(f"腕の可動範囲の端です（{reason}）")
    return xyz[0], xyz[1], xyz[2]


def decode_alarms(state: bytes) -> list[int]:
    """GetAlarmsState のビット列 → 立っているアラーム番号（バイト位置 × 8 + ビット位置）。"""
    return [i * 8 + bit for i, byte in enumerate(state) for bit in range(8) if byte >> bit & 1]


def describe_alarms(codes: list[int]) -> str:
    return "、".join(f"{ALARM_NAMES.get(c, '不明')}（0x{c:02X}）" for c in codes)


def joint_angles(x: float, y: float, z: float, tool_x: float = DEFAULT_TOOL_X_MM) -> dict[str, float] | None:
    """手先位置 → 関節角（度）。腕の長さでは届かない位置なら None。"""
    wrist_r = math.hypot(x, y) - tool_x
    d = math.hypot(wrist_r, z)
    if d == 0 or d > REAR_ARM_MM + FOREARM_MM or d < abs(REAR_ARM_MM - FOREARM_MM):
        return None
    cos_shoulder = (REAR_ARM_MM**2 + d**2 - FOREARM_MM**2) / (2 * REAR_ARM_MM * d)
    rear_elevation = math.atan2(z, wrist_r) + math.acos(max(-1.0, min(1.0, cos_shoulder)))
    elbow_r, elbow_z = REAR_ARM_MM * math.cos(rear_elevation), REAR_ARM_MM * math.sin(rear_elevation)
    return {"J1": math.degrees(math.atan2(y, x)),
            "J2": 90.0 - math.degrees(rear_elevation),
            "J3": math.degrees(math.atan2(elbow_z - z, wrist_r - elbow_r))}


def joint_violation(x: float, y: float, z: float, tool_x: float = DEFAULT_TOOL_X_MM) -> tuple[float, str]:
    """関節の可動範囲（余裕 JOINT_MARGIN_DEG 込み）からのはみ出し（度の合計）と、その説明。範囲内なら (0, "")。"""
    joints = joint_angles(x, y, z, tool_x)
    if joints is None:
        return math.inf, "腕の長さでは届かない位置"
    total, reasons = 0.0, []
    for name, (lo, hi) in JOINT_LIMITS_DEG.items():
        over = max(lo + JOINT_MARGIN_DEG - joints[name], 0.0, joints[name] - (hi - JOINT_MARGIN_DEG))
        if over > 0:
            total += over
            reasons.append(f"{name} が {joints[name]:.0f}°（範囲 {lo:.0f}〜{hi:.0f}°）")
    return total, "、".join(reasons)


class MagicianArm(MagicianSuction):
    """吸引に加えて、位置の読み取り・直線移動・停止・アラーム。"""

    def pose(self) -> Pose:
        params = self._command(ID_POSE)
        if len(params) < 16:
            raise RuntimeError(f"GetPose: 応答が短い ({len(params)} bytes)")
        x, y, z, r = struct.unpack_from("<4f", params, 0)
        return x, y, z, r

    def joints(self) -> tuple[float, float, float, float]:
        j1, j2, j3, j4 = struct.unpack_from("<4f", self._command(ID_POSE), 16)
        return j1, j2, j3, j4

    def tool_x(self) -> float:
        return struct.unpack_from("<f", self._command(ID_END_EFFECTOR_PARAMS), 0)[0]

    def alarms(self) -> list[int]:
        return decode_alarms(self._command(ID_ALARMS))

    def clear_alarms(self) -> None:
        self._command(ID_ALARMS, CTRL_WRITE_IMMEDIATE)

    def set_speed(self, percent: float) -> None:
        self._command(ID_PTP_COMMON_PARAMS, CTRL_WRITE_IMMEDIATE, struct.pack("<2f", percent, percent))

    def move_linear(self, x: float, y: float, z: float, r: float) -> None:
        """直線移動をキューに積んで実行を始める（到着は待たない）。"""
        self._command(ID_QUEUE_START, CTRL_WRITE_IMMEDIATE)
        self._command(ID_PTP_CMD, CTRL_WRITE_QUEUED, struct.pack("<B4f", PTP_MOVL_XYZ, x, y, z, r))

    def stop(self) -> None:
        self._command(ID_QUEUE_FORCE_STOP, CTRL_WRITE_IMMEDIATE)
        self._command(ID_QUEUE_CLEAR, CTRL_WRITE_IMMEDIATE)


class DryArm(DrySuction):
    """実機なし。移動は即座に終わったことにする。"""

    def __init__(self, pose: Pose = (200.0, 0.0, 50.0, 0.0)):
        super().__init__()
        self._pose = pose
        self.moves: list[Pose] = []
        self.stops = 0
        self.alarm_codes: list[int] = []

    def pose(self) -> Pose:
        return self._pose

    def joints(self) -> tuple[float, float, float, float]:
        j = joint_angles(*self._pose[:3]) or {"J1": 0.0, "J2": 0.0, "J3": 0.0}
        return j["J1"], j["J2"], j["J3"], 0.0

    def tool_x(self) -> float:
        return DEFAULT_TOOL_X_MM

    def alarms(self) -> list[int]:
        return list(self.alarm_codes)

    def clear_alarms(self) -> None:
        self.alarm_codes = []

    def set_speed(self, percent: float) -> None:
        self.speed = percent

    def move_linear(self, x: float, y: float, z: float, r: float) -> None:
        self.moves.append((x, y, z, r))
        self._pose = (x, y, z, r)

    def stop(self) -> None:
        self.stops += 1


class ArmWorker(SuctionWorker):
    """吸引のワーカーに、1 歩ずつの移動と停止を足したもの。機器を持つのはこのスレッドだけ。"""

    def __init__(self, dev, cfg: RobotConfig, speed_pct: float = DEFAULT_SPEED_PCT,
                 max_on_s: float = DEFAULT_MAX_ON_S, clock=time.monotonic, sleep=time.sleep,
                 log_path: Path | None = None):
        super().__init__(dev, max_on_s=max_on_s, clock=clock)
        self.cfg = cfg
        self.speed_pct = speed_pct
        self.log_path = log_path
        self.tool_x = dev.tool_x()
        self._alarms: list[int] = []
        self._sleep = sleep
        self._stop = threading.Event()
        self._stopped_at = -math.inf
        self._speed_sent = False
        self._pose: Pose | None = None
        self._moving = False

    def snapshot(self) -> dict:
        snap = super().snapshot()
        with self._lock:
            pose = self._pose
            snap["moving"] = self._moving
            snap["alarms"] = describe_alarms(self._alarms)
        snap["pose"] = None if pose is None else {k: round(v, 1) for k, v in zip("xyzr", pose, strict=True)}
        return snap

    def _report(self, note: str = "", error: str = "") -> None:
        with self._lock:
            self._note, self._error = note, error

    def _read_pose(self) -> Pose:
        pose = self.dev.pose()
        with self._lock:
            self._pose = pose
        return pose

    def _read_alarms(self) -> list[int]:
        codes = self.dev.alarms()
        with self._lock:
            changed, self._alarms = codes != self._alarms, codes
        if changed:
            self._log("alarms", codes=[f"0x{c:02X}" for c in codes], text=describe_alarms(codes))
        return codes

    def _log(self, event: str, **data) -> None:
        if self.log_path is None:
            return
        record = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **data}
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def tick(self) -> None:
        super().tick()
        try:
            self._read_pose()
            self._read_alarms()
        except Exception as e:  # noqa: BLE001  読めない理由を画面に出す
            self._report(error=f"位置を読めません: {type(e).__name__}: {e}")

    # --- HTTP スレッドから呼ぶ ---
    def jog(self, axis: str, delta_mm: float) -> None:
        asked_at = self._clock()
        try:
            self.submit(lambda: self._jog(axis, delta_mm, asked_at), wait_s=MOVE_TIMEOUT_S * 2)
        except MotionStopped:
            raise
        except Exception as e:
            self._report(error=str(e))
            raise

    def stop_motion(self) -> None:
        self._stop.set()  # 移動を待っているワーカーがすぐ気づく
        self.submit(self._do_stop)

    def clear_alarms(self) -> None:
        left = self.submit(self._clear_alarms)
        if left:
            raise RuntimeError(f"アラームが解除されません: {describe_alarms(left)}")
        self._report(note="アラームを解除しました")

    # --- ワーカースレッドで走る ---
    def _clear_alarms(self) -> list[int]:
        """解除を送り、読み返して残っているアラームを返す。"""
        self.dev.clear_alarms()
        return self._read_alarms()

    def _do_stop(self) -> None:
        self.dev.stop()
        self._stopped_at = self._clock()
        self._stop.clear()
        self._log("stop")
        self._report(note="停止しました")

    def _jog(self, axis: str, delta_mm: float, asked_at: float) -> None:
        if asked_at <= self._stopped_at:
            raise MotionStopped("停止の前に押された操作なので実行しません")
        pose = self._read_pose()
        record = {"axis": axis, "mm": delta_mm, "from": [round(v, 1) for v in pose],
                  "joints": [round(v, 1) for v in self.dev.joints()]}
        try:
            self._jog_from(pose, axis, delta_mm, record)
        except Exception as e:
            self._log("jog", **record, result=f"{type(e).__name__}: {e}")
            raise
        self._log("jog", **record, result="ok", to=[round(v, 1) for v in self._read_pose()])
        self._report()

    def _jog_from(self, pose: Pose, axis: str, delta_mm: float, record: dict) -> None:
        before = self._read_alarms()
        if before and (not set(before) <= RANGE_ALARMS or self._clear_alarms()):
            raise RuntimeError(f"アラームが出ているので動かしません: {describe_alarms(before)}。"
                               "原因を確かめてから「アラーム解除」を押してください")
        x, y, z = jog_target(self.cfg, pose, axis, delta_mm, self.tool_x)
        record["target"] = [x, y, z]
        if not self._speed_sent:
            self.dev.set_speed(self.speed_pct)
            self._speed_sent = True
        with self._lock:
            self._moving = True
        try:
            self.dev.move_linear(x, y, z, pose[3])
            self._wait_arrival((x, y, z))
        finally:
            with self._lock:
                self._moving = False

    def _wait_arrival(self, target: tuple[float, float, float]) -> None:
        deadline = self._clock() + MOVE_TIMEOUT_S
        while self._clock() < deadline:
            if self._stop.is_set():
                self._do_stop()
                raise MotionStopped("停止しました")
            if math.dist(self._read_pose()[:3], target) <= ARRIVE_TOL_MM:
                return
            codes = self._read_alarms()
            if codes:
                self._stop_on_alarm(codes)
            self._sleep(POLL_S)
        self._do_stop()
        raise RuntimeError("時間内に目標へ届かなかったので止めました。電源と、腕が何かに当たっていないかを確認してください")

    def _stop_on_alarm(self, codes: list[int]) -> None:
        """本体がアラームで止めた。キューを止め、「行けない目標」の種類なら解除して、理由を上げる。"""
        self._do_stop()
        text = describe_alarms(codes)
        if set(codes) <= RANGE_ALARMS and not self._clear_alarms():
            raise RuntimeError(f"その向きは腕の可動範囲の端なので止めました（{text}）。アラームは解除済みです。ほかの向きへ動かせます")
        raise RuntimeError(f"アラームで止まりました: {text}。原因を確かめてから「アラーム解除」を押してください")

    def shutdown(self) -> None:
        try:
            super().shutdown()
        finally:
            with contextlib.suppress(Exception):  # 終了時の停止は念のため。失敗しても OFF は送り終えている
                self.dev.stop()


PAGE_HTML = """<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Magician 操作</title>
<style>
 body{margin:0;min-height:100vh;font-family:'Segoe UI','Hiragino Sans',Meiryo,sans-serif;background:#1b1f24;color:#e6e6e6;
      display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;padding:16px;box-sizing:border-box}
 main{display:flex;gap:40px;flex-wrap:wrap;justify-content:center;align-items:center}
 h3{margin:0 0 10px;font-size:14px;color:#9fb3c8;font-weight:normal;text-align:center}
 button{color:#fff;border:0;border-radius:14px;cursor:pointer;font-weight:bold;font-size:22px;background:#3a4656;
        touch-action:none;user-select:none;-webkit-user-select:none}
 button:active,button.held{background:#3498db}
 .pad{display:grid;grid-template-columns:repeat(3,92px);grid-template-rows:repeat(3,92px);gap:8px}
 .zcol{display:grid;grid-template-rows:repeat(2,92px);gap:8px;width:92px;align-content:center}
 .pad small,.zcol small{display:block;font-size:12px;font-weight:normal;color:#b8c4d2}
 #stop{background:#c0392b;font-size:20px}
 .steps{display:flex;gap:6px;justify-content:center;margin-top:12px}
 .steps button{font-size:15px;padding:8px 12px;border-radius:8px;opacity:.6}
 .steps button.sel{opacity:1;background:#3498db}
 .suc{display:grid;gap:10px}
 .suc button{width:190px;height:92px;font-size:26px;opacity:.55}
 .suc button.active{opacity:1;outline:4px solid #fff}
 #on{background:#2e9d57}#off{background:#4a5568}
 #sstate{font-size:28px;font-weight:bold;text-align:center}
 .on{color:#7bd88f}.off{color:#8a94a3}.ng{color:#ff5c5c}
 #pose{font-size:20px;font-variant-numeric:tabular-nums;color:#cfd8e3}
 #info{font-size:16px;min-height:1.4em;text-align:center;color:#9fb3c8}
 #keys,label{font-size:13px;color:#6b7686}
 #clear{font-size:14px;padding:8px 12px;border-radius:8px;display:none}
</style></head><body>
<main>
 <section><h3>前後・左右</h3>
  <div class="pad">
   <span></span><button data-axis="x" data-sign="1">▲<small>前</small></button><span></span>
   <button data-axis="y" data-sign="1">◀<small>左</small></button><button id="stop">停止</button><button data-axis="y" data-sign="-1">▶<small>右</small></button>
   <span></span><button data-axis="x" data-sign="-1">▼<small>後ろ</small></button><span></span>
  </div>
  <div class="steps" id="steps"></div>
 </section>
 <section><h3>上下</h3>
  <div class="zcol">
   <button data-axis="z" data-sign="1">▲<small>上</small></button>
   <button data-axis="z" data-sign="-1">▼<small>下</small></button>
  </div>
 </section>
 <section><h3>吸引</h3>
  <div class="suc">
   <div id="sstate" class="off">-</div>
   <button id="on">吸引 ON</button>
   <button id="off">吸引 OFF</button>
  </div>
 </section>
</main>
<div id="pose">-</div>
<div id="info"></div>
<button id="clear">アラーム解除</button>
<label><input type="checkbox" id="flip"> ロボットと向かい合って操作する（前後・左右を反転）</label>
<div id="keys">矢印キー: 前後左右 ／ W・S: 上下 ／ スペース: 吸引の切り替え ／ Esc: 停止 ／ 押し続けると 1 歩ずつ繰り返す</div>
<script>
const STEPS=[1,5,10,20];let step=5,isOn=false,held=null,running=false,sticky='';
const $=id=>document.getElementById(id);
function store(k,v){try{if(v===undefined)return localStorage.getItem(k);localStorage.setItem(k,v);}catch(e){return null;}}
function drawSteps(){$('steps').innerHTML=STEPS.map(s=>'<button class="'+(s===step?'sel':'')+'" onclick="setStep('+s+')">'+s+' mm</button>').join('');}
function setStep(s){step=s;store('step',s);drawSteps();}
function show(s){
 if(s.on!==undefined){isOn=!!s.on;$('sstate').textContent=isOn?'吸引中':'停止';$('sstate').className=isOn?'on':'off';
  $('on').classList.toggle('active',isOn);$('off').classList.toggle('active',!isOn);}
 if(s.pose)$('pose').textContent='X '+s.pose.x.toFixed(1)+'　Y '+s.pose.y.toFixed(1)+'　Z '+s.pose.z.toFixed(1)+' mm';
 let info=s.error||sticky||(s.alarms?'アラーム: '+s.alarms:'')||s.note||'';
 if(!info&&isOn)info='吸引 '+Math.round(s.on_for_s)+' 秒'+(s.remaining_s==null?'':'（あと '+s.remaining_s+' 秒で自動 OFF）');
 $('info').textContent=info;$('info').className=(s.error||sticky||s.alarms)?'ng':'';
 if(s.alarms!==undefined)$('clear').style.display=s.alarms?'inline-block':'none';
}
async function post(path,body){
 try{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  const s=await r.json();sticky=r.ok?'':(s.error||'失敗しました');show(s);return r.ok;}
 catch(e){sticky='画面のサーバーに届きません（jog.py は動いていますか）';show({});return false;}
}
async function run(){
 if(running)return;running=true;
 while(held){const h=held,flip=(h.axis!=='z'&&$('flip').checked)?-1:1;
  if(!await post('/jog',{axis:h.axis,mm:h.sign*flip*step}))release();}
 running=false;
}
function press(axis,sign,el){held={axis:axis,sign:sign,el:el};if(el)el.classList.add('held');run();}
function release(){if(held&&held.el)held.el.classList.remove('held');held=null;}
function stopNow(){release();post('/stop');}
document.querySelectorAll('button[data-axis]').forEach(b=>{
 b.addEventListener('pointerdown',e=>{e.preventDefault();press(b.dataset.axis,Number(b.dataset.sign),b);});
 ['pointerup','pointerleave','pointercancel'].forEach(t=>b.addEventListener(t,release));
});
$('stop').onclick=stopNow;$('on').onclick=()=>post('/suction',{on:true});$('off').onclick=()=>post('/suction',{on:false});
$('clear').onclick=()=>post('/clear_alarms');
$('flip').checked=store('flip')==='1';$('flip').onchange=()=>store('flip',$('flip').checked?'1':'0');
const KEYS={ArrowUp:['x',1],ArrowDown:['x',-1],ArrowLeft:['y',1],ArrowRight:['y',-1],w:['z',1],W:['z',1],s:['z',-1],S:['z',-1]};
document.addEventListener('keydown',e=>{
 if(e.key==='Escape'){stopNow();return;}
 if(e.code==='Space'){e.preventDefault();if(!e.repeat)post('/suction',{on:!isOn});return;}
 const k=KEYS[e.key];if(!k)return;e.preventDefault();if(!e.repeat)press(k[0],k[1],null);
});
document.addEventListener('keyup',e=>{if(KEYS[e.key])release();});
window.addEventListener('blur',release);
async function poll(){try{show(await (await fetch('/status')).json());}catch(e){sticky='画面のサーバーに届きません（jog.py は動いていますか）';show({});}}
const saved=Number(store('step'));if(STEPS.includes(saved))step=saved;
drawSteps();setInterval(poll,400);poll();
</script></body></html>"""


def make_panel(worker: ArmWorker, port: int = DEFAULT_HTTP_PORT) -> SuctionPanel:
    def post_jog(body: dict) -> None:
        axis, mm = body.get("axis"), body.get("mm")
        if axis not in AXES or isinstance(mm, bool) or not isinstance(mm, int | float) or abs(mm) not in STEPS_MM:
            raise ValueError(f'{{"axis": {"|".join(AXES)}, "mm": ±{"|".join(map(str, STEPS_MM))}}} が必要')
        try:
            worker.jog(axis, float(mm))
        except OutOfWorkspace as e:
            raise RuntimeError(f"作業範囲の外なので動かしません: {e}") from e

    return SuctionPanel(worker, port=port, page=PAGE_HTML, posts={
        "/jog": post_jog,
        "/stop": lambda body: worker.stop_motion(),
        "/clear_alarms": lambda body: worker.clear_alarms(),
    })


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Dobot Magician の撮影用操作画面（1 歩ずつの移動と吸引 ON/OFF。腕が動く）")
    ap.add_argument("--port", default=None, help="シリアルポート（Mac: /dev/cu.usbserial-*・Windows: COMn）")
    ap.add_argument("--config", default=str(HERE / "config.json"), help="作業範囲と、--port が無いときの robot.port")
    ap.add_argument("--dry", action="store_true", help="実機なし")
    ap.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT)
    ap.add_argument("--speed", type=float, default=DEFAULT_SPEED_PCT, help="移動の速度と加速度（%%・1〜100）")
    ap.add_argument("--max-on-s", type=float, default=DEFAULT_MAX_ON_S, help="吸引の連続 ON の上限秒（0 で無効）")
    ap.add_argument("--no-open", action="store_true", help="ブラウザを自動で開かない")
    args = ap.parse_args(argv)
    if not 1 <= args.speed <= 100:
        ap.error("--speed は 1〜100")

    config_path = Path(args.config)
    cfg = AppConfig.load(config_path).robot if config_path.exists() else AppConfig.default().robot
    dev = DryArm() if args.dry else MagicianArm.open(resolve_port(args.port, config_path))
    try:
        x, y, z, _r = dev.pose()
        print(f"接続: Magician SN {dev.device_sn()}  位置 x={x:.1f} y={y:.1f} z={z:.1f}")
        log_path = None
        if not args.dry:
            (HERE / "logs").mkdir(exist_ok=True)
            log_path = HERE / "logs" / f"jog_{time.strftime('%Y%m%d')}.jsonl"
        worker = ArmWorker(dev, cfg, speed_pct=args.speed, max_on_s=args.max_on_s, log_path=log_path)
        serve(worker, make_panel(worker, args.http_port), not args.no_open)
    finally:
        dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

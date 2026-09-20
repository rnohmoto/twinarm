#!/usr/bin/env python3
"""One Koch pair: teleop + current telemetry + leader force feedback (koch4 build).

mock/v0/koch_teleop_plus.py（twinarm main）・robotics 版（2026-08-04: 差分反射式
`--ff-style error`・パネルにカメラ `--cams`・全リトライ注入）・branch
rn/fix/gripper-force-feedback（2026-09-04: 実機レジスタ計測で確認した修正3点）を
1本に合流させた koch4 用の版。加えて:

  * `--config-dir` / `--work-dir`: 較正 JSON・設定・ログを専用フォルダに置く
    （lerobot の calibration_dir を明示指定。~/.cache に散らばらない）
  * `--follower-port none`: リーダーのみ（VR 仮想反力デモ・フォロワー不要）
  * `--ff vwall`: 仮想物体（VR ブリッジ or `--wall` 固定指定）の反力。壁の描画は
    サーボ内部の位置ループに任せ（Operating_Mode 5・Goal_Position=壁・Goal_Current=上限・
    Position_P_Gain=硬さ）、ホストは「壁に触れているか」だけを 30fps で切り替える
  * 制御則（spring / error / virtual wall / 温度ガード）は
    twinarm/src/twinarm/domain/gripper_feedback.py の写し。descovery は独立スクリプトの
    規則なので import しない。変更するときは両方を直し、twinarm 側のテストで固定する

力覚 FB の3方式:
  --ff gripper --ff-style spring  旧 ALOHA 式（既定）。フォロワー電流→リーダー戻りバネ電流。
      9/4 修正: Position_P_Gain=800（既定のままでは Goal_Current を上げても押し返さない・実測）
      / 再接続時は初回の基準を引き継ぐ / 基準=較正済みの開端（range_max）
  --ff gripper --ff-style error   差分反射式（robotics 8/04）。L指令−F実位置を反力に。
      フォロワー停止時のみ・不感帯・スルーレート制限。自由空間は完全フリー
  --ff arm                        上に腕3軸反力（FACTR 式）を足す。実機未検証（67_ §3-3）
  --ff vwall                      仮想物体。フォロワー不要。壁= {name,width,p_gain,cap_ma,release}

安全（変更しない値の根拠は robotics web_research/bilateral_force_feedback_koch_20260803.md）:
  - Goal_Current 上限は既定 450mA（M077 ストール 1.47A@5V の ~30%）。最大 900
  - 温度ガード: 60°C でゲイン半減・65°C で FB 停止（Temperature Limit 70°C 手前）
  - 実機を動かす・トルクを抜くのはユーザーが明示的に頼んだときだけ（AGENTS.md）

使い方（Mac・descovery で `uv run`）:
  uv run python koch4/koch4_teleop.py \
      --follower-port /dev/tty.usbmodemXXXX --follower-id koch_follower_A \
      --leader-port   /dev/tty.usbmodemYYYY --leader-id   koch_leader_A \
      --ff gripper --panel --csv
  uv run python koch4/koch4_teleop.py --leader-port /dev/tty.usbmodemYYYY \
      --follower-port none --ff vwall --wall ball:45:800:300      # 壁を固定して手で確認
  python koch4_teleop.py --selftest       # 制御則の自己診断（ハード・lerobot 不要）
"""

import argparse
import csv
import inspect
import json
import logging
import math
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):  # Windows cp932 console: never crash on symbols
    _reconfigure(errors="replace")

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = HERE / "config"
DEFAULT_WORK_DIR = HERE / "work"
LEADER_ONLY = "none"

XL430_JOINTS = {"shoulder_pan", "shoulder_lift"}  # フォロワーの XL430（負荷 0.1%/unit）
ARM_FF_JOINTS = ["elbow_flex", "wrist_flex", "wrist_roll"]  # 電流が読める XL330 の腕3軸
VW_JOINTS = [
    "shoulder_lift",
    "elbow_flex",
]  # 仮想重さを返すリーダー腕関節(どちらも XL330-M077)
JOINT_SPAN_DEG = {
    "shoulder_pan": 180.0,
    "shoulder_lift": 100.0,
    "elbow_flex": 100.0,
    "wrist_flex": 100.0,
    "wrist_roll": 180.0,
}
LINK_M = (0.11, 0.108, 0.115)  # 上腕・前腕・手首＋グリッパ先端まで(分身モデルと同じ)
G_MPS2 = 9.81
KT_NM_PER_A = 0.146  # XL330-M077 5V ストール値(0.215 N*m / 1.47 A)
# 位置ループ剛性。既定のままでは偏差 158 ticks で ~124mA しか要求せず、Goal_Current を
# 450 まで上げても実電流が頭打ち（9/4 実測）。Operating_Mode を書くと既定へ戻るので
# 必ずモード設定の後に書く。
FF_GRIPPER_P_GAIN = 800
FF_CAP_MAX_MA = 900
FF_CAP_WARN_MA = 500
ARM_CAP_MAX_MA = 400
FREE_SPACE_P_GAIN = 800
STALL_FRAMES_MAX = 10
MAX_RECONNECTS = 20
TEMP_CHECK_EVERY = 60  # frames (2 s at 30 fps)
WALL_TTL_SEC = (
    3.0  # VR ブリッジからの壁更新が途絶えたら壁を解除する秒数(固定壁は無期限)
)
TELEMETRY_HOST = "127.0.0.1"
HW_ERROR_BITS = {
    0: "入力電圧",
    2: "過熱",
    3: "エンコーダ",
    4: "電気ショック",
    5: "過負荷",
}
ALERT_TTL_SEC = 4.0


class _DropClampWarning(logging.Filter):
    """Silence lerobot's per-frame clamp warning (harmless, caused by pose gaps)."""

    def filter(self, record):
        """Drop records that mention the safety clamp."""
        return "clamped to be safe" not in str(record.getMessage())


logging.getLogger().addFilter(_DropClampWarning())


# ==================================================================== laws
# twinarm/src/twinarm/domain/gripper_feedback.py の写し（同期を保つこと）


@dataclass(frozen=True)
class SpringLaw:
    """Follower current -> leader return-spring current (Operating_Mode 5)."""

    gain: float = 1.5
    floor_ma: int = 60
    cap_ma: int = 450
    deadband_ma: int = 25
    alpha: float = 0.2


@dataclass(frozen=True)
class SpringState:
    """Smoothed follower current and the last commanded current."""

    ema: float = 0.0
    command_ma: int = 0


def spring_step(law, state, follower_current_ma):
    """Advance the spring law by one frame."""
    magnitude = max(abs(follower_current_ma) - law.deadband_ma, 0.0)
    ema = (1.0 - law.alpha) * state.ema + law.alpha * magnitude
    command = int(min(law.floor_ma + law.gain * ema, law.cap_ma))
    return SpringState(ema=ema, command_ma=command)


@dataclass(frozen=True)
class ErrorReflectionLaw:
    """Leader-minus-follower position error -> signed leader current (mode 0)."""

    ke: float = 9.0
    deadband: float = 6.0
    sign: int = 1
    cap_ma: int = 450
    slew_ma: int = 90
    move_eps: float = 0.4
    stall_frames: int = 3
    alpha: float = 0.3
    release: float = 0.5


@dataclass(frozen=True)
class ErrorReflectionState:
    """Stall detector state, smoothed target and the last commanded current."""

    prev_follower_pos: float | None = None
    stall_frames: int = 0
    ema: float = 0.0
    command_ma: int = 0


def error_reflection_step(law, state, leader_cmd, follower_pos):
    """Advance the error-reflection law by one frame."""
    moving = (
        state.prev_follower_pos is not None
        and abs(follower_pos - state.prev_follower_pos) > law.move_eps
    )
    stall = 0 if moving else min(state.stall_frames + 1, STALL_FRAMES_MAX)
    error = leader_cmd - follower_pos
    if abs(error) < law.deadband or stall < law.stall_frames:
        target = 0.0
    else:
        over = error - (law.deadband if error > 0 else -law.deadband)
        target = -law.ke * over * law.sign
    ema = (1.0 - law.alpha) * state.ema + law.alpha * target
    if target == 0.0:
        ema *= law.release
    raw = max(min(ema, law.cap_ma), -law.cap_ma)
    command = int(
        max(
            min(raw, state.command_ma + law.slew_ma),
            state.command_ma - law.slew_ma,
        )
    )
    return ErrorReflectionState(
        prev_follower_pos=follower_pos, stall_frames=stall, ema=ema, command_ma=command
    )


@dataclass(frozen=True)
class VirtualWall:
    """A virtual object the leader gripper can close on (normalized width)."""

    name: str
    width: float
    p_gain: int = 800
    cap_ma: int = 300
    release: float = 1.0
    mass_g: float = 0.0


@dataclass(frozen=True)
class WallCommand:
    """Registers to write on the leader gripper for one frame."""

    goal_position_tick: int
    goal_current_ma: int
    p_gain: int
    engaged: bool


def wall_tick(width, closed_tick, open_tick):
    """Map a normalized opening (0-100) to a raw tick between the two ends."""
    fraction = min(max(width, 0.0), 100.0) / 100.0
    return round(closed_tick + fraction * (open_tick - closed_tick))


def virtual_wall_step(wall, opening, closed_tick, open_tick, engaged):
    """Decide whether the wall is engaged and what to write to the servo."""
    if wall is None:
        return WallCommand(open_tick, 0, FREE_SPACE_P_GAIN, False)
    limit = wall.width + (wall.release if engaged else 0.0)
    now_engaged = opening <= limit
    return WallCommand(
        goal_position_tick=wall_tick(wall.width, closed_tick, open_tick),
        goal_current_ma=wall.cap_ma if now_engaged else 0,
        p_gain=wall.p_gain,
        engaged=now_engaged,
    )


@dataclass(frozen=True)
class ThermalGuard:
    """Servo temperature policy: derate at one threshold, stop at another."""

    derate_c: int = 60
    stop_c: int = 65
    min_gain: float = 0.1


def thermal_derate(guard, temp_c, gain):
    """Return the gain to use and whether feedback must stop."""
    if temp_c >= guard.stop_c:
        return gain, True
    if temp_c >= guard.derate_c:
        return max(gain * 0.5, guard.min_gain), False
    return gain, False


@dataclass(frozen=True)
class JointMap:
    """How a normalized joint value (+-100) becomes a display/model angle."""

    span_deg: float
    offset_deg: float = 0.0
    sign: int = 1


def norm_to_rad(value, joint_map):
    """Map a normalized joint value to radians with the joint's span, offset and sign."""
    degrees = value / 100.0 * joint_map.span_deg / 2.0 + joint_map.offset_deg
    return joint_map.sign * math.radians(degrees)


def tip_levers(lift_rad, elbow_rad, wrist_rad, links=LINK_M):
    """Horizontal lever arms [m] of the tip about shoulder_lift and elbow (0 rad = up)."""
    a1 = lift_rad
    a2 = a1 + elbow_rad
    a3 = a2 + wrist_rad
    x1 = links[0] * math.sin(a1)
    x2 = x1 + links[1] * math.sin(a2)
    x3 = x2 + links[2] * math.sin(a3)
    return x3, x3 - x1


@dataclass(frozen=True)
class VirtualWeightLaw:
    """Render a grasped object's weight as current on the leader's lift and elbow."""

    scale: float = 0.12
    cap_ma: int = 120
    alpha: float = 0.2
    release: float = 0.5
    invert_shoulder: bool = False
    invert_elbow: bool = False


@dataclass(frozen=True)
class VirtualWeightState:
    """Smoothed currents [mA] for shoulder_lift and elbow_flex."""

    shoulder_ma: float = 0.0
    elbow_ma: float = 0.0


def _weight_target_ma(law, torque_nm, invert):
    physical_ma = torque_nm / KT_NM_PER_A * 1000.0
    target = max(min(physical_ma * law.scale, law.cap_ma), -law.cap_ma)
    return -target if invert else target


def virtual_weight_step(law, state, engaged, mass_g, levers_m):
    """Advance the weight law by one frame; releases decay faster than they rise."""
    if engaged and mass_g > 0.0:
        force_n = mass_g / 1000.0 * G_MPS2
        target_s = _weight_target_ma(law, force_n * levers_m[0], law.invert_shoulder)
        target_e = _weight_target_ma(law, force_n * levers_m[1], law.invert_elbow)
        alpha = law.alpha
    else:
        target_s = target_e = 0.0
        alpha = law.release
    return VirtualWeightState(
        shoulder_ma=(1.0 - alpha) * state.shoulder_ma + alpha * target_s,
        elbow_ma=(1.0 - alpha) * state.elbow_ma + alpha * target_e,
    )


# ================================================================ helpers


def parse_wall(spec):
    """Parse ``name:width[:p_gain[:cap_ma[:release[:mass_g]]]]`` into a VirtualWall."""
    parts = spec.split(":")
    if len(parts) < 2:
        raise argparse.ArgumentTypeError(
            "--wall は name:width[:p_gain[:cap_ma[:release[:mass_g]]]]"
        )
    name, width = parts[0], float(parts[1])
    p_gain = int(parts[2]) if len(parts) > 2 else 800
    cap = int(parts[3]) if len(parts) > 3 else 300
    release = float(parts[4]) if len(parts) > 4 else 1.0
    mass_g = float(parts[5]) if len(parts) > 5 else 0.0
    return wall_from_fields(name, width, p_gain, cap, release, mass_g)


def wall_from_fields(name, width, p_gain, cap_ma, release, mass_g=0.0):
    """Build a VirtualWall with every field clamped to a safe range."""
    return VirtualWall(
        name=str(name)[:32],
        width=min(max(float(width), 0.0), 100.0),
        p_gain=int(min(max(int(p_gain), 0), 16383)),
        cap_ma=int(min(max(int(cap_ma), 0), FF_CAP_MAX_MA)),
        release=min(max(float(release), 0.0), 20.0),
        mass_g=min(max(float(mass_g), 0.0), 2000.0),
    )


def wall_from_message(payload):
    """Turn the ``vwall`` field of a control message into a VirtualWall or None."""
    if not payload:
        return None
    return wall_from_fields(
        payload.get("name", "obj"),
        payload.get("width", 50.0),
        payload.get("p_gain", 800),
        payload.get("cap_ma", 300),
        payload.get("release", 1.0),
        payload.get("mass_g", 0.0),
    )


def selftest():
    """Run the mirrored laws on fixed vectors (no hardware, no lerobot)."""
    spring = spring_step(SpringLaw(), SpringState(), 525.0)
    assert spring.command_ma == 210, spring
    state = ErrorReflectionState()
    for _ in range(5):
        state = error_reflection_step(ErrorReflectionLaw(), state, 50.0, 20.0)
    assert state.command_ma < 0, state
    wall = VirtualWall("ball", 45.0, 800, 300)
    free = virtual_wall_step(wall, 70.0, 2036, 2773, False)
    hit = virtual_wall_step(wall, 44.0, 2036, 2773, False)
    assert not free.engaged and free.goal_current_ma == 0, free
    assert hit.engaged and hit.goal_current_ma == 300, hit
    assert wall_tick(50.0, 2000, 2800) == 2400
    assert thermal_derate(ThermalGuard(), 65, 1.5) == (1.5, True)
    levers = tip_levers(math.pi / 2, 0.0, 0.0)
    assert abs(levers[0] - sum(LINK_M)) < 1e-9, levers
    vw = VirtualWeightLaw(scale=0.12, cap_ma=120, alpha=1.0)
    w = virtual_weight_step(vw, VirtualWeightState(), True, 100.0, (0.2, 0.1))
    assert w.shoulder_ma == 120.0 and 80.0 < w.elbow_ma < 81.0, w
    print(
        "selftest OK: spring/error/vwall/weight/thermal 制御則は twinarm domain と同じ値"
    )


def to_signed16(v):
    """Interpret a 16-bit register value as signed."""
    return v - 65536 if v > 32767 else v


def load_lerobot():
    """Import lerobot lazily, injecting bus retries (lerobot 0.5+: retry=0 default)."""
    from lerobot.motors.motors_bus import MotorsBus

    def inject_retry(name, n):
        orig = getattr(MotorsBus, name)
        if "num_retry" not in inspect.signature(orig).parameters:
            return

        def patched(self, *args, **kwargs):
            kwargs["num_retry"] = max(int(kwargs.get("num_retry", 0) or 0), n)
            return orig(self, *args, **kwargs)

        setattr(MotorsBus, name, patched)

    for nm, n in (("sync_read", 10), ("read", 5), ("write", 5), ("sync_write", 5)):
        inject_retry(nm, n)

    from lerobot.robots.koch_follower import KochFollower, KochFollowerConfig
    from lerobot.teleoperators.koch_leader import KochLeader, KochLeaderConfig

    try:
        from lerobot.errors import DeviceNotConnectedError

        retry_errors = (ConnectionError, DeviceNotConnectedError)
    except Exception:  # noqa: BLE001 - older lerobot without the error module
        retry_errors = (ConnectionError,)
    return KochFollower, KochFollowerConfig, KochLeader, KochLeaderConfig, retry_errors


def gripper_ticks(bus):
    """Return (closed_tick, open_tick) from calibration, or None if uncalibrated.

    実測（較正値 2036-2773 に対し握り込み時の生値 2106）: 握ると raw は range_min 方向へ
    減る。したがって range_max が全開端、range_min が閉端（9/4 commit）。
    """
    cal = getattr(bus, "calibration", None) or {}
    g = cal.get("gripper")
    if g is None:
        return None
    return int(g.range_min), int(g.range_max)


def setup_gripper_ff(
    leader, cap_ma, anchor=None, floor_ma=60, p_gain=FF_GRIPPER_P_GAIN
):
    """Put the leader gripper in current-based position mode holding an anchor.

    anchor を渡すとその位置を保持目標にする（再接続では初回の基準を引き継ぐ: 通信断の
    瞬間に握っていた位置を基準にすると、手を離してもそこへ引き戻されるため）。
    """
    bus = leader.bus
    bus.write("Torque_Enable", "gripper", 0, normalize=False)
    bus.write("Operating_Mode", "gripper", 5, normalize=False)  # current-based position
    # モード書き込みでリセットされるので、この順序は動かさないこと
    bus.write("Position_P_Gain", "gripper", p_gain, normalize=False)
    bus.write("Torque_Enable", "gripper", 1, normalize=False)
    open_pos, src = anchor, "再接続で引き継ぎ"
    if open_pos is None:
        ticks = gripper_ticks(bus)
        if ticks is not None:
            open_pos, src = ticks[1], "キャリブレーション全開端"
    if open_pos is None:
        open_pos = bus.read("Present_Position", "gripper", normalize=False)
        src = "起動時位置(較正なし)"
    bus.write("Goal_Position", "gripper", int(open_pos), normalize=False)
    bus.write("Goal_Current", "gripper", int(floor_ma), normalize=False)
    print(
        f"[ff] リーダーgripper: current-based position mode / "
        f"開位置={int(open_pos)}({src}) / 床{floor_ma}mA / 上限{cap_ma}mA"
    )
    mode = int(bus.read("Operating_Mode", "gripper", normalize=False))
    trq = int(bus.read("Torque_Enable", "gripper", normalize=False))
    pgain = int(bus.read("Position_P_Gain", "gripper", normalize=False))
    print(
        f"[ff] 設定確認: Operating_Mode={mode}(期待5) / Torque_Enable={trq}(期待1) "
        f"/ Position_P_Gain={pgain}(期待{p_gain})"
    )
    if mode != 5 or trq != 1 or pgain != p_gain:
        print(
            "[ff] ⚠ モード/トルク/ゲインが未反映 — 握り返しは効きません(この行をClaudeへ)"
        )
    return int(open_pos)


def setup_gripper_ff_error(leader, cap_ma):
    """Put the leader gripper in current control (mode 0), free when idle."""
    bus = leader.bus
    bus.write("Torque_Enable", "gripper", 0, normalize=False)
    bus.write("Operating_Mode", "gripper", 0, normalize=False)  # current control
    bus.write("Torque_Enable", "gripper", 1, normalize=False)
    bus.write("Goal_Current", "gripper", 0, normalize=False)
    mode = int(bus.read("Operating_Mode", "gripper", normalize=False))
    trq = int(bus.read("Torque_Enable", "gripper", normalize=False))
    print(
        f"[ff] リーダーgripper: 差分反射モード(L指令−F実位置を反力に) / 上限±{cap_ma}mA / "
        f"mode={mode}(期待0) trq={trq}(期待1)"
    )
    if mode != 0 or trq != 1:
        print("[ff] ⚠ モード/トルクが未反映 — 握り返しは効きません(この行をClaudeへ)")


def arm_gripper_ff(leader, args, anchor=None):
    """Set up the gripper for the selected style; returns the anchor tick or None."""
    if args.ff == "vwall":
        return setup_gripper_ff(
            leader, args.ff_cap, anchor, floor_ma=0, p_gain=args.ff_pgain
        )
    if args.ff_style == "error":
        setup_gripper_ff_error(leader, args.ff_cap)
        return None
    return setup_gripper_ff(
        leader, args.ff_cap, anchor, floor_ma=args.ff_floor, p_gain=args.ff_pgain
    )


def setup_arm_ff(leader, joints):
    """Put the leader arm joints in current control with zero current."""
    bus = leader.bus
    for j in joints:
        bus.write("Torque_Enable", j, 0, normalize=False)
        bus.write("Operating_Mode", j, 0, normalize=False)  # current control mode
        bus.write("Torque_Enable", j, 1, normalize=False)
        bus.write("Goal_Current", j, 0, normalize=False)
    modes = [int(bus.read("Operating_Mode", j, normalize=False)) for j in joints]
    print(f"[ff-arm] 腕反力ON {joints} Operating_Mode={modes}(期待[0, 0, 0])")


def release_gripper(bus):
    """Zero the gripper current and drop its torque (safe exit)."""
    bus.write("Goal_Current", "gripper", 0, normalize=False)
    bus.write("Torque_Enable", "gripper", 0, normalize=False)


def release_arm(bus, joints=ARM_FF_JOINTS):
    """Zero the arm-joint currents and drop their torque (safe exit)."""
    for j in joints:
        bus.write("Goal_Current", j, 0, normalize=False)
        bus.write("Torque_Enable", j, 0, normalize=False)


def apply_follower_grip_cap(robot, cap_ma):
    """Bound the follower gripper's Goal_Current (mode 5 = torque limit) if asked.

    フォロワー gripper は lerobot が Current-based Position Mode にするが Goal_Current は
    書かない(電源投入時の値のまま)。物を掴んで位置偏差が残ると電流が張り付き、
    過負荷停止(Hardware_Error bit5)→落下になるので、上限を明示する。
    """
    if robot is None or cap_ma is None:
        return
    cap = int(min(max(cap_ma, 0), 1750))
    robot.bus.write("Goal_Current", "gripper", cap, normalize=False)
    back = int(robot.bus.read("Goal_Current", "gripper", normalize=False))
    print(
        f"[grip] フォロワーgripper Goal_Current={back}mA(期待{cap}) — 過負荷停止の予防"
    )


# ===================================================== wireless follower (UDP)
# link 判定は twinarm/src/twinarm/domain/link_watchdog.py の写し（同期を保つこと）

FOLLOWER_MOTORS = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]
LINK_HELLO_TIMEOUT_SEC = 3.0
LINK_RX_TIMEOUT_SEC = 0.2


@dataclass(frozen=True)
class LinkPolicy:
    """Ages [s] at which the follower link is downgraded."""

    alert_after_s: float = 0.3
    hold_after_s: float = 0.5
    stale_after_s: float = 3.0


DEFAULT_LINK_POLICY = LinkPolicy()


@dataclass(frozen=True)
class LinkState:
    """Bookkeeping of what arrived from the host: last sequence, time, counters."""

    last_seq: int = -1
    last_rx: float = -math.inf
    received: int = 0
    dropped: int = 0
    missed: int = 0


def accept_packet(state, seq, now):
    """Register a packet; only a newer sequence number is accepted."""
    if seq <= state.last_seq:
        return replace(state, dropped=state.dropped + 1), False
    gap = seq - state.last_seq - 1 if state.last_seq >= 0 else 0
    return LinkState(
        seq, now, state.received + 1, state.dropped, state.missed + gap
    ), True


def link_health(state, now, policy=DEFAULT_LINK_POLICY):
    """Grade the link by the age of the last accepted packet."""
    age = now - state.last_rx
    if state.received == 0 or age >= policy.stale_after_s:
        return "stale"
    if age >= policy.hold_after_s:
        return "hold"
    if age >= policy.alert_after_s:
        return "alert"
    return "ok"


def loss_ratio(state):
    """Fraction of packets that never arrived."""
    total = state.received + state.missed
    return state.missed / total if total else 0.0


class RemoteBus:
    """The subset of MotorsBus the frame loop uses, answered from the host's last state."""

    def __init__(self, owner):
        self._owner = owner
        self.motors = list(FOLLOWER_MOTORS)

    def sync_read(self, data_name, motors=None, *, normalize=True, num_retry=0):
        """Positions, raw currents and error bits from the last state message."""
        state = self._owner.fresh_state()
        if data_name == "Present_Position":
            return {m: float(v) for m, v in state.get("fpos", {}).items()}
        if data_name == "Present_Current":
            return {m: int(v) for m, v in state.get("cur", {}).items()}
        if data_name == "Hardware_Error_Status":
            return {m: int(v) for m, v in (state.get("hw") or {}).items()}
        raise KeyError(data_name)

    def read(self, data_name, motor, *, normalize=True, num_retry=0):
        """Temperature, gripper cap and error bits of one motor."""
        state = self._owner.fresh_state()
        if data_name == "Present_Temperature":
            return int((state.get("temp") or {}).get(motor, 0))
        if data_name == "Goal_Current":
            return int(self._owner.grip_ma or 0)
        if data_name == "Hardware_Error_Status":
            return int((state.get("hw") or {}).get(motor, 0))
        raise KeyError(data_name)

    def write(self, data_name, motor, value, *, normalize=True, num_retry=0):
        """Only the gripper current cap can be written; it becomes a cfg message."""
        if data_name == "Goal_Current" and motor == "gripper":
            self._owner.grip_ma = int(value)
            self._owner.send_cfg(grip_ma=int(value))
            return
        raise KeyError(data_name)


class RemoteFollowerConfig:
    """`config.max_relative_target` that forwards changes to the host."""

    def __init__(self, owner, max_rel):
        self._owner = owner
        self._max_rel = max_rel

    @property
    def max_relative_target(self):
        """Current safety limiter (also applied on the host)."""
        return self._max_rel

    @max_relative_target.setter
    def max_relative_target(self, value):
        self._max_rel = value
        self._owner.send_cfg(max_rel=value)


class RemoteFollower:
    """A Koch follower driven by koch4_follower_host.py over UDP (wireless follower).

    The frame loop treats it like KochFollower: connect/disconnect/is_connected,
    send_action, and `bus.sync_read/read/write` for the few registers it uses.
    When no state has arrived for LinkPolicy.stale_after_s, reads raise
    ConnectionError so the existing reconnect path takes over.
    """

    def __init__(self, url, max_rel, grip_ma=None):
        host, _, port = url.removeprefix("udp://").partition(":")
        if not host or not port.isdigit():
            raise ValueError(f"--follower-port は udp://<host>:<port> の形: {url}")
        self.addr = (host, int(port))
        self.sock = None
        self.seq = 0
        self.state = {}
        self.link = LinkState()
        self.rtt_ms = 0.0
        self.grip_ma = grip_ma
        self.bus = RemoteBus(self)
        self.config = RemoteFollowerConfig(self, max_rel)
        self._lock = threading.Lock()

    @property
    def is_connected(self):
        """True while the socket is open and states keep arriving."""
        with self._lock:
            return (
                self.sock is not None
                and link_health(self.link, time.monotonic()) != "stale"
            )

    def connect(self):
        """Open the socket, greet the host and wait for its first state."""
        self.disconnect()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(LINK_RX_TIMEOUT_SEC)
        with self._lock:
            self.sock = sock
            self.link = LinkState()
        threading.Thread(target=self._rx_loop, args=(sock,), daemon=True).start()
        deadline = time.monotonic() + LINK_HELLO_TIMEOUT_SEC
        while time.monotonic() < deadline:
            self._send({"type": "hello"})
            self.send_cfg(max_rel=self.config.max_relative_target, grip_ma=self.grip_ma)
            time.sleep(0.3)
            if self.is_connected:
                print(f"[link] フォロワー無線 接続 {self.addr[0]}:{self.addr[1]}")
                return
        raise ConnectionError(
            f"follower host {self.addr[0]}:{self.addr[1]} から状態が届きません"
        )

    def disconnect(self):
        """Close the socket (the host keeps holding the pose)."""
        with self._lock:
            sock, self.sock = self.sock, None
        if sock is not None:
            sock.close()

    def _rx_loop(self, sock):
        while True:
            with self._lock:
                if self.sock is not sock:
                    return
            try:
                data = sock.recv(65535)
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                msg = json.loads(data.decode())
            except ValueError:
                continue
            if msg.get("type") != "state":
                continue
            now = time.monotonic()
            with self._lock:
                self.link, ok = accept_packet(self.link, int(msg.get("n", -1)), now)
                if ok:
                    self.state = msg
                    if msg.get("echo_t") is not None:
                        rtt = max(0.0, (now - float(msg["echo_t"])) * 1000)
                        self.rtt_ms = 0.8 * self.rtt_ms + 0.2 * rtt

    def fresh_state(self):
        """Last state, or ConnectionError when the link has gone stale."""
        with self._lock:
            state, link = self.state, self.link
        now = time.monotonic()
        if link_health(link, now) == "stale":
            age = now - link.last_rx if link.received else float("inf")
            raise ConnectionError(f"フォロワー無線が途絶({age:.1f}s)")
        return state

    def _send(self, msg):
        with self._lock:
            sock = self.sock
        if sock is not None:
            try:
                sock.sendto(json.dumps(msg).encode(), self.addr)
            except OSError:
                pass

    def send_action(self, action):
        """Ship the leader's goals to the host (the host clamps and writes them)."""
        self.seq += 1
        self._send(
            {
                "type": "action",
                "seq": self.seq,
                "t": round(time.monotonic(), 4),
                "action": {k: float(v) for k, v in action.items()},
            }
        )
        return action

    def send_cfg(self, **fields):
        """Forward max_rel / grip_ma to the host."""
        self._send(
            {"type": "cfg", **{k: v for k, v in fields.items() if v is not None}}
        )

    def link_stats(self):
        """Age / rtt / loss / health for telemetry and alerts."""
        with self._lock:
            link, rtt = self.link, self.rtt_ms
        now = time.monotonic()
        age = (now - link.last_rx) * 1000 if link.received else -1
        return {
            "age_ms": int(age),
            "rtt_ms": int(rtt),
            "loss": round(loss_ratio(link), 3),
            "health": link_health(link, now),
        }


def check_hw_errors(dev, label):
    """Print any latched hardware error (overload etc.) on a bus."""
    try:
        errs = dev.bus.sync_read("Hardware_Error_Status", normalize=False)
    except Exception as e:  # noqa: BLE001 - diagnostics must never abort startup
        print(
            f"[hw] {label}: エラーレジスタ読み取り不可({type(e).__name__}) — 診断スキップ"
        )
        return
    hit = False
    for m, v in errs.items():
        v = int(v)
        if v:
            hit = True
            names = "/".join(n for b, n in HW_ERROR_BITS.items() if v >> b & 1) or str(
                v
            )
            print(
                f"[hw] ⚠ {label}の{m} がエラー停止中({names}) — 電源を10秒抜いて入れ直すまで動きません"
            )
    if not hit:
        print(f"[hw] {label}: 全軸エラーなし")


def soft_resync(robot, teleop, fps=30, seconds=1.5):
    """Ramp the follower from its pose to the leader's pose (no jump on start)."""
    if robot is None:
        return
    try:
        start = robot.bus.sync_read("Present_Position")  # 正規化値
        steps = max(int(seconds * fps), 1)
        for i in range(steps):
            target = teleop.get_action()
            a = (i + 1) / steps
            robot.send_action(
                {
                    k: (1 - a) * float(start.get(k.replace(".pos", ""), v))
                    + a * float(v)
                    for k, v in target.items()
                }
            )
            time.sleep(1.0 / fps)
    except Exception as e:  # noqa: BLE001 - the ramp is best effort
        print(f"[sync] 合流ランプ失敗(そのまま続行): {e}")


def build_parser():
    """Define the command line."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--follower-port",
        default=LEADER_ONLY,
        help="フォロワーのポート。'none'=リーダーのみ(VR)",
    )
    ap.add_argument("--follower-id", default="koch_follower_arm")
    ap.add_argument("--leader-port", default=None)
    ap.add_argument("--leader-id", default="koch_leader_arm")
    ap.add_argument(
        "--config-dir",
        type=Path,
        default=DEFAULT_CONFIG_DIR,
        help="較正 JSON を置く専用フォルダ(calibration/koch_follower|koch_leader/<id>.json)",
    )
    ap.add_argument(
        "--work-dir", type=Path, default=DEFAULT_WORK_DIR, help="CSV・ログの出力先"
    )
    ap.add_argument(
        "--lerobot-cache",
        action="store_true",
        help="較正を lerobot 既定(~/.cache/huggingface/lerobot/calibration)から読む",
    )
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument(
        "--max-rel", type=float, default=20, help="max_relative_target(安全リミッタ)"
    )
    ap.add_argument(
        "--ff",
        choices=["off", "gripper", "arm", "vwall"],
        default="off",
        help="gripper=握り返し / arm=握り返し+腕3軸反力 / vwall=仮想物体(VR)",
    )
    ap.add_argument(
        "--ff-style",
        choices=["spring", "error"],
        default="spring",
        help="spring=戻りバネ式(9/4 実機修正済み・既定) / error=差分反射式(8/04)",
    )
    ap.add_argument(
        "--ff-pgain",
        type=int,
        default=FF_GRIPPER_P_GAIN,
        help="mode5 の Position_P_Gain",
    )
    ap.add_argument(
        "--ff-ke", type=float, default=9.0, help="差分反射の強さ[mA/正規化unit]"
    )
    ap.add_argument(
        "--ff-edb", type=float, default=6.0, help="差分反射の不感帯[正規化unit]"
    )
    ap.add_argument(
        "--ff-sign", type=int, choices=[1, -1], default=1, help="差分反射の向き"
    )
    ap.add_argument(
        "--ff-gain",
        type=float,
        default=1.5,
        help="spring: Goal_Current=floor+gain×|I_F|",
    )
    ap.add_argument(
        "--ff-cap", type=int, default=450, help="Goal_Current 上限[mA](最大900)"
    )
    ap.add_argument(
        "--ff-floor", type=int, default=60, help="spring: 常時の戻りバネ電流[mA]"
    )
    ap.add_argument(
        "--ff-deadband", type=int, default=25, help="spring: 無負荷電流の無視幅[mA]"
    )
    ap.add_argument("--ff-arm-gain", type=float, default=0.6)
    ap.add_argument(
        "--ff-arm-cap", type=int, default=150, help="腕反力の上限[mA](最大400)"
    )
    ap.add_argument("--ff-arm-deadband", type=int, default=60)
    ap.add_argument(
        "--ff-arm-invert", default="", help="反力が逆向きの関節をカンマ列挙で反転"
    )
    ap.add_argument(
        "--wall",
        type=parse_wall,
        default=None,
        help="vwall の固定壁 name:width[:p_gain[:cap_ma[:release]]] 例 ball:45:800:300",
    )
    ap.add_argument(
        "--vw",
        action="store_true",
        help="vwall で握った物体の重さをリーダーの肩・肘に電流で返す(腕反力は実機未検証。--vw-cap 小から)",
    )
    ap.add_argument(
        "--vw-scale",
        type=float,
        default=0.12,
        help="重さの倍率(物理値=1/Kt に対する割合)",
    )
    ap.add_argument(
        "--vw-cap", type=int, default=120, help="重さ電流の上限[mA/関節](最大400)"
    )
    ap.add_argument(
        "--vw-invert",
        default="",
        help="重さの向きが逆の関節をカンマ列挙(shoulder_lift,elbow_flex)",
    )
    ap.add_argument(
        "--follower-grip-ma",
        type=int,
        default=None,
        help="フォロワーgripperの Goal_Current 上限[mA]。過負荷停止対策(未指定=触らない)",
    )
    ap.add_argument(
        "--viz-port",
        default="8765",
        help="テレメトリ UDP 配信ポート(カンマ区切りで複数可。例 8765,8769 / 0=なし)",
    )
    ap.add_argument(
        "--ctl-port", type=int, default=8766, help="制御コマンド受信 UDP ポート(0=無効)"
    )
    ap.add_argument("--csv", action="store_true", help="work-dir/csv に記録する")
    ap.add_argument("--plot", action="store_true", help="koch4_live_plot.py を自動起動")
    ap.add_argument(
        "--panel", action="store_true", help="koch4_web_panel.py を自動起動(http 8780)"
    )
    ap.add_argument("--cams", default="", help="パネルに映すカメラ index(例 0 / 0,1)")
    ap.add_argument(
        "--selftest", action="store_true", help="制御則の自己診断のみ(ハード不要)"
    )
    return ap


def parse_ports(spec):
    """Turn '8765,8769' into [8765, 8769]; zero and blanks are dropped."""
    ports = []
    for part in str(spec).split(","):
        part = part.strip()
        if part and int(part) > 0:
            ports.append(int(part))
    return ports


def spawn_viewers(args, viz_ports):
    """Start the plot / panel helper processes if asked; returns the Popen list."""
    children = []
    if args.plot and viz_ports:
        path = HERE / "koch4_live_plot.py"
        if path.exists():
            children.append(
                subprocess.Popen(
                    [sys.executable, str(path), "--port", str(viz_ports[0])]
                )
            )
            print("[plot] グラフビューアを自動起動しました")
        else:
            print(f"[plot] {path} が見つかりません")
    if args.panel and viz_ports:
        path = HERE / "koch4_web_panel.py"
        if path.exists():
            cmd = [
                sys.executable,
                str(path),
                "--telemetry",
                str(viz_ports[0]),
                "--ctl-port",
                str(args.ctl_port),
            ]
            if args.cams:
                cmd += ["--cams", args.cams]
            children.append(subprocess.Popen(cmd))
            print("[panel] ブラウザ操作パネル自動起動 → http://127.0.0.1:8780")
        else:
            print(f"[panel] {path} が見つかりません")
    return children


def open_csv(work_dir):
    """Create work-dir/csv/teleop_<timestamp>.csv and return (writer, file)."""
    stamp = datetime.now()  # noqa: DTZ005 - local wall-clock time is what the log reader expects
    path = work_dir / "csv" / f"teleop_{stamp:%Y%m%d_%H%M%S}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fcsv = open(path, "w", newline="")  # noqa: SIM115 - closed in main's finally
    print(f"[csv] {path}")
    return csv.writer(fcsv), fcsv


def main():  # the frame loop keeps the hardware-tested shape of mock/v0 on purpose
    """Run one leader/follower pair until Ctrl+C."""
    args = build_parser().parse_args()
    if args.selftest:
        selftest()
        return
    if not args.leader_port:
        sys.exit("--leader-port は必須です(ポートは推測しません。ユーザーが渡す)")
    leader_only = args.follower_port.lower() == LEADER_ONLY
    if leader_only and args.ff in ("gripper", "arm"):
        sys.exit("--follower-port none のとき使える --ff は off / vwall だけです")
    if args.ff_cap > FF_CAP_MAX_MA:
        args.ff_cap = FF_CAP_MAX_MA
        print(f"[ff] cap は安全のため最大{FF_CAP_MAX_MA}mAに制限しました")
    elif args.ff_cap > FF_CAP_WARN_MA:
        print(f"[ff] ⚠ cap={args.ff_cap}mA(>{FF_CAP_WARN_MA}) — 発熱が速い。短時間で")
    if args.ff_arm_cap > ARM_CAP_MAX_MA:
        args.ff_arm_cap = ARM_CAP_MAX_MA
        print(f"[ff-arm] 腕capは安全のため最大{ARM_CAP_MAX_MA}mAに制限しました")
    arm_invert = {s.strip() for s in args.ff_arm_invert.split(",") if s.strip()}
    vw_invert = {s.strip() for s in args.vw_invert.split(",") if s.strip()}
    if args.vw_cap > ARM_CAP_MAX_MA:
        args.vw_cap = ARM_CAP_MAX_MA
        print(f"[vw] 重さ cap は安全のため最大{ARM_CAP_MAX_MA}mAに制限しました")
    guard = ThermalGuard()
    viz_ports = parse_ports(args.viz_port)
    if args.wall is not None and args.wall.cap_ma > args.ff_cap:
        args.wall = replace(
            args.wall, cap_ma=int(args.ff_cap)
        )  # 固定壁も --ff-cap を超えない

    KochFollower, KochFollowerConfig, KochLeader, KochLeaderConfig, retry_errors = (
        load_lerobot()
    )
    cal_f = (
        None
        if args.lerobot_cache
        else args.config_dir / "calibration" / "koch_follower"
    )
    cal_l = (
        None if args.lerobot_cache else args.config_dir / "calibration" / "koch_leader"
    )
    cal_label = (
        "lerobot既定(~/.cache)"
        if args.lerobot_cache
        else str(args.config_dir / "calibration")
    )
    print(f"[dir] config={args.config_dir}  work={args.work_dir}  較正={cal_label}")

    children = spawn_viewers(args, viz_ports)
    robot = None
    remote_follower = (not leader_only) and args.follower_port.lower().startswith(
        "udp://"
    )
    if remote_follower:
        robot = RemoteFollower(
            args.follower_port,
            float(args.max_rel) if args.max_rel > 0 else None,
            args.follower_grip_ma,
        )
        print(
            f"[link] フォロワー無線: {args.follower_port}（相手側で koch4_follower_host.py を先に起動）"
        )
    elif not leader_only:
        robot = KochFollower(
            KochFollowerConfig(
                port=args.follower_port,
                id=args.follower_id,
                calibration_dir=cal_f,
                max_relative_target=float(args.max_rel) if args.max_rel > 0 else None,
            )
        )
    teleop = KochLeader(
        KochLeaderConfig(
            port=args.leader_port, id=args.leader_id, calibration_dir=cal_l
        )
    )
    if robot is not None:
        robot.connect()
    teleop.connect()
    if robot is not None:
        check_hw_errors(robot, "フォロワー")
    check_hw_errors(teleop, "リーダー")
    apply_follower_grip_cap(robot, args.follower_grip_ma)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if viz_ports else None
    ctl = None
    if args.ctl_port:
        ctl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ctl.bind((TELEMETRY_HOST, args.ctl_port))
        ctl.setblocking(False)
    writer, fcsv = (None, None)
    if args.csv:
        writer, fcsv = open_csv(args.work_dir)
    motors = list(teleop.bus.motors)  # ['shoulder_pan', ..., 'gripper']
    if writer:
        writer.writerow(
            ["t_sec"]
            + [f"{m}.cur" for m in motors]
            + [f"{m}.Lpos" for m in motors]
            + [f"{m}.Fpos" for m in motors]
            + ["ff_mA", "vwall_engaged", "vw_shoulder_mA", "vw_elbow_mA"]
        )

    ff_on = args.ff in ("gripper", "arm")
    ff_arm = args.ff == "arm"
    vwall_on = args.ff == "vwall"
    ff_anchor = [None]  # 初回の保持目標。再接続で引き継ぐ
    if ff_on or vwall_on:
        ff_anchor[0] = arm_gripper_ff(teleop, args)
    if ff_arm:
        setup_arm_ff(teleop, ARM_FF_JOINTS)
        print("[ff-arm] ⚠ 腕反力中はリーダーから手を離さない(反力で勝手に動き得る)")
    ticks = gripper_ticks(teleop.bus)
    if vwall_on and ticks is None:
        print(
            "[vwall] ⚠ リーダーgripperの較正がありません — 壁位置を決められないので vwall を無効化"
        )
        vwall_on = False
    vw_on = vwall_on and args.vw
    if vw_on:
        setup_arm_ff(teleop, VW_JOINTS)
        print(
            "[vw] ⚠ 仮想重さON: 握っている間だけ肩・肘に電流が出る。リーダーから手を離さない"
        )
    wall = [args.wall]  # 現在の仮想物体(None=自由空間)
    wall_cmd = [None]  # 最後に書いた WallCommand
    wall_expire = [
        float("inf")
    ]  # ブリッジ経由の壁の有効期限(monotonic 秒)。固定壁は無期限
    if vwall_on and wall[0]:
        print(f"[vwall] 固定壁 {wall[0]}")

    spring = [SpringState()]
    erefl = [ErrorReflectionState()]
    ema_arm = {j: 0.0 for j in ARM_FF_JOINTS}
    arm_out = {j: 0 for j in ARM_FF_JOINTS}
    arm_rr = [0]
    last_temp = [0]
    stop_req = [False]
    last_notify = [0.0]
    ff_ma = [0]
    ff_hot = [False]
    n = [0]
    reconnects = [0]
    vw_state = [VirtualWeightState()]
    vw_out = {j: 0 for j in VW_JOINTS}
    twin_map = {j: JointMap(JOINT_SPAN_DEG[j]) for j in JOINT_SPAN_DEG}
    alerts = {}  # key -> (text, expire_monotonic): パネル・VR HUD に出す警告
    hw_bits = {"L": 0, "F": 0}
    cap_frames = [0]  # 握り反力が上限に張り付いた連続フレーム
    fgrip_frames = [0]  # フォロワー握力が上限付近の連続フレーム

    def raise_alert(key, text, ttl=ALERT_TTL_SEC):
        """Keep an alert visible on the panel / VR HUD for ttl seconds."""
        alerts[key] = (text, time.monotonic() + ttl)

    def active_alerts():
        """Drop expired alerts and return the texts still active."""
        now = time.monotonic()
        for key in [k for k, (_, exp) in alerts.items() if exp < now]:
            del alerts[key]
        return [text for text, _ in alerts.values()]

    def check_hw_alert():
        """Every 2 s: latched hardware errors (overload etc.) on both grippers."""
        try:
            hw_bits["L"] = int(
                teleop.bus.read("Hardware_Error_Status", "gripper", normalize=False)
            )
            hw_bits["F"] = (
                int(robot.bus.read("Hardware_Error_Status", "gripper", normalize=False))
                if robot is not None
                else 0
            )
        except Exception:  # noqa: BLE001 - diagnostics must never stop the loop
            return
        for label, key in (("リーダー", "L"), ("フォロワー", "F")):
            bits = hw_bits[key]
            if bits:
                names = "/".join(n for b, n in HW_ERROR_BITS.items() if bits >> b & 1)
                raise_alert(
                    f"hw{key}",
                    f"⛔ {label}gripper エラー停止({names or bits}) — 電源を10秒抜いて入れ直す",
                    ttl=6.0,
                )

    def check_follower_grip_alert(cur):
        """Warn when the follower gripper sits near its current cap for a second."""
        cap = args.follower_grip_ma
        if robot is None or not cap:
            return
        near = abs(cur.get("gripper", 0.0)) >= 0.9 * cap
        fgrip_frames[0] = fgrip_frames[0] + 1 if near else 0
        if fgrip_frames[0] >= int(args.fps):
            raise_alert(
                "fgrip", f"⚠ フォロワー握力が上限 {cap}mA 付近 — 滑り・過負荷停止に注意"
            )

    def check_link_alert():
        """Warn when the wireless follower's state stream is late or on hold."""
        if not isinstance(robot, RemoteFollower):
            return
        stats = robot.link_stats()
        if stats["health"] in ("alert", "hold"):
            raise_alert(
                "link",
                f"⚠ フォロワー無線 遅延 {stats['age_ms']}ms"
                f"（往復 {stats['rtt_ms']}ms・欠落 {stats['loss'] * 100:.0f}%）",
            )

    def vw_law():
        return VirtualWeightLaw(
            scale=args.vw_scale,
            cap_ma=args.vw_cap,
            invert_shoulder="shoulder_lift" in vw_invert,
            invert_elbow="elbow_flex" in vw_invert,
        )

    def spring_law():
        return SpringLaw(args.ff_gain, args.ff_floor, args.ff_cap, args.ff_deadband)

    def error_law():
        return ErrorReflectionLaw(args.ff_ke, args.ff_edb, args.ff_sign, args.ff_cap)

    def apply_wall(cmd):
        """Write only the virtual-wall registers that changed."""
        last = wall_cmd[0]
        bus = teleop.bus
        if last is None or cmd.p_gain != last.p_gain:
            bus.write("Position_P_Gain", "gripper", cmd.p_gain, normalize=False)
        if last is None or cmd.goal_position_tick != last.goal_position_tick:
            bus.write(
                "Goal_Position", "gripper", cmd.goal_position_tick, normalize=False
            )
        if last is None or cmd.goal_current_ma != last.goal_current_ma:
            bus.write("Goal_Current", "gripper", cmd.goal_current_ma, normalize=False)
        if last is None or cmd.engaged != last.engaged:
            name = wall[0].name if wall[0] else "-"
            print(
                f"\n[vwall] {'接触' if cmd.engaged else '離脱'}: {name} "
                f"tick={cmd.goal_position_tick} cap={cmd.goal_current_ma}mA P={cmd.p_gain}"
            )
        wall_cmd[0] = cmd

    def stop_weight():
        """Drop the weight currents and free the lift/elbow joints."""
        nonlocal vw_on
        if vw_on:
            release_arm(teleop.bus, VW_JOINTS)
        vw_on = False
        vw_state[0] = VirtualWeightState()
        for j in VW_JOINTS:
            vw_out[j] = 0

    def update_twin_map(payload):
        """Take the joint spans/offsets/signs the bridge uses for the twin (weight FK)."""
        joints = (payload or {}).get("joints", {})
        for j, m in joints.items():
            if j in twin_map and isinstance(m, dict):
                twin_map[j] = JointMap(
                    float(m.get("span_deg", twin_map[j].span_deg)),
                    float(m.get("offset_deg", twin_map[j].offset_deg)),
                    1 if int(m.get("sign", twin_map[j].sign)) >= 0 else -1,
                )

    def set_ff_mode(new):
        """Switch FF mode from the panel / bridge (off/gripper/arm/vwall) with cleanup."""
        nonlocal ff_on, ff_arm, vwall_on, vw_on
        if new not in ("off", "gripper", "arm", "vwall"):
            return
        if new in ("gripper", "arm") and leader_only:
            print("\n[ctl] リーダーのみ構成では gripper/arm は使えません")
            return
        if new in ("gripper", "arm") and not ff_on:
            if vwall_on:
                vwall_on, wall_cmd[0] = False, None
                stop_weight()
            args.ff = new
            ff_anchor[0] = arm_gripper_ff(teleop, args)
            ff_on = True
        if new == "vwall" and not vwall_on and ticks is not None:
            args.ff = new
            ff_anchor[0] = arm_gripper_ff(teleop, args)
            ff_on, vwall_on, wall_cmd[0] = False, True, None
            if args.vw:
                setup_arm_ff(teleop, VW_JOINTS)
                vw_on = True
        if new == "off" and (ff_on or vwall_on):
            release_gripper(teleop.bus)
            ff_on, vwall_on, wall_cmd[0] = False, False, None
            stop_weight()
        if new == "arm" and not ff_arm:
            setup_arm_ff(teleop, ARM_FF_JOINTS)
            ff_arm = True
        if new != "arm" and ff_arm:
            release_arm(teleop.bus)
            ff_arm = False
        args.ff = new
        print(f"\n[ctl] FFモード → {new}")

    def handle_ctl():
        """Drain the control socket (panel sliders, bridge walls, stop)."""
        if ctl is None:
            return
        while True:
            try:
                data, _ = ctl.recvfrom(2048)
            except (BlockingIOError, OSError):
                return
            try:
                cmd = json.loads(data.decode())
            except ValueError:
                continue
            if "ff_gain" in cmd:
                args.ff_gain = float(cmd["ff_gain"])
            if "ff_ke" in cmd:
                args.ff_ke = float(cmd["ff_ke"])
            if "ff_cap" in cmd:
                args.ff_cap = int(min(max(float(cmd["ff_cap"]), 0), FF_CAP_MAX_MA))
            if "ff_floor" in cmd:
                args.ff_floor = int(min(max(float(cmd["ff_floor"]), 0), 120))
            if "arm_gain" in cmd:
                args.ff_arm_gain = float(cmd["arm_gain"])
            if "arm_cap" in cmd:
                args.ff_arm_cap = int(
                    min(max(float(cmd["arm_cap"]), 0), ARM_CAP_MAX_MA)
                )
            if "max_rel" in cmd and robot is not None:
                robot.config.max_relative_target = float(cmd["max_rel"])
            if "vw_scale" in cmd:
                args.vw_scale = min(max(float(cmd["vw_scale"]), 0.0), 1.0)
            if "vw_cap" in cmd:
                args.vw_cap = int(min(max(float(cmd["vw_cap"]), 0), ARM_CAP_MAX_MA))
            if "twin" in cmd:
                update_twin_map(cmd["twin"])
            if "mode" in cmd:
                set_ff_mode(cmd["mode"])
            if "vwall" in cmd:
                wall[0] = wall_from_message(cmd["vwall"])
                if wall[0] is not None and wall[0].cap_ma > args.ff_cap:
                    wall[0] = replace(
                        wall[0], cap_ma=int(args.ff_cap)
                    )  # ページ側の値も --ff-cap で頭打ち
                wall_expire[0] = time.monotonic() + WALL_TTL_SEC
            if cmd.get("resync"):
                print("\n[ctl] 合流リセット")
                soft_resync(robot, teleop, args.fps)
            if cmd.get("stop"):
                stop_req[0] = True

    def gripper_feedback(action, cur, fpos):
        """One frame of gripper feedback (spring or error style)."""
        nonlocal ff_on
        if args.ff_style == "error":
            erefl[0] = error_reflection_step(
                error_law(),
                erefl[0],
                float(action.get("gripper.pos", 0.0)),
                float(fpos.get("gripper", 0.0)),
            )
            ff_ma[0] = erefl[0].command_ma
        else:
            spring[0] = spring_step(spring_law(), spring[0], cur.get("gripper", 0.0))
            ff_ma[0] = spring[0].command_ma
        teleop.bus.write("Goal_Current", "gripper", ff_ma[0], normalize=False)
        cap_frames[0] = cap_frames[0] + 1 if abs(ff_ma[0]) >= args.ff_cap else 0
        if cap_frames[0] >= int(args.fps):
            raise_alert("cap", f"⚠ 握り反力が上限 {args.ff_cap}mA に張り付いています")
        th_on = 120 if args.ff_style == "error" else args.ff_floor + 80
        th_off = 60 if args.ff_style == "error" else args.ff_floor + 40
        now = time.monotonic()
        if not ff_hot[0] and abs(ff_ma[0]) >= th_on and now - last_notify[0] > 3.0:
            ff_hot[0], last_notify[0] = True, now
            print(
                f"\n[ff] 握り返し作動中: grip={cur.get('gripper', 0.0):.0f}mA → cmd={ff_ma[0]}mA"
            )
        elif ff_hot[0] and abs(ff_ma[0]) < th_off:
            ff_hot[0] = False
        if n[0] % TEMP_CHECK_EVERY == 0:
            temp = int(
                teleop.bus.read("Present_Temperature", "gripper", normalize=False)
            )
            last_temp[0] = temp
            if robot is not None:
                ftemp = int(
                    robot.bus.read("Present_Temperature", "gripper", normalize=False)
                )
                if ftemp >= guard.derate_c:
                    print(
                        f"\n[hw] ⚠ フォロワーgripper {ftemp}°C — 物を掴んだまま放置しない"
                    )
                    raise_alert(
                        "ftemp", f"⚠ フォロワーgripper {ftemp}°C — 掴んだまま放置しない"
                    )
            arm_max = max((abs(x) for x in arm_out.values()), default=0)
            print(
                f"\r[ff] grip={cur.get('gripper', 0.0):5.0f}mA cmd={ff_ma[0]:4d}mA "
                f"arm=±{arm_max:3d}mA temp={temp}C   ",
                end="",
                flush=True,
            )
            args.ff_gain, stop = thermal_derate(guard, temp, args.ff_gain)
            if stop:
                print(
                    f"\n[ff] リーダーgripper {temp}°C — FBを停止します(冷えたら再起動)"
                )
                teleop.bus.write(
                    "Goal_Current", "gripper", args.ff_floor, normalize=False
                )
                ff_on = False
                raise_alert(
                    "ltemp", f"⛔ リーダーgripper {temp}°C — 力覚FBを停止", ttl=10.0
                )
            elif temp >= guard.derate_c:
                print(
                    f"\n[ff] リーダーgripper {temp}°C — ゲインを{args.ff_gain:.2f}に減衰"
                )
                raise_alert("ltemp", f"⚠ リーダーgripper {temp}°C — ゲイン減衰中")

    def wall_feedback(action):
        """One frame of virtual-wall feedback (VR / fixed wall)."""
        nonlocal vwall_on
        if wall[0] is not None and time.monotonic() > wall_expire[0]:
            wall[0] = None  # ブリッジが止まった/ページが閉じた → 壁を残さない
            print("\n[vwall] ブリッジからの更新が途絶えたので壁を解除しました")
            raise_alert(
                "wallttl", "⚠ VR からの更新が途絶えたので壁を解除しました", ttl=6.0
            )
        opening = float(action.get("gripper.pos", 100.0))
        engaged = bool(wall_cmd[0].engaged) if wall_cmd[0] else False
        cmd = virtual_wall_step(wall[0], opening, ticks[0], ticks[1], engaged)
        apply_wall(cmd)
        ff_ma[0] = cmd.goal_current_ma if cmd.engaged else 0
        if vw_on:
            weight_feedback(action, cmd.engaged)
        if n[0] % TEMP_CHECK_EVERY == 0:
            temp = int(
                teleop.bus.read("Present_Temperature", "gripper", normalize=False)
            )
            last_temp[0] = temp
            name = wall[0].name if wall[0] else "-"
            print(
                f"\r[vwall] open={opening:5.1f} obj={name} engaged={cmd.engaged} "
                f"cap={cmd.goal_current_ma}mA temp={temp}C   ",
                end="",
                flush=True,
            )
            _, stop = thermal_derate(guard, temp, 1.0)
            if stop:
                print(
                    f"\n[vwall] リーダーgripper {temp}°C — 壁を無効化します(冷えたら再起動)"
                )
                teleop.bus.write("Goal_Current", "gripper", 0, normalize=False)
                vwall_on = False
                raise_alert(
                    "ltemp", f"⛔ リーダーgripper {temp}°C — 仮想壁を停止", ttl=10.0
                )
            elif temp >= guard.derate_c:
                raise_alert("ltemp", f"⚠ リーダーgripper {temp}°C — 発熱注意")

    def weight_feedback(action, engaged):
        """One frame of virtual weight: grasped mass × tip lever → lift/elbow current."""
        angles = [
            norm_to_rad(float(action.get(f"{j}.pos", 0.0)), twin_map[j])
            for j in ("shoulder_lift", "elbow_flex", "wrist_flex")
        ]
        levers = tip_levers(*angles)
        mass = wall[0].mass_g if wall[0] else 0.0
        vw_state[0] = virtual_weight_step(vw_law(), vw_state[0], engaged, mass, levers)
        outs = {
            "shoulder_lift": vw_state[0].shoulder_ma,
            "elbow_flex": vw_state[0].elbow_ma,
        }
        for j in VW_JOINTS:
            out = round(outs[j])
            if abs(out - vw_out[j]) >= 3 or (out == 0 and vw_out[j] != 0):
                teleop.bus.write("Goal_Current", j, out, normalize=False)
                vw_out[j] = out
        if engaged and any(abs(v) >= args.vw_cap for v in vw_out.values()):
            raise_alert(
                "vwcap",
                f"⚠ 重さの電流が上限 {args.vw_cap}mA — 物体が重すぎるか倍率が高い",
            )
        if n[0] % TEMP_CHECK_EVERY == TEMP_CHECK_EVERY // 2:
            j = VW_JOINTS[(n[0] // TEMP_CHECK_EVERY) % len(VW_JOINTS)]
            at = int(teleop.bus.read("Present_Temperature", j, normalize=False))
            args.vw_scale, stop = thermal_derate(guard, at, args.vw_scale)
            if stop:
                print(f"\n[vw] {j} {at}°C — 仮想重さを停止します(冷えたら再起動)")
                stop_weight()
                raise_alert("vwtemp", f"⛔ {j} {at}°C — 仮想重さを停止", ttl=10.0)
            elif at >= guard.derate_c:
                print(f"\n[vw] {j} {at}°C — 重さ倍率を{args.vw_scale:.2f}に減衰")
                raise_alert("vwtemp", f"⚠ {j} {at}°C — 重さ倍率を減衰中")

    def arm_feedback(cur):
        """One frame of arm-joint feedback (FACTR style, signed current mirror)."""
        nonlocal ff_arm
        for j in ARM_FF_JOINTS:
            v = cur.get(j, 0.0)
            e = abs(v) - args.ff_arm_deadband
            e = 0.0 if e <= 0 else (e if v > 0 else -e)
            ema_arm[j] = 0.8 * ema_arm[j] + 0.2 * e
            out = int(
                max(
                    min(-args.ff_arm_gain * ema_arm[j], args.ff_arm_cap),
                    -args.ff_arm_cap,
                )
            )
            if j in arm_invert:
                out = -out
            if abs(out - arm_out[j]) >= 5 or (out == 0 and arm_out[j] != 0):
                teleop.bus.write("Goal_Current", j, out, normalize=False)
                arm_out[j] = out
        if n[0] % TEMP_CHECK_EVERY == TEMP_CHECK_EVERY // 2:
            j = ARM_FF_JOINTS[arm_rr[0] % len(ARM_FF_JOINTS)]
            arm_rr[0] += 1
            at = int(teleop.bus.read("Present_Temperature", j, normalize=False))
            args.ff_arm_gain, stop = thermal_derate(guard, at, args.ff_arm_gain)
            if stop:
                print(f"\n[ff-arm] {j} {at}°C — 腕反力を停止します(冷えたら再起動)")
                for jj in ARM_FF_JOINTS:
                    teleop.bus.write("Goal_Current", jj, 0, normalize=False)
                ff_arm = False
            elif at >= guard.derate_c:
                print(f"\n[ff-arm] {j} {at}°C — 腕ゲインを{args.ff_arm_gain:.2f}に減衰")

    def publish(t, action, cur, fpos):
        """Send one telemetry frame over UDP and append the CSV row."""
        engaged = bool(wall_cmd[0].engaged) if wall_cmd[0] else False
        if sock:
            payload = json.dumps(
                {
                    "t": round(t, 3),
                    "cur": {m: round(v, 1) for m, v in cur.items()},
                    "pos": {
                        k.replace(".pos", ""): round(float(v), 1)
                        for k, v in action.items()
                    },
                    "fpos": {m: round(float(v), 1) for m, v in fpos.items()},
                    "ff": ff_ma[0],
                    "mode": args.ff,
                    "temp": last_temp[0],
                    "rec": reconnects[0],
                    "n": n[0],
                    "leader_only": leader_only,
                    "alerts": active_alerts(),
                    "hw": dict(hw_bits),
                    "link": (
                        robot.link_stats()
                        if isinstance(robot, RemoteFollower)
                        else None
                    ),
                    "vw": {j: vw_out[j] for j in VW_JOINTS} if vw_on else None,
                    "vwall": (
                        {
                            "name": wall[0].name,
                            "width": wall[0].width,
                            "engaged": engaged,
                        }
                        if (vwall_on and wall[0])
                        else None
                    ),
                    "params": {
                        "ff_gain": args.ff_gain,
                        "ff_ke": args.ff_ke,
                        "ff_cap": args.ff_cap,
                        "ff_floor": args.ff_floor,
                        "arm_gain": args.ff_arm_gain,
                        "arm_cap": args.ff_arm_cap,
                        "vw_scale": args.vw_scale,
                        "vw_cap": args.vw_cap,
                        "max_rel": (robot.config.max_relative_target or 0)
                        if robot
                        else 0,
                    },
                }
            ).encode()
            for port in viz_ports:
                sock.sendto(payload, (TELEMETRY_HOST, port))
        if writer:
            writer.writerow(
                [round(t, 3)]
                + [round(cur.get(m, 0.0), 1) for m in motors]
                + [round(float(action.get(f"{m}.pos", 0.0)), 1) for m in motors]
                + [round(float(fpos.get(m, 0.0)), 1) for m in motors]
                + [
                    ff_ma[0],
                    int(engaged),
                    vw_out["shoulder_lift"],
                    vw_out["elbow_flex"],
                ]
            )

    t0 = time.perf_counter()
    clean_exit = False
    print(
        "[sync] 起動合流ランプ(1.5秒・跳ね防止)…"
        if robot
        else "[sync] リーダーのみ(フォロワーなし)"
    )
    soft_resync(robot, teleop, args.fps)
    print("テレオペ開始。Ctrl+Cで終了。")

    def frame():
        """One frame: follow + read currents + feedback + publish + record."""
        t_frame = time.perf_counter()
        if ctl:
            handle_ctl()
        if stop_req[0]:
            raise KeyboardInterrupt
        action = teleop.get_action()
        cur, fpos = {}, {}
        if robot is not None:
            robot.send_action(action)
            cur_raw = robot.bus.sync_read("Present_Current", normalize=False)
            cur = {
                m: to_signed16(int(v)) * (0.1 if m in XL430_JOINTS else 1.0)
                for m, v in cur_raw.items()
            }
            fpos = robot.bus.sync_read("Present_Position")
            check_follower_grip_alert(cur)
            if remote_follower:
                check_link_alert()
        if ff_on:
            gripper_feedback(action, cur, fpos)
        elif vwall_on:
            wall_feedback(action)
        if ff_arm:
            arm_feedback(cur)
        if n[0] % TEMP_CHECK_EVERY == TEMP_CHECK_EVERY // 4:
            check_hw_alert()
        publish(time.perf_counter() - t0, action, cur, fpos)
        n[0] += 1
        time.sleep(max(0.0, 1.0 / args.fps - (time.perf_counter() - t_frame)))

    def reconnect():
        """Recover from a bus dropout: reconnect, re-arm feedback, ramp back in."""
        nonlocal ff_on, ff_arm
        for dev in (robot, teleop):
            try:
                if dev is not None and dev.is_connected:
                    dev.disconnect()
            except Exception:  # noqa: BLE001, S110 - best effort before reconnecting
                pass
        time.sleep(2)
        if robot is not None and not robot.is_connected:
            robot.connect()
        if not teleop.is_connected:
            teleop.connect()
        if robot is not None:
            check_hw_errors(robot, "フォロワー")
        check_hw_errors(teleop, "リーダー")
        if args.ff in ("gripper", "arm", "vwall"):
            ff_anchor[0] = arm_gripper_ff(teleop, args, anchor=ff_anchor[0])
            ff_on = args.ff in ("gripper", "arm")
        if args.ff == "arm":
            setup_arm_ff(teleop, ARM_FF_JOINTS)
            ff_arm = True
        if vw_on:
            setup_arm_ff(teleop, VW_JOINTS)
            vw_state[0] = VirtualWeightState()
            for j in VW_JOINTS:
                vw_out[j] = 0
        apply_follower_grip_cap(robot, args.follower_grip_ma)
        spring[0], erefl[0], ff_ma[0], wall_cmd[0] = (
            SpringState(),
            ErrorReflectionState(),
            0,
            None,
        )
        soft_resync(robot, teleop, args.fps)
        print("[robust] 再接続成功。テレオペ継続")

    try:
        while True:
            try:
                frame()
            except retry_errors as e:
                reconnects[0] += 1
                if reconnects[0] > MAX_RECONNECTS:
                    print(
                        "[robust] 再接続上限に達しました。物理側(コネクタ・電源)の点検が必要です"
                    )
                    raise
                print(
                    f"\n[robust] 通信断を検知: {e}\n"
                    f"[robust] 2秒後に再接続します… ({reconnects[0]}/{MAX_RECONNECTS})"
                )
                raise_alert(
                    "link",
                    f"⚠ 通信断 → 再接続中 ({reconnects[0]}/{MAX_RECONNECTS})",
                    ttl=8.0,
                )
                try:
                    reconnect()
                except Exception as e2:  # noqa: BLE001 - retried on the next loop
                    print(f"[robust] 再接続失敗: {e2} — 2秒後に再試行")
                    time.sleep(2)
    except KeyboardInterrupt:
        clean_exit = True
        print("\n終了処理中…")
    finally:
        try:
            if args.ff in ("gripper", "arm", "vwall"):
                release_gripper(teleop.bus)
            if args.ff == "arm":
                release_arm(teleop.bus)
            if vw_on:
                release_arm(teleop.bus, VW_JOINTS)
        except Exception:  # noqa: BLE001, S110 - the bus may already be gone
            pass
        if fcsv:
            fcsv.close()
        for child in children:
            if child.poll() is None:
                if clean_exit:
                    child.terminate()
                else:
                    print("[plot] 異常終了のためビューア/パネルは開いたままにします")
        for dev in (robot, teleop):
            try:
                if dev is not None:
                    dev.disconnect()
            except Exception:  # noqa: BLE001, S110
                pass
        print(f"完了({n[0]}フレーム)。使用後は電源を抜くこと(過熱防止)")


if __name__ == "__main__":
    main()

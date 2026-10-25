#!/usr/bin/env python3
"""One Koch pair: teleop + current telemetry + leader force feedback (koch build).

mock/v0/koch_teleop_plus.py（twinarm main）・robotics 版（2026-08-04: 差分反射式
`--ff-style error`・パネルにカメラ `--cams`・全リトライ注入）・branch
rn/fix/gripper-force-feedback（2026-09-04: 実機レジスタ計測で確認した修正3点）を
1本に合流させた koch 用の版。加えて:

  * `--config-dir` / `--work-dir`: 較正 JSON・設定・ログを専用フォルダに置く
    （lerobot の calibration_dir を明示指定。~/.cache に散らばらない）
  * `--follower-port none`: リーダーのみ（VR 仮想反力デモ・フォロワー不要）
  * `--leader-type koch_follower`: 入力装置がフォロワー機（手で動かす 2 人目用）。接続後に
    腕 5 軸のトルクを抜き、gripper だけ Mode 5 の壁にする。重さは肘（XL330-M288・電流制御）と
    肩（XL430-W250・電流制御が無いので PWM＝電圧制御モード。握っている間だけトルク ON）。
    Kt と握りの上限は M288 用に自動で換算。`--vw-pwm-cap 0` で肩の PWM を使わない
  * `--follower-type koch_leader`: 役割入替（握手・10/25）。`--leader-type koch_follower` と組で、
    操作者が持つ入力装置はフォロワー機（腕トルク抜き・gripper は M288 なので同じ mA で約 2.4 倍の
    握り返し）、リーダー機（M077）が位置制御で追従して相手側になる。有線のみ。ランチャは
    `--swap-roles`、`koch.sh handshake` は既定で入替（`SWAP=0` で従来）
  * `--assist elbow_flex,wrist_flex`: 上の入力装置の摩擦アシスト。減速比 288:1 の軸は力を
    出していなくても重いので、動かしている向きへ小さな電流を足す（速度×`--assist-gain`・
    上限 `--assist-cap` mA・不感帯・速すぎるときは切る）。向きが逆なら重くなるだけなので
    `--assist-invert` で反転。実機未検証。上限は小さい値から
  * `--ff vwall`: 仮想物体（VR ブリッジ or `--wall` 固定指定）の反力。壁の描画は
    サーボ内部の位置ループに任せ（Operating_Mode 5・Goal_Position=壁・Goal_Current=上限・
    Position_P_Gain=硬さ）、ホストは「壁に触れているか」だけを 30fps で切り替える
  * 制御則（spring / error / virtual wall / 温度ガード）は
    twinarm/src/twinarm/domain/gripper_feedback.py の写し。discovery は独立スクリプトの
    規則なので import しない。変更するときは両方を直し、twinarm 側のテストで固定する

力覚 FB の3方式:
  --ff gripper --ff-style spring  旧 ALOHA 式（既定）。フォロワー電流→リーダー戻りバネ電流。
      9/4 修正: Position_P_Gain=800（既定のままでは Goal_Current を上げても押し返さない・実測）
      / 再接続時は初回の基準を引き継ぐ / 基準=較正済みの開端（range_max）
  --ff gripper --ff-style error   差分反射式（robotics 8/04）。L指令−F実位置を反力に。
      フォロワー停止時のみ・不感帯・スルーレート制限。自由空間は完全フリー。
      応答のつまみ（10/25: 上限を上げても遅く感じる原因はここ）: `--ff-slew`（1 フレームの電流変化の
      上限）・`--ff-alpha`（平滑化）・`--ff-stall`（停止判定のフレーム数）・`--ff-move-eps`（追従中と見る
      速さ）。走行中は制御ポートへ同名のキー（`python -m koch.tools.ctl ff_slew=300`）で変えられる
  --ff gripper --ff-style follow  追従壁式（10/25）。リーダー gripper を Mode 5（電流制限付き位置）にし、目標位置＝
      フォロワーの実位置を毎フレーム渡す。壁はサーボ内部の位置ループが即時に出す（硬さ=--ff-pgain・最大=--ff-cap）。
      ホスト側の停止判定・平滑化・スルー制限が無いので遅れもかちかちも構造的に出ない。自由空間は追従の遅れ分の軽い粘り
  --ff arm                        上に腕3軸反力（FACTR 式）を足す。実機未検証（67_ §3-3）
  --ff vwall                      仮想物体。フォロワー不要。壁= {name,width,p_gain,cap_ma,release}

安全（変更しない値の根拠は robotics web_research/bilateral_force_feedback_koch_20260803.md）:
  - Goal_Current 上限は既定 450mA（M077 ストール 1.47A@5V の ~30%）。最大 900
  - 温度ガード: 60°C でゲイン半減（spring=ff_gain / error=ff_ke）・65°C で FB 停止（Temperature Limit 70°C 手前。停止後は冷えてから再起動）
  - 実機を動かす・トルクを抜くのはユーザーが明示的に頼んだときだけ（AGENTS.md）

使い方（Mac・discovery/koch で `uv run`）:
  uv run koch-teleop \
      --follower-port /dev/tty.usbmodemXXXX --follower-id koch_follower_A \
      --leader-port   /dev/tty.usbmodemYYYY --leader-id   koch_leader_A \
      --ff gripper --panel --csv
  uv run koch-teleop --leader-port /dev/tty.usbmodemYYYY \
      --follower-port none --ff vwall --wall ball:45:800:300      # 壁を固定して手で確認
  uv run koch-teleop --leader-port /dev/tty.usbmodemZZZZ --leader-id koch_follower_B
      --leader-type koch_follower --follower-port none --ff vwall --vw --arm-label F   # 2 人目: フォロワー機を手で
  koch-teleop --selftest       # 制御則の自己診断（ハード・lerobot 不要）
"""

import argparse
import csv
import json
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from koch.console import install_clamp_filter, reconfigure_stdout
from koch.control import (
    parse_wall,
    scale_wall,
    twin_map_from_message,
    wall_from_message,
)
from koch.driven_leader import ARM_GAINS as DRIVEN_ARM_GAINS
from koch.driven_leader import ARM_JOINTS as DRIVEN_ARM_JOINTS
from koch.driven_leader import DrivenLeader, arm_gains
from koch.feedback import (
    JOINT_SPAN_DEG,
    ErrorReflectionLaw,
    ErrorReflectionState,
    HandAssistLaw,
    HandAssistState,
    JointMap,
    SpringLaw,
    SpringState,
    ThermalGuard,
    VirtualWeightLaw,
    VirtualWeightState,
    error_reflection_step,
    hand_assist_step,
    norm_to_rad,
    spring_step,
    thermal_derate,
    tip_levers,
    virtual_wall_step,
    virtual_weight_step,
    wall_tick,
)
from koch.lerobot_compat import load_lerobot
from koch.motors import (
    ARM_CAP_MAX_MA,
    ARM_FF_JOINTS,
    ASSIST_CAP_MAX_MA,
    ASSIST_JOINTS,
    DEVICE_MODELS,
    FF_CAP_MAX_MA,
    FF_CAP_WARN_MA,
    FF_GRIPPER_P_GAIN,
    FOLLOWER_TYPES,
    MODE_EXTENDED_POSITION,
    MODE_PWM,
    PWM_CAP_MAX,
    PWM_LIMIT_DEFAULT,
    VW_JOINTS,
    gripper_ticks,
    hw_error_names,
    role_names,
    to_signed16,
    xl430_joints,
)
from koch.paths import CONFIG_DIR, WORK_DIR, calibration_dir
from koch.remote_follower import RemoteFollower
from koch.telemetry import TELEMETRY_HOST, parse_ports, telemetry_frame, viewer_commands

DEFAULT_CONFIG_DIR = CONFIG_DIR
DEFAULT_WORK_DIR = WORK_DIR
LEADER_ONLY = "none"

MAX_RECONNECTS = 20
TEMP_CHECK_EVERY = 60  # frames (2 s at 30 fps)
WALL_TTL_SEC = (
    3.0  # VR ブリッジからの壁更新が途絶えたら壁を解除する秒数(固定壁は無期限)
)
ALERT_TTL_SEC = 4.0


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


def follow_wall(leader_cmd, follower_pos, ticks, cap_ma, deadband):
    """The follow-wall style for one frame: the leader's goal tick and the force to report.

    The goal is the follower's normalized gripper position on the leader's own tick
    range (closed, open), so the servo's position loop renders the wall where the
    follower actually is; the reported current is the cap while the leader is pushed
    beyond the deadband, zero otherwise (the servo decides the real current).
    """
    goal = wall_tick(float(follower_pos), ticks[0], ticks[1])
    engaged = abs(float(leader_cmd) - float(follower_pos)) > deadband
    return goal, (int(cap_ma) if engaged else 0)


def arm_gripper_ff(leader, args, anchor=None):
    """Set up the gripper for the selected style; returns the anchor tick or None."""
    if args.ff == "vwall":
        return setup_gripper_ff(
            leader, args.ff_cap, anchor, floor_ma=0, p_gain=args.ff_pgain
        )
    if args.ff_style == "follow":
        bus = leader.bus
        here = int(bus.read("Present_Position", "gripper", normalize=False))
        setup_gripper_ff(
            leader, args.ff_cap, here, floor_ma=args.ff_cap, p_gain=args.ff_pgain
        )
        print(
            f"[ff] 追従壁式: 目標=フォロワーの実位置(毎フレーム)・硬さ P={args.ff_pgain}・上限 {args.ff_cap}mA"
            "(壁はサーボ内部ループ。ホスト側の判定・平滑化なし)"
        )
        return None
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
    print(f"[ff-arm] 腕反力ON {joints} Operating_Mode={modes}(期待{[0] * len(joints)})")


def release_gripper(bus):
    """Zero the gripper current and drop its torque (safe exit)."""
    bus.write("Goal_Current", "gripper", 0, normalize=False)
    bus.write("Torque_Enable", "gripper", 0, normalize=False)


def release_arm(bus, joints=ARM_FF_JOINTS):
    """Zero the arm-joint currents and drop their torque (safe exit)."""
    for j in joints:
        bus.write("Goal_Current", j, 0, normalize=False)
        bus.write("Torque_Enable", j, 0, normalize=False)


class PwmWeightJoints:
    """XL430 joints that render the virtual weight in PWM (voltage) mode.

    The XL430-W250 has no current control, so the shoulder of a hand-moved follower
    gets its weight as a PWM duty (Operating_Mode 16, Goal_PWM in 0.113 % counts).
    Torque is enabled only while a weight is applied: with torque on and zero duty
    the driver brakes the motor, which would make the free arm feel heavy.
    Goal_PWM is zeroed *before* torque is enabled, because in position mode the same
    register holds the PWM limit (885 = 100 %); it is restored on release so the arm
    works as a follower again without a power cycle. Risk class: moves motors.
    """

    def __init__(self, dev, joints):
        self.dev = dev
        self.joints = list(joints)
        self.torque = dict.fromkeys(self.joints, False)
        self.out = dict.fromkeys(self.joints, 0)
        self.limit = dict.fromkeys(self.joints, PWM_LIMIT_DEFAULT)
        self.ready = False

    def setup(self, quiet=False):
        """Switch the joints to PWM mode with zero duty and torque off; read back."""
        if not self.joints:
            return
        bus = self.dev.bus
        ok = True
        for j in self.joints:
            bus.write("Torque_Enable", j, 0, normalize=False)
            limit = int(bus.read("PWM_Limit", j, normalize=False))
            self.limit[j] = limit if limit > 0 else PWM_LIMIT_DEFAULT
            bus.write("Operating_Mode", j, MODE_PWM, normalize=False)
            bus.write("Goal_PWM", j, 0, normalize=False)
            mode = int(bus.read("Operating_Mode", j, normalize=False))
            goal = int(bus.read("Goal_PWM", j, normalize=False))
            ok = ok and mode == MODE_PWM and goal == 0
            self.torque[j], self.out[j] = False, 0
            if not quiet:
                print(
                    f"[vw] {j}(XL430) PWM モード: Operating_Mode={mode}(期待{MODE_PWM}) "
                    f"/ Goal_PWM={goal}(期待0) / PWM_Limit={self.limit[j]}"
                )
        self.ready = ok
        if not ok and not quiet:
            print(
                "[vw] ⚠ PWM モードの設定を読み戻せません — この軸の重さは出しません(この行をClaudeへ)"
            )

    def write(self, joint, out):
        """Apply a duty to one joint; returns the duty now commanded."""
        if not self.ready:
            return 0
        bus = self.dev.bus
        if out != 0 and not self.torque[joint]:
            bus.write(
                "Goal_PWM", joint, 0, normalize=False
            )  # 上限値が残ったまま ON にしない
            bus.write("Torque_Enable", joint, 1, normalize=False)
            self.torque[joint] = True
        if abs(out - self.out[joint]) >= 3 or (out == 0 and self.out[joint] != 0):
            bus.write("Goal_PWM", joint, out, normalize=False)
            self.out[joint] = out
        if out == 0 and self.torque[joint]:
            bus.write(
                "Torque_Enable", joint, 0, normalize=False
            )  # 0 duty のブレーキを残さない
            self.torque[joint] = False
        return self.out[joint]

    def release(self):
        """Zero the duty, drop torque, restore extended-position mode and the PWM limit."""
        if not self.joints:
            return
        bus = self.dev.bus
        for j in self.joints:
            bus.write("Goal_PWM", j, 0, normalize=False)
            bus.write("Torque_Enable", j, 0, normalize=False)
            bus.write("Operating_Mode", j, MODE_EXTENDED_POSITION, normalize=False)
            bus.write("Goal_PWM", j, self.limit[j], normalize=False)
            self.torque[j], self.out[j] = False, 0
        self.ready = False


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


class HandFollower:
    """A Koch follower moved by hand: the input device of a second player.

    Wraps lerobot's KochFollower so the frame loop can treat it like KochLeader
    (connect / disconnect / is_connected / ``get_action()`` with ``<motor>.pos``
    normalized values / ``bus``). On connect the five arm joints are left
    torque-free so a person can move the arm (hold it: a raised arm drops); the
    gripper keeps its torque for the virtual wall (Mode 5). Risk class: torque
    off (arm) + moves motors (gripper wall).
    """

    def __init__(self, follower, hand_joints):
        self._follower = follower
        self._hand_joints = list(hand_joints)

    @property
    def bus(self):
        """The follower's MotorsBus (the frame loop reads/writes registers on it)."""
        return self._follower.bus

    @property
    def is_connected(self):
        """True while lerobot considers the follower connected."""
        return self._follower.is_connected

    @property
    def is_calibrated(self):
        """True when the servos hold the same calibration as the file."""
        return self._follower.is_calibrated

    def connect(self, calibrate=True):
        """Connect through lerobot (it enables torque), then free the arm joints."""
        self._follower.connect(calibrate=calibrate)
        self.free_arm()

    def free_arm(self):
        """Drop torque on the arm joints so the arm can be moved by hand."""
        for j in self._hand_joints:
            self._follower.bus.write("Torque_Enable", j, 0, normalize=False)
        print(
            f"[hand] 腕 {len(self._hand_joints)} 軸のトルクを抜きました"
            "(手で動かせる。離すと落ちるので支えること)"
        )

    def disconnect(self):
        """Disconnect through lerobot (its default drops torque on every motor)."""
        self._follower.disconnect()

    def get_action(self):
        """Read the hand-moved pose as a leader-style action dict."""
        pos = self._follower.bus.sync_read("Present_Position")
        return {f"{m}.pos": float(v) for m, v in pos.items()}


def connect_device(dev, label):
    """Connect one arm without ever leaving lerobot's calibration question unanswered.

    When the calibration file differs from what the servos hold, lerobot asks on stdin
    and ENTER writes the file into the servos' EEPROM. Under the launcher stdout is a
    log file: the question would be invisible, the process would wait forever, and a
    stray ENTER would overwrite the EEPROM (e.g. after the A and B ports were swapped).
    A run in a terminal keeps lerobot's prompt; any other run stops with the reason.
    """
    stdin = sys.stdin
    interactive = stdin is not None and stdin.isatty() and sys.stdout.isatty()
    if interactive or isinstance(dev, RemoteFollower):
        dev.connect()
        return
    dev.connect(calibrate=False)
    if dev.is_calibrated:
        return
    try:
        dev.disconnect()
    except Exception:  # noqa: BLE001, S110 - already stopping
        pass
    sys.exit(
        f"[calib] ✗ {label}の較正が、サーボ内の値とファイルで一致しません。起動を止めました。\n"
        "  よくある原因: ポートの取り違え(A と B・リーダーとフォロワー)／"
        "再キャリブレーション後にファイルを入れ替えていない\n"
        "  → config/config.json のポートと id、config/calibration/ のファイルを確認。\n"
        "  ファイルの値をサーボに書き込むと決めた場合だけ、このコマンドを端末で直接実行し、"
        "lerobot の問いに ENTER で答える"
    )


def role_error(leader_type, follower_type, follower_port):
    """Why this combination of machines and ports cannot run (None when it can).

    The usual pair is a leader machine driving a follower machine (wired or over UDP).
    A follower machine as the input device needs either no follower at all (the
    hand-moved second player) or, for the swapped handshake, a leader machine to
    drive over a wire. Two machines of the same kind are not a configuration we own.
    """
    port = str(follower_port).lower()
    leader_only, wireless = port == LEADER_ONLY, port.startswith("udp://")
    swapped = follower_type == "koch_leader"
    if swapped and leader_only:
        return "--follower-type koch_leader には追従させるリーダー機のポート(--follower-port)が要ります"
    if swapped and wireless:
        return "--follower-type koch_leader は有線のフォロワーポートでだけ使えます(無線 udp:// は不可)"
    if swapped and leader_type != "koch_follower":
        return "--follower-type koch_leader は --leader-type koch_follower(役割入替)と組で使います"
    if leader_type != "koch_leader" and not leader_only and not swapped:
        return (
            "--leader-type koch_follower は --follower-port none(手で動かす入力装置)か、"
            "--follower-type koch_leader(役割入替)と組でだけ使えます"
        )
    return None


def connect_pair(robot, teleop, roles=None):
    """Connect the follower, then the leader; a refused start leaves nothing connected."""
    roles = roles or role_names(False)
    connected = []
    try:
        for dev, label in ((robot, roles["F"]), (teleop, roles["L"])):
            if dev is not None:
                connect_device(dev, label)
                connected.append(dev)
    except SystemExit:
        for dev in connected:
            try:
                dev.disconnect()
            except Exception:  # noqa: BLE001, S110 - already stopping
                pass
        raise


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
            names = hw_error_names(v) or str(v)
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


FF_RESPONSE_BOUNDS = {  # the control port may change these while running
    "ff_slew": (1, FF_CAP_MAX_MA),
    "ff_alpha": (0.05, 1.0),
    "ff_stall": (0, 30),
    "ff_move_eps": (0.0, 20.0),
    "ff_pgain": (
        0,
        16383,
    ),  # the follow wall's stiffness (and spring / vwall on re-arm)
}


def error_law_from(args):
    """The error-reflection law with the gain, cap and response knobs of the command line."""
    return ErrorReflectionLaw(
        args.ff_ke,
        args.ff_edb,
        args.ff_sign,
        args.ff_cap,
        slew_ma=int(args.ff_slew),
        move_eps=float(args.ff_move_eps),
        stall_frames=int(args.ff_stall),
        alpha=float(args.ff_alpha),
    )


def apply_ff_response(args, cmd):
    """Take the response knobs of a control message (bounded); other keys are ignored."""
    for key, (lo, hi) in FF_RESPONSE_BOUNDS.items():
        if key in cmd:
            value = min(max(float(cmd[key]), lo), hi)
            setattr(
                args,
                key,
                int(value) if key in ("ff_slew", "ff_stall", "ff_pgain") else value,
            )


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
        "--leader-type",
        choices=list(DEVICE_MODELS),
        default="koch_leader",
        help="入力装置の機種。koch_follower=フォロワー機を手で動かす"
        "(2 人目は --follower-port none と、役割入替は --follower-type koch_leader と組)",
    )
    ap.add_argument(
        "--follower-type",
        choices=list(FOLLOWER_TYPES),
        default="koch_follower",
        help="追従する腕の機種。koch_leader=リーダー機を位置制御で追従させる"
        "(役割入替: --leader-type koch_follower と組・有線のみ)",
    )
    ap.add_argument(
        "--follower-pgain",
        type=int,
        default=DRIVEN_ARM_GAINS["Position_P_Gain"],
        help="役割入替で追従するリーダー機の腕 5 軸の Position_P_Gain"
        "(サーボ既定では重力と摩擦に負けて差が残る。負=書かずサーボ既定のまま)",
    )
    ap.add_argument(
        "--follower-igain",
        type=int,
        default=DRIVEN_ARM_GAINS["Position_I_Gain"],
        help="同 Position_I_Gain(負=書かない)",
    )
    ap.add_argument(
        "--follower-dgain",
        type=int,
        default=DRIVEN_ARM_GAINS["Position_D_Gain"],
        help="同 Position_D_Gain(負=書かない)",
    )
    ap.add_argument(
        "--arm-label",
        default="",
        help="テレメトリに載せる腕の名前(VR ページの表示と /contact の宛先。例 B / F)",
    )
    ap.add_argument(
        "--wall-scale",
        type=float,
        default=None,
        help="仮想壁の電流上限の倍率(未指定=機種既定 M077 1.0 / M288 0.45)",
    )
    ap.add_argument(
        "--vw-kt",
        type=float,
        default=None,
        help="重さ計算のトルク定数 N*m/A(未指定=機種既定 0.146 / 0.354)",
    )
    ap.add_argument(
        "--vw-pwm-scale",
        type=float,
        default=0.5,
        help="肩が XL430 のとき: 重さの倍率(物理値=ストール 1.5 N*m で duty 100%% に対する割合)",
    )
    ap.add_argument(
        "--vw-pwm-cap",
        type=int,
        default=150,
        help="肩が XL430 のとき: PWM の上限(885=100%%・既定 150≈17%%・最大 400)。0=肩の PWM を使わない",
    )
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
        choices=["spring", "error", "follow"],
        default="spring",
        help="spring=戻りバネ式(9/4 実機修正済み・既定) / error=差分反射式(8/04) / "
        "follow=追従壁式(10/25): gripper を電流制限付き位置モードにし目標=フォロワー実位置。"
        "壁はサーボ内部ループ(硬さ --ff-pgain・最大 --ff-cap)",
    )
    ap.add_argument(
        "--ff-pgain",
        type=int,
        default=FF_GRIPPER_P_GAIN,
        help="mode5 の Position_P_Gain(spring・vwall・follow の壁の硬さ。制御ポートの ff_pgain でも)",
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
        "--ff-slew",
        type=int,
        default=ErrorReflectionLaw.slew_ma,
        help="差分反射: 1 フレームの電流変化の上限[mA](立ち上がりの速さ。90 だと上限 500 まで 6 フレーム≈0.2 秒)",
    )
    ap.add_argument(
        "--ff-alpha",
        type=float,
        default=ErrorReflectionLaw.alpha,
        help="差分反射: 目標電流の平滑化係数 0.05-1(1 で平滑化なし)",
    )
    ap.add_argument(
        "--ff-stall",
        type=int,
        default=ErrorReflectionLaw.stall_frames,
        help="差分反射: フォロワーが止まったと見るまでのフレーム数(0 で待たない)",
    )
    ap.add_argument(
        "--ff-move-eps",
        type=float,
        default=ErrorReflectionLaw.move_eps,
        help="差分反射: これ以上動くフォロワーは追従中と見て反力を出さない[unit/フレーム]",
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
        "--assist",
        default="",
        help="摩擦アシストを入れる関節をカンマ列挙(elbow_flex,wrist_flex,wrist_roll)。"
        "--leader-type koch_follower のときだけ有効",
    )
    ap.add_argument(
        "--assist-cap",
        type=int,
        default=25,
        help=f"摩擦アシストの電流上限[mA](最大{ASSIST_CAP_MAX_MA})。小さい値から",
    )
    ap.add_argument(
        "--assist-gain",
        type=float,
        default=1.5,
        help="摩擦アシストの強さ[mA/(正規化unit/s)]",
    )
    ap.add_argument(
        "--assist-deadband",
        type=float,
        default=5.0,
        help="摩擦アシストの不感帯[正規化unit/s](止まっているときに出さない)",
    )
    ap.add_argument(
        "--assist-invert",
        default="",
        help="アシストで逆に重くなる関節をカンマ列挙で反転",
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
    ap.add_argument("--plot", action="store_true", help="koch-plot を自動起動")
    ap.add_argument(
        "--panel", action="store_true", help="koch-panel を自動起動(http 8780)"
    )
    ap.add_argument("--cams", default="", help="パネルに映すカメラ index(例 0 / 0,1)")
    ap.add_argument(
        "--selftest", action="store_true", help="制御則の自己診断のみ(ハード不要)"
    )
    return ap


def spawn_viewers(args, viz_ports):
    """Start the plot / panel helper processes if asked; returns the Popen list."""
    commands = viewer_commands(
        plot=args.plot,
        panel=args.panel,
        cams=args.cams,
        ctl_port=args.ctl_port,
        viz_ports=viz_ports,
    )
    children = [subprocess.Popen(cmd) for cmd in commands]
    if args.plot and viz_ports:
        print("[plot] グラフビューアを自動起動しました")
    if args.panel and viz_ports:
        print("[panel] ブラウザ操作パネル自動起動 → http://127.0.0.1:8780")
    return children


def open_csv(work_dir):
    """Create work-dir/csv/teleop_<timestamp>.csv and return (writer, file)."""
    stamp = datetime.now()
    path = work_dir / "csv" / f"teleop_{stamp:%Y%m%d_%H%M%S}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fcsv = open(path, "w", newline="")  # noqa: SIM115 - closed in main's finally
    print(f"[csv] {path}")
    return csv.writer(fcsv), fcsv


def main():  # the frame loop keeps the hardware-tested shape of mock/v0 on purpose
    """Run one leader/follower pair until Ctrl+C."""
    reconfigure_stdout()
    install_clamp_filter()
    args = build_parser().parse_args()
    if args.selftest:
        from koch import selfcheck

        selfcheck.run()
        return
    if not args.leader_port:
        sys.exit("--leader-port は必須です(ポートは推測しません。ユーザーが渡す)")
    leader_only = args.follower_port.lower() == LEADER_ONLY
    if leader_only and args.ff in ("gripper", "arm"):
        sys.exit("--follower-port none のとき使える --ff は off / vwall だけです")
    model = DEVICE_MODELS[args.leader_type]
    why = role_error(args.leader_type, args.follower_type, args.follower_port)
    if why:
        sys.exit(why)
    swapped = args.follower_type == "koch_leader"  # 役割入替: リーダー機が追従する
    roles = role_names(swapped)
    f_xl430 = xl430_joints(args.follower_type)  # 追従する腕の XL430(電流は負荷 %)
    wall_scale = (
        model["wall_scale"]
        if args.wall_scale is None
        else max(0.0, min(args.wall_scale, 2.0))
    )
    vw_kt = model["kt"] if args.vw_kt is None else max(0.05, args.vw_kt)
    vw_joints = list(model["vw_joints"])  # 重さを書く関節(機種で違う)
    pwm_spec = dict(model["pwm_joints"])  # 電流制御の無い軸(XL430)は PWM で重さを返す
    if args.vw_pwm_cap > PWM_CAP_MAX:
        args.vw_pwm_cap = PWM_CAP_MAX
        print(f"[vw] 肩 PWM の上限は安全のため最大{PWM_CAP_MAX}に制限しました")
    if args.vw_pwm_cap <= 0:
        vw_joints = [j for j in vw_joints if j not in pwm_spec]
        pwm_spec = {}
    cur_joints = [j for j in vw_joints if j not in pwm_spec]  # 電流制御で重さを書く軸
    assist_joints = [
        j for j in (x.strip() for x in args.assist.split(",")) if j in ASSIST_JOINTS
    ]
    if assist_joints and args.leader_type != "koch_follower":
        print(
            "[assist] 摩擦アシストは --leader-type koch_follower のときだけ(この腕では無効)"
        )
        assist_joints = []
    args.assist_cap = max(0, min(args.assist_cap, ASSIST_CAP_MAX_MA))
    assist_invert = {x.strip() for x in args.assist_invert.split(",") if x.strip()}
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
    # 機種差の換算(M288 は同じ電流で約 2.4 倍の力)と --ff-cap の頭打ち
    args.wall = scale_wall(args.wall, wall_scale, args.ff_cap)

    KochFollower, KochFollowerConfig, KochLeader, KochLeaderConfig, retry_errors = (
        load_lerobot()
    )
    cal_f = calibration_dir(
        args.config_dir, "koch_follower", use_lerobot_cache=args.lerobot_cache
    )
    cal_l = calibration_dir(
        args.config_dir, "koch_leader", use_lerobot_cache=args.lerobot_cache
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
            f"[link] フォロワー無線: {args.follower_port}（相手側で koch-host を先に起動）"
        )
    elif swapped:
        robot = DrivenLeader(
            KochLeader(
                KochLeaderConfig(
                    port=args.follower_port, id=args.follower_id, calibration_dir=cal_l
                )
            ),
            float(args.max_rel) if args.max_rel > 0 else None,
            gains=arm_gains(
                args.follower_pgain, args.follower_igain, args.follower_dgain
            ),
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
    if args.leader_type == "koch_follower":
        teleop = HandFollower(
            KochFollower(
                KochFollowerConfig(
                    port=args.leader_port, id=args.leader_id, calibration_dir=cal_f
                )
            ),
            model["hand_joints"],
        )
        if swapped:
            print(
                "[swap] ⚠ 役割入替: 操作する腕=フォロワー機(XL430/M288。接続直後に腕のトルクが抜ける→"
                "手で支えて起動)、追従する腕=リーダー機(M077・位置制御)。"
                "握り返しの gripper は M288 なので同じ mA で約 2.4 倍の力。"
                f" 追従側の腕ゲイン P/I/D={args.follower_pgain}/{args.follower_igain}/{args.follower_dgain}"
                "(負=サーボ既定)・腕の温度は 2 秒ごとに 1 軸ずつ監視"
            )
        else:
            print(
                "[hand] ⚠ 入力装置=フォロワー機: 接続直後に腕のトルクが抜ける(腕を手で支えて起動)。"
                f" 重さ=電流 {cur_joints}＋PWM {list(pwm_spec)}・壁の電流上限 ×{wall_scale}・Kt={vw_kt}"
            )
    else:
        teleop = KochLeader(
            KochLeaderConfig(
                port=args.leader_port, id=args.leader_id, calibration_dir=cal_l
            )
        )
    connect_pair(robot, teleop, roles)
    if robot is not None:
        check_hw_errors(robot, roles["F"])
    check_hw_errors(teleop, roles["L"])
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
    if args.ff_style == "follow" and gripper_ticks(teleop.bus) is None:
        print(
            "[ff] ⚠ リーダーgripperの較正がありません — 追従壁式は使えないので差分反射式にします"
        )
        args.ff_style = "error"
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
    pwm = PwmWeightJoints(teleop, list(pwm_spec))
    if vw_on:
        setup_arm_ff(teleop, cur_joints)
        pwm.setup()
        print(
            f"[vw] ⚠ 仮想重さON: 握っている間だけ {vw_joints} に力が出る。腕から手を離さない"
        )
    if assist_joints:
        setup_arm_ff(teleop, assist_joints)
        print(
            f"[assist] ⚠ 摩擦アシストON {assist_joints}: 動かした向きへ最大 {args.assist_cap}mA。"
            "手を離して動き続けるなら --assist-cap を下げる"
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
    farm_rr = [0]  # 役割入替: 追従するリーダー機の腕の温度を読む関節の巡回
    last_temp = [0]
    stop_req = [False]
    last_notify = [0.0]
    ff_ma = [0]
    ff_hot = [False]
    n = [0]
    reconnects = [0]
    vw_state = [VirtualWeightState()]
    vw_out = {j: 0 for j in VW_JOINTS}
    assist_state = {j: HandAssistState() for j in assist_joints}
    assist_out = dict.fromkeys(assist_joints, 0)  # 法則が出した電流
    # 重さと別に自分で書いた電流(None=重さ側が書いている)
    assist_sent: dict[str, int | None] = dict.fromkeys(assist_joints, 0)
    assist_t = [None]
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
        for label, key in ((roles["L"], "L"), (roles["F"], "F")):
            bits = hw_bits[key]
            if bits:
                names = hw_error_names(bits)
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
                "fgrip",
                f"⚠ {roles['F']}握力が上限 {cap}mA 付近 — 滑り・過負荷停止に注意",
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

    def check_follower_arm_temp():
        """Every 2 s, one arm joint of the driven leader machine: the M077 works near its limit."""
        if robot is None:
            return
        j = DRIVEN_ARM_JOINTS[farm_rr[0] % len(DRIVEN_ARM_JOINTS)]
        farm_rr[0] += 1
        try:
            at = int(robot.bus.read("Present_Temperature", j, normalize=False))
        except Exception:  # noqa: BLE001 - diagnostics must never stop the loop
            return
        _, stop = thermal_derate(guard, at, 1.0)
        if stop:
            print(
                f"\n[swap] ⛔ {roles['F']} {j} {at}°C — 止めて冷ます"
                "(サーボは 70°C で自ら止まり、腕が落ちる)"
            )
            raise_alert(
                "farmtemp", f"⛔ {roles['F']} {j} {at}°C — 過熱。止めて冷ます", ttl=10.0
            )
        elif at >= guard.derate_c:
            raise_alert("farmtemp", f"⚠ {roles['F']} {j} {at}°C — 発熱注意")

    shoulder_pwm = pwm_spec.get("shoulder_lift")

    def vw_law():
        return VirtualWeightLaw(
            scale=args.vw_scale,
            cap_ma=args.vw_cap,
            invert_shoulder="shoulder_lift" in vw_invert,
            invert_elbow="elbow_flex" in vw_invert,
            kt_nm_per_a=vw_kt,
            shoulder_gain_per_nm=(
                args.vw_pwm_scale * shoulder_pwm["pwm_limit"] / shoulder_pwm["stall_nm"]
                if shoulder_pwm
                else None
            ),
            shoulder_cap=args.vw_pwm_cap if shoulder_pwm else None,
        )

    def spring_law():
        return SpringLaw(args.ff_gain, args.ff_floor, args.ff_cap, args.ff_deadband)

    def error_law():
        return error_law_from(args)

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

    def rearm_assist(joints):
        """Put assist joints back in zero-current control (after a release or reconnect)."""
        if joints:
            setup_arm_ff(teleop, joints)
        for j in joints:
            assist_state[j], assist_out[j], assist_sent[j] = HandAssistState(), 0, 0
        assist_t[0] = None

    def assist_feedback(action):
        """One frame of friction assist on the hand-moved joints."""
        now = time.perf_counter()
        dt = 0.0 if assist_t[0] is None else now - assist_t[0]
        assist_t[0] = now
        weight_owns = cur_joints if (vw_on and vwall_on) else []
        for j in assist_joints:
            law = HandAssistLaw(
                gain_ma_s=args.assist_gain,
                cap_ma=args.assist_cap,
                deadband=args.assist_deadband,
                invert=j in assist_invert,
            )
            assist_state[j] = hand_assist_step(
                law, assist_state[j], float(action.get(f"{j}.pos", 0.0)), dt
            )
            out = assist_out[j] = assist_state[j].command_ma
            if j in weight_owns:  # 重さと同じレジスタ: weight_feedback が足して書く
                assist_sent[j] = None
                continue
            last = assist_sent[j]
            if last is None or abs(out - last) >= 2 or (out == 0 and last != 0):
                teleop.bus.write("Goal_Current", j, out, normalize=False)
                assist_sent[j] = out

    def stop_weight():
        """Drop the weight currents and free the lift/elbow joints."""
        nonlocal vw_on
        if vw_on:
            release_arm(teleop.bus, cur_joints)
            pwm.release()
            rearm_assist([j for j in cur_joints if j in assist_joints])
        vw_on = False
        vw_state[0] = VirtualWeightState()
        for j in VW_JOINTS:
            vw_out[j] = 0

    def update_twin_map(payload):
        """Take the joint spans/offsets/signs the bridge uses for the twin (weight FK)."""
        twin_map.update(twin_map_from_message(twin_map, payload))

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
                setup_arm_ff(teleop, cur_joints)
                pwm.setup()
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
            apply_ff_response(args, cmd)
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
                # 機種差の換算と、ページ側の値も --ff-cap で頭打ち
                wall[0] = scale_wall(
                    wall_from_message(cmd["vwall"]), wall_scale, args.ff_cap
                )
                wall_expire[0] = time.monotonic() + WALL_TTL_SEC
            if cmd.get("resync"):
                print("\n[ctl] 合流リセット")
                soft_resync(robot, teleop, args.fps)
            if cmd.get("stop"):
                stop_req[0] = True

    follow_last = {"tick": None, "cap": None, "pgain": None}  # 追従壁式: 最後に書いた値
    follow_scale = [1.0]  # 追従壁式の温度ディレート(上限に掛ける)

    def follow_feedback(action, fpos):
        """One frame of the follow-wall style: the leader's goal moves to where the follower is."""
        cap = int(args.ff_cap * follow_scale[0])
        goal, ff_ma[0] = follow_wall(
            action.get("gripper.pos", 0.0),
            fpos.get("gripper", action.get("gripper.pos", 0.0)),
            ticks,
            cap,
            args.ff_edb,
        )
        bus = teleop.bus
        if follow_last["tick"] is None or abs(goal - follow_last["tick"]) >= 1:
            bus.write("Goal_Position", "gripper", goal, normalize=False)
            follow_last["tick"] = goal
        if cap != follow_last["cap"]:
            bus.write("Goal_Current", "gripper", cap, normalize=False)
            follow_last["cap"] = cap
        if int(args.ff_pgain) != follow_last["pgain"]:
            bus.write("Position_P_Gain", "gripper", int(args.ff_pgain), normalize=False)
            follow_last["pgain"] = int(args.ff_pgain)

    def gripper_feedback(action, cur, fpos):
        """One frame of gripper feedback (spring, error or follow style)."""
        nonlocal ff_on
        if args.ff_style == "follow":
            follow_feedback(action, fpos)
        elif args.ff_style == "error":
            erefl[0] = error_reflection_step(
                error_law(),
                erefl[0],
                float(action.get("gripper.pos", 0.0)),
                float(fpos.get("gripper", 0.0)),
            )
            ff_ma[0] = erefl[0].command_ma
            teleop.bus.write("Goal_Current", "gripper", ff_ma[0], normalize=False)
        else:
            spring[0] = spring_step(spring_law(), spring[0], cur.get("gripper", 0.0))
            ff_ma[0] = spring[0].command_ma
            teleop.bus.write("Goal_Current", "gripper", ff_ma[0], normalize=False)
        saturated = abs(ff_ma[0]) >= args.ff_cap and args.ff_style != "follow"
        cap_frames[0] = cap_frames[0] + 1 if saturated else 0
        if cap_frames[0] >= int(args.fps):
            raise_alert("cap", f"⚠ 握り反力が上限 {args.ff_cap}mA に張り付いています")
        th_on = 120 if args.ff_style in ("error", "follow") else args.ff_floor + 80
        th_off = 60 if args.ff_style in ("error", "follow") else args.ff_floor + 40
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
                        f"\n[hw] ⚠ {roles['F']}gripper {ftemp}°C — 物を掴んだまま放置しない"
                    )
                    raise_alert(
                        "ftemp",
                        f"⚠ {roles['F']}gripper {ftemp}°C — 掴んだまま放置しない",
                    )
            arm_max = max((abs(x) for x in arm_out.values()), default=0)
            print(
                f"\r[ff] grip={cur.get('gripper', 0.0):5.0f}mA cmd={ff_ma[0]:4d}mA "
                f"arm=±{arm_max:3d}mA temp={temp}C   ",
                end="",
                flush=True,
            )
            # 減衰させるゲインは方式ごと: spring=ff_gain / error=ff_ke(差分ゲイン) / follow=上限の倍率
            if args.ff_style == "follow":
                follow_scale[0], stop = thermal_derate(guard, temp, follow_scale[0])
                derated = follow_scale[0]
            elif args.ff_style == "error":
                args.ff_ke, stop = thermal_derate(guard, temp, args.ff_ke)
                derated = args.ff_ke
            else:
                args.ff_gain, stop = thermal_derate(guard, temp, args.ff_gain)
                derated = args.ff_gain
            if stop:
                print(
                    f"\n[ff] {roles['L']}gripper {temp}°C — FBを停止します(冷えたら再起動)"
                )
                teleop.bus.write(  # error 式は Mode 0 なので床電流ではなく 0(フリー)
                    "Goal_Current",
                    "gripper",
                    0 if args.ff_style in ("error", "follow") else args.ff_floor,
                    normalize=False,
                )
                ff_on = False
                raise_alert(
                    "ltemp", f"⛔ {roles['L']}gripper {temp}°C — 力覚FBを停止", ttl=10.0
                )
            elif temp >= guard.derate_c:
                print(
                    f"\n[ff] {roles['L']}gripper {temp}°C — ゲインを{derated:.2f}に減衰"
                )
                raise_alert("ltemp", f"⚠ {roles['L']}gripper {temp}°C — ゲイン減衰中")

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
                    f"\n[vwall] {roles['L']}gripper {temp}°C — 壁を無効化します(冷えたら再起動)"
                )
                teleop.bus.write("Goal_Current", "gripper", 0, normalize=False)
                vwall_on = False
                raise_alert(
                    "ltemp", f"⛔ {roles['L']}gripper {temp}°C — 仮想壁を停止", ttl=10.0
                )
            elif temp >= guard.derate_c:
                raise_alert("ltemp", f"⚠ {roles['L']}gripper {temp}°C — 発熱注意")

    def weight_feedback(action, engaged):
        """One frame of virtual weight: grasped mass × tip lever → lift/elbow current."""
        lift, elbow, wrist = (
            norm_to_rad(float(action.get(f"{j}.pos", 0.0)), twin_map[j])
            for j in ("shoulder_lift", "elbow_flex", "wrist_flex")
        )
        levers = tip_levers(lift, elbow, wrist)
        mass = wall[0].mass_g if wall[0] else 0.0
        vw_state[0] = virtual_weight_step(vw_law(), vw_state[0], engaged, mass, levers)
        outs = {
            "shoulder_lift": vw_state[0].shoulder_ma,
            "elbow_flex": vw_state[0].elbow_ma,
        }
        for j in vw_joints:
            out = round(outs[j])
            if (
                j in pwm_spec
            ):  # XL430: PWM(トルク ON/OFF の順序は PwmWeightJoints が持つ)
                vw_out[j] = pwm.write(j, out)
                continue
            out += assist_out.get(j, 0)  # 摩擦アシスト(同じ Goal_Current に足す)
            if abs(out - vw_out[j]) >= 3 or (out == 0 and vw_out[j] != 0):
                teleop.bus.write("Goal_Current", j, out, normalize=False)
                vw_out[j] = out
        if engaged and any(
            abs(vw_out[j]) >= (args.vw_pwm_cap if j in pwm_spec else args.vw_cap)
            for j in vw_joints
        ):
            raise_alert(
                "vwcap",
                f"⚠ 重さの出力が上限（電流 {args.vw_cap}mA／肩 PWM {args.vw_pwm_cap}）"
                " — 物体が重すぎるか倍率が高い",
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
            params = {
                "ff_gain": args.ff_gain,
                "ff_ke": args.ff_ke,
                "ff_cap": args.ff_cap,
                "ff_floor": args.ff_floor,
                "ff_slew": args.ff_slew,
                "ff_alpha": args.ff_alpha,
                "ff_stall": args.ff_stall,
                "ff_move_eps": args.ff_move_eps,
                "ff_pgain": args.ff_pgain,
                "arm_gain": args.ff_arm_gain,
                "arm_cap": args.ff_arm_cap,
                "vw_scale": args.vw_scale,
                "vw_cap": args.vw_cap,
                "max_rel": (robot.config.max_relative_target or 0) if robot else 0,
            }
            frame_dict = telemetry_frame(
                t=t,
                action=action,
                cur=cur,
                fpos=fpos,
                ff_ma=ff_ma[0],
                mode=args.ff,
                temp=last_temp[0],
                reconnects=reconnects[0],
                n=n[0],
                leader_only=leader_only,
                arm_label=args.arm_label,
                device=args.leader_type,
                alerts=active_alerts(),
                hw=hw_bits,
                link=robot.link_stats() if isinstance(robot, RemoteFollower) else None,
                vw_on=vw_on,
                vw_joints=vw_joints,
                pwm_joints=pwm_spec,
                vw_out=vw_out,
                vwall_on=vwall_on,
                wall=wall[0],
                wall_engaged=engaged,
                params=params,
            )
            payload = json.dumps(frame_dict).encode()
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
                m: to_signed16(int(v)) * (0.1 if m in f_xl430 else 1.0)
                for m, v in cur_raw.items()
            }
            fpos = robot.bus.sync_read("Present_Position")
            check_follower_grip_alert(cur)
            if remote_follower:
                check_link_alert()
        if assist_joints:
            assist_feedback(action)
        if ff_on:
            gripper_feedback(action, cur, fpos)
        elif vwall_on:
            wall_feedback(action)
        if ff_arm:
            arm_feedback(cur)
        if n[0] % TEMP_CHECK_EVERY == TEMP_CHECK_EVERY // 4:
            check_hw_alert()
        if swapped and n[0] % TEMP_CHECK_EVERY == TEMP_CHECK_EVERY * 3 // 4:
            check_follower_arm_temp()
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
            connect_device(robot, roles["F"])
        if not teleop.is_connected:
            connect_device(teleop, roles["L"])
        if robot is not None:
            check_hw_errors(robot, roles["F"])
        check_hw_errors(teleop, roles["L"])
        if args.ff in ("gripper", "arm", "vwall"):
            ff_anchor[0] = arm_gripper_ff(teleop, args, anchor=ff_anchor[0])
            follow_last.update(tick=None, cap=None, pgain=None)
            ff_on = args.ff in ("gripper", "arm")
        if args.ff == "arm":
            setup_arm_ff(teleop, ARM_FF_JOINTS)
            ff_arm = True
        if vw_on:
            setup_arm_ff(teleop, cur_joints)
            pwm.setup()
            vw_state[0] = VirtualWeightState()
            for j in VW_JOINTS:
                vw_out[j] = 0
        rearm_assist(assist_joints)
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
                release_arm(teleop.bus, cur_joints)
                pwm.release()
            if assist_joints:
                release_arm(teleop.bus, assist_joints)
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

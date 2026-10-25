"""Servo models, joint tables, register values and the helpers every entry point shares.

Pure data and pure functions: no lerobot, no serial.
"""

from typing import TypedDict

MOTORS = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]
XL430_JOINTS = {"shoulder_pan", "shoulder_lift"}  # フォロワーの XL430（負荷 0.1%/unit）
FOLLOWER_TYPES = (
    "koch_follower",
    "koch_leader",
)  # 追従する腕の機種(koch_leader=役割入替)
# 役の名前。役割入替(操作者がフォロワー機を持ち、リーダー機が追従)では機体も名乗る
ROLE_NAMES = {"L": "リーダー", "F": "フォロワー"}
SWAPPED_ROLE_NAMES = {"L": "リーダー役(フォロワー機)", "F": "フォロワー役(リーダー機)"}
ARM_FF_JOINTS = ["elbow_flex", "wrist_flex", "wrist_roll"]  # 電流が読める XL330 の腕3軸
VW_JOINTS = [
    "shoulder_lift",
    "elbow_flex",
]  # 仮想重さを返すリーダー腕関節(どちらも XL330-M077)


# 入力装置の機種差。koch_follower を手で動かす 2 人目用: 肩 2 軸は XL430-W250 で
# 電流制御(Mode 0/5)が無い → 肩の重さは PWM モード(Mode 16・Goal_PWM 0.113 %/count・
# 上限 885)で返す。ストール 1.5 N*m @ 12 V なので duty ≈ トルク / 1.5 N*m。肘は M288 の電流制御。
# M288 のストールは M077 の約 2.4 倍(0.52 vs 0.215 N*m @ 5V, 同じ 1.47 A)なので Kt が違い、
# 握りの電流上限も同じ力になるよう縮める。
class PwmJoint(TypedDict):
    """A joint without current control (XL430): weight is rendered in PWM mode."""

    stall_nm: float
    pwm_limit: int


class DeviceModel(TypedDict):
    """What differs between a Koch leader and a Koch follower used as the input device."""

    kt: float
    vw_joints: list[str]
    pwm_joints: dict[str, PwmJoint]
    wall_scale: float
    hand_joints: list[str]


DEVICE_MODELS: dict[str, DeviceModel] = {
    "koch_leader": {
        "kt": 0.146,
        "vw_joints": ["shoulder_lift", "elbow_flex"],
        "pwm_joints": {},
        "wall_scale": 1.0,
        "hand_joints": [],
    },
    "koch_follower": {
        "kt": 0.354,
        "vw_joints": ["shoulder_lift", "elbow_flex"],
        "pwm_joints": {"shoulder_lift": {"stall_nm": 1.5, "pwm_limit": 885}},
        "wall_scale": 0.45,
        "hand_joints": [
            "shoulder_pan",
            "shoulder_lift",
            "elbow_flex",
            "wrist_flex",
            "wrist_roll",
        ],
    },
}

# 位置ループ剛性。既定のままでは偏差 158 ticks で ~124mA しか要求せず、Goal_Current を
# 450 まで上げても実電流が頭打ち（9/4 実測）。Operating_Mode を書くと既定へ戻るので
# 必ずモード設定の後に書く。
FF_GRIPPER_P_GAIN = 800
FF_CAP_MAX_MA = 900
FF_CAP_WARN_MA = 500
ARM_CAP_MAX_MA = 400
ASSIST_JOINTS = [
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
]  # 手で動かすフォロワー機の XL330-M288(電流制御できる軸)
ASSIST_CAP_MAX_MA = (
    80  # 摩擦アシストの上限。関節の摩擦より大きいと手を離しても動き続ける
)
PWM_CAP_MAX = 400  # 肩 PWM の上限(885 = 100 %)。400 ≈ 45 % ≈ 0.68 N*m
PWM_LIMIT_DEFAULT = 885
MODE_PWM = 16
MODE_EXTENDED_POSITION = 4

HW_ERROR_BITS = {
    0: "入力電圧",
    2: "過熱",
    3: "エンコーダ",
    4: "電気ショック",
    5: "過負荷",
}


def role_names(swapped: bool) -> dict[str, str]:
    """Operator-facing names of the two roles; the machine is named once the roles are swapped."""
    return dict(SWAPPED_ROLE_NAMES if swapped else ROLE_NAMES)


def xl430_joints(device: str) -> frozenset[str]:
    """Joints whose ``Present_Current`` is really the XL430 load (0.1 %/unit) on this machine."""
    return frozenset(XL430_JOINTS) if device == "koch_follower" else frozenset()


def to_signed16(v):
    """Interpret a 16-bit register value as signed."""
    return v - 65536 if v > 32767 else v


def gripper_ticks(bus):
    """Return (closed_tick, open_tick) from calibration, or None if uncalibrated.

    実測（較正値 2036-2773 に対し握り込み時の生値 2106）: 握ると raw は range_min 方向へ
    減る。したがって range_max が全開端、range_min が閉端（9/4 commit）。
    """
    cal = getattr(bus, "calibration", None) or {}
    g = cal.get("gripper")
    if g is None:
        return None
    if int(getattr(g, "drive_mode", 0) or 0) == 1:  # 反転モーターは端が入れ替わる
        return int(g.range_max), int(g.range_min)
    return int(g.range_min), int(g.range_max)


def hw_error_names(bits: int) -> str:
    """Names of the set bits of Hardware_Error_Status, joined with ``/`` (empty if none)."""
    return "/".join(n for b, n in HW_ERROR_BITS.items() if bits >> b & 1)

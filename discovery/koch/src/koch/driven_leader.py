"""A Koch leader arm driven as the follower of a swapped pair (the handshake, 10/25).

The handshake can run with the machines exchanged: the operator holds the Koch
*follower* arm (XL430 shoulders, XL330-M288 elsewhere: about 2.4 times the torque
of the leader's M077 at the same current, so the grip feedback is stronger) and
the Koch *leader* arm follows and is the arm offered to the visitor.

lerobot's ``KochLeader`` knows the leader's servo models and calibration but only
reads; ``DrivenLeader`` wraps it so the frame loop can drive it like
``KochFollower``: ``send_action`` with ``<motor>.pos`` normalized goals,
``config.max_relative_target`` (the panel changes it), ``bus``, connect and
disconnect. lerobot configures the arm as an input device (arm joints in extended
position mode with torque off, the gripper in current-based position mode); the
position-loop gains of the arm joints are written next and read back, then torque
is enabled on them so the arm holds and follows. The gains matter: with the
servo's own defaults the M077 arm lost to gravity and friction on the bench
(2026-10-25: elbow 9 and wrist 12 normalized units behind, stepwise motion, slow
return), so the defaults here are the values lerobot gives the follower's elbow.
Writing ``Operating_Mode`` resets the gains, which is why they are written after
lerobot's configure. A servo takes its present position as the goal when torque
goes on, so nothing jumps; the launch ramp (``soft_resync``) then brings the arm to
the operator's pose. Disconnecting goes through lerobot, whose default drops
torque on every motor. Risk class: moves motors.
"""

from dataclasses import dataclass

from koch.motors import MOTORS

ARM_JOINTS = [m for m in MOTORS if m != "gripper"]
# lerobot's KochFollower gives its M288 elbow P 1500 / I 0 / D 600 "to close the gap
# between recorded states and actions"; the M077 arm needs at least that on every joint.
ARM_GAINS = {"Position_P_Gain": 1500, "Position_I_Gain": 0, "Position_D_Gain": 600}
GAIN_MAX = 16383  # the registers are 14-bit


def arm_gains(p: int, i: int, d: int) -> dict[str, int]:
    """The gain registers to write from the command line: negatives are left alone."""
    wanted = {"Position_P_Gain": p, "Position_I_Gain": i, "Position_D_Gain": d}
    return {
        name: min(int(value), GAIN_MAX) for name, value in wanted.items() if value >= 0
    }


def clamp_goals(
    goal: dict[str, float], present: dict[str, float], limit: float | None
) -> dict[str, float]:
    """Cap each goal to ``limit`` away from its present position (lerobot's safety limiter).

    Motors without a present value pass through; ``None`` disables the limiter.
    """
    if limit is None:
        return dict(goal)
    return {
        motor: (
            present[motor] + max(-limit, min(limit, value - present[motor]))
            if motor in present
            else value
        )
        for motor, value in goal.items()
    }


@dataclass
class DrivenLeaderConfig:
    """The one setting the frame loop and the panel change at run time."""

    max_relative_target: float | None


class DrivenLeader:
    """lerobot's KochLeader driven in position control, with KochFollower's interface."""

    def __init__(
        self,
        leader,
        max_relative_target: float | None,
        gains: dict[str, int] | None = None,
    ):
        self._leader = leader
        self.config = DrivenLeaderConfig(max_relative_target)
        self.gains = dict(ARM_GAINS if gains is None else gains)
        self.gains_ok = True

    @property
    def bus(self):
        """The leader's MotorsBus (the frame loop reads currents and errors on it)."""
        return self._leader.bus

    @property
    def is_connected(self) -> bool:
        """True while lerobot considers the leader connected."""
        return self._leader.is_connected

    @property
    def is_calibrated(self) -> bool:
        """True when the servos hold the same calibration as the file."""
        return self._leader.is_calibrated

    def connect(self, calibrate: bool = True) -> None:
        """Connect through lerobot, write the arm gains, then enable torque so the arm follows."""
        self._leader.connect(calibrate=calibrate)
        self.setup_arm()
        self._leader.bus.enable_torque(ARM_JOINTS)
        print(
            f"[swap] リーダー機の腕 {len(ARM_JOINTS)} 軸にトルクを入れました"
            "(位置制御で追従する。終了時に抜ける)"
        )

    def setup_arm(self) -> None:
        """Write the position-loop gains on the arm joints (torque still off) and read them back."""
        if not self.gains:
            return
        bus = self._leader.bus
        for joint in ARM_JOINTS:
            for name, value in self.gains.items():
                bus.write(name, joint, value, normalize=False)
        back = {
            joint: {
                name: int(bus.read(name, joint, normalize=False)) for name in self.gains
            }
            for joint in ARM_JOINTS
        }
        self.gains_ok = all(back[j] == self.gains for j in ARM_JOINTS)
        shown = "/".join(f"{n.split('_')[1]}={v}" for n, v in self.gains.items())
        if self.gains_ok:
            print(f"[swap] リーダー機の腕ゲイン {shown} を書きました(読み戻し一致)")
            return
        off = {j: g for j, g in back.items() if g != self.gains}
        print(
            f"[swap] ⚠ 腕ゲインの読み戻しが違います {off} — サーボ既定のまま追従します(この行をClaudeへ)"
        )

    def disconnect(self) -> None:
        """Disconnect through lerobot (its default drops torque on every motor)."""
        self._leader.disconnect()

    def send_action(self, action: dict[str, float]) -> dict[str, float]:
        """Write the normalized goals (capped by the limiter) and return what was sent."""
        goal = {
            key.removesuffix(".pos"): float(value)
            for key, value in action.items()
            if key.endswith(".pos")
        }
        limit = self.config.max_relative_target
        if limit is not None:
            present = self._leader.bus.sync_read("Present_Position")
            goal = clamp_goals(goal, present, float(limit))
        self._leader.bus.sync_write("Goal_Position", goal)
        return {f"{motor}.pos": value for motor, value in goal.items()}

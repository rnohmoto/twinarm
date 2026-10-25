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
position mode with torque off, the gripper in current-based position mode); torque
is enabled on the arm joints right after, so the arm holds and follows. A servo
takes its present position as the goal when torque goes on, so nothing jumps; the
launch ramp (``soft_resync``) then brings the arm to the operator's pose.
Disconnecting goes through lerobot, whose default drops torque on every motor.
Risk class: moves motors.
"""

from dataclasses import dataclass

from koch.motors import MOTORS

ARM_JOINTS = [m for m in MOTORS if m != "gripper"]


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

    def __init__(self, leader, max_relative_target: float | None):
        self._leader = leader
        self.config = DrivenLeaderConfig(max_relative_target)

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
        """Connect through lerobot, then enable torque on the arm joints so the arm follows."""
        self._leader.connect(calibrate=calibrate)
        self._leader.bus.enable_torque(ARM_JOINTS)
        print(
            f"[swap] リーダー機の腕 {len(ARM_JOINTS)} 軸にトルクを入れました"
            "(位置制御で追従する。終了時に抜ける)"
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

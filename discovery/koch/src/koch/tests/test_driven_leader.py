"""A Koch leader arm driven as the follower of a swapped pair, on a fake lerobot leader."""

import pytest

from koch.driven_leader import (
    ARM_GAINS,
    ARM_JOINTS,
    DrivenLeader,
    arm_gains,
    clamp_goals,
)

PRESENT = {
    "shoulder_pan": 0.0,
    "shoulder_lift": 10.0,
    "elbow_flex": -10.0,
    "wrist_flex": 0.0,
    "wrist_roll": 0.0,
    "gripper": 50.0,
}


class FakeBus:
    def __init__(self, present):
        self.present = present
        self.calls = []
        self.reg = {}

    def enable_torque(self, motors=None):
        self.calls.append(("enable_torque", list(motors) if motors else None))

    def write(self, data_name, motor, value, *, normalize=True):
        self.reg[(data_name, motor)] = int(value)
        self.calls.append(("write", data_name, motor, int(value)))

    def read(self, data_name, motor, *, normalize=True):
        return self.reg.get((data_name, motor), 0)

    def sync_read(self, data_name, motors=None, *, normalize=True):
        self.calls.append(("sync_read", data_name))
        return dict(self.present)

    def sync_write(self, data_name, values):
        self.calls.append(("sync_write", data_name, dict(values)))


class FakeLeader:
    """What DrivenLeader uses of lerobot's KochLeader."""

    def __init__(self, present):
        self.bus = FakeBus(present)
        self.is_connected = False
        self.is_calibrated = True
        self.connects = []

    def connect(self, calibrate=True):
        self.connects.append(calibrate)
        self.is_connected = True

    def disconnect(self):
        self.is_connected = False


@pytest.fixture
def driven() -> tuple[FakeLeader, DrivenLeader]:
    leader = FakeLeader(PRESENT)
    return leader, DrivenLeader(leader, 20.0, gains={})


@pytest.mark.unit
def test_arm_joints_are_every_motor_but_the_gripper() -> None:
    assert ARM_JOINTS == [
        "shoulder_pan",
        "shoulder_lift",
        "elbow_flex",
        "wrist_flex",
        "wrist_roll",
    ]


@pytest.mark.unit
def test_connect_passes_the_calibrate_flag_then_enables_torque_on_the_arm_joints(
    driven,
) -> None:
    leader, robot = driven
    robot.connect(calibrate=False)
    assert leader.connects == [False]
    assert leader.bus.calls == [("enable_torque", ARM_JOINTS)]
    assert robot.is_connected and robot.is_calibrated and robot.bus is leader.bus


@pytest.mark.unit
def test_send_action_clamps_each_goal_to_the_limiter_and_writes_goal_positions(
    driven,
) -> None:
    leader, robot = driven
    sent = robot.send_action(
        {
            "shoulder_lift.pos": 100.0,
            "elbow_flex.pos": -40.0,
            "gripper.pos": 45.0,
            "other": 1,
        }
    )
    assert sent == {
        "shoulder_lift.pos": 30.0,
        "elbow_flex.pos": -30.0,
        "gripper.pos": 45.0,
    }
    assert leader.bus.calls == [
        ("sync_read", "Present_Position"),
        (
            "sync_write",
            "Goal_Position",
            {"shoulder_lift": 30.0, "elbow_flex": -30.0, "gripper": 45.0},
        ),
    ]


@pytest.mark.unit
def test_without_a_limiter_the_goals_go_through_unread(driven) -> None:
    leader, robot = driven
    robot.config.max_relative_target = None
    robot.send_action({"shoulder_lift.pos": 100.0})
    assert leader.bus.calls == [
        ("sync_write", "Goal_Position", {"shoulder_lift": 100.0})
    ]


@pytest.mark.unit
def test_the_limiter_can_be_changed_at_run_time_like_lerobots(driven) -> None:
    _, robot = driven
    robot.config.max_relative_target = 5.0
    assert robot.send_action({"shoulder_lift.pos": 100.0}) == {
        "shoulder_lift.pos": 15.0
    }


@pytest.mark.unit
def test_disconnect_goes_through_lerobot(driven) -> None:
    _, robot = driven
    robot.connect()
    robot.disconnect()
    assert not robot.is_connected


@pytest.mark.unit
def test_clamp_goals_is_pure_and_keeps_motors_without_a_present_value() -> None:
    present = {"a": 0.0}
    goal = {"a": 50.0, "b": 7.0}
    assert clamp_goals(goal, present, 20.0) == {"a": 20.0, "b": 7.0}
    assert clamp_goals({"a": -50.0}, present, 20.0) == {"a": -20.0}
    assert clamp_goals(goal, present, None) == goal
    assert goal == {"a": 50.0, "b": 7.0} and present == {"a": 0.0}


@pytest.mark.unit
def test_default_gains_are_lerobots_follower_elbow_values() -> None:
    assert ARM_GAINS == {
        "Position_P_Gain": 1500,
        "Position_I_Gain": 0,
        "Position_D_Gain": 600,
    }


@pytest.mark.unit
def test_arm_gains_drops_negative_values_and_clamps_to_the_register_range() -> None:
    assert arm_gains(1500, 0, 600) == ARM_GAINS
    assert arm_gains(-1, 0, 600) == {"Position_I_Gain": 0, "Position_D_Gain": 600}
    assert arm_gains(-1, -1, -1) == {}
    assert arm_gains(99999, 0, 0)["Position_P_Gain"] == 16383


@pytest.mark.unit
def test_connect_writes_the_gains_on_every_arm_joint_before_torque_goes_on() -> None:
    leader = FakeLeader(PRESENT)
    robot = DrivenLeader(
        leader, 20.0, gains={"Position_P_Gain": 1500, "Position_D_Gain": 600}
    )
    robot.connect()
    writes = [c for c in leader.bus.calls if c[0] == "write"]
    assert {c[2] for c in writes} == set(ARM_JOINTS)  # never the gripper
    assert {(c[1], c[3]) for c in writes} == {
        ("Position_P_Gain", 1500),
        ("Position_D_Gain", 600),
    }
    assert leader.bus.calls.index(("enable_torque", ARM_JOINTS)) > max(
        i for i, c in enumerate(leader.bus.calls) if c[0] == "write"
    )
    assert robot.gains_ok


@pytest.mark.unit
def test_connect_with_no_gains_leaves_the_servo_defaults(driven) -> None:
    leader, robot = driven
    robot.connect()
    assert leader.bus.calls == [("enable_torque", ARM_JOINTS)]
    assert robot.gains_ok


@pytest.mark.unit
def test_a_gain_that_does_not_read_back_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leader = FakeLeader(PRESENT)
    monkeypatch.setattr(leader.bus, "read", lambda name, motor, *, normalize=True: 400)
    robot = DrivenLeader(leader, 20.0, gains={"Position_P_Gain": 1500})
    robot.connect()
    assert not robot.gains_ok
    assert robot.is_connected  # the arm still follows, with the servo's own gain

"""The in-process stand-in for lerobot's KochFollower used by ``koch-host --sim``."""

import pytest

from koch import sim_follower as sf
from koch.motors import MOTORS


@pytest.mark.unit
def test_goals_are_clamped_by_the_relative_target_limit() -> None:
    follower = sf.SimFollower(max_rel=10)
    follower.send_action({"gripper.pos": 90.0})
    assert follower.goal["gripper"] == 60.0  # starts at 50


@pytest.mark.unit
def test_no_limit_means_no_clamp() -> None:
    follower = sf.SimFollower(max_rel=None)
    follower.send_action({"gripper.pos": 90.0})
    assert follower.goal["gripper"] == 90.0


@pytest.mark.unit
def test_positions_converge_on_the_goals() -> None:
    follower = sf.SimFollower(max_rel=None)
    follower.send_action({"elbow_flex.pos": 30.0})
    for _ in range(60):
        follower.step(1 / 30)
    assert follower.pos["elbow_flex"] == pytest.approx(30.0, abs=0.01)


@pytest.mark.unit
def test_simulated_current_grows_with_the_error_and_fits_16_bits() -> None:
    follower = sf.SimFollower(max_rel=None)
    follower.send_action({"elbow_flex.pos": 100.0})
    current = follower.bus.sync_read("Present_Current")
    assert current["elbow_flex"] == 2000 and current["gripper"] == 0
    assert all(0 <= v <= 0xFFFF for v in current.values())


@pytest.mark.unit
def test_gripper_cap_round_trips_and_errors_are_zero() -> None:
    follower = sf.SimFollower(max_rel=None)
    follower.bus.write("Goal_Current", "gripper", 450)
    assert follower.bus.read("Goal_Current", "gripper") == 450
    assert follower.bus.sync_read("Hardware_Error_Status") == dict.fromkeys(MOTORS, 0)
    assert follower.bus.read("Present_Temperature", "gripper") == 36


@pytest.mark.unit
def test_connect_and_disconnect_toggle_the_flag() -> None:
    follower = sf.SimFollower(max_rel=None)
    follower.connect()
    assert follower.is_connected
    follower.disconnect()
    assert not follower.is_connected

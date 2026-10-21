"""Servo models, joint tables and register helpers shared by the entry points."""

from types import SimpleNamespace

import pytest

from koch import motors


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "signed"), [(0, 0), (32767, 32767), (32768, -32768), (65535, -1)]
)
def test_to_signed16(raw: int, signed: int) -> None:
    assert motors.to_signed16(raw) == signed


@pytest.mark.unit
def test_the_six_koch_motors_in_bus_order() -> None:
    assert motors.MOTORS == [
        "shoulder_pan",
        "shoulder_lift",
        "elbow_flex",
        "wrist_flex",
        "wrist_roll",
        "gripper",
    ]
    assert motors.XL430_JOINTS <= set(motors.MOTORS)


@pytest.mark.unit
def test_device_models_cover_both_input_devices() -> None:
    assert set(motors.DEVICE_MODELS) == {"koch_leader", "koch_follower"}
    assert motors.DEVICE_MODELS["koch_leader"]["kt"] == 0.146
    assert motors.DEVICE_MODELS["koch_follower"]["wall_scale"] == 0.45
    assert "shoulder_lift" in motors.DEVICE_MODELS["koch_follower"]["pwm_joints"]


@pytest.mark.unit
def test_gripper_ticks_needs_a_calibration() -> None:
    assert motors.gripper_ticks(SimpleNamespace(calibration=None)) is None
    assert motors.gripper_ticks(SimpleNamespace(calibration={})) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("drive_mode", "expected"), [(0, (2036, 2773)), (1, (2773, 2036))]
)
def test_gripper_ticks_are_closed_then_open_and_swap_for_a_reversed_motor(
    drive_mode: int, expected: tuple[int, int]
) -> None:
    cal = {
        "gripper": SimpleNamespace(
            range_min=2036, range_max=2773, drive_mode=drive_mode
        )
    }
    assert motors.gripper_ticks(SimpleNamespace(calibration=cal)) == expected


@pytest.mark.unit
def test_hw_error_names_lists_the_set_bits() -> None:
    assert motors.hw_error_names(1 << 5) == "過負荷"
    assert motors.hw_error_names((1 << 2) | (1 << 5)) == "過熱/過負荷"
    assert motors.hw_error_names(0) == ""

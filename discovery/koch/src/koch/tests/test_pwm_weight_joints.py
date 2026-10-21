"""Write order of the PWM shoulder (XL430 without current control) on a fake bus.

The order is safety critical: Goal_PWM must be zero before torque goes on (in
position mode the register still holds the 885 limit), and the mode and limit
must be restored on release.
"""

import pytest

from koch.selfcheck import FakeDevice
from koch.teleop import PwmWeightJoints


@pytest.fixture
def pwm() -> tuple[PwmWeightJoints, list[tuple[str, int]]]:
    dev = FakeDevice()
    joints = PwmWeightJoints(dev, ["shoulder_lift"])
    joints.setup(quiet=True)
    return joints, dev.bus.log


@pytest.mark.unit
def test_setup_drops_torque_switches_to_pwm_mode_and_zeroes_the_pwm(pwm) -> None:
    joints, log = pwm
    assert joints.ready
    assert log == [("Torque_Enable", 0), ("Operating_Mode", 16), ("Goal_PWM", 0)]


@pytest.mark.unit
def test_first_nonzero_write_zeroes_before_torque_on(pwm) -> None:
    joints, log = pwm
    log.clear()
    assert joints.write("shoulder_lift", 120) == 120
    assert log == [("Goal_PWM", 0), ("Torque_Enable", 1), ("Goal_PWM", 120)]


@pytest.mark.unit
def test_a_small_change_writes_nothing(pwm) -> None:
    joints, log = pwm
    joints.write("shoulder_lift", 120)
    log.clear()
    assert joints.write("shoulder_lift", 121) == 120
    assert log == []


@pytest.mark.unit
def test_zero_drops_torque(pwm) -> None:
    joints, log = pwm
    joints.write("shoulder_lift", 120)
    log.clear()
    assert joints.write("shoulder_lift", 0) == 0
    assert log == [("Goal_PWM", 0), ("Torque_Enable", 0)]


@pytest.mark.unit
def test_release_restores_position_mode_and_the_limit_then_refuses_writes(pwm) -> None:
    joints, log = pwm
    log.clear()
    joints.release()
    assert log == [
        ("Goal_PWM", 0),
        ("Torque_Enable", 0),
        ("Operating_Mode", 4),
        ("Goal_PWM", 885),
    ]
    assert joints.write("shoulder_lift", 50) == 0


@pytest.mark.unit
def test_setup_is_not_ready_when_the_readback_disagrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev = FakeDevice()
    monkeypatch.setattr(dev.bus, "read", lambda name, motor, *, normalize=True: 999)
    joints = PwmWeightJoints(dev, ["shoulder_lift"])
    joints.setup(quiet=True)
    assert not joints.ready

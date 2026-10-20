"""The operator self-test behind ``koch-teleop --selftest``: no hardware, no lerobot.

Runs the mirrored control laws on fixed vectors and checks the safety-critical
write order of the PWM shoulder on a fake bus. It stays runnable on a field
machine without pytest; ``tests/`` cover the same ground in finer grain.
"""

import math

from koch.feedback import (
    LINK_M,
    ErrorReflectionLaw,
    ErrorReflectionState,
    HandAssistLaw,
    HandAssistState,
    SpringLaw,
    SpringState,
    ThermalGuard,
    VirtualWall,
    VirtualWeightLaw,
    VirtualWeightState,
    error_reflection_step,
    hand_assist_step,
    spring_step,
    thermal_derate,
    tip_levers,
    virtual_wall_step,
    virtual_weight_step,
    wall_tick,
)
from koch.teleop import PwmWeightJoints


def run():
    """Run the mirrored laws on fixed vectors and the PWM write order (no hardware)."""
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
    m288 = VirtualWeightLaw(scale=0.12, cap_ma=400, alpha=1.0, kt_nm_per_a=0.354)
    w2 = virtual_weight_step(m288, VirtualWeightState(), True, 100.0, (0.1, 0.1))
    assert abs(w2.elbow_ma - 80.6 * 0.146 / 0.354) < 0.5, w2
    pwm_law = VirtualWeightLaw(
        alpha=1.0, shoulder_gain_per_nm=0.5 * 885 / 1.5, shoulder_cap=150
    )
    light = virtual_weight_step(pwm_law, VirtualWeightState(), True, 45.0, (0.25, 0.1))
    heavy = virtual_weight_step(pwm_law, VirtualWeightState(), True, 300.0, (0.25, 0.1))
    assert 32.0 < light.shoulder_ma < 33.0 and heavy.shoulder_ma == 150.0, (
        light,
        heavy,
    )
    assist = HandAssistLaw(alpha=1.0, slew_ma=1000)
    a0 = hand_assist_step(assist, HandAssistState(), 0.0, 1 / 30)
    slow = hand_assist_step(assist, a0, 0.5, 1 / 30)
    fast = hand_assist_step(assist, a0, 2.0, 1 / 30)
    wild = hand_assist_step(assist, a0, -12.0, 1 / 30)
    assert (a0.command_ma, slow.command_ma, fast.command_ma, wild.command_ma) == (
        0,
        15,
        25,
        0,
    ), (slow, fast, wild)
    check_pwm_joints()
    print(
        "selftest OK: spring/error/vwall/weight/assist/thermal 制御則は twinarm domain と同じ値"
        "・肩 PWM の書込み順序"
    )


class FakeBus:
    """Selftest stand-in for MotorsBus: records writes, reads back the last value."""

    def __init__(self):
        self.reg = {
            ("PWM_Limit", "shoulder_lift"): 885,
            ("Goal_PWM", "shoulder_lift"): 885,
        }
        self.log = []

    def write(self, name, motor, value, *, normalize=True):
        """Store and log one register write."""
        self.reg[(name, motor)] = int(value)
        self.log.append((name, int(value)))

    def read(self, name, motor, *, normalize=True):
        """Return the last value written to a register (0 if never written)."""
        return self.reg.get((name, motor), 0)


class FakeDevice:
    """Selftest stand-in for a device with a ``bus``."""

    def __init__(self):
        self.bus = FakeBus()


def check_pwm_joints():
    """Check the safety-critical write order of the PWM shoulder with a fake bus."""
    dev = FakeDevice()
    log = dev.bus.log
    pwm = PwmWeightJoints(dev, ["shoulder_lift"])
    pwm.setup(quiet=True)
    assert pwm.ready, "PWM setup not confirmed"
    assert log == [("Torque_Enable", 0), ("Operating_Mode", 16), ("Goal_PWM", 0)], log
    log.clear()
    assert pwm.write("shoulder_lift", 120) == 120
    # 0 を書いてからトルク ON(位置モードの上限値 885 が残ったまま ON にしない)
    assert log == [("Goal_PWM", 0), ("Torque_Enable", 1), ("Goal_PWM", 120)], log
    log.clear()
    assert pwm.write("shoulder_lift", 121) == 120 and log == [], log
    assert pwm.write("shoulder_lift", 0) == 0
    assert log == [("Goal_PWM", 0), ("Torque_Enable", 0)], log
    log.clear()
    pwm.release()
    assert log == [
        ("Goal_PWM", 0),
        ("Torque_Enable", 0),
        ("Operating_Mode", 4),
        ("Goal_PWM", 885),
    ], log
    assert pwm.write("shoulder_lift", 50) == 0  # release 後は書かない

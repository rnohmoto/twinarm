"""The control laws mirrored from ``twinarm.domain.gripper_feedback``.

The vectors are the ones ``koch-teleop --selftest`` has always pinned, split one
behaviour per test, plus the edges the frame loop relies on.
"""

import math

import pytest

from koch import feedback as fb


@pytest.mark.unit
def test_spring_current_grows_with_the_follower_current_above_the_deadband() -> None:
    state = fb.spring_step(fb.SpringLaw(), fb.SpringState(), 525.0)
    assert state.command_ma == 210


@pytest.mark.unit
def test_spring_uses_the_magnitude_of_a_signed_current() -> None:
    plus = fb.spring_step(fb.SpringLaw(), fb.SpringState(), 525.0)
    minus = fb.spring_step(fb.SpringLaw(), fb.SpringState(), -525.0)
    assert plus.command_ma == minus.command_ma


@pytest.mark.unit
def test_spring_floor_applies_below_the_deadband() -> None:
    state = fb.spring_step(fb.SpringLaw(), fb.SpringState(), 10.0)
    assert state.command_ma == fb.SpringLaw().floor_ma


@pytest.mark.unit
def test_error_reflection_pushes_back_once_the_follower_has_stalled() -> None:
    state = fb.ErrorReflectionState()
    for _ in range(5):
        state = fb.error_reflection_step(fb.ErrorReflectionLaw(), state, 50.0, 20.0)
    assert state.command_ma < 0


@pytest.mark.unit
def test_error_reflection_stays_silent_while_the_follower_moves() -> None:
    law = fb.ErrorReflectionLaw()
    state = fb.error_reflection_step(law, fb.ErrorReflectionState(), 50.0, 20.0)
    state = fb.error_reflection_step(law, state, 50.0, 25.0)  # still travelling
    assert state.command_ma == 0


@pytest.mark.unit
def test_virtual_wall_is_free_outside_and_engaged_at_the_width() -> None:
    wall = fb.VirtualWall("ball", 45.0, 800, 300)
    free = fb.virtual_wall_step(wall, 70.0, 2036, 2773, False)
    hit = fb.virtual_wall_step(wall, 44.0, 2036, 2773, False)
    assert not free.engaged and free.goal_current_ma == 0
    assert hit.engaged and hit.goal_current_ma == 300


@pytest.mark.unit
def test_virtual_wall_lets_go_only_past_the_release_hysteresis() -> None:
    wall = fb.VirtualWall("ball", 45.0, release=2.0)
    held = fb.virtual_wall_step(wall, 46.0, 2036, 2773, True)
    gone = fb.virtual_wall_step(wall, 47.5, 2036, 2773, True)
    assert held.engaged and not gone.engaged


@pytest.mark.unit
def test_no_wall_means_free_space_with_the_default_gain() -> None:
    cmd = fb.virtual_wall_step(None, 10.0, 2036, 2773, True)
    assert cmd == fb.WallCommand(2773, 0, fb.FREE_SPACE_P_GAIN, False)


@pytest.mark.unit
def test_virtual_wall_carries_a_mass_for_the_weight_law() -> None:
    assert fb.VirtualWall("ball", 45.0).mass_g == 0.0
    assert fb.VirtualWall("iron", 60.0, mass_g=500).mass_g == 500


@pytest.mark.unit
def test_wall_tick_maps_the_opening_between_the_calibrated_ends() -> None:
    assert fb.wall_tick(50.0, 2000, 2800) == 2400
    assert fb.wall_tick(-5.0, 2000, 2800) == 2000
    assert fb.wall_tick(150.0, 2000, 2800) == 2800


@pytest.mark.unit
@pytest.mark.parametrize(
    ("temp_c", "expected"),
    [(40, (1.5, False)), (60, (0.75, False)), (65, (1.5, True))],
)
def test_thermal_guard_derates_then_stops(
    temp_c: int, expected: tuple[float, bool]
) -> None:
    assert fb.thermal_derate(fb.ThermalGuard(), temp_c, 1.5) == expected


@pytest.mark.unit
def test_norm_to_rad_uses_half_the_span_offset_and_sign() -> None:
    joint = fb.JointMap(span_deg=100.0, offset_deg=10.0, sign=-1)
    assert fb.norm_to_rad(100.0, joint) == pytest.approx(-math.radians(60.0))


@pytest.mark.unit
def test_tip_levers_are_the_link_sum_when_the_arm_is_horizontal() -> None:
    levers = fb.tip_levers(math.pi / 2, 0.0, 0.0)
    assert levers[0] == pytest.approx(sum(fb.LINK_M))
    assert fb.tip_levers(0.0, 0.0, 0.0) == (0.0, 0.0)


@pytest.mark.unit
def test_virtual_weight_reaches_the_cap_on_the_shoulder_and_scales_the_elbow() -> None:
    law = fb.VirtualWeightLaw(scale=0.12, cap_ma=120, alpha=1.0)
    w = fb.virtual_weight_step(law, fb.VirtualWeightState(), True, 100.0, (0.2, 0.1))
    assert w.shoulder_ma == 120.0
    assert 80.0 < w.elbow_ma < 81.0


@pytest.mark.unit
def test_virtual_weight_is_zero_when_nothing_is_grasped() -> None:
    law = fb.VirtualWeightLaw(alpha=1.0)
    w = fb.virtual_weight_step(law, fb.VirtualWeightState(), False, 100.0, (0.2, 0.1))
    assert w == fb.VirtualWeightState()


@pytest.mark.unit
def test_virtual_weight_rescales_with_the_torque_constant() -> None:
    m288 = fb.VirtualWeightLaw(scale=0.12, cap_ma=400, alpha=1.0, kt_nm_per_a=0.354)
    w = fb.virtual_weight_step(m288, fb.VirtualWeightState(), True, 100.0, (0.1, 0.1))
    assert w.elbow_ma == pytest.approx(80.6 * 0.146 / 0.354, abs=0.5)


@pytest.mark.unit
def test_pwm_shoulder_uses_its_own_gain_and_cap() -> None:
    law = fb.VirtualWeightLaw(
        alpha=1.0, shoulder_gain_per_nm=0.5 * 885 / 1.5, shoulder_cap=150
    )
    light = fb.virtual_weight_step(
        law, fb.VirtualWeightState(), True, 45.0, (0.25, 0.1)
    )
    heavy = fb.virtual_weight_step(
        law, fb.VirtualWeightState(), True, 300.0, (0.25, 0.1)
    )
    assert 32.0 < light.shoulder_ma < 33.0
    assert heavy.shoulder_ma == 150.0


@pytest.mark.unit
def test_hand_assist_follows_speed_within_its_band() -> None:
    law = fb.HandAssistLaw(alpha=1.0, slew_ma=1000)
    a0 = fb.hand_assist_step(law, fb.HandAssistState(), 0.0, 1 / 30)
    slow = fb.hand_assist_step(law, a0, 0.5, 1 / 30)
    fast = fb.hand_assist_step(law, a0, 2.0, 1 / 30)
    wild = fb.hand_assist_step(law, a0, -12.0, 1 / 30)
    assert (a0.command_ma, slow.command_ma, fast.command_ma, wild.command_ma) == (
        0,
        15,
        25,
        0,
    )


@pytest.mark.unit
def test_hand_assist_does_nothing_on_the_first_frame_or_without_time() -> None:
    law = fb.HandAssistLaw()
    first = fb.hand_assist_step(law, fb.HandAssistState(), 3.0, 1 / 30)
    frozen = fb.hand_assist_step(law, first, 9.0, 0.0)
    assert first.command_ma == 0 and frozen.command_ma == 0

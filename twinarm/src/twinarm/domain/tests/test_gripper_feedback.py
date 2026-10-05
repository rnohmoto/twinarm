"""Tests for the gripper force-feedback laws (stdlib-only domain logic)."""

import math

import pytest

from twinarm.domain.gripper_feedback import (
    ErrorReflectionLaw,
    ErrorReflectionState,
    HandAssistLaw,
    HandAssistState,
    SpringLaw,
    SpringState,
    ThermalGuard,
    VirtualWall,
    error_reflection_step,
    hand_assist_step,
    spring_step,
    thermal_derate,
    virtual_wall_step,
    wall_tick,
)

# ---------------------------------------------------------------- spring law


@pytest.mark.unit
def test_spring_returns_floor_when_follower_current_is_below_deadband() -> None:
    law = SpringLaw(gain=1.5, floor_ma=60, cap_ma=450, deadband_ma=25)

    state = spring_step(law, SpringState(), follower_current_ma=10.0)

    assert state.command_ma == 60
    assert state.ema == 0.0


@pytest.mark.unit
def test_spring_rises_with_ema_and_never_exceeds_cap() -> None:
    law = SpringLaw(gain=1.5, floor_ma=60, cap_ma=450, deadband_ma=25, alpha=0.2)

    first = spring_step(law, SpringState(), follower_current_ma=525.0)
    state = first
    for _ in range(60):
        state = spring_step(law, state, follower_current_ma=525.0)

    # one step: ema = 0.2 * (525 - 25) = 100 -> 60 + 1.5 * 100 = 210
    assert first.command_ma == 210
    # converged: ema -> 500 -> 60 + 750 = 810, clipped to the cap
    assert state.command_ma == 450


@pytest.mark.unit
def test_spring_uses_magnitude_of_signed_current() -> None:
    law = SpringLaw(gain=1.0, floor_ma=0, cap_ma=450, deadband_ma=0, alpha=1.0)

    state = spring_step(law, SpringState(), follower_current_ma=-120.0)

    assert state.command_ma == 120


# ------------------------------------------------------ error-reflection law


@pytest.mark.unit
def test_error_reflection_is_zero_while_follower_is_moving() -> None:
    law = ErrorReflectionLaw(ke=9.0, deadband=6.0, cap_ma=450, slew_ma=90)
    state = ErrorReflectionState()

    state = error_reflection_step(law, state, leader_cmd=50.0, follower_pos=10.0)
    state = error_reflection_step(law, state, leader_cmd=50.0, follower_pos=20.0)

    assert state.command_ma == 0
    assert state.stall_frames == 0


@pytest.mark.unit
def test_error_reflection_pushes_back_after_stall_beyond_deadband() -> None:
    law = ErrorReflectionLaw(
        ke=9.0, deadband=6.0, sign=1, cap_ma=450, slew_ma=90, stall_frames=3
    )
    state = ErrorReflectionState()

    for _ in range(5):  # follower blocked at 20 while the leader asks 50
        state = error_reflection_step(law, state, leader_cmd=50.0, follower_pos=20.0)

    # error 30 - deadband 6 = 24 -> target -216 mA, ramped by EMA and slew
    assert state.command_ma < 0
    assert abs(state.command_ma) <= law.cap_ma


@pytest.mark.unit
def test_error_reflection_respects_slew_rate_limit() -> None:
    law = ErrorReflectionLaw(ke=100.0, deadband=0.0, cap_ma=900, slew_ma=90)
    state = ErrorReflectionState(stall_frames=10, prev_follower_pos=0.0)

    state = error_reflection_step(law, state, leader_cmd=100.0, follower_pos=0.0)

    assert state.command_ma == -90


@pytest.mark.unit
def test_error_reflection_sign_flips_direction() -> None:
    law = ErrorReflectionLaw(ke=100.0, deadband=0.0, sign=-1, cap_ma=900, slew_ma=90)
    state = ErrorReflectionState(stall_frames=10, prev_follower_pos=0.0)

    state = error_reflection_step(law, state, leader_cmd=100.0, follower_pos=0.0)

    assert state.command_ma == 90


# ------------------------------------------------------------- virtual wall


@pytest.mark.unit
def test_wall_tick_maps_normalized_opening_between_closed_and_open_end() -> None:
    assert wall_tick(0.0, closed_tick=2036, open_tick=2773) == 2036
    assert wall_tick(100.0, closed_tick=2036, open_tick=2773) == 2773
    assert wall_tick(50.0, closed_tick=2000, open_tick=2800) == 2400


@pytest.mark.unit
def test_virtual_wall_is_free_outside_the_object() -> None:
    wall = VirtualWall(name="ball", width=45.0, p_gain=800, cap_ma=300)

    cmd = virtual_wall_step(
        wall, opening=70.0, closed_tick=2036, open_tick=2773, engaged=False
    )

    assert cmd.engaged is False
    assert cmd.goal_current_ma == 0
    assert cmd.goal_position_tick == wall_tick(45.0, 2036, 2773)
    assert cmd.p_gain == 800


@pytest.mark.unit
def test_virtual_wall_engages_when_opening_reaches_object_width() -> None:
    wall = VirtualWall(name="ball", width=45.0, p_gain=800, cap_ma=300)

    cmd = virtual_wall_step(
        wall, opening=44.0, closed_tick=2036, open_tick=2773, engaged=False
    )

    assert cmd.engaged is True
    assert cmd.goal_current_ma == 300


@pytest.mark.unit
def test_virtual_wall_releases_only_past_the_hysteresis_margin() -> None:
    wall = VirtualWall(name="ball", width=45.0, p_gain=800, cap_ma=300, release=2.0)

    still = virtual_wall_step(
        wall, opening=46.0, closed_tick=2036, open_tick=2773, engaged=True
    )
    freed = virtual_wall_step(
        wall, opening=47.5, closed_tick=2036, open_tick=2773, engaged=True
    )

    assert still.engaged is True
    assert freed.engaged is False
    assert freed.goal_current_ma == 0


@pytest.mark.unit
def test_virtual_wall_none_means_free_space() -> None:
    cmd = virtual_wall_step(
        None, opening=10.0, closed_tick=2036, open_tick=2773, engaged=True
    )

    assert cmd.engaged is False
    assert cmd.goal_current_ma == 0
    assert cmd.goal_position_tick == 2773


# ------------------------------------------------------------ thermal guard


@pytest.mark.unit
def test_thermal_guard_keeps_gain_below_derate_temperature() -> None:
    guard = ThermalGuard(derate_c=60, stop_c=65, min_gain=0.1)

    gain, stop = thermal_derate(guard, temp_c=45, gain=1.5)

    assert gain == 1.5
    assert stop is False


@pytest.mark.unit
def test_thermal_guard_halves_gain_at_derate_and_floors_it() -> None:
    guard = ThermalGuard(derate_c=60, stop_c=65, min_gain=0.1)

    gain, stop = thermal_derate(guard, temp_c=61, gain=0.15)

    assert gain == 0.1
    assert stop is False


@pytest.mark.unit
def test_thermal_guard_stops_at_stop_temperature() -> None:
    guard = ThermalGuard(derate_c=60, stop_c=65, min_gain=0.1)

    gain, stop = thermal_derate(guard, temp_c=65, gain=1.5)

    assert stop is True
    assert gain == 1.5


# ----------------------------------------------------------- virtual weight


@pytest.mark.unit
def test_norm_to_rad_maps_full_scale_to_half_span_plus_offset() -> None:
    from twinarm.domain.gripper_feedback import JointMap, norm_to_rad

    m = JointMap(span_deg=100.0, offset_deg=10.0, sign=1)

    assert norm_to_rad(0.0, m) == pytest.approx(math.radians(10.0))
    assert norm_to_rad(100.0, m) == pytest.approx(math.radians(60.0))
    assert norm_to_rad(100.0, JointMap(100.0, 0.0, -1)) == pytest.approx(
        -math.radians(50.0)
    )


@pytest.mark.unit
def test_tip_levers_are_zero_when_the_arm_points_straight_up() -> None:
    from twinarm.domain.gripper_feedback import tip_levers

    shoulder, elbow = tip_levers(0.0, 0.0, 0.0)

    assert shoulder == pytest.approx(0.0)
    assert elbow == pytest.approx(0.0)


@pytest.mark.unit
def test_tip_levers_equal_link_sums_when_the_arm_is_horizontal() -> None:
    from twinarm.domain.gripper_feedback import LINK_M, tip_levers

    shoulder, elbow = tip_levers(math.pi / 2, 0.0, 0.0)

    assert shoulder == pytest.approx(sum(LINK_M))
    assert elbow == pytest.approx(LINK_M[1] + LINK_M[2])


@pytest.mark.unit
def test_virtual_weight_is_zero_when_nothing_is_grasped() -> None:
    from twinarm.domain.gripper_feedback import (
        VirtualWeightLaw,
        VirtualWeightState,
        virtual_weight_step,
    )

    state = virtual_weight_step(
        VirtualWeightLaw(),
        VirtualWeightState(),
        engaged=False,
        mass_g=100.0,
        levers_m=(0.2, 0.1),
    )

    assert state.shoulder_ma == 0.0
    assert state.elbow_ma == 0.0


@pytest.mark.unit
def test_virtual_weight_rises_with_ema_and_saturates_at_cap() -> None:
    from twinarm.domain.gripper_feedback import (
        VirtualWeightLaw,
        VirtualWeightState,
        virtual_weight_step,
    )

    law = VirtualWeightLaw(scale=0.12, cap_ma=120, alpha=0.2)
    first = virtual_weight_step(
        law, VirtualWeightState(), engaged=True, mass_g=100.0, levers_m=(0.2, 0.1)
    )
    state = first
    for _ in range(80):
        state = virtual_weight_step(
            law, state, engaged=True, mass_g=100.0, levers_m=(0.2, 0.1)
        )

    # 0.1 kg * 9.81 * 0.2 m = 0.1962 Nm -> 1344 mA physical -> *0.12 = 161 mA -> cap 120
    assert first.shoulder_ma == pytest.approx(0.2 * 120.0)
    assert state.shoulder_ma == pytest.approx(120.0, abs=0.5)
    # elbow: 0.0981 Nm -> 672 mA -> *0.12 = 80.6 mA, below the cap
    assert state.elbow_ma == pytest.approx(80.6, abs=0.5)


@pytest.mark.unit
def test_virtual_weight_invert_flips_the_sign_per_joint() -> None:
    from twinarm.domain.gripper_feedback import (
        VirtualWeightLaw,
        VirtualWeightState,
        virtual_weight_step,
    )

    law = VirtualWeightLaw(scale=0.12, cap_ma=120, alpha=1.0, invert_elbow=True)

    state = virtual_weight_step(
        law, VirtualWeightState(), engaged=True, mass_g=100.0, levers_m=(0.2, 0.1)
    )

    assert state.shoulder_ma > 0
    assert state.elbow_ma < 0


@pytest.mark.unit
def test_virtual_weight_releases_quickly() -> None:
    from twinarm.domain.gripper_feedback import (
        VirtualWeightLaw,
        VirtualWeightState,
        virtual_weight_step,
    )

    law = VirtualWeightLaw(alpha=0.2)
    state = VirtualWeightState(shoulder_ma=100.0, elbow_ma=50.0)

    state = virtual_weight_step(
        law, state, engaged=False, mass_g=100.0, levers_m=(0.2, 0.1)
    )

    assert state.shoulder_ma == pytest.approx(50.0)
    assert state.elbow_ma == pytest.approx(25.0)


@pytest.mark.unit
def test_virtual_weight_scales_with_the_servo_torque_constant() -> None:
    from twinarm.domain.gripper_feedback import (
        VirtualWeightLaw,
        VirtualWeightState,
        virtual_weight_step,
    )

    m077 = VirtualWeightLaw(scale=0.12, cap_ma=400, alpha=1.0)
    m288 = VirtualWeightLaw(scale=0.12, cap_ma=400, alpha=1.0, kt_nm_per_a=0.354)

    leader = virtual_weight_step(
        m077, VirtualWeightState(), engaged=True, mass_g=100.0, levers_m=(0.1, 0.1)
    )
    hand_follower = virtual_weight_step(
        m288, VirtualWeightState(), engaged=True, mass_g=100.0, levers_m=(0.1, 0.1)
    )

    # same torque (0.0981 Nm): 672 mA * 0.12 = 80.6 mA on an M077, 0.146/0.354 of it on an M288
    assert leader.elbow_ma == pytest.approx(80.6, abs=0.5)
    assert hand_follower.elbow_ma == pytest.approx(80.6 * 0.146 / 0.354, abs=0.5)


@pytest.mark.unit
def test_virtual_weight_shoulder_can_be_a_pwm_actuator() -> None:
    from twinarm.domain.gripper_feedback import (
        VirtualWeightLaw,
        VirtualWeightState,
        virtual_weight_step,
    )

    # XL430-W250 shoulder in PWM mode: half of (885 counts / 1.5 Nm stall) per Nm, cap 150
    law = VirtualWeightLaw(
        alpha=1.0, shoulder_gain_per_nm=0.5 * 885 / 1.5, shoulder_cap=150
    )

    light = virtual_weight_step(
        law, VirtualWeightState(), engaged=True, mass_g=45.0, levers_m=(0.25, 0.1)
    )
    heavy = virtual_weight_step(
        law, VirtualWeightState(), engaged=True, mass_g=300.0, levers_m=(0.25, 0.1)
    )
    flipped = virtual_weight_step(
        VirtualWeightLaw(
            alpha=1.0,
            shoulder_gain_per_nm=0.5 * 885 / 1.5,
            shoulder_cap=150,
            invert_shoulder=True,
        ),
        VirtualWeightState(),
        engaged=True,
        mass_g=300.0,
        levers_m=(0.25, 0.1),
    )

    # 0.045 kg * 9.81 * 0.25 m = 0.110 Nm -> 32.6 counts; 0.3 kg -> 217 -> cap 150
    assert light.shoulder_ma == pytest.approx(32.6, abs=0.2)
    assert heavy.shoulder_ma == pytest.approx(150.0)
    assert flipped.shoulder_ma == pytest.approx(-150.0)
    # the elbow keeps the current law (M077 default): 0.2943 Nm -> 2016 mA * 0.12 -> cap 120
    assert heavy.elbow_ma == pytest.approx(120.0)


# ---------------------------------------------------------------- hand assist


def _assist_run(
    law: HandAssistLaw, positions: list[float], dt: float = 1 / 30
) -> HandAssistState:
    state = HandAssistState()
    for pos in positions:
        state = hand_assist_step(law, state, pos, dt)
    return state


@pytest.mark.unit
def test_hand_assist_is_zero_while_the_joint_rests() -> None:
    law = HandAssistLaw(alpha=1.0, slew_ma=1000)

    state = _assist_run(law, [10.0, 10.0, 10.05, 10.0])

    assert state.command_ma == 0


@pytest.mark.unit
def test_hand_assist_first_frame_only_records_the_position() -> None:
    law = HandAssistLaw(alpha=1.0, slew_ma=1000)

    state = hand_assist_step(law, HandAssistState(), 55.0, 1 / 30)

    assert state.command_ma == 0
    assert state.position == 55.0


@pytest.mark.unit
def test_hand_assist_pushes_along_the_motion_and_saturates_at_cap() -> None:
    law = HandAssistLaw(gain_ma_s=1.5, cap_ma=25, deadband=5.0, alpha=1.0, slew_ma=1000)

    slow = _assist_run(law, [0.0, 0.5])  # 15 units/s -> 1.5 * (15 - 5) = 15 mA
    fast = _assist_run(law, [0.0, 2.0])  # 60 units/s -> cap
    back = _assist_run(law, [0.0, -2.0])

    assert slow.command_ma == 15
    assert fast.command_ma == 25
    assert back.command_ma == -25


@pytest.mark.unit
def test_hand_assist_invert_flips_the_current() -> None:
    law = HandAssistLaw(alpha=1.0, slew_ma=1000, invert=True)

    state = _assist_run(law, [0.0, 2.0])

    assert state.command_ma == -law.cap_ma


@pytest.mark.unit
def test_hand_assist_cuts_out_above_the_runaway_speed() -> None:
    law = HandAssistLaw(alpha=1.0, slew_ma=1000, max_speed=300.0)

    state = _assist_run(law, [0.0, 12.0])  # 360 units/s

    assert state.command_ma == 0


@pytest.mark.unit
def test_hand_assist_limits_the_change_per_frame() -> None:
    law = HandAssistLaw(cap_ma=25, alpha=1.0, slew_ma=8)

    state = _assist_run(law, [0.0, 2.0])

    assert state.command_ma == 8


@pytest.mark.unit
def test_hand_assist_ignores_a_stalled_clock() -> None:
    law = HandAssistLaw(alpha=1.0, slew_ma=1000)
    moving = _assist_run(law, [0.0, 2.0])

    state = hand_assist_step(law, moving, 4.0, 0.0)

    assert state.command_ma == 0

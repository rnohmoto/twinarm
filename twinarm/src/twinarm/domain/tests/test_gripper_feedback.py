"""Tests for the gripper force-feedback laws (stdlib-only domain logic)."""

import pytest

from twinarm.domain.gripper_feedback import (
    ErrorReflectionLaw,
    ErrorReflectionState,
    SpringLaw,
    SpringState,
    ThermalGuard,
    VirtualWall,
    error_reflection_step,
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

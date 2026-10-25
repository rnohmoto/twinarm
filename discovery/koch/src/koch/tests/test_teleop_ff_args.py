"""The error-reflection law built from the command line: defaults unchanged, every knob reachable."""

import pytest

from koch import teleop
from koch.feedback import ErrorReflectionLaw


@pytest.mark.unit
def test_without_flags_the_law_is_the_mirrored_default_with_the_bench_gain_and_cap() -> (
    None
):
    args = teleop.build_parser().parse_args([])
    law = teleop.error_law_from(args)
    default = ErrorReflectionLaw()
    assert law == ErrorReflectionLaw(
        ke=args.ff_ke, deadband=args.ff_edb, sign=1, cap_ma=args.ff_cap
    )
    assert (law.slew_ma, law.move_eps, law.stall_frames, law.alpha, law.release) == (
        default.slew_ma,
        default.move_eps,
        default.stall_frames,
        default.alpha,
        default.release,
    )


@pytest.mark.unit
def test_the_response_knobs_reach_the_law() -> None:
    args = teleop.build_parser().parse_args(
        [
            "--ff-slew",
            "250",
            "--ff-alpha",
            "0.5",
            "--ff-stall",
            "1",
            "--ff-move-eps",
            "1.0",
        ]
    )
    law = teleop.error_law_from(args)
    assert (law.slew_ma, law.alpha, law.stall_frames, law.move_eps) == (
        250,
        0.5,
        1,
        1.0,
    )


@pytest.mark.unit
def test_control_messages_change_the_response_knobs_within_bounds() -> None:
    args = teleop.build_parser().parse_args([])
    teleop.apply_ff_response(
        args, {"ff_slew": 5000, "ff_alpha": 3, "ff_stall": -2, "ff_move_eps": 2.5}
    )
    assert (args.ff_slew, args.ff_alpha, args.ff_stall, args.ff_move_eps) == (
        900,
        1.0,
        0,
        2.5,
    )
    teleop.apply_ff_response(args, {"ff_alpha": 0.0, "ff_slew": 0})
    assert (args.ff_alpha, args.ff_slew) == (0.05, 1)
    teleop.apply_ff_response(args, {"unrelated": 1})  # ignored
    assert args.ff_stall == 0


@pytest.mark.unit
def test_follow_wall_is_free_until_the_hand_squeezes_past_the_follower() -> None:
    ticks = (2000, 2800)  # closed, open; free 6, ramp 100 mA per unit, cap 500
    assert teleop.follow_wall(50.0, 50.0, ticks, 500, 6.0, 100) == (2400, 0)
    # the follower lags 3 units: the goal is the hand's own position and the limit is zero
    assert teleop.follow_wall(47.0, 50.0, ticks, 500, 6.0, 100) == (2376, 0)
    # squeezed 10 units past a stopped follower: the wall stands 6 units past it, 4 units deep
    assert teleop.follow_wall(40.0, 50.0, ticks, 500, 6.0, 100) == (2352, 400)
    assert teleop.follow_wall(20.0, 50.0, ticks, 500, 6.0, 100) == (2352, 500)  # capped
    # the opening side is always free
    assert teleop.follow_wall(60.0, 50.0, ticks, 500, 6.0, 100) == (2480, 0)
    assert teleop.follow_wall(0.0, 0.0, ticks, 500, 6.0, 100) == (2000, 0)
    assert teleop.follow_wall(100.0, 100.0, ticks, 500, 6.0, 100)[0] == 2800


@pytest.mark.unit
def test_the_parser_accepts_the_follow_style_and_the_stiffness_is_live() -> None:
    args = teleop.build_parser().parse_args(
        ["--ff-style", "follow", "--ff-pgain", "1200"]
    )
    assert (args.ff_style, args.ff_pgain) == ("follow", 1200)
    teleop.apply_ff_response(args, {"ff_pgain": 99999})
    assert args.ff_pgain == 16383
    teleop.apply_ff_response(args, {"ff_pgain": -5})
    assert args.ff_pgain == 0


@pytest.mark.unit
def test_the_free_space_tolerance_has_a_default_and_is_live() -> None:
    args = teleop.build_parser().parse_args([])
    assert args.ff_free == 6.0
    teleop.apply_ff_response(args, {"ff_free": 8})
    assert args.ff_free == 8.0
    teleop.apply_ff_response(args, {"ff_free": -1})
    assert args.ff_free == 0.0


@pytest.mark.unit
def test_the_force_ramp_has_a_default_and_is_live() -> None:
    args = teleop.build_parser().parse_args([])
    assert args.ff_ramp == 100
    teleop.apply_ff_response(args, {"ff_ramp": 5000})
    assert args.ff_ramp == 900

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
def test_follow_wall_puts_the_goal_where_the_follower_is_and_reports_the_cap_only_when_pushed() -> (
    None
):
    ticks = (2000, 2800)  # closed, open
    assert teleop.follow_wall(50.0, 50.0, ticks, 500, 6.0) == (2400, 0)
    assert teleop.follow_wall(20.0, 50.0, ticks, 500, 6.0) == (
        2400,
        500,
    )  # squeezed past a stopped follower
    assert teleop.follow_wall(46.0, 50.0, ticks, 500, 6.0) == (
        2400,
        0,
    )  # inside the deadband
    assert teleop.follow_wall(0.0, 0.0, ticks, 500, 6.0)[0] == 2000
    assert teleop.follow_wall(100.0, 100.0, ticks, 500, 6.0)[0] == 2800


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

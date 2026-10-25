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

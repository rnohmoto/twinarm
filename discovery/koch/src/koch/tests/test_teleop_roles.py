"""Which machine plays which role in a teleop, and the combinations that cannot run."""

import pytest

from koch import teleop


@pytest.mark.unit
def test_the_usual_pair_and_the_hand_moved_follower_are_accepted() -> None:
    assert teleop.role_error("koch_leader", "koch_follower", "/dev/F") is None
    assert teleop.role_error("koch_leader", "koch_follower", "udp://h:9101") is None
    assert teleop.role_error("koch_leader", "koch_follower", "none") is None
    assert teleop.role_error("koch_follower", "koch_follower", "none") is None


@pytest.mark.unit
def test_the_swapped_pair_is_accepted_on_a_wire() -> None:
    assert teleop.role_error("koch_follower", "koch_leader", "/dev/L") is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("leader_type", "follower_type", "follower_port"),
    [
        ("koch_follower", "koch_follower", "/dev/F"),  # two follower machines
        ("koch_follower", "koch_leader", "none"),  # nothing to drive
        ("koch_follower", "koch_leader", "udp://h:9101"),  # wireless
        ("koch_leader", "koch_leader", "/dev/L"),  # two leader machines
    ],
)
def test_other_combinations_are_refused_with_a_reason(
    leader_type, follower_type, follower_port
) -> None:
    why = teleop.role_error(leader_type, follower_type, follower_port)
    assert why and follower_type in why or (why and leader_type in why)


@pytest.mark.unit
def test_the_parser_defaults_to_the_follower_machine_as_the_follower() -> None:
    assert teleop.build_parser().parse_args([]).follower_type == "koch_follower"
    args = teleop.build_parser().parse_args(
        ["--leader-type", "koch_follower", "--follower-type", "koch_leader"]
    )
    assert (args.leader_type, args.follower_type) == ("koch_follower", "koch_leader")


@pytest.mark.unit
def test_the_driven_arm_gains_default_to_lerobots_values_and_can_be_left_alone() -> (
    None
):
    args = teleop.build_parser().parse_args([])
    assert (args.follower_pgain, args.follower_igain, args.follower_dgain) == (
        1500,
        0,
        600,
    )
    args = teleop.build_parser().parse_args(
        ["--follower-pgain", "-1", "--follower-dgain", "800"]
    )
    assert (args.follower_pgain, args.follower_dgain) == (-1, 800)

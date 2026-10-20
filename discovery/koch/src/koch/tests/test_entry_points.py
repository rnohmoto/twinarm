"""Importing an entry module must stay free of side effects; its CLI must parse."""

import subprocess
import sys

import pytest

from koch import feedback, motors

PROBE = """
import logging, sys
import koch.teleop, koch.follower_host
from koch.console import DropClampWarning
filters = [f for f in logging.getLogger().filters if isinstance(f, DropClampWarning)]
print("filters", len(filters))
print("lerobot", "lerobot" in sys.modules)
"""


@pytest.mark.integration
def test_importing_the_entry_modules_touches_neither_the_root_logger_nor_lerobot() -> (
    None
):
    out = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, check=True
    ).stdout
    assert "filters 0" in out, out
    assert "lerobot False" in out, out


@pytest.mark.unit
def test_teleop_parser_accepts_selftest_and_a_wall_spec() -> None:
    from koch import teleop

    args = teleop.build_parser().parse_args(["--selftest", "--wall", "ball:45:800:300"])
    assert args.selftest
    assert args.wall == feedback.VirtualWall("ball", 45.0, 800, 300)


@pytest.mark.unit
def test_teleop_leader_types_are_the_device_models() -> None:
    from koch import teleop

    action = next(a for a in teleop.build_parser()._actions if a.dest == "leader_type")
    assert list(action.choices) == list(motors.DEVICE_MODELS)


@pytest.mark.unit
def test_follower_host_parser_accepts_sim() -> None:
    from koch import follower_host

    args = follower_host.build_parser().parse_args(["--sim"])
    assert args.sim

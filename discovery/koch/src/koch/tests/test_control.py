"""Inbound control messages and CLI values: walls from the page, twin maps, scaling."""

import argparse

import pytest

from koch import control
from koch.feedback import JointMap, VirtualWall
from koch.motors import FF_CAP_MAX_MA


@pytest.mark.unit
def test_parse_wall_minimal_and_full_specs() -> None:
    assert control.parse_wall("ball:45") == VirtualWall("ball", 45.0)
    assert control.parse_wall("iron:60:1200:400:2:500") == VirtualWall(
        "iron", 60.0, 1200, 400, 2.0, 500.0
    )


@pytest.mark.unit
def test_parse_wall_rejects_a_spec_without_a_width() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        control.parse_wall("ball")


@pytest.mark.unit
def test_wall_from_fields_clamps_every_field() -> None:
    wall = control.wall_from_fields("x" * 40, 140.0, 99999, 5000, 99.0, 9000.0)
    assert len(wall.name) == 32
    assert wall.width == 100.0
    assert wall.p_gain == 16383
    assert wall.cap_ma == FF_CAP_MAX_MA
    assert wall.release == 20.0
    assert wall.mass_g == 2000.0


@pytest.mark.unit
def test_wall_from_message_defaults_and_none() -> None:
    assert control.wall_from_message(None) is None
    assert control.wall_from_message({}) is None
    assert control.wall_from_message({"name": "cup"}) == VirtualWall("cup", 50.0)


@pytest.mark.unit
def test_scale_wall_rounds_the_device_scale_then_caps_at_ff_cap() -> None:
    wall = VirtualWall("iron", 60.0, cap_ma=400)
    assert control.scale_wall(wall, 0.45, 900).cap_ma == 180
    assert control.scale_wall(wall, 0.45, 100).cap_ma == 100
    assert control.scale_wall(wall, 1.0, 900) is wall
    assert control.scale_wall(None, 0.45, 900) is None


@pytest.mark.unit
def test_twin_map_from_message_merges_known_joints_without_touching_the_input() -> None:
    current = {"elbow_flex": JointMap(100.0), "wrist_flex": JointMap(100.0)}
    payload = {
        "joints": {
            "elbow_flex": {"offset_deg": 5, "sign": -3},
            "wrist_flex": "garbage",
            "unknown": {"span_deg": 1},
        }
    }
    merged = control.twin_map_from_message(current, payload)
    assert merged == {"elbow_flex": JointMap(100.0, 5.0, -1)}
    assert current["elbow_flex"] == JointMap(100.0)


@pytest.mark.unit
def test_twin_map_from_message_tolerates_an_empty_payload() -> None:
    assert control.twin_map_from_message({"elbow_flex": JointMap(100.0)}, None) == {}

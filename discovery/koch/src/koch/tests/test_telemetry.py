"""The outbound wire contract: telemetry frames, viewer ports and viewer processes."""

import json
import sys

import pytest

from koch import telemetry
from koch.feedback import VirtualWall

FRAME_KEYS = [
    "t", "cur", "pos", "fpos", "ff", "mode", "temp", "rec", "n", "leader_only", "arm",
    "device", "alerts", "hw", "link", "vw", "vw_unit", "vwall", "params",
]  # fmt: skip


def _frame(**overrides):
    base = {
        "t": 12.34567,
        "action": {"gripper.pos": 42.26, "elbow_flex.pos": "3.14159"},
        "cur": {"gripper": -12.34, "elbow_flex": 0.0},
        "fpos": {"gripper": 41.0},
        "ff_ma": 210,
        "mode": "gripper",
        "temp": 36,
        "reconnects": 0,
        "n": 7,
        "leader_only": False,
        "arm_label": "A",
        "device": "koch_leader",
        "alerts": {},
        "hw": {"gripper": 0},
        "link": None,
        "vw_on": False,
        "vw_joints": ["shoulder_lift", "elbow_flex"],
        "pwm_joints": {},
        "vw_out": {"shoulder_lift": 0.0, "elbow_flex": 0.0},
        "vwall_on": False,
        "wall": None,
        "wall_engaged": False,
        "params": {"ff_gain": 1.5},
    }
    base.update(overrides)
    return telemetry.telemetry_frame(**base)


@pytest.mark.unit
def test_parse_ports_drops_zero_and_blanks() -> None:
    assert telemetry.parse_ports("8765,8769") == [8765, 8769]
    assert telemetry.parse_ports(" 8765 , ,0") == [8765]
    assert telemetry.parse_ports("0") == []
    assert telemetry.parse_ports("") == []


@pytest.mark.unit
def test_frame_keeps_the_key_order_and_rounds_the_values() -> None:
    frame = _frame()
    assert list(frame) == FRAME_KEYS
    assert frame["t"] == 12.346
    assert frame["cur"] == {"gripper": -12.3, "elbow_flex": 0.0}
    assert frame["pos"] == {"gripper": 42.3, "elbow_flex": 3.1}
    assert frame["fpos"] == {"gripper": 41.0}
    assert frame["ff"] == 210 and frame["n"] == 7 and frame["arm"] == "A"
    assert frame["params"] == {"ff_gain": 1.5}


@pytest.mark.unit
def test_frame_fields_are_none_while_their_feature_is_off() -> None:
    frame = _frame()
    assert frame["link"] is None and frame["vw"] is None
    assert frame["vw_unit"] is None and frame["vwall"] is None


@pytest.mark.unit
def test_frame_carries_the_weight_and_wall_shape_when_on() -> None:
    frame = _frame(
        vw_on=True,
        pwm_joints={"shoulder_lift": {}},
        vw_out={"shoulder_lift": 150.0, "elbow_flex": 80.6},
        vwall_on=True,
        wall=VirtualWall("iron", 60.0),
        wall_engaged=True,
        link={"age_ms": 3, "rtt_ms": 1, "loss": 0.0, "health": "ok"},
    )
    assert frame["vw"] == {"shoulder_lift": 150.0, "elbow_flex": 80.6}
    assert frame["vw_unit"] == {"shoulder_lift": "PWM", "elbow_flex": "mA"}
    assert frame["vwall"] == {"name": "iron", "width": 60.0, "engaged": True}
    assert frame["link"]["health"] == "ok"
    json.dumps(frame)


@pytest.mark.unit
def test_viewer_commands_spawn_the_package_modules() -> None:
    cmds = telemetry.viewer_commands(
        plot=True, panel=True, cams="0,1", ctl_port=8766, viz_ports=[8765, 8769]
    )
    assert cmds == [
        [sys.executable, "-m", "koch.live_plot", "--port", "8765"],
        [
            sys.executable,
            "-m",
            "koch.panel",
            "--telemetry",
            "8765",
            "--ctl-port",
            "8766",
            "--cams",
            "0,1",
        ],
    ]


@pytest.mark.unit
def test_viewer_commands_need_a_telemetry_port() -> None:
    assert (
        telemetry.viewer_commands(
            plot=True, panel=True, cams="", ctl_port=8766, viz_ports=[]
        )
        == []
    )
    assert (
        telemetry.viewer_commands(
            plot=False, panel=False, cams="", ctl_port=8766, viz_ports=[8765]
        )
        == []
    )

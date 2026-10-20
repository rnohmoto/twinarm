"""The one module that imports lerobot, lazily, and the retry injection it applies."""

import subprocess
import sys

import pytest

from koch import lerobot_compat


class _Bus:
    """A stand-in for MotorsBus with the four methods lerobot 0.6 retries on."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def sync_read(self, name, *, num_retry=0):
        self.calls.append(("sync_read", num_retry))

    def read(self, name, motor, *, num_retry=0):
        self.calls.append(("read", num_retry))

    def write(self, name, motor, value, *, num_retry=0):
        self.calls.append(("write", num_retry))

    def sync_write(self, name, values, *, num_retry=0):
        self.calls.append(("sync_write", num_retry))

    def ping(self, motor):
        self.calls.append(("ping", -1))


@pytest.mark.unit
def test_install_retries_raises_the_retry_count_to_the_floor() -> None:
    class Bus(_Bus):
        pass

    lerobot_compat.install_retries(Bus)
    bus = Bus()
    bus.sync_read("Present_Position")
    bus.read("Present_Temperature", "gripper")
    bus.write("Goal_Current", "gripper", 1)
    bus.sync_write("Goal_Position", {})
    assert bus.calls == [
        ("sync_read", 10),
        ("read", 5),
        ("write", 5),
        ("sync_write", 5),
    ]


@pytest.mark.unit
def test_install_retries_keeps_a_larger_explicit_count() -> None:
    class Bus(_Bus):
        pass

    lerobot_compat.install_retries(Bus)
    bus = Bus()
    bus.sync_read("Present_Position", num_retry=20)
    assert bus.calls == [("sync_read", 20)]


@pytest.mark.unit
def test_install_retries_skips_methods_without_a_retry_parameter() -> None:
    class Bus(_Bus):
        pass

    lerobot_compat.install_retries(Bus, spec=(("ping", 5),))
    bus = Bus()
    bus.ping("gripper")
    assert bus.calls == [("ping", -1)]


@pytest.mark.integration
def test_importing_the_module_does_not_import_lerobot() -> None:
    probe = "import sys, koch.lerobot_compat; print('lerobot' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"


@pytest.mark.integration
def test_load_lerobot_returns_the_koch_classes_with_retries_installed() -> None:
    pytest.importorskip("lerobot")
    follower, _follower_cfg, leader, _leader_cfg, retry_errors = (
        lerobot_compat.load_lerobot()
    )
    assert follower.__name__ == "KochFollower" and leader.__name__ == "KochLeader"
    assert ConnectionError in retry_errors
    from lerobot.motors.motors_bus import MotorsBus

    assert MotorsBus.sync_read.__name__ == "patched"

"""The Mac-side client of the wireless follower (``koch-host`` over UDP)."""

import json
import socket
import threading
import time

import pytest

from koch import remote_follower as rf
from koch.link import LinkState


def _fresh(follower: rf.RemoteFollower) -> None:
    follower.link = LinkState(last_seq=0, last_rx=time.monotonic(), received=1)


@pytest.mark.unit
def test_url_must_be_udp_host_port() -> None:
    assert rf.RemoteFollower("udp://pi.local:9101", 20).addr == ("pi.local", 9101)
    with pytest.raises(ValueError, match="udp://"):
        rf.RemoteFollower("/dev/tty.usbmodem1", 20)


@pytest.mark.unit
def test_not_connected_without_a_socket() -> None:
    assert not rf.RemoteFollower("udp://h:9101", 20).is_connected


@pytest.mark.unit
def test_reads_raise_connection_error_while_the_link_is_stale() -> None:
    follower = rf.RemoteFollower("udp://h:9101", 20)
    with pytest.raises(ConnectionError):
        follower.bus.sync_read("Present_Position")


@pytest.mark.unit
def test_gripper_cap_is_the_one_register_that_can_be_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    follower = rf.RemoteFollower("udp://h:9101", 20, grip_ma=500)
    sent: list[dict] = []
    monkeypatch.setattr(follower, "_send", sent.append)
    _fresh(follower)
    assert follower.bus.read("Goal_Current", "gripper") == 500
    follower.bus.write("Goal_Current", "gripper", 300)
    assert follower.bus.read("Goal_Current", "gripper") == 300
    assert sent == [{"type": "cfg", "grip_ma": 300}]
    with pytest.raises(KeyError):
        follower.bus.write("Goal_Position", "gripper", 1)


@pytest.mark.unit
def test_unknown_register_raises_key_error() -> None:
    follower = rf.RemoteFollower("udp://h:9101", 20)
    _fresh(follower)
    with pytest.raises(KeyError):
        follower.bus.sync_read("Goal_PWM")


@pytest.mark.unit
def test_send_action_numbers_the_packets_and_floats_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    follower = rf.RemoteFollower("udp://h:9101", 20)
    sent: list[dict] = []
    monkeypatch.setattr(follower, "_send", sent.append)
    follower.send_action({"gripper.pos": 12})
    follower.send_action({"gripper.pos": 13})
    assert [m["seq"] for m in sent] == [1, 2]
    assert sent[0]["action"] == {"gripper.pos": 12.0}


@pytest.mark.unit
def test_changing_max_relative_target_forwards_it_to_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    follower = rf.RemoteFollower("udp://h:9101", 20)
    sent: list[dict] = []
    monkeypatch.setattr(follower, "_send", sent.append)
    follower.config.max_relative_target = 10
    assert follower.config.max_relative_target == 10
    assert sent == [{"type": "cfg", "max_rel": 10}]


@pytest.mark.unit
def test_link_stats_shape() -> None:
    follower = rf.RemoteFollower("udp://h:9101", 20)
    stats = follower.link_stats()
    assert set(stats) == {"age_ms", "rtt_ms", "loss", "health"}
    assert stats["age_ms"] == -1 and stats["health"] == "stale"


def _fake_host(stop: threading.Event) -> tuple[socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.1)

    def serve() -> None:
        n = 0
        while not stop.is_set():
            try:
                _, addr = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                return
            n += 1
            state = {
                "type": "state",
                "n": n,
                "fpos": {"gripper": 42.0},
                "cur": {},
                "hw": {},
                "temp": {},
            }
            sock.sendto(json.dumps(state).encode(), addr)

    threading.Thread(target=serve, daemon=True).start()
    return sock, sock.getsockname()[1]


@pytest.mark.integration
def test_connects_to_a_loopback_host_and_reads_its_state() -> None:
    stop = threading.Event()
    sock, port = _fake_host(stop)
    follower = rf.RemoteFollower(f"udp://127.0.0.1:{port}", 20)
    try:
        follower.connect()
        assert follower.is_connected
        assert follower.bus.sync_read("Present_Position") == {"gripper": 42.0}
    finally:
        follower.disconnect()
        stop.set()
        sock.close()

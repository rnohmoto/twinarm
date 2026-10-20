"""``koch-set-id``: writes motor configuration, so every guard is tested on a fake bus."""

import pytest

from koch import set_id


class _Packet:
    def __init__(self, on_bus: dict[int, tuple[int, int]]) -> None:
        self.on_bus = on_bus
        self.writes: list[tuple[int, int, int]] = []

    def broadcastPing(self, port):
        return dict(self.on_bus), 0

    def write1ByteTxRx(self, port, dxl_id, addr, value):
        self.writes.append((dxl_id, addr, value))
        if addr == set_id.ADDR_ID:
            self.on_bus = {value: self.on_bus[dxl_id]}
        return 0, 0


@pytest.mark.unit
def test_parser_direct_form_needs_port_and_both_ids() -> None:
    args = set_id.parse_args(["/dev/p", "1", "6"])
    assert (args.port, args.current_id, args.new_id, args.setup) == (
        "/dev/p",
        1,
        6,
        False,
    )
    with pytest.raises(SystemExit):
        set_id.parse_args(["/dev/p", "1"])


@pytest.mark.unit
def test_parser_setup_form_needs_a_port_and_no_ids() -> None:
    args = set_id.parse_args(["--setup", "--port", "/dev/p", "--id", "koch_follower_A"])
    assert args.setup and args.port == "/dev/p" and args.id == "koch_follower_A"
    assert args.type == "koch_follower"
    with pytest.raises(SystemExit):
        set_id.parse_args(["--setup"])
    with pytest.raises(SystemExit):
        set_id.parse_args(["--setup", "/dev/p", "1", "6"])


@pytest.mark.unit
def test_change_id_requires_exactly_the_target_motor_on_the_bus() -> None:
    packet = _Packet({1: (1190, 44), 2: (1190, 44)})
    with pytest.raises(SystemExit, match="1個だけ"):
        set_id.change_id(packet, None, current_id=1, new_id=6)
    assert packet.writes == []


@pytest.mark.unit
def test_change_id_drops_torque_then_writes_the_id_and_reports_the_bus() -> None:
    packet = _Packet({1: (1190, 44)})
    after = set_id.change_id(packet, None, current_id=1, new_id=6)
    assert packet.writes == [(1, set_id.ADDR_TORQUE_ENABLE, 0), (1, set_id.ADDR_ID, 6)]
    assert after == [6]

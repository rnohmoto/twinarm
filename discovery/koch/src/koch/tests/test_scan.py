"""``koch-scan``: the read-only bus survey (both bauds) and the position read loop."""

import pytest

from koch import scan


@pytest.mark.unit
def test_parser_defaults_to_both_bauds_and_no_loop() -> None:
    args = scan.build_parser().parse_args(["/dev/tty.usbmodem1"])
    assert args.port == "/dev/tty.usbmodem1"
    assert args.baud == [1_000_000, 57_600]
    assert args.loop == 0.0 and args.ids is None


@pytest.mark.unit
def test_parser_reads_bauds_ids_and_loop() -> None:
    args = scan.build_parser().parse_args(
        ["p", "--baud", "57600", "--ids", "1-3,6", "--loop", "30"]
    )
    assert args.baud == [57_600]
    assert args.ids == [1, 2, 3, 6]
    assert args.loop == 30.0


@pytest.mark.unit
def test_ping_lines_name_the_models() -> None:
    lines = scan.ping_lines({2: (1190, 44), 1: (1060, 46)})
    assert lines == ["  ID 1: XL430-W250 (FW 46)", "  ID 2: XL330-M077 (FW 44)"]
    assert scan.ping_lines({}) == ["  応答なし"]


@pytest.mark.unit
def test_read_loop_counts_successes_and_failures() -> None:
    results = iter([0, 0, 1, 0])

    def read(dxl_id: int) -> int:
        return next(results, 0)

    ok, ng = scan.read_loop(read, ids=[1, 2], seconds=0.0, hz=1000, min_rounds=2)
    assert (ok, ng) == (3, 1)

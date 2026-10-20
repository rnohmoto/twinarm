"""koch-scan — survey an assembled Koch bus without writing anything (read-only).

Pings every motor at 1 Mbps and at the factory 57600 bps (a motor fresh out of the
box answers only there) and prints ID, model and firmware. ``--loop SECONDS`` then
reads Present_Position of the motors found (or ``--ids``) in a loop and counts
failures: the old communication-stability test.

  koch-scan /dev/tty.usbmodem<XXXX>
  koch-scan /dev/tty.usbmodem<XXXX> --loop 30
  koch-scan /dev/tty.usbmodem<XXXX> --baud 57600
Risk class: read-only (ping and reads only).
"""

import argparse
import time
from collections.abc import Callable

from koch.dxl import (
    ADDR_PRESENT_POSITION,
    BAUD,
    FACTORY_BAUD,
    JOINTS,
    model_name,
    open_port,
)


def parse_ids(spec: str) -> list[int]:
    """``1-3,6`` -> ``[1, 2, 3, 6]``."""
    ids: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            ids.extend(range(int(lo), int(hi) + 1))
        else:
            ids.append(int(part))
    return ids


def parse_bauds(spec: str) -> list[int]:
    """``1000000,57600`` -> ``[1000000, 57600]``."""
    return [int(p) for p in spec.split(",") if p.strip()]


def build_parser() -> argparse.ArgumentParser:
    """CLI of ``koch-scan``."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("port", help="serial port of the arm (never guessed)")
    ap.add_argument(
        "--baud",
        type=parse_bauds,
        default=[BAUD, FACTORY_BAUD],
        help="bauds to ping, comma list",
    )
    ap.add_argument(
        "--ids",
        type=parse_ids,
        default=None,
        help="motor IDs for --loop (default: the ones found)",
    )
    ap.add_argument(
        "--loop",
        type=float,
        default=0.0,
        help="seconds of Present_Position reads after the scan (always at 1 Mbps)",
    )
    return ap


def ping_lines(data: dict[int, tuple[int, int]]) -> list[str]:
    """Lines of a ping result: ``ID n: model (FW f)``, sorted by ID."""
    if not data:
        return ["  応答なし"]
    return [
        f"  ID {i}: {model_name(data[i][0])} (FW {data[i][1]})" for i in sorted(data)
    ]


def read_loop(
    read: Callable[[int], int],
    ids: list[int],
    seconds: float,
    hz: float = 100.0,
    min_rounds: int = 1,
) -> tuple[int, int]:
    """Call ``read(id)`` for every ID round after round; count result 0 as ok, anything else as ng."""
    ok = ng = 0
    rounds = 0
    deadline = time.monotonic() + seconds
    while rounds < min_rounds or time.monotonic() < deadline:
        for dxl_id in ids:
            if read(dxl_id) == 0:
                ok += 1
            else:
                ng += 1
        rounds += 1
        time.sleep(1.0 / hz)
    return ok, ng


def main() -> None:
    """Ping at each baud, then optionally read positions in a loop."""
    args = build_parser().parse_args()
    found: dict[int, tuple[int, int]] = {}
    for baud in args.baud:
        port, packet = open_port(args.port, baud)
        data, _ = packet.broadcastPing(port)
        print(f"\n=== {baud} bps ===")
        print("\n".join(ping_lines(data)))
        if baud == BAUD:
            found = data
        port.closePort()
    if args.loop <= 0:
        return
    ids = args.ids or sorted(found) or list(JOINTS)
    port, packet = open_port(args.port, BAUD)

    def read(dxl_id: int) -> int:
        _, result, _ = packet.read4ByteTxRx(port, dxl_id, ADDR_PRESENT_POSITION)
        return result

    try:
        ok, ng = read_loop(read, ids, args.loop)
    finally:
        port.closePort()
    print(f"{args.loop:g} 秒間: 成功 {ok} / 失敗 {ng}")


if __name__ == "__main__":
    main()

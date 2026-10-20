"""koch-set-id — change one motor's ID, or run lerobot's motor setup. Writes motor configuration.

  koch-set-id <port> <current-id> <new-id>
      With exactly that motor on the bus: torque off, write the ID (EEPROM, address 7),
      ping again. Refuses when the bus shows anything else.
  koch-set-id --setup --port <port> [--id koch_follower_A] [--type koch_follower|koch_leader]
      lerobot's ``setup_motors()``: assigns IDs and the 1 Mbps baud one motor at a time,
      following its prompts (a motor at the factory 57600 bps is handled there).
Risk class: writes motor config. Run only when asked, with the port the user gave.
"""

import argparse

from koch.dxl import ADDR_ID, ADDR_TORQUE_ENABLE, BAUD, open_port

ARM_TYPES = ("koch_follower", "koch_leader")


def build_parser() -> argparse.ArgumentParser:
    """CLI of ``koch-set-id`` (validated by :func:`parse_args`)."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("port", nargs="?", help="serial port (direct form)")
    ap.add_argument(
        "current_id", nargs="?", type=int, help="ID the motor answers to now"
    )
    ap.add_argument("new_id", nargs="?", type=int, help="ID to write")
    ap.add_argument(
        "--setup", action="store_true", help="run lerobot's setup_motors() instead"
    )
    ap.add_argument("--port", dest="port_opt", help="serial port for --setup")
    ap.add_argument(
        "--id", default=None, help="lerobot arm id for --setup (default: <type>_A)"
    )
    ap.add_argument(
        "--type",
        choices=ARM_TYPES,
        default="koch_follower",
        help="arm kind for --setup",
    )
    return ap


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse and validate: the direct form needs port + both IDs, --setup needs a port and no IDs."""
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.setup:
        port = args.port_opt or args.port
        if not port:
            ap.error("--setup には --port <port> が要ります")
        if args.current_id is not None or args.new_id is not None:
            ap.error("--setup に ID は渡せません（lerobot が対話で決めます）")
        args.port = port
        if args.id is None:
            args.id = f"{args.type}_A"
    elif args.port is None or args.current_id is None or args.new_id is None:
        ap.error(
            "使い方: koch-set-id <port> <current-id> <new-id>  または  --setup --port <port>"
        )
    return args


def change_id(packet, port, *, current_id: int, new_id: int) -> list[int]:
    """Write ``new_id`` to the one motor on the bus; returns the IDs seen afterwards."""
    data, _ = packet.broadcastPing(port)
    print("バス上:", sorted(data))
    if len(data) != 1 or current_id not in data:
        raise SystemExit("中止: バス上が「対象モーター1個だけ」になっていません")
    packet.write1ByteTxRx(port, current_id, ADDR_TORQUE_ENABLE, 0)  # torque off first
    packet.write1ByteTxRx(port, current_id, ADDR_ID, new_id)
    data, _ = packet.broadcastPing(port)
    return sorted(data)


def setup_with_lerobot(port: str, arm_id: str, kind: str) -> None:
    """Lerobot's interactive motor setup for one arm (imports lerobot lazily)."""
    if kind == "koch_follower":
        from lerobot.robots.koch_follower import KochFollower, KochFollowerConfig

        KochFollower(KochFollowerConfig(port=port, id=arm_id)).setup_motors()
        return
    from lerobot.teleoperators.koch_leader import KochLeader, KochLeaderConfig

    KochLeader(KochLeaderConfig(port=port, id=arm_id)).setup_motors()


def main() -> None:
    """Dispatch to the direct ID write or to lerobot's setup."""
    args = parse_args()
    if args.setup:
        setup_with_lerobot(args.port, args.id, args.type)
        return
    port, packet = open_port(args.port, BAUD)
    try:
        after = change_id(packet, port, current_id=args.current_id, new_id=args.new_id)
    finally:
        port.closePort()
    print("変更後バス上:", after, "←", [args.new_id], "になっていれば成功")


if __name__ == "__main__":
    main()

"""Cleanup after a crashed teleop: write Torque_Enable=0 to IDs 1-6 on one bus.

RISK CLASS: torque off — an arm held up by torque will drop. Use only when a teleop died
without its own cleanup (it normally releases torque on exit) and the arm is resting or held.

  python -m koch.tools.torque_off <port>
"""

import argparse

from koch.dxl import ADDR_TORQUE_ENABLE, JOINTS, open_port


def build_parser():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("port")
    return ap


def main():
    args = build_parser().parse_args()
    port, packet = open_port(args.port)
    try:
        for i in JOINTS:
            res, err = packet.write1ByteTxRx(port, i, ADDR_TORQUE_ENABLE, 0)
            back, r2, _ = packet.read1ByteTxRx(port, i, ADDR_TORQUE_ENABLE)
            print(
                f"ID {i}: write res={res} err={err} -> Torque_Enable={back if r2 == 0 else 'NG'}"
            )
    finally:
        port.closePort()


if __name__ == "__main__":
    main()

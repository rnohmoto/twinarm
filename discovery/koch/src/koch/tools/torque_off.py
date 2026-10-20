"""Cleanup after a crashed teleop: write Torque_Enable=0 to IDs 1-6 on one bus.

RISK CLASS: torque off — an arm held up by torque will drop. Use only when a teleop died
without its own cleanup (it normally releases torque on exit) and the arm is resting or held.

  koch_torque_off.py <port>
"""

import sys

from dynamixel_sdk import PacketHandler, PortHandler

port = PortHandler(sys.argv[1])
packet = PacketHandler(2.0)
if not port.openPort():
    raise SystemExit(f"ポートが開けません: {sys.argv[1]}")
port.setBaudRate(1_000_000)
try:
    for i in range(1, 7):
        res, err = packet.write1ByteTxRx(port, i, 64, 0)
        back, r2, _ = packet.read1ByteTxRx(port, i, 64)
        print(
            f"ID {i}: write res={res} err={err} -> Torque_Enable={back if r2 == 0 else 'NG'}"
        )
finally:
    port.closePort()

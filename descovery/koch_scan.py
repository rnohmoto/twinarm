"""koch_scan.py — 組立済みKochの現状把握（配線そのまま・非破壊）"""
import sys
from dynamixel_sdk import PortHandler, PacketHandler

PORT = sys.argv[1]
MODEL = {1060: 'XL430-W250', 1200: 'XL330-M288', 1190: 'XL330-M077'}

for baud in (1000000, 57600):
    port = PortHandler(PORT)
    packet = PacketHandler(2.0)
    if not port.openPort():
        print(f"ポートが開けません: {PORT}")
        break
    port.setBaudRate(baud)
    data, result = packet.broadcastPing(port)
    print(f"\n=== {baud} bps ===")
    if data:
        for dxl_id in sorted(data):
            model, fw = data[dxl_id][0], data[dxl_id][1]
            print(f"  ID {dxl_id}: {MODEL.get(model, f'model {model}')} (FW {fw})")
    else:
        print("  応答なし")
    port.closePort()
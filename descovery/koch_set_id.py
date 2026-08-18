"""koch_set_id.py — 使い方: python koch_set_id.py <ポート> <現在ID> <新ID>"""
import sys
from dynamixel_sdk import PortHandler, PacketHandler

PORT, cur, new = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
port = PortHandler(PORT); packet = PacketHandler(2.0)
if not port.openPort(): sys.exit(f"ポートが開けません: {PORT}")
port.setBaudRate(1000000)
data, _ = packet.broadcastPing(port)
print("バス上:", sorted(data))
if len(data) != 1 or cur not in data:
    sys.exit("中止: バス上が「対象モーター1個だけ」になっていません")
packet.write1ByteTxRx(port, cur, 64, 0)      # トルクOFF（念のため）
res, err = packet.write1ByteTxRx(port, cur, 7, new)  # アドレス7 = ID
data, _ = packet.broadcastPing(port)
print("変更後バス上:", sorted(data), "←", [new], "になっていれば成功")
port.closePort()
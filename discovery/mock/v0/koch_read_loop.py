"""koch_read_loop.py — トルクなしで位置を連続読み（通信安定性テスト）"""
import sys, time
from dynamixel_sdk import PortHandler, PacketHandler

PORT = sys.argv[1]
port = PortHandler(PORT); packet = PacketHandler(2.0)
port.openPort(); port.setBaudRate(1000000)
ok = ng = 0
t0 = time.time()
while time.time() - t0 < 30:
    for i in range(1, 7):
        pos, res, err = packet.read4ByteTxRx(port, i, 132)  # Present_Position
        if res == 0: ok += 1
        else: ng += 1
    time.sleep(0.01)
print(f"30秒間: 成功 {ok} / 失敗 {ng}")
port.closePort()
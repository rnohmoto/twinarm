"""Read-only trace of Present_Position for one motor — NO writes."""

import itertools
import sys
import time
from collections import Counter

from dynamixel_sdk import PacketHandler, PortHandler

port = PortHandler(sys.argv[1])
dxl_id = int(sys.argv[2])
secs = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
packet = PacketHandler(2.0)
assert port.openPort()
port.setBaudRate(1_000_000)


def s32(v):
    return v - (1 << 32) if v >= (1 << 31) else v


vals, fails = [], 0
t0 = time.time()
try:
    while time.time() - t0 < secs:
        v, res, _ = packet.read4ByteTxRx(port, dxl_id, 132)
        if res == 0:
            vals.append(s32(v))
        else:
            fails += 1
        time.sleep(0.005)
    extra = {}
    for name, addr, size in (
        ("Present_PWM", 124, 2),
        ("Present_Load", 126, 2),
        ("Present_Velocity", 128, 4),
        ("Moving", 122, 1),
        ("Moving_Status", 123, 1),
        ("Torque_Enable", 64, 1),
        ("Hardware_Error_Status", 70, 1),
        ("Present_Input_Voltage", 144, 2),
        ("Present_Temperature", 146, 1),
        ("Realtime_Tick", 120, 2),
    ):
        fn = {
            1: packet.read1ByteTxRx,
            2: packet.read2ByteTxRx,
            4: packet.read4ByteTxRx,
        }[size]
        v, res, _ = fn(port, dxl_id, addr)
        if res == 0:
            if size == 4:
                v = s32(v)
            elif size == 2 and name in ("Present_PWM", "Present_Load"):
                v = v - 65536 if v >= 32768 else v
            extra[name] = v
        else:
            extra[name] = "NG"
finally:
    port.closePort()

n = len(vals)
jumps = [abs(b - a) for a, b in itertools.pairwise(vals)]
big = sum(1 for j in jumps if j > 50)
turns = Counter(v // 4096 for v in vals)
within = [v % 4096 for v in vals]
top = Counter(vals).most_common(8)
print(f"{sys.argv[1]} ID {dxl_id}: {n} サンプル/{secs:.0f}s 読み失敗={fails}")
print(
    f"  値の範囲 {min(vals)} 〜 {max(vals)}（幅 {max(vals) - min(vals)} tick） 種類={len(set(vals))}"
)
print(
    f"  回転数カウントの分布 {dict(turns)}   1回転内の位置 {min(within)}〜{max(within)}"
)
print(
    f"  隣接サンプル間で 50 tick 超の跳び {big} 回（最大 {max(jumps) if jumps else 0} tick）"
)
print(f"  頻出値 {top}")
print(f"  {extra}")

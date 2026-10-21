"""Read-only trace of Present_Position for one motor, plus a register snapshot at the end (NO writes).

python -m koch.tools.pos_trace <port> <id> [seconds]
"""

import argparse
import itertools
import time
from collections import Counter

from koch.dxl import ADDR_PRESENT_POSITION, open_port, s32, to_signed16

EXTRA_REGS = (
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
)
SIGNED16 = ("Present_PWM", "Present_Load")


def build_parser():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("port")
    ap.add_argument("id", type=int)
    ap.add_argument("seconds", type=float, nargs="?", default=10.0)
    return ap


def main():
    args = build_parser().parse_args()
    port, packet = open_port(args.port)
    vals, fails, extra = [], 0, {}
    t0 = time.time()
    try:
        while time.time() - t0 < args.seconds:
            v, res, _ = packet.read4ByteTxRx(port, args.id, ADDR_PRESENT_POSITION)
            if res == 0:
                vals.append(s32(v))
            else:
                fails += 1
            time.sleep(0.005)
        for name, addr, size in EXTRA_REGS:
            fn = {
                1: packet.read1ByteTxRx,
                2: packet.read2ByteTxRx,
                4: packet.read4ByteTxRx,
            }[size]
            v, res, _ = fn(port, args.id, addr)
            if res != 0:
                extra[name] = "NG"
            elif size == 4:
                extra[name] = s32(v)
            elif size == 2 and name in SIGNED16:
                extra[name] = to_signed16(v)
            else:
                extra[name] = v
    finally:
        port.closePort()
    n = len(vals)
    print(
        f"{args.port} ID {args.id}: {n} サンプル/{args.seconds:.0f}s 読み失敗={fails}"
    )
    if not vals:
        print(f"  {extra}")
        return
    jumps = [abs(b - a) for a, b in itertools.pairwise(vals)]
    big = sum(1 for j in jumps if j > 50)
    turns = Counter(v // 4096 for v in vals)
    within = [v % 4096 for v in vals]
    top = Counter(vals).most_common(8)
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


if __name__ == "__main__":
    main()

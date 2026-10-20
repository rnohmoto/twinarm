"""Read-only go/no-go check of one Koch pair right before a teleop start (NO writes).

Exit 0 = every blocking check passed. Uses only read1/2/4ByteTxRx.
"""

import argparse
import itertools
import json
import sys
import time
from pathlib import Path

from dynamixel_sdk import PacketHandler, PortHandler

MODEL = {1060: "XL430-W250", 1200: "XL330-M288", 1190: "XL330-M077"}
JOINTS = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]
EXPECT = {
    "leader": ["XL330-M077"] * 6,
    "follower": ["XL430-W250", "XL430-W250"] + ["XL330-M288"] * 4,
}


def s32(v):
    return v - (1 << 32) if v >= (1 << 31) else v


def rd(packet, port, dxl_id, addr, size):
    fn = {1: packet.read1ByteTxRx, 2: packet.read2ByteTxRx, 4: packet.read4ByteTxRx}[
        size
    ]
    for _ in range(4):
        val, res, _err = fn(port, dxl_id, addr)
        if res == 0:
            return val
    return None


def read_bus(port_name, trace_sec):
    port = PortHandler(port_name)
    packet = PacketHandler(2.0)
    if not port.openPort():
        return None, None
    port.setBaudRate(1_000_000)
    rows, trace = {}, []
    try:
        for i in range(1, 7):
            model = rd(packet, port, i, 0, 2)
            if model is None:
                rows[i] = None
                continue
            rows[i] = {
                "model": MODEL.get(model, str(model)),
                "drive_mode": rd(packet, port, i, 10, 1),
                "homing_offset": s32(rd(packet, port, i, 20, 4)),
                "range_max": rd(packet, port, i, 48, 4),
                "range_min": rd(packet, port, i, 52, 4),
                "torque": rd(packet, port, i, 64, 1),
                "hw": rd(packet, port, i, 70, 1),
                "pos": s32(rd(packet, port, i, 132, 4)),
                "volt": rd(packet, port, i, 144, 2) / 10,
                "temp": rd(packet, port, i, 146, 1),
            }
        t0 = time.time()
        while time.time() - t0 < trace_sec:  # base joint only
            v, res, _ = packet.read4ByteTxRx(port, 1, 132)
            if res == 0:
                trace.append(s32(v))
            time.sleep(0.005)
    finally:
        port.closePort()
    return rows, trace


def norm(joint, raw, cal):
    lo, hi = cal["range_min"], cal["range_max"]
    b = min(hi, max(lo, raw))
    return (
        (b - lo) / (hi - lo) * 100
        if joint == "gripper"
        else (b - lo) / (hi - lo) * 200 - 100
    )


def check(role, port_name, cal_path, trace_sec, problems):
    cal = json.loads(Path(cal_path).read_text())
    rows, trace = read_bus(port_name, trace_sec)
    if rows is None:
        problems.append(f"{role}: ポートを開けない {port_name}")
        return None
    pose = {}
    print(f"[{role}] {port_name}")
    for i, j in enumerate(JOINTS, start=1):
        r = rows.get(i)
        if r is None:
            problems.append(f"{role} ID {i} {j}: 応答なし")
            continue
        c = cal[j]
        if r["model"] != EXPECT[role][i - 1]:
            problems.append(
                f"{role} ID {i}: モーター型 {r['model']} が {role} の想定 {EXPECT[role][i - 1]} と違う(ポート取り違え?)"
            )
        if r["hw"]:
            problems.append(f"{role} ID {i} {j}: Hardware_Error_Status=0x{r['hw']:02x}")
        for k in ("drive_mode", "homing_offset", "range_min", "range_max"):
            if c[k] != r[k]:
                problems.append(
                    f"{role} {j}.{k}: 較正ファイル {c[k]} != モーター {r[k]} (起動すると較正プロンプトが出る)"
                )
        lim = (11.0, 13.0) if r["model"] == "XL430-W250" else (4.6, 5.6)
        if not lim[0] <= r["volt"] <= lim[1]:
            problems.append(f"{role} ID {i} {j}: 電圧 {r['volt']} V が想定 {lim} の外")
        if r["temp"] >= 50:
            problems.append(f"{role} ID {i} {j}: 温度 {r['temp']} °C")
        if j == "shoulder_pan" and not 0 <= r["pos"] <= 4095:
            problems.append(f"{role} {j}: 現在位置 {r['pos']} が 1 回転(0-4095)の外")
        if abs(r["pos"]) > 3 * 4096:
            problems.append(f"{role} {j}: 現在位置 {r['pos']} が多回転に飛んでいる")
        pose[j] = norm(j, r["pos"], c)
        out = (
            ""
            if c["range_min"] - 150 <= r["pos"] <= c["range_max"] + 150
            else "  ← 較正範囲の外(正規化は端に丸められる)"
        )
        print(
            f"  {i} {j:<13} {r['model']:<11} pos={r['pos']:>6} norm={pose[j]:>7.1f} torque={r['torque']} hw={r['hw']} "
            f"{r['volt']:>4.1f}V {r['temp']}C{out}"
        )
    if trace:
        jumps = sum(1 for a, b in itertools.pairwise(trace) if abs(b - a) > 50)
        print(
            f"  base(ID1) trace: {len(trace)} samples / {trace_sec:.0f}s  range {min(trace)}..{max(trace)}  50tick 超の跳び {jumps} 回"
        )
        if jumps:
            problems.append(
                f"{role} shoulder_pan: {trace_sec:.0f}s のトレースで 50 tick 超の跳びが {jumps} 回"
            )
        if min(trace) < 0 or max(trace) > 4095:
            problems.append(
                f"{role} shoulder_pan: トレース中に 0-4095 の外の値 ({min(trace)}..{max(trace)})"
            )
    else:
        problems.append(f"{role} shoulder_pan: トレースを読めない")
    return pose


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leader-port", required=True)
    ap.add_argument("--follower-port", required=True)
    ap.add_argument("--leader-calib", required=True)
    ap.add_argument("--follower-calib", required=True)
    ap.add_argument("--trace-sec", type=float, default=10.0)
    args = ap.parse_args()
    problems = []
    lp = check(
        "leader",
        args.leader_port,
        args.leader_calib,
        min(args.trace_sec, 3.0),
        problems,
    )
    fp = check(
        "follower", args.follower_port, args.follower_calib, args.trace_sec, problems
    )
    if lp and fp:
        print(
            "[start ramp] 起動合流ランプ(1.5 s)でフォロワーが動く量 = leader − follower [norm]:"
        )
        print(
            "  "
            + "  ".join(
                f"{j.split('_')[-1]}={lp[j] - fp[j]:+.1f}"
                for j in JOINTS
                if j in lp and j in fp
            )
        )
    if problems:
        print("NO-GO:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("GO: ブロッキング項目は全て合格")
    return 0


if __name__ == "__main__":
    sys.exit(main())

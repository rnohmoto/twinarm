"""Read-only register snapshot of one Koch bus (scratch script; performs NO writes).

Only read1/2/4ByteTxRx are used. Compares Homing_Offset / Min / Max / Drive_Mode with
lerobot calibration JSON files the same way DynamixelMotorsBus.is_calibrated does.
"""

import argparse
import json
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
HW_BITS = {0: "入力電圧", 2: "過熱", 3: "エンコーダ", 4: "電気ショック", 5: "過負荷"}


def s32(v):
    return v - (1 << 32) if v >= (1 << 31) else v


def s16(v):
    return v - (1 << 16) if v >= (1 << 15) else v


def rd(packet, port, dxl_id, addr, size):
    fn = {1: packet.read1ByteTxRx, 2: packet.read2ByteTxRx, 4: packet.read4ByteTxRx}[
        size
    ]
    for _ in range(4):
        val, res, _err = fn(
            port, dxl_id, addr
        )  # err may carry the alert bit; data still valid
        if res == 0:
            return val
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument(
        "--calib", action="append", default=[], help="calibration JSON to compare"
    )
    args = ap.parse_args()

    port = PortHandler(args.port)
    packet = PacketHandler(2.0)
    if not port.openPort():
        raise SystemExit(f"ポートが開けません: {args.port}")
    port.setBaudRate(1_000_000)
    rows = {}
    try:
        for i in range(1, 7):
            model = rd(packet, port, i, 0, 2)
            if model is None:
                rows[i] = None
                continue
            r = {
                "model": MODEL.get(model, str(model)),
                "drive_mode": rd(packet, port, i, 10, 1),
                "op_mode": rd(packet, port, i, 11, 1),
                "homing": s32(rd(packet, port, i, 20, 4)),
                "max": rd(packet, port, i, 48, 4),
                "min": rd(packet, port, i, 52, 4),
                "torque": rd(packet, port, i, 64, 1),
                "hw_err": rd(packet, port, i, 70, 1),
                "p_gain": rd(packet, port, i, 84, 2),
                "pos": s32(rd(packet, port, i, 132, 4)),
                "volt": rd(packet, port, i, 144, 2),
                "temp": rd(packet, port, i, 146, 1),
            }
            if model in (1190, 1200):  # XL330 only: current registers
                r["cur_limit"] = rd(packet, port, i, 38, 2)
                r["goal_cur"] = s16(rd(packet, port, i, 102, 2))
                r["cur"] = s16(rd(packet, port, i, 126, 2))
            rows[i] = r
    finally:
        port.closePort()

    print(f"port {args.port}")
    hdr = f"{'ID':>2} {'joint':<13} {'model':<11} {'drv':>3} {'mode':>4} {'homing':>7} {'min':>5} {'max':>5} {'torq':>4} {'hwerr':>5} {'Pgain':>5} {'pos':>6} {'V':>5} {'degC':>4} {'Ilim':>5} {'Igoal':>5} {'I':>4}"
    print(hdr)
    for i, r in rows.items():
        j = JOINTS[i - 1]
        if r is None:
            print(f"{i:>2} {j:<13} 応答なし")
            continue
        print(
            f"{i:>2} {j:<13} {r['model']:<11} {r['drive_mode']:>3} {r['op_mode']:>4} {r['homing']:>7} {r['min']:>5} {r['max']:>5} "
            f"{r['torque']:>4} {r['hw_err']:>5} {r['p_gain']:>5} {r['pos']:>6} {int(r['volt']) / 10:>5.1f} {r['temp']:>4} "
            f"{r.get('cur_limit', '-')!s:>5} {r.get('goal_cur', '-')!s:>5} {r.get('cur', '-')!s:>4}"
        )
    errs = {i: r["hw_err"] for i, r in rows.items() if r and r["hw_err"]}
    for i, e in errs.items():
        names = [n for b, n in HW_BITS.items() if int(e) & (1 << b)]
        print(f"  ⚠ ID {i} Hardware_Error_Status=0x{e:02x} ({'/'.join(names)})")
    on = [i for i, r in rows.items() if r and r["torque"]]
    print(
        f"  torque ON の ID: {on if on else 'なし(全軸 OFF)'}   HW エラー: {'あり' if errs else 'なし'}"
    )

    for path in args.calib:
        cal = json.loads(Path(path).read_text())
        diffs = []
        for j, c in cal.items():
            r = rows.get(c["id"])
            if r is None:
                diffs.append(f"{j}: 応答なし")
                continue
            for key, reg in (
                ("drive_mode", "drive_mode"),
                ("homing_offset", "homing"),
                ("range_min", "min"),
                ("range_max", "max"),
            ):
                if c[key] != r[reg]:
                    diffs.append(f"{j}.{key}: file={c[key]} motor={r[reg]}")
        tag = (
            "一致(is_calibrated 相当=True)" if not diffs else f"不一致 {len(diffs)} 件"
        )
        print(f"  較正比較 {Path(path).name}: {tag}")
        for d in diffs:
            print(f"      {d}")


if __name__ == "__main__":
    main()

"""Read-only register dump of several Koch buses and a side-by-side diff (NO writes).

python -m koch.tools.reg_diff <label> <port> [<label> <port> ...]
"""

import argparse

from koch.dxl import open_port

REGS = [  # (addr, size, name, area)
    (6, 1, "FW", "E"),
    (9, 1, "Return_Delay", "E"),
    (10, 1, "Drive_Mode", "E"),
    (11, 1, "Operating_Mode", "E"),
    (12, 1, "Secondary_ID", "E"),
    (13, 1, "Protocol", "E"),
    (24, 4, "Moving_Threshold", "E"),
    (31, 1, "Temp_Limit", "E"),
    (32, 2, "Max_Volt", "E"),
    (34, 2, "Min_Volt", "E"),
    (36, 2, "PWM_Limit", "E"),
    (38, 2, "Current_Limit", "E"),
    (40, 4, "Accel_Limit", "E"),
    (44, 4, "Velocity_Limit", "E"),
    (60, 1, "Startup_Cfg", "E"),
    (63, 1, "Shutdown", "E"),
    (64, 1, "Torque", "R"),
    (68, 1, "Status_Ret", "R"),
    (70, 1, "HW_Err", "R"),
    (76, 2, "Vel_I", "R"),
    (78, 2, "Vel_P", "R"),
    (80, 2, "Pos_D", "R"),
    (82, 2, "Pos_I", "R"),
    (84, 2, "Pos_P", "R"),
    (88, 2, "FF2", "R"),
    (90, 2, "FF1", "R"),
    (98, 1, "Bus_Watchdog", "R"),
    (100, 2, "Goal_PWM", "R"),
    (102, 2, "Goal_Current", "R"),
    (108, 4, "Prof_Accel", "R"),
    (112, 4, "Prof_Vel", "R"),
    (146, 1, "Temp", "R"),
]
MODEL = {1060: "XL430", 1200: "M288", 1190: "M077"}


def dump(port_name):
    port, packet = open_port(port_name)
    out = {}
    try:
        for i in range(1, 7):
            m, res, _ = packet.read2ByteTxRx(port, i, 0)
            if res != 0:
                continue
            row = {"model": MODEL.get(m, str(m))}
            for addr, size, name, _area in REGS:
                fn = {
                    1: packet.read1ByteTxRx,
                    2: packet.read2ByteTxRx,
                    4: packet.read4ByteTxRx,
                }[size]
                v, res, _ = fn(port, i, addr)
                row[name] = v if res == 0 else None
            out[i] = row
    finally:
        port.closePort()
    return out


def build_parser():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "pairs", nargs="+", metavar="LABEL PORT", help="label and port, repeated"
    )
    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    if len(args.pairs) % 2:
        ap.error("<label> <port> の組で渡してください")
    labels, ports = args.pairs[0::2], args.pairs[1::2]
    dumps = {lab: dump(p) for lab, p in zip(labels, ports)}
    names = [r[2] for r in REGS]
    print(f"比較: {labels}")
    for i in range(1, 7):
        diffs = []
        for name in names:
            vals = [dumps[lab].get(i, {}).get(name) for lab in labels]
            if len(set(vals)) > 1:
                diffs.append(f"{name}={'/'.join(str(v) for v in vals)}")
        models = "/".join(str(dumps[lab].get(i, {}).get("model")) for lab in labels)
        print(f"ID {i} [{models}]: " + ("差なし" if not diffs else "  ".join(diffs)))


if __name__ == "__main__":
    main()

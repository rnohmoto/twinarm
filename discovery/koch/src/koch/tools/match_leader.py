"""Fit the LEADER's calibration to the follower from two matched poses (reads only the bus).

lerobot maps each arm's recorded sweep to -100..100. When the two arms were swept over
different physical ranges (the leader has more travel, or a sweep stopped short), the same
normalized value is a different angle. This tool reads both arms at two poses the user
makes identical by eye (stretched / folded), then rewrites the leader JSON so that the
leader's normalized value equals the follower's at both poses:

  lift / elbow / wrist_flex : new leader range_min/range_max (two points -> exact fit)
  shoulder_pan / wrist_roll : new leader homing_offset (mean of leader-follower raw difference)
  gripper                   : untouched (trigger vs jaw differ by design)

Only the JSON changes; apply it to the motors afterwards with lerobot-calibrate (plain ENTER
at the "use provided calibration file" prompt). Both arms must be torque-free (they are after
a normal teleop exit); nothing is written to any motor here.
"""

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from dynamixel_sdk import PacketHandler, PortHandler

from koch.paths import CONFIG_DIR, WORK_DIR

JOINTS = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]
RANGED = ["shoulder_lift", "elbow_flex", "wrist_flex"]
HOMED = ["shoulder_pan", "wrist_roll"]
POSES = [
    (
        "伸ばし",
        "両腕とも: 上腕を水平に前へ、肘を伸ばし切って一直線、手首もまっすぐ。土台は正面、グリッパは水平",
    ),
    (
        "畳み",
        "両腕とも: 上腕を奥いっぱい(垂直)、肘を曲げ切り、手首も曲げ切り。土台は正面、グリッパは水平",
    ),
]


def s32(v):
    return v - (1 << 32) if v >= (1 << 31) else v


class Arm:
    def __init__(self, port_name):
        self.port = PortHandler(port_name)
        self.packet = PacketHandler(2.0)
        if not self.port.openPort():
            sys.exit(f"ポートが開けません: {port_name}")
        self.port.setBaudRate(1_000_000)

    def read1(self, dxl_id, addr, size):
        fn = {1: self.packet.read1ByteTxRx, 4: self.packet.read4ByteTxRx}[size]
        for _ in range(4):
            v, res, _ = fn(self.port, dxl_id, addr)
            if res == 0:
                return v
        return None

    def torque_on_ids(self):
        return [i for i in range(1, 7) if self.read1(i, 64, 1)]

    def positions(self, seconds=1.0):
        acc = {i: [] for i in range(1, 7)}
        t0 = time.time()
        while time.time() - t0 < seconds:
            for i in range(1, 7):
                v = self.read1(i, 132, 4)
                if v is not None:
                    acc[i].append(s32(v))
            time.sleep(0.02)
        return {JOINTS[i - 1]: (sum(a) / len(a) if a else None) for i, a in acc.items()}

    def close(self):
        self.port.closePort()


def frac(raw, cal):
    return (raw - cal["range_min"]) / (cal["range_max"] - cal["range_min"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leader-port", required=True)
    ap.add_argument("--follower-port", required=True)
    ap.add_argument("--leader-id", default="koch_leader_B")
    ap.add_argument("--follower-id", default="koch_follower_B")
    ap.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    ap.add_argument("--seconds", type=float, default=1.0, help="各姿勢での平均時間")
    ap.add_argument(
        "--dry-run", action="store_true", help="JSON を書かずに結果だけ表示"
    )
    args = ap.parse_args()

    lpath = args.config_dir / "calibration" / "koch_leader" / f"{args.leader_id}.json"
    fpath = (
        args.config_dir / "calibration" / "koch_follower" / f"{args.follower_id}.json"
    )
    L = json.loads(lpath.read_text(encoding="utf-8"))
    F = json.loads(fpath.read_text(encoding="utf-8"))

    leader, follower = Arm(args.leader_port), Arm(args.follower_port)
    try:
        on = leader.torque_on_ids() + follower.torque_on_ids()
        if on:
            sys.exit(
                f"トルクが入っている軸があります {on}。テレオペを止めてから(トルクが抜けた状態で)実行してください"
            )
        samples = []
        for name, text in POSES:
            input(f"\n[{name}] {text}\n  両腕をその姿勢で保持したまま Enter → ")
            lp, fp = leader.positions(args.seconds), follower.positions(args.seconds)
            samples.append((lp, fp))
            print(f"  {'joint':<14}{'L raw':>8}{'F raw':>8}{'L%':>8}{'F%':>8}")
            for j in JOINTS:
                lv, fv = lp[j], fp[j]
                ln = frac(lv, L[j]) * (100 if j == "gripper" else 200) - (
                    0 if j == "gripper" else 100
                )
                fn = frac(fv, F[j]) * (100 if j == "gripper" else 200) - (
                    0 if j == "gripper" else 100
                )
                print(f"  {j:<14}{lv:>8.0f}{fv:>8.0f}{ln:>8.1f}{fn:>8.1f}")
    finally:
        leader.close()
        follower.close()

    (l1, f1), (l2, f2) = samples
    new = json.loads(json.dumps(L))
    print("\n=== リーダー JSON の変更案 ===")
    for j in RANGED:
        a, b = (
            frac(f1[j], F[j]),
            frac(f2[j], F[j]),
        )  # follower fractions at the two poses
        if abs(b - a) < 0.3:
            print(
                f"  {j:<14} 2 姿勢の差が小さい(フォロワーで {abs(b - a) * 100:.0f}%)ので変更しない"
            )
            continue
        span = (l2[j] - l1[j]) / (b - a)
        lo = l1[j] - a * span
        hi = lo + span
        if span < 300:
            print(
                f"  {j:<14} 計算結果が不自然(span={span:.0f})。姿勢の向きが両腕で逆の可能性。変更しない"
            )
            continue
        new[j]["range_min"], new[j]["range_max"] = round(lo), round(hi)
        print(
            f"  {j:<14} range {L[j]['range_min']}-{L[j]['range_max']} (幅 {L[j]['range_max'] - L[j]['range_min']})"
            f" → {new[j]['range_min']}-{new[j]['range_max']} (幅 {new[j]['range_max'] - new[j]['range_min']}),"
            f" フォロワー幅 {F[j]['range_max'] - F[j]['range_min']}"
        )
    for j in HOMED:
        shift = ((l1[j] - f1[j]) + (l2[j] - f2[j])) / 2
        spread = abs((l1[j] - f1[j]) - (l2[j] - f2[j]))
        new[j]["homing_offset"] = round(L[j]["homing_offset"] - shift)
        print(
            f"  {j:<14} homing_offset {L[j]['homing_offset']} → {new[j]['homing_offset']}"
            f" (L−F の平均 {shift:+.0f} tick = {shift * 360 / 4096:+.1f}°、2 姿勢間の差 {spread:.0f} tick)"
        )
    print("  gripper        変更しない(設計上の差)")

    if args.dry_run:
        print("\n--dry-run: 書き込みなし")
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    bdir = WORK_DIR / f"calib_backup_{stamp}_match"
    bdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(lpath, bdir / lpath.name)
    lpath.write_text(json.dumps(new, indent=4) + "\n", encoding="utf-8")
    print(f"\n書き込み: {lpath}\n退避: {bdir / lpath.name}")
    print(
        "\n次: モーターへ反映(プロンプトで素の Enter):\n"
        f"  uv run lerobot-calibrate --teleop.type=koch_leader --teleop.port={args.leader_port}"
        f" --teleop.id={args.leader_id} --teleop.calibration_dir={args.config_dir / 'calibration' / 'koch_leader'}"
    )


if __name__ == "__main__":
    main()

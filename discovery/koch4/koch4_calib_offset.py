"""koch4_calib_offset.py — leader/follower の初期位置ズレを数値で見る（mock/v0 版の koch4 対応）.

「同期はするが初期位置が微妙にズレる」「過負荷停止→電源入れ直しの後に座標が合っていない気がする」の
切り分け用。両アームのトルクを切り、手で同じ姿勢に合わせて保持すると、各関節の生位置[tick]と
正規化位置[%]を両アームで比較表示する。正規化のズレが大きい関節(目安 >3%)が犯人。

対処は2択:
  A) 再キャリブレーション(推奨): lerobot-calibrate で、最初の Enter を押す瞬間の姿勢を両アームで
     厳密に一致させる。プロンプトでは必ず 'c'+Enter(素の Enter は保存済み再利用)
  B) JSON 手修正: shoulder_pan/wrist_roll(レンジ 0..4095 固定)は homing_offset を ±pulse で編集
     (1°≈11.4p)、掃引4関節は range_min/max を同量シフト。修正前にファイルをコピー

過負荷停止の後: サーボは電源を入れ直すまでトルクが入らず(Hardware_Error bit5)、入れ直しても
EEPROM の homing_offset は残るので座標は変わらないはず。それでも合わないなら本ツールで数値化する。

Risk class: torque off (both arms) — フォロワーは支えないと落ちる。

使い方（Mac・descovery で `uv run`）:
  uv run python koch4/koch4_calib_offset.py --leader-port /dev/tty.usbmodemXXXX \
      --follower-port /dev/tty.usbmodemYYYY --leader-id koch_leader_A --follower-id koch_follower_A
  # 較正 JSON は koch4/config/calibration/ から読む(--lerobot-cache で ~/.cache から)。--watch で連続表示
"""

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):  # Windows cp932 console: never crash on symbols
    _reconfigure(errors="replace")

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = HERE / "config"
ADDR_TORQUE_ENABLE = 64
ADDR_PRESENT_POSITION = 132
JOINTS = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]


def open_port(path):
    """Open a Dynamixel port at 1 Mbps."""
    from dynamixel_sdk import PacketHandler, PortHandler

    port = PortHandler(path)
    packet = PacketHandler(2.0)
    if not port.openPort() or not port.setBaudRate(1000000):
        raise SystemExit(f"ポートを開けません: {path}")
    return port, packet


def read_positions(port, packet, ids):
    """Read Present_Position of each id (None on failure)."""
    out = {}
    for i in ids:
        pos, res, _ = packet.read4ByteTxRx(port, i, ADDR_PRESENT_POSITION)
        out[i] = pos if res == 0 else None
    return out


def load_calibration(config_dir, kind, arm_id, lerobot_cache):
    """Return (path, dict) for one arm's calibration JSON, or None."""
    if not lerobot_cache:
        path = config_dir / "calibration" / kind / f"{arm_id}.json"
        if path.exists():
            return str(path), json.loads(path.read_text(encoding="utf-8"))
        print(f"calib[{kind}]: {path} が無いので lerobot 既定の場所を探します")
    root = os.path.expanduser(
        os.environ.get(
            "HF_LEROBOT_CALIBRATION", "~/.cache/huggingface/lerobot/calibration"
        )
    )
    for p in glob.glob(os.path.join(root, "**", f"{arm_id}.json"), recursive=True):
        try:
            with open(p, encoding="utf-8") as f:
                return p, json.load(f)
        except (OSError, ValueError):
            continue
    return None


def normalize(raw, calib_motor, is_gripper):
    """Normalize like lerobot: map the range to [-100,100] (gripper: [0,100])."""
    lo, hi = calib_motor.get("range_min"), calib_motor.get("range_max")
    if lo is None or hi is None or hi == lo or raw is None:
        return None
    x = (raw - lo) / (hi - lo)
    return x * 100 if is_gripper else x * 200 - 100


def build_parser():
    """Define the command line."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--leader-port", required=True)
    ap.add_argument("--follower-port", required=True)
    ap.add_argument("--leader-id", default="koch_leader_arm")
    ap.add_argument("--follower-id", default="koch_follower_arm")
    ap.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    ap.add_argument(
        "--lerobot-cache", action="store_true", help="較正を ~/.cache から探す"
    )
    ap.add_argument("--ids", default="1,2,3,4,5,6")
    ap.add_argument("--seconds", type=float, default=5, help="平均するサンプリング秒数")
    ap.add_argument("--watch", action="store_true", help="1秒ごとに連続表示")
    return ap


def report(ids, acc_l, acc_f, lcal, fcal):
    """Print the comparison table for one snapshot."""

    def avg(xs):
        return sum(xs) / len(xs) if xs else None

    print(
        f"\n{'関節':14s} {'L生tick':>9s} {'F生tick':>9s} {'L正規化%':>9s} {'F正規化%':>9s} "
        f"{'ズレ%':>7s}  修正案(FのJSONをずらす量)"
    )
    for idx, i in enumerate(ids):
        name = JOINTS[idx] if idx < len(JOINTS) else f"id{i}"
        lraw, fraw = avg(acc_l[i]), avg(acc_f[i])
        ln = (
            normalize(lraw, lcal[1][name], name == "gripper")
            if (lcal and lraw is not None)
            else None
        )
        fn = (
            normalize(fraw, fcal[1][name], name == "gripper")
            if (fcal and fraw is not None)
            else None
        )
        if ln is None or fn is None:
            print(
                f"{name:14s} {lraw or float('nan'):9.0f} {fraw or float('nan'):9.0f} "
                f"{'—':>9s} {'—':>9s} {'—':>7s}  (calib未検出)"
            )
            continue
        d = fn - ln
        span = fcal[1][name]["range_max"] - fcal[1][name]["range_min"]
        dt = int(d / (100 if name == "gripper" else 200) * span)
        if abs(d) < 3:
            fix = "OK(±3%以内)"
        elif name in ("shoulder_pan", "wrist_roll"):
            fix = f"homing_offset {-dt:+d}p (反映=素のEnter)"
        else:
            fix = f"range_min/max {dt:+d} (または再キャリブ)"
        print(f"{name:14s} {lraw:9.0f} {fraw:9.0f} {ln:9.1f} {fn:9.1f} {d:7.1f}  {fix}")


def main():
    """Entry point."""
    args = build_parser().parse_args()
    ids = [int(x) for x in args.ids.split(",")]
    lport, lpkt = open_port(args.leader_port)
    fport, fpkt = open_port(args.follower_port)
    for i in ids:  # 両方トルクOFF(手で姿勢を合わせられるように)
        lpkt.write1ByteTxRx(lport, i, ADDR_TORQUE_ENABLE, 0)
        fpkt.write1ByteTxRx(fport, i, ADDR_TORQUE_ENABLE, 0)
    print("★ 両アームのトルクを切りました。フォロワーは支えないと落ちます — 注意")

    lcal = load_calibration(
        args.config_dir, "koch_leader", args.leader_id, args.lerobot_cache
    )
    fcal = load_calibration(
        args.config_dir, "koch_follower", args.follower_id, args.lerobot_cache
    )
    for tag, item in (("leader", lcal), ("follower", fcal)):
        print(f"calib[{tag}]: {item[0] if item else '見つからず(生tick比較のみ)'}")

    def snapshot():
        acc_l = {i: [] for i in ids}
        acc_f = {i: [] for i in ids}
        t_end = time.time() + (1.0 if args.watch else args.seconds)
        while time.time() < t_end:
            for i, v in read_positions(lport, lpkt, ids).items():
                if v is not None:
                    acc_l[i].append(v)
            for i, v in read_positions(fport, fpkt, ids).items():
                if v is not None:
                    acc_f[i].append(v)
            time.sleep(0.02)
        report(ids, acc_l, acc_f, lcal, fcal)

    try:
        print("\n両アームを同じ姿勢に合わせて保持してください…")
        if args.watch:
            while True:
                snapshot()
        else:
            snapshot()
            print(
                "\n対処: ズレ>3%の関節があれば (A)再キャリブレーション['c'+Enter・両アーム同姿勢でEnter]"
                "\n      (B)フォロワーJSONの range_min/max を上記tick量だけ両方ずらす(要バックアップ)"
            )
    except KeyboardInterrupt:
        pass
    finally:
        lport.closePort()
        fport.closePort()


if __name__ == "__main__":
    main()

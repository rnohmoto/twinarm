"""koch_calib_offset.py — 初期位置ズレの定量診断ツール

「同期はするが初期位置が微妙にズレる」の原因は、リーダーとフォロワーの
キャリブレーション0点/レンジの不一致。このツールで ズレを数値で見える化 する。

手順:
  1. 両アームのトルクを切った状態で、両方を"同じ姿勢"に手で合わせて保持する
     (例: 61_ログの通り、土台正面・アーム垂直の基準姿勢)
  2. 実行すると各関節の 生位置[tick] と 正規化位置[%] を両アームで比較表示
  3. 正規化のズレが大きい関節(目安 >3%)が犯人。対処は2択:
     A) 再キャリブレーション(推奨): lerobot-calibrate で、最初のEnterを押す瞬間の
        姿勢を両アームで厳密に一致させる(0点はこの瞬間の姿勢で決まる — 61_ログ知見#3)。
        プロンプトでは必ず 'c'+Enter (素のEnterは保存済み再利用になる — 知見#2)
     B) JSON手修正: shoulder_pan/wrist_roll(レンジ0..4095固定でズレの主犯)は homing_offset を
        ±pulseで編集(1°≈11.4p)、掃引4関節は range_min/max を同量シフト。編集の反映は
        次回キャリブプロンプトで「素のEnter」(cを押すと編集が消え再校正になる)。
        修正前にファイルをコピーしておくこと。

使い方:
  conda activate twinarm
  python koch_calib_offset.py --leader-port /dev/tty.usbmodem5B141156061 \
                              --follower-port /dev/tty.usbmodem5B141156401
  # 5秒間サンプリングして比較表を表示。--watch で連続表示(姿勢を変えながら確認可)
"""
import argparse, glob, json, os, time

from dynamixel_sdk import PortHandler, PacketHandler

ADDR_TORQUE_ENABLE = 64
ADDR_PRESENT_POSITION = 132
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]


def open_port(path):
    port = PortHandler(path)
    packet = PacketHandler(2.0)
    if not port.openPort() or not port.setBaudRate(1000000):
        raise SystemExit(f"ポートを開けません: {path}")
    return port, packet


def read_positions(port, packet, ids):
    out = {}
    for i in ids:
        pos, res, _ = packet.read4ByteTxRx(port, i, ADDR_PRESENT_POSITION)
        out[i] = pos if res == 0 else None
    return out


def load_calibrations():
    """~/.cache/huggingface/lerobot/calibration/ 配下のJSONを全部拾う"""
    root = os.path.expanduser(os.environ.get(
        "HF_LEROBOT_CALIBRATION", "~/.cache/huggingface/lerobot/calibration"))
    found = {}
    for p in glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
        try:
            with open(p) as f:
                found[p] = json.load(f)
        except Exception:
            pass
    return found


def normalize(raw, calib_motor, is_gripper):
    """lerobot流の正規化: レンジを[-100,100](gripperは[0,100])に写像"""
    lo, hi = calib_motor.get("range_min"), calib_motor.get("range_max")
    if lo is None or hi is None or hi == lo or raw is None:
        return None
    x = (raw - lo) / (hi - lo)
    return x * 100 if is_gripper else x * 200 - 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leader-port", required=True)
    ap.add_argument("--follower-port", required=True)
    ap.add_argument("--ids", default="1,2,3,4,5,6")
    ap.add_argument("--seconds", type=float, default=5, help="平均するサンプリング秒数")
    ap.add_argument("--watch", action="store_true", help="1秒ごとに連続表示")
    args = ap.parse_args()
    ids = [int(x) for x in args.ids.split(",")]

    lport, lpkt = open_port(args.leader_port)
    fport, fpkt = open_port(args.follower_port)
    for i in ids:  # 両方トルクOFF(手で姿勢を合わせられるように)
        lpkt.write1ByteTxRx(lport, i, ADDR_TORQUE_ENABLE, 0)
        fpkt.write1ByteTxRx(fport, i, ADDR_TORQUE_ENABLE, 0)
    print("★ 両アームのトルクを切りました。フォロワーは支えないと落ちます — 注意")

    calibs = load_calibrations()
    lcal = fcal = None
    for p, c in calibs.items():
        if not (isinstance(c, dict) and all(j in c for j in JOINTS[:3])):
            continue
        low = p.lower()
        if "leader" in low:
            lcal = (p, c)
        elif "follower" in low:
            fcal = (p, c)
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
        avg = lambda xs: sum(xs) / len(xs) if xs else None
        print(f"\n{'関節':14s} {'L生tick':>9s} {'F生tick':>9s} {'L正規化%':>9s} {'F正規化%':>9s} "
              f"{'ズレ%':>7s}  修正案(FのJSONをずらす量)")
        for idx, i in enumerate(ids):
            name = JOINTS[idx] if idx < len(JOINTS) else f"id{i}"
            lraw, fraw = avg(acc_l[i]), avg(acc_f[i])
            ln = normalize(lraw, lcal[1][name], name == "gripper") if (lcal and lraw is not None) else None
            fn = normalize(fraw, fcal[1][name], name == "gripper") if (fcal and fraw is not None) else None
            if ln is not None and fn is not None:
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
            else:
                print(f"{name:14s} {lraw or float('nan'):9.0f} {fraw or float('nan'):9.0f} "
                      f"{'—':>9s} {'—':>9s} {'—':>7s}  (calib未検出)")

    try:
        print("\n両アームを同じ姿勢に合わせて保持してください…")
        if args.watch:
            while True:
                snapshot()
        else:
            snapshot()
            print("\n対処: ズレ>3%の関節があれば (A)再キャリブレーション['c'+Enter・両アーム同姿勢でEnter]"
                  "\n      (B)フォロワーJSONの range_min/max を上記tick量だけ両方ずらす(要バックアップ)")
    except KeyboardInterrupt:
        pass
    finally:
        lport.closePort()
        fport.closePort()


if __name__ == "__main__":
    main()

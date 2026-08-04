"""koch_teleop_plus.py — テレオペ + 電流モニタ配信 + グリッパー力覚フィードバック(試作)

koch_teleop_robust.py の後継。lerobot 0.5.x のクラスを直接使った自前ループで、
テレオペしながら以下を同時に行う:
  1. フォロワー全軸の電流/負荷を毎フレーム読み、UDP(127.0.0.1:8765)へJSON配信
     → koch_live_plot.py がリアルタイムグラフ表示(--plotで自動起動)
  2. --csv でCSV記録(タイムスタンプ+電流+リーダー指令/フォロワー実位置)
  3. --ff gripper でリーダーのグリッパーに力覚FB(ALOHA方式):
     リーダーのグリッパーを電流ベース位置制御(Operating_Mode=5)にして開位置を保持させ、
     フォロワーのグリッパー電流に比例した Goal_Current を毎フレーム書く。
     → 物を掴むとリーダーのトリガーが重くなる(握り返してくる)
  3b. --ff arm で腕3軸(elbow_flex/wrist_flex/wrist_roll)にもFACTR式の反力:
     リーダー腕関節を電流制御モード(0)にし、τ_L = -gain×(フォロワー電流-デッドバンド) を書く。
     → フォロワーが物に当たると、リーダーの腕が同じ方向に押し返される。
     肩2軸はXL430(電流センサなし)のため対象外。⚠腕反力中はリーダーから手を離さないこと
  4. 通信断(強負荷時の過渡パケット欠け等)は自動再接続(最大20回) — 61_の実証知見を移植

安全(web_research/bilateral_force_feedback_koch_20260803.md の裏取り値):
  - FBはグリッパー(リーダー=XL330-M077)のみ。腕関節には反力を出さない(暴れ防止)
  - Goal_Current上限は既定450mA(M077ストール1470mAの~30%。指先換算~2N)。最強は--ff-gain 2.5 --ff-cap 500
  - リスクは人体でなく発熱側: 60°Cでゲイン半減・65°CでFB停止(Limit70°C手前の自主停止)
  - モード5ではGoal_Current=位置制御トルクの上限=トリガー反力。位置アンカーが残るので安全
  - APIはlerobot v0.5.1タグ実ソースで確認済み(import経路/bus.read/write/sync_readシグネチャ)
  - ゲインは0(FBなし)→0.3→0.8→1.2と漸増して調整すること(30fps離散FBのため平滑必須)

使い方(61_ログの運用レシピと同じ引数体系):
  conda activate twinarm
  python koch_teleop_plus.py \
      --follower-port /dev/tty.usbmodem5B141156401 --follower-id koch_follower_arm \
      --leader-port   /dev/tty.usbmodem5B141156061 --leader-id   koch_leader_arm \
      --plot --csv [--ff gripper]
実機初回はアームを可動域中央に置き、すぐCtrl+Cできる姿勢で。
"""
import argparse, csv, json, logging, os, socket, subprocess, sys, time
from datetime import datetime


class _DropClampWarning(logging.Filter):
    """lerobotの安全クランプ警告(起動時の姿勢差で毎回出る・無害)を抑制"""
    def filter(self, record):
        return "clamped to be safe" not in str(record.getMessage())


logging.getLogger().addFilter(_DropClampWarning())

# lerobot 0.5.1: sync_read リトライ0回対策(koch_teleop_robust.py と同じ注入)
from lerobot.motors.motors_bus import MotorsBus
_orig_sync_read = MotorsBus.sync_read
def _sync_read_retry(self, *args, **kwargs):
    kwargs["num_retry"] = max(int(kwargs.get("num_retry", 0) or 0), 10)
    return _orig_sync_read(self, *args, **kwargs)
MotorsBus.sync_read = _sync_read_retry

from lerobot.robots.koch_follower import KochFollower, KochFollowerConfig          # noqa: E402
from lerobot.teleoperators.koch_leader import KochLeader, KochLeaderConfig        # noqa: E402

try:  # 再接続対象の例外(lerobotの未接続例外も拾う)
    from lerobot.errors import DeviceNotConnectedError
    RETRY_ERRORS = (ConnectionError, DeviceNotConnectedError)
except Exception:
    RETRY_ERRORS = (ConnectionError,)

XL430_JOINTS = {"shoulder_pan", "shoulder_lift"}  # フォロワーのXL430(電流でなく負荷0.1%/unit)


def to_signed16(v):
    return v - 65536 if v > 32767 else v


def setup_gripper_ff(leader, cap_ma):
    """リーダーのグリッパーを電流ベース位置制御にして、開位置を保持させる"""
    bus = leader.bus
    bus.write("Torque_Enable", "gripper", 0, normalize=False)
    bus.write("Operating_Mode", "gripper", 5, normalize=False)  # current-based position
    bus.write("Torque_Enable", "gripper", 1, normalize=False)
    open_pos = bus.read("Present_Position", "gripper", normalize=False)
    bus.write("Goal_Position", "gripper", int(open_pos), normalize=False)
    bus.write("Goal_Current", "gripper", 60, normalize=False)  # 戻りバネの床値から開始
    print(f"[ff] リーダーgripper: current-based position mode / 開位置={int(open_pos)} / 上限{cap_ma}mA")
    mode = int(bus.read("Operating_Mode", "gripper", normalize=False))
    trq = int(bus.read("Torque_Enable", "gripper", normalize=False))
    print(f"[ff] 設定確認: Operating_Mode={mode}(期待5) / Torque_Enable={trq}(期待1)")
    if mode != 5 or trq != 1:
        print("[ff] ⚠ モード/トルクが反映されていません — この状態では握り返しは効きません(この行をClaudeへ)")
    return int(open_pos)


def setup_arm_ff(leader, joints):
    """リーダー腕関節を電流制御モード(0)にして反力ゼロで開始(手では自由に動かせる)"""
    bus = leader.bus
    for j in joints:
        bus.write("Torque_Enable", j, 0, normalize=False)
        bus.write("Operating_Mode", j, 0, normalize=False)  # current control mode
        bus.write("Torque_Enable", j, 1, normalize=False)
        bus.write("Goal_Current", j, 0, normalize=False)
    modes = [int(bus.read("Operating_Mode", j, normalize=False)) for j in joints]
    print(f"[ff-arm] 腕反力ON {joints} Operating_Mode={modes}(期待[0, 0, 0])")


def check_hw_errors(robot):
    """フォロワーのハードウェアエラー停止(過負荷等)を検出して表示する"""
    try:
        errs = robot.bus.sync_read("Hardware_Error_Status", normalize=False)
    except Exception:
        return
    bits = {0: "入力電圧", 2: "過熱", 3: "エンコーダ", 4: "電気ショック", 5: "過負荷"}
    for m, v in errs.items():
        v = int(v)
        if v:
            names = "/".join(n for b, n in bits.items() if v >> b & 1) or str(v)
            print(f"[hw] ⚠ フォロワー{m} がエラー停止中({names}) — 電源を10秒抜いて入れ直すまでこの軸は動きません")


def soft_resync(robot, teleop, fps=30, seconds=1.5):
    """(再)開始時の跳ね防止: フォロワーを現在位置からリーダー位置へ滑らかに合流させる"""
    try:
        start = robot.bus.sync_read("Present_Position")  # 正規化値
        steps = max(int(seconds * fps), 1)
        for i in range(steps):
            target = teleop.get_action()
            a = (i + 1) / steps
            robot.send_action({k: (1 - a) * float(start.get(k.replace(".pos", ""), v)) + a * float(v)
                               for k, v in target.items()})
            time.sleep(1.0 / fps)
    except Exception as e:
        print(f"[sync] 合流ランプ失敗(そのまま続行): {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--follower-port", required=True)
    ap.add_argument("--follower-id", default="koch_follower_arm")
    ap.add_argument("--leader-port", required=True)
    ap.add_argument("--leader-id", default="koch_leader_arm")
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--max-rel", type=float, default=20, help="max_relative_target(安全リミッタ)")
    ap.add_argument("--ff", choices=["off", "gripper", "arm"], default="off",
                    help="力覚FB: gripper=握り返しのみ / arm=握り返し+腕3軸反力(elbow/wrist_flex/wrist_roll)")
    ap.add_argument("--ff-gain", type=float, default=1.5, help="Goal_Current = floor+gain×|follower電流mA|。最強は2.5")
    ap.add_argument("--ff-cap", type=int, default=450, help="Goal_Current上限[mA]。最大900(500超は発熱が速く温度ガード頼み)")
    ap.add_argument("--ff-floor", type=int, default=60, help="常時の戻りバネ電流[mA](トリガーの操作感)")
    ap.add_argument("--ff-deadband", type=int, default=25, help="無負荷自己電流の無視幅[mA](待機~17mA+余裕)")
    ap.add_argument("--ff-arm-gain", type=float, default=0.6, help="腕反力: Goal_Current = -gain×外乱電流")
    ap.add_argument("--ff-arm-cap", type=int, default=150, help="腕反力の上限[mA](最大400)")
    ap.add_argument("--ff-arm-deadband", type=int, default=60, help="腕: 自重/摩擦/加減速電流の無視幅[mA]")
    ap.add_argument("--ff-arm-invert", default="", help="反力が逆向きに感じる関節をカンマ列挙で反転(例: wrist_roll)")
    ap.add_argument("--viz-port", type=int, default=8765, help="UDP配信ポート(0=配信なし)")
    ap.add_argument("--csv", action="store_true", help="CSV記録する")
    ap.add_argument("--plot", action="store_true",
                    help="グラフビューア(koch_live_plot.py)を自動起動(1コマンドでテレオペ+ライブグラフ)")
    ap.add_argument("--panel", action="store_true",
                    help="ブラウザ操作パネル(koch_web_panel.py)を自動起動: グラフ+ゲインスライダ+停止/リセット")
    ap.add_argument("--ctl-port", type=int, default=8766, help="パネルからの制御コマンド受信UDPポート(0=無効)")
    args = ap.parse_args()

    if args.ff_cap > 900:
        args.ff_cap = 900
        print("[ff] cap は安全のため最大900mAに制限しました")
    elif args.ff_cap > 500:
        print(f"[ff] ⚠ cap={args.ff_cap}mA(>500) — 発熱が速く、温度ガード(60°C減衰/65°C停止)が働きやすくなります。短時間で")
    if args.ff_arm_cap > 400:
        args.ff_arm_cap = 400
        print("[ff-arm] 腕capは安全のため最大400mAに制限しました")
    ARM_FF_JOINTS = ["elbow_flex", "wrist_flex", "wrist_roll"]  # 電流が読めるXL330の腕3軸(肩2軸=XL430は対象外)
    arm_invert = {s.strip() for s in args.ff_arm_invert.split(",") if s.strip()}

    viewer = None
    if args.plot and args.viz_port:
        viewer_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "koch_live_plot.py")
        if os.path.exists(viewer_path):
            viewer = subprocess.Popen([sys.executable, viewer_path, "--port", str(args.viz_port)])
            print(f"[plot] グラフビューアを自動起動しました(正常終了時に自動で閉じます)")
        else:
            print(f"[plot] {viewer_path} が見つかりません。別ターミナルで koch_live_plot.py を起動してください")
    panel = None
    if args.panel and args.viz_port:
        panel_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "koch_web_panel.py")
        if os.path.exists(panel_path):
            panel = subprocess.Popen([sys.executable, panel_path, "--telemetry", str(args.viz_port),
                                      "--ctl-port", str(args.ctl_port)])
            print("[panel] ブラウザ操作パネル自動起動 → http://127.0.0.1:8780 (自動で開きます)")
        else:
            print(f"[panel] {panel_path} が見つかりません")

    robot = KochFollower(KochFollowerConfig(
        port=args.follower_port, id=args.follower_id,
        max_relative_target=float(args.max_rel) if args.max_rel > 0 else None))  # lerobotはfloat型のみ受理(intはTypeError)
    teleop = KochLeader(KochLeaderConfig(port=args.leader_port, id=args.leader_id))
    robot.connect()
    teleop.connect()
    check_hw_errors(robot)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if args.viz_port else None
    ctl = None
    if args.ctl_port:
        ctl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ctl.bind(("127.0.0.1", args.ctl_port))
        ctl.setblocking(False)
    writer = None
    if args.csv:
        path = os.path.abspath(os.path.join(
            os.path.dirname(__file__) or ".", "..", "..", "TacitCapture", "logs",
            f"teleop_{datetime.now():%Y%m%d_%H%M%S}.csv"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fcsv = open(path, "w", newline="")
        writer = csv.writer(fcsv)
        print(f"[csv] {path}")

    motors = list(robot.bus.motors)  # ['shoulder_pan', ..., 'gripper']
    if writer:
        writer.writerow(["t_sec"] + [f"{m}.cur" for m in motors] + [f"{m}.Lpos" for m in motors]
                        + [f"{m}.Fpos" for m in motors] + ["ff_mA"])

    ff_on = args.ff in ("gripper", "arm")
    ff_arm = args.ff == "arm"
    if ff_on:
        setup_gripper_ff(teleop, args.ff_cap)
    if ff_arm:
        setup_arm_ff(teleop, ARM_FF_JOINTS)
        print("[ff-arm] ⚠ 腕反力中はリーダーから手を離さない(反力で勝手に動き得る)。"
              "逆向きに感じる関節は --ff-arm-invert で反転")
    ema = 0.0
    ema_arm = {j: 0.0 for j in ARM_FF_JOINTS}
    arm_out = {j: 0 for j in ARM_FF_JOINTS}
    arm_rr = [0]  # 腕温度チェックの巡回インデックス
    last_temp = [0]
    stop_req = [False]
    ff_ma = 0

    def set_ff_mode(new):
        """パネルからのFFモード切替(off/gripper/arm)。移行時の後始末込み"""
        nonlocal ff_on, ff_arm
        if new not in ("off", "gripper", "arm"):
            return
        if new in ("gripper", "arm") and not ff_on:
            setup_gripper_ff(teleop, args.ff_cap)
            ff_on = True
        if new == "off" and ff_on:
            teleop.bus.write("Goal_Current", "gripper", 0, normalize=False)
            teleop.bus.write("Torque_Enable", "gripper", 0, normalize=False)
            ff_on = False
        if new == "arm" and not ff_arm:
            setup_arm_ff(teleop, ARM_FF_JOINTS)
            ff_arm = True
        if new != "arm" and ff_arm:
            for j in ARM_FF_JOINTS:
                teleop.bus.write("Goal_Current", j, 0, normalize=False)
                teleop.bus.write("Torque_Enable", j, 0, normalize=False)
            ff_arm = False
        args.ff = new
        print(f"\n[ctl] FFモード → {new}")
    t0 = time.perf_counter()
    n = 0
    reconnects = 0
    clean_exit = False
    print("[sync] 起動合流ランプ(1.5秒・跳ね防止)…")
    soft_resync(robot, teleop, args.fps)
    print("テレオペ開始。Ctrl+Cで終了。")

    ff_hot = False

    def frame():
        """1フレーム: 追従+電流読み+FB+配信+記録"""
        nonlocal ema, ff_ma, ff_on, ff_arm, n, ff_hot
        t_frame = time.perf_counter()

        if ctl:  # パネルからの制御コマンド(スライダ/ボタン)を処理
            while True:
                try:
                    data, _ = ctl.recvfrom(1024)
                except (BlockingIOError, OSError):
                    break
                try:
                    cmd = json.loads(data.decode())
                except Exception:
                    continue
                if "ff_gain" in cmd:
                    args.ff_gain = float(cmd["ff_gain"])
                if "ff_cap" in cmd:
                    args.ff_cap = int(min(max(float(cmd["ff_cap"]), 0), 900))
                if "ff_floor" in cmd:
                    args.ff_floor = int(min(max(float(cmd["ff_floor"]), 0), 120))
                if "arm_gain" in cmd:
                    args.ff_arm_gain = float(cmd["arm_gain"])
                if "arm_cap" in cmd:
                    args.ff_arm_cap = int(min(max(float(cmd["arm_cap"]), 0), 400))
                if "max_rel" in cmd:
                    robot.config.max_relative_target = float(cmd["max_rel"])
                if "mode" in cmd:
                    set_ff_mode(cmd["mode"])
                if cmd.get("resync"):
                    print("\n[ctl] 合流リセット")
                    soft_resync(robot, teleop, args.fps)
                if cmd.get("stop"):
                    stop_req[0] = True
        if stop_req[0]:
            raise KeyboardInterrupt

        action = teleop.get_action()
        robot.send_action(action)

        # フォロワー電流(生値)一括読み — XL430はPresent_Load(0.1%)、XL330はmA
        cur_raw = robot.bus.sync_read("Present_Current", normalize=False)
        cur = {m: to_signed16(int(v)) * (0.1 if m in XL430_JOINTS else 1.0)
               for m, v in cur_raw.items()}
        # フォロワー実位置(正規化・キャリブ適用済み) — L/F比較と差分表示用
        fpos = robot.bus.sync_read("Present_Position")

        if ff_on:  # グリッパー力覚FB: フォロワー電流→リーダーGoal_Current
            mag = max(abs(cur.get("gripper", 0.0)) - args.ff_deadband, 0.0)
            ema = 0.8 * ema + 0.2 * mag  # EMA平滑(発振防止)。0.2で立ち上がり~0.15秒
            ff_ma = int(min(args.ff_floor + args.ff_gain * ema, args.ff_cap))
            teleop.bus.write("Goal_Current", "gripper", ff_ma, normalize=False)
            # 握り返しの作動を立ち上がりで1回だけ通知(スクロールさせない)
            if not ff_hot and ff_ma >= args.ff_floor + 80:
                ff_hot = True
                print(f"\n[ff] 握り返し作動中: grip={cur.get('gripper', 0.0):.0f}mA → cmd={ff_ma}mA")
            elif ff_hot and ff_ma < args.ff_floor + 40:
                ff_hot = False
            if n % 60 == 0:  # 2秒ごと: 計器は同じ1行を上書き表示 + 温度ガード(60°C減衰/65°C停止)
                temp = int(teleop.bus.read("Present_Temperature", "gripper", normalize=False))
                last_temp[0] = temp
                arm_max = max((abs(x) for x in arm_out.values()), default=0)
                print(f"\r[ff] grip={cur.get('gripper', 0.0):5.0f}mA cmd={ff_ma:3d}mA "
                      f"arm=±{arm_max:3d}mA temp={temp}C   ", end="", flush=True)
                if temp >= 65:
                    print(f"\n[ff] リーダーgripper {temp}°C — FBを停止します(冷えたら再起動)")
                    teleop.bus.write("Goal_Current", "gripper", args.ff_floor, normalize=False)
                    ff_on = False
                elif temp >= 60:
                    args.ff_gain = max(args.ff_gain * 0.5, 0.1)
                    print(f"\n[ff] リーダーgripper {temp}°C — ゲインを{args.ff_gain:.2f}に減衰")

        if ff_arm:  # 腕3軸の反力: フォロワーの外乱電流を符号付きで映す(τ_L = -gain×I_F)
            for j in ARM_FF_JOINTS:
                v = cur.get(j, 0.0)  # lerobotが符号復号済み[mA]
                e = abs(v) - args.ff_arm_deadband
                e = 0.0 if e <= 0 else (e if v > 0 else -e)
                ema_arm[j] = 0.8 * ema_arm[j] + 0.2 * e
                out = int(max(min(-args.ff_arm_gain * ema_arm[j], args.ff_arm_cap), -args.ff_arm_cap))
                if j in arm_invert:
                    out = -out
                if abs(out - arm_out[j]) >= 5 or (out == 0 and arm_out[j] != 0):
                    teleop.bus.write("Goal_Current", j, out, normalize=False)
                    arm_out[j] = out
            if n % 60 == 30:  # 2秒ごと(計器と半周期ずらし): 腕温度を1関節ずつ巡回チェック
                j = ARM_FF_JOINTS[arm_rr[0] % len(ARM_FF_JOINTS)]
                arm_rr[0] += 1
                at = int(teleop.bus.read("Present_Temperature", j, normalize=False))
                if at >= 65:
                    print(f"\n[ff-arm] {j} {at}°C — 腕反力を停止します(冷えたら再起動)")
                    for jj in ARM_FF_JOINTS:
                        teleop.bus.write("Goal_Current", jj, 0, normalize=False)
                    ff_arm = False
                elif at >= 60:
                    args.ff_arm_gain = max(args.ff_arm_gain * 0.5, 0.1)
                    print(f"\n[ff-arm] {j} {at}°C — 腕ゲインを{args.ff_arm_gain:.2f}に減衰")

        t = time.perf_counter() - t0
        if sock:
            sock.sendto(json.dumps({
                "t": round(t, 3), "cur": {m: round(v, 1) for m, v in cur.items()},
                "pos": {k.replace(".pos", ""): round(float(v), 1) for k, v in action.items()},
                "fpos": {m: round(float(v), 1) for m, v in fpos.items()},
                "ff": ff_ma, "mode": args.ff, "temp": last_temp[0], "rec": reconnects, "n": n,
                "params": {"ff_gain": args.ff_gain, "ff_cap": args.ff_cap, "ff_floor": args.ff_floor,
                           "arm_gain": args.ff_arm_gain, "arm_cap": args.ff_arm_cap,
                           "max_rel": robot.config.max_relative_target or 0}}).encode(),
                ("127.0.0.1", args.viz_port))
        if writer:
            writer.writerow([round(t, 3)] + [round(cur.get(m, 0.0), 1) for m in motors]
                            + [round(float(action.get(f"{m}.pos", 0.0)), 1) for m in motors]
                            + [round(float(fpos.get(m, 0.0)), 1) for m in motors]
                            + [ff_ma])
        n += 1
        time.sleep(max(0.0, 1.0 / args.fps - (time.perf_counter() - t_frame)))

    try:
        while True:
            try:
                frame()
            except RETRY_ERRORS as e:
                # 強負荷時の過渡パケット欠け等 → 自動再接続(61_の実証知見)
                reconnects += 1
                if reconnects > 20:
                    print("[robust] 再接続上限に達しました。物理側(コネクタ・電源)の点検が必要です")
                    raise
                print(f"\n[robust] 通信断を検知: {e}\n[robust] 2秒後に再接続します… ({reconnects}/20)")
                for dev in (robot, teleop):
                    try:
                        if dev.is_connected:
                            dev.disconnect()
                    except Exception:
                        pass
                time.sleep(2)
                try:
                    if not robot.is_connected:
                        robot.connect()
                    if not teleop.is_connected:
                        teleop.connect()
                    check_hw_errors(robot)
                    if args.ff in ("gripper", "arm"):
                        setup_gripper_ff(teleop, args.ff_cap)
                        ff_on = True
                    if args.ff == "arm":
                        setup_arm_ff(teleop, ARM_FF_JOINTS)
                        ff_arm = True
                    soft_resync(robot, teleop, args.fps)
                    print("[robust] 再接続成功。テレオペ継続")
                except Exception as e2:
                    print(f"[robust] 再接続失敗: {e2} — 2秒後に再試行")
                    time.sleep(2)
    except KeyboardInterrupt:
        clean_exit = True
        print("\n終了処理中…")
    finally:
        try:
            if args.ff in ("gripper", "arm"):
                teleop.bus.write("Goal_Current", "gripper", 0, normalize=False)
                teleop.bus.write("Torque_Enable", "gripper", 0, normalize=False)
            if args.ff == "arm":
                for j in ARM_FF_JOINTS:
                    teleop.bus.write("Goal_Current", j, 0, normalize=False)
                    teleop.bus.write("Torque_Enable", j, 0, normalize=False)
        except Exception:
            pass
        if writer:
            fcsv.close()
        for child in (viewer, panel):
            if child and child.poll() is None:
                if clean_exit:
                    child.terminate()
                else:
                    print("[plot] 異常終了のためビューア/パネルは開いたままにします(最後の状態を確認可)")
        for dev in (robot, teleop):
            try:
                dev.disconnect()
            except Exception:
                pass
        print(f"完了({n}フレーム)。使用後は電源を抜くこと(過熱防止)")


if __name__ == "__main__":
    main()

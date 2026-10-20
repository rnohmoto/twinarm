#!/usr/bin/env python3
"""Launch the two Koch pairs (four arms) as parallel koch4_teleop.py processes.

Koch には lerobot 公式の bimanual 設定が無い（v0.6.1 に bi_koch なし）ため、
「独立2ペアを2プロセスで並行起動」する。本スクリプトは起動・ポート衝突回避・
ログ分離・一括停止を受け持つ（robotics `scripts/koch/koch_dual_launch.py` 2026-08-11 の後継）。
**2ペアが原則**: 既定は `--pair both`、パネルは1枚で両ペアを表示し、ゲイン等は全ペアへ同じ値を送る。

固定ポート（2ペアで衝突しない）:
  ペアA: telemetry UDP 8765 / 制御 UDP 8766 / VR ブリッジ 8443（テレメトリ複製 8769）
  ペアB: telemetry UDP 8767 / 制御 UDP 8768 / VR ブリッジ 8444（テレメトリ複製 8770）
  2 人目（--vr2: VR ペアのフォロワー機を手で動かす）: telemetry 8771 / 制御 8772 / 複製 8773
    （ブリッジは同じ 1 本で腕 2 本 `--arms B,F`。ページには分身が 2 体出る）
  パネル: http://127.0.0.1:8780（1枚で全ペア）

専用フォルダ（既定・`--config-dir` / `--work-dir` で変更可）:
  config/koch4_config.json              ペアごとのポート・シリアル番号・id・カメラ
  config/koch4_twin.json                VR の分身・物体の設定（ページの編集モードで保存）
  config/calibration/koch_follower/<id>.json, koch_leader/<id>.json   lerobot の較正
  work/logs/dual_<pair>_{teleop,vr}.log, dual_panel.log   各プロセスの標準出力
  work/csv/                                     --csv の記録

使い方（Mac・descovery で `uv run`。ポートはユーザーが渡す＝推測しない）:
  uv run python koch4/koch4_dual_launch.py --list        # ポート+シリアル列挙(読み取りのみ)
  uv run python koch4/koch4_dual_launch.py --init        # config 雛形を作る → 記入
  uv run python koch4/koch4_dual_launch.py --doctor      # 準備状況(握手/VR/2 人で足りないもの)。腕には触れない
  uv run python koch4/koch4_dual_launch.py --ff gripper                # 2ペア同時(既定)
  uv run python koch4/koch4_dual_launch.py --pair A --ff gripper       # ペアA単独(段階テスト)
  uv run python koch4/koch4_dual_launch.py --pair B --vr B --vw        # VR は別枠: ペアBだけ仮想反力＋重さ
  uv run python koch4/koch4_dual_launch.py --pair B --vr B --vw --follower none  # VR 1 人: リーダーだけ(フォロワーは繋がなくてよい)
  uv run python koch4/koch4_dual_launch.py --pair B --vr B --vw --vr2  # 2 人: ペアBのフォロワー機も手で握る入力装置に
  uv run python koch4/koch4_dual_launch.py --dry-run                   # コマンドを表示するだけ

設定ファイルのシリアル番号: /dev/tty.usbmodem* の名前はハブ差し替え・再起動で変わり得るので、
USB シリアル番号を登録すれば起動時に現在のポート名へ解決する。CH343 ボードは個体シリアルが
重複／空の報告がある（74_ §6.3）→ その場合はポート名直書き＋物理ポート固定（テープ）。
"""

import argparse
import json
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):  # Windows cp932 console: never crash on symbols
    _reconfigure(errors="replace")

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = HERE / "config"
DEFAULT_WORK_DIR = HERE / "work"
CONFIG_NAME = "koch4_config.json"
LEADER_ONLY = "none"
PAIR_SETTLE_SEC = 2.0
STOP_GRACE_SEC = 2.0
PANEL_HTTP = 8780
PORTS = {  # ペアごとの通信ポート割り当て(vrviz=ブリッジ専用のテレメトリ複製先)
    "A": {"viz": 8765, "ctl": 8766, "vr": 8443, "vrviz": 8769},
    "B": {"viz": 8767, "ctl": 8768, "vr": 8444, "vrviz": 8770},
}
# --vr2 の 2 人目(VR ペアのフォロワー機を手で動かす teleop)。VR ペアは同時に 1 つなので 1 組
HAND_PORTS = {"viz": 8771, "ctl": 8772, "vrviz": 8773}
HAND_LABEL = "F"

CONFIG_TEMPLATE = {
    "pairs": {
        "A": {
            "leader_port": "/dev/tty.usbmodemXXXX",
            "follower_port": "/dev/tty.usbmodemYYYY",
            "leader_serial": None,
            "follower_serial": None,
            "leader_id": "koch_leader_A",
            "follower_id": "koch_follower_A",
            "follower_host": None,
            "cams": "",
        },
        "B": {
            "leader_port": "/dev/tty.usbmodemZZZZ",
            "follower_port": "/dev/tty.usbmodemWWWW",
            "leader_serial": None,
            "follower_serial": None,
            "leader_id": "koch_leader_B",
            "follower_id": "koch_follower_B",
            "follower_host": None,
            "cams": "",
        },
    },
    "_memo": "serial は --list の USB シリアル番号(ユニークなら登録)。id は較正ファイル名。"
    "follower_port を none にするとそのペアはリーダーのみ(VR 用)。"
    "cams はパネルに映すカメラ index(空=無効)。"
    "follower_host に udp://<握手の場の PC の IP>:9101 を書くとフォロワー無線(--follower wireless 既定)。"
    "その PC では koch4_follower_host.py を先に起動。有線に戻すときは --follower wired。",
}


def list_ports():
    """Print serial ports with USB serial numbers and flag duplicated serials (read-only)."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        sys.exit("pyserial が必要です: uv add pyserial（または pip install pyserial）")
    rows = [
        (
            p.device,
            p.serial_number,
            f"{p.vid:04x}:{p.pid:04x}" if p.vid else "-",
            p.description,
        )
        for p in lp.comports()
    ]
    if not rows:
        print("シリアルポートが見つかりません")
        return
    print(f"{'device':<32} {'serial':<20} {'vid:pid':<10} description")
    for d, s, v, desc in sorted(rows):
        print(f"{d:<32} {s!s:<20} {v:<10} {desc}")
    serials = [s for _, s, _, _ in rows if s]
    dup = {s for s in serials if serials.count(s) > 1}
    if dup:
        print(f"\n⚠ シリアル番号の重複を検出: {dup}")
        print(
            "  → serial 解決は使えません。ポート名直書き＋物理ポート固定(テープ)で運用"
        )
    elif serials:
        print(
            "\n✓ シリアル番号はユニーク。config に serial を登録すればポート名変動に追従できます"
        )


def resolve_port(cfg_pair, role):
    """Resolve a role's port from its registered serial, else use the configured name."""
    want = cfg_pair.get(f"{role}_serial")
    port = cfg_pair.get(f"{role}_port")
    if not want:
        return port, None
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return port, "pyserial 未導入のため serial 解決をスキップ"
    hits = [p.device for p in lp.comports() if p.serial_number == want]
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        return (
            port,
            f"serial {want} が複数ポートに一致(重複個体)。ポート名 {port} を使用",
        )
    return port, f"serial {want} のデバイスが見つからない。ポート名 {port} を使用"


def spawn(cmd, log_path, dry_run):
    """Start a child with stdout/stderr appended to log_path (or just print it)."""
    shown = " ".join(cmd)
    if dry_run:
        print(f"  $ {shown}\n    → log {log_path}")
        return None, None
    f = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 - closed in main's finally
    stamp = datetime.now()  # noqa: DTZ005 - local wall-clock time for the log header
    f.write(f"\n=== {stamp:%Y-%m-%d %H:%M:%S} $ {shown}\n")
    f.flush()
    return subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT), f


def is_leader_only(port):
    """True when a follower port is the literal 'none'."""
    return str(port).lower() == LEADER_ONLY


def pair_commands(pair, cfg, args, log_dir):
    """Build [(name, cmd, log)] for one pair: teleop and the optional VR bridge."""
    ports = PORTS[pair]
    lp_, warn1 = resolve_port(cfg, "leader")
    fp_, warn2 = cfg.get("follower_port", LEADER_ONLY), None
    host = cfg.get("follower_host")
    hand_port, warn3 = None, None
    two_hands = args.vr == pair and args.vr2
    if two_hands:
        # 2 人目: このペアのフォロワー機は USB で Mac に繋ぎ、手で動かす入力装置になる。
        # メインの teleop はリーダーのみ(フォロワーを動かさない)
        if is_leader_only(fp_):
            print(
                f"[{pair}] ✗ --vr2 には config の follower_port(2 人目の機体)が要ります"
            )
            return []
        hand_port, warn3 = resolve_port(cfg, "follower")
        fp_ = LEADER_ONLY
    elif args.follower == "none":
        fp_ = LEADER_ONLY  # フォロワーを使わない(VR 1 人: 繋がっていなくてよい)
    elif args.follower == "wireless" and host and not is_leader_only(fp_):
        fp_ = str(host)  # udp://… → koch4_teleop.py が RemoteFollower で繋ぐ
        print(f"[{pair}] フォロワー無線 {fp_}（有線に戻す: --follower wired）")
    elif not is_leader_only(fp_):
        fp_, warn2 = resolve_port(cfg, "follower")
    for w in (warn1, warn2, warn3):
        if w:
            print(f"[{pair}] ⚠ {w}")
    if two_hands and (
        not hand_port or (not args.dry_run and not Path(str(hand_port)).exists())
    ):
        print(
            f"[{pair}] ✗ 2 人目(フォロワー機)のポートが存在しません: {hand_port} — --list で確認して config を直してください"
        )
        return []
    if not lp_ or (not args.dry_run and not Path(lp_).exists()):
        print(
            f"[{pair}] ✗ leader ポートが存在しません: {lp_} — --list で確認して config を直してください"
        )
        return []
    if (
        not is_leader_only(fp_)
        and not str(fp_).startswith("udp://")
        and not args.dry_run
        and not Path(fp_).exists()
    ):
        print(
            f"[{pair}] ✗ follower ポートが存在しません: {fp_} — --list で確認して config を直してください"
        )
        return []
    ff = "vwall" if args.vr == pair else args.ff
    if is_leader_only(fp_) and ff in ("gripper", "arm"):
        print(f"[{pair}] ⚠ follower_port=none なので --ff {ff} は使えません → off")
        ff = "off"
    viz = f"{ports['viz']},{ports['vrviz']}" if args.vr == pair else str(ports["viz"])
    cmd = [
        sys.executable,
        str(HERE / "koch4_teleop.py"),
        "--leader-port",
        lp_,
        "--follower-port",
        str(fp_),
        "--leader-id",
        cfg.get("leader_id", f"koch_leader_{pair}"),
        "--follower-id",
        cfg.get("follower_id", f"koch_follower_{pair}"),
        "--config-dir",
        str(args.config_dir),
        "--work-dir",
        str(args.work_dir),
        "--viz-port",
        viz,
        "--ctl-port",
        str(ports["ctl"]),
        "--ff",
        ff,
    ]
    if args.vr == pair:
        cmd += ["--arm-label", pair]
    if args.vr == pair and args.vw:
        cmd += ["--vw"]
    if args.grip_ma is not None:
        cmd += ["--follower-grip-ma", str(args.grip_ma)]
    if args.lerobot_cache:
        cmd += ["--lerobot-cache"]
    if args.csv:
        cmd += ["--csv"]
    if args.extra:
        cmd += args.extra.split()
    plan = [(f"{pair}-teleop", cmd, log_dir / f"dual_{pair}_teleop.log")]
    arms, tele, ctl = [pair], [ports["vrviz"]], [ports["ctl"]]
    if two_hands:
        hand = [
            sys.executable,
            str(HERE / "koch4_teleop.py"),
            "--leader-port",
            str(hand_port),
            "--leader-id",
            cfg.get("follower_id", f"koch_follower_{pair}"),
            "--leader-type",
            "koch_follower",
            "--follower-port",
            LEADER_ONLY,
            "--config-dir",
            str(args.config_dir),
            "--work-dir",
            str(args.work_dir),
            "--viz-port",
            f"{HAND_PORTS['viz']},{HAND_PORTS['vrviz']}",
            "--ctl-port",
            str(HAND_PORTS["ctl"]),
            "--ff",
            "vwall",
            "--arm-label",
            HAND_LABEL,
        ]
        if args.vw:
            hand += ["--vw"]
        if args.lerobot_cache:
            hand += ["--lerobot-cache"]
        if args.csv:
            hand += ["--csv"]
        if args.extra:
            hand += args.extra.split()
        plan.append((f"{pair}-hand", hand, log_dir / f"dual_{pair}_hand.log"))
        arms.append(HAND_LABEL)
        tele.append(HAND_PORTS["vrviz"])
        ctl.append(HAND_PORTS["ctl"])
    if args.vr == pair:
        cmd = [
            sys.executable,
            str(HERE / "koch4_vr_bridge.py"),
            "--arms",
            ",".join(arms),
            "--telemetry",
            ",".join(str(p) for p in tele),
            "--ctl-port",
            ",".join(str(p) for p in ctl),
            "--port",
            str(ports["vr"]),
            "--config-dir",
            str(args.config_dir),
            "--work-dir",
            str(args.work_dir),
        ]
        if args.vr_http:
            cmd += ["--http"]
        plan.append((f"{pair}-vr", cmd, log_dir / f"dual_{pair}_vr.log"))
    return plan


def panel_command(pairs, config, args):
    """One panel for every launched pair (shared sliders, broadcast control)."""
    two_hands = bool(args.vr) and args.vr2  # 2 人目の teleop もパネルに載せる
    hand_viz = [str(HAND_PORTS["viz"])] if two_hands else []
    hand_ctl = [str(HAND_PORTS["ctl"])] if two_hands else []
    hand_lbl = [HAND_LABEL] if two_hands else []
    cmd = [
        sys.executable,
        str(HERE / "koch4_web_panel.py"),
        "--http",
        str(PANEL_HTTP),
        "--telemetry",
        ",".join([str(PORTS[p]["viz"]) for p in pairs] + hand_viz),
        "--ctl-port",
        ",".join([str(PORTS[p]["ctl"]) for p in pairs] + hand_ctl),
        "--labels",
        ",".join(pairs + hand_lbl),
    ]
    cams = [
        c
        for p in pairs
        for c in config["pairs"][p].get("cams", "").split(",")
        if c.strip()
    ]
    if cams:
        cmd += ["--cams", ",".join(dict.fromkeys(cams))]
    if args.no_browser:
        cmd += ["--no-browser"]
    return cmd


def build_parser():
    """Define the command line."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--list", action="store_true", help="接続中ポート+シリアル列挙(読み取りのみ)"
    )
    ap.add_argument("--init", action="store_true", help="config 雛形を作成")
    ap.add_argument(
        "--pair",
        choices=["A", "B", "both"],
        default="both",
        help="起動するペア(既定 both)",
    )
    ap.add_argument(
        "--ff",
        choices=["off", "gripper", "arm", "vwall"],
        default="gripper",
        help="力覚 FB モード(全ペア共通。既定 gripper=握手)",
    )
    ap.add_argument(
        "--vr",
        choices=["A", "B"],
        default=None,
        help="このペアを VR 仮想反力(--ff vwall)にしてブリッジも起動(VR は別枠で運用)",
    )
    ap.add_argument(
        "--vw",
        action="store_true",
        help="VR ペアで握った物体の重さを肩・肘に返す(--vw-cap 小から)",
    )
    ap.add_argument(
        "--vr2",
        action="store_true",
        help="VR ペアのフォロワー機も手で動かす 2 人目の入力装置にする(teleop をもう 1 本・ブリッジは腕 2 本)",
    )
    ap.add_argument(
        "--vr-http", action="store_true", help="ブリッジを TLS なしで(adb reverse 方式)"
    )
    ap.add_argument(
        "--grip-ma",
        type=int,
        default=None,
        help="フォロワー gripper の Goal_Current 上限[mA](過負荷停止対策。例 500)",
    )
    ap.add_argument(
        "--panel",
        action="store_true",
        default=True,
        help="ブラウザパネルも起動(既定on)",
    )
    ap.add_argument("--no-panel", dest="panel", action="store_false")
    ap.add_argument(
        "--no-browser", action="store_true", help="ブラウザ自動オープンを抑止"
    )
    ap.add_argument("--csv", action="store_true", help="全ペアの CSV 記録")
    ap.add_argument(
        "--lerobot-cache", action="store_true", help="較正を lerobot 既定の場所から読む"
    )
    ap.add_argument(
        "--extra", default="", help="koch4_teleop.py へ透過する追加引数(全ペア共通)"
    )
    ap.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    ap.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    ap.add_argument(
        "--dry-run", action="store_true", help="起動せずコマンドを表示(ハード不要)"
    )
    ap.add_argument(
        "--follower",
        choices=["wireless", "wired", "none"],
        default="wireless",
        help="config に follower_host があれば無線(既定)。wired=USB のフォロワーポートを使う(バックアップ)。"
        "none=フォロワーを使わずリーダーだけ(VR 1 人。フォロワーは繋がなくてよい)",
    )
    ap.add_argument(
        "--doctor",
        action="store_true",
        help="準備状況を表示(config・ポートの有無・較正ファイル・VR の部品)。腕には触れない",
    )
    return ap


def log_tail(path, lines=6):
    """Last non-empty lines of a child's log, shown when it exits with an error."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    rows = [r for r in text.replace("\r", "\n").split("\n") if r.strip()]
    return "\n".join(f"    | {r[:220]}" for r in rows[-lines:])


def doctor(args, config_path):
    """Print what is ready for handshake / VR / two players. Reads files only.

    Ports are only tested for existence (the names written in the config); nothing is
    opened and no port is chosen for the user.
    """
    mark = {True: "✓", False: "✗"}
    print(f"準備状況（腕には触れません）  config: {config_path}")
    asset = HERE / "webxr" / "three.module.js"
    twin = args.config_dir / "koch4_twin.json"
    print(f"  {mark[asset.exists()]} VR ページの部品 three.module.js", end="")
    print(
        ""
        if asset.exists()
        else "  → uv run python koch4/webxr/setup_assets.py（要ネット・1 回）"
    )
    print(f"  {mark[twin.exists()]} 分身と物体の設定 {twin.name}")
    if not config_path.exists():
        print(
            f"  ✗ {CONFIG_NAME} がありません → --list でポートを確認し --init で雛形を作って記入"
        )
        return
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    cal = args.config_dir / "calibration"
    ready = {}
    for pair, cfg in config.get("pairs", {}).items():
        lp_, fp_ = cfg.get("leader_port"), cfg.get("follower_port", LEADER_ONLY)
        host = cfg.get("follower_host")
        lid = cfg.get("leader_id", f"koch_leader_{pair}")
        fid = cfg.get("follower_id", f"koch_follower_{pair}")
        l_port = bool(lp_) and Path(str(lp_)).exists()
        f_port = not is_leader_only(fp_) and Path(str(fp_)).exists()
        l_cal = (cal / "koch_leader" / f"{lid}.json").exists()
        f_cal = (cal / "koch_follower" / f"{fid}.json").exists()
        print(f"  ペア {pair}")
        print(
            f"    {mark[l_port]} リーダーのポート {lp_}"
            + ("" if l_port else "（未接続か名前違い）")
        )
        if is_leader_only(fp_):
            print("    － フォロワーのポート none（このペアはリーダーだけ）")
        else:
            print(
                f"    {mark[f_port]} フォロワーのポート {fp_}"
                + ("" if f_port else "（未接続か名前違い）")
            )
        print(
            f"    {'✓' if host else '－'} フォロワー無線の宛先 {host or '未設定（有線で使う）'}"
        )
        print(
            f"    {mark[l_cal]} 較正 koch_leader/{lid}.json   {mark[f_cal]} 較正 koch_follower/{fid}.json"
        )
        ready[pair] = {
            "握手": l_port and l_cal and (bool(host) or (f_port and f_cal)),
            "VR 1 人": l_port and l_cal and asset.exists(),
            "VR 2 人": l_port and l_cal and f_port and f_cal and asset.exists(),
        }
    print("  起動できるもの（この PC から見える範囲）")
    for pair, modes in ready.items():
        row = "  ".join(f"{mark[ok]} {name}" for name, ok in modes.items())
        print(f"    ペア {pair}: {row}")
    print(
        "  ※ 無線の握手は、握手用の PC でフォロワー側（start.sh host）が先に動いていること"
    )


def write_template(config_path):
    """Create the config template unless it already exists."""
    if config_path.exists():
        sys.exit(f"{config_path} は既にあります(上書きしません)")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(CONFIG_TEMPLATE, f, ensure_ascii=False, indent=2)
    print(
        f"雛形を作成: {config_path}\n--list の結果を見てポート/シリアルを埋めてください"
    )


def request_stop(ports):
    """Ask teleops to stop through their control ports (a clean exit on any OS)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for port in ports:
            s.sendto(b'{"stop": true}', ("127.0.0.1", port))


def stop_all(procs, ctl_by_name=None):
    """Stop every child: control-port request and Ctrl+C (teleop drops torque), then terminate.

    The stop request goes only to teleops this launcher started and that are still
    alive (a live child owns its control port), so a dry run, a failed start, or the
    other launcher's pair (handshake on A, VR on B) is never stopped by this one.
    """
    ctl_by_name = ctl_by_name or {}
    request_stop(
        [
            ctl_by_name[name]
            for name, proc, _ in procs
            if name in ctl_by_name and proc.poll() is None
        ]
    )
    for _, proc, _ in procs:
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
            except (
                ValueError,
                OSError,
            ):  # Windows cannot signal a child; the request above stops teleops
                pass
    time.sleep(STOP_GRACE_SEC)
    for _, proc, f in procs:
        if proc.poll() is None:
            proc.terminate()
        f.close()
    if procs:
        print("全プロセス停止")


def main():
    """Entry point."""
    args = build_parser().parse_args()
    config_path = args.config_dir / CONFIG_NAME
    if args.list:
        list_ports()
        return
    if args.init:
        write_template(config_path)
        return
    if args.doctor:
        doctor(args, config_path)
        return
    if not config_path.exists():
        sys.exit(f"{config_path} がありません。まず --init で作成してください")
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    log_dir = args.work_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    pairs = ["A", "B"] if args.pair == "both" else [args.pair]
    if args.vr and args.vr not in pairs:
        sys.exit(f"--vr {args.vr} は起動するペアに含まれていません")
    if args.vr2 and not args.vr:
        sys.exit("--vr2 は --vr A|B と一緒に使います")

    plan = []
    if args.panel:
        plan.append(
            ("panel", panel_command(pairs, config, args), log_dir / "dual_panel.log")
        )
    per_pair = {
        pair: pair_commands(pair, config["pairs"][pair], args, log_dir)
        for pair in pairs
    }
    if not any(per_pair.values()):
        sys.exit(1)
    ctl_by_name = {f"{pair}-teleop": PORTS[pair]["ctl"] for pair in pairs}
    if args.vr:
        ctl_by_name[f"{args.vr}-hand"] = HAND_PORTS["ctl"]
    procs = []
    try:
        for name, cmd, log in plan:
            proc, f = spawn(cmd, log, args.dry_run)
            if proc is not None:
                procs.append((name, proc, f))
        for pair in pairs:
            if not per_pair[pair]:
                continue
            print(f"[{pair}] " + " / ".join(name for name, _, _ in per_pair[pair]))
            for name, cmd, log in per_pair[pair]:
                proc, f = spawn(cmd, log, args.dry_run)
                if proc is not None:
                    procs.append((name, proc, f))
            if not args.dry_run:
                time.sleep(PAIR_SETTLE_SEC)  # ペアBの列挙前にペアAの接続を安定させる
        if args.dry_run:
            print("dry-run: 何も起動していません")
            return
        where = [f"パネル http://127.0.0.1:{PANEL_HTTP}"] if args.panel else []
        if args.vr:
            port = PORTS[args.vr]["vr"]
            where.append(
                f"VR ページ http://localhost:{port}/"
                if args.vr_http
                else f"VR ページ https://<この PC の IP>:{port}/"
            )
        print(
            f"\n全プロセス起動完了({len(procs)}個)。{'  '.join(where)}  ログ: {log_dir}\n終了: Ctrl+C"
        )
        failed = False
        while True:
            time.sleep(2)
            for name, proc, f in procs:
                if proc.poll() is not None:
                    print(
                        f"⚠ {name} が終了しました(code={proc.returncode})。ログ: {log_dir}"
                    )
                    if proc.returncode:  # 理由をその場で見せる(ログの末尾)
                        failed = True
                        print(log_tail(f.name))
            procs = [(n_, p, f) for n_, p, f in procs if p.poll() is None]
            if not any("teleop" in n_ for n_, _, _ in procs):
                print("teleop が全て終了したためランチャを終了します")
                if failed:
                    sys.exit(1)
                return
    except KeyboardInterrupt:
        print("\n停止中...")
    finally:
        stop_all(procs, ctl_by_name)


if __name__ == "__main__":
    main()

"""koch-current — 電流/負荷のリアルタイムグラフ + CSV 記録（単独実行用）.

テレオペを動かさずに、バスから直接 Present_Current（XL330: mA）/ Present_Load（XL430: %）を
読んで描き、CSV に残す。--port2 で 2 台目（リーダー等）を同じ窓に重ね描き。

注意: シリアルポートは排他なので、テレオペと同時には使えない。
      テレオペ中のライブ表示は koch-teleop --plot（koch-plot）を使う。
      トルク OFF のモーターは駆動電流が流れないため電流はほぼ 0 が正常
      (電流/負荷が意味を持つのはトルク ON 時か駆動中)。

使い方:
  koch-current <フォロワーのポート>                       # フォロワーのみ
  koch-current <フォロワーのポート> --port2 <リーダーのポート> # 1 窓で両アーム(実線/破線)
  koch-current <ポート> --torque on --duration 60 --csv noise_on.csv   # トルク ON の保持電流
終了: Ctrl+C または --duration 秒経過。終了時に軸ごとの統計(平均/標準偏差/p-p)を表示。
CSV の既定は work/csv/currents_<日時>.csv。
Risk class: torque off — 起動時と終了時に対象 ID 全てへ Torque_Enable=0 を書く（トルクで支えている腕は落ちる）;
--torque on は代わりに energizes（保持したまま測る）。
"""

import argparse
import csv
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from koch.dxl import (
    ADDR_PRESENT_CURRENT,
    ADDR_TORQUE_ENABLE,
    JOINTS,
    open_port,
    to_signed16,
)
from koch.paths import WORK_DIR


def default_csv_path(now: datetime) -> Path:
    """``work/csv/currents_<timestamp>.csv`` next to the teleop recordings."""
    return WORK_DIR / "csv" / f"currents_{now:%Y%m%d_%H%M%S}.csv"


def col_name(arm, i):
    """CSV column of one motor: ``<tag>_id<n>_<joint>[unit]``."""
    tag = f"{arm['tag']}_" if arm["tag"] else ""
    unit = "%" if i in arm["xl430"] else "mA"
    return f"{tag}id{i}_{JOINTS.get(i, '')}[{unit}]"


def open_arm(tag, port_path, ids, xl430_ids, torque_on):
    """Open one bus, set torque on the listed motors and prepare a bulk reader."""
    from dynamixel_sdk import GroupSyncRead

    port, packet = open_port(port_path)
    for i in ids:
        packet.write1ByteTxRx(port, i, ADDR_TORQUE_ENABLE, 1 if torque_on else 0)
    reader = GroupSyncRead(port, packet, ADDR_PRESENT_CURRENT, 2)
    for i in ids:
        reader.addParam(i)
    return {
        "tag": tag,
        "path": port_path,
        "port": port,
        "packet": packet,
        "reader": reader,
        "ids": ids,
        "xl430": xl430_ids,
        "stats": {i: [] for i in ids},
        "fail": 0,
    }


def build_parser():
    """CLI of ``koch-current`` (the same flags as the v0 tool)."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "port", help="1台目(通常フォロワー)。例: /dev/tty.usbmodem5B141156401"
    )
    ap.add_argument(
        "--port2", default=None, help="2台目(通常リーダー)。指定すると1窓に重ね描き"
    )
    ap.add_argument("--ids", default="1,2,3,4,5,6", help="読むモーターID(両アーム共通)")
    ap.add_argument(
        "--xl430", default="1,2", help="1台目のXL430のID(負荷[%%]扱い)。フォロワー=1,2"
    )
    ap.add_argument(
        "--xl430-2",
        default="",
        help="2台目のXL430のID。リーダーは全軸XL330なので空でよい",
    )
    ap.add_argument(
        "--torque",
        choices=["on", "off"],
        default="off",
        help="on=位置保持しながら測定(実験1-1) / off=手で動かせる(電流はほぼ0が正常)",
    )
    ap.add_argument("--duration", type=float, default=0, help="秒。0=Ctrl+Cまで")
    ap.add_argument("--hz", type=float, default=50, help="サンプリング周波数")
    ap.add_argument("--window", type=float, default=15, help="グラフの表示幅[秒]")
    ap.add_argument("--no-plot", action="store_true", help="グラフなし(CSVのみ)")
    ap.add_argument(
        "--csv", default=None, help="CSV出力先(省略時 work/csv/currents_*.csv)"
    )
    return ap


def main():
    """Sample currents from one or two arms, plot them live and write a CSV."""
    args = build_parser().parse_args()

    ids = [int(x) for x in args.ids.split(",")]
    torque_on = args.torque == "on"
    arms = [
        open_arm(
            "F" if args.port2 else "",
            args.port,
            ids,
            {int(x) for x in args.xl430.split(",") if x},
            torque_on,
        )
    ]
    if args.port2:
        arms.append(
            open_arm(
                "L",
                args.port2,
                ids,
                {int(x) for x in args.xl430_2.split(",") if x},
                torque_on,
            )
        )

    csv_path = (
        Path(args.csv).resolve() if args.csv else default_csv_path(datetime.now())
    )
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    plot = not args.no_plot
    if plot:
        import matplotlib.pyplot as plt

        maxlen = int(args.window * args.hz)
        t_buf = deque(maxlen=maxlen)
        bufs = {}  # (tag, id) -> deque
        lines = {}
        plt.ion()
        fig, (ax_ma, ax_pct) = plt.subplots(2, 1, sharex=True, figsize=(10, 6))
        if fig.canvas.manager is not None:
            fig.canvas.manager.set_window_title("Koch current monitor")
        for arm in arms:
            style = "--" if arm["tag"] == "L" else "-"
            for i in arm["ids"]:
                ax = ax_pct if i in arm["xl430"] else ax_ma
                label = (
                    f"{arm['tag']}:" if arm["tag"] else ""
                ) + f"{i}:{JOINTS.get(i, '?')}"
                (lines[(arm["tag"], i)],) = ax.plot(
                    [], [], style, label=label, linewidth=1.2
                )
                bufs[(arm["tag"], i)] = deque(maxlen=maxlen)
        ax_ma.set_ylabel("XL330 current [mA]")
        ax_ma.grid(True)
        if ax_ma.lines:
            ax_ma.legend(loc="upper left", fontsize=7, ncol=2)
        ax_pct.set_ylabel("XL430 load [%]")
        ax_pct.set_xlabel("time [s]")
        ax_pct.grid(True)
        if ax_pct.lines:
            ax_pct.legend(loc="upper left", fontsize=7)

    t0 = time.perf_counter()
    next_draw = 0.0
    print(
        f"記録開始 → {csv_path}\nCtrl+Cで終了。トルク={args.torque}"
        + (
            f"\n1台目={args.port}(実線) / 2台目={args.port2}(破線)"
            if args.port2
            else ""
        )
    )
    try:
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_sec"] + [col_name(a, i) for a in arms for i in a["ids"]])
            while True:
                t = time.perf_counter() - t0
                if args.duration and t > args.duration:
                    break
                row: list[float | str] = [round(t, 3)]
                got_any = False
                for arm in arms:
                    if arm["reader"].txRxPacket() != 0:
                        arm["fail"] += 1
                        row += [""] * len(arm["ids"])
                        continue
                    got_any = True
                    for i in arm["ids"]:
                        raw = to_signed16(
                            arm["reader"].getData(i, ADDR_PRESENT_CURRENT, 2)
                        )
                        val = raw * 0.1 if i in arm["xl430"] else float(raw)
                        row.append(round(val, 1))
                        arm["stats"][i].append(val)
                        if plot:
                            bufs[(arm["tag"], i)].append(val)
                if not got_any:
                    continue
                w.writerow(row)
                if plot:
                    t_buf.append(t)
                    if t >= next_draw:  # 描画は10Hzに間引く(CSVはhzのまま)
                        for key, ln in lines.items():
                            buf = bufs[key]
                            if buf:
                                ln.set_data(list(t_buf)[-len(buf) :], buf)
                        for ax in (ax_ma, ax_pct):
                            ax.relim()
                            ax.autoscale_view()
                        fig.canvas.draw_idle()
                        fig.canvas.flush_events()
                        next_draw = t + 0.1
                time.sleep(max(0.0, 1.0 / args.hz - (time.perf_counter() - t0 - t)))
    except KeyboardInterrupt:
        pass
    finally:
        for arm in arms:
            for i in arm["ids"]:
                arm["packet"].write1ByteTxRx(arm["port"], i, ADDR_TORQUE_ENABLE, 0)
            arm["port"].closePort()

    for arm in arms:
        head = f"[{arm['tag'] or '1台目'}] {arm['path']}"
        print(f"\n=== 統計(全区間) {head} 通信失敗 {arm['fail']} 回 ===")
        for i in arm["ids"]:
            s = arm["stats"][i]
            if not s:
                continue
            m = sum(s) / len(s)
            sd = (sum((x - m) ** 2 for x in s) / len(s)) ** 0.5
            unit = "%" if i in arm["xl430"] else "mA"
            print(
                f"  id{i} {JOINTS.get(i, ''):13s}: 平均 {m:7.1f} / SD {sd:6.1f} / "
                f"p-p {max(s) - min(s):7.1f} [{unit}] (n={len(s)})"
            )
    print(f"CSV: {csv_path}")


if __name__ == "__main__":
    main()

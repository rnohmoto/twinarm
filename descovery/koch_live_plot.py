"""koch_live_plot.py — koch_teleop_plus.py のUDP配信ビューア v3（上からL・F・L−F・電流）

パネル構成（色 = 関節。全パネルで同じ関節は同じ色）:
  1段目: リーダー指令位置 L [正規化値]
  2段目: フォロワー実位置 F [正規化値]
  3段目: 差分 L−F — 追従誤差/初期ズレがそのまま見える
  4段目: フォロワー電流 — XL330 4軸=左軸[mA]・肩2軸XL430=右軸[%点線]・FF指令=黒破線
         (--no-current で4段目を消して3段にできる)

使い方（通常は koch_teleop_plus.py --plot が自動起動するので手動起動は不要）:
  python koch_live_plot.py --port 8765 --window 20
終了: ウィンドウを閉じる / Ctrl+C / 無データ300秒で自動終了。
"""
import argparse, json, socket, time
from collections import deque

import matplotlib
try:
    matplotlib.use("TkAgg")  # macosxバックエンドはサブプロセス起動で窓が出ないことがあるためTkに固定
except Exception:
    pass
import matplotlib.pyplot as plt

ORDER = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
XL430 = {"shoulder_pan", "shoulder_lift"}  # フォロワーの肩2軸(負荷[%])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--window", type=float, default=15, help="表示幅[秒]")
    ap.add_argument("--no-current", action="store_true", help="電流パネル(4段目)を表示しない")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", args.port))
    except OSError:
        print(f"ポート{args.port}は既に別のビューアが使用中です(そちらの窓をそのまま使えます)")
        return
    sock.setblocking(False)
    print(f"UDP {args.port} で待受中… (koch_teleop_plus.py を起動してください)")

    maxlen = int(args.window * 35)
    t_buf = deque(maxlen=maxlen)
    bufs = {(p, m): deque(maxlen=maxlen) for p in "LFDC" for m in ORDER}
    ff_buf = deque(maxlen=maxlen)

    plt.ion()
    n_ax = 3 if args.no_current else 4
    fig, axes = plt.subplots(n_ax, 1, sharex=True, figsize=(11, 7.5 if n_ax == 3 else 9.5))
    fig.canvas.manager.set_window_title("Koch live plot v3")
    ax_L, ax_F, ax_D = axes[0], axes[1], axes[2]
    ax_C = None if args.no_current else axes[3]
    color = {m: f"C{i}" for i, m in enumerate(ORDER)}
    lines = {}
    for i, m in enumerate(ORDER):
        (lines[("L", m)],) = ax_L.plot([], [], "-", color=color[m], lw=1.3, label=f"{i + 1}:{m}")
        (lines[("F", m)],) = ax_F.plot([], [], "-", color=color[m], lw=1.3)
        (lines[("D", m)],) = ax_D.plot([], [], "-", color=color[m], lw=1.2)
    ff_line = None
    if ax_C is not None:
        ax_C_r = ax_C.twinx()
        for i, m in enumerate(ORDER):
            if m in XL430:
                (lines[("C", m)],) = ax_C_r.plot([], [], ":", color=color[m], lw=1.1,
                                                 label=f"{i + 1}:{m} [%]")
            else:
                (lines[("C", m)],) = ax_C.plot([], [], "-", color=color[m], lw=1.2,
                                               label=f"{i + 1}:{m} [mA]")
        (ff_line,) = ax_C.plot([], [], "k--", lw=1.0, label="FF cmd [mA]")
        ax_C.set_ylabel("XL330 current [mA]")
        ax_C_r.set_ylabel("XL430 load [%]")
        ax_C.legend(loc="upper left", fontsize=6, ncol=3)
        ax_C_r.legend(loc="upper right", fontsize=6)
        ax_C.grid(True)

    ax_L.set_ylabel("Leader [norm]")
    ax_L.legend(loc="upper left", fontsize=6.5, ncol=3)  # 凡例は1段目のみ(色は全段共通)
    ax_F.set_ylabel("Follower [norm]")
    ax_D.set_ylabel("L - F [norm]")
    ax_D.axhline(0, color="gray", lw=0.6)
    axes[-1].set_xlabel("time [s]")
    for ax in (ax_L, ax_F, ax_D):
        ax.grid(True)
    fig.tight_layout()
    plt.show(block=False)  # 起動時に一度だけ前面に出す(以後は前面化しない=フォーカス強奪なし)
    print(f"グラフウィンドウを表示しました(backend={matplotlib.get_backend()})。見当たらない場合はDockを確認")

    last_rx = time.time()
    try:
        while plt.fignum_exists(fig.number):
            got = False
            for _ in range(200):  # 溜まったパケットを吸い切る
                try:
                    data, _ = sock.recvfrom(4096)
                except BlockingIOError:
                    break
                msg = json.loads(data.decode())
                t_buf.append(msg["t"])
                lpos = msg.get("pos", {})
                fpos = msg.get("fpos", lpos)  # 旧版teleop_plus対策(差分0になる)
                cur = msg.get("cur", {})
                for m in ORDER:
                    lv = float(lpos.get(m, 0.0))
                    fv = float(fpos.get(m, 0.0))
                    bufs[("L", m)].append(lv)
                    bufs[("F", m)].append(fv)
                    bufs[("D", m)].append(lv - fv)
                    bufs[("C", m)].append(float(cur.get(m, 0.0)))
                ff_buf.append(msg.get("ff", 0))
                got = True
                last_rx = time.time()
            if got:
                ts = list(t_buf)
                for key, ln in lines.items():
                    buf = bufs[key]
                    if buf:
                        ln.set_data(ts[-len(buf):], buf)
                if ff_line is not None and ff_buf:
                    ff_line.set_data(ts[-len(ff_buf):], ff_buf)
                all_axes = [ax_L, ax_F, ax_D] + ([ax_C, ax_C_r] if ax_C is not None else [])
                for ax in all_axes:
                    ax.relim()
                    ax.autoscale_view()
            fig.canvas.draw_idle()
            # plt.pause は毎回ウィンドウを前面に出す(フォーカス強奪)ため使わない
            fig.canvas.flush_events()
            time.sleep(0.05)
            if time.time() - last_rx > 300:
                print("300秒データが来ないため終了します")
                break
    except KeyboardInterrupt:
        pass
    print("終了")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Quest を Mac に繋いで VR ページを開く（adb reverse 方式・会場の Wi-Fi を使わない）.

会場（有明 GYM-EX）のゲスト Wi-Fi は端末間通信が通らず、AP の干渉も警告されている。
USB ケーブル 1 本で済ませる手順を 1 コマンドにしたもの。Risk class: network only
（ロボットには触れない。ヘッドセットに対しては adb reverse と「URL を開く」だけ）。

前提（1 回だけ・CHECKLIST の U0）:
  1. Meta アカウントで開発者モードを有効化（Meta Horizon アプリ → ヘッドセット → 開発者モード。
     開発者アカウントの検証＝電話番号か支払い方法が要る）
  2. Mac に adb を入れる（`brew install android-platform-tools` か Meta Quest Developer Hub）
  3. 初回接続時にヘッドセット内の「USB デバッグを許可」で「常に許可」

使い方（Mac・descovery で。ブリッジは `--http` で起動しておく。ペア B の既定ポートは 8444）:
  uv run python koch4/koch4_quest_usb.py --check                 # adb と端末の状態だけ見る
  uv run python koch4/koch4_quest_usb.py --port 8444             # reverse を張って Quest Browser で開く
  uv run python koch4/koch4_quest_usb.py --port 8444 --spectator # 観客用 URL（?spectator=1）を開く
  uv run python koch4/koch4_quest_usb.py --port 8444 --mirror    # 加えて scrcpy でヘッドセットの画面を Mac に出す
  uv run python koch4/koch4_quest_usb.py --port 8444 --wifi      # adb を Wi-Fi に切替え、USB の口を充電に空ける

Quest 側で開く URL は http://localhost:<port>/ 。localhost は WebXR の安全なコンテキストなので
証明書は要らない（https + 自己署名の警告も出ない）。

--wifi（給電用）: USB で繋いだ状態で実行すると `adb tcpip 5555` → `adb connect <Quest の IP>` →
Wi-Fi 越しに reverse を張り直す。以後は USB ケーブルを抜いて 45W の充電器かモバイルバッテリーに
差し替えられる。Mac と Quest が同じ自前ルータ（5 GHz）にいることが前提。ヘッドセットを再起動すると
USB の adb に戻るので、もう一度 --wifi を実行する。
"""

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):  # Windows cp932 console: never crash on symbols
    _reconfigure(errors="replace")

ADB_CANDIDATES = [
    "adb",
    "~/Library/Android/sdk/platform-tools/adb",
    "/opt/homebrew/bin/adb",
    "/usr/local/bin/adb",
    "~/Library/Application Support/Meta Quest Developer Hub/platform-tools/adb",
    "~/Library/Application Support/Oculus Developer Hub/platform-tools/adb",
]
QUEST_BROWSER = "com.oculus.browser"
ADB_TCP_PORT = 5555


def find_adb(explicit):
    """Locate adb: --adb, PATH, then the usual Mac install locations."""
    if explicit:
        return explicit
    found = shutil.which("adb")
    if found:
        return found
    for cand in ADB_CANDIDATES[1:]:
        p = Path(cand).expanduser()
        if p.exists():
            return str(p)
    return None


def run(cmd, check=False):
    """Run a command, return (code, stdout+stderr)."""
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    out = (proc.stdout or "") + (proc.stderr or "")
    if check and proc.returncode != 0:
        sys.exit(f"失敗: {' '.join(cmd)}\n{out}")
    return proc.returncode, out


def devices(adb):
    """Parse `adb devices` into [(serial, state)]."""
    _, out = run([adb, "devices"])
    rows = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def explain(rows):
    """Tell the user what the device list means; return the first usable serial."""
    if not rows:
        print(
            "✗ 端末が見えません。USB ケーブル（データ対応品）・ヘッドセットの電源・"
            "開発者モードを確認。ケーブルを挿し直してヘッドセットを被ると許可ダイアログが出ます"
        )
        return None
    usable = None
    for serial, state in rows:
        if state == "device":
            kind = "Wi-Fi" if ":" in serial else "USB"
            print(f"✓ {serial}: 接続済み（adb 可・{kind}）")
            usable = usable or serial
        elif state == "unauthorized":
            print(
                f"⚠ {serial}: 未許可 — ヘッドセットを被って「USB デバッグを許可」→「常に許可」"
            )
        else:
            print(f"⚠ {serial}: {state}")
    return usable


def battery(adb, serial):
    """Print the headset's battery level (read-only)."""
    _, out = run([adb, "-s", serial, "shell", "dumpsys", "battery"])
    level = re.search(r"level:\s*(\d+)", out)
    status = re.search(r"status:\s*(\d+)", out)
    if level:
        charging = "充電中" if status and status.group(1) in ("2", "5") else "放電中"
        print(f"  バッテリー {level.group(1)}%（{charging}）")


def switch_to_wifi(adb, serial):
    """Move adb to TCP/IP so the USB port is free for a charger; returns the new serial."""
    _, out = run(
        [adb, "-s", serial, "shell", "ip", "-f", "inet", "addr", "show", "wlan0"]
    )
    match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
    if not match:
        print(
            "⚠ ヘッドセットの Wi-Fi の IP が取れません（Mac と同じ自前ルータに繋いでから再実行）"
        )
        return None
    ip = match.group(1)
    run([adb, "-s", serial, "tcpip", str(ADB_TCP_PORT)], check=True)
    time.sleep(2.0)
    wifi_serial = f"{ip}:{ADB_TCP_PORT}"
    _, out = run([adb, "connect", wifi_serial])
    if "connected" not in out:
        print(f"⚠ Wi-Fi の adb に繋がりません: {out.strip()}（同じネットワークか確認）")
        return None
    print(
        f"✓ adb over Wi-Fi: {wifi_serial}。USB ケーブルを抜いて充電器（45W）に差し替えられます"
    )
    return wifi_serial


def build_parser():
    """Define the command line."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--port", type=int, default=8444, help="ブリッジの --port（--http）"
    )
    ap.add_argument(
        "--adb", default=None, help="adb の実行ファイル（未指定=PATH と既知の場所）"
    )
    ap.add_argument("--check", action="store_true", help="adb と端末の状態を見るだけ")
    ap.add_argument("--spectator", action="store_true", help="観客用 URL を開く")
    ap.add_argument(
        "--no-open", action="store_true", help="URL を開かない（reverse だけ張る）"
    )
    ap.add_argument(
        "--wifi",
        action="store_true",
        help="adb を Wi-Fi に切替える（USB の口を充電に空ける。Mac と Quest は同じ自前ルータ）",
    )
    ap.add_argument(
        "--mirror",
        action="store_true",
        help="scrcpy でヘッドセットの画面を Mac に出す（brew install scrcpy）",
    )
    ap.add_argument(
        "--scrcpy-args",
        default="--max-fps 30 --no-audio",
        help="scrcpy に渡す引数（Quest Pro の片目だけ切り出すなら --crop を足す）",
    )
    return ap


def main():
    """Entry point."""
    args = build_parser().parse_args()
    adb = find_adb(args.adb)
    if not adb:
        sys.exit(
            "adb が見つかりません。`brew install android-platform-tools` か "
            "Meta Quest Developer Hub を入れて、--adb でパスを渡してください"
        )
    _, ver = run([adb, "version"])
    print(f"adb: {adb}\n{ver.strip().splitlines()[0] if ver.strip() else ''}")
    serial = explain(devices(adb))
    if serial is None:
        return
    battery(adb, serial)
    if args.check:
        return
    if args.wifi and ":" not in serial:
        serial = switch_to_wifi(adb, serial) or serial
    run(
        [adb, "-s", serial, "reverse", f"tcp:{args.port}", f"tcp:{args.port}"],
        check=True,
    )
    _, rev = run([adb, "-s", serial, "reverse", "--list"])
    print(f"✓ adb reverse: {rev.strip() or f'tcp:{args.port}'}")
    url = f"http://localhost:{args.port}/" + ("?spectator=1" if args.spectator else "")
    if not args.no_open:
        code, out = run(
            [
                adb,
                "-s",
                serial,
                "shell",
                "am",
                "start",
                "-a",
                "android.intent.action.VIEW",
                "-d",
                url,
                QUEST_BROWSER,
            ]
        )
        if code != 0 or "Error" in out:
            print(
                f"⚠ ブラウザ起動の返事: {out.strip()}\n  → ヘッドセット内で手で開く: {url}"
            )
        else:
            print(f"✓ Quest Browser で開きました: {url}")
    else:
        print(f"Quest Browser で開く URL: {url}")
    if args.mirror:
        scrcpy = shutil.which("scrcpy")
        if not scrcpy:
            print(
                "⚠ scrcpy が見つかりません（brew install scrcpy）。"
                "代替: Meta Quest Developer Hub のキャスト"
            )
            return
        cmd = [scrcpy, "-s", serial, *args.scrcpy_args.split()]
        print(f"scrcpy 起動: {' '.join(cmd)}（閉じると終了）")
        subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()

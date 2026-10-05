#!/usr/bin/env bash
# Koch 4 本: 握手（ペア A）と VR 2 人（ペア B の 2 本を別々の人が手で動かす）を Mac で起動する
#   ./koch4/mac/koch.sh              使い方を表示
#   ./koch4/mac/koch.sh handshake    握手: ペア A・差分反射式（フリーなら戻らない）・握力上限 500 mA・追従リミッタ 10
#   ./koch4/mac/koch.sh vr-leader    VR 1 人目: リーダー B を手で。ページ http://localhost:8444/
#   ./koch4/mac/koch.sh vr-follower  VR 2 人目: フォロワー B を手で（別の空間）。ページ http://localhost:8443/
#   ./koch4/mac/koch.sh all          上の 3 つを、ターミナルの窓 3 枚で起動する
#   ./koch4/mac/koch.sh status       動いているものを表示
#   ./koch4/mac/koch.sh stop         全部止める（握手を止めるとフォロワー A の力が抜ける。先に支える）
# 同じフォルダの .command をダブルクリックしても同じ。3 つは同時に動かせる。止めるのは各窓で Ctrl+C。
# どれも実機が動く・力が出る。ポートは koch4/config/koch4_config.json のものを使う（推測しない）。
# 後ろに書いた引数はそのまま渡る（例: handshake --dry-run）。
# 環境変数: ASSIST=摩擦アシストの関節（既定 elbow_flex,wrist_flex・空で無効）  ASSIST_CAP=上限 mA（既定 25）
set -e
here="$(cd "$(dirname "$0")" && pwd)"        # koch4/mac
koch4="$(cd "$here/.." && pwd)"
cd "$koch4/.."                               # descovery（uv プロジェクトの場所）

PY=".venv/bin/python3"
F_HTTP=8443                                  # 2 人目のページ（1 人目は 8444）
F_CONFIG="$koch4/config/solo_F"              # 2 人目の分身の設定（1 人目と別のファイル）
ASSIST="${ASSIST-elbow_flex,wrist_flex}"
ASSIST_CAP="${ASSIST_CAP:-25}"

prepare() {
  command -v uv >/dev/null 2>&1 || { echo "uv が見つかりません。https://docs.astral.sh/uv/ の手順で入れてください"; exit 1; }
  uv sync -q
  [ -f koch4/webxr/three.module.js ] || uv run python koch4/webxr/setup_assets.py
  if [ ! -f koch4/config/koch4_config.json ]; then
    echo "koch4/config/koch4_config.json がありません。./koch4/start.sh check の案内に従って作ってください"; exit 1
  fi
}

ports_free() {  # ports_free <名前> <UDP 番号...> -- <TCP 番号...>: 使用中なら理由を出して止まる
  local what="$1"; shift
  "$PY" - "$@" <<'PYEOF' || { echo "⛔ $what は起動しません。同じものが既に動いているか、通し稽古（start.sh rehearse）の最中です。"; echo "   ./koch4/mac/koch.sh status で確認し、止めてからもう一度起動してください。"; exit 1; }
import socket, sys
kind, busy = socket.SOCK_DGRAM, []
for a in sys.argv[1:]:
    if a == "--":
        kind = socket.SOCK_STREAM
        continue
    s = socket.socket(socket.AF_INET, kind)
    try:
        s.bind(("127.0.0.1", int(a)))
    except OSError:
        busy.append(("UDP " if kind == socket.SOCK_DGRAM else "TCP ") + a)
    finally:
        s.close()
if busy:
    print("使用中のポート: " + ", ".join(busy))
    sys.exit(1)
PYEOF
}

has_arg() { local want="$1"; shift; for a in "$@"; do [ "$a" = "$want" ] && return 0; done; return 1; }

open_later() {  # open_later <秒> <URL>
  if command -v open >/dev/null 2>&1; then ( sleep "$1"; open "$2" ) >/dev/null 2>&1 & fi
}

follower_b_port() {
  "$PY" -c "import json;print(json.load(open('koch4/config/koch4_config.json'))['pairs']['B']['follower_port'].replace('/dev/tty.','/dev/cu.'))"
}

usage() { sed -n '2,13p' "$here/koch.sh" | sed 's/^# \{0,1\}//'; }

mode="${1:-help}"
[ $# -gt 0 ] && shift
case "$mode" in
  handshake)
    prepare
    has_arg --dry-run "$@" || ports_free "握手" 8765 8766 -- 8780
    echo "握手（ペア A・差分反射式）。起動時にフォロワー A がリーダー A の姿勢へ 1.5 秒で動きます。パネル http://127.0.0.1:8780"
    exec "$PY" koch4/koch4_dual_launch.py --pair A --ff gripper --grip-ma 500 \
      --extra "--ff-style error --max-rel 10" "$@"
    ;;
  vr-leader)
    prepare
    has_arg --dry-run "$@" || ports_free "VR 1 人目" 8768 8770 -- 8444
    echo "VR 1 人目（リーダー B）。ページ http://localhost:8444/"
    has_arg --dry-run "$@" || open_later 10 "http://localhost:8444/"
    exec "$PY" koch4/koch4_dual_launch.py --pair B --vr B --vw --vr-http --no-panel --follower none \
      --extra "--vw-invert elbow_flex --vw-cap 60" "$@"
    ;;
  vr-follower)
    prepare
    ports_free "VR 2 人目" 8772 8773 -- "$F_HTTP"
    port="$(follower_b_port)"
    assist=()
    [ -n "$ASSIST" ] && assist=(--assist "$ASSIST" --assist-cap "$ASSIST_CAP")
    echo "VR 2 人目（フォロワー B: $port）。ページ http://localhost:${F_HTTP}/  摩擦アシスト: ${ASSIST:-なし}（上限 ${ASSIST_CAP} mA）"
    echo "⚠ 接続直後と終了時にフォロワー B の腕の力が抜けます。腕を手で支えてから Enter"
    read -r _
    mkdir -p "$koch4/work/logs"
    "$PY" koch4/koch4_vr_bridge.py --arms F --telemetry 8773 --ctl-port 8772 --port "$F_HTTP" \
      --config-dir "$F_CONFIG" --work-dir "$koch4/work" --http >> "$koch4/work/logs/solo_F_vr.log" 2>&1 &
    bridge=$!
    trap 'kill "$bridge" 2>/dev/null || true' EXIT
    open_later 10 "http://localhost:${F_HTTP}/"
    # --viz-port に 8771 を足さない（握手 A の監視が使う番号）
    "$PY" koch4/koch4_teleop.py --leader-port "$port" --leader-id koch_follower_B --leader-type koch_follower \
      --follower-port none --config-dir "$koch4/config" --work-dir "$koch4/work" \
      --viz-port 8773 --ctl-port 8772 --ff vwall --arm-label F --vw --vw-cap 60 --vw-pwm-cap 80 \
      ${assist[@]+"${assist[@]}"} "$@"
    ;;
  all)
    command -v osascript >/dev/null 2>&1 || { echo "all は Mac のターミナル用です。3 つを別々の端末で起動してください"; exit 1; }
    for m in handshake vr-leader vr-follower; do
      osascript -e "tell application \"Terminal\" to do script \"'$here/koch.sh' $m\"" >/dev/null
      sleep 1
    done
    osascript -e 'tell application "Terminal" to activate' >/dev/null
    echo "ターミナルの窓を 3 枚開きました。「VR 2 人目」の窓は、フォロワー B を支えてから Enter を押してください"
    ;;
  status)
    pat="koch4_teleop.py|koch4_dual_launch.py|koch4_vr_bridge.py|koch4_web_panel.py"
    if pgrep -f "$pat" >/dev/null; then pgrep -fl "$pat" | cut -c1-200; else echo "何も動いていません"; fi
    ;;
  stop)
    pkill -INT -f "koch4_teleop.py" 2>/dev/null || true
    pkill -INT -f "koch4_dual_launch.py" 2>/dev/null || true
    sleep 5
    pkill -f "koch4_vr_bridge.py" 2>/dev/null || true
    pkill -f "koch4_web_panel.py" 2>/dev/null || true
    pat="koch4_teleop.py|koch4_dual_launch.py|koch4_vr_bridge.py"
    if pgrep -f "$pat" >/dev/null; then echo "まだ動いています:"; pgrep -fl "$pat" | cut -c1-200
    else echo "全部止まりました。使用後は AC アダプタを抜いてください"; fi
    ;;
  *) usage ;;
esac

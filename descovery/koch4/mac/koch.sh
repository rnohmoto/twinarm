#!/usr/bin/env bash
# Koch 4 本: 握手（ペア A）と VR（どちらのペアでも、2 本を別々の人が手で動かす）を Mac で起動する
#   ./koch4/mac/koch.sh              使い方を表示
#   ./koch4/mac/koch.sh handshake    握手: ペア A・差分反射式（フリーなら戻らない）・握力上限 500 mA・追従リミッタ 20
#   ./koch4/mac/koch.sh vr-leader    VR 1 人目: リーダーを手で。ペア B=http://localhost:8444/  ペア A=http://localhost:8445/
#   ./koch4/mac/koch.sh vr-follower  VR 2 人目: フォロワーを手で（別の空間）。ペア B=http://localhost:8443/  ペア A=http://localhost:8446/
#   ./koch4/mac/koch.sh all          握手 A と VR（ペア B の 2 本）を、ターミナルの窓 3 枚で起動する
#   ./koch4/mac/koch.sh status       動いているものを表示
#   ./koch4/mac/koch.sh stop         全部止める（握手を止めるとフォロワー A の力が抜ける。先に支える）
# 同じフォルダの .command をダブルクリックしても同じ。止めるのは各窓で Ctrl+C。
# VR のペアは PAIR=A|B（既定 B）。例: PAIR=A ./koch4/mac/koch.sh vr-leader。ペア A の握手と VR は同時には動かせない（同じ腕）。
# どれも実機が動く・力が出る。ポートは koch4/config/koch4_config.json のものを使う（推測しない）。
# 後ろに書いた引数はそのまま渡る（例: handshake --dry-run）。
# 環境変数: ASSIST=摩擦アシストの関節（既定 elbow_flex,wrist_flex・空で無効）  ASSIST_CAP=上限 mA（既定 25）
#           MAX_REL=握手の追従リミッタ（既定 20。小さいほどフォロワーが遅れる）  KOCH_YES=1 で vr-follower の Enter 待ちを省く
#           VW_INVERT=重さの向きを反転する関節（既定は分身の設定から決める: 向きが「反転」の肩・肘）
set -e
here="$(cd "$(dirname "$0")" && pwd)"        # koch4/mac
koch4="$(cd "$here/.." && pwd)"
cd "$koch4/.."                               # descovery（uv プロジェクトの場所）

PY=".venv/bin/python3"
ASSIST="${ASSIST-elbow_flex,wrist_flex}"
ASSIST_CAP="${ASSIST_CAP:-25}"
MAX_REL="${MAX_REL:-20}"
PAIR="${PAIR:-B}"
# 腕ごとに別のブリッジ（＝別の空間・別のページ）と別の分身設定。番号は握手（8765/8766/8780）と重ならない
case "$PAIR" in
  B) L_LABEL=B;  L_HTTP=8444; L_VIZ=8770; L_CTL=8768; L_CFG="$koch4/config/solo_B"
     F_LABEL=F;  F_HTTP=8443; F_VIZ=8773; F_CTL=8772; F_CFG="$koch4/config/solo_F" ;;
  A) L_LABEL=A;  L_HTTP=8445; L_VIZ=8775; L_CTL=8774; L_CFG="$koch4/config/solo_A"
     F_LABEL=FA; F_HTTP=8446; F_VIZ=8777; F_CTL=8776; F_CFG="$koch4/config/solo_FA" ;;
  *) echo "PAIR は A か B"; exit 1 ;;
esac

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
    if kind == socket.SOCK_STREAM:  # 止めた直後の TIME_WAIT を「使用中」と数えない
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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

pair_port() {  # pair_port leader|follower: 設定ファイルのポート
  "$PY" -c "import json,sys;print(json.load(open('koch4/config/koch4_config.json'))['pairs']['$PAIR'][sys.argv[1]+'_port'].replace('/dev/tty.','/dev/cu.'))" "$1"
}

vw_invert() {  # vw_invert <設定フォルダ> <腕の名前>: 分身の向きが「反転」の肩・肘は、重さの電流も反転する
  if [ -n "${VW_INVERT+x}" ]; then echo "$VW_INVERT"; return; fi
  "$PY" - "$1/koch4_twin.json" "$2" <<'PYEOF'
import json, sys
try:
    joints = json.load(open(sys.argv[1], encoding="utf-8"))["arms"][sys.argv[2]]["joints"]
    print(",".join(j for j in ("shoulder_lift", "elbow_flex") if int(joints[j]["sign"]) < 0))
except (OSError, KeyError, ValueError):
    print("")
PYEOF
}

run_hand() {  # run_hand <名前> <機種> <腕の役> <ブリッジ HTTP> <テレメトリ> <制御> <設定フォルダ> [teleop の追加引数...]
  local label="$1" model="$2" role="$3" http="$4" viz="$5" ctl="$6" cfg="$7"; shift 7
  local port inv bridge
  port="$(pair_port "$role")"
  inv="$(vw_invert "$cfg" "$label")"
  mkdir -p "$cfg" "$koch4/work/logs"
  echo "腕 $label（$port）。ページ http://localhost:${http}/  重さの反転: ${inv:-なし}"
  "$PY" koch4/koch4_vr_bridge.py --arms "$label" --models "$model" --telemetry "$viz" --ctl-port "$ctl" --port "$http" \
    --config-dir "$cfg" --work-dir "$koch4/work" --http >> "$koch4/work/logs/solo_${label}_vr.log" 2>&1 &
  bridge=$!
  trap 'kill "$bridge" 2>/dev/null || true' EXIT INT TERM HUP   # 窓を閉じても Ctrl+C でもブリッジを残さない
  open_later 10 "http://localhost:${http}/"
  "$PY" koch4/koch4_teleop.py --leader-port "$port" --leader-id "koch_${role}_${PAIR}" --leader-type "$model" \
    --follower-port none --config-dir "$koch4/config" --work-dir "$koch4/work" \
    --viz-port "$viz" --ctl-port "$ctl" --ff vwall --arm-label "$label" --vw --vw-cap 60 --vw-invert "$inv" "$@" || true
  kill "$bridge" 2>/dev/null || true
}

usage() { sed -n '2,16p' "$here/koch.sh" | sed 's/^# \{0,1\}//'; }

mode="${1:-help}"
[ $# -gt 0 ] && shift
case "$mode" in
  handshake)
    prepare
    has_arg --dry-run "$@" || ports_free "握手" 8765 8766 -- 8780
    echo "握手（ペア A・差分反射式）。起動時にフォロワー A がリーダー A の姿勢へ 1.5 秒で動きます。パネル http://127.0.0.1:8780"
    exec "$PY" koch4/koch4_dual_launch.py --pair A --ff gripper --grip-ma 500 \
      --extra "--ff-style error --max-rel $MAX_REL" "$@"
    ;;
  vr-leader)
    prepare
    ports_free "VR 1 人目（ペア $PAIR）" "$L_CTL" "$L_VIZ" -- "$L_HTTP"
    echo "VR 1 人目（リーダー $PAIR）"
    run_hand "$L_LABEL" koch_leader leader "$L_HTTP" "$L_VIZ" "$L_CTL" "$L_CFG" "$@"
    ;;
  vr-follower)
    prepare
    ports_free "VR 2 人目（ペア $PAIR）" "$F_CTL" "$F_VIZ" -- "$F_HTTP"
    assist=()
    [ -n "$ASSIST" ] && assist=(--assist "$ASSIST" --assist-cap "$ASSIST_CAP")
    echo "VR 2 人目（フォロワー $PAIR）  摩擦アシスト: ${ASSIST:-なし}（上限 ${ASSIST_CAP} mA）"
    if [ "${KOCH_YES:-0}" = "1" ]; then
      echo "⚠ 接続直後と終了時にフォロワーの腕の力が抜けます（KOCH_YES=1: 確認なしで起動）"
    else
      echo "⚠ 接続直後と終了時にフォロワーの腕の力が抜けます。腕を手で支えてから Enter"
      read -r _
    fi
    run_hand "$F_LABEL" koch_follower follower "$F_HTTP" "$F_VIZ" "$F_CTL" "$F_CFG" \
      --vw-pwm-cap 80 ${assist[@]+"${assist[@]}"} "$@"
    ;;
  all)
    command -v osascript >/dev/null 2>&1 || { echo "all は Mac のターミナル用です。3 つを別々の端末で起動してください"; exit 1; }
    for m in handshake vr-leader vr-follower; do
      osascript -e "tell application \"Terminal\" to do script \"PAIR=B '$here/koch.sh' $m\"" >/dev/null
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

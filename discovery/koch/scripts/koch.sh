#!/usr/bin/env bash
# Koch 4 本: 握手（ペア A）と VR（どちらのペアでも、2 本を別々の人が手で動かす）を Mac で起動する。これが起動の正本。
#   ./scripts/koch.sh              使い方を表示
#   ./scripts/koch.sh handshake    握手: ペア A・差分反射式（フリーなら戻らない）・握力上限 500 mA・追従リミッタ 20・握り返し 差分ゲイン 15／上限 500 mA（10/19 仮設定）
#                                  役割入替が既定（10/25: 操作者はフォロワー機 A を持ち、リーダー機 A が追従して来場者側。SWAP=0 で従来の向き）
#   ./scripts/koch.sh vr-leader    VR 1 人目: リーダーを手で。ペア B=http://localhost:8444/  ペア A=http://localhost:8445/
#   ./scripts/koch.sh vr-follower  VR 2 人目: フォロワーを手で（別の空間）。ペア B=http://localhost:8443/  ペア A=http://localhost:8446/
#   ./scripts/koch.sh quest        Quest を繋ぎ直す: 動いているブリッジの口に adb reverse を張り、ゴーグルのブラウザでページを開く
#   ./scripts/koch.sh all          握手 A と VR（ペア B の 2 本）を、ターミナルの窓 3 枚で起動する（Mac 1 台で全部やるとき）
#   ./scripts/koch.sh status       動いているもの・Quest の接続・reverse を表示
#   ./scripts/koch.sh stop         全部止める（握手を止めると追従していた腕の力が抜ける。入替ではリーダー機 A。先に支える）
# 同じフォルダの .command をダブルクリックしても同じ。止めるのは各窓で Ctrl+C。
# VR のペアは PAIR=A|B（既定 B）。例: PAIR=A ./scripts/koch.sh vr-leader。ペア A の握手と VR は同時には動かせない（同じ腕）。
# フェスの形: MacBook Pro 1 = vr-leader（リーダー B＋Quest Pro #1）、MacBook Pro 2 = vr-follower（フォロワー B＋Quest Pro #2）。Mac 同士の通信は無い。
# vr-leader / vr-follower がやること: uv sync → 設定が無ければ fest 設定（4 本のシリアル入り）から作る → ポートをシリアルで解決 →
#   ブリッジ＋teleop を起動 → Mac のブラウザに操作ページ（観客モード＋文字）→ Quest の許可を待って reverse を張りページを開く →
#   セッション中は USB の再列挙を見張って reverse を張り直す（ログ work/logs/quest_<腕>.log）
# どれも実機が動く・力が出る。ポートは config/config.json のものを使う（シリアル番号で現在の名前に解決。推測しない）。
# 後ろに書いた引数はそのまま渡る（例: handshake --dry-run）。
# 環境変数: ASSIST=摩擦アシストの関節（既定 elbow_flex,wrist_flex・空で無効）  ASSIST_CAP=上限 mA（既定 25）
#           MAX_REL=握手の追従リミッタ（既定 20。小さいほどフォロワーが遅れる）  KOCH_YES=1 で vr-follower の Enter 待ちを省く
#           FF_KE=握手の握り返しの差分ゲイン mA/unit（既定 15＝10/19 仮設定。硬さ。上げても発熱は増えない）
#           FF_CAP=握り返しの電流上限 mA（既定 500・最大 900。握っている間の最大電流＝発熱。TEST 5 の 30 分で温度を見てから上げる）
#           SWAP=1 で握手の役割入替（既定）・0 で従来  FF_SIGN=握り返しの向き（入替で握ると引き込まれるなら -1）  GRIP_MA=追従する腕の握力上限 mA（既定 500）
#           VW_INVERT=重さの向きを反転する関節（既定は分身の設定から決める: 向きが「反転」の肩・肘）
#           QUEST=0 で Quest の接続を扉せず Mac のブラウザだけ（ゴーグル無しの確認）  QUEST_WAIT=許可を待つ秒数（既定 900）
set -e
here="$(cd "$(dirname "$0")" && pwd)"        # scripts/
root="$(cd "$here/.." && pwd)"               # discovery/koch（uv プロジェクトの場所）
cd "$root"

PY="$root/.venv/bin/python3"
ASSIST="${ASSIST-elbow_flex,wrist_flex}"
ASSIST_CAP="${ASSIST_CAP:-25}"
MAX_REL="${MAX_REL:-20}"
FF_KE="${FF_KE:-15}"                         # 握り返しの差分ゲイン（10/19 仮設定。launcher の素の既定は 9）
FF_CAP="${FF_CAP:-500}"                      # 握り返しの電流上限 mA（同上。素の既定は 450）
SWAP="${SWAP:-1}"                            # 1=役割入替（10/25: 操作者はフォロワー機を持ち、リーダー機が追従）0=従来
FF_SIGN="${FF_SIGN:-1}"                      # 握り返しの向き。入替で逆向き（握ると引き込まれる）なら -1
GRIP_MA="${GRIP_MA:-500}"                    # 追従する腕の gripper 電流上限 mA（入替では M077: 同じ mA で力は約 1/2.4）
PAIR="${PAIR:-B}"
QUEST="${QUEST:-1}"
QUEST_WAIT="${QUEST_WAIT:-900}"
QUEST_DELAY_SEC=8                            # ブリッジが聞き始めるのを待ってからゴーグルでページを開く
FEST_CONFIG="$root/config/config.fest.json"   # フェスの 4 本（A/B のリーダー・フォロワー）のシリアル入り。初回はこれから作る
# 腕ごとに別のブリッジ（＝別の空間・別のページ）と別の分身設定。番号は握手（8765/8766/8780）と重ならない
case "$PAIR" in
  B) L_LABEL=B;  L_HTTP=8444; L_VIZ=8770; L_CTL=8768; L_CFG="$root/config/solo_B"
     F_LABEL=F;  F_HTTP=8443; F_VIZ=8773; F_CTL=8772; F_CFG="$root/config/solo_F" ;;
  A) L_LABEL=A;  L_HTTP=8445; L_VIZ=8775; L_CTL=8774; L_CFG="$root/config/solo_A"
     F_LABEL=FA; F_HTTP=8446; F_VIZ=8777; F_CTL=8776; F_CFG="$root/config/solo_FA" ;;
  *) echo "PAIR は A か B"; exit 1 ;;
esac
VR_HTTP_PORTS="8444 8443 8445 8446"

prepare() {  # prepare [vr]: uv・VR ページの部品・設定ファイル（無ければ fest 設定から）・adb（VR のとき）
  command -v uv >/dev/null 2>&1 || { echo "uv が見つかりません。https://docs.astral.sh/uv/ の手順で入れてください"; exit 1; }
  uv sync -q
  [ -f "$root/src/webxr/three.module.js" ] || uv run python "$root/src/webxr/setup_assets.py"
  if [ ! -f "$root/config/config.json" ]; then
    if [ -f "$FEST_CONFIG" ]; then
      cp "$FEST_CONFIG" "$root/config/config.json"
      echo "config/config.json を fest 設定から作りました（4 本のシリアル番号で現在のポートに解決します）"
    else
      echo "config/config.json がありません。./scripts/start.sh check の案内に従って作ってください"; exit 1
    fi
  fi
  if [ "${1:-}" = "vr" ] && [ "$QUEST" = "1" ] && ! command -v adb >/dev/null 2>&1 && [ ! -x /opt/homebrew/bin/adb ]; then
    echo "⚠ adb が見つかりません（brew install --cask android-platform-tools）。Quest は繋がず、Mac のブラウザだけで続けます"
    QUEST=0
  fi
}

ports_free() {  # ports_free <名前> <UDP 番号...> -- <TCP 番号...>: 使用中なら理由を出して止まる
  local what="$1"; shift
  "$PY" - "$@" <<'PYEOF' || { echo "⛔ $what は起動しません。同じものが既に動いているか、通し稽古（scripts/start.sh rehearse）の最中です。"; echo "   ./scripts/koch.sh status で確認し、止めてからもう一度起動してください。"; exit 1; }
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

pair_port() {  # pair_port leader|follower: 設定ファイルの <役>_serial を今のポート名に解決（無ければ <役>_port を使う）。見つからなければ止まる
  "$PY" - "$PAIR" "$1" "$root/config/config.json" <<'PYEOF'
import json, sys
from pathlib import Path
pair, role = sys.argv[1], sys.argv[2]
cfg = json.load(open(sys.argv[3]))["pairs"][pair]
want, port = cfg.get(f"{role}_serial"), str(cfg.get(f"{role}_port") or "")
found = None
if want:
    try:
        from serial.tools import list_ports
        found = next((p.device for p in list_ports.comports() if p.serial_number == want), None)
    except ImportError:
        found = None
port = (found or port).replace("/dev/tty.", "/dev/cu.")
if not port or not Path(port).exists():
    print(f"✗ ペア {pair} の {role} が見つかりません（serial={want} port={port}）。uv run koch-launch --list で確認", file=sys.stderr)
    sys.exit(1)
print(port)
PYEOF
}

vw_invert() {  # vw_invert <設定フォルダ> <腕の名前>: 分身の向きが「反転」の肩・肘は、重さの電流も反転する
  if [ -n "${VW_INVERT+x}" ]; then echo "$VW_INVERT"; return; fi
  "$PY" - "$1/twin.json" "$2" <<'PYEOF'
import json, sys
try:
    joints = json.load(open(sys.argv[1], encoding="utf-8"))["arms"][sys.argv[2]]["joints"]
    print(",".join(j for j in ("shoulder_lift", "elbow_flex") if int(joints[j]["sign"]) < 0))
except (OSError, KeyError, ValueError):
    print("")
PYEOF
}

listening_vr_ports() {  # 動いているブリッジの HTTP 口（8444/8443/8445/8446 のうち応答するもの）
  "$PY" - $VR_HTTP_PORTS <<'PYEOF'
import socket, sys
for p in sys.argv[1:]:
    s = socket.socket(); s.settimeout(0.3)
    try:
        s.connect(("127.0.0.1", int(p))); print(p)
    except OSError:
        pass
    finally:
        s.close()
PYEOF
}

quest_connect() {  # quest_connect <HTTP ポート> <ログ>: 許可を待って reverse を張り、ゴーグルでページを開き、以後は見張り続ける（バックグラウンド）
  local http="$1" log="$2"
  ( sleep "$QUEST_DELAY_SEC"; exec "$PY" -m koch.quest_usb --port "$http" --wait "$QUEST_WAIT" --watch --devtools ) >> "$log" 2>&1 &
  echo $!
}

run_hand() {  # run_hand <名前> <機種> <腕の役> <ブリッジ HTTP> <テレメトリ> <制御> <設定フォルダ> [teleop の追加引数...]
  local label="$1" model="$2" role="$3" http="$4" viz="$5" ctl="$6" cfg="$7"; shift 7
  local port inv bridge quest=""
  port="$(pair_port "$role")"
  inv="$(vw_invert "$cfg" "$label")"
  mkdir -p "$cfg" "$root/work/logs"
  echo "腕 $label（$port）。Mac の操作ページ http://localhost:${http}/?spectator=1&info=1  ゴーグルのページ http://localhost:${http}/  重さの反転: ${inv:-なし}"
  "$PY" -m koch.bridge --arms "$label" --models "$model" --telemetry "$viz" --ctl-port "$ctl" --port "$http" \
    --config-dir "$cfg" --work-dir "$root/work" --http >> "$root/work/logs/solo_${label}_vr.log" 2>&1 &
  bridge=$!
  if [ "$QUEST" = "1" ]; then
    quest="$(quest_connect "$http" "$root/work/logs/quest_${label}.log")"
    echo "Quest: 許可を待って http://localhost:${http}/ を開きます（USB が切れても自動で張り直す。記録 work/logs/quest_${label}.log）"
  fi
  trap 'kill "$bridge" $quest 2>/dev/null || true' EXIT INT TERM HUP   # 窓を閉じても Ctrl+C でもブリッジと見張りを残さない
  open_later 10 "http://localhost:${http}/?spectator=1&info=1"      # Mac 側は観客モード＋文字（接触はゴーグルのページだけが送る）
  "$PY" -m koch.teleop --leader-port "$port" --leader-id "koch_${role}_${PAIR}" --leader-type "$model" \
    --follower-port none --config-dir "$root/config" --work-dir "$root/work" \
    --viz-port "$viz" --ctl-port "$ctl" --ff vwall --arm-label "$label" --vw --vw-cap 60 --vw-invert "$inv" "$@" || true
  kill "$bridge" $quest 2>/dev/null || true
}

usage() { sed -n '2,26p' "$here/koch.sh" | sed 's/^# \{0,1\}//'; }

mode="${1:-help}"
[ $# -gt 0 ] && shift
case "$mode" in
  handshake)
    prepare
    has_arg --dry-run "$@" || ports_free "握手" 8765 8766 -- 8780
    swap=()
    if [ "$SWAP" = "1" ]; then
      swap=(--swap-roles)
      echo "握手（ペア A・差分反射式・役割入替）: 操作者が持つ腕=フォロワー機 A（XL430/M288: 同じ mA で約 2.4 倍の握り返し）、来場者側=リーダー機 A（M077）が追従。パネル http://127.0.0.1:8780"
      echo "  起動時にリーダー機 A がフォロワー機 A の姿勢へ 1.5 秒で動きます。従来の向きに戻す: SWAP=0 ./scripts/koch.sh handshake（コードごと戻すなら git switch main）"
      if ! has_arg --dry-run "$@"; then
        if [ "${KOCH_YES:-0}" = "1" ]; then
          echo "⚠ 接続直後と終了時にフォロワー機 A の腕の力が抜けます（KOCH_YES=1: 確認なしで起動）"
        else
          echo "⚠ 接続直後と終了時にフォロワー機 A の腕の力が抜けます。腕を手で支えてから Enter"
          read -r _
        fi
      fi
    else
      echo "握手（ペア A・差分反射式）。起動時にフォロワー A がリーダー A の姿勢へ 1.5 秒で動きます。パネル http://127.0.0.1:8780"
    fi
    echo "握り返し: 差分ゲイン ${FF_KE}・電流上限 ${FF_CAP} mA・向き ${FF_SIGN}・追従する腕の握力上限 ${GRIP_MA} mA（FF_KE／FF_CAP／FF_SIGN／GRIP_MA で変更。ゲインと上限はパネルのスライダでも動かせる）"
    exec "$PY" -m koch.launch --pair A --ff gripper --grip-ma "$GRIP_MA" ${swap[@]+"${swap[@]}"} \
      --extra "--ff-style error --max-rel $MAX_REL --ff-ke $FF_KE --ff-cap $FF_CAP --ff-sign $FF_SIGN" "$@"
    ;;
  vr-leader)
    prepare vr
    ports_free "VR 1 人目（ペア $PAIR）" "$L_CTL" "$L_VIZ" -- "$L_HTTP"
    echo "VR 1 人目（リーダー $PAIR）"
    run_hand "$L_LABEL" koch_leader leader "$L_HTTP" "$L_VIZ" "$L_CTL" "$L_CFG" "$@"
    ;;
  vr-follower)
    prepare vr
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
  quest)
    prepare vr
    [ "$QUEST" = "1" ] || exit 1
    ports="$(listening_vr_ports)"
    if [ -z "$ports" ]; then echo "動いているブリッジがありません（vr-leader か vr-follower を先に）"; exit 1; fi
    first=1
    for p in $ports; do
      if [ "$first" = 1 ]; then first=0; "$PY" -m koch.quest_usb --port "$p" --wait "${QUEST_WAIT}" --devtools "$@"
      else "$PY" -m koch.quest_usb --port "$p" --no-open; fi
    done
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
    pat="koch[.-](teleop|launch|bridge|panel|quest)"
    if pgrep -f "$pat" >/dev/null; then pgrep -fl "$pat" | cut -c1-200; else echo "何も動いていません"; fi
    if command -v adb >/dev/null 2>&1; then
      echo "Quest: $(adb get-state 2>/dev/null || echo 未接続)   reverse: $(adb reverse --list 2>/dev/null | tr '\n' ' ')"
    fi
    ;;
  stop)
    pkill -INT -f "koch[.-]teleop" 2>/dev/null || true
    pkill -INT -f "koch[.-]launch" 2>/dev/null || true
    sleep 5
    pkill -f "koch[.-]bridge" 2>/dev/null || true
    pkill -f "koch[.-]panel" 2>/dev/null || true
    pkill -f "koch[.-]quest" 2>/dev/null || true
    pat="koch[.-](teleop|launch|bridge|quest)"
    if pgrep -f "$pat" >/dev/null; then echo "まだ動いています:"; pgrep -fl "$pat" | cut -c1-200
    else echo "全部止まりました。使用後は AC アダプタを抜いてください"; fi
    ;;
  *) usage ;;
esac

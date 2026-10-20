#!/usr/bin/env bash
# Koch 4 本（握手・VR）起動スクリプト（Mac）
#   ./start.sh                  使い方を表示
#   ./start.sh check            腕に触れない確認: 制御則の自己診断・準備状況・ポート一覧・Quest の adb（VR ページの部品も取得する）
#   ./start.sh handshake [...]  握手: 握り返しあり・パネル http://127.0.0.1:8780。既定は 2 ペア。1 ペアだけなら handshake --pair A
#   ./start.sh host A|B <port>  握手用の 2 台目の PC で: フォロワーを無線で受ける側（<port> はフォロワーのシリアルポート）
#   ./start.sh vr [...]         VR 1 人: リーダーだけで仮想物体（重さあり）＋ Quest を USB で開く（フォロワーは繋がなくてよい）
#   ./start.sh vr2 [...]        VR 2 人: 上に加えて、同じペアのフォロワー機も手で握る入力装置に
#   ./start.sh sim              実機なしで VR ページを確認（Mac のブラウザが開く）
#   ./start.sh rehearse [名前]  腕なしの通し稽古: 仮想のサーボで握手・VR・2 人・無線を起動から終了まで（--list で一覧）
#   ./start.sh quest            Quest を繋ぎ直したとき: adb reverse を張り直してページを開く
#   ./start.sh wifi             給電用: adb を Wi-Fi に切替える（USB の口を充電器に空ける）
#   ./start.sh manual           マニュアル（HTML）を開く
# [...] はランチャへそのまま渡す追加の引数（例: --pair A / --follower wired / --dry-run / --csv）
# 握手（ペア A）と VR（ペア B）は、端末を 2 つ開けば同時に動かせる: handshake --pair A と vr
# 環境変数: VR_PAIR=A|B（既定 B）  GRIP_MA=フォロワー握力の電流上限 mA（既定 500）  VR_MIRROR=1 で VR 1 人でもフォロワーを動かす
# 握手・VR は実機が動く。ポートは config/koch4_config.json に書いたものを使う（推測しない）。
set -e
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/.."   # descovery（uv プロジェクトの場所）

VR_PAIR="${VR_PAIR:-B}"
GRIP_MA="${GRIP_MA:-500}"
if [ "$VR_PAIR" = "A" ]; then VR_PORT=8443; else VR_PORT=8444; fi

open_url() {
  if command -v open >/dev/null 2>&1; then open "$1"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1"
  else echo "ブラウザで開く: $1"
  fi
}

need_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv が見つかりません。https://docs.astral.sh/uv/ の手順でインストールしてから再実行してください。"
    exit 1
  fi
  uv sync -q
}

need_assets() {
  [ -f koch4/webxr/three.module.js ] || uv run python koch4/webxr/setup_assets.py
}

need_config() {
  if [ ! -f koch4/config/koch4_config.json ]; then
    echo "koch4/config/koch4_config.json がありません。"
    echo "  uv run python koch4/koch4_dual_launch.py --list   でポートとシリアルを確認し、"
    echo "  uv run python koch4/koch4_dual_launch.py --init   で雛形を作って記入してください。"
    exit 1
  fi
}

has_arg() {  # has_arg --dry-run "$@"
  local want="$1"; shift
  for a in "$@"; do [ "$a" = "$want" ] && return 0; done
  return 1
}

usage() { sed -n '2,17p' "$here/start.sh" | sed 's/^# \{0,1\}//'; }

mode="${1:-help}"
[ $# -gt 0 ] && shift
case "$mode" in
  check)
    need_uv
    uv run python koch4/koch4_teleop.py --selftest
    if [ ! -f koch4/webxr/three.module.js ]; then
      uv run python koch4/webxr/setup_assets.py || echo "⚠ VR ページの部品を取得できません。ネットに繋がるときにもう一度 check を実行してください"
    fi
    uv run python koch4/koch4_dual_launch.py --doctor || true
    uv run python koch4/koch4_dual_launch.py --list || true
    uv run python koch4/koch4_quest_usb.py --check || true
    ;;
  handshake)
    need_uv; need_config
    exec uv run python koch4/koch4_dual_launch.py --ff gripper --grip-ma "$GRIP_MA" "$@"
    ;;
  host)
    pair="${1:-}"; port="${2:-}"
    case "$pair" in
      A) listen=9101 ;;
      B) listen=9102 ;;
      *) echo "使い方: ./start.sh host A|B <フォロワーのシリアルポート>"; exit 1 ;;
    esac
    if [ -z "$port" ]; then
      echo "フォロワーのシリアルポートを渡してください（推測しません）。"
      echo "  uv run python koch4/koch4_dual_launch.py --list   で確認できます。"
      exit 1
    fi
    need_uv
    ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
    if [ -n "$ip" ]; then
      echo "操作する側の koch4_config.json に書く宛先: \"follower_host\": \"udp://$ip:$listen\""
    fi
    exec uv run python koch4/koch4_follower_host.py --port "$port" --id "koch_follower_$pair" --listen "$listen" --grip-ma "$GRIP_MA"
    ;;
  vr|vr2)
    need_uv; need_config; need_assets
    extra=()
    if [ "$mode" = "vr2" ]; then
      extra+=(--vr2)
      if ! has_arg --dry-run "$@"; then
        echo "⚠ 2 人目のフォロワー機は接続直後に腕のトルクが抜けます。腕を手で支えてから Enter"
        read -r _
      fi
    elif [ "${VR_MIRROR:-0}" != "1" ]; then
      extra+=(--follower none)   # 1 人のときはリーダーだけ（フォロワーは繋がなくてよい）
    fi
    if ! has_arg --dry-run "$@"; then
      # ブリッジが立ち上がった頃に Quest 側でページを開く（USB・adb reverse）
      ( sleep 12; uv run python koch4/koch4_quest_usb.py --port "$VR_PORT" || true ) &
    fi
    exec uv run python koch4/koch4_dual_launch.py --pair "$VR_PAIR" --vr "$VR_PAIR" --vw --vr-http --no-panel ${extra[@]+"${extra[@]}"} "$@"
    ;;
  sim)
    need_uv; need_assets
    ( sleep 3; open_url "http://localhost:${VR_PORT}/" ) &
    exec uv run python koch4/koch4_vr_bridge.py --sim --arms B,F --http --port "$VR_PORT"
    ;;
  rehearse)
    need_uv; need_assets
    exec uv run python koch4/simbus/rehearse.py "$@"
    ;;
  quest)
    need_uv
    exec uv run python koch4/koch4_quest_usb.py --port "$VR_PORT"
    ;;
  wifi)
    need_uv
    exec uv run python koch4/koch4_quest_usb.py --port "$VR_PORT" --wifi
    ;;
  manual)
    open_url "$here/manual/index.html"
    ;;
  *) usage ;;
esac

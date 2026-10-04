#!/usr/bin/env bash
# Koch 4 本（握手 2 ペア・VR）起動スクリプト（Mac）
#   ./start.sh            使い方を表示
#   ./start.sh check      ハードに触れない確認（制御則の自己診断・ポート一覧・Quest の adb）
#   ./start.sh handshake  握手: 2 ペア同時（握り返しあり・パネル http://127.0.0.1:8780）
#   ./start.sh vr         VR 1 人: ペア B のリーダーで仮想物体（重さあり）＋ Quest を USB で開く
#   ./start.sh vr2        VR 2 人: 上に加えて、ペア B のフォロワー機も手で握る入力装置に
#   ./start.sh sim        実機なしで VR ページを確認（Mac のブラウザが開く）
#   ./start.sh quest      Quest を繋ぎ直したとき: adb reverse を張り直してページを開く
#   ./start.sh wifi       給電用: adb を Wi-Fi に切替える（USB の口を充電器に空ける）
#   ./start.sh manual     マニュアル（HTML）を開く
# 環境変数: VR_PAIR=A|B（既定 B）  GRIP_MA=フォロワー握力の電流上限 mA（既定 500）
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

usage() { sed -n '2,13p' "$here/start.sh" | sed 's/^# \{0,1\}//'; }

mode="${1:-help}"
case "$mode" in
  check)
    need_uv
    uv run python koch4/koch4_teleop.py --selftest
    uv run python koch4/koch4_dual_launch.py --list || true
    uv run python koch4/koch4_quest_usb.py --check || true
    ;;
  handshake)
    need_uv; need_config
    exec uv run python koch4/koch4_dual_launch.py --ff gripper --grip-ma "$GRIP_MA"
    ;;
  vr|vr2)
    need_uv; need_config; need_assets
    extra=""
    if [ "$mode" = "vr2" ]; then
      extra="--vr2"
      echo "⚠ 2 人目のフォロワー機は接続直後に腕のトルクが抜けます。腕を手で支えてから Enter"
      read -r _
    fi
    # ブリッジが立ち上がった頃に Quest 側でページを開く（USB・adb reverse）
    ( sleep 12; uv run python koch4/koch4_quest_usb.py --port "$VR_PORT" || true ) &
    # shellcheck disable=SC2086
    exec uv run python koch4/koch4_dual_launch.py --pair "$VR_PAIR" --vr "$VR_PAIR" --vw --vr-http --no-panel $extra
    ;;
  sim)
    need_uv; need_assets
    ( sleep 3; open_url "http://localhost:${VR_PORT}/" ) &
    exec uv run python koch4/koch4_vr_bridge.py --sim --arms B,F --http --port "$VR_PORT"
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

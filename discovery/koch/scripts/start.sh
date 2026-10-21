#!/usr/bin/env bash
# Koch 4 本（握手・VR）起動スクリプト（Mac）。discovery/koch（uv プロジェクト）で動く。
#   ./scripts/start.sh                  使い方を表示
#   ./scripts/start.sh check            腕に触れない確認: 制御則の自己診断・準備状況・ポート一覧・Quest の adb（VR ページの部品も取得する）
#   ./scripts/start.sh handshake [...]  握手: 握り返しあり・パネル http://127.0.0.1:8780。既定は 2 ペア。1 ペアだけなら handshake --pair A
#   ./scripts/start.sh host A|B <port>  握手用の 2 台目の PC で: フォロワーを無線で受ける側（<port> はフォロワーのシリアルポート）
#   ./scripts/start.sh vr [...]         VR 1 人: リーダーだけで仮想物体（重さあり）＋ Quest を USB で開く（フォロワーは繋がなくてよい）
#   ./scripts/start.sh vr2 [...]        VR 2 人: 上に加えて、同じペアのフォロワー機も手で握る入力装置に
#   ./scripts/start.sh sim              実機なしで VR ページを確認（Mac のブラウザが開く）
#   ./scripts/start.sh rehearse [名前]  腕なしの通し稽古: 仮想のサーボで握手・VR・2 人・無線を起動から終了まで（--list で一覧）
#   ./scripts/start.sh quest            Quest を繋ぎ直したとき: adb reverse を張り直してページを開く
#   ./scripts/start.sh wifi             給電用: adb を Wi-Fi に切替える（USB の口を充電器に空ける）
#   ./scripts/start.sh manual           マニュアル（HTML）を開く
# [...] はランチャへそのまま渡す追加の引数（例: --pair A / --follower wired / --dry-run / --csv）
# 握手（ペア A）と VR（ペア B）は、端末を 2 つ開けば同時に動かせる: handshake --pair A と vr
# 環境変数: VR_PAIR=A|B（既定 B）  GRIP_MA=フォロワー握力の電流上限 mA（既定 500）  VR_MIRROR=1 で VR 1 人でもフォロワーを動かす
# 握手・VR は実機が動く。ポートは config/config.json に書いたものを使う（推測しない）。
set -e
here="$(cd "$(dirname "$0")/.." && pwd)"
cd "$here"   # discovery/koch（uv プロジェクトの場所）

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
  [ -f "$here/src/webxr/three.module.js" ] || uv run python "$here/src/webxr/setup_assets.py"
}

need_config() {
  if [ ! -f "$here/config/config.json" ]; then
    echo "config/config.json がありません。"
    echo "  uv run koch-launch --list   でポートとシリアルを確認し、"
    echo "  uv run koch-launch --init   で雛形を作って記入してください。"
    exit 1
  fi
}

# 一時的な移行（フェスト後に消す）: git pull で古い discovery/koch4/ に残った git 管理外のファイルを新しい場所へ移す
migrate_from_koch4() {
  local old="$here/../koch4"
  [ -d "$old" ] || return 0
  if [ -f "$old/config/koch4_config.json" ] && [ ! -f "$here/config/config.json" ]; then
    mv "$old/config/koch4_config.json" "$here/config/config.json" || { echo "⚠ 移行に失敗: koch4/config/koch4_config.json"; exit 1; }
    echo "移行: koch4/config/koch4_config.json → config/config.json"
  fi
  if [ -f "$old/webxr/three.module.js" ] && [ ! -f "$here/src/webxr/three.module.js" ]; then
    mv "$old/webxr/three.module.js" "$here/src/webxr/three.module.js" || { echo "⚠ 移行に失敗: koch4/webxr/three.module.js"; exit 1; }
    echo "移行: koch4/webxr/three.module.js → src/webxr/three.module.js"
  fi
  if [ -d "$old/work" ] && [ ! -d "$here/work" ]; then
    mv "$old/work" "$here/work" || { echo "⚠ 移行に失敗: koch4/work/"; exit 1; }
    echo "移行: koch4/work/ → work/"
  fi
  local left
  left="$(find "$old" -type f -not -name .DS_Store -not -path '*/__pycache__/*' 2>/dev/null | head -1)"
  if [ -z "$left" ]; then rm -rf "$old" && echo "移行: 空になった koch4/ を削除しました"
  else echo "⚠ koch4/ にまだファイルがあります（手で確認してください）: $old"; fi
}

has_arg() {  # has_arg --dry-run "$@"
  local want="$1"; shift
  for a in "$@"; do [ "$a" = "$want" ] && return 0; done
  return 1
}

usage() { sed -n '2,17p' "$here/scripts/start.sh" | sed 's/^# \{0,1\}//'; }

mode="${1:-help}"
[ $# -gt 0 ] && shift
case "$mode" in
  check)
    migrate_from_koch4
    need_uv
    uv run koch-teleop --selftest
    if [ ! -f "$here/src/webxr/three.module.js" ]; then
      uv run python "$here/src/webxr/setup_assets.py" || echo "⚠ VR ページの部品を取得できません。ネットに繋がるときにもう一度 check を実行してください"
    fi
    uv run koch-launch --doctor || true
    uv run koch-launch --list || true
    uv run koch-quest --check || true
    ;;
  handshake)
    need_uv; need_config
    exec uv run koch-launch --ff gripper --grip-ma "$GRIP_MA" "$@"
    ;;
  host)
    pair="${1:-}"; port="${2:-}"
    case "$pair" in
      A) listen=9101 ;;
      B) listen=9102 ;;
      *) echo "使い方: ./scripts/start.sh host A|B <フォロワーのシリアルポート>"; exit 1 ;;
    esac
    if [ -z "$port" ]; then
      echo "フォロワーのシリアルポートを渡してください（推測しません）。"
      echo "  uv run koch-launch --list   で確認できます。"
      exit 1
    fi
    need_uv
    ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
    if [ -n "$ip" ]; then
      echo "操作する側の config.json に書く宛先: \"follower_host\": \"udp://$ip:$listen\""
    fi
    exec uv run koch-host --port "$port" --id "koch_follower_$pair" --listen "$listen" --grip-ma "$GRIP_MA"
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
      ( sleep 12; uv run koch-quest --port "$VR_PORT" || true ) &
    fi
    exec uv run koch-launch --pair "$VR_PAIR" --vr "$VR_PAIR" --vw --vr-http --no-panel ${extra[@]+"${extra[@]}"} "$@"
    ;;
  sim)
    need_uv; need_assets
    ( sleep 3; open_url "http://localhost:${VR_PORT}/" ) &
    exec uv run koch-bridge --sim --arms B,F --http --port "$VR_PORT"
    ;;
  rehearse)
    need_uv; need_assets
    exec uv run python "$here/src/koch/simbus/rehearse.py" "$@"
    ;;
  quest)
    need_uv
    exec uv run koch-quest --port "$VR_PORT"
    ;;
  wifi)
    need_uv
    exec uv run koch-quest --port "$VR_PORT" --wifi
    ;;
  manual)
    open_url "$here/docs/manual/index.html"
    ;;
  *) usage ;;
esac

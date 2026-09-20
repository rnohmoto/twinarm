#!/usr/bin/env bash
# Dobot Magician ピック＆プレース 起動スクリプト（Mac / Linux）
#   ./start.sh          設定が済んでいれば実演画面、まだならウィザード
#   ./start.sh wizard   初期設定ウィザード（カメラ→ロボット→較正→置き場→高さ→検出→試運転）
#   ./start.sh demo     実演画面（config.json のポートとカメラで。ブラウザが開く）
#   ./start.sh dry      実機なしの実演（動作確認）
#   ./start.sh check    読み取りだけ（カメラ一覧・ポート候補・テスト）
set -e
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv が見つかりません。https://docs.astral.sh/uv/ の手順でインストールしてから再実行してください。"
  exit 1
fi
uv sync -q

open_url() {
  local url="$1"
  if command -v open >/dev/null 2>&1; then open "$url"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url"
  fi
}

configured() {
  [ -f config.json ] && uv run python - <<'PY'
import json, os, sys
c = json.load(open("config.json", encoding="utf-8"))
ok = bool(c.get("robot", {}).get("port")) and os.path.exists(c.get("homography_path", "assets/homography.json"))
sys.exit(0 if ok else 1)
PY
}

mode="${1:-auto}"
if [ "$mode" = "auto" ]; then
  if configured; then mode=demo; else mode=wizard; fi
  echo "→ $mode を起動します（config.json のポートと較正の有無で判断）"
fi

case "$mode" in
  wizard) exec uv run python setup_wizard.py ;;
  demo)
    port=$(uv run python -c "import json;print(json.load(open('config.json',encoding='utf-8'))['demo']['panel_port'])")
    ( sleep 4; open_url "http://127.0.0.1:${port}" ) &
    exec uv run python demo.py --config config.json --robot pydobot ;;
  dry)
    ( sleep 4; open_url "http://127.0.0.1:8790" ) &
    exec uv run python demo.py --dry-run --no-llm ;;
  check)
    uv run python check_camera.py --list || true
    uv run python check_robot.py --list
    uv run pytest -q ;;
  *) echo "使い方: ./start.sh [auto|wizard|demo|dry|check]"; exit 2 ;;
esac

#!/bin/bash
# ダブルクリックで起動（Finder）。中身は ./start.sh check: 自己診断・準備状況・ポート一覧・Quest の接続（腕は動かない）
here="$(cd "$(dirname "$0")" && pwd)"
[ -f "$here/start.sh" ] || { echo "⛔ start.sh が隣にありません（${here}）。この .command は scripts/ の中に置いたまま実行してください。ショートカットからは、この .command の絶対パスを open で開いてください"; read -r -p "Enter で閉じます " _; exit 1; }
"$here/start.sh" check
echo
read -r -p "Enter でこの窓を閉じます " _

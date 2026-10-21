#!/bin/bash
# ダブルクリックで起動（Finder）。中身は koch.sh all
here="$(cd "$(dirname "$0")" && pwd)"
[ -f "$here/koch.sh" ] || { echo "⛔ koch.sh が隣にありません（${here}）。この .command は scripts/ の中に置いたまま実行してください。ショートカットからは、この .command の絶対パスを open で開いてください"; read -r -p "Enter で閉じます " _; exit 1; }
"$here/koch.sh" all
echo
read -r -p "Enter でこの窓を閉じます " _

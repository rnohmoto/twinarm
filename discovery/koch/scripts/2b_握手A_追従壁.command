#!/bin/bash
# ダブルクリックで起動（Finder）。中身は FF_STYLE=follow koch.sh handshake（追従壁式の握り返し・10/25。硬さは FF_PGAIN、既定 1200）
here="$(cd "$(dirname "$0")" && pwd)"
[ -f "$here/koch.sh" ] || { echo "⛔ koch.sh が隣にありません（${here}）。この .command は scripts/ の中に置いたまま実行してください。ショートカットからは、この .command の絶対パスを open で開いてください"; read -r -p "Enter で閉じます " _; exit 1; }
FF_STYLE=follow "$here/koch.sh" handshake
echo
read -r -p "Enter でこの窓を閉じます " _

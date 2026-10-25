#!/bin/bash
# ダブルクリックで起動（Finder）。中身は koch.sh handshake。
# Ctrl+C を 1 回押すと後片付け（トルクを抜く）をしてから、この窓は自動で閉じる。異常終了のときだけ窓を残す。
here="$(cd "$(dirname "$0")" && pwd)"
[ -f "$here/koch.sh" ] || { echo "⛔ koch.sh が隣にありません（${here}）。この .command は scripts/ の中に置いたまま実行してください。ショートカットからは、この .command の絶対パスを open で開いてください"; read -r -p "Enter で閉じます " _; exit 1; }
"$here/koch.sh" handshake
code=$?
if [ "$code" -eq 0 ]; then
  echo "止まりました。この窓を閉じます（使用後は両腕の電源を抜いてください）"
  TTY="$(tty)"
  # 自分のシェルが終わった後に窓を閉じる（SIGHUP で消されないよう nohup）
  nohup bash -c "sleep 1; osascript -e 'tell application \"Terminal\" to close (every window whose tty of selected tab is \"$TTY\")'" >/dev/null 2>&1 &
  exit 0
fi
echo
read -r -p "⚠ 異常終了（code=$code）。上の内容を確認したら Enter でこの窓を閉じます " _

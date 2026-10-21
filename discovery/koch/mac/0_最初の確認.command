#!/bin/bash
# ダブルクリックで起動（Finder）。中身は ../scripts/start.sh check: 自己診断・準備状況・ポート一覧・Quest の接続（腕は動かない）
cd "$(dirname "$0")"
../scripts/start.sh check
echo
read -r -p "Enter でこの窓を閉じます " _

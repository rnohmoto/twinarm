#!/bin/bash
# ダブルクリックで起動（Finder）。中身は koch.sh stop
cd "$(dirname "$0")"
./koch.sh stop
echo
read -r -p "Enter でこの窓を閉じます " _

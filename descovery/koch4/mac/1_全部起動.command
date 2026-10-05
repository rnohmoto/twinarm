#!/bin/bash
# ダブルクリックで起動（Finder）。中身は koch.sh all
cd "$(dirname "$0")"
./koch.sh all
echo
read -r -p "Enter でこの窓を閉じます " _

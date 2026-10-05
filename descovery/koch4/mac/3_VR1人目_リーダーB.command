#!/bin/bash
# ダブルクリックで起動（Finder）。中身は koch.sh vr-leader
cd "$(dirname "$0")"
./koch.sh vr-leader
echo
read -r -p "Enter でこの窓を閉じます " _

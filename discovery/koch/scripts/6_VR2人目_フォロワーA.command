#!/bin/bash
# ダブルクリックで起動（Finder）。中身は PAIR=A ./koch.sh vr-follower
cd "$(dirname "$0")"
PAIR=A ./koch.sh vr-follower
echo
read -r -p "Enter でこの窓を閉じます " _

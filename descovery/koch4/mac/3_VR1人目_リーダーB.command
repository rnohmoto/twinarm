#!/bin/bash
# ダブルクリックで起動（Finder）。中身は PAIR=B ./koch.sh vr-leader
cd "$(dirname "$0")"
PAIR=B ./koch.sh vr-leader
echo
read -r -p "Enter でこの窓を閉じます " _

#!/bin/bash
# ダブルクリックで起動（Finder）。中身は koch.sh quest: 動いているブリッジの口に adb reverse を張り、ゴーグルのブラウザでページを開く
cd "$(dirname "$0")"
./koch.sh quest
echo
read -r -p "Enter でこの窓を閉じます " _

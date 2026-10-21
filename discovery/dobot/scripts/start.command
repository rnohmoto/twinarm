#!/usr/bin/env bash
# Mac: Finder でダブルクリック → Terminal が開き、ランチャー画面（ブラウザ）が出る。
# そこから 実演の起動・停止、較正、置き場の調整、設定の変更ができる。中身は ./scripts/start.sh launcher。
cd "$(dirname "$0")" && exec ./start.sh launcher "$@"

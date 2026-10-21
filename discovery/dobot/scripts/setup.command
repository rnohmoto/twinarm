#!/usr/bin/env bash
# Mac: Finder でダブルクリック → Terminal が開いて準備（scripts/setup.sh）が走る。1 回だけ。
cd "$(dirname "$0")" && exec ./setup.sh "$@"

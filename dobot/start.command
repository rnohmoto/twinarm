#!/usr/bin/env bash
# Mac: Finder でダブルクリックすると Terminal が開いて実演が起動する。中身は ./start.sh と同じ（引数もそのまま渡す）。
cd "$(dirname "$0")" && exec ./start.sh "$@"

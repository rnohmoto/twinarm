#!/usr/bin/env bash
# 新しい Mac での準備（1 回だけ）。git clone のあとにこれを実行すれば、start.command で実演が起動できる状態になる。
#   ./setup.sh          uv の導入 → 依存の取得（音声・LLM 込み）→ 音声モデルの取得 → オフラインテスト → 機材の確認
# 腕は動かさない。ネットワークは uv・依存・音声モデル（約 480 MB）の取得に使う。
set -e
cd "$(dirname "$0")"

echo "== 1/5 uv"
if ! command -v uv >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    export PATH="$HOME/.local/bin:$PATH"
  else
    echo "   uv をインストールします（https://astral.sh/uv）"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi
uv --version

echo "== 2/5 依存（Python 3.13 と OpenCV・pydobot・faster-whisper・sounddevice・anthropic）"
uv sync --extra voice --extra llm

echo "== 3/5 音声モデル（faster-whisper small。初回は約 480 MB のダウンロード）"
uv run --no-sync python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8'); print('   モデル OK')"

echo "== 4/5 オフラインテスト（機材なしで通る）"
uv run --no-sync pytest -q

echo "== 5/5 機材の確認（読み取りだけ）"
echo "   シリアルポート:"; ls /dev/cu.usbserial-* 2>/dev/null || echo "   （Magician が見えません: 電源と USB を確認。CH9102 は macOS 12 以降なら追加ドライバ不要）"
echo "   設定のポート: $(uv run --no-sync python -c "import json;print(json.load(open('config.json'))['robot']['port'])")"
echo "   較正ファイル: $( [ -f assets/homography.json ] && echo 'あり（同じ机ならそのまま使える。動かしたらランチャーの「較正をやり直す」）' || echo '無し → ランチャーの「較正をやり直す」' )"
echo
echo "準備ができました。次: start.command をダブルクリック（または ./start.sh launcher）"
echo "  - 初めてマイクを使うとき macOS が許可を聞いてきます（Terminal に許可）"
echo "  - カメラの露出・フォーカスの固定は Logi Options+ で（docs/setup-manual.md）"

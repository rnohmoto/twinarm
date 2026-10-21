# 導入手順（新しい Mac・新しい机）

版: 2026-10-19 ／ 関連: [操作マニュアル](operation-manual.md)・[要件定義書](requirements.md)・機材の初期設定は [`../manual/index.html`](../manual/index.html)

## 1. 必要なもの

- Dobot Magician（吸盤、電源、USB ケーブル）
- Logicool C922 と、机の真上に固定する器具（アーム・三脚）
- Mac（Apple Silicon で確認。Intel も uv が対応）。初回だけインターネット
- 色ブロック（赤・緑・青・黄）。較正用に青 1 個
- このリポジトリ（`git clone`）

## 2. Mac の準備（1 回だけ）

```bash
git clone <リポジトリ>            # 既にあるなら git pull
cd twinarm/dobot                  # または twinarm-main/dobot
```

Finder で `dobot/scripts` を開き、**`setup.command` をダブルクリック**（または `./scripts/setup.sh`）。次が自動で進みます。

1. uv（Python の管理ツール）の導入。Python 3.13 も uv が取得する
2. 依存ライブラリ（OpenCV、pydobot、faster-whisper、sounddevice、anthropic）
3. 音声モデル faster-whisper small（約 480 MB）のダウンロード
4. 機材なしで通るテスト（126 件）
5. シリアルポートと較正ファイルの有無の表示

権限:

- **マイク**: 初めてマイクを使うとき、macOS が Terminal への許可を聞く → 許可。出なければ システム設定 → プライバシーとセキュリティ → マイク
- **カメラ**: 同様に Terminal へ許可
- `scripts/start.command` をダブルクリックして「開発元を確認できない」と出たら、右クリック → 開く（git clone したファイルでは通常出ない）

Logi Options+（任意だが推奨）: C922 のフォーカスを「自動 OFF・机にピント」、露出を「自動 OFF」に固定する。WB は自動のまま（手動だと青被り）。固定しないと、腕が映り込むたびに明るさが変わって色の判定が揺れる。

## 3. 機材の接続

1. Magician の電源を入れ、本体のキーを 2 秒長押し（LED 緑＝ホーミング済み）。**毎回**
2. Magician の USB を Mac へ。`ls /dev/cu.usbserial-*` にポートが出る（CH9102、macOS 12 以降は追加ドライバ不要）。`src/config.json` の `robot.port` と違えば書き換える（ポート名の末尾はアダプタ固有なので、同じ機材なら Mac を替えても同じ）
3. カメラを机の真上に固定し、作業域（ロボットの前 15〜30 cm、左右 ±15 cm）が画面の中央に入るように向ける。C922 は OpenCV の index 0 のはず（Mac 内蔵が 1）。違えば `src/config.json` の `camera.index`
4. 腕の周りを空ける。机に黒や白のマットがあると検出が安定する

## 4. 初回だけ（同じ机・同じ配置なら不要）

`src/config.json` と `assets/homography.json` はベンチの実機値でコミットしてあります。**同じ机・同じカメラ位置なら、このまま起動できます。** カメラかロボットを動かしたら 4.1、トレイを置き直したら 4.2、机や台を変えたら 4.3。

### 4.1 較正（5 分、腕が動く）

ランチャーの「較正をやり直す」、または:

```bash
uv run dobot-calibrate --config src/config.json --object blue_cube --drive
```

腕が 3×3 の 9 点を回り、各点でブロック上面の 8 mm 上に止まる。吸盤の真下に青ブロックを置いて Enter。
終わると `RMS=… mm` が出る。**3 mm 以下で合格**。前の較正は `logs/homography_before_drive_*.json` に残る。
「腕を手で当てて座標を読む」方式（`--drive` なし）は使わない（たわみで 7〜11 mm ずれた）。

### 4.2 置き場（1 分、腕は動かない）

ランチャーの「置き場を調整」、または `uv run dobot-zones`。数字で選んで実物のトレイの真ん中をクリック、s で保存。

### 4.3 高さ（2 分、腕は手で動かす）

腕のロック解除ボタンを押しながら吸盤を当て、座標を読む:

```bash
uv run dobot-check-robot --config src/config.json      # pose x= y= z=
```

| 当てる場所 | 書く場所 | 値 |
| --- | --- | --- |
| ブロックの上面 | `objects[].z_pick`（4 色とも） | 読んだ z − 1 |
| 置く面に、ブロックを吸盤に付けたまま底を当てる | `robot.z_place` | 読んだ z ＋ 2 |
| — | `robot.z_min` | どちらより下（例 −80） |

ウィザードでも可: `uv run dobot-setup --from 5`（5 置き場・6 高さ）。

### 4.4 検出の確認（照明が変わったとき）

```bash
uv run dobot-setup --only 7
```

全部のブロックに枠が付き、余計な枠が無ければ OK。外れる色は `src/config.json` の `objects[].ranges`（HSV の下限・上限）を広げる。2026-10-19 の値: 緑の彩度下限 50、黄の色相下限 16、木片の縦横比上限 4.0。

## 5. 起動と確認

`scripts/start.command` → ランチャー → 「実演を起動」。初回は実演画面で **往復（試運転）** → 文字で 1 回ピック → **ループ ×3** の順に確認する（[操作マニュアル](operation-manual.md)）。

## 6. 別の机・別の配置に移したとき

| 変えたもの | やること |
| --- | --- |
| Mac だけ | §2 のみ（較正・設定はそのまま） |
| カメラの位置・向き | §4.1 較正 → §4.2 置き場 |
| ロボットの位置 | §4.1 較正 → §4.2 置き場 |
| 机・台の高さ | §4.3 高さ → §4.1 較正 |
| 照明 | §4.4 検出。必要なら Logi Options+ で露出を固定し直す |
| ブロックの種類 | §4.3 高さ（上面）と §4.4 色 |

## 7. Windows

`scripts\start.bat`（ランチャー未対応: `wizard`・`demo`・`dry`・`check`）。ポートは `COMn`。未検証。

## 8. ファイルの場所

| ファイル | 内容 |
| --- | --- |
| `src/config.json` | 設定（ポート、高さ、置き場、色、速さ、画面）。ランチャーと道具が書く。保存前のコピーは `config.bak_*.json` |
| `assets/homography.json` | 較正結果。`assets/homography.pairs.json` は較正の対応点（再開用） |
| `logs/pnp_*.jsonl` | 実演の記録（検出・指示・動作・返事） |
| `logs/bench_*.md` | ベンチ作業の記録 |
| `logs/demo_launcher.log` | ランチャーから起動した実演の出力 |
| `docs/HANDOFF.md` | 短い再開手順（Claude 向けの起動プロンプトも） |

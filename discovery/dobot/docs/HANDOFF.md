# dobot 引き継ぎ — Magician 実演を再現する（2026-10-19 時点）

この文書だけで、実演の起動・使い方・設定のやり直しができるようにしてある。経緯の記録は
`../logs/bench_20261015.md` と `../logs/bench_20261016.md`、コードの一覧と各スクリプトの危険度は [`../README.md`](../README.md)。

## 0. 起動は 1 コマンド

```bash
cd dobot && ./scripts/start.sh launcher   # Finder なら scripts/start.command をダブルクリック（同じもの）。Windows は scripts\start.bat（ランチャー無し）
```

ブラウザにランチャー（http://127.0.0.1:8789）が開く。「実演を起動」で実演（http://127.0.0.1:8790）。停止・再起動・較正・置き場・設定・ログも
この画面から。新しい Mac は先に `scripts/setup.command`（1 回）。説明員向けの手順は [操作マニュアル](operation-manual.md)、
導入は [導入手順](setup-manual.md)。

- 設定が揃っていれば実演画面（`dobot-demo`）が立ち上がり、ブラウザで http://127.0.0.1:8790 が開く。終了は Ctrl+C
- **起動時に腕がホーム姿勢へ動く。** 腕の周りを空けてから実行する
- 先に Magician の電源を入れ、本体キーを 2 秒長押ししてホーミング（LED 緑）しておく
- 画面は上から: カメラ → マイク → 操作ボタン → 記録。状態（待機／動作中／非常停止）はヘッダー。「詳細」を開くと fps やポートなど
- 映像は 15 fps の専用スレッドで配信され、腕が動いている間も止まらない。**表示は左右反転**（`demo.panel_mirror=true`、来場者がロボットと向かい合う向き）。検出・較正・`dobot-zones` の座標は元の向きのままで、`dobot-zones` も同じ反転で表示する

## 1. いまの構成（2026-10-16 に実機で確認したもの）

| 項目 | 値 |
| --- | --- |
| PC | MacBook Pro（フェス Mac D）。作業ディレクトリは `twinarm-main/dobot` |
| ロボット | Dobot Magician、吸盤。ポート `/dev/cu.usbserial-5A9E0126111`（CH9102）、backend `magician`（非常停止レーン付き） |
| カメラ | Logicool C922 俯瞰、OpenCV index 0、1280×720。Logi Options+ で AF OFF・手動フォーカス（机）・自動露出 OFF、WB は自動（手動だと青被り） |
| 物 | 木のブロック 4 色（赤・緑・青・黄）、厚み 16 mm。ゴルフボール（白）は `detector: sphere`＝白い机でも「丸い縁の影」で見つける（10/26・実機未確認。手引き `manual/fest_quickstart.html` §3） |
| 高さ | ブロック上面 z=-56.1 → `z_pick`=-57.1（4 色）。机面 z=-72.2 → `z_place`=-54.1（面＋厚み＋2）。`z_safe`=20、`z_min`=-80 |
| 速さ | `speed_pct`=100、`travel_mode`=movj、`ee_settle_s`=0.4（§6） |
| 置き場 | `tray_right`=(211,-125)。`tray_left`(230,110)・`box_front`(170,0)・`box_back`(280,0)・`start`(235,0) は既定 |
| 較正 | `assets/homography.json`（git 管理・2026-10-19 12:38・9 点・RMS 3.10 mm）。カメラかロボットを動かしたときだけ取り直す: `--verify`（30 秒の確認）→ 3 mm 超なら `--carry`（ブロック 1 個を腕が運ぶ・約 1 分）。§4.1 |
| 音声 | faster-whisper `small`（`~/.cache/huggingface`、初回 480 MB）。マイクは Mac 内蔵。ターミナルにマイクの許可が必要 |
| 解析 | ルールベース（`config.json` の `llm.enabled=false`）。Claude を使うなら `true` にして `ANTHROPIC_API_KEY` |

`src/config.json` はこの値でコミットしてある。ポートだけは PC・ケーブルで変わる（`ls /dev/cu.usbserial-*`）。

## 2. 起動前の確認（腕は動かない）

```bash
./scripts/start.sh check                                # カメラ一覧・ポート候補・オフラインテスト
uv run dobot-check-robot --config src/config.json       # つないで現在位置を読むだけ
```

候補ポートが「なし」と出ても、CH9102 はその一覧に出ないだけ。`config.json` のポートが `/dev/cu.*` に存在すればよい。

## 3. 画面の使い方

- **声**: 「声の準備ができました。」が出てから、マイクのボタン（または Space）を押したまま話して離す。聞き取った文と解釈が出て、2 秒後に実行（Enter ですぐ、Esc で取消）。聞き取りを直して Enter でも実行できる
- **文字**: 入力欄に「赤いブロックを左に置いて」「全部手前に入れて」「片付けて」など
- **ボタン**: ホーム／往復（試運転）／片付け（元に戻す）／ループ ×3／非常停止／停止解除／自動ループ
- **非常停止**: ボタンか Esc（入力欄の外で）。動作中でも止まる。再開は 停止解除 → ホーム
- 「〜はもう右にあります」と返るのは、その物が置き場の半径（60 mm）内にあるから。動かしたい物は置き場の外に置く
- 置くときは、カメラに見えている物の上には乗せない（物の大きさ＋8 mm 離れた空きを、スロット → 中心 → 置き場の中の順に探す）。空きが無いと「〜に空きがありません」と返して拾わない

## 4. 設定のやり直し（それぞれ独立）

### 4.1 較正（カメラ画素 → ロボット座標）— **腕が動く**

較正が要るのは **カメラかロボットを動かしたときだけ**。同じ配置なら `assets/homography.json` をそのまま使う
（ランチャーに日時・RMS・点数が出る）。迷ったら先に「確認」。

**確認（約 30 秒）**: 青ブロックを 1 個、カメラに見える所（トレイの外・作業域の中）に置いて:

```bash
uv run dobot-calibrate --config src/config.json --object blue_cube --verify
```

腕が今の較正でそれを拾い、中心 (225,0) に置き、置いた位置をカメラで読んで命令座標との差を出す。
**3 mm 以下なら「使えます」＝やり直し不要。** 拾えなければ（物が動かなければ）較正がずれているか吸着不良 → やり直す。

**やり直し（約 1 分・ブロックを置くのは 1 回だけ）**: 机からトレイをどけ、青ブロックを 1 個用意して:

```bash
uv run dobot-calibrate --config src/config.json --object blue_cube --carry                  # 3×3 の 9 点
uv run dobot-calibrate --config src/config.json --object blue_cube --carry --points quick   # 中心＋四隅の 5 点（約 40 秒）
```

腕が最初の点（基部に近い右の隅 (175,−125)）の上 8 mm で止まるので、**吸盤の真下にブロックを置いて Enter（これ 1 回だけ）**。
あとは腕がブロックを拾い、四隅 → 中心 → 辺の中点の順に、各点に置く → 脇へ退避 → カメラで読む → 拾う、を繰り返す。
拾えていなかったときは前の点から自動で拾い直す。
見えない・途中で落としたときはその点の上で止まり「置いて Enter」と聞いてくる（s=飛ばす・q=中断）。
終わると RMS と前回との差が出る。**RMS 3 mm 以下で合格。** 残差 8 mm 超の点は自動で外す（5 点は残す）。
前の較正は `../logs/homography_before_carry_*.json` に残る。

点数の考え方: ホモグラフィは 4 点で決まる。5 点（quick）で足り、9 点（full）にすると余った点で当てはまりの悪さ（RMS）が
見える。点を作業域の端（内側 25 mm）まで広く取るのは精度のためで、可動域を確かめているわけではない。
`--drive`（各点で人が置く・Enter 9 回）は `--carry` が動かないときの予備。「腕を手で当てて座標を読む」方式（フラグ無し）は
使わない: たわみで 7〜11 mm ずれた（2026-10-16）。

### 4.2 置き場（トレイの位置）— 腕は動かない

```bash
uv run dobot-zones
```

カメラ画像に置き場の丸が出る。数字キー（1〜5）で選び、実物のトレイの真ん中をクリック → s で保存。
置く位置（スロット）が腕の作業域からはみ出す場所は拒否される（もう少し内側へ）。ロボットの「右」は画面では左側。

### 4.3 高さ（拾う・置く）— 腕は手で動かす

腕のロック解除ボタンを押しながら吸盤を当て、座標を読む:

```bash
uv run dobot-check-robot --config src/config.json      # pose x= y= z= が出る
```

- 物の上面に当てた z → `config.json` の `objects[].z_pick` = その z − 1
- 置く面に物を**つかんだまま**当てた z（＝面＋物の厚み）→ `robot.z_place` = その z ＋ 2
- `robot.z_min` はどちらより下に

ウィザード（`uv run dobot-setup --from 6`）でも同じことができる。

### 4.4 カメラの固定

Logi Options+ → C922 → フォーカス: 自動 OFF にして机にピント、露出: 自動 OFF、ホワイトバランス: 自動のまま。
確認は `uv run dobot-check-camera --index 0`（読み戻しは AVFoundation の都合で「未確認」と出るが、Options+ 側で固定されていればよい）。

### 4.5 音声モデルの取得（ネットが要る、1 回だけ）

```bash
uv run --no-sync python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
```

### 4.6 別の PC で動かす

`git clone` → `cd dobot && uv sync --extra voice --extra llm` → `src/config.json` の `robot.port` を直す → 4.5 → 4.4 → 4.1 → 4.2 → 4.3 → `./scripts/start.sh`。
Windows は `scripts\start.bat`（ポートは `COMn`、カメラ index は `dobot-check-camera --list` で確認）。

## 5. 既知の注意

- 置き場は「中心」だけでなく置く位置（スロット）も作業域に入っている必要がある（`dobot-zones` が確認する）
- 会場の机と壁は `robot.right_limit_mm`／`left_limit_mm`／`front_edge_mm`（基部の中心からの距離・ランチャーの設定で入れる）。
  `RobotConfig.bounds()` が作業域の箱との狭い方を返し、ガード・ジョグ・較正の格子と退避点・置き場の確認が全部それを使う（2026-10-26。
  本人「長机の右側が壁で、腕を伸ばし切ると当たる」）。余裕は `edge_margin_mm`=40。ホーミングの左右の振りは機械の動作で箱の外＝壁まで 30 cm 未満なら ⚠
- 置くときの吸盤高さは物ごと（`objects[].z_place` → 置き場の `z_place` → `robot.z_place` の順）。ブロック用の −54.1 を
  ゴルフボール（高さ 43 mm）に使うと 27 mm 押し付けるので、ボールは `z_pick` −30.5／`z_place` −27.5（机 −72.2 からの
  計算値。現地で `dobot-setup --from 6` を通せば実測で上書きされ、z_place は z_pick + 3 で自動）
- 色の範囲（HSV）は `config.json` の `objects[].ranges`。照明が変わって検出が崩れたら `dobot-setup --from 7` で枠を確認
- `./scripts/start.sh wizard` を 1 番から通すと `src/config.json` を上書きしていく。途中からは `--from N`
- `uv run pytest` は実機の較正ファイルがあっても通る（2026-10-19 修正）。ポート 8790 を実演が使っている間はパネルのテスト 2 件が落ちる
- `config.bak_*.json` はウィザードと `dobot-zones` が保存前に残すコピー（git 管理外）

## 6. 速さの調整

2026-10-16 のログでは 1 回のピック＆プレースに 20 秒かかっていた（速度比 30 %・全区間が直線補間・退避高さ 40）。
2026-10-19 に次の 4 つを変えた（`config.json` の `robot`）。遅く戻すときもここ。

| 項目 | 前 | 今 | 意味・戻し方 |
| --- | --- | --- | --- |
| `speed_pct` | 30 | 100 | Magician の速度比・加速度比（公式に単位・範囲の記載なし。100 を上限に）。J1〜J4 の**脱調アラーム**が出たり、物を振り落とすなら 70 に下げる。脱調の復帰は本体キーのホーミング（パネルの「ホーム」は座標へ動くだけ） |
| `travel_mode` | （直線） | `movj` | 退避・横移動・持ち上げを関節補間に（速い。経路は少し曲がる）。物へ下りる区間は常に直線。箱の縁すれすれを通すなら `movl` |
| `z_safe` | 40 | 20 | 退避高さ。運んでいる物の底（吸盤 −16 mm）が箱やトレイの縁より上になる値にする。縁が高い箱を使うなら 40 に戻す |
| `ee_settle_s` | 0.6 | 0.4 | 吸着が効くまでの待ち秒数。吸えずに落とすなら 0.6 に戻す |

確認の順番: パネルの「往復（試運転）」→ 文字で 1 回ピック → 連続（ループ ×3）。落とす・止まる・アラームが出たら上の表の順に戻す。
もっと速くする次の手は、PTP の座標／関節パラメータ（SetPTPCoordinateParams / SetPTPJointParams）を明示的に上げること（未実装・実機で上限を測ってから）。

## 7. 新しい会話の起動プロンプト（コピペ）

```
twinarm-main の dobot で Dobot Magician の実演を再開します。dobot/AGENTS.md → dobot/docs/HANDOFF.md の順に読んでください。
- 腕が動くコマンド（./scripts/start.sh、dobot-calibrate --verify / --carry / --drive、dobot-setup の y）は私がこの会話で頼んだときだけ実行する
- ポートは src/config.json のものを使い、推測しない。実機の挙動は私が実行して報告した結果だけを事実にする
- まず ./scripts/start.sh check の結果を見せてから、./scripts/start.sh で実演を起動する手順を案内して
```

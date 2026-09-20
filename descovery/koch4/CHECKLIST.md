# koch4 チェックリスト — Mac で回す手順と記録欄（TEST 0〜6 ＋ VR V0〜V3）

正本: robotics `TacitCapture/76_`（TEST 0〜6）・`58_` 付録A（受入）・`62_` §2（運用レシピ）・
`.claude/handoff/260920_T2_Koch4本_2ペア接続検証.md` §4。本書はそれらを koch4 のコマンドに
置き換えて 1 枚にしたもの。**合格は、ユーザーが実行して報告した結果だけを事実とする。**
実機を動かす・トルクを抜くコマンドは、その会話でユーザーが明示的に頼んだときだけ実行する。

前提: MacBook Pro／twinarm `descovery/` で `uv sync` 済み／lerobot 0.6.1（uv.lock）／
電源 Leader=5V・Follower=12V／`max_relative_target=20` は安全リミッタ／終了は Ctrl+C の後に
**両アームの AC アダプタを必ず抜く**（62_ §2.3）。

```
実施日:            実施者:            Mac:            uv sync 日:
ハブ: 型番                 セルフパワー [ ]  4口ともデータ対応 [ ]  ケーブル: データ対応品 [ ]
```

---

## TEST 0 — ポート列挙とシリアル確認（読み取りのみ・4台の識別体制）

```bash
uv run python koch4/koch4_dual_launch.py --list
system_profiler SPUSBDataType | grep -A5 CH34      # 補助
```
- 合格: 4 ボードが 4 ポートとして見え、シリアル番号が表示される
- 分岐: シリアルがユニーク → `--init` した config に serial を登録／重複・空（CH343 の既知リスク）→
  ポート名直書き＋**どのボードを Mac／ハブのどの口に挿すかを物理固定（テープ）**

```
結果: ポート数 [  ]  シリアル: ユニーク / 重複 / 空
A leader=                      A follower=
B leader=                      B follower=
物理固定の写真 [ ]
```

## TEST 1 — 2台目（ペアB）の単独受入（58_ CHECK 0〜8）

koch4 以前の問題（ID 重複・較正）は 58_／61_ の手順で先に潰す。較正は koch4 の場所に保存する:

```bash
# 較正 JSON を koch4/config/calibration/ に置く（既存の ~/.cache の JSON はコピーでも可）
lerobot-calibrate --robot.type=koch_follower --robot.port=<F> --robot.id=koch_follower_B \
    --robot.calibration_dir=koch4/config/calibration/koch_follower
lerobot-calibrate --teleop.type=koch_leader --teleop.port=<L> --teleop.id=koch_leader_B \
    --teleop.calibration_dir=koch4/config/calibration/koch_leader
```
（`lerobot-calibrate` の引数名は 0.6.1 の CLI で確認する。動かなければ `--lerobot-cache` で既定の場所を使う）

```
2台目: 目視 [ ] LED 12/12 [ ] スキャン判定 F: A/B/C/D  L: A/B/C/D  ID修正: 要/不要
最終スキャン F 6/6 [ ]  L 6/6 [ ]   較正 F [ ] L [ ]   較正ファイルの所在: config/ / ~/.cache
```

## TEST 2 — ペアA 単独回帰

```bash
uv run python koch4/koch4_dual_launch.py --init      # 初回のみ → config/koch4_config.json を記入
uv run python koch4/koch4_dual_launch.py --pair A --ff gripper
```
- 合格: 追従＋グリッパ力覚 FB（握り返し）＋パネル http://127.0.0.1:8780 が従来どおり
- 確認する行: `[ff] 設定確認: Operating_Mode=5 / Torque_Enable=1 / Position_P_Gain=800` が期待値どおりか

```
追従 [ ]  握り返し [ ]  P_Gain 読み戻し=       開位置(較正全開端) tick=       温度(2分後)=   °C
```

## TEST 3 — ペアB 単独（新機体）

```bash
uv run python koch4/koch4_dual_launch.py --pair B --ff gripper      # パネル 8781
```
```
追従 [ ]  握り返し [ ]  ペアAとの体感差:
```

## TEST 4 — 2ペア同時（本題）

```bash
uv run python koch4/koch4_dual_launch.py --pair both --ff gripper --csv
```
- 合格 ①両ペア 30 fps 維持（`work/csv/teleop_*.csv` の t_sec 差分、または teleop ログ）
  ②クロストークなし（片方を動かしても他方のフォロワーが動かない＝ポート取り違え検出）
  ③10 分連続で偽通信断・HW エラーなし（`work/logs/dual_*_teleop.log` の `[robust]`/`[hw]` 行）
- ハブ経由でフレーム落ちが出たら、シリアル 4 本を Mac 直挿しに逃がす（ALOHA 公式の逃げ方）

```
fps A=      B=      クロストーク: なし/あり   10分: 通信断  回・HWエラー  回   ハブ: 可/直挿しへ
```

## TEST 5 — 連続稼働リハ（30 分）

```
30分: 制御30fps [ ]  カメラ15fps(使う場合) [ ]  サーボ温度 最高   °C(<55)  プロセス死: なし/あり
```

## TEST 6 — 握手アタッチメント装着後の体験リハ

前提: `robotics/output/cad/print_kit_fest2610/` の印刷前ゲート（87_ §4）を実機採寸で通す。
```
説明30秒で握れた [ ]  握り返しを自覚 [ ]  危険動作なし [ ]  つまずき:
```

---

## VR — 仮想物体の反力（`--ff vwall`）

### V0 — 壁だけ（ヘッドセットなし・リーダーのみ・10 分）

物理的にはこれが本質。リーダーだけ繋いで、固定の仮想物体に握り込む:
```bash
uv run python koch4/koch4_teleop.py --leader-port <L> --leader-id koch_leader_B \
    --follower-port none --ff vwall --wall ball:45:800:300
```
- 期待: 開き 100→45 までは指が自由（Goal_Current 0）。45 を切った瞬間に `[vwall] 接触` が出て
  トリガーが押し返す。離すと `[vwall] 離脱`。`--wall sponge:60:250:120` で柔らかさが違うか
- 記録する数値: 接触時に手で感じる差（硬い／柔らかい／段差）、`Position_P_Gain` の読み戻し、
  2 分後の温度、発振（かちかち）の有無。発振したら `--ff-pgain` を 800→400 に下げて再試行

```
ball: 反力 [ ] 発振: なし/あり  sponge: 差を感じる [ ]  温度=   °C  P_Gain読戻=
```

### V1 — 分身の空間投影（84_ Step 0〜1 と同じ・Quest Pro）

```bash
python koch4/webxr/setup_assets.py                         # 初回のみ(three.js 取得)
uv run python koch4/koch4_vr_bridge.py --sim               # Quest ブラウザで https://<Mac IP>:8443/
```
- 合格: 分身が動き、3 物体が机上に見え、「AR表示」でパススルーに浮かぶ。HUD の「送信: 送信OK」
- 会場 Wi-Fi はクライアント隔離が濃厚 → 自前ルータ／テザリング。開発者モードなら `--http --port 8080`＋`adb reverse tcp:8080 tcp:8080`

```
URL 到達 [ ]  分身が動く [ ]  AR 表示 [ ]  送信OK [ ]  遅延の体感:
```

### V2 — 実機リーダー × VR（本題）

```bash
uv run python koch4/koch4_dual_launch.py --pair B --vr B      # B は follower_port "none" でも可
```
- 分身がリーダーに追従 → 分身の指先を机上の物体に寄せる（HUD「物体: 硬いボール」）→ 握る →
  トリガーが押し返す。物体ごとに硬さが違う。手を離すと解放。ページを閉じると 3 秒で壁が消える
- ボタン「1/2/3」で物体を手動強制できる（腕を動かさずに壁だけ試す）

```
追従 [ ]  近接で物体が光る [ ]  握って反力 [ ]  物体差 [ ]  解放 [ ]  ページ閉で解除 [ ]
```

### V3 — フェス形態の判断（84_ Step 3）

案A=実機の隣に分身をモニタ表示（Quest なしでも成立）／案B=来場者に Quest を被せて AR（衛生・回転率・補助員 1 名）。
```
判断: A / B / 見送り   理由:
```

---

## 記録の置き場
- 実機ログ md: robotics `TacitCapture/61_` と同じ型で新番号（TEST の結果・つまずき・解決）
- 本書の記入済みコピー: `koch4/work/` は git 管理外なので、robotics 側に写す
- 裁定（決めどころ①〜⑤・VR 形態）: robotics `.claude/cases/koch-4arm-dual.md`・`koch-vr-haptics.md`

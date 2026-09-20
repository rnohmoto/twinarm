# koch4 チェックリスト — Mac で回す手順と記録欄（TEST 0〜6 ＋ VR V0〜V3 ＋ 重さ・編集）

正本: robotics `TacitCapture/76_`（TEST 0〜6）・`58_` 付録A（受入）・`62_` §2（運用レシピ）・
`.claude/handoff/260920_T2_Koch4本_2ペア接続検証.md` §4・`TacitCapture/94_`（VR）。本書はそれらを
koch4 のコマンドに置き換えて 1 枚にしたもの。**合格は、ユーザーが実行して報告した結果だけを事実とする。**
実機を動かす・トルクを抜くコマンドは、その会話でユーザーが明示的に頼んだときだけ実行する。

方針（2026-09-20 裁定）: **2ペアが原則**（`--pair both` 既定・パネル 1 枚・ゲインは全ペア同じ値）。
**VR は別枠**（リーダー 1 本で `--vr`。握手ペアと混ぜない）。

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
lerobot-calibrate --robot.type=koch_follower --robot.port=<F> --robot.id=koch_follower_B \
    --robot.calibration_dir=koch4/config/calibration/koch_follower
lerobot-calibrate --teleop.type=koch_leader --teleop.port=<L> --teleop.id=koch_leader_B \
    --teleop.calibration_dir=koch4/config/calibration/koch_leader
```
（引数名は 0.6.1 の CLI で確認する。動かなければ `--lerobot-cache` で既定の場所を使う）

```
2台目: 目視 [ ] LED 12/12 [ ] スキャン判定 F: A/B/C/D  L: A/B/C/D  ID修正: 要/不要
最終スキャン F 6/6 [ ]  L 6/6 [ ]   較正 F [ ] L [ ]   較正ファイルの所在: config/ / ~/.cache
```

## TEST 1b — 初期位置ズレの数値化（トルク OFF・両腕を手で同じ姿勢に）

同期はするが初期位置がずれる、過負荷停止のあと座標が合わない気がする、のとき:
```bash
uv run python koch4/koch4_calib_offset.py --leader-port <L> --follower-port <F> \
    --leader-id koch_leader_A --follower-id koch_follower_A
```
- 目安: 正規化のズレ ±3% 以内なら OK。超える関節は再キャリブ（'c'+Enter・両腕同姿勢で Enter）
- 過負荷停止（Hardware_Error bit5）は電源を 10 秒抜いて入れ直すまでトルクが入らない。EEPROM の
  homing_offset は残るので座標は変わらないはず。変わっていたらこの表で確定する

```
ズレ%: pan     lift     elbow     wrist_flex     roll     gripper      再キャリブ: 要/不要
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

## TEST 2b — フォロワー gripper の電流上限（過負荷停止→落下の対策）

実機報告（2026-09-20）: ゴルフボールは超ぎりぎり持てるが、摩擦不足で落ちる／角度と加速度によっては
過負荷停止して落ちる。lerobot はフォロワー gripper を Mode 5 にするが Goal_Current を書かないので、
物を掴んで位置偏差が残ると電流が張り付いて過負荷停止に至る。上限を明示して再現性を見る:
```bash
uv run python koch4/koch4_dual_launch.py --pair A --ff gripper --grip-ma 500
```
- 起動時の行 `[grip] フォロワーgripper Goal_Current=500mA(期待500)` を確認
- 同じ物（卓球ボール 40 mm が安全側。ゴルフボールは限界付近）を 10 回持ち上げ、落下と過負荷停止の回数を記録。
  500 で止まらなければ 400／350 へ。握力が足りなければ 600／700 へ（発熱と相談）
- 摩擦: 指先にシリコンテープやグリップテープ。加速度: `--extra "--max-rel 15"` で動きをなだらかに

```
grip-ma=      落下  /10   過負荷停止  /10   温度=   °C   摩擦材: なし/テープ
```

## TEST 3 — ペアB 単独（新機体）

```bash
uv run python koch4/koch4_dual_launch.py --pair B --ff gripper
```
```
追従 [ ]  握り返し [ ]  ペアAとの体感差:
```

## TEST 4 — 2ペア同時（本題・既定の起動形）

```bash
uv run python koch4/koch4_dual_launch.py --ff gripper --grip-ma 500 --csv
```
- パネル 1 枚（8780）に A/B が並ぶ。スライダとモードは両ペアへ同じ値で送られる
- 合格 ①両ペア 30 fps 維持（`work/csv/teleop_*.csv` の t_sec 差分、または teleop ログ）
  ②クロストークなし（片方を動かしても他方のフォロワーが動かない＝ポート取り違え検出）
  ③10 分連続で偽通信断・HW エラーなし（`work/logs/dual_*_teleop.log` の `[robust]`/`[hw]` 行）
- ハブ経由でフレーム落ちが出たら、シリアル 4 本を Mac 直挿しに逃がす（ALOHA 公式の逃げ方）

```
fps A=      B=      クロストーク: なし/あり   10分: 通信断  回・HWエラー  回   ハブ: 可/直挿しへ
パネル: 2ペア表示 [ ]  スライダが両方に効く [ ]
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

## VR — 仮想物体の反力（別枠。リーダー 1 本）

### V0 — 壁だけ（ヘッドセットなし・10 分）

```bash
uv run python koch4/koch4_teleop.py --leader-port <L> --leader-id koch_leader_B \
    --follower-port none --ff vwall --wall ball:45:800:300
```
- 期待: 開き 100→45 までは指が自由（Goal_Current 0）。45 を切った瞬間に `[vwall] 接触` が出て
  トリガーが押し返す。離すと `[vwall] 離脱`。`--wall sponge:60:250:120` で柔らかさが違うか
- 発振（かちかち）したら `--ff-pgain` を 800→400 に下げて再試行

```
ball: 反力 [ ] 発振: なし/あり  sponge: 差を感じる [ ]  温度=   °C  P_Gain読戻=
```

### V-w — 重さ（腕反力・実機未検証。必ず低い上限から）

```bash
uv run python koch4/koch4_teleop.py --leader-port <L> --leader-id koch_leader_B \
    --follower-port none --ff vwall --vw --vw-cap 60 --wall ball:45:800:300:1.5:100
```
- `[vw] ⚠ 仮想重さON` の行が出る。**リーダーから手を離さない**（肩・肘が電流制御になる）
- 握って `[vwall] 接触` の後、腕を前に伸ばすほど肩・肘が「下に引かれる」感じになれば向きは正しい。
  上に押される関節があれば `--vw-invert shoulder_lift` または `elbow_flex`（両方なら `shoulder_lift,elbow_flex`）
- 向きが決まったら `--vw-cap` を 60→120→150 と上げ、`--vw-scale` で重さの倍率を調整（既定 0.12）
- 手を離した瞬間に電流 0 になる（`[vwall] 離脱`）。パネルの「重さ 肩/肘 mA」で確認

```
向き: 肩 正/逆  肘 正/逆   invert=              cap=      scale=      体感: 軽/ちょうど/重   発熱=   °C
```

### V1 — 分身の空間投影（84_ Step 0〜1 と同じ・Quest Pro）

```bash
python koch4/webxr/setup_assets.py                         # 初回のみ(three.js 取得)
uv run python koch4/koch4_vr_bridge.py --sim               # Quest ブラウザで https://<Mac IP>:8443/
```
- 合格: 分身が動き、3 物体が机上に見え、「AR表示」でパススルーに浮かぶ。HUD の「送信: 送信OK」
- ネット: 会場 Wi-Fi はクライアント隔離が濃厚 → **自前ルータかスマホのテザリング**（ゲスト Wi-Fi は使わない）。
  開発者モードなら USB ケーブル＋`--http --port 8080`＋`adb reverse tcp:8080 tcp:8080` で Wi-Fi なしでも動く
- 観客用: Mac のブラウザで `https://<Mac IP>:8443/?spectator=1`（接触を送らない）。ヘッドセットの実画面は
  Meta のキャスト（meta.com/casting・同一 Wi-Fi・同一アカウント）か scrcpy（開発者モード）

```
URL 到達 [ ]  分身が動く [ ]  AR 表示 [ ]  送信OK [ ]  観客ページ [ ]  遅延の体感:
```

### V-e — 編集モード（分身の位置合わせ・物体の置き場所）

- ページで **E**（または「E 編集」）→ 分身が実機とずれている関節の offset/sign、台の位置・向き、
  物体の幅・硬さ・上限・重さを直し、「ここに置く」で指先の位置に物体を置く → **保存**
  （`config/koch4_twin.json` に書かれ、関節の写像は teleop にも送られて重さの計算に使われる）
- **R**（「R 配置リセット」）で物体を置き場所へ戻す（AR/VR 中も画面内のボタンで可）

```
offset: pan    lift    elbow    wrist    roll     台: x     z     yaw     保存 [ ]  リセット動作 [ ]
```

### V2 — 実機リーダー × VR（本題）

```bash
uv run python koch4/koch4_dual_launch.py --pair B --vr B --vw --no-panel
```
- 分身がリーダーに追従 → 分身の指先を机上の物体に寄せる（HUD「物体: 硬いボール」）→ 握る →
  トリガーが押し返し、腕に重さが乗る。物体は指先に付いて動き、放すと机へ落ちる。ページを閉じると 3 秒で壁が消える
- ボタン「1/2/3」で物体を手動強制できる（腕を動かさずに壁だけ試す）

```
追従 [ ]  近接で物体が光る [ ]  握って反力 [ ]  重さ [ ]  物体が付いてくる [ ]  放すと落ちる [ ]  解放 [ ]  ページ閉で解除 [ ]
```

### V3 — フェス形態の判断（84_ Step 3）

案A=実機の隣に分身をモニタ表示（観客ページ or キャスト）／案B=来場者に Quest を被せて AR（衛生・回転率・補助員 1 名）。
```
判断: A / B / 見送り   理由:
```

---

## 記録の置き場
- 実機ログ md: robotics `TacitCapture/61_` と同じ型で新番号（TEST の結果・つまずき・解決）
- 本書の記入済みコピー: `koch4/work/` は git 管理外なので、robotics 側に写す
- 裁定（決めどころ・VR 形態）: robotics `.claude/cases/koch-4arm-dual.md`・`koch-vr-haptics.md`

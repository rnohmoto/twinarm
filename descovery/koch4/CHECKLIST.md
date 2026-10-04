# koch4 チェックリスト — Mac で回す手順と記録欄（TEST 0〜6 ＋ VR V0〜V3 ＋ 重さ・編集）

正本: robotics `TacitCapture/76_`（TEST 0〜6）・`58_` 付録A（受入）・`62_` §2（運用レシピ）・
`.claude/handoff/260920_T2_Koch4本_2ペア接続検証.md` §4・`TacitCapture/94_`（VR）。本書はそれらを
koch4 のコマンドに置き換えて 1 枚にしたもの。**合格は、ユーザーが実行して報告した結果だけを事実とする。**
実機を動かす・トルクを抜くコマンドは、その会話でユーザーが明示的に頼んだときだけ実行する。

入口: `./koch4/start.sh`（check／handshake／host／vr／vr2／sim／rehearse／quest／wifi／manual）。図解つきの手引きは `manual/index.html`（握手＝`handshake.html`・VR＝`vr.html`・Quest の準備＝`quest_setup.html`）。

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

## S0 — 準備状況と通し稽古（腕に触れない・各 Mac で 1 回）

```bash
./koch4/start.sh check       # 自己診断・準備状況（起動できるもの）・ポート一覧・Quest。VR ページの部品も取得する
./koch4/start.sh rehearse    # 仮想のサーボで握手・VR・2 人・無線・較正不一致・通信断・同時起動を通す（約 3 分）
```
- 合格: `check` に「selftest OK」。`rehearse` の最後に「すべて通りました」
- 通し稽古が確かめるのはソフトの経路だけ（起動・モード切替・書込みの順序・終了時のトルク）。腕の挙動は以下の TEST で確かめる
- `rehearse` は固定のポート番号を使うので、実機のセッションを止めてから実行する

```
check: selftest OK [ ]  起動できるもの 握手 A [ ] B [ ]  VR 1 人 [ ]  VR 2 人 [ ]      rehearse: すべて通りました [ ]
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

## W — フォロワー無線（リーダー有線・フォロワー無線・バックアップ有線）

握手の場の PC（Raspberry Pi 4 か手持ちの Windows／Mac）で `koch4_follower_host.py` を動かし、Mac の teleop は
`udp://<その PC>:9101` に目標角を送る。ラズパイは必須ではない（lerobot が動く PC なら同じ）。

### W0 — 経路だけ（ハード無し・Mac 1 台）
```bash
uv run python koch4/koch4_follower_host.py --sim --listen 9101              # 端末 1
uv run python koch4/koch4_teleop.py --leader-port <L> --follower-port udp://127.0.0.1:9101 --ff off   # 端末 2
```
- 合格: teleop に `[link] フォロワー無線 接続 127.0.0.1:9101`、パネルに `age`/`rtt` が出る。端末 1 を止めると 3 秒で通信断→再接続待ち、再起動で復帰
```
接続 [ ]  rtt=   ms  端末1停止→赤帯 [ ] →復帰 [ ]
```

### W1 — 実機フォロワーを Mac 自身の host で（有線と同じ動きになること）
```bash
uv run python koch4/koch4_follower_host.py --port <F> --id koch_follower_A --listen 9101 --grip-ma 500
uv run python koch4/koch4_teleop.py --leader-port <L> --follower-port udp://127.0.0.1:9101 --ff gripper
```
```
追従 [ ]  握り返し [ ]  有線(TEST 2)との体感差:            rtt=   ms
```

### W2 — 握手の場の PC で（5 GHz 自前ルータ越し）
- その PC: `uv sync` 済み・フォロワーの USB・12 V 電源。`hostname -I`（Pi/Linux）や `ipconfig`（Windows）で IP を確認
- **2 台目の MacBook Pro を使う場合（推奨）**: `git clone` → `cd twinarm/descovery && uv sync` → フォロワーを USB で挿す →
  `./koch4/start.sh host A /dev/tty.usbmodemXXXX`（2 本目は `host B <port>`。起動時に、操作者側の設定に書く宛先を表示する。
  中身は `koch4_follower_host.py --port <port> --id koch_follower_A --listen 9101 --grip-ma 500`）。較正 JSON は `koch4/config/calibration/koch_follower/` に置く。Mac 本体はバッテリで動くので握手の場の電源はアームの 12 V だけ
- Mac: config の `follower_host` に `udp://<IP>:9101` → `koch4_dual_launch.py --pair A --ff gripper`
```
IP=              rtt=   ms  age 最大=   ms  欠落=   %  10 分: 赤帯  回・通信断  回
```

### W3 — 切断試験（位置保持と復帰）
- Mac 側の Wi-Fi を 2 秒切る／ルータの電源を 10 秒切る／host PC を持って歩く
- 期待: 0.5 秒で「位置保持」（腕が落ちない）→ 復帰後 1.5 秒で滑らかに追従。赤帯「フォロワー無線 遅延」→ 復帰で消える
```
2秒断: 保持 [ ] 復帰 [ ]   10秒断: 再接続 [ ] 合流ランプ [ ]   腕の落下: なし [ ]
```

### W4 — 有線バックアップへの切替（当日の手順書に入れる）
```bash
uv run python koch4/koch4_dual_launch.py --follower wired --ff gripper    # USB の follower_port を使う
```
```
切替所要:   分   動作 [ ]
```

## U — Quest の USB 接続（1 回だけ・ヘッドセットと Mac の準備）

会場（有明 GYM-EX）のゲスト Wi-Fi は端末間が通らず、会場資料も AP の干渉を警告している → **本命は USB ケーブル＋adb reverse**（電波を使わない・証明書警告なし。`http://localhost` は WebXR の安全なコンテキストなので https が要らない）。退路は自前 5 GHz ルータ＋自己署名 https（Quest Browser で証明書警告を「続行」）。

### U0 — 開発者モードと adb（展示の 1〜2 週間前に）
図解の手順は `manual/quest_setup.html`（Meta アカウントの作り方 → アプリ → 初期設定 → 開発者の登録 → 開発者モード → Mac と接続）。
1. 展示専用の Meta アカウント（会社メール）でヘッドセットを初期設定（Meta アカウントは必須・Facebook は不要）
2. developers.meta.com で開発者組織（チーム）を作り、アカウントを検証（SMS の 2 段階認証か支払い方法。PayPal 不可・18 歳以上）。2026 年は Meta の回答で「組織の検証（Admin Verification＝身分証・2 分）」まで求められた例があるので、そこまで済ませる
3. Meta Horizon アプリ → ヘッドセット → ヘッドセット設定 → 開発者モード ON
4. Mac: `brew install --cask android-platform-tools`（または Meta Quest Developer Hub）
5. データ対応の USB-C ケーブルで Quest を Mac に挿し、ヘッドセットを被って「USB デバッグを許可」→「常に許可」（予備の Mac でも）
6. `uv run python koch4/koch4_quest_usb.py --check` → `✓ <serial>: 接続済み`
7. 給電: Quest Pro のバッテリーは公称 1〜2 時間・付属の 45W ドックで満充電に約 2 時間 → **バッテリーだけでは 1 日もたない**。構成は 4 つ:
   A. USB のまま（Mac から給電。Mac の USB は 45W 充電器より弱く、減りが遅くなるだけのことがある）→ まず 30 分測る（`./koch4/start.sh check` が残量 % を出す）
   B. `./koch4/start.sh wifi`（adb を Wi-Fi に切替・Mac と Quest は同じ自前 5 GHz ルータ）→ `✓ adb reverse` が出たら USB を抜いて 45W 充電器かモバイルバッテリーに差し替える。`✗ adb reverse を張れません` なら、この機体では Wi-Fi 越しの reverse が通らない（古い Quest で報告あり。Meta の文書は USB の手順だけ）→ https の経路（`manual/vr.html` §9）で繋ぐ。ヘッドセットを再起動したらやり直す
   A+. 充電ポート付きの Link 用ケーブル（データは Mac・電気は付属の 45W アダプター。Quest Pro 対応と書かれていない製品が多いので `check` で「接続済み」と「充電中」を確認）
   C. 休憩ごとにドックで充電（満充電まで約 2 時間）
   補足: Mac からゴーグルへ映像は送っていない（関節角度だけ・腕 2 本で毎秒約 40 kB）。2.4 GHz でも量は足りるが、会場の混雑を避けるため 5 GHz を使う

```
Meta アカウント [ ]  組織の検証 [ ]  開発者モード [ ]  adb --check [ ]
30 分の減り: A(USB)   %  B(Wi-Fi＋充電器)   %   採用: A / B / C     adb over Wi-Fi で reverse が張れる [ ]
```

### U1 — 観客用の画面（ヘッドセットの中を見せる）
- MQDH（Meta Quest Developer Hub・macOS 版あり）の Cast を USB で → Cinematic 16:9 を全画面にして HDMI で観客用モニタへ。予備は `koch4_quest_usb.py --port 8444 --mirror`（scrcpy。片目だけ切り出すなら `--scrcpy-args "--crop …"`）
- 代わりに Mac のブラウザで `http://localhost:8444/?spectator=1`（観客ページ＝分身の一人称プレビュー。接触は送らない）

```
Cast [ ]  scrcpy [ ]  観客ページ [ ]  採用:
```

---

## VR — 仮想物体の反力（別枠。リーダー 1 本・2 人目はフォロワー機を手で）

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

### V1 — 分身の空間投影（Quest Pro・USB）

```bash
python koch4/webxr/setup_assets.py                              # 初回のみ(three.js 取得)
uv run python koch4/koch4_vr_bridge.py --sim --http --port 8444  # まずテレオペなし
uv run python koch4/koch4_quest_usb.py --port 8444               # 別ターミナル: reverse を張り Quest Browser で http://localhost:8444/ を開く
```
- 合格: 分身 1 体と 3 物体（スポンジ・ボール・鉄ブロック）が机上に見え、「AR表示（パススルー）」でパススルーに浮かぶ。HUD の「送信OK」。
  Mac のブラウザで同じ URL を開くと一人称プレビュー（V で俯瞰に切替）
- 退路（Wi-Fi）: `--http` を外して https で起動し、自前ルータ経由で `https://<Mac の IP>:8444/` → 証明書警告を「続行」

```
URL 到達 [ ]  AR 表示 [ ]  送信OK [ ]  観客ページ [ ]  遅延の体感:
```

### V-c — 位置合わせ（一人称: 分身を実機のリーダーに重ねる）

```bash
uv run python koch4/koch4_dual_launch.py --pair B --vr B --vw --vr-http --no-panel   # 実機リーダー（テレオペ）＋ブリッジ
uv run python koch4/koch4_quest_usb.py --port 8444
```
- AR 表示にして **C 位置合わせ**（画面下のボタンかキー C）→ 画面中央の案内どおり、コントローラの先端（ハンドトラッキングなら人差し指の先）を実機の**指先の中点**に当ててトリガー。
  腕の姿勢を変えて 2〜3 点（1 点目は位置・2 点目以降で向き。点どうしは 25 cm 以上離す）→ 表示される残差（cm）を見て **確定(保存)**（`config/koch4_twin.json` の台 x/y/z/yaw に入る）
- ずれが残るときは E 編集で台の x/y/z/yaw を 1 cm 刻みに。**G** で分身の見せ方を 実体／半透明／指先だけ に切替（パススルーで実機が見えるので半透明か指先だけが見やすい）
- 境界（ガーディアン）を引き直すと座標が変わるのでやり直す（10 秒）。ずれの目安 ☆: 静止で約 1 cm・動かすと 1〜4 cm（パススルーの遅延 35〜40 ms とテレオペ 30 fps のため）

```
点数:    残差:    cm   見せ方: 実体/半透明/指先   ずれの体感:          ガーディアン引き直し後の再現:
```

### V-e — 編集モード（数値の微調整・物体の置き場所・机）

- ページで **E**（または「E 編集」）→ 腕（B／F）を選び、分身が実機とずれている関節の offset/sign、台の位置・向き、
  物体の幅・硬さ・上限・重さ・**反発（0〜1）**、机の大きさ・AR の分身の見せ方・音 → 「ここに置く」で指先の位置に物体を置く → **保存**
  （`config/koch4_twin.json` に書かれ、関節の写像は各 teleop にも送られて重さの計算に使われる）
- **R**（「R 配置リセット」）で物体を置き場所へ戻す（AR/VR 中も画面内のボタンで可）

```
offset: pan    lift    elbow    wrist    roll     台: x     y     z     yaw     反発: sponge    ball    block    保存 [ ]
```

### V2 — 実機リーダー × VR（本題）

- V-c の起動のまま、分身の指先を机上の物体に寄せる（HUD「物体: ボール」・物体が黄色く光る）→ 握る →
  トリガーが押し返し、腕に重さが乗る。物体は指先に付いて動き、放すと落ちる（上から落とせば跳ねる）。ページを閉じると 3 秒で壁が消える
- ボタン「1/2/3」で物体を手動強制できる（腕を動かさずに壁だけ試す）

```
追従 [ ]  近接で物体が光る [ ]  握って反力 [ ]  重さ [ ]  物体が付いてくる [ ]  放すと落ちる [ ]  解放 [ ]  ページ閉で解除 [ ]
```

### V4 — 投げる・落とす（物理）

- 握ったまま腕を振って放す → 放す直前 0.1 秒の指先速度で飛ぶ（上限 5 m/s）。机に落ちると反発（既定: スポンジ 0.15・ボール 0.65・鉄 0.05）して滑って止まる。机の外へ出れば床（world y=0）まで落ちる。物体同士もぶつかる。当たると音（S で切替）。R で配置に戻す
- 体感で反発・重さを E で調整して保存

```
投げられる [ ]  飛距離の体感:        跳ね方の違い（3 種） [ ]  机から落ちる [ ]  音 [ ]  R で戻る [ ]
```

### V5 — 2 人（同じペアのフォロワー機を手で握る 2 人目）

```bash
uv run python koch4/koch4_dual_launch.py --pair B --vr B --vw --vr-http --vr2 --no-panel
```
- ⚠ 2 人目のフォロワー機は接続直後に腕 5 軸のトルクが抜ける（**手で支えて起動**）。gripper だけ壁（M288 用に電流上限 ×0.45・Kt 0.354）
- 重さ: 肘（M288）は電流制御、**肩（XL430）は PWM＝電圧制御モード**（電流制御が無いため）。握っている間だけ肩のトルクが入る。
  初回は `--extra "--vw-cap 60 --vw-pwm-cap 80"` で起動し、ログ `work/logs/dual_B_hand.log` の
  `[vw] shoulder_lift(XL430) PWM モード: Operating_Mode=16(期待16) / Goal_PWM=0(期待0)` を確認 → 鉄ブロックを握って腕を前に伸ばし、
  肩が下に引かれれば正しい。上に押されるなら `--vw-invert shoulder_lift`。弱ければ `--vw-pwm-cap` を 80→150→250、`--vw-pwm-scale` を 0.5→1.0
- 終了後、フォロワー機を握手に戻す前に `[vw]` の解放（Ctrl+C で自動: Goal_PWM を上限へ戻し Mode 4）を確認。気になるときは AC アダプタを抜き差しすれば初期値に戻る
- ページに分身が 2 体（B＝橙・F＝青）。**Tab** で選択を切替え、F も C 位置合わせ。同じ物体は先に握った腕のもの（もう片方は候補にならない）。ヘッドセットの人が B、2 人目はモニタ（一人称プレビュー／観客ページ）で見る
- 2 人目の壁が硬すぎる／柔らかすぎるときは `--extra "--wall-scale 0.3"` のように倍率を変える（F の teleop にも同じ `--extra` が渡る）

```
F の壁 [ ]  F の重さ 肘 [ ] 肩(PWM) [ ] 向き: 正/逆  pwm-cap=     pwm-scale=     Mode16/Goal_PWM=0 の読み戻し [ ]
2 体の位置合わせ [ ]  受け渡し [ ]  発熱(F gripper／肩):   °C   wall-scale=      握手に戻して肩が保持できる [ ]
```

### V3 — フェス形態の判断（84_ Step 3）

案A=実機の隣に分身をモニタ表示（観客ページ or キャスト）／案B=来場者に Quest を被せて AR（衛生・回転率・補助員 1 名）／案C=2 人（1 人は Quest・1 人はモニタ）。
```
判断: A / B / C / 見送り   理由:
```

---

## 運用メモ — ブラウザ・接続経路・アラート

### 何が何を介して流れるか
| 経路 | 中身 | 手段・頻度 |
|---|---|---|
| リーダー実機 → teleop | 関節角（正規化）・グリッパ開き | USB シリアル 30 fps（teleop プロセスだけがバスを触る） |
| teleop → ブリッジ／パネル | 姿勢・電流・壁の接触状態・重さ電流・温度・**アラート** | UDP（127.0.0.1 の 8765／8769 など）30 fps |
| ブリッジ → VR ページ | `/state`（上記）・`/config`（分身・物体の設定） | ヘッドセットのブラウザが HTTPS で 20 Hz ポーリング |
| VR ページ → ブリッジ → teleop | 接触した物体（幅・硬さ・上限・重さ）・設定の保存 | `POST /contact`／`POST /config` → UDP 8766（壁は 3 秒更新が無いと解除） |
| パネル → teleop | ゲイン・上限・モード・合流・停止（全ペアへ同じ値） | ブラウザ → `/ctl` → UDP（各ペアの制御ポート） |

VR 内のボタン（配置リセット・強制・編集）はページの DOM overlay なので、AR/VR 中もコントローラのポインタで押せる。
リーダー実機そのものが入力装置なので、Quest のコントローラやハンドトラッキングは制御には使わない。

### 接続設定（当日）
1. 本命は USB: Quest を Mac に USB で繋ぎ（開発者モード済み・U0）、`koch4_dual_launch.py … --vr-http` で起動 → `koch4_quest_usb.py --port 8444` が `adb reverse` を張って Quest Browser で `http://localhost:8444/` を開く。会場のゲスト Wi-Fi は使わない（クライアント隔離で届かない）。
   退路: 自前の 5 GHz ルータ（かスマホのテザリング）に Mac と Quest を入れ、`--vr-http` を外して https で起動 → 証明書警告を「続行」
2. `koch4_dual_launch.py --pair B --vr B --vw --vr-http`（2 人なら `--vr2`）→ ブリッジが URL を表示（ペア B は 8444）
3. Quest Browser でページが開いたら「AR表示（パススルー）」→ 初回は C 位置合わせ（V-c）→ G で分身を半透明か指先だけに
4. Mac 側: パネルは `http://127.0.0.1:8780`、観客用は `http://localhost:8444/?spectator=1`（一人称プレビュー）か MQDH の Cast（U1）

### ブラウザ
- パネル・観客ページとも標準の Web 機能だけ（fetch・SSE・Canvas・ES modules・WebGL）。Windows の Chrome と Mac の Safari で動く。
  Safari は自己署名証明書の警告が厳しめ（「詳細を表示 → この Web サイトを閲覧」）。ヘッドセット側は Meta Quest Browser（Chromium）
- VR 表示（AR/VR ボタン）が出るのはヘッドセットのブラウザだけ。PC のブラウザではインライン 3D 表示になる

### アラート（VR の画面上部の赤帯・パネルの赤チップ・teleop のログ）
| 表示 | 意味 | 対処 |
|---|---|---|
| 握り反力が上限 …mA に張り付き | リーダーの Goal_Current が 1 秒以上上限 | 握り込みを緩める。`--ff-cap` を上げるなら発熱と相談 |
| フォロワー握力が上限 …mA 付近 | フォロワー gripper 電流が `--grip-ma` の 9 割以上（1 秒） | 滑り・過負荷停止の前兆。物を軽く／深く持つ |
| ⛔ …gripper エラー停止(過負荷) | Hardware_Error_Status が立った（トルクが入らない） | 電源を 10 秒抜いて入れ直す。頻発なら `--grip-ma` を下げる |
| …°C 発熱／ゲイン減衰／停止 | 60 °C で減衰・65 °C で停止（温度ガード） | 休ませる。体験 1 回ごとに小休止 |
| 重さの電流が上限 | 仮想重さが `--vw-cap` に達した | 物体の重さか倍率を下げる |
| 通信断 → 再接続中 | シリアルのパケット欠け（20 回まで自動） | 手を離して 2 秒待つ。頻発ならケーブル・電源 |
| VR からの更新が途絶えたので壁を解除 | ページが閉じた／Wi-Fi 断 | ページを開き直す |
| テレオペ／ブリッジと未接続 | ページに状態が来ていない | プロセスとネットワークを確認 |

## 記録の置き場
- 実機ログ md: robotics `TacitCapture/61_` と同じ型で新番号（TEST／U／V の結果・つまずき・解決）
- 本書の記入済みコピー: `koch4/work/` は git 管理外なので、robotics 側に写す
- 裁定（決めどころ・VR 形態）: robotics `.claude/cases/koch-4arm-dual.md`・`koch-vr-haptics.md`

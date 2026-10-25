# koch チェックリスト — Mac で回す手順と記録欄（TEST 0〜6 ＋ VR V0〜V3 ＋ 重さ・編集）

正本: robotics `TacitCapture/76_`（TEST 0〜6）・`58_` 付録A（受入）・`62_` §2（運用レシピ）・
`.claude/handoff/260920_T2_Koch4本_2ペア接続検証.md` §4・`TacitCapture/94_`（VR）。本書はそれらを
koch のコマンドに置き換えて 1 枚にしたもの。**合格は、ユーザーが実行して報告した結果だけを事実とする。**
実機を動かす・トルクを抜くコマンドは、その会話でユーザーが明示的に頼んだときだけ実行する。

入口: `./scripts/start.sh`（check／handshake／host／vr／vr2／sim／rehearse／quest／wifi／manual）。図解つきの手引きは `manual/index.html`（握手＝`handshake.html`・VR＝`vr.html`・Quest の準備＝`quest_setup.html`）。

方針（2026-09-20 裁定）: **2ペアが原則**（`--pair both` 既定・パネル 1 枚・ゲインは全ペア同じ値）。
**VR は別枠**（リーダー 1 本で `--vr`。握手ペアと混ぜない）。

前提: MacBook Pro／twinarm `discovery/` で `uv sync` 済み／lerobot 0.6.1（uv.lock）／
電源 Leader=5V・Follower=12V／`max_relative_target=20` は安全リミッタ／終了は Ctrl+C の後に
**両アームの AC アダプタを必ず抜く**（62_ §2.3）。

```
実施日:            実施者:            Mac:            uv sync 日:
ハブ: 型番                 セルフパワー [ ]  4口ともデータ対応 [ ]  ケーブル: データ対応品 [ ]
```

---

## S0 — 準備状況と通し稽古（腕に触れない・各 Mac で 1 回）

```bash
./scripts/start.sh check       # 自己診断・準備状況（起動できるもの）・ポート一覧・Quest。VR ページの部品も取得する
./scripts/start.sh rehearse    # 仮想のサーボで握手・VR・2 人・無線・較正不一致・通信断・同時起動を通す（約 3 分）
```
- 合格: `check` に「selftest OK」。`rehearse` の最後に「すべて通りました」
- 通し稽古が確かめるのはソフトの経路だけ（起動・モード切替・書込みの順序・終了時のトルク）。腕の挙動は以下の TEST で確かめる
- `rehearse` は固定のポート番号を使うので、実機のセッションを止めてから実行する

```
check: selftest OK [x]  起動できるもの 握手 A [x] B [x]  VR 1 人 [x]  VR 2 人 [x]      rehearse: すべて通りました [x]   （10/19 この Mac・4 本接続時・コード変更後の rehearse 7 シナリオ通過）
```

---

## TEST 0 — ポート列挙とシリアル確認（読み取りのみ・4台の識別体制）

```bash
uv run koch-launch --list
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

この構成より前の問題（ID 重複・較正）は 58_／61_ の手順で先に潰す。較正は `config/calibration/` に保存する:

```bash
lerobot-calibrate --robot.type=koch_follower --robot.port=<F> --robot.id=koch_follower_B \
    --robot.calibration_dir=config/calibration/koch_follower
lerobot-calibrate --teleop.type=koch_leader --teleop.port=<L> --teleop.id=koch_leader_B \
    --teleop.calibration_dir=config/calibration/koch_leader
```
（引数名は 0.6.1 の CLI で確認する。動かなければ `--lerobot-cache` で既定の場所を使う）

```
2台目: 目視 [ ] LED 12/12 [ ] スキャン判定 F: A/B/C/D  L: A/B/C/D  ID修正: 要/不要
最終スキャン F 6/6 [ ]  L 6/6 [ ]   較正 F [ ] L [ ]   較正ファイルの所在: config/ / ~/.cache
```

## TEST 1b — 初期位置ズレの数値化（トルク OFF・両腕を手で同じ姿勢に）

同期はするが初期位置がずれる、過負荷停止のあと座標が合わない気がする、のとき:
```bash
uv run koch-calib --leader-port <L> --follower-port <F> \
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
uv run koch-launch --init      # 初回のみ → config/config.json を記入
uv run koch-launch --pair A --ff gripper
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
uv run koch-launch --pair A --ff gripper --grip-ma 500
```
- 起動時の行 `[grip] フォロワーgripper Goal_Current=500mA(期待500)` を確認
- 同じ物（卓球ボール 40 mm が安全側。ゴルフボールは限界付近）を 10 回持ち上げ、落下と過負荷停止の回数を記録。
  500 で止まらなければ 400／350 へ。握力が足りなければ 600／700 へ（発熱と相談）
- 摩擦: 指先にシリコンテープやグリップテープ。加速度: `--extra "--max-rel 15"` で動きをなだらかに

```
grip-ma=      落下  /10   過負荷停止  /10   温度=   °C   摩擦材: なし/テープ
```

## TEST 2c — 役割入替（操作者がフォロワー機 A を持ち、リーダー機 A が追従・10/25）— **不採用**

結果（10/25・2 回）: 1 回目（サーボ既定ゲイン）は肘 −8.8・手首 +11.9 単位の差、2 回目（P1500/D600）でも
リーダー機の肘が前腕を持ち上げられず（M077 の 0.2 N·m が限界）。`koch.sh handshake` は従来の向きに戻した（`SWAP=1` で実験は残る）。
以下は実験の手順として残す。

握り返しの力は操作者が持つ gripper のモーターで決まる。リーダー機は XL330-M077（500 mA で ≈ 0.07 N·m）、
フォロワー機の gripper は XL330-M288（同じ電流で約 2.4 倍・発熱は同じ）。枝 `rn/feat/koch-handshake-swap-roles` の
`./scripts/koch.sh handshake` は既定で入替（中身は `launch.py --pair A --swap-roles`）。設定ファイルは触らない。
```bash
./scripts/koch.sh handshake              # 入替。フォロワー機 A の腕を手で支えてから Enter（接続直後と終了時にトルクが抜ける）
SWAP=0 ./scripts/koch.sh handshake       # 従来の向き（コードごと戻すなら git switch main）
FF_SIGN=-1 ./scripts/koch.sh handshake   # 握ると引き込まれる（向きが逆）なら
GRIP_MA=700 ./scripts/koch.sh handshake  # 来場者側（リーダー機 M077）の握力が弱ければ上げる（発熱は mA で決まる）
FOLLOW_P=2000 FOLLOW_D=800 ./scripts/koch.sh handshake  # 追従が緩ければ腕ゲインを上げる（既定 1500/0/600。振動したら下げる。負で書かない）
```
- 起動時の行: `[A] 役割入替: 操作する腕=フォロワー機 …` ／ `[swap] リーダー機の腕 5 軸にトルクを入れました` ／
  `[hand] 腕 5 軸のトルクを抜きました`。パネルのペア名が「A（役割入替: 操作側=フォロワー機／相手側=リーダー機）」
- 1 回目（10/25 18:50・サーボ既定ゲイン）: 接続と追従は成立。肘 −8.8・手首 +11.9 単位の差、段階的な動き、戻りが遅い（ユーザー）、
  wrist_roll が操作側で −100 に張り付き（1 回転戻す）。→ 腕ゲイン P1500/I0/D600 と腕の温度監視を追加して再試験
- 合格: リーダー機 A がフォロワー機 A に追従する（合流ランプ 1.5 秒・跳ねない）／来場者側の gripper を手で止めると、
  操作者の gripper が**開く向きに**押し返す（逆向き＝引き込まれるなら `FF_SIGN=-1`）／フォロワー機の腕は手で動かせる
  （減速比が大きく重い。離すと落ちるので支える）／パネルの握り返しグラフが従来と同じ形
- 体感を TEST 2（従来）と比べる。同じ FF_KE・FF_CAP で約 2.4 倍の力になるはず。gripper の温度は同じ mA なら同じ速さで上がる
- 来場者側の握力 GRIP_MA=500 は M077 なので従来の約 1/2.4。足りなければ 700／900（上限 1750）

```
追従 [ ]  腕ゲイン P/I/D=    /   /     肘・手首の差=      振動: なし/あり  握り返しの向き: 正/逆(FF_SIGN=-1)  体感(従来比):        手で動かす重さ:        温度(2分後) 操作側=   °C 相手側 肘=   °C
```

## TEST 2d — 握り返しの応答チューニング（従来の向き・10/25）

「上限を上げても遅延するだけ」の正体は差分反射則の固定の応答: 1 フレーム 90 mA のスルー制限（上限 500 まで 6 フレーム≈0.2 秒。
上限を上げるほど到達が遅い）＋平滑化 α 0.3＋停止判定 3 フレーム＋追従中判定 0.4 unit/フレーム（柔らかい手だとフォロワーが
じわじわ動き続け「追従中」と見なされて反力が出ない）。`koch.sh handshake` の既定を ke 25・slew 250・α 0.5・停止判定 1・
追従中判定 1.0 にした。素の teleop の既定は従来どおり。
```bash
./scripts/koch.sh handshake                                  # 10/25 の既定で起動
./scripts/koch.sh tune ff_slew=500 ff_alpha=1.0 ff_stall=0   # 別の端末で、走行中に即応型へ（チャタリングが出たら戻す）
./scripts/koch.sh tune ff_ke=35 ff_cap=700                   # 壁を硬く・強く（cap は発熱: 60 ℃でゲイン半減・65 ℃で停止）
FF_SLEW=90 FF_ALPHA=0.3 FF_STALL=3 FF_MOVE_EPS=0.4 ./scripts/koch.sh handshake   # 10/19 の応答に戻す
FF_STYLE=follow ./scripts/koch.sh handshake                  # 追従壁式: 壁をサーボ内部ループに（判定・平滑化なし＝遅れ 1 フレーム・かちかち無し）。硬さ FF_PGAIN=1200
./scripts/koch.sh tune ff_pgain=2000 ff_cap=700              # follow の硬さと最大を走行中に
```
- 実機（10/25 19:26〜・差分反射式・ke 25）: 握り返しは出る（上限 500 mA まで立つ）が「かちかち」（停止判定の二値が毎フレーム切替）と
  「速度が遅い」（ユーザー）。→ `tune ff_stall=0 ff_alpha=1.0 ff_slew=900` で判定・平滑化を外す／追従壁式へ
- ユーザー裁定（10/25 19:5x）: 応答は 10/19 の値（slew 90・α 0.3・停止判定 3・追従中判定 0.4・ke 15）に戻し、**上限だけ 900 mA**（グリッパだけ強く）。
  `koch.sh handshake` の既定をそうした。熱くなるなら FF_CAP=700。
- 19:54 上限 900・10/19 の応答: それでも「かちかち」＋「遅い」（ユーザー）。判定の二値切替が原因 → 既定を停止判定なし（FF_STALL=0）・平滑化なし（FF_ALPHA=1.0）・
  slew 450（900 まで 2 フレーム）にした。ke 15 と上限 900 はそのまま
- 力の上限は操作側 gripper の XL330-M077 で決まる（上限 500 mA で約 0.07 N·m・指先で約 2 N、900 mA で約 0.13 N·m）。
  握手の握りには 1 桁足りない。ソフトで良くなるのは立ち上がりと硬さの感触まで。強くするなら gripper のサーボを M288（2.4 倍）に
- 見るもの: 握った瞬間から押し返しが立ち上がるまでの遅れ／壁の硬さ（ke）／最大の強さ（cap）／握ったまま静止したときの
  チャタリング（指先の微振動。出たら slew・α を下げるか move_eps を上げる）／自由空間で勝手に力が出ないこと
- 発熱: パネルの温度チップ（操作側 gripper）。cap 700 以上は TEST 5 の 30 分を別に取る

```
ke=    cap=    slew=    α=    stall=    move_eps=    立ち上がり: 速い/普通/遅い  硬さ:        チャタリング: なし/あり  温度(10分後)=   °C
```

## TEST 3 — ペアB 単独（新機体）

```bash
uv run koch-launch --pair B --ff gripper
```
```
追従 [ ]  握り返し [ ]  ペアAとの体感差:
```

## TEST 4 — 2ペア同時（本題・既定の起動形）

```bash
uv run koch-launch --ff gripper --grip-ma 500 --csv
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

握手の場の PC（Raspberry Pi 4 か手持ちの Windows／Mac）で `follower_host.py` を動かし、Mac の teleop は
`udp://<その PC>:9101` に目標角を送る。ラズパイは必須ではない（lerobot が動く PC なら同じ）。

### W0 — 経路だけ（ハード無し・Mac 1 台）
```bash
uv run koch-host --sim --listen 9101              # 端末 1
uv run koch-teleop --leader-port <L> --follower-port udp://127.0.0.1:9101 --ff off   # 端末 2
```
- 合格: teleop に `[link] フォロワー無線 接続 127.0.0.1:9101`、パネルに `age`/`rtt` が出る。端末 1 を止めると 3 秒で通信断→再接続待ち、再起動で復帰
```
接続 [ ]  rtt=   ms  端末1停止→赤帯 [ ] →復帰 [ ]
```

### W1 — 実機フォロワーを Mac 自身の host で（有線と同じ動きになること）
```bash
uv run koch-host --port <F> --id koch_follower_A --listen 9101 --grip-ma 500
uv run koch-teleop --leader-port <L> --follower-port udp://127.0.0.1:9101 --ff gripper
```
```
追従 [ ]  握り返し [ ]  有線(TEST 2)との体感差:            rtt=   ms
```

### W2 — 握手の場の PC で（5 GHz 自前ルータ越し）
- その PC: `uv sync` 済み・フォロワーの USB・12 V 電源。`hostname -I`（Pi/Linux）や `ipconfig`（Windows）で IP を確認
- **2 台目の MacBook Pro を使う場合（推奨）**: `git clone` → `cd twinarm/discovery/koch && uv sync` → フォロワーを USB で挿す →
  `./scripts/start.sh host A /dev/tty.usbmodemXXXX`（2 本目は `host B <port>`。起動時に、操作者側の設定に書く宛先を表示する。
  中身は `follower_host.py --port <port> --id koch_follower_A --listen 9101 --grip-ma 500`）。較正 JSON は `config/calibration/koch_follower/` に置く。Mac 本体はバッテリで動くので握手の場の電源はアームの 12 V だけ
- Mac: config の `follower_host` に `udp://<IP>:9101` → `launch.py --pair A --ff gripper`
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
uv run koch-launch --follower wired --ff gripper    # USB の follower_port を使う
```
```
切替所要:   分   動作 [ ]
```

## U — Quest の USB 接続（1 回だけ・ヘッドセットと Mac の準備）

会場（有明 GYM-EX）のゲスト Wi-Fi は端末間が通らず、会場資料も AP の干渉を警告している → **本命は USB ケーブル＋adb reverse**（電波を使わない・証明書警告なし。`http://localhost` は WebXR の安全なコンテキストなので https が要らない）。退路は自前 5 GHz ルータ＋自己署名 https（Quest Browser で証明書警告を「続行」）。

### U0 — 開発者モードと adb（展示の 1〜2 週間前に）
図解の手順は `manual/quest_setup.html`（Meta アカウントの作り方 → アプリ → 初期設定 → 開発者の登録 → 開発者モード → Mac と接続）。
0. 借用機は組織管理のゴーグルでないこと（管理者が個人アカウントの追加や開発者モードを無効にしていることがある。借り先に確認）。SMS を使うなら国と通信会社の対応を確認
1. 展示専用の Meta アカウント（会社メール）でヘッドセットを初期設定（Meta アカウントは必須・Facebook は不要）
2. developers.meta.com で開発者組織（チーム）を作り、アカウントを検証（SMS の 2 段階認証か支払い方法。PayPal 不可・18 歳以上）。2026 年は Meta の回答で「組織の検証（Admin Verification＝身分証・2 分）」まで求められた例があるので、そこまで済ませる
3. Meta Horizon アプリ → ヘッドセット → ヘッドセット設定 → 開発者モード ON
4. Mac: `brew install --cask android-platform-tools`（または Meta Quest Developer Hub）
5. データ対応の USB-C ケーブルで Quest を Mac に挿し、ヘッドセットを被る → 設定 → 開発者 → **MTP 通知 ON**（Meta の手順 2026-08 版の段階）→「USB デバッグを許可」→「常に許可」（許可は Mac×ヘッドセットの組ごと。予備の Mac・2 台目以降のヘッドセットでも）
6. `uv run koch-quest --check` → `✓ <serial>: 接続済み`
7. 給電: Quest Pro のバッテリーは公称 1〜2 時間・付属の 45W ドックで満充電に約 2 時間 → **バッテリーだけでは 1 日もたない**。構成は 4 つ:
   A. USB のまま（Mac から給電。Mac の USB は 45W 充電器より弱く、減りが遅くなるだけのことがある）→ まず 30 分測る（`./scripts/start.sh check` が残量 % を出す）
   B. `./scripts/start.sh wifi`（adb を Wi-Fi に切替・Mac と Quest は同じ自前 5 GHz ルータ）→ `✓ adb reverse` が出たら USB を抜いて 45W 充電器かモバイルバッテリーに差し替える。`✗ adb reverse を張れません` なら、この機体では Wi-Fi 越しの reverse が通らない（古い Quest で報告あり。Meta の文書は USB の手順だけ）→ https の経路（`manual/vr.html` §9）で繋ぐ。ヘッドセットを再起動したらやり直す
   A+. 充電ポート付きの Link 用ケーブル（データは Mac・電気は付属の 45W アダプター。Quest Pro 対応と書かれていない製品が多いので `check` で「接続済み」と「充電中」を確認）
   C. 休憩ごとにドックで充電（満充電まで約 2 時間）
   補足: Mac からゴーグルへ映像は送っていない（関節角度だけ・腕 2 本で毎秒約 40 kB）。2.4 GHz でも量は足りるが、会場の混雑を避けるため 5 GHz を使う

```
Meta アカウント [x]  組織の検証 [ ]  開発者モード [x]  adb --check [x]  （10/19 Quest Pro 230YC01D8L0376・借用機を出荷時リセット→展示用アカウント。adb reverse 8444 ✓）
30 分の減り: A(USB)   %  B(Wi-Fi＋充電器)   %   採用: A / B / C     adb over Wi-Fi で reverse が張れる [ ]
10/19 USB（ハブ経由・Mac 給電）: 98%→99% を 1 時間、dumpsys は「放電中」と「充電中」が入れ替わる（減らないが安定して充電もしない）
```

### U1 — 観客用の画面（ヘッドセットの中を見せる）
- MQDH（Meta Quest Developer Hub・macOS 版あり）の Cast を USB で → Cinematic 16:9 を全画面にして HDMI で観客用モニタへ。予備は `quest_usb.py --port 8444 --mirror`（scrcpy。片目だけ切り出すなら `--scrcpy-args "--crop …"`）
- 代わりに Mac のブラウザで `http://localhost:8444/?spectator=1`（観客ページ＝分身の一人称プレビュー。接触は送らない）

```
Cast [ ]  scrcpy [ ]  観客ページ [ ]  採用:
```

---

## VR — 仮想物体の反力（別枠。リーダー 1 本・2 人目はフォロワー機を手で）

### V0 — 壁だけ（ヘッドセットなし・10 分）

```bash
uv run koch-teleop --leader-port <L> --leader-id koch_leader_B \
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
uv run koch-teleop --leader-port <L> --leader-id koch_leader_B \
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
uv run python src/webxr/setup_assets.py                              # 初回のみ(three.js 取得)
uv run koch-bridge --sim --http --port 8444  # まずテレオペなし
uv run koch-quest --port 8444               # 別ターミナル: reverse を張り Quest Browser で http://localhost:8444/ を開く
```
- 合格: 分身 1 体と 3 物体（スポンジ・ボール・鉄ブロック）が机上に見え、「AR表示（パススルー）」でパススルーに浮かぶ。HUD の「送信OK」。
  Mac のブラウザで同じ URL を開くと一人称プレビュー（V で俯瞰に切替）
- **AR 中はゴーグルにボタン・文字が出ない**（Quest Browser は DOM overlay を描かない・10/19 実機）。「送信OK」は Mac のブラウザ（`?spectator=1&info=1`）で見る。10/19: Quest Pro で分身が実機に連動することを確認（ユーザー）
- 退路（Wi-Fi）: `--http` を外して https で起動し、自前ルータ経由で `https://<Mac の IP>:8444/` → 証明書警告を「続行」

```
URL 到達 [x]  AR 表示 [x]  送信OK [x]（Mac の DevTools から読んだ値。ゴーグル内に文字は出ない）  観客ページ [ ]  遅延の体感:   （10/19・分身は実機に連動＝ユーザー）
```

### V-c — 位置合わせ（一人称: 分身を実機のリーダーに重ねる）

```bash
uv run koch-launch --pair B --vr B --vw --vr-http --no-panel   # 実機リーダー（テレオペ）＋ブリッジ
uv run koch-quest --port 8444
```
- AR 表示にして **C 位置合わせ**（画面下のボタンかキー C）→ 画面中央の案内どおり、コントローラの先端（ハンドトラッキングなら人差し指の先）を実機の**指先の中点**に当ててトリガー。
  腕の姿勢を変えて 2〜3 点（1 点目は位置・2 点目以降で向き。点どうしは 25 cm 以上離す）→ 表示される残差（cm）を見て **確定(保存)**（`config/twin.json` の台 x/y/z/yaw に入る）
- ずれが残るときは E 編集で台の x/y/z/yaw を 1 cm 刻みに。**G** で分身の見せ方を 実体／半透明／指先だけ に切替（パススルーで実機が見えるので半透明か指先だけが見やすい）
- 境界（ガーディアン）を引き直すと座標が変わるのでやり直す（10 秒）。ずれの目安 ☆: 静止で約 1 cm・動かすと 1〜4 cm（パススルーの遅延 35〜40 ms とテレオペ 30 fps のため）
- **つかんで動かす（10/19 追加・本命）**: 分身の 35 cm 以内でつまんだまま（コントローラはグリップか トリガー長押し）動かすと台が手に付いてくる（平行移動＋yaw）。離して 1.5 秒で保存＝アンカー登録（音 3 つ）。C の点取りはゴーグルでは案内が見えないので Mac から（DevTools で `koch4.startCalib()`）。10/19: つかんで移動・保存・アンカー登録まで動作（ユーザー）
- 土台の回転だけ分身に出ないときは shoulder_pan が ±100 に張り付いている（電源投入後に 1 回転以上回した）。逆向きに 1 回転戻す（10/19 に発生・復帰）
- **空間アンカー（10/19 追加・実機未検証）**: 確定(保存) で台の位置がゴーグルの永続アンカーに登録され、次の AR 開始時に自動で復元される（HUD の「表示:」行に `B:アンカー復元済み`）。確かめる 3 つ: ① AR を終えて（Meta ボタン→ブラウザに戻る）もう一度「AR表示」→ 触らずに重なるか ② ゴーグルを外して別の人が被り直す→重なるか（セッションが続くか、切れても復元されるか） ③ 境界を引き直す／ゴーグルを再起動→復元されるか。復元できないときは `B:アンカー未登録→C` などが出て従来どおり設定値から始まる。`?anchors=0` で無効化、「A アンカー解除」で登録を消す

```
点数:    残差:    cm   見せ方: 実体/半透明/指先   ずれの体感:          ガーディアン引き直し後の再現:
つかんで合わせ [x]  保存の音 3 つ [ ]  向き(yaw)の合わせやすさ:   （10/19: 「だいたい合った」＝ユーザー。保存 9 回・台 x 0.71 y 0.82 z −0.54 yaw 136°。点取り（C）は Mac から入れたが、腕を動かさず 60 点・5 点入って不成立→つかむ方式に切替）
アンカー: 再 AR [ ]  付け替え [ ]  境界引き直し/再起動 [ ]  復元後のずれ:    cm   HUD の表示:
```

### V-e — 編集モード（数値の微調整・物体の置き場所・机）

- ページで **E**（または「E 編集」）→ 腕（B／F）を選び、分身が実機とずれている関節の offset/sign、台の位置・向き、
  物体の幅・硬さ・上限・重さ・**反発（0〜1）**、机の大きさ・**物が乗る面の高さ y**（10/19 追加・ペア B は 0.04）・AR の分身の見せ方・音 → 「ここに置く」で指先の位置に物体を置く → **保存**
  （`config/twin.json` に書かれ、関節の写像は各 teleop にも送られて重さの計算に使われる）
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
uv run koch-launch --pair B --vr B --vw --vr-http --vr2 --no-panel
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
1. 本命は USB: Quest を Mac に USB で繋ぎ（開発者モード済み・U0）、`launch.py … --vr-http` で起動 → `quest_usb.py --port 8444` が `adb reverse` を張って Quest Browser で `http://localhost:8444/` を開く。会場のゲスト Wi-Fi は使わない（クライアント隔離で届かない）。
   退路: 自前の 5 GHz ルータ（かスマホのテザリング）に Mac と Quest を入れ、`--vr-http` を外して https で起動 → 証明書警告を「続行」
2. `launch.py --pair B --vr B --vw --vr-http`（2 人なら `--vr2`）→ ブリッジが URL を表示（ペア B は 8444）
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
- 本書の記入済みコピー: `work/` は git 管理外なので、robotics 側に写す
- 裁定（決めどころ・VR 形態）: robotics `.claude/cases/koch-4arm-dual.md`・`koch-vr-haptics.md`

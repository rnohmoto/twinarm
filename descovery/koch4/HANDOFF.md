# koch4 引き継ぎ — 新しい会話はここから（2026-10-04 夜の時点）

ラーニングフェス（2026-10-26〜27・有明 GYM-EX）向けの Koch 4 本（2 ペア）握手デモ、VR 仮想物体（一人称・投げる・2 人）、無線フォロワーの実装一式。
コードと手順はこのフォルダに全部入っている。背景の根拠（調査・設計書・裁定の記録）は robotics リポジトリ側（§7）。
この文書は「twinarm だけを開いた新しい会話（人でも Claude でも）が、実機の検証から再開できる」ためのもの。

## 1. 新しい会話の起動プロンプト（コピペ）

握手をすぐ動かす（操作者側の Mac。握手用の 2 台目でも同じ文で始められる）:

```
twinarm の descovery/koch4 で握手を実機で立ち上げます。AGENTS.md（実機安全）→ descovery/koch4/HANDOFF.md → manual/handshake.html の順に読んでください。
- いま動いている PC を最初に宣言する（操作者側の Mac か、握手用の 2 台目の Mac か）
- 最初に git pull → cd descovery → ./koch4/start.sh check を実行し、「起動できるもの」の表を私に見せる（腕は動かない）
- 設定ファイルのポートは私が渡す（--list の結果から私が選ぶ。推測しない。リーダーとフォロワーは入替不可）
- 腕が動くコマンド（./koch4/start.sh handshake・host）は、私がこの会話で明示的に頼んだときだけ実行する。実機の挙動は私が実行して報告した結果だけを事実にする
- 進め方: CHECKLIST の S0 → TEST 0 → 1b → 2 → 2b → 3 → 4 →（無線にするなら W0〜W4）を 1 つずつ。各テストの記録欄を埋める
- ペア A だけで握手する日は ./koch4/start.sh handshake --pair A（ペア B の VR と同時に動かせる）
```

VR をすぐ動かす（操作者側の Mac と Quest Pro）:

```
twinarm の descovery/koch4 で VR を実機と Quest Pro で立ち上げます。AGENTS.md（実機安全）→ descovery/koch4/HANDOFF.md → manual/vr.html の順に読んでください。Quest の準備（Meta アカウント・開発者モード）がまだなら manual/quest_setup.html から。
- 最初に git pull → cd descovery → ./koch4/start.sh check を実行し、「起動できるもの」と Quest の接続・電池残量を私に見せる（腕は動かない）
- 腕が動くコマンド（./koch4/start.sh vr・vr2）は、私がこの会話で明示的に頼んだときだけ実行する。ポートは私が渡す。実機の挙動は私の報告だけを事実にする
- Meta アカウントの作成と開発者の登録は私が行う
- 進め方: CHECKLIST の S0 → U0 → V1（./koch4/start.sh sim と Quest で AR 表示）→ V0 → V-w（./koch4/start.sh vr --extra "--vw-cap 60" から）→ V-c → V-e → V2 → V4 → V5（vr2）→ V3 を 1 つずつ
```

どちらの会話にも共通:

- 結果は robotics の TacitCapture に実機ログ md（61_ と同じ型・新番号）として残す
- コードを直すときは twinarm の規則（AGENTS.md・.claude/rules）に従う。descovery のスクリプトは独立（import しない）。制御則を変えたら twinarm/src/twinarm/domain の写しとテストも直す。直したら `./koch4/start.sh rehearse` を通してから実機へ
- 入口は `./koch4/start.sh`（check／handshake／host／vr／vr2／sim／rehearse／quest／wifi／manual）。図解の手引きは `koch4/manual/index.html`

## 2. 現状（何ができていて、何が未検証か）

| 機能 | 実装 | Windows で確認済み | 実機・現場で未検証 |
|---|---|---|---|
| 2 ペア同時テレオペ（`koch4_dual_launch.py` 既定 both・パネル 1 枚・全ペア同じゲイン） | ✅ | dry-run・パネル表示 | TEST 4（30 fps・クロストーク・10 分） |
| 握り返し（`--ff gripper`・spring 既定＝9/4 実機修正 3 点入り／error＝8/04 差分反射） | ✅ | selftest・domain テスト | TEST 2（P_Gain 読み戻し 800） |
| フォロワー gripper 電流上限 `--grip-ma`（過負荷停止→落下の対策） | ✅ | selftest | TEST 2b（効果と値） |
| 初期位置ズレの数値化 `koch4_calib_offset.py` | ✅ | lint | TEST 1b |
| 無線フォロワー（`koch4_follower_host.py`＋`--follower-port udp://`・0.5 s 位置保持・3 s 再接続・`--follower wired` バックアップ） | ✅ | ループバック（sim host）全項目 | W1〜W3（実機・Wi-Fi 越し） |
| VR 仮想壁（`--ff vwall`・Mode 5 に壁・ブリッジ・WebXR ページ） | ✅ | `--sim`＋デスクトップ Chrome | V0（リーダーで壁を感じる）・V1（Quest Pro） |
| VR 重さ（`--vw`・肩と肘に電流・握っている間だけ。Kt は機種別） | ✅ | domain テスト 29 件 | V-w（向きと上限。腕反力は実機未検証） |
| **一人称の位置合わせ（C）**: コントローラ先端／指先で実機の指先を 2〜3 点触って台の x/y/z/yaw を最小二乗で解く・残差表示・保存 | ✅（10/4） | 解法の自己診断 `koch4SelfTest()` が真値に一致（残差 0） | V-c（Quest Pro・実機で残差と体感） |
| **投げる・落とす・跳ねる**（放した瞬間の指先速度・重力・空気抵抗・机／床の反発と摩擦・物体同士の衝突・音） | ✅（10/4） | Chrome（sim）で握る→放す→机に落ちる・編集・視点切替 | V4（実機の握りで投げる。速度上限 5 m/s・反発値の体感） |
| **2 人**（`--vr2`: ペアのフォロワー機を手で動かす 2 人目の入力装置。`--leader-type koch_follower`＝腕トルク抜き・gripper だけ壁・M288 換算。重さは肘＝電流、**肩（XL430）＝PWM モード**（電流制御が無いため電圧で。握っている間だけトルク ON・`--vw-pwm-scale`／`--vw-pwm-cap`）。ブリッジ `--arms B,F`・分身 2 体） | ✅（10/4） | launcher dry-run・bridge `--sim --arms B,F`・ページで 2 体・肩 PWM の書込み順序を `--selftest` で確認（0 を書いてからトルク ON・解放時に Mode 4 と上限 885 へ復帰） | V5（実機。肩 PWM の向きと強さ・握手に戻したとき肩が保持できること） |
| **USB 接続** `koch4_quest_usb.py`（adb reverse＋Quest Browser で `http://localhost:<port>/` を開く・`--mirror`=scrcpy・**`--wifi`＝adb を Wi-Fi に切替えて USB の口を充電器に空ける**・`--check` は電池残量も表示） | ✅（10/4） | `--check`（adb 無しの経路） | U0（開発者モード・adb 疎通・30 分の電池の減り・adb over Wi-Fi で reverse が張れるか） |
| **起動スクリプトと HTML マニュアル**: `start.sh`（check／handshake／host／vr／vr2／sim／rehearse／quest／wifi／manual。後ろに書いた引数はランチャへ渡る）・`manual/index.html`・`handshake.html`・`vr.html`・`quest_setup.html`（図解・関門・困ったとき・参考資料） | ✅（10/4） | Git Bash で全モードを実行（check・dry-run・rehearse）・ブラウザで目視 | Mac で `./koch4/start.sh check` から |
| **準備状況の表示** `koch4_dual_launch.py --doctor`（`check` が呼ぶ）: 設定ファイル・ポートの有無・較正ファイル・VR ページの部品から、握手／VR 1 人／VR 2 人の起動可否を出す。ポートは開かない | ✅（10/4 夜） | 実行して表示を確認 | Mac の実際の設定で |
| **通し稽古** `simbus/`（仮想の Dynamixel バス＋`rehearse.py` の 7 シナリオ）: lerobot 0.6.1 の実物の上で、ランチャ・teleop・ブリッジ・パネル・host を起動から終了まで通し、書込みの順序・EEPROM 書込みの拒否・終了時のトルクを検査する | ✅（10/4 夜） | 7 シナリオ・69 項目すべて通過 | Mac で 1 回（CHECKLIST S0）。**実機の挙動は分からない** |
| **較正の不一致で止まる**: ランチャ配下では lerobot の問い（ENTER でファイルの値を EEPROM に書く）が見えないので、理由を出して終了する。EEPROM は書かない。端末で直接実行したときは従来どおり lerobot が尋ねる | ✅（10/4 夜） | 通し稽古 `mismatch` | 不一致が起きたとき |
| **握手（A）と VR（B）の同時起動**（端末 2 つ・片方を止めても他方は続く）／VR 1 人はリーダーだけ（`--follower none`。フォロワーは繋がなくてよい）／子プロセスが異常終了したら理由（記録の末尾）を画面に出し、ランチャも異常終了を返す | ✅（10/4 夜） | 通し稽古 `fest`・`vr`・`mismatch` | 実機 |
| **VR を 2 人が別々に**（10/5・Mac）: リーダー B とフォロワー B を別々の人が手で動かし、それぞれ自分の空間・自分の主観で掴む。ブリッジを腕ごとに 1 つ（1 人目 8444・2 人目 8443、2 人目の分身の設定は `config/solo_F/`）。入口は `mac/koch.sh`（`handshake`／`vr-leader`／`vr-follower`／`all`／`status`／`stop`）と、同じフォルダの `.command`（Finder でダブルクリック） | ✅（10/5） | — | Mac のブラウザ 2 枚で 2 本同時に動作（ユーザー確認: 2 人目の分身の向きと肩の可動域）。Quest 2 台は未接続。`.command` と `all` からの実機起動は未実施 |
| **摩擦アシスト**（10/5）`--assist elbow_flex,wrist_flex`: 手で動かすフォロワー機は減速比 288:1 で、力を出していなくても重い（ユーザー報告 10/5）。動かしている向きへ小さな電流を足す（上限 `--assist-cap` 既定 25 mA・最大 80）。向きが逆なら `--assist-invert`。肩と土台（XL430）は対象外 | ✅（10/5） | — | domain テスト 7 件・通し稽古 `vr2`（手首の電流が上限以内・リーダーには出ない）。**実機未検証**（軽くなるか・手を離して止まるか・向き） |
| **VR ページの関節名**（10/5）: 分身の各関節に名札（1 土台の回転〜6 グリッパ）、HUD に関節ごとの読み値と分身の角度。`J` で切替 | ✅（10/5） | — | 2 人目の分身合わせで使用（ユーザー） |
| **VR ページの視点 5 種**（10/5）: `V` で 一人称（台座の後ろから腕の伸びる向き）→ 手先カメラ（手首に付いて動く）→ 俯瞰（真上・固定。指先に連動する俯瞰は酔うので廃止＝ユーザー 10/5）→ 接写（物体の向こう側に固定）→ 俯瞰（ドラッグで回す）。`?view=top` `?view=macro` で指定して開ける（モニタを並べるときは `?spectator=1&view=top`） | ✅（10/5） | — | `M` で画面の割り方（主画面＋右に手先カメラと接写の小窓＝既定／手先カメラ｜接写の左右 2 分割／1 画面。`?layout=pip|split|single`）。ヘッドレス Chrome の画面キャプチャで 3 視点と 2 つの割り方の描画を確認。見え方の良し悪しはユーザーの確認待ち |
| 分身の見せ方 G（実体／半透明／指先だけ・AR のとき）・一人称プレビュー V（モニタ用・既定）・観客ページ `?spectator=1` | ✅ | Chrome | — |
| アラート（上限・過負荷停止・温度・重さ上限・通信断・壁解除・未接続→VR 赤帯／パネル赤チップ。2 本のときは `[B]` `[F]` 付き） | ✅ | sim で赤帯目視 | 実機で各条件 |
| 腕 3 軸反力 `--ff arm` | ✅（旧実装） | — | 実機未検証（フェスでは使わない） |

Mac 側から来た分（branch `rn/feat/koch4-vr-objects`・9/20 以降）: A/B 両ペアの較正 JSON（`config/calibration/`）、当てはめ済みの分身設定（`config/koch4_twin.json`＝10/4 に v2 形式へ移行済み・B の関節オフセットはそのまま）、物体の順序＝軽→重。

### 10/4 夜の通し確認で分かったこと（Windows・仮想のサーボ・lerobot 0.6.1 の実物）

GitHub から main を新しく取得して `uv sync` → `./koch4/start.sh check` → `rehearse` を通した。握手 2 ペア・VR 1 人・VR 2 人・無線フォロワー・通信断からの復帰・過負荷エラーの警告は、起動から終了まで例外なく走り、スクリプトが使うレジスタ名はリーダー（XL330-M077）とフォロワー（XL430-W250・XL330-M288）の全機種で lerobot の制御表に存在した。見つかって直した点:

- 新しく取得した直後は VR ページの部品（three.module.js）が無く、ブリッジが黙って終了していた → `check` が取得し、子プロセスの終了理由を画面に出す
- 較正が腕の中の値と違うと、lerobot の問いが記録ファイルに隠れて待ち続けていた → 理由を出して止まる（EEPROM は書かない）
- 無線フォロワーの受け側が、物を握っている間 lerobot の警告を毎フレーム出していた → teleop と同じ抑止を入れた
- VR 1 人は、設定ファイルにフォロワーが書いてあると繋いでいなくても起動できなかった → リーダーだけで起動する
- ランチャの停止が、止める相手を選ばなかった → 自分が起動して生きている teleop だけに停止を送る（握手と VR を別々に止められる）

これは**ソフトの経路**の確認であり、力・摩擦・発熱・実時間は模擬していない。実機の検証（CHECKLIST）は Mac で必要。

## 3. 裁定済み（変えない前提）

- **2 ペア原則**: 既定は 2 ペア同時、パネル 1 枚、ゲインは全ペア同じ値。1 本ずつはいじらない
- **VR は別枠**: 握手 2 ペアと混ぜない。リーダー 1 本の別コマンド（`--pair B --vr B --vw`）。2 人にするときは同じペアのフォロワー機を手で（`--vr2`）
- **フェスの形（ユーザー 10/4）**: アーム A＝握手、アーム B＝VR。端末を 2 つ開き、`./koch4/start.sh handshake --pair A` と `./koch4/start.sh vr` を別々に起動する
- **リーダー有線・フォロワー無線・バックアップ有線**: 握手用 PC は 2 台目の MacBook Pro（Pi は不要）。無線は 5 GHz の自前ルータ。会場のゲスト Wi-Fi は使わない
- **反力の設計**: 壁はサーボ内部ループ（Mode 5: Goal_Position=壁・Goal_Current=上限・P ゲイン=硬さ）に置き、ホストは接触の ON/OFF だけ。重さは握っている間だけ肩・肘に電流。完全モーター模倣は不要
- **一人称**: パススルー AR で実機を見ながら、分身は半透明か指先だけ。位置合わせは VR 内のコントローラ（C）で、数値の微調整だけ編集モード（E）。配置リセットは VR 内のボタン（R）
- **Quest の接続**: 本命 USB＋adb reverse（開発者モードが前提）・退路 自前 5 GHz ルータ＋自己署名 https の警告承諾
- **Quest の電源**: バッテリーは公称 1〜2 時間（45W ドックで満充電 約 2 時間）なので、バッテリーだけでは 1 日もたない。45W 電源アダプターと充電ケーブルは箱に同梱。Mac からは映像を送っておらず（関節角度だけ・腕 2 本で毎秒約 40 kB）、USB の口を充電に回して Wi-Fi で繋いでも体験は変わらない。A: USB のまま 30 分測る／A+: 充電ポート付き Link ケーブル／B: `start.sh wifi`（adb over Wi-Fi）＋45W アダプター／C: ドック補助
- **実機の事実（ユーザー報告 9/20）**: ゴルフボールは超ぎりぎり持てる。摩擦不足で落ちる／角度と加速度によっては過負荷停止して落ちる → `--grip-ma`・摩擦材・`--max-rel` 低め。卓球ボール 40 mm が安全側

## 4. 決めどころ（未裁定）

1. 力覚コードの既定（spring＝仮置き。TEST 2 で error と比べてもよい）
2. Anker ハブの型番（4 口データ対応か・セルフパワーか）
3. 2 本ともフォロワー無線にするか／フォロワー電源はコンセントかバッテリか／自前ルータの機種
4. VR をフェスに積むか・形態（隣にモニタ＝観客ページかキャスト／来場者に Quest）・2 人形態（ヘッドセット 1 台＋モニタ）
5. 物体プリセットの硬さ・重さ・反発（V0・V-w・V4 の体感で `koch4_twin.json` を保存）
6. Meta アカウント（展示専用を会社メールで作るか・開発者組織の検証をどこまで）と給電（A: USB のまま／B: Wi-Fi の adb＋45W 充電器／C: ドック交代。U0 の実測で決める）

## 5. ファイルの地図

| 見たいこと | ファイル |
|---|---|
| まず起動したい・図で見たい | `start.sh`・`manual/index.html`（握手＝`handshake.html`・VR＝`vr.html`・Meta アカウントの作り方から USB 接続まで＝`quest_setup.html`） |
| 腕なしでソフトの通り道を確かめたい | `simbus/`（`README.md`・`rehearse.py`・仮想のバス `dynamixel_sdk/`） |
| 何がどう動くか（一覧・危険度・流れ） | `README.md` |
| 実機での手順と記録欄 | `CHECKLIST.md`（TEST／W／U／V の各節・運用メモ＝経路表・接続手順・ブラウザ・アラート表） |
| 1 ペアの制御（力覚・壁・重さ・無線フォロワー・2 人目の `--leader-type koch_follower`） | `koch4_teleop.py`（冒頭 docstring が仕様） |
| 2 ペア起動・VR 起動・2 人（`--vr2`）・有線／無線切替 | `koch4_dual_launch.py`・`config/koch4_config.example.json` |
| 握手用 PC 側（フォロワー host） | `koch4_follower_host.py`（docstring にプロトコル） |
| VR の見え方・物理・位置合わせ・編集モード | `webxr/index.html`（`DEFAULTS`・`stepPhysics`・`solveCalib`）・`koch4_vr_bridge.py`（`/state` `/config` `/contact`・設定 v2） |
| Quest を USB で繋ぐ・充電しながら使う | `koch4_quest_usb.py`（`--check` → `--port 8444` → `--wifi` → `--mirror`） |
| パネル | `koch4_web_panel.py` |
| 制御則の正本（テスト付き） | `../../twinarm/src/twinarm/domain/gripper_feedback.py`・`link_watchdog.py`（`mise run test` in `twinarm/`） |

## 6. Mac 側の準備（両方の Mac）

```bash
git clone https://github.com/rnohmoto/twinarm.git && cd twinarm/descovery && uv sync
# main に入っている（2026-10-04 マージ）。rn/feat/koch4-vr-fps と rn/feat/koch4-vr-objects も同じ内容なので、どのブランチにいても git pull で届く
./koch4/start.sh check                                # 自己診断・準備状況・ポート・Quest の接続と電池（腕は動かない。VR ページの部品も取得）
./koch4/start.sh rehearse                             # 腕なしの通し稽古（仮想のサーボ・約 3 分）
./koch4/start.sh manual                               # 図解マニュアルを開く
python koch4/webxr/setup_assets.py                    # three.js を取得（VR を使う Mac だけ）
uv run python koch4/koch4_teleop.py --selftest        # ハード無しで制御則を確認
uv run python koch4/koch4_dual_launch.py --list       # ポートとシリアル（読み取りのみ）
uv run python koch4/koch4_dual_launch.py --init       # config/koch4_config.json を作って記入
# 較正 JSON は config/calibration/ に A/B 両ペア分が入っている（9/20 Mac 作成）
# Quest を USB で: brew install --cask android-platform-tools → 開発者モード → uv run python koch4/koch4_quest_usb.py --check
```
握手用 Mac: フォロワーを USB で挿し `./koch4/start.sh host A <port>`（B は `host B <port>`）。起動時に、操作者側の設定に書く宛先（IP と 9101／9102）を表示する。
操作者側 Mac: config の `follower_host` に `udp://<IP>:9101` → `uv run python koch4/koch4_dual_launch.py --ff gripper`。有線に戻す＝`--follower wired`。
Mac でダブルクリック起動: `koch4/mac/` の `.command`（全部起動／握手 A／VR 1 人目／VR 2 人目／動作確認／全部停止）。中身は `./koch4/mac/koch.sh <名前>`。握手は差分反射式（`--ff-style error`・フリーなら戻らない。10/5 にユーザーが戻りバネ式を「フリーなのに戻るのはおかしい」と指摘）。
握手: `./koch4/start.sh handshake`（ペア A だけなら `--pair A` を足す）。VR（USB・1 人）: `./koch4/start.sh vr`（＝`koch4_dual_launch.py --pair B --vr B --vw --vr-http --no-panel --follower none`＋12 秒後に `koch4_quest_usb.py --port 8444`）。
VR（2 人）: `./koch4/start.sh vr2`（`--vr2` を足す。フォロワー機は接続直後にトルクが抜けるので手で支える）。初回の重さは `./koch4/start.sh vr2 --extra "--vw-cap 60 --vw-pwm-cap 80"` で。
`git pull` が `koch4_twin.json` の手元の変更で止まったら `git stash` → `git pull`（旧形式の内容は新形式に読み込める）。

## 7. 根拠が要るときの正本（robotics リポジトリ・user-m-s/robotics）

- `TacitCapture/110_Koch_VR一人称化と2人化_Quest接続_Magicianカメラ_判断材料_v1.md`（10/4: 一人称・投げる・2 人の設計、Quest 接続の判定表、Magician カメラの代替）
- `TacitCapture/94_Koch×VR仮想反力_構成調査と実装計画_v1.md`（VR の答えと設計・§8 に第 2 版と会場ネットワーク）
- `TacitCapture/95_フォロワー無線化_構成設計_v1.md`（無線の設計・エラー耐性・機材比較）
- `web_research/koch4_2609/A_ B_ C_`・`web_research/koch4_2610/`（lerobot／XL330 レジスタ・Quest Pro 接続・ハプティクス理論・WebXR 物理と位置合わせ・Mac カメラの一次情報）
- `TacitCapture/112_`（10/4 夜の通し稽古の記録: 方法・69 項目・見つけて直した 5 点）・`111_` v2（買い物と USB ケーブル）・`web_research/koch4_2610/F_`（Koch 基板の USB は USB 2.0・adb の Wi-Fi 接続ほかの裏取り）
- `.claude/cases/koch-4arm-dual.md`・`koch-vr-haptics.md`（裁定の履歴）・`.claude/handoff/261005_INDEX.md`（最新の引き継ぎ: 握手 T7・VR T8・裏取り T9・資料追加 T10）
- `TacitCapture/76_`（2 ペアのテスト設計）・`58_`／`61_`／`62_`（受入・実機ログ・運用）・`87_`／`85_`（握手アタッチメントの印刷）・`output/cad/print_kit_fest2610/`（印刷キット）

## 8. 安全（AGENTS.md の要約）

動かす・トルクを抜く・ID を書くのはユーザーが明示的に頼んだときだけ（`start.sh handshake／vr／vr2` は腕が動く）。ポートは推測しない。実機の挙動はユーザーの報告だけが事実。終了は Ctrl+C の後に両アームの AC アダプタを抜く。`--leader-type koch_follower`（2 人目）は接続直後に腕のトルクが抜けるので、腕を手で支えてから起動する。

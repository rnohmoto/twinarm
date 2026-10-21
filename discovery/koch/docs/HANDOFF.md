# koch 引き継ぎ — 新しい会話はここから（2026-10-04 夜の時点）

ラーニングフェス（2026-10-26〜27・有明 GYM-EX）向けの Koch 4 本（2 ペア）握手デモ、VR 仮想物体（一人称・投げる・2 人）、無線フォロワーの実装一式。
コードと手順はこのフォルダに全部入っている。背景の根拠（調査・設計書・裁定の記録）は robotics リポジトリ側（§7）。
この文書は「twinarm だけを開いた新しい会話（人でも Claude でも）が、実機の検証から再開できる」ためのもの。

## 1. 新しい会話の起動プロンプト（コピペ）

フェスの Mac 4 台（握手 A・VR 1 人目 B・VR 2 人目 C・卓上 D）に分けた版と、新しい Mac の導入手順は `docs/fest_start_prompts.md`（2026-10-14）。Mac 1 台に会話 1 つで、その Mac の役割のプロンプトを貼る。

握手をすぐ動かす（操作者側の Mac。握手用の 2 台目でも同じ文で始められる）:

```
twinarm の discovery/koch で握手を実機で立ち上げます。AGENTS.md（実機安全）→ discovery/koch/docs/HANDOFF.md → manual/handshake.html の順に読んでください。
- いま動いている PC を最初に宣言する（操作者側の Mac か、握手用の 2 台目の Mac か）
- 最初に git pull → cd discovery/koch → ./scripts/start.sh check を実行し、「起動できるもの」の表を私に見せる（腕は動かない）
- 設定ファイルのポートは私が渡す（--list の結果から私が選ぶ。推測しない。リーダーとフォロワーは入替不可）
- 腕が動くコマンド（./scripts/start.sh handshake・host）は、私がこの会話で明示的に頼んだときだけ実行する。実機の挙動は私が実行して報告した結果だけを事実にする
- 進め方: CHECKLIST の S0 → TEST 0 → 1b → 2 → 2b → 3 → 4 →（無線にするなら W0〜W4）を 1 つずつ。各テストの記録欄を埋める
- ペア A だけで握手する日は ./scripts/start.sh handshake --pair A（ペア B の VR と同時に動かせる）
```

VR をすぐ動かす（操作者側の Mac と Quest Pro）:

```
twinarm の discovery/koch で VR を実機と Quest Pro で立ち上げます。AGENTS.md（実機安全）→ discovery/koch/docs/HANDOFF.md → manual/vr.html の順に読んでください。Quest の準備（Meta アカウント・開発者モード）がまだなら manual/quest_setup.html から。
- 最初に git pull → cd discovery/koch → ./scripts/start.sh check を実行し、「起動できるもの」と Quest の接続・電池残量を私に見せる（腕は動かない）
- 腕が動くコマンド（scripts/3_VR1人目_リーダーB.command＝koch.sh vr-leader、4_VR2人目_フォロワーB.command＝vr-follower。従来の ./scripts/start.sh vr・vr2 も可）は、私がこの会話で明示的に頼んだときだけ実行する。ポートは fest 設定のシリアル番号で解決される（違う腕なら --list で私が直す）。実機の挙動は私の報告だけを事実にする
- Meta アカウントの作成と開発者の登録は私が行う
- 進め方: CHECKLIST の S0 → U0 → V1（./scripts/start.sh sim と Quest で AR 表示）→ V0 → V-w（./scripts/start.sh vr --extra "--vw-cap 60" から）→ V-c → V-e → V2 → V4 → V5（vr2）→ V3 を 1 つずつ
```

どちらの会話にも共通:

- 結果は robotics の TacitCapture に実機ログ md（61_ と同じ型・新番号）として残す
- コードを直すときは twinarm の規則（AGENTS.md・.claude/rules）に従う。koch のコードは `koch` パッケージのモジュール同士で import する（`uv` は discovery/koch で）。制御則は twinarm/src/twinarm/domain が正で、src/koch/feedback.py・link.py はその逐語の写し（tests/test_mirror.py が照合する）。変えるときは twinarm 側を先に直してから写す。直したら `./scripts/start.sh rehearse` を通してから実機へ
- 入口は `./scripts/start.sh`（check／handshake／host／vr／vr2／sim／rehearse／quest／wifi／manual）。図解の手引きは `docs/manual/index.html`

## 2. 現状（何ができていて、何が未検証か）

| 機能 | 実装 | Windows で確認済み | 実機・現場で未検証 |
|---|---|---|---|
| 2 ペア同時テレオペ（`launch.py` 既定 both・パネル 1 枚・全ペア同じゲイン） | ✅ | dry-run・パネル表示 | TEST 4（30 fps・クロストーク・10 分） |
| 握り返し（`--ff gripper`・spring 既定＝9/4 実機修正 3 点入り／error＝8/04 差分反射） | ✅ | selftest・domain テスト | TEST 2（P_Gain 読み戻し 800） |
| フォロワー gripper 電流上限 `--grip-ma`（過負荷停止→落下の対策） | ✅ | selftest | TEST 2b（効果と値） |
| 初期位置ズレの数値化 `calib_offset.py` | ✅ | lint | TEST 1b |
| 無線フォロワー（`follower_host.py`＋`--follower-port udp://`・0.5 s 位置保持・3 s 再接続・`--follower wired` バックアップ） | ✅ | ループバック（sim host）全項目 | W1〜W3（実機・Wi-Fi 越し） |
| VR 仮想壁（`--ff vwall`・Mode 5 に壁・ブリッジ・WebXR ページ） | ✅ | `--sim`＋デスクトップ Chrome | V0（リーダーで壁を感じる）・V1（Quest Pro） |
| VR 重さ（`--vw`・肩と肘に電流・握っている間だけ。Kt は機種別） | ✅ | domain テスト 29 件 | V-w（向きと上限。腕反力は実機未検証） |
| **一人称の位置合わせ（C）**: コントローラ先端／指先で実機の指先を 2〜3 点触って台の x/y/z/yaw を最小二乗で解く・残差表示・保存 | ✅（10/4） | 解法の自己診断 `koch4SelfTest()` が真値に一致（残差 0） | V-c（Quest Pro・実機で残差と体感） |
| **空間アンカー**（10/19）: 確定(保存) で台の位置を WebXR の永続アンカーに登録（`requestPersistentHandle`→localStorage）・AR 開始時に `restorePersistentAnchor` で復元し、毎フレーム「アンカーの姿勢×差分」で台を置く。位置合わせ・編集は差分を持ち直し、保存で登録し直す。失敗時は設定値と C に戻る。`?anchors=0`・「A アンカー解除」 | ✅（10/19・desktop） | 自己診断 `koch4AnchorSelfTest()`（差分の往復・原点が変わっても台がアンカーに追従）・`node --check` | V-c の 3 項目（再 AR・付け替え・境界引き直し） |
| **AR 中の操作はつかんで動かす**（10/19・実機）: Quest Browser は AR 中に DOM overlay（ボタン・文字・案内）を描かない（Meta フォーラムで裏取り）。分身の 35 cm 以内でつまむ／グリップ／トリガー長押し → 台が手に貼り付いて動く（平行移動＋yaw）→ 離して 1.5 秒で保存＝アンカー登録。音で合図（つかむ 1・離す 2・保存 3・点取り 1）。コントローラに基準点の小球と光線。Mac からの遠隔操作は Chrome DevTools（`adb forward tcp:9222 localabstract:chrome_devtools_remote`・`koch4.startCalib()`／`koch4.stopCalib(true)`） | ✅（10/19） | 自己診断 `koch4GrabSelfTest()`・`node --check` | Quest Pro 実機でつかんで移動・保存・アンカー登録まで動作（ユーザー 10/19）。向きの合わせやすさと再 AR の復元は確認中 |
| **土台の回転が 100 に張り付く**（10/19・実機）: shoulder_pan の較正は 0〜4095 の 1 回転・サーボは多回転モード → 電源投入後に土台を 1 回転以上回すと読み値が ±100 に固定され、分身の土台だけ止まる。逆向きに 1 回転戻せば復帰（AC 抜き差しでも） | 現象の記録 | — | 恒久策は未定（teleop で 4096 の剰余を取る／較正の範囲を見直す） |
| **置き場所を寄せ、物が乗る面の高さ `desk.y` を新設**（10/19・実機）: 既定の spot を 22〜27 cm → 12〜15 cm（スポンジ −10/12・ボール 0/15・鉄ブロック 10/12 cm）。`scene.desk.y`（既定 0・`solo_B` は 0.04）で物・置く的・物理の床をその面に。編集モードに「高さ y」。ブリッジも `y` を受けて保存。理由: 腕の全長近くの机の面は実機の指先が届かず「地面より下じゃないと掴めない」（ユーザー 10/19） | ✅（10/19） | `node --check`・ruff・ty | 実機で高さ 4 cm・距離 12〜15 cm の体感はユーザー確認待ち |
| **起動の一本化と Mac からの遠隔操作**（10/19 夜）: `scripts/koch.sh vr-leader`／`vr-follower`（＝`.command`）が、uv sync・設定（無ければ `config/config.fest.json` から）・ポートのシリアル解決・ブリッジ・teleop・Mac の操作ページ（`?spectator=1&info=1`）・Quest の許可待ち→reverse→ページを開く→USB の見張り（`quest_usb.py --wait --watch --devtools`）まで自動。ブリッジに `POST /op`（位置合わせ開始／確定／取消／中止・配置リセット・アンカー解除・再読込）と `POST /status`（ページの点数・残差・アンカー）、`/state` に `cfg_version`（Mac で保存した設定をゴーグルが 1 秒以内に読み直す）。フェスの形は Mac 1＝リーダー B＋Quest #1、Mac 2＝フォロワー B＋Quest #2。要件は `REQUIREMENTS_VR.md`、記録は `LOG_261019_VR.md` | ✅（10/19） | `node --check`・ruff・ty・`bash -n`・`rehearse` 7 シナリオ通過・操作キューの往復（Mac → Quest Browser のページで calib_start/cancel）・操作ページのボタン（headless Chrome で高さ ±1 cm → 版が上がりブリッジが保存） | Mac 2＋Quest #2 の実機、`.command` のダブルクリック起動は未 |
| **投げる・落とす・跳ねる**（放した瞬間の指先速度・重力・空気抵抗・机／床の反発と摩擦・物体同士の衝突・音） | ✅（10/4） | Chrome（sim）で握る→放す→机に落ちる・編集・視点切替 | V4（実機の握りで投げる。速度上限 5 m/s・反発値の体感） |
| **2 人**（`--vr2`: ペアのフォロワー機を手で動かす 2 人目の入力装置。`--leader-type koch_follower`＝腕トルク抜き・gripper だけ壁・M288 換算。重さは肘＝電流、**肩（XL430）＝PWM モード**（電流制御が無いため電圧で。握っている間だけトルク ON・`--vw-pwm-scale`／`--vw-pwm-cap`）。ブリッジ `--arms B,F`・分身 2 体） | ✅（10/4） | launcher dry-run・bridge `--sim --arms B,F`・ページで 2 体・肩 PWM の書込み順序を `--selftest` で確認（0 を書いてからトルク ON・解放時に Mode 4 と上限 885 へ復帰） | V5（実機。肩 PWM の向きと強さ・握手に戻したとき肩が保持できること） |
| **USB 接続** `quest_usb.py`（adb reverse＋Quest Browser で `http://localhost:<port>/` を開く・`--mirror`=scrcpy・**`--wifi`＝adb を Wi-Fi に切替えて USB の口を充電器に空ける**・`--check` は電池残量も表示） | ✅（10/4） | `--check`（adb 無しの経路） | U0（開発者モード・adb 疎通・30 分の電池の減り・adb over Wi-Fi で reverse が張れるか） |
| **起動スクリプトと HTML マニュアル**: `start.sh`（check／handshake／host／vr／vr2／sim／rehearse／quest／wifi／manual。後ろに書いた引数はランチャへ渡る）・`manual/index.html`・`handshake.html`・`vr.html`・`quest_setup.html`（図解・関門・困ったとき・参考資料） | ✅（10/4） | Git Bash で全モードを実行（check・dry-run・rehearse）・ブラウザで目視 | Mac で `./scripts/start.sh check` から |
| **準備状況の表示** `launch.py --doctor`（`check` が呼ぶ）: 設定ファイル・ポートの有無・較正ファイル・VR ページの部品から、握手／VR 1 人／VR 2 人の起動可否を出す。ポートは開かない | ✅（10/4 夜） | 実行して表示を確認 | Mac の実際の設定で |
| **通し稽古** `src/koch/simbus/`（仮想の Dynamixel バス＋`rehearse.py` の 7 シナリオ）: lerobot 0.6.1 の実物の上で、ランチャ・teleop・ブリッジ・パネル・host を起動から終了まで通し、書込みの順序・EEPROM 書込みの拒否・終了時のトルクを検査する | ✅（10/4 夜） | 7 シナリオ・69 項目すべて通過 | Mac で 1 回（CHECKLIST S0）。**実機の挙動は分からない** |
| **較正の不一致で止まる**: ランチャ配下では lerobot の問い（ENTER でファイルの値を EEPROM に書く）が見えないので、理由を出して終了する。EEPROM は書かない。端末で直接実行したときは従来どおり lerobot が尋ねる | ✅（10/4 夜） | 通し稽古 `mismatch` | 不一致が起きたとき |
| **握手（A）と VR（B）の同時起動**（端末 2 つ・片方を止めても他方は続く）／VR 1 人はリーダーだけ（`--follower none`。フォロワーは繋がなくてよい）／子プロセスが異常終了したら理由（記録の末尾）を画面に出し、ランチャも異常終了を返す | ✅（10/4 夜） | 通し稽古 `fest`・`vr`・`mismatch` | 実機 |
| **VR を 2 人が別々に**（10/5・Mac）: リーダー B とフォロワー B を別々の人が手で動かし、それぞれ自分の空間・自分の主観で掴む。ブリッジを腕ごとに 1 つ（1 人目 8444・2 人目 8443、2 人目の分身の設定は `config/solo_F/`）。入口は `scripts/koch.sh`（`handshake`／`vr-leader`／`vr-follower`／`all`／`status`／`stop`）と、同じフォルダの `.command`（Finder でダブルクリック） | ✅（10/5） | — | Mac のブラウザ 2 枚で 2 本同時に動作（ユーザー確認: 2 人目の分身の向きと肩の可動域）。Quest 2 台は未接続。`.command` と `all` からの実機起動は未実施 |
| **摩擦アシスト**（10/5）`--assist elbow_flex,wrist_flex`: 手で動かすフォロワー機は減速比 288:1 で、力を出していなくても重い（ユーザー報告 10/5）。動かしている向きへ小さな電流を足す（上限 `--assist-cap` 既定 25 mA・最大 80）。向きが逆なら `--assist-invert`。肩と土台（XL430）は対象外 | ✅（10/5） | — | domain テスト 7 件・通し稽古 `vr2`（手首の電流が上限以内・リーダーには出ない）。**実機未検証**（軽くなるか・手を離して止まるか・向き） |
| **VR ページの関節名**（10/5）: 分身の各関節に名札（1 土台の回転〜6 グリッパ）、HUD に関節ごとの読み値と分身の角度。`J` で切替 | ✅（10/5） | — | 2 人目の分身合わせで使用（ユーザー） |
| **見せる用の画面が既定**（10/5）: 関節の名札・状態の文字・キー一覧・小窓の名前を出さない（置けた数だけ）。`H` で文字、`J` で名札と関節の読み値。`?info=1` で最初から文字あり | ✅（10/5） | — | 画面キャプチャで確認 |
| **置く的**（10/5）: 机の上に物体と同じ色の丸が 3 つ。放して丸の中で止まれば OK（丸が緑・音）、3 つ置けたらクリアと秒数。`B` で出す／消す、`R` でやり直し。投げ入れる遊び（ゴール）は実機を乱暴に振られるので作らない（ユーザー 10/5）。腕なしの確認は `?demo=place` | ✅（10/5） | — | 仮想の腕のページで `?demo=place` を画面キャプチャ（丸が緑になり「置けた数」が増える）。実機での置きやすさ・丸の大きさと位置は未確認 |
| **VR ページの視点 5 種**（10/5）: `V` で 一人称（台座の後ろから腕の伸びる向き）→ 手先カメラ（手首に付いて動く）→ 俯瞰（真上・固定。指先に連動する俯瞰は酔うので廃止＝ユーザー 10/5）→ 接写（物体の向こう側に固定）→ 俯瞰（ドラッグで回す）。`?view=top` `?view=macro` で指定して開ける（モニタを並べるときは `?spectator=1&view=top`） | ✅（10/5） | — | `M` で画面の割り方（主画面＋右に手先カメラと接写の小窓＝既定／手先カメラ｜接写の左右 2 分割／1 画面。`?layout=pip|split|single`）。ヘッドレス Chrome の画面キャプチャで 3 視点と 2 つの割り方の描画を確認。見え方の良し悪しはユーザーの確認待ち |
| 分身の見せ方 G（実体／半透明／指先だけ・AR のとき）・一人称プレビュー V（モニタ用・既定）・観客ページ `?spectator=1` | ✅ | Chrome | — |
| アラート（上限・過負荷停止・温度・重さ上限・通信断・壁解除・未接続→VR 赤帯／パネル赤チップ。2 本のときは `[B]` `[F]` 付き） | ✅ | sim で赤帯目視 | 実機で各条件 |
| 腕 3 軸反力 `--ff arm` | ✅（旧実装） | — | 実機未検証（フェスでは使わない） |

Mac 側から来た分（branch `rn/feat/koch4-vr-objects`・9/20 以降）: A/B 両ペアの較正 JSON（`config/calibration/`）、当てはめ済みの分身設定（`config/twin.json`＝10/4 に v2 形式へ移行済み・B の関節オフセットはそのまま）、物体の順序＝軽→重。

### 10/4 夜の通し確認で分かったこと（Windows・仮想のサーボ・lerobot 0.6.1 の実物）

GitHub から main を新しく取得して `uv sync` → `./scripts/start.sh check` → `rehearse` を通した。握手 2 ペア・VR 1 人・VR 2 人・無線フォロワー・通信断からの復帰・過負荷エラーの警告は、起動から終了まで例外なく走り、スクリプトが使うレジスタ名はリーダー（XL330-M077）とフォロワー（XL430-W250・XL330-M288）の全機種で lerobot の制御表に存在した。見つかって直した点:

- 新しく取得した直後は VR ページの部品（three.module.js）が無く、ブリッジが黙って終了していた → `check` が取得し、子プロセスの終了理由を画面に出す
- 較正が腕の中の値と違うと、lerobot の問いが記録ファイルに隠れて待ち続けていた → 理由を出して止まる（EEPROM は書かない）
- 無線フォロワーの受け側が、物を握っている間 lerobot の警告を毎フレーム出していた → teleop と同じ抑止を入れた
- VR 1 人は、設定ファイルにフォロワーが書いてあると繋いでいなくても起動できなかった → リーダーだけで起動する
- ランチャの停止が、止める相手を選ばなかった → 自分が起動して生きている teleop だけに停止を送る（握手と VR を別々に止められる）

これは**ソフトの経路**の確認であり、力・摩擦・発熱・実時間は模擬していない。実機の検証（CHECKLIST）は Mac で必要。

## 3. 裁定済み（変えない前提）

- **2 ペア原則**: 既定は 2 ペア同時、パネル 1 枚、ゲインは全ペア同じ値。1 本ずつはいじらない
- **VR は別枠**: 握手 2 ペアと混ぜない。リーダー 1 本の別コマンド（`--pair B --vr B --vw`）。2 人にするときは同じペアのフォロワー機を手で（`--vr2`）
- **フェスの形（ユーザー 10/4）**: アーム A＝握手、アーム B＝VR。端末を 2 つ開き、`./scripts/start.sh handshake --pair A` と `./scripts/start.sh vr` を別々に起動する
- **リーダー有線・フォロワー無線・バックアップ有線**: 握手用 PC は 2 台目の MacBook Pro（Pi は不要）。無線は 5 GHz の自前ルータ。会場のゲスト Wi-Fi は使わない
- **反力の設計**: 壁はサーボ内部ループ（Mode 5: Goal_Position=壁・Goal_Current=上限・P ゲイン=硬さ）に置き、ホストは接触の ON/OFF だけ。重さは握っている間だけ肩・肘に電流。完全モーター模倣は不要
- **一人称**: パススルー AR で実機を見ながら、分身は半透明か指先だけ。位置合わせは VR 内のコントローラ（C）で、数値の微調整だけ編集モード（E）。配置リセットは VR 内のボタン（R）
- **Quest の接続**: 本命 USB＋adb reverse（開発者モードが前提）・退路 自前 5 GHz ルータ＋自己署名 https の警告承諾
- **Quest の電源**: バッテリーは公称 1〜2 時間（45W ドックで満充電 約 2 時間）なので、バッテリーだけでは 1 日もたない。45W 電源アダプターと充電ケーブルは箱に同梱。Mac からは映像を送っておらず（関節角度だけ・腕 2 本で毎秒約 40 kB）、USB の口を充電に回して Wi-Fi で繋いでも体験は変わらない。A: USB のまま 30 分測る／A+: 充電ポート付き Link ケーブル／B: `scripts/start.sh wifi`（adb over Wi-Fi）＋45W アダプター／C: ドック補助
- **実機の事実（ユーザー報告 9/20）**: ゴルフボールは超ぎりぎり持てる。摩擦不足で落ちる／角度と加速度によっては過負荷停止して落ちる → `--grip-ma`・摩擦材・`--max-rel` 低め。卓球ボール 40 mm が安全側

## 4. 決めどころ（未裁定）

1. 力覚コードの既定（spring＝仮置き。TEST 2 で error と比べてもよい）
2. Anker ハブの型番（4 口データ対応か・セルフパワーか）
3. 2 本ともフォロワー無線にするか／フォロワー電源はコンセントかバッテリか／自前ルータの機種
4. VR をフェスに積むか・形態（隣にモニタ＝観客ページかキャスト／来場者に Quest）・2 人形態（ヘッドセット 1 台＋モニタ）
5. 物体プリセットの硬さ・重さ・反発（V0・V-w・V4 の体感で `twin.json` を保存）
6. Meta アカウント（展示専用を会社メールで作るか・開発者組織の検証をどこまで）と給電（A: USB のまま／B: Wi-Fi の adb＋45W 充電器／C: ドック交代。U0 の実測で決める）

## 5. ファイルの地図

| 見たいこと | ファイル |
|---|---|
| まず起動したい・図で見たい | `scripts/*.command`（VR の正本・ダブルクリック）・`scripts/koch.sh`・`scripts/start.sh`・`manual/index.html`（握手＝`handshake.html`・VR＝`vr.html`・Quest＝`quest_setup.html`・借用機の初期化＝`quest_reset.html`） |
| VR の要件と設計判断・10/19 の実機ログ | `REQUIREMENTS_VR.md`・`LOG_261019_VR.md` |
| Quest を繋ぐ・見張る・Mac から遠隔操作 | `quest_usb.py`（`--wait`・`--watch`・`--devtools`）・ブリッジの `/op` `/status`・操作ページ `?spectator=1&info=1` |
| 新しい Mac に配る設定（4 本のシリアル） | `config/config.fest.json`（`koch.sh` が `config.json` の無いときに写す） |
| 腕なしでソフトの通り道を確かめたい | `src/koch/simbus/`（`README.md`・`rehearse.py`・仮想のバス `dynamixel_sdk/`） |
| 何がどう動くか（一覧・危険度・流れ） | `README.md` |
| 実機での手順と記録欄 | `CHECKLIST.md`（TEST／W／U／V の各節・運用メモ＝経路表・接続手順・ブラウザ・アラート表） |
| 1 ペアの制御（力覚・壁・重さ・無線フォロワー・2 人目の `--leader-type koch_follower`） | `teleop.py`（冒頭 docstring が仕様） |
| 2 ペア起動・VR 起動・2 人（`--vr2`）・有線／無線切替 | `launch.py`・`config/config.example.json` |
| 握手用 PC 側（フォロワー host） | `follower_host.py`（docstring にプロトコル） |
| VR の見え方・物理・位置合わせ・編集モード | `src/webxr/index.html`（`DEFAULTS`・`stepPhysics`・`solveCalib`）・`bridge.py`（`/state` `/config` `/contact`・設定 v2） |
| Quest を USB で繋ぐ・充電しながら使う | `quest_usb.py`（`--check` → `--port 8444` → `--wifi` → `--mirror`） |
| パネル | `panel.py` |
| 制御則の正本（テスト付き） | `../../twinarm/src/twinarm/domain/gripper_feedback.py`・`link_watchdog.py`（`mise run test` in `twinarm/`） |

## 6. Mac 側の準備（両方の Mac）

```bash
git clone https://github.com/rnohmoto/twinarm.git && cd twinarm/discovery/koch && uv sync
# main に入っている（2026-10-04 マージ）。rn/feat/koch4-vr-fps と rn/feat/koch4-vr-objects も同じ内容なので、どのブランチにいても git pull で届く
./scripts/start.sh check                                # 自己診断・準備状況・ポート・Quest の接続と電池（腕は動かない。VR ページの部品も取得）
./scripts/start.sh rehearse                             # 腕なしの通し稽古（仮想のサーボ・約 3 分）
./scripts/start.sh manual                               # 図解マニュアルを開く
uv run python src/webxr/setup_assets.py                    # three.js を取得（VR を使う Mac だけ）
uv run koch-teleop --selftest        # ハード無しで制御則を確認
uv run koch-launch --list       # ポートとシリアル（読み取りのみ）
uv run koch-launch --init       # config/config.json を作って記入
# 較正 JSON は config/calibration/ に A/B 両ペア分が入っている（9/20 Mac 作成）
# Quest を USB で: brew install --cask android-platform-tools → 開発者モード → uv run koch-quest --check
```
握手用 Mac: フォロワーを USB で挿し `./scripts/start.sh host A <port>`（B は `host B <port>`）。起動時に、操作者側の設定に書く宛先（IP と 9101／9102）を表示する。
操作者側 Mac: config の `follower_host` に `udp://<IP>:9101` → `uv run koch-launch --ff gripper`。有線に戻す＝`--follower wired`。
Mac でダブルクリック起動: `scripts/` の `.command`（最初の確認／全部起動／握手 A／VR 1 人目／VR 2 人目／Quest 再接続／動作確認／全部停止）。中身は `./scripts/koch.sh <名前>`。**10/19 から VR の正本は `koch.sh vr-leader`／`vr-follower`**: uv sync・設定（無ければ `config.fest.json` から）・ポートのシリアル解決・ブリッジ・teleop・Mac の操作ページ・Quest の許可待ち→reverse→ページを開く→USB の見張り、まで自動。フェスは MacBook Pro 1＝リーダー B＋Quest #1、MacBook Pro 2＝フォロワー B＋Quest #2（Mac 同士の通信なし）。握手は差分反射式（`--ff-style error`・フリーなら戻らない。10/5 にユーザーが戻りバネ式を「フリーなのに戻るのはおかしい」と指摘）。
握手: `./scripts/start.sh handshake`（ペア A だけなら `--pair A` を足す）。VR（USB・1 人）: `./scripts/start.sh vr`（＝`launch.py --pair B --vr B --vw --vr-http --no-panel --follower none`＋12 秒後に `quest_usb.py --port 8444`）。
VR（2 人）: `./scripts/start.sh vr2`（`--vr2` を足す。フォロワー機は接続直後にトルクが抜けるので手で支える）。初回の重さは `./scripts/start.sh vr2 --extra "--vw-cap 60 --vw-pwm-cap 80"` で。
`git pull` が `twin.json` の手元の変更で止まったら `git stash` → `git pull`（旧形式の内容は新形式に読み込める）。

## 7. 根拠が要るときの正本（robotics リポジトリ・user-m-s/robotics）

- `TacitCapture/110_Koch_VR一人称化と2人化_Quest接続_Magicianカメラ_判断材料_v1.md`（10/4: 一人称・投げる・2 人の設計、Quest 接続の判定表、Magician カメラの代替）
- `TacitCapture/94_Koch×VR仮想反力_構成調査と実装計画_v1.md`（VR の答えと設計・§8 に第 2 版と会場ネットワーク）
- `TacitCapture/95_フォロワー無線化_構成設計_v1.md`（無線の設計・エラー耐性・機材比較）
- `web_research/koch4_2609/A_ B_ C_`・`web_research/koch4_2610/`（lerobot／XL330 レジスタ・Quest Pro 接続・ハプティクス理論・WebXR 物理と位置合わせ・Mac カメラの一次情報）
- `TacitCapture/112_`（10/4 夜の通し稽古の記録: 方法・69 項目・見つけて直した 5 点）・`111_` v2（買い物と USB ケーブル）・`web_research/koch4_2610/F_`（Koch 基板の USB は USB 2.0・adb の Wi-Fi 接続ほかの裏取り）
- `.claude/cases/koch-4arm-dual.md`・`koch-vr-haptics.md`（裁定の履歴）・`.claude/handoff/261005_INDEX.md`（最新の引き継ぎ: 握手 T7・VR T8・裏取り T9・資料追加 T10）
- `TacitCapture/76_`（2 ペアのテスト設計）・`58_`／`61_`／`62_`（受入・実機ログ・運用）・`87_`／`85_`（握手アタッチメントの印刷）・`output/cad/print_kit_fest2610/`（印刷キット）

## 8. 安全（AGENTS.md の要約）

動かす・トルクを抜く・ID を書くのはユーザーが明示的に頼んだときだけ（`scripts/start.sh handshake／vr／vr2` は腕が動く）。ポートは推測しない。実機の挙動はユーザーの報告だけが事実。終了は Ctrl+C の後に両アームの AC アダプタを抜く。`--leader-type koch_follower`（2 人目）は接続直後に腕のトルクが抜けるので、腕を手で支えてから起動する。

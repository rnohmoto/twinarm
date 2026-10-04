# koch4 引き継ぎ — 新しい会話はここから（2026-10-04 時点）

ラーニングフェス（2026-10-26〜27・有明 GYM-EX）向けの Koch 4 本（2 ペア）握手デモ、VR 仮想物体（一人称・投げる・2 人）、無線フォロワーの実装一式。
コードと手順はこのフォルダに全部入っている。背景の根拠（調査・設計書・裁定の記録）は robotics リポジトリ側（§7）。
この文書は「twinarm だけを開いた新しい会話（人でも Claude でも）が、実機の検証から再開できる」ためのもの。

## 1. 新しい会話の起動プロンプト（コピペ）

```
twinarm の descovery/koch4 を実機で検証します。最初に AGENTS.md（実機安全）→ descovery/koch4/HANDOFF.md → README.md → CHECKLIST.md の順に読んでください。
- いま動いている PC を最初に宣言する（操作者側の Mac か、握手用の 2 台目 Mac か）
- 実機を動かす・トルクを抜く・モーター ID を書くコマンドは、私がこの会話で明示的に頼んだときだけ実行する。ポートは推測しない（私が渡す。リーダーとフォロワーは入替不可）。実機の挙動は私が実行して報告した結果だけを事実にする
- 入口は `./koch4/start.sh`（check／handshake／vr／vr2／sim／quest／wifi／manual）。図解の手引きは `koch4/manual/index.html`（握手・VR）
- 進め方: CHECKLIST の TEST 0 → 1b → 2 → 2b → 3 → 4 → W0〜W4（握手）／U0 → V0 → V-w → V1 → V-c → V-e → V2 → V4 → V5（VR）を 1 つずつ。各テストの記録欄を埋め、結果は robotics の TacitCapture に実機ログ md（61_ と同じ型・新番号）として残す
- コードを直すときは twinarm の規則（AGENTS.md・.claude/rules）に従う。descovery のスクリプトは独立（import しない）。制御則を変えたら twinarm/src/twinarm/domain の写しとテストも直す
```

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
| **起動スクリプトと HTML マニュアル**: `start.sh`（check／handshake／vr／vr2／sim／quest／wifi／manual）・`manual/index.html`・`handshake.html`・`vr.html`（図解・関門・困ったとき・参考資料） | ✅（10/4） | `bash -n`・ブラウザで目視 | Mac で `./koch4/start.sh check` から |
| 分身の見せ方 G（実体／半透明／指先だけ・AR のとき）・一人称プレビュー V（モニタ用・既定）・観客ページ `?spectator=1` | ✅ | Chrome | — |
| アラート（上限・過負荷停止・温度・重さ上限・通信断・壁解除・未接続→VR 赤帯／パネル赤チップ。2 本のときは `[B]` `[F]` 付き） | ✅ | sim で赤帯目視 | 実機で各条件 |
| 腕 3 軸反力 `--ff arm` | ✅（旧実装） | — | 実機未検証（フェスでは使わない） |

Mac 側から来た分（branch `rn/feat/koch4-vr-objects`・9/20 以降）: A/B 両ペアの較正 JSON（`config/calibration/`）、当てはめ済みの分身設定（`config/koch4_twin.json`＝10/4 に v2 形式へ移行済み・B の関節オフセットはそのまま）、物体の順序＝軽→重。

## 3. 裁定済み（変えない前提）

- **2 ペア原則**: 既定は 2 ペア同時、パネル 1 枚、ゲインは全ペア同じ値。1 本ずつはいじらない
- **VR は別枠**: 握手 2 ペアと混ぜない。リーダー 1 本の別コマンド（`--pair B --vr B --vw`）。2 人にするときは同じペアのフォロワー機を手で（`--vr2`）
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
git clone https://github.com/rnohmoto/twinarm.git && cd twinarm && git checkout rn/feat/koch4-vr-fps && cd descovery && uv sync
# 既に rn/feat/koch4-vr-objects で作業している Mac は git pull だけでよい（同じ内容を入れてある）
./koch4/start.sh check                                # 自己診断・ポート・Quest の接続と電池（腕は動かない）
./koch4/start.sh manual                               # 図解マニュアルを開く
python koch4/webxr/setup_assets.py                    # three.js を取得（VR を使う Mac だけ）
uv run python koch4/koch4_teleop.py --selftest        # ハード無しで制御則を確認
uv run python koch4/koch4_dual_launch.py --list       # ポートとシリアル（読み取りのみ）
uv run python koch4/koch4_dual_launch.py --init       # config/koch4_config.json を作って記入
# 較正 JSON は config/calibration/ に A/B 両ペア分が入っている（9/20 Mac 作成）
# Quest を USB で: brew install --cask android-platform-tools → 開発者モード → uv run python koch4/koch4_quest_usb.py --check
```
握手用 Mac: フォロワーを USB で挿し `ipconfig getifaddr en0` で IP → `uv run python koch4/koch4_follower_host.py --port <serial> --id koch_follower_A --listen 9101 --grip-ma 500`。
操作者側 Mac: config の `follower_host` に `udp://<IP>:9101` → `uv run python koch4/koch4_dual_launch.py --ff gripper`。有線に戻す＝`--follower wired`。
握手: `./koch4/start.sh handshake`。VR（USB・1 人）: `./koch4/start.sh vr`（＝`koch4_dual_launch.py --pair B --vr B --vw --vr-http --no-panel`＋12 秒後に `koch4_quest_usb.py --port 8444`）。
VR（2 人）: `./koch4/start.sh vr2`（`--vr2` を足す。フォロワー機は接続直後にトルクが抜けるので手で支える）。初回の重さは `--extra "--vw-cap 60 --vw-pwm-cap 80"` で。
`git pull` が `koch4_twin.json` の手元の変更で止まったら `git stash` → `git pull`（旧形式の内容は新形式に読み込める）。

## 7. 根拠が要るときの正本（robotics リポジトリ・user-m-s/robotics）

- `TacitCapture/110_Koch_VR一人称化と2人化_Quest接続_Magicianカメラ_判断材料_v1.md`（10/4: 一人称・投げる・2 人の設計、Quest 接続の判定表、Magician カメラの代替）
- `TacitCapture/94_Koch×VR仮想反力_構成調査と実装計画_v1.md`（VR の答えと設計・§8 に第 2 版と会場ネットワーク）
- `TacitCapture/95_フォロワー無線化_構成設計_v1.md`（無線の設計・エラー耐性・機材比較）
- `web_research/koch4_2609/A_ B_ C_`・`web_research/koch4_2610/`（lerobot／XL330 レジスタ・Quest Pro 接続・ハプティクス理論・WebXR 物理と位置合わせ・Mac カメラの一次情報）
- `.claude/cases/koch-4arm-dual.md`・`koch-vr-haptics.md`（裁定の履歴）・`.claude/handoff/261004_T6_*.md`（最新の引き継ぎ）
- `TacitCapture/76_`（2 ペアのテスト設計）・`58_`／`61_`／`62_`（受入・実機ログ・運用）・`87_`／`85_`（握手アタッチメントの印刷）・`output/cad/print_kit_fest2610/`（印刷キット）

## 8. 安全（AGENTS.md の要約）

動かす・トルクを抜く・ID を書くのはユーザーが明示的に頼んだときだけ（`start.sh handshake／vr／vr2` は腕が動く）。ポートは推測しない。実機の挙動はユーザーの報告だけが事実。終了は Ctrl+C の後に両アームの AC アダプタを抜く。`--leader-type koch_follower`（2 人目）は接続直後に腕のトルクが抜けるので、腕を手で支えてから起動する。

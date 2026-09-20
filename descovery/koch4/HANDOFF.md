# koch4 引き継ぎ — 新しい会話はここから（2026-09-20 時点）

ラーニングフェス（2026-10-26〜27・有明 GYM-EX）向けの Koch 4 本（2 ペア）握手デモ、VR 仮想反力、無線フォロワーの実装一式。
コードと手順はこのフォルダに全部入っている。背景の根拠（調査・設計書・裁定の記録）は robotics リポジトリ側（§7）。
この文書は「twinarm だけを開いた新しい会話（人でも Claude でも）が、実機の検証から再開できる」ためのもの。

## 1. 新しい会話の起動プロンプト（コピペ）

```
twinarm の descovery/koch4 を実機で検証します。最初に AGENTS.md（実機安全）→ descovery/koch4/HANDOFF.md → README.md → CHECKLIST.md の順に読んでください。
- いま動いている PC を最初に宣言する（操作者側の Mac か、握手用の 2 台目 Mac か）
- 実機を動かす・トルクを抜く・モーター ID を書くコマンドは、私がこの会話で明示的に頼んだときだけ実行する。ポートは推測しない（私が渡す。リーダーとフォロワーは入替不可）。実機の挙動は私が実行して報告した結果だけを事実にする
- 進め方: CHECKLIST の TEST 0 → 1b → 2 → 2b → 3 → 4 → W0〜W4 → V0 → V-w → V1 → V-e → V2 を 1 つずつ。各テストの記録欄を埋め、結果は robotics の TacitCapture に実機ログ md（61_ と同じ型・新番号）として残す
- コードを直すときは twinarm の規則（AGENTS.md・.claude/rules）に従う。descovery のスクリプトは独立（import しない）。制御則を変えたら twinarm/src/twinarm/domain の写しとテストも直す
```

## 2. 現状（何ができていて、何が未検証か）

| 機能 | 実装 | Windows で確認済み | 実機・現場で未検証 |
|---|---|---|---|
| 2 ペア同時テレオペ（`koch4_dual_launch.py` 既定 both・パネル 1 枚・全ペア同じゲイン） | ✅ | dry-run・パネル表示 | TEST 4（30 fps・クロストーク・10 分） |
| 握り返し（`--ff gripper`・spring 既定＝9/4 実機修正 3 点入り／error＝8/04 差分反射） | ✅ | selftest・domain テスト | TEST 2（P_Gain 読み戻し 800） |
| フォロワー gripper 電流上限 `--grip-ma`（過負荷停止→落下の対策） | ✅ | selftest | TEST 2b（効果と値） |
| 初期位置ズレの数値化 `koch4_calib_offset.py` | ✅ | lint | TEST 1b |
| 無線フォロワー（`koch4_follower_host.py`＋`--follower-port udp://`・0.5 s 位置保持・3 s 再接続・遅延/欠落表示・`--follower wired` バックアップ） | ✅ | ループバック（sim host）全項目 | W1〜W3（実機・Wi-Fi 越し） |
| VR 仮想壁（`--ff vwall`・Mode 5 に壁・ブリッジ・WebXR ページ） | ✅ | `--sim`＋デスクトップ Chrome | V0（リーダーで壁を感じる）・V1（Quest Pro） |
| VR 重さ（`--vw`・肩と肘に電流・握っている間だけ） | ✅ | domain テスト | V-w（向きと上限。腕反力は実機未検証） |
| VR 編集モード（関節オフセット・台・物体・保存）・配置リセット・持ち上げ／落下・観客ページ | ✅ | Chrome で目視 | V-e・V2 |
| アラート（上限・過負荷停止・温度・重さ上限・通信断・壁解除・未接続→VR 赤帯／パネル赤チップ） | ✅ | sim で赤帯目視 | 実機で各条件 |
| 腕 3 軸反力 `--ff arm` | ✅（旧実装） | — | 実機未検証（フェスでは使わない） |

## 3. 裁定済み（変えない前提）

- **2 ペア原則**: 既定は 2 ペア同時、パネル 1 枚、ゲインは全ペア同じ値。1 本ずつはいじらない
- **VR は別枠**: 握手 2 ペアと混ぜない。リーダー 1 本の別コマンド（`--pair B --vr B --vw`）
- **リーダー有線・フォロワー無線・バックアップ有線**: 握手用 PC は 2 台目の MacBook Pro（Pi は不要）。無線は 5 GHz の自前ルータ。会場のゲスト Wi-Fi は使わない（端末間が通らない前提・会場資料も AP 干渉を警告）
- **反力の設計**: 壁はサーボ内部ループ（Mode 5: Goal_Position=壁・Goal_Current=上限・P ゲイン=硬さ）に置き、ホストは接触の ON/OFF だけ。重さは握っている間だけ肩・肘に電流。完全モーター模倣は不要
- **校正は編集モード（E）＋ `config/koch4_twin.json`**、配置リセットは VR 内のボタン（R）
- **実機の事実（ユーザー報告 9/20）**: ゴルフボールは超ぎりぎり持てる。摩擦不足で落ちる／角度と加速度によっては過負荷停止して落ちる → `--grip-ma`・摩擦材・`--max-rel` 低め。卓球ボール 40 mm が安全側

## 4. 決めどころ（未裁定）

1. 力覚コードの既定（spring＝仮置き。TEST 2 で error と比べてもよい）
2. Anker ハブの型番（4 口データ対応か・セルフパワーか）
3. 2 本ともフォロワー無線にするか（Mac 2 で host を 2 つ）／フォロワー電源はコンセントかバッテリか／自前ルータの機種
4. VR をフェスに積むか・形態（隣にモニタ＝観客ページ／来場者に Quest）
5. 物体プリセットの硬さ・重さ（V0・V-w の体感で `koch4_twin.json` を保存）

## 5. ファイルの地図

| 見たいこと | ファイル |
|---|---|
| 何がどう動くか（一覧・危険度・流れ） | `README.md` |
| 実機での手順と記録欄 | `CHECKLIST.md`（TEST／W／V の各節・運用メモ＝経路表・接続手順・ブラウザ・アラート表） |
| 1 ペアの制御（力覚・壁・重さ・無線フォロワー） | `koch4_teleop.py`（冒頭 docstring が仕様） |
| 2 ペア起動・VR 起動・有線／無線切替 | `koch4_dual_launch.py`・`config/koch4_config.example.json` |
| 握手用 PC 側（フォロワー host） | `koch4_follower_host.py`（docstring にプロトコル） |
| VR の見え方・物体・編集モード | `webxr/index.html`（`DEFAULTS` と `OBJECTS`）・`koch4_vr_bridge.py`（`/state` `/config` `/contact`） |
| パネル | `koch4_web_panel.py` |
| 制御則の正本（テスト付き） | `../../twinarm/src/twinarm/domain/gripper_feedback.py`・`link_watchdog.py`（`mise run test` in `twinarm/`） |

## 6. Mac 側の準備（両方の Mac）

```bash
git clone https://github.com/rnohmoto/twinarm.git && cd twinarm/descovery && uv sync
python koch4/webxr/setup_assets.py                    # three.js を取得（VR を使う Mac だけ）
uv run python koch4/koch4_teleop.py --selftest        # ハード無しで制御則を確認
uv run python koch4/koch4_dual_launch.py --list       # ポートとシリアル（読み取りのみ）
uv run python koch4/koch4_dual_launch.py --init       # config/koch4_config.json を作って記入
# 較正 JSON: config/calibration/koch_follower/<id>.json, koch_leader/<id>.json（~/.cache からコピー可・--lerobot-cache でも可）
```
握手用 Mac: フォロワーを USB で挿し `ipconfig getifaddr en0` で IP → `uv run python koch4/koch4_follower_host.py --port <serial> --id koch_follower_A --listen 9101 --grip-ma 500`。
操作者側 Mac: config の `follower_host` に `udp://<IP>:9101` → `uv run python koch4/koch4_dual_launch.py --ff gripper`。有線に戻す＝`--follower wired`。

## 7. 根拠が要るときの正本（robotics リポジトリ・user-m-s/robotics）

- `TacitCapture/94_Koch×VR仮想反力_構成調査と実装計画_v1.md`（VR の答えと設計・§8 に第 2 版と会場ネットワーク）
- `TacitCapture/95_フォロワー無線化_構成設計_v1.md`（無線の設計・エラー耐性・機材比較）
- `web_research/koch4_2609/A_ B_ C_`（lerobot 0.6.1／XL330 レジスタ・Quest Pro 接続・ハプティクス理論の一次情報）
- `.claude/cases/koch-4arm-dual.md`・`koch-vr-haptics.md`（裁定の履歴）・`.claude/handoff/260920_T2_*.md`（旧引き継ぎ）
- `TacitCapture/76_`（2 ペアのテスト設計）・`58_`／`61_`／`62_`（受入・実機ログ・運用）・`87_`／`85_`（握手アタッチメントの印刷）・`output/cad/print_kit_fest2610/`（印刷キット）

## 8. 安全（AGENTS.md の要約）

動かす・トルクを抜く・ID を書くのはユーザーが明示的に頼んだときだけ。ポートは推測しない。実機の挙動はユーザーの報告だけが事実。終了は Ctrl+C の後に両アームの AC アダプタを抜く。

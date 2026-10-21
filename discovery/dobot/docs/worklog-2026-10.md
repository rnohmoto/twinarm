# 作業記録 2026-10（Dobot 実演の立ち上げ）

版: 2026-10-21 ／ 詳細な時系列は `logs/bench_20261015.md`・`logs/bench_20261016.md`

## 1. 要約

10/15 に実機の疎通・吸着・非常停止を確認し、10/16 に較正・置き場・高さを決めて初めてのピック＆プレースに成功、
声の指示も通った。10/19 に機材の位置を変えて再較正し、速さ（20 秒 → 7 秒）、映像（動作中も止まらない・左右反転）、
置き場の空き探索、素人向けのランチャーと文書を整えた。残りは 20 サイクル連続運転の確認。

## 2. 日ごと

### 2026-10-05
- Magician を Mac に初接続。CH9102 の USB シリアルとして認識。吸着コマンドの応答を確認。

### 2026-10-15
- ジョグ移動、作業域ガード、Esc での停止、吸盤での木片の保持を実機で確認。
- `demo.py --robot magician` で起動時ホーミング、往復（試運転）→ 非常停止 → 停止解除 → ホームの停止レーンを確認（停止まで 1.2 秒）。
- C922 の露出・フォーカスを Logi Options+ で固定（AppleScript で UI 操作）。WB は手動だと青被りするため自動のまま。
- 較正: 消しゴムは検出が不安定 → 青ブロックに変更。俯瞰カメラでは腕が目印を隠すため、較正の Space を 2 段階（ロック → 記録）に改修。
- 4 点記録後に較正ツールが落ちて点が消えた（原因不明）。

### 2026-10-16
- 較正ツールを改修: 1 点ごとに逐次保存・再開、ロック後 1 秒のデバウンス、クラッシュログ。
- 手で腕を当てて座標を読む較正を 15 点で実施。外れ値 2 点を除いて RMS 3.7 mm。
- 置き場を決めるツール `zones_view.py`（画像をクリック）を作成。スロットが作業域から出る位置は拒否。
- 高さを実測: 机 z=−72.2、ブロック上面 z=−56.1（厚み 16 mm）→ z_pick −57.1、z_place −54.1、z_min −80。
- ウィザードの不具合を修正（backend を pydobot に書き換える、`--from` が渡らない、案内文）。
- demo で初めてのピック＆プレース成功。ただし狙いが数 mm 外側にずれ、ブロックを寄せる必要があった。
- ロボット主導の較正 `calibrate.py --drive` を作成して実施: RMS 1.91 mm。手押し較正は平均 (+7, −11) mm ずれていた。
- 音声: モデル未ダウンロードで初回押下が無反応に見えた → 起動時に先読み・準備中表示。さらに demo が常に文字入力モードにしていた不具合を修正 → 声で動作確認。
- 1 回のピック＆プレースは 20.5 秒。

### 2026-10-19
- 再現用の文書 `HANDOFF.md`、`start.command`、実機値の `config.json` をコミット。パネルを見せる向けの縦 1 列に変更。
- 速さ: 速度比 30 → 100 %、退避・横移動を関節補間（降下だけ直線）、z_safe 40 → 20、吸着待ち 0.6 → 0.4 → 実測 7.1 秒。
- 置き場の空き探索: 見えている物の大きさ（mm）で重なりを避ける。満杯なら断る。
- 機材の位置を変えて再較正（RMS 3.10 mm）。露出が自動に戻った可能性があり、緑・黄の色範囲と木片の縦横比上限を今の映りに合わせた。
- 映像を専用スレッド（15 fps）に分離し、腕が動いている間も更新されるようにした。表示だけ左右反転。
- dry-run のテストが実機の較正ファイルを拾う問題を修正。
- ランチャー `launcher.py`（起動・停止・再起動、較正、置き場、設定、ログ）、`setup.command`（新しい Mac の準備）、文書 4 本（要件定義書・操作マニュアル・導入手順・この記録）。較正ファイルを git 管理に。

### 2026-10-21
- 構成を src レイアウトへ移行。トップレベルの 22 本の .py を `src/dobot/` パッケージに入れ、ベア名 import を
  `from dobot.X import …` に、テストを `src/dobot/tests/` に移した（`sys.path` 書き換えは廃止）。
- 操作者が打つコマンドを console script に統一（`dobot-launcher`・`dobot-demo`・`dobot-calibrate` など 11 本）。
  `./start.sh` と `start.bat` も同じ名前を呼ぶ。ランチャーが子プロセスを起こす経路は `python -m dobot.demo`。
- `config.json`・`assets/`・`logs/` はこれまでどおり `dobot/` 直下に置き、`dobot.paths`（`CONFIG_PATH`・
  `ASSETS_DIR`・`LOGS_DIR`・`from_root()`）経由で解決する。カレントディレクトリ依存を解消したので、
  どこから実行しても同じファイルを読む。
- 挙動は変えていない。テスト 126 件・ruff の既存指摘 25 件・ty の 138 件はいずれも移行前と同じ。
  実機は動かしていない。

## 3. 数値のまとめ

| 項目 | 値 |
| --- | --- |
| 較正 | 10/16 手押し 3.7 mm → --drive 1.91 mm ／ 10/19 --drive 3.10 mm |
| 手押し較正の系統誤差 | 平均 dx +7.0、dy −10.7 mm（基部から見て外側） |
| 高さ | 机 −72.2、ブロック上面 −56.1、z_pick −57.1、z_place −54.1、z_safe 20、z_min −80 |
| 速さ | 20.5 s → 7.1 s（1 回のピック＆プレース） |
| 映像 | 6.6 fps（動作中は停止）→ 14.2 fps（動作中も更新） |
| 非常停止 | 1.2 s で停止（10/15） |
| テスト | 126 件（機材なし） |

## 4. コミット一覧（dobot/、2026-10-15 以降）

| 日付 | コミット | 内容 |
| --- | --- | --- |
| 2026-10-19 | `f84ce32` | feat(dobot): camera stream on its own thread at 15 fps, display-only mirror |\n| 2026-10-19 | `516b477` | feat(dobot): place beside the objects the camera sees, never on top; refuse when a zone is full |\n| 2026-10-19 | `0e68ed0` | perf(dobot): joint-interpolated travel, straight descents, and bench speed settings |\n| 2026-10-19 | `4aabe86` | docs(dobot): HANDOFF.md runbook, start.command, bench config.json committed |\n| 2026-10-19 | `bc054cb` | feat(dobot): panel in show order (camera, mic, controls, log); dry-run keeps the synthetic homography |\n| 2026-10-16 | `877d8a5` | fix(dobot): demo no longer forces text input, so the panel mic can actually run Whisper |\n| 2026-10-16 | `d8cedb4` | chore(dobot): ignore the wizard's config.bak_* copies |\n| 2026-10-16 | `9a77f52` | feat(dobot): calibrate --drive — the robot visits a grid and the camera reads the block under the cup |\n| 2026-10-16 | `c727925` | fix(dobot): voice UI shows the Whisper model load and the demo pre-loads it at start |\n| 2026-10-16 | `303947d` | fix(dobot): wizard's closing hint no longer forces --robot pydobot |\n| 2026-10-16 | `e624990` | fix(dobot): refuse zone centres whose slots fall outside the arm workspace |\n| 2026-10-16 | `27ade26` | fix(dobot): wizard z_place is measured with the object attached (planner lowers the cup to z_place as is) |\n| 2026-10-16 | `c520b0e` | feat(dobot): zones_view.py — place the zones by clicking on the camera image |\n| 2026-10-16 | `657d277` | fix(dobot): wizard keeps a configured hardware backend; launchers pass --from and stop forcing pydobot |\n| 2026-10-16 | `1550937` | feat(dobot): calibrate saves each pair as it is recorded, resumes, debounces lock→record |\n| 2026-10-15 | `a1f82a7` | feat(dobot): two-phase space for object calibration, camera read retry window |

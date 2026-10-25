# 作業記録 2026-10（Dobot 実演の立ち上げ）

版: 2026-10-26 ／ 詳細な時系列は `logs/bench_20261015.md`・`logs/bench_20261016.md`

## 1. 要約

10/15 に実機の疎通・吸着・非常停止を確認し、10/16 に較正・置き場・高さを決めて初めてのピック＆プレースに成功、
声の指示も通った。10/19 に機材の位置を変えて再較正し、速さ（20 秒 → 7 秒）、映像（動作中も止まらない・左右反転）、
置き場の空き探索、素人向けのランチャーと文書を整えた。10/26（フェス当日未明・機材なし）に較正を「ブロックを 1 回置くだけ」
の自動運搬に変え、30 秒の確認モードと、物ごとの置く高さ（ボール）、凸包で測る円形度を入れた。残りは実機での確認と
20 サイクル連続運転。

## 2. 日ごと

### 2026-10-26（Windows・機材なし・フェス当日の未明）
- 本人の問い: 較正で最大可動域を見るようなスキャンを毎回やるのか・各軸 4 点ほど要るのか・もっと簡単にならないか・
  ブロックを最初に 1 個置いたら後は自動にならないか・ゴルフボールは認識できそうか。
- 答えの実装: `dobot-calibrate --carry` — 最初の点（右手前の隅）の上で止まった吸盤の真下にブロックを置いて Enter（これ 1 回）。
  あとは腕が拾い、各点に置く → 脇へ退避 → カメラで読む → 拾う。順は四隅 → 中心 → 辺の中点（最初の 4 点が一直線を含まない順。
  同じ辺の 3 点＋中心、あるいは中心＋対角の 2 隅だと当てはめが壊れ、5 点目の残差が 46 mm になった＝合成テストで発見）。拾い損ねは前の点から自動で
  拾い直し、見えない・落とした点は人に聞く（3 回で飛ばす）。残差 8 mm 超の点は外す（5 点は残す・一直線になるなら外さない）。
  `--points quick` で中心＋四隅の 5 点。`--verify` は 30 秒の確認: 見えている物を今の較正で拾って中心へ置き、置いた位置の
  読みと命令座標の差を測る（3 mm 以下で「使えます」）。ランチャーに「較正を確認」「較正をやり直す（腕が運ぶ）」と
  較正ファイルの日時・RMS・点数。
- 較正は毎回ではない: カメラかロボットを動かしたときだけ。`assets/homography.json` は git 管理（10/19 の 9 点・RMS 3.10）。
- 点数の考え方: ホモグラフィは 4 点で決まり、5 点で足りる。9 点は余った点で当てはまりの悪さが見える。端まで広く取るのは
  精度のためで「可動域の確認」ではない。
- 置く高さを物ごとに（`objects[].z_place`）: ブロック用の z_place −54.1 をゴルフボール（43 mm）に使うと 27 mm 押し付ける。
  ゴルフボール z_pick −30.5／z_place −27.5、卓球ボール −33.2／−30.2（机 −72.2 からの計算値。現地の 6 番で実測して確定。
  ウィザード 6 番は z_place = z_pick + 3 を自動で入れる）。ロボットと物が同じ面に載る限り、会場が変わっても高さは同じ。
- 球の円形度を輪郭の凸包で測る（ディンプル・縁の影でぎざぎざでも丸と分かる）。白い正方形（ArUco の白地）はぼかしと開閉で
  角が丸まり 0.80 を超えるので、下限は 0.85（ゴルフ）・0.82（卓球）のまま。縦横比の上限は 1.3。
- 文書: HANDOFF 4.1・導入手順 4.1・操作マニュアル 0／6.3・要件 F2.1／F3.1／F6.1・現地チェックリスト
  `docs/fest-checklist-261026.md`・Magician の仕様と作法 `docs/magician-best-practices.md`・root の fest_start_prompts。
- 実機は未確認（この PC に腕は無い）。現地の順: 「較正を確認」→ 3 mm 超なら「やり直す」→ 置き場 → 1 回動かす。
- 一次情報の調査（robotics `web_research/magician_spec_2610/`・136 件＝★101／☆25／▲10）を受けて `magician-best-practices.md` に出典を付けた。
  効いた事実: 公式ページの関節範囲は J1 ±120°（初代期の文献は ±90°・コードは狭い方）、velocityRatio の単位・範囲は公式に無い（100 を上限に）、
  脱調アラーム 0x50〜53 の公式の復帰条件は回零（ホーミング）＝パネルの「ホーム」では直らない → 操作マニュアル §7・チェックリスト §7・
  アラームの文言に「本体キー 2 秒長押し」を足した。Dobot 純正の Vision Kit は 3 点較正で「9 点」は Cognex の作法。

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
  起動スクリプトも同じ名前を呼ぶ。ランチャーが子プロセスを起こす経路は `python -m dobot.demo`。
- 続けてトップレベルを片付けた: 起動スクリプト 5 本（`start.sh`・`start.command`・`start.bat`・`setup.sh`・
  `setup.command`）を `scripts/` へ、`HANDOFF.md` を `docs/` へ、`config.json` を `src/` へ移した。
  各スクリプトは `cd "$(dirname "$0")/.."`（`.bat` は `cd /d "%~dp0.."`）でプロジェクトルートに降りてから動く。
- `config.json` は `src/config.json`、`assets/` と `logs/` は `dobot/` 直下。いずれも `dobot.paths`
  （`CONFIG_PATH`・`ASSETS_DIR`・`LOGS_DIR`・`from_root()`）経由で解決するので、どこから実行しても同じ
  ファイルを読む。文書とスクリプトの参照もこの配置に合わせて直した。
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

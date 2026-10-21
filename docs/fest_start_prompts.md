# フェス前の実機セッション — Mac 4 台の起動プロンプト（2026-10-14・twinarm main 用）

Learning Fest（2026-10-26/27）の前に、Mac 4 台で実機を立ち上げるときの手引き。**Mac 1 台に Claude Code の会話を 1 つ**持ち、下のプロンプトを丸ごと貼る。Claude は twinarm の中だけを読んで動けるように書いてある（robotics リポジトリが無くてもよい）。実機・ゴーグル・マイク・カメラの結果は本人の報告だけを事実にする（`AGENTS.md`）。

## 1. 使い方（本人）
1. 新しい Mac なら §3 の導入を先に。既存の Mac は `cd twinarm && git pull`。
2. `cd twinarm && claude` で Claude Code を起動する（会話は Mac 1 台に 1 つ。どの役割も、腕が動くコマンドは本人が頼んだときだけ実行される）。
3. 自分の Mac の役割（§2）のプロンプト（§4）を丸ごと貼る。Claude は最初に「腕に触れない確認」の結果を見せる。
4. 腕を動かすのは本人。結果を会話に書いて報告する。Claude はログを読み、設定を直し、記録する。
5. 終わったら Claude に「実機ログを書いて」と頼む（§5）。

## 2. 役割表（Mac 4 台 × 腕 4 本 × ゴーグル 3 台）
| Mac | 役割 | つなぐ物 | 起動（`discovery/koch` で） | ページ | 段（`discovery/koch/docs/CHECKLIST.md`） |
|---|---|---|---|---|---|
| A | 握手（ペア A・差分反射式） | リーダー A（5 V）＋フォロワー A（12 V）＝USB シリアル 2 本・握手ハンド | `./scripts/koch.sh handshake` | パネル http://127.0.0.1:8780 | S0 → TEST 2 → 2b → 5 → 6 |
| B | VR 1 人目（リーダー B） | リーダー B（5 V）＝USB シリアル 1 本・Quest Pro #1（データ対応 USB-C ケーブル） | `scripts/3_VR1人目_リーダーB.command`（＝`PAIR=B ./scripts/koch.sh vr-leader`。uv sync・設定・Quest の許可待ち・reverse・見張りまで自動） | ゴーグル http://localhost:8444/ ・Mac の操作ページ …`?spectator=1&info=1` | S0 → U0 → V1 → V0 → V-w → V-c → V-e → V2 → V4 → V3 |
| C | VR 2 人目（フォロワー B を手で） | フォロワー B（12 V）＝USB シリアル 1 本・Quest Pro #2 | `scripts/4_VR2人目_フォロワーB.command`（＝`PAIR=B ./scripts/koch.sh vr-follower`。Quest も自動で開く） | ゴーグル http://localhost:8443/ ・Mac の操作ページ | S0 → U0 → V5 |
| D | 卓上（Magician の P&P・声・俯瞰カメラ） | Magician（USB シリアル）・C922n（三脚・俯瞰）・マイク | `cd dobot && ./scripts/start.sh`（初回は wizard） | パネル http://127.0.0.1:8790 | §4 D の M0 → M1・M2 → M3 → 声 |

- Quest Pro #3 は予備。3 台とも同じアカウントで初期設定し、USB デバッグの許可は Mac B と Mac C の両方で済ませる（許可は Mac とヘッドセットの組ごと）。
- 設定ファイル `discovery/koch/config/config.json` は Mac ごと（git 管理外）。較正 JSON（A・B 両ペア）と物体プリセット `twin.json` は main に入っている。Mac C の 2 人目は `config/solo_F/twin.json` が別ファイル。
- 元の設計は 1 台の Mac で 3 つを同時に動かす形（`koch.sh all`）。分けても各プロセスは独立で、別の Mac ならポートは競合しない。Mac 同士の通信は無い。

## 3. 新しい Mac の導入（コピペ・ネットのある所で・1 台 30〜45 分）
```bash
uname -m                                   # arm64 であること（Intel は不可: discovery/koch の uv.lock に torch の macOS x86_64 ホイールが無い）
xcode-select --install                     # git。入っていれば「already installed」
curl -LsSf https://astral.sh/uv/install.sh | sh    # uv。終わったらターミナルを開き直す
git clone https://github.com/rnohmoto/twinarm.git && cd twinarm/discovery/koch
./scripts/start.sh check                     # uv sync（Python 3.13 と依存を自動取得・約 200 MB）→ selftest → three.js 取得 → ポート一覧 → adb の有無
uv run koch-launch --list    # ポート名と USB シリアル番号（読み取りのみ）
uv run koch-launch --init    # config/config.json の雛形 → この Mac に挿す腕の行だけ記入（推測しない）
./scripts/start.sh rehearse                  # 仮想のサーボで通し稽古・約 3 分。「すべて通りました」で合格
```
VR の Mac（B・C）はさらに:
```bash
brew install --cask android-platform-tools # adb。Homebrew が無ければ Android の platform-tools zip を取り、quest_usb.py に --adb <path> で渡す
# Quest 側: 開発者モード ON → USB で Mac へ → 設定 → 開発者 → MTP 通知 ON → 「USB デバッグを許可」→「常に許可」（discovery/koch/docs/manual/quest_setup.html §7）
uv run koch-quest --check     # 「✓ <番号>: 接続済み（adb 可・USB）」と電池残量
```
卓上の Mac（D）は:
```bash
cd twinarm/dobot && uv sync --extra voice --extra llm   # 約 150 MB
export ANTHROPIC_API_KEY=...               # 無ければルール解析だけで動く
./scripts/start.sh check                   # カメラ一覧・ポート候補・オフラインのテスト
# 声の初回は faster-whisper の small モデル（約 0.5 GB）を自動取得する。ネットのある所で一度 demo を動かしておく。macOS はターミナルにマイクの許可が要る
```
会場でネットが要るのは LLM だけ。Quest のページは Mac から USB 越しに配る。聞き取りはローカル。

## 4. プロンプト（Mac ごとに丸ごと貼る）

### A. 握手（ペア A）
```
twinarm の discovery/koch で握手（ペア A だけ）を実機で立ち上げます。いま動いている PC は「握手の Mac A」です。
読む順: AGENTS.md（実機安全）→ discovery/koch/docs/HANDOFF.md → discovery/koch/docs/manual/handshake.html → discovery/koch/docs/CHECKLIST.md の S0・TEST 2・2b・5・6 → docs/fest_start_prompts.md §2。
やりたいこと: フェスの握手体験（来場者がフォロワー A に手を出し、操作者がリーダー A で握り返す。ミラー制御なし）を、実機で 30 分通し、握手ハンドを付けた形まで確かめる。
- 最初に git pull → cd discovery/koch → ./scripts/start.sh check を実行し、「起動できるもの」の表とポート一覧を私に見せる（腕は動かない）
- config/config.json のペア A のポートは私が渡す（--list の結果から私が選ぶ。推測しない。リーダーとフォロワーは入替不可）
- 腕が動くコマンド（./scripts/koch.sh handshake。中身は launch.py --pair A --ff gripper --grip-ma 500 --extra "--ff-style error --max-rel 20 --ff-ke 15 --ff-cap 500"。ゲインと上限は 10/19 の仮設定）は、私がこの会話で明示的に頼んだときだけ実行する。実機の挙動は私の報告だけを事実にする
- 進め方: S0 → TEST 2（ペア A 単独の回帰）→ 2b（フォロワー握力の上限 500 mA で過負荷停止と滑りの境）→ 5（30 分の連続稼働）→ 6（握手ハンド装着後の体験リハ）を 1 つずつ。各テストの記録欄を埋める
- 握りの硬さや上限を変えるときは環境変数で: FF_KE=20 FF_CAP=600 ./scripts/koch.sh handshake（ke＝差分ゲイン mA/unit＝硬さ、cap＝握っている間の最大電流）。10/19 の仮設定は ke 15・cap 500（launcher の素の既定は ke 9・cap 450）。パネルのスライダ「(error)差分ゲイン」「握り上限mA」で動かしながらも変えられる。cap を上げるほど握っている間の発熱が速い（450→900 で 4 倍）。60 ℃で ke 半減・65 ℃で握り返し停止（停止後は冷えてから再起動）。cap を上げる前に TEST 5（30 分）で温度の曲線を取る
- 終わったら実機ログ md を書く（docs/fest_start_prompts.md §5）
```

### B. VR 1 人目（リーダー B × Quest Pro #1）
```
twinarm の discovery/koch で VR（1 人目・リーダー B）を実機と Quest Pro で立ち上げます。いま動いている PC は「VR の Mac B」です。
読む順: AGENTS.md → discovery/koch/docs/HANDOFF.md → discovery/koch/docs/manual/vr.html → discovery/koch/docs/CHECKLIST.md の S0・U0・V 系 → docs/fest_start_prompts.md §2。Quest の準備（開発者モード・USB の許可）がまだなら discovery/koch/docs/manual/quest_setup.html の ②〜⑦ から。
やりたいこと: ゴーグルの中の仮想の物（スポンジ・ボール・鉄ブロック）をリーダー B で握ったときに、硬さと重さが手に返る状態にし、10/19 の体験に出せるかを判断する。
- 最初に git pull → cd discovery/koch → ./scripts/start.sh check を実行し、「起動できるもの」と Quest の接続・電池残量を私に見せる（腕は動かない）
- 腕が動くコマンド（scripts/3_VR1人目_リーダーB.command＝PAIR=B ./scripts/koch.sh vr-leader。重さ 60 mA・肘反転・Quest の許可待ちと reverse・USB の見張り・Mac の操作ページまで自動）は、私が明示的に頼んだときだけ。ポートは fest 設定のシリアル番号で解決される（違う腕なら --list で直す）。実機の挙動は私の報告だけを事実にする
- AR 中はゴーグルにボタンも文字も出ない（Quest Browser は DOM overlay を描かない）。位置合わせはゴーグルの中で分身をつまんで動かす（離して 1.5 秒で保存＝アンカー登録）。細かい操作と状態の確認は Mac の操作ページ（http://localhost:8444/?spectator=1&info=1）から
- 進め方: S0 → U0 → V1（./scripts/start.sh sim と Quest で表示）→ V0（壁だけ・ゴーグルなし）→ V-w（重さの上限は低くから）→ V-c（つかんで合わせ・アンカー復元）→ V-e（編集モード・高さと置き場所）→ V2（実機リーダー × VR）→ V4（投げる・落とす）→ V3（フェス形態の判断）を 1 つずつ。10/19 の記録は discovery/koch/docs/LOG_261019_VR.md、要件は REQUIREMENTS_VR.md
- V-e で、物体ごとの硬さ p_gain（50 刻み）と上限 cap_ma（10 刻み）をゴーグル内のスライダで掃引し、体感と CSV（--extra "--csv"）を記録する。既定はスポンジ P250／120 mA・ボール P900／350・鉄ブロック P2000／450。保存先は config/twin.json（Mac C に写すなら枝に載せる）
- 給電: 電池残量を 30 分の体験の前後で記録する（USB のまま／充電口つきケーブル／Wi-Fi 切替 ./scripts/start.sh wifi）
- 終わったら実機ログ md を書く（docs/fest_start_prompts.md §5）
```

### C. VR 2 人目（フォロワー B を手で × Quest Pro #2）
```
twinarm の discovery/koch で VR の 2 人目（フォロワー B を手で動かす側・別の空間）を実機と Quest Pro で立ち上げます。いま動いている PC は「VR の Mac C」です。
読む順: AGENTS.md → discovery/koch/docs/HANDOFF.md → discovery/koch/docs/manual/vr.html → discovery/koch/docs/CHECKLIST.md の S0・U0・V5 → docs/fest_start_prompts.md §2。
やりたいこと: 2 人目がフォロワー B を手で動かして、同じ仮想の物を握れる状態にする（1 人目と同じ体験を 2 台目のゴーグルで）。
- 最初に git pull → cd discovery/koch → ./scripts/start.sh check。Quest の接続は uv run koch-quest --check
- config/config.json は無ければ fest 設定（4 本のシリアル入り）から自動で作られ、ポートはシリアル番号で解決される。違う腕を挿すときだけ --list を見て私が直す（推測しない）
- 起動は scripts/4_VR2人目_フォロワーB.command（＝PAIR=B ./scripts/koch.sh vr-follower。ページは http://localhost:8443/）。Quest Pro #2 の許可（この Mac で「常に許可」）が済んでいれば、ゴーグルのページは自動で開き、USB が切れても見張りが張り直す。Mac の操作ページは http://localhost:8443/?spectator=1&info=1
- 接続直後と終了時にフォロワー B のトルクが抜ける。私が腕を支えてから Enter を押す。腕が動くコマンドは私が明示的に頼んだときだけ。実機の挙動は私の報告だけを事実にする
- 進め方: S0 → U0 → V5（CHECKLIST）。重さは --vw-cap 60 --vw-pwm-cap 80（既定）から上げる。摩擦アシストは環境変数 ASSIST（既定 elbow_flex,wrist_flex・上限 25 mA）
- 終わったら実機ログ md を書く（docs/fest_start_prompts.md §5）
```

### D. 卓上（Magician の P&P・声・俯瞰カメラ）
```
twinarm の dobot で卓上ロボット（Dobot Magician）のピック＆プレース・声の UI・俯瞰カメラを実機で立ち上げます。いま動いている PC は「卓上の Mac D」です。
読む順: dobot/AGENTS.md → dobot/README.md → dobot/manual/index.html の §2（接続）・§3（カメラの固定。Mac は Logi Options+／Logi Tune／uvc-util・設定は起動のたびに書き直す）・§4 → docs/fest_start_prompts.md §2。
やりたいこと: 木片・ゴルフボール・消しゴムを俯瞰カメラで見つけ、画面のボタンと声（「赤いブロックを右に置いて」）で拾って置く体験を、20 回連続で通せる状態にする。
- 最初に git pull → cd dobot → uv sync --extra voice --extra llm → ./scripts/start.sh check を実行し、カメラ一覧・ポート候補・テストの結果を私に見せる（腕は動かない）
- 腕が動くコマンド（./scripts/start.sh、dobot-demo、dobot-jog、dobot-suction）は、私が明示的に頼んだときだけ。ポートは私が渡す（/dev/cu.usbserial-… を --list で選ぶ。推測しない）。実機の挙動は私の報告だけを事実にする
- 進め方（1 つずつ）:
  M0 疎通と停止: 電源 ON → 本体キー 2 秒長押しでホーミング（LED 青点滅 → 緑）→ USB。uv run dobot-jog --port <ポート> --speed 30 で 20 mm 動かし「停止」。次に uv run dobot-demo --config src/config.json --robot magician --port <ポート> --camera-index <N> --no-llm → パネル「ホーム」→ 動作中に「非常停止」→ 止まるまでの秒数 →「再開」→「ホーム」。logs/*.jsonl の halt(estop)。吸着を 1 回（吸って置く・排気で離れるか）
  M1・M2 カメラと較正: Logi Options+ でオートフォーカス・露出・ホワイトバランスを切って値を決める → uv run dobot-check-camera --index <N> で読み戻し（「OFF が効いています」）→ ./scripts/start.sh wizard（カメラ → ロボット → マーカー → 較正 → 置き場 → 高さ → 検出 → 試運転。wizard は pydobot で動く）
  M3 20 回: demo（--robot magician・--no-llm）でパネルのボタンと文字入力で 20 回連続。成功率・1 サイクルの秒数・アラーム・熱を JSONL から集計し、speed_pct を決める（基準: 20 回中 吸着ミス 1 回以下）
  声: uv run dobot-demo --dry-run --no-llm でマイクボタン長押し →「赤いブロックを右のトレイに置いて」が聞き取れるか・語頭が欠けないか・logs の asr（seconds・avg_logprob・no_speech_prob）。「赤じゃなくて青を右に」で「これですか？」が出て Enter で動き Esc で動かないこと。「止まって」で非常停止が掛かるまでの秒数。最後に実機で声で 1 回
  LLM: ANTHROPIC_API_KEY を入れて --no-llm を外し、同じ 3 文で返答と動作を見る（ネット断なら rule_parser に落ちる）
- 終わったら実機ログ md を書く（docs/fest_start_prompts.md §5）
```

## 5. 記録
- robotics リポジトリ（user-m-s/robotics）がこの Mac にあるなら、`TacitCapture/` に実機ログ md（61_ の型・119_ 以降の番号）を書き、`.claude/cases/` の該当案件に 1 節足す。
- 無ければ、Claude にこの会話の末尾へ「実機ログ」（日付・Mac・段ごとの結果・数値・直した設定・次）を書かせ、`discovery/koch/docs/CHECKLIST.md` の記録欄を埋める。後で robotics に写す。
- twinarm のコードを直したときは枝 → `discovery/koch/scripts/start.sh rehearse`（koch）／`uv run pytest`（dobot）→ 実機。main へのマージは本人の指示があるときだけ。

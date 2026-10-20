"""声の UI（T13 Step 2・3・5）: 長押し録音・受理の判定・曖昧検出・表示→カウントダウン実行・「これですか？」。実機・マイクなし。"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asr import PushToTalkRecorder, TextASR, Transcript, judge_transcript
from config import AppConfig, ASRConfig
from demo import DemoRunner, parse_args
from llm_agent import RuleAgent
from panel_web import PanelState
from rule_parser import Intent, mentions, parse
from voice_front import VoiceFront, describe

DATA = Path(__file__).resolve().parent / "data" / "utterances_v1.jsonl"


def wait_for(pred, timeout_s: float = 3.0, step: float = 0.005) -> bool:
    t_end = time.monotonic() + timeout_s
    while time.monotonic() < t_end:
        if pred():
            return True
        time.sleep(step)
    return pred()


# ------------------------------------------------------------------ recorder
class FakeClock:
    def __init__(self):
        self.now = 0.0


class FakeStream:
    def __init__(self, cb):
        self.cb = cb
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        pass


def test_recorder_preroll_postroll_and_double_start():
    cfg = ASRConfig(sample_rate=16000, preroll_s=0.4, postroll_s=0.3, max_record_s=6.0)
    clk = FakeClock()
    streams: list[FakeStream] = []

    def factory(cb):
        s = FakeStream(cb)
        streams.append(s)
        return s

    pushed: list[float] = []

    def push_until(t_target: float) -> None:
        """0.05 秒ごとのチャンク（値＝到着時刻）を t_target まで押し込む。"""
        while (len(pushed) + 1) * 0.05 <= t_target + 1e-9:
            t = round((len(pushed) + 1) * 0.05, 6)
            clk.now = t
            pushed.append(t)
            streams[0].cb(np.full(800, t, dtype=np.float32))

    def sleep(s: float) -> None:
        clk.now += s
        push_until(clk.now)

    rec = PushToTalkRecorder(cfg, stream_factory=factory, clock=lambda: clk.now, sleep=sleep)
    rec.open()                                      # マイクを開く（start() でも開く）
    assert streams and streams[0].started
    push_until(1.0)
    clk.now = 1.0
    rec.start()                                     # 押した（1.0 s）
    rec.start()                                     # 二重 start は無視
    assert rec.recording
    push_until(2.0)
    clk.now = 2.0
    audio = rec.stop()                              # 離した（2.0 s）→ ポストロール 0.3 s 待つ
    assert not rec.recording
    expected = [t for t in pushed if 0.6 - 1e-9 <= t <= 2.3 + 1e-9]
    assert len(audio) == len(expected) * 800, (len(audio), len(expected))
    assert audio[0] == pytest.approx(expected[0]) and audio[-1] == pytest.approx(expected[-1])
    assert rec.stop().size == 0                     # 録音中でなければ空
    rec.close()
    assert rec._stream is None


def test_judge_transcript_gate():
    cfg = ASRConfig()
    assert judge_transcript(Transcript(""), cfg) == ("", "empty")
    assert judge_transcript(Transcript("ご視聴ありがとうございました"), cfg) == ("", "hallucination")
    assert judge_transcript(Transcript("赤を右に置いてご視聴ありがとうございました"), cfg) == ("赤を右に置いて", "ok")
    assert judge_transcript(Transcript("赤を右に", avg_logprob=-0.3, no_speech_prob=0.9), cfg)[1] == "ok"   # 自信があれば無音でない
    assert judge_transcript(Transcript("あ", avg_logprob=-1.5, no_speech_prob=0.9), cfg) == ("", "no_speech")
    assert judge_transcript(Transcript("赤を右に", avg_logprob=-1.5, no_speech_prob=0.1), cfg) == ("", "low_confidence")
    assert judge_transcript(Transcript("赤を右に", avg_logprob=-0.5, no_speech_prob=0.1), cfg) == ("赤を右に", "ok")


# --------------------------------------------------------------- rule parser
def test_mentions_longest_match_and_order():
    cfg = AppConfig.default()
    objs, zones = mentions("ゴルフボールを右のトレイに", cfg)
    assert [n for _, n in objs] == ["golf_ball"] and [n for _, n in zones] == ["tray_right"]
    objs, _ = mentions("赤じゃなくて青を", cfg)
    assert [n for _, n in objs] == ["red_cube", "blue_cube"]


def test_rule_parser_ambiguity_and_best_guess():
    cfg = AppConfig.default()
    it = parse("赤じゃなくて青を右に", cfg)
    assert (it.action, it.object, it.zone, it.ambiguous) == ("pick_and_place", "blue_cube", "tray_right", True)
    assert "negation" in it.reason
    it = parse("赤を右、いや左に置いて", cfg)
    assert (it.object, it.zone, it.ambiguous) == ("red_cube", "tray_left", True)
    it = parse("赤いやつを右にお願い", cfg)
    assert (it.object, it.zone, it.ambiguous) == ("red_cube", "tray_right", False)      # 「いや」の誤検出なし
    it = parse("赤と青を両方右に置いて", cfg)
    assert it.action == "pick_and_place" and it.ambiguous and "objects" in it.reason
    assert parse("ホームに戻して", cfg).action == "home" and parse("ホームポジションに戻して", cfg).action == "home"
    assert parse("元に戻して", cfg).action == "tidy" and not parse("元に戻して", cfg).ambiguous
    it = parse("リセットして", cfg)
    assert it.action == "tidy" and it.ambiguous and it.reason == "reset"
    assert parse("青取って赤取って交換して", cfg).action == "unsupported"
    assert parse("赤以外を全部右に", cfg).action == "unsupported"
    it = parse("それを左に", cfg)
    assert (it.action, it.object, it.zone, it.ambiguous) == ("pick_and_place", None, "tray_left", True)
    for s in ("待って", "とまれ", "止めて", "止まれ", "危ない", "やっぱりやめて", "止まって止まって"):
        assert parse(s, cfg).action == "stop", s
    d = parse("赤じゃなくて青を右に", cfg).to_dict()
    assert Intent.from_dict({**d, "extra": 1}) == parse("赤じゃなくて青を右に", cfg)


def _judge(exp: str, it: Intent) -> str:
    """合成発話の採点。WRONG＝確認なしで違う動作をしてしまう。"""
    p = exp.split()
    if p[0] == "state":
        return "SKIP"
    if p[0] == "stop":
        return "PASS" if it.action == "stop" else "WRONG"
    executable = it.action in ("pick_and_place", "tidy", "home", "loop") and not it.ambiguous
    if it.action == "pick_and_place" and (it.zone is None or (it.object is None and it.count != -1)):
        executable = False                                      # 足りないので聞き返す
    if p[0] in ("none", "unsupported", "clarify"):
        return "WRONG" if executable else "PASS"
    if it.ambiguous:
        return "ASK"                                           # 「これですか？」で人が確かめる
    if p[0] == "pnp":
        want = (None if p[1] == "-" else p[1], None if p[2] == "-" else p[2], "all" in p[3:])
        got = (it.object, it.zone, it.count == -1)
        if it.action == "pick_and_place" and got == want:
            return "PASS"
        return "MISS" if not executable else "WRONG"
    if it.action == p[0]:
        return "PASS"
    return "MISS" if not executable else "WRONG"


def test_rule_parser_regression_on_utterance_set():
    cfg = AppConfig.default()
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    verdicts = {r["id"]: _judge(r["exp"], parse(r["text"], cfg)) for r in rows}
    wrong = [i for i, v in verdicts.items() if v == "WRONG"]
    assert not wrong, wrong                                     # 確認なしの誤実行は 0
    stops = [r for r in rows if r.get("critical")]
    assert all(verdicts[r["id"]] == "PASS" for r in stops)      # 停止語 9/9
    assert verdicts["E01"] == "ASK" and parse("赤じゃなくて青を右に", cfg).object == "blue_cube"
    assert verdicts["C07"] == "PASS" and parse("赤いブロック", cfg).zone is None            # 物だけ → 置き場を聞き返す
    for i in ("E02", "E03", "E04"):                              # 否定は後に言った方を最有力に
        r = next(x for x in rows if x["id"] == i)
        it = parse(r["text"], cfg)
        _, o, z = r["exp"].split()[:3]
        assert (it.object, it.zone) == (o, z) and it.ambiguous, (i, it)
    passed = sum(1 for v in verdicts.values() if v == "PASS")
    assert passed >= 80, passed


# --------------------------------------------------------------- voice front
class FakeRecorder:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.closed = False

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1
        return np.zeros(1600, dtype=np.float32)

    def close(self):
        self.closed = True


class FakeASR:
    def __init__(self, result: Transcript):
        self.rec = FakeRecorder()
        self.result = result

    def transcribe_detail(self, audio):
        return self.result


def _front(cfg: AppConfig, asr=None, **kw):
    state = PanelState()
    got: list[dict] = []
    stops: list[int] = []
    front = VoiceFront(cfg, state, submit=got.append, parse=lambda t: parse(t, cfg),
                       asr_factory=(lambda: asr) if asr is not None else None,
                       on_stop=lambda: stops.append(1), **kw)
    return front, state, got, stops


def test_describe_texts():
    cfg = AppConfig.default()
    assert describe(parse("赤いブロックを右のトレイに置いて", cfg), cfg) == ("赤いブロックを右へ", "run")
    assert describe(parse("赤じゃなくて青を右に", cfg), cfg)[1] == "ask"
    assert describe(parse("それを左に", cfg), cfg, last_object="red_cube") == ("直前の物（赤いブロック）を左へ", "ask")
    assert describe(parse("それを左に", cfg), cfg)[1] == "clarify"
    assert describe(parse("赤いブロック", cfg), cfg)[1] == "clarify"
    assert describe(parse("青取って赤取って交換して", cfg), cfg)[1] == "unsupported"
    assert describe(parse("止まって", cfg), cfg)[1] == "stop"
    assert describe(parse("何がある", cfg), cfg) == ("見えている物を答える", "run")


def test_voice_front_ptt_countdown_executes():
    cfg = AppConfig.default()
    cfg.demo.confirm_auto_run_s = 0.05
    asr = FakeASR(Transcript("赤いブロックを右のトレイに置いて", avg_logprob=-0.2, no_speech_prob=0.1))
    front, state, got, _ = _front(cfg, asr)
    front.ptt_start()
    assert wait_for(lambda: front.phase == "recording") and asr.rec.started == 1
    front.ptt_stop()
    assert wait_for(lambda: front.phase == "proposed")
    v = state.snapshot()["voice"]
    assert v["heard"].startswith("赤い") and v["proposal"] == "赤いブロックを右へ" and v["deadline"]
    assert wait_for(lambda: got)                                  # カウントダウンで実行
    assert got[0]["type"] == "intent" and got[0]["intent"]["object"] == "red_cube" and got[0]["intent"]["zone"] == "tray_right"
    assert front.phase == "idle"


def test_voice_front_enter_runs_at_once_and_esc_cancels():
    cfg = AppConfig.default()
    cfg.demo.confirm_auto_run_s = 5.0
    front, state, got, _ = _front(cfg)
    front.heard("青いブロックを左に置いて")
    assert wait_for(lambda: front.phase == "proposed")
    front.confirm()
    assert got and got[-1]["intent"]["object"] == "blue_cube" and front.phase == "idle"
    front.heard("緑のブロックを手前に置いて")
    assert wait_for(lambda: front.phase == "proposed")
    front.cancel()
    assert front.phase == "idle" and len(got) == 1
    assert "取り消し" in state.snapshot()["voice"]["message"]
    front.heard("ボールを奥に置いて", run_now=True)               # 編集して Enter
    assert wait_for(lambda: len(got) == 2) and got[-1]["intent"]["object"] == "ball"


def test_voice_front_ambiguous_asks_before_running():
    cfg = AppConfig.default()
    cfg.demo.confirm_auto_run_s = 0.02
    front, state, got, _ = _front(cfg)
    front.heard("赤じゃなくて青を右に")
    assert wait_for(lambda: front.phase == "confirming")
    v = state.snapshot()["voice"]
    assert "これですか" in v["question"] and v["proposal"] == "青いブロックを右へ" and v["ambiguous"]
    time.sleep(0.1)
    assert not got and front.phase == "confirming"                # 勝手には動かない
    front.confirm()
    assert got and got[0]["intent"]["object"] == "blue_cube"
    front.heard("リセットして")
    assert wait_for(lambda: front.phase == "confirming")
    front.cancel()
    assert len(got) == 1


def test_voice_front_always_confirm_mode():
    cfg = AppConfig.default()
    cfg.demo.confirm_auto_run_s = -1
    front, _, got, _ = _front(cfg)
    front.heard("赤いブロックを右のトレイに置いて")
    assert wait_for(lambda: front.phase == "confirming") and not got
    front.confirm()
    assert got


def test_voice_front_stop_word_and_failures():
    cfg = AppConfig.default()
    cfg.demo.confirm_auto_run_s = 0.02
    front, state, got, stops = _front(cfg)
    front.heard("待って")
    assert wait_for(lambda: stops) and not got and front.phase == "idle"
    front.heard("青取って赤取って交換して")
    assert wait_for(lambda: front.phase == "failed")
    assert "入れ替え" in state.snapshot()["voice"]["message"] and not got
    front.heard("右に置いて")                                     # 対象が無い → 聞き返す
    assert wait_for(lambda: "どれを" in state.snapshot()["voice"]["message"]) and not got
    front.heard("こんにちは")
    assert wait_for(lambda: front.phase == "failed") and not got
    time.sleep(0.05)
    assert not got


def test_voice_front_low_confidence_and_no_mic():
    cfg = AppConfig.default()
    asr = FakeASR(Transcript("ご視聴ありがとうございました", avg_logprob=-1.4, no_speech_prob=0.8))
    front, state, got, _ = _front(cfg, asr)
    front.ptt_start()
    front.ptt_stop()
    assert wait_for(lambda: front.phase == "failed") and not got
    assert "もう一度" in state.snapshot()["voice"]["message"]
    front2, state2, _got2, _ = _front(cfg, TextASR())             # マイクのない構成
    front2.ptt_start()
    assert wait_for(lambda: front2.phase == "failed") and "マイク" in state2.snapshot()["voice"]["message"]


def test_rule_agent_run_intent_matches_handle(tmp_path):
    from main import build
    cfg0 = AppConfig.default()
    cfg0.log_dir = str(tmp_path / "logs")
    cfg0.save(tmp_path / "config.json")
    cfg, _camera, _robot, ex, agent, _logger = build(parse_args(["--dry-run", "--no-llm", "--no-panel",
                                                                "--config", str(tmp_path / "config.json")]))
    assert isinstance(agent, RuleAgent)
    assert "どれを" in agent.run_intent(replace(parse("右に置いて", cfg), object=None))   # まだ直前の物が無い → 聞き返す
    r1 = agent.run_intent(parse("赤いブロックを右のトレイに置いて", cfg))
    assert "赤いブロック" in r1 and ex.stats["picks_ok"] == 1
    assert "入れ替え" in agent.run_intent(Intent("unsupported", reason="swap"))
    r2 = agent.run_intent(replace(parse("右に置いて", cfg), object=None))                 # 直前の物（赤）に解決される
    assert "赤いブロック" in r2 and ex.stats["picks_ok"] + ex.stats["picks_fail"] == 2


# ------------------------------------------------------------ demo + panel
def _runner(tmp_path) -> DemoRunner:
    cfg = AppConfig.default()
    cfg.log_dir = str(tmp_path / "logs")
    cfg.demo.confirm_auto_run_s = 0.05
    cfg.save(tmp_path / "config.json")
    # --keyboard: 文字入力だけの構成（マイク無し）を試す。無いと config の asr.engine（Whisper）を実際に読みに行く
    args = parse_args(["--dry-run", "--no-llm", "--no-panel", "--keyboard", "--config", str(tmp_path / "config.json")])
    return DemoRunner(args)


def _post(port: int, obj: dict) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/cmd", data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read())


def test_demo_panel_voice_roundtrip(tmp_path):
    runner = _runner(tmp_path)
    port = runner.start_panel(port=0)
    th = threading.Thread(target=runner.run, kwargs={"seconds": 20}, daemon=True)
    th.start()
    try:
        assert wait_for(lambda: "home" in runner.robot.log)
        assert _post(port, {"type": "heard", "text": "赤いブロックを右のトレイに置いて"})["ok"]
        assert wait_for(lambda: runner.executor.stats["picks_ok"] == 1, 5.0)     # 表示 → カウントダウン → ワーカーが実行
        snap = runner.state.snapshot()
        assert snap["utterance"].startswith("赤い") and snap["voice"]["phase"] == "idle"
        assert _post(port, {"type": "heard", "text": "赤じゃなくて青を右に"})["ok"]
        assert wait_for(lambda: runner.state.snapshot()["voice"]["phase"] == "confirming")
        assert _post(port, {"type": "cancel"})["ok"]
        assert wait_for(lambda: runner.state.snapshot()["voice"]["phase"] == "idle")
        time.sleep(0.2)
        assert runner.executor.stats["picks_ok"] == 1                               # 取り消したので動かない
        assert _post(port, {"type": "heard", "text": "止まって"})["ok"]              # 声の停止語 → 非常停止
        assert wait_for(lambda: runner.robot.estop.is_set())
        assert wait_for(lambda: runner.state.snapshot()["state"] == "estop")
        assert _post(port, {"type": "ptt_start"})["ok"]                            # ドライランにマイクは無い
        assert wait_for(lambda: "マイク" in runner.state.snapshot()["voice"]["message"])
        assert "voice" in runner.state.snapshot()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            html = r.read().decode("utf-8")
        assert 'id="ptt"' in html and 'id="heard"' in html and "ptt_start" in html and "これですか" not in html
    finally:
        runner.shutdown()
        runner._panel.stop()


def _front_with_factory(cfg: AppConfig, factory):
    return VoiceFront(cfg, PanelState(), submit=lambda i: None, parse=lambda t: parse(t, cfg), asr_factory=factory)


def test_voice_front_warmup_loads_the_asr_once_and_says_ready():
    # 2026-10-16: 初めてマイクを押した瞬間に faster-whisper のモデル読み込み（初回はダウンロード）が始まり、
    # 終わるまで何も表示されず「効かない」ように見えた。起動時に先読みし、できたら知らせる。
    cfg = AppConfig.default()
    calls: list[int] = []

    def factory():
        calls.append(1)
        return SimpleNamespace(rec=FakeRecorder())

    front = _front_with_factory(cfg, factory)
    try:
        front.warmup()
        assert wait_for(lambda: len(calls) == 1 and "できました" in front.view["message"])
        assert front.view["phase"] == "idle"
        front.ptt_start()
        assert wait_for(lambda: front.view["phase"] == "recording")
        assert len(calls) == 1                                    # 2 回は読み込まない
    finally:
        front.close()


def test_ptt_start_before_the_asr_is_ready_says_preparing_and_asks_to_press_again():
    cfg = AppConfig.default()

    def slow_factory():
        time.sleep(1.2)                                           # 読み込みに時間がかかる（初回ダウンロードの体）
        return SimpleNamespace(rec=FakeRecorder())

    front = _front_with_factory(cfg, slow_factory)
    try:
        front.ptt_start()
        assert wait_for(lambda: "準備しています" in front.view["message"])
        assert front.view["phase"] == "idle"                      # 録音は始めない（離した後に始まっても困る）
        front.ptt_stop()                                          # 待っている間に離す
        assert wait_for(lambda: "もう一度" in front.view["message"])
        front.ptt_start()
        assert wait_for(lambda: front.view["phase"] == "recording")
    finally:
        front.close()

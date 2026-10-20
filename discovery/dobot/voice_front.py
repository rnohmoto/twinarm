"""声の UI の中身（robotics T13 Step 2・5＋裁定 ②）: 長押し録音 → 文字起こし → ルール解析 → 表示 → カウントダウンで実行。
曖昧なら「これですか？」と確認してから実行。停止語は即時に非常停止。

役割分担
- HTTP スレッド（panel_web → demo._on_panel_command）: ptt_start / ptt_stop / heard / confirm / cancel を呼ぶ。どれも速く返る。
- ASR スレッド（このモジュールが持つ 1 本）: マイクを開く・録音の取り出し・文字起こし・解析・表示。シリアルは触らない。
- ワーカー（demo.py）: 確定した意図 {"type": "intent", "intent": {...}, "text": ...} だけを受け取って実行する。

phase: idle → recording → transcribing → proposed（カウントダウン）／confirming（これですか？）／failed → idle
表示は PanelState.voice に入れる（panel_web の JS が描く）。実機の挙動・マイクの実測はまだ無い。
"""
from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

from asr import ASRBase, Transcript, judge_transcript
from config import AppConfig
from rule_parser import Intent

UNSUPPORTED_MESSAGES = {
    "swap": "入れ替えはまだできません。1 つずつ言ってください。",
    "exclusion": "「〜以外」はまだできません。動かす物を言ってください。",
}
FAIL_MESSAGES = {
    "empty": "聞き取れませんでした。もう一度どうぞ。",
    "hallucination": "声が入っていませんでした。もう一度どうぞ。",
    "no_speech": "声が入っていませんでした。もう一度どうぞ。",
    "low_confidence": "聞き取りに自信がありません。もう一度ゆっくりどうぞ。",
}
_HINT_JA = {"largest": "一番大きい", "smallest": "一番小さい", "nearest": "一番近い",
            "leftmost": "一番左の", "rightmost": "一番右の"}


def describe(it: Intent, cfg: AppConfig, last_object: str | None = None) -> tuple[str, str]:
    """意図 → (画面に出す解釈の文, 種別)。種別: run（表示して実行）／ask（これですか？）／clarify／unsupported／unknown／stop。"""
    a = it.action
    if a == "stop":
        return "停止", "stop"
    if a == "list":
        return "見えている物を答える", "run"
    if a == "home":
        return "ホーム（待機姿勢）へ", "ask" if it.ambiguous else "run"
    if a == "tidy":
        return "片付け（物を全部スタート台へ戻す）", "ask" if it.ambiguous else "run"
    if a == "loop":
        return f"ループ ×{it.count}（運んで戻す）", "ask"
    if a == "unsupported":
        return UNSUPPORTED_MESSAGES.get(it.reason, "その操作はまだできません。"), "unsupported"
    if a == "pick_and_place":
        if it.object:
            who = cfg.label(it.object)
        elif it.count == -1:
            who = "全部の物"
        elif last_object:
            who = f"直前の物（{cfg.label(last_object)}）"
        else:
            names = "・".join(o.label for o in cfg.objects[:4])
            return f"どれを動かしますか？（{names} など）", "clarify"
        if not it.zone:
            places = "・".join(z.aliases[0] for z in cfg.zones if z.aliases)
            return f"{who}をどこに置きますか？（{places}）", "clarify"
        return f"{_HINT_JA.get(it.hint, '')}{who}を{cfg.zone_label(it.zone)}へ", ("ask" if it.ambiguous else "run")
    return "聞き取れませんでした。「赤いブロックを右のトレイに置いて」のように言ってください。", "unknown"


class VoiceFront:
    """声の UI の状態機械。submit(cmd) にワーカー向けのコマンドを渡す。state.update(voice=...) で画面に出す。"""

    def __init__(self, cfg: AppConfig, state, submit: Callable[[dict], None], parse: Callable[[str], Intent], *,
                 asr_factory: Callable[[], ASRBase] | None = None,
                 last_object: Callable[[], str | None] | None = None,
                 on_stop: Callable[[], None] | None = None,
                 on_activity: Callable[[], None] | None = None,
                 logger: Callable[[dict], None] | None = None,
                 timer_factory: Callable = threading.Timer):
        self.cfg = cfg
        self.state = state
        self.submit = submit
        self.parse = parse
        self.asr_factory = asr_factory
        self.last_object = last_object or (lambda: None)
        self.on_stop = on_stop or (lambda: None)
        self.on_activity = on_activity or (lambda: None)
        self.logger = logger or (lambda ev: None)
        self._timer_factory = timer_factory
        self._lock = threading.RLock()
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._timer = None
        self.asr: ASRBase | None = None
        self.phase = "idle"
        self.gen = 0
        self.intent: Intent | None = None
        self.view: dict = {"phase": "idle", "heard": "", "proposal": "", "question": "", "message": "",
                           "deadline": None, "auto_run_s": cfg.demo.confirm_auto_run_s, "gen": 0,
                           "ambiguous": False, "reason": "", "asr": {}}
        self._publish()

    # ------------------------------------------------------- panel 側（速い）
    def ptt_start(self) -> None:
        self.on_activity()
        self._put(("start",))

    def ptt_stop(self) -> None:
        self.on_activity()
        self._put(("stop",))

    def heard(self, text: str, run_now: bool = False) -> None:
        """文字を「聞き取った文」として入れる（ドライランの試運転・編集して Enter）。run_now なら確認を飛ばして実行。"""
        self.on_activity()
        self._put(("heard", text, run_now))

    def confirm(self) -> None:
        """Enter／はい: 表示中の解釈を実行する。"""
        self.on_activity()
        with self._lock:
            if self.phase in ("proposed", "confirming") and self.intent is not None:
                self._submit(self.intent)

    def cancel(self) -> None:
        """Esc／いいえ: 表示中の解釈を捨てる。"""
        self.on_activity()
        with self._lock:
            if self.phase in ("proposed", "confirming", "failed"):
                self._cancel_timer()
                self.gen += 1
                self.intent = None
                self._set(phase="idle", proposal="", question="", deadline=None, message="取り消しました。")

    def close(self) -> None:
        with self._lock:
            self._cancel_timer()
        rec = getattr(self.asr, "rec", None)
        if rec is not None:
            try:
                rec.close()
            except Exception:  # noqa: BLE001, S110 — 終了時は握りつぶす
                pass

    # ------------------------------------------------------------ ASR スレッド
    def _put(self, job: tuple) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, name="voice-asr", daemon=True)
                self._thread.start()
        self._jobs.put(job)

    def _loop(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                self._handle(job)
            except Exception as e:  # noqa: BLE001 — 声の UI の失敗で実演を止めない
                self.logger({"ev": "voice_error", "job": job[0], "msg": repr(e)})
                self._fail(f"声の処理に失敗しました: {type(e).__name__}")

    def _ensure_asr(self) -> ASRBase | None:
        if self.asr is None and self.asr_factory is not None:
            self.asr = self.asr_factory()
        return self.asr

    def _handle(self, job: tuple) -> None:
        kind = job[0]
        if kind == "start":
            asr = self._ensure_asr()
            rec = getattr(asr, "rec", None)
            if rec is None:
                self._fail("マイクが使えません（faster-whisper／sounddevice 未導入か、テキスト入力の設定）。文字で試してください。")
                return
            with self._lock:
                self._cancel_timer()
                self.gen += 1
                self.intent = None
            rec.start()
            self._set(phase="recording", heard="", proposal="", question="", deadline=None, message="録音中…離すと聞き取ります")
            self.logger({"ev": "ptt", "state": "start"})
        elif kind == "stop":
            asr = self.asr
            rec = getattr(asr, "rec", None)
            if self.phase != "recording" or asr is None or rec is None:
                return
            self._set(phase="transcribing", message="聞き取っています…")
            audio = rec.stop()
            tr: Transcript = asr.transcribe_detail(audio)
            text, why = judge_transcript(tr, self.cfg.asr)
            asr_info = {"avg_logprob": tr.avg_logprob, "no_speech_prob": tr.no_speech_prob,
                        "audio_s": round(tr.audio_s, 2), "seconds": round(tr.seconds, 2)}
            self.logger({"ev": "asr", "text": tr.text, "accepted": text, "why": why, **asr_info})
            if not text:
                self._fail(FAIL_MESSAGES.get(why, FAIL_MESSAGES["empty"]), heard=tr.text, asr=asr_info)
                return
            self._propose(text, asr=asr_info)
        elif kind == "heard":
            self._propose(job[1], run_now=bool(job[2]))

    # --------------------------------------------------------------- 解析→表示
    def _propose(self, text: str, run_now: bool = False, asr: dict | None = None) -> None:
        text = text.strip()
        if not text:
            self._fail(FAIL_MESSAGES["empty"])
            return
        it = self.parse(text)
        self.logger({"ev": "voice_intent", "text": text, **it.to_dict()})
        desc, kind = describe(it, self.cfg, self.last_object())
        with self._lock:
            self._cancel_timer()
            self.gen += 1
            gen = self.gen
            self.intent = it
            base = {"heard": text, "proposal": desc, "ambiguous": it.ambiguous, "reason": it.reason,
                    "asr": asr or {}, "question": "", "deadline": None}
            if kind == "stop":
                self.intent = None
                self._set(phase="idle", message="止まります。", **base)
                self.on_stop()
                return
            if kind in ("clarify", "unsupported", "unknown"):
                self.intent = None
                self._set(phase="failed", message=desc, **dict(base, proposal=""))
                return
            if run_now:
                self._submit(it)
                return
            auto = float(self.cfg.demo.confirm_auto_run_s)
            if kind == "ask" and self.cfg.demo.confirm_ask_ambiguous or auto < 0:
                q = "これですか？（Enter／はい で実行・Esc／いいえ で取消）"
                self._set(phase="confirming", message="確認を待っています", **dict(base, question=q))
                return
            if it.action == "list" or auto == 0:
                self._submit(it)
                return
            deadline = time.time() + auto
            self._set(phase="proposed", message=f"{auto:g} 秒後に実行します（Enter で今すぐ・Esc で取消）",
                      **dict(base, deadline=deadline))
            self._timer = self._timer_factory(auto, self._auto, args=(gen,))
            self._timer.daemon = True
            self._timer.start()

    def _auto(self, gen: int) -> None:
        with self._lock:
            if self.phase == "proposed" and self.gen == gen and self.intent is not None:
                self._submit(self.intent)

    def _submit(self, it: Intent) -> None:
        """ワーカーへ確定した意図を渡す（ロックの中から呼ばれる）。"""
        self._cancel_timer()
        self.gen += 1
        self.intent = None
        self.submit({"type": "intent", "intent": it.to_dict(), "text": self.view.get("heard", "")})
        self.logger({"ev": "voice_submit", **it.to_dict()})
        self._set(phase="idle", question="", deadline=None, message="実行します。")

    def _fail(self, message: str, heard: str | None = None, asr: dict | None = None) -> None:
        with self._lock:
            self._cancel_timer()
            self.gen += 1
            self.intent = None
            kw = {"phase": "failed", "proposal": "", "question": "", "deadline": None, "message": message}
            if heard is not None:
                kw["heard"] = heard
            if asr is not None:
                kw["asr"] = asr
            self._set(**kw)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _set(self, **kw) -> None:
        with self._lock:
            if "phase" in kw:
                self.phase = kw["phase"]
            self.view.update(kw)
            self.view["gen"] = self.gen
            self._publish()

    def _publish(self) -> None:
        self.state.update(voice=dict(self.view))

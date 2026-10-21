"""音声入力: 押している間だけ録音する PTT（Enter 不要）→ faster-whisper。キーボード入力の代替も同じインターフェース。

- 展示は騒がしいので常時聞き取りにせず、**押している間だけ** 録音する（robotics 113_ §4・裁定 ①）。
  マイクは最初の start() で開いて以後は開いたまま（リングバッファ）。押す少し前（preroll_s）から、離した後も少し
  （postroll_s）まで含めて語頭・語尾の欠けを防ぐ。start()／stop() はどのスレッドからでも速く返る（stop() だけは
  ポストロール分だけ待つので、文字起こしをするスレッドから呼ぶ）。
- 旧 API record(wait_key=True)（Enter → 無音で終了）はそのまま残す。
- faster-whisper は CTranslate2 で CPU 実行可。日本語は `small`〜`medium`、または
  kotoba-whisper の faster 版（例: kotoba-tech/kotoba-whisper-v2.0-faster）を ASRConfig.model に指定。
- 区間ごとの avg_logprob・no_speech_prob を Transcript に載せ、judge_transcript() で受理を判定する
  （既知の幻覚句「ご視聴ありがとうございました」は捨てる。しきい値は ASRConfig・実発話で決める）。
- 依存: sounddevice（録音）、faster-whisper。未導入なら TextASR（input()）にフォールバック。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from dobot.config import ASRConfig


@dataclass
class Transcript:
    text: str
    avg_logprob: float | None = None     # 区間の平均（低いほど自信がない）
    no_speech_prob: float | None = None  # 区間の最大（高いほど無音らしい）
    audio_s: float = 0.0                 # 渡した音声の長さ
    seconds: float = 0.0                 # 文字起こしに掛かった秒数


class ASRBase:
    rec: PushToTalkRecorder | None = None   # 録音器があれば押して話す（PTT）に対応

    def listen(self) -> str:
        raise NotImplementedError

    def transcribe_detail(self, audio: np.ndarray) -> Transcript:
        raise NotImplementedError


class TextASR(ASRBase):
    """キーボードから 1 行（ドライラン／ASR 不調時の退避）。PTT には対応しない。"""
    def __init__(self, prompt: str = "発話> "):
        self.prompt = prompt

    def listen(self) -> str:
        return input(self.prompt).strip()


class PushToTalkRecorder:
    """押している間だけ録音（start/stop）。float32 mono 16k を返す。

    stream_factory(callback) は start()/stop()/close() を持つ入力ストリームを返す（既定は sounddevice）。
    テストではフェイクのストリームと時計を渡す。
    """
    def __init__(self, cfg: ASRConfig, stream_factory: Callable | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self._factory = stream_factory
        self._clock, self._sleep = clock, sleep
        self._stream = None
        self._lock = threading.Lock()
        self._ring: deque[tuple[float, np.ndarray]] = deque()
        self._ring_s = 0.0
        self._keep_s = cfg.preroll_s + cfg.max_record_s + cfg.postroll_s + 1.0
        self._t_start: float | None = None
        self.recording = False

    # --- ストリームのスレッドから ---
    def _on_audio(self, x: np.ndarray) -> None:
        now = self._clock()
        with self._lock:
            self._ring.append((now, x))
            self._ring_s += len(x) / self.cfg.sample_rate
            while self._ring and self._ring_s > self._keep_s:
                _, old = self._ring.popleft()
                self._ring_s -= len(old) / self.cfg.sample_rate

    def _default_factory(self, callback: Callable[[np.ndarray], None]):
        import sounddevice as sd  # type: ignore
        sr = self.cfg.sample_rate

        def cb(indata, frames, t, status):
            callback(np.asarray(indata[:, 0], dtype=np.float32).copy())

        return sd.InputStream(samplerate=sr, channels=1, dtype="float32", blocksize=int(sr * 0.05), callback=cb)

    def open(self) -> None:
        if self._stream is not None:
            return
        factory = self._factory or self._default_factory
        self._stream = factory(self._on_audio)
        self._stream.start()

    def close(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None

    # --- 押した／離した ---
    def start(self) -> None:
        self.open()
        with self._lock:
            if self.recording:
                return                                  # 二重 start は無視
            self.recording = True
            self._t_start = self._clock()

    def stop(self) -> np.ndarray:
        """離した。ポストロール分だけ待ってから、押す少し前〜離した少し後の音声を返す。録音中でなければ空。"""
        with self._lock:
            if not self.recording or self._t_start is None:
                return np.zeros(0, dtype=np.float32)
            self.recording = False
            t0 = self._t_start
        t_end = self._clock()
        self._sleep(self.cfg.postroll_s)
        lo, hi = t0 - self.cfg.preroll_s, t_end + self.cfg.postroll_s
        with self._lock:
            chunks = [x for (t, x) in self._ring if lo <= t <= hi]
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        max_n = int((self.cfg.preroll_s + self.cfg.max_record_s + self.cfg.postroll_s) * self.cfg.sample_rate)
        return audio[:max_n]

    # --- 旧 API: Enter で開始 → 無音で終了 ---
    def record(self, wait_key: bool = True) -> np.ndarray:
        import sounddevice as sd  # type: ignore
        if wait_key:
            input("[Enter] を押して話してください…")
        sr = self.cfg.sample_rate
        chunks: list[np.ndarray] = []
        silent_for = 0.0
        t0 = time.time()
        block = int(sr * 0.1)
        with sd.InputStream(samplerate=sr, channels=1, dtype="float32", blocksize=block) as stream:
            while True:
                data, _ = stream.read(block)
                x = data[:, 0]
                chunks.append(x.copy())
                rms = float(np.sqrt(np.mean(x ** 2)))
                silent_for = silent_for + 0.1 if rms < self.cfg.silence_rms else 0.0
                if time.time() - t0 > self.cfg.max_record_s:
                    break
                if time.time() - t0 > 1.0 and silent_for >= self.cfg.silence_hold_s:
                    break
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


def judge_transcript(tr: Transcript, cfg: ASRConfig) -> tuple[str, str]:
    """(受理した文, 理由)。受理できなければ文は ""。理由: ok | empty | hallucination | no_speech | low_confidence。

    既知の幻覚句（無音・雑音で Whisper が出す決まり文句）は取り除く。文末に付いただけなら残りを使う。
    無音判定は faster-whisper と同じく「no_speech_prob が高く、かつ avg_logprob が低い」とき。
    """
    text = tr.text.strip()
    for ph in cfg.hallucination_phrases:
        if ph and ph in text:
            text = text.replace(ph, "").strip()
            if not text:
                return "", "hallucination"
    if not text:
        return "", "empty"
    low = tr.avg_logprob is not None and tr.avg_logprob < cfg.min_avg_logprob
    if tr.no_speech_prob is not None and tr.no_speech_prob > cfg.max_no_speech_prob and low:
        return "", "no_speech"
    if low:
        return "", "low_confidence"
    return text, "ok"


class FasterWhisperASR(ASRBase):
    rec: PushToTalkRecorder

    def __init__(self, cfg: ASRConfig, recorder: PushToTalkRecorder | None = None):
        from faster_whisper import WhisperModel  # type: ignore
        self.cfg = cfg
        self.model = WhisperModel(cfg.model, device=cfg.device, compute_type=cfg.compute_type)
        self.rec = recorder or PushToTalkRecorder(cfg)

    def transcribe_detail(self, audio: np.ndarray) -> Transcript:
        if audio.size == 0:
            return Transcript("")
        t0 = time.time()
        segments, _info = self.model.transcribe(audio, language=self.cfg.language, beam_size=1,
                                                vad_filter=True, condition_on_previous_text=False)
        segs = list(segments)
        text = "".join(s.text for s in segs).strip()
        lp = [s.avg_logprob for s in segs if getattr(s, "avg_logprob", None) is not None]
        ns = [s.no_speech_prob for s in segs if getattr(s, "no_speech_prob", None) is not None]
        return Transcript(text, avg_logprob=(sum(lp) / len(lp) if lp else None),
                          no_speech_prob=(max(ns) if ns else None),
                          audio_s=len(audio) / self.cfg.sample_rate, seconds=time.time() - t0)

    def transcribe(self, audio: np.ndarray) -> str:
        return self.transcribe_detail(audio).text

    def listen(self) -> str:
        """旧経路: Enter → 録音 → 文字起こし（demo の「listen」コマンド用。声の UI は voice_front が rec を直接使う）。"""
        audio = self.rec.record()
        tr = self.transcribe_detail(audio)
        print(f"[ASR {tr.seconds:.2f}s] {tr.text}")
        return tr.text


def make_asr(cfg: ASRConfig) -> ASRBase:
    if cfg.engine == "text":
        return TextASR()
    try:
        return FasterWhisperASR(cfg)
    except Exception as e:  # noqa: BLE001 — 未導入・モデル未取得なら退避
        print(f"[asr] faster-whisper unavailable ({e!r}); falling back to keyboard input")
        return TextASR()

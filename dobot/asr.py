"""音声入力（push-to-talk 録音 → faster-whisper）。キーボード入力の代替も同じインターフェース。

- 展示は騒がしいので常時聞き取りにせず、**押している間／押してから無音まで** の push-to-talk。
- faster-whisper は CTranslate2 で CPU 実行可。日本語は `small`〜`medium`、または
  kotoba-whisper の faster 版（例: kotoba-tech/kotoba-whisper-v2.0-faster）を ASRConfig.model に指定。
- 依存: sounddevice（録音）、faster-whisper。未導入なら TextASR（input()）にフォールバック。
"""
from __future__ import annotations

import time

import numpy as np

from config import ASRConfig


class ASRBase:
    def listen(self) -> str:
        raise NotImplementedError


class TextASR(ASRBase):
    """キーボードから 1 行（ドライラン／ASR 不調時の退避）。"""
    def __init__(self, prompt: str = "発話> "):
        self.prompt = prompt

    def listen(self) -> str:
        return input(self.prompt).strip()


class PushToTalkRecorder:
    """Enter で録音開始 → 無音 silence_hold_s 続くか max_record_s で終了。float32 mono 16k を返す。"""
    def __init__(self, cfg: ASRConfig):
        self.cfg = cfg

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


class FasterWhisperASR(ASRBase):
    def __init__(self, cfg: ASRConfig, recorder: PushToTalkRecorder | None = None):
        from faster_whisper import WhisperModel  # type: ignore
        self.cfg = cfg
        self.model = WhisperModel(cfg.model, device=cfg.device, compute_type=cfg.compute_type)
        self.rec = recorder or PushToTalkRecorder(cfg)

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size == 0:
            return ""
        segments, _info = self.model.transcribe(audio, language=self.cfg.language, beam_size=1,
                                                vad_filter=True, condition_on_previous_text=False)
        return "".join(s.text for s in segments).strip()

    def listen(self) -> str:
        audio = self.rec.record()
        t0 = time.time()
        text = self.transcribe(audio)
        print(f"[ASR {time.time()-t0:.2f}s] {text}")
        return text


def make_asr(cfg: ASRConfig) -> ASRBase:
    if cfg.engine == "text":
        return TextASR()
    try:
        return FasterWhisperASR(cfg)
    except Exception as e:
        print(f"[asr] faster-whisper unavailable ({e!r}); falling back to keyboard input")
        return TextASR()

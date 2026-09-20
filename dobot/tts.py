"""日本語 TTS（返事）。auto: VOICEVOX(ローカルHTTP) → macOS say → Windows SAPI → print。

- VOICEVOX はローカルで動くエンジン（http://127.0.0.1:50021）。展示PCで起動しておく。
- 依存を増やさないため、HTTP は urllib、再生は簡易（sounddevice があれば再生、無ければ wav 保存のみ）。
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import threading
import urllib.parse
import urllib.request

from config import TTSConfig


class TTS:
    def __init__(self, cfg: TTSConfig):
        self.cfg = cfg
        self.engine = cfg.engine
        if self.engine == "auto":
            self.engine = self._probe()

    def _probe(self) -> str:
        try:
            urllib.request.urlopen(self.cfg.voicevox_url + "/version", timeout=0.5).read()
            return "voicevox"
        except Exception:
            pass
        if platform.system() == "Darwin":
            return "say"
        if platform.system() == "Windows":
            return "sapi"
        return "print"

    def speak(self, text: str) -> None:
        if not text:
            return
        if self.cfg.blocking:
            self._speak(text)
        else:
            threading.Thread(target=self._speak, args=(text,), daemon=True).start()

    def _speak(self, text: str) -> None:
        try:
            if self.engine == "voicevox":
                self._voicevox(text)
            elif self.engine == "say":
                subprocess.run(["say", "-v", "Kyoko", text], check=False)
            elif self.engine == "sapi":
                ps = ("Add-Type -AssemblyName System.Speech; "
                      "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                      "$s.Speak([Console]::In.ReadToEnd())")
                subprocess.run(["powershell", "-NoProfile", "-Command", ps], input=text.encode("utf-8"), check=False)
            else:
                print(f"[TTS] {text}")
        except Exception as e:
            print(f"[TTS:{self.engine} failed {e!r}] {text}", file=sys.stderr)

    def _voicevox(self, text: str) -> None:
        base = self.cfg.voicevox_url
        q = urllib.parse.urlencode({"text": text, "speaker": self.cfg.voicevox_speaker})
        req = urllib.request.Request(f"{base}/audio_query?{q}", method="POST")
        query = json.loads(urllib.request.urlopen(req, timeout=3).read())
        req = urllib.request.Request(f"{base}/synthesis?speaker={self.cfg.voicevox_speaker}",
                                     data=json.dumps(query).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        wav = urllib.request.urlopen(req, timeout=10).read()
        self._play_wav(wav)

    @staticmethod
    def _play_wav(wav_bytes: bytes) -> None:
        import io
        import wave
        try:
            import numpy as np
            import sounddevice as sd  # type: ignore
            with wave.open(io.BytesIO(wav_bytes)) as w:
                sr = w.getframerate()
                n = w.getnframes()
                data = np.frombuffer(w.readframes(n), dtype=np.int16).astype("float32") / 32768.0
                if w.getnchannels() == 2:
                    data = data.reshape(-1, 2)
            sd.play(data, sr)
            sd.wait()
        except Exception:
            from pathlib import Path
            p = Path("logs") / "last_tts.wav"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(wav_bytes)
            if platform.system() == "Windows":
                ps = f"(New-Object Media.SoundPlayer '{p.resolve()}').PlaySync()"
                subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
            elif platform.system() == "Darwin":
                subprocess.run(["afplay", str(p)], check=False)
            else:
                subprocess.run(["aplay", str(p)], check=False)


if __name__ == "__main__":
    TTS(TTSConfig(blocking=True)).speak("赤いブロックを右のトレイに置きました。")

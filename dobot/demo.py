"""実演ランナー: ブラウザ UI（panel_web）＋対話モード（発話→意図→拾って置く）＋自動ループ（attract）。

  uv run python demo.py --dry-run                                     # 実機なし・合成画像。http://127.0.0.1:8790
  uv run python demo.py --config config.json --robot pydobot --port /dev/tty.usbserial-XXXX --camera-index 0 --no-llm
  uv run python demo.py ... --attract                                 # 誰も話さなければ自動で「右へ運ぶ→片付ける」
  uv run python demo.py --dry-run --once "赤いブロックを右のトレイに置いて"   # 1 発話だけ処理して終了（動作確認）

実演の形（案・robotics 91_/93_）
  対話モード: 来場者が「〜を右に置いて」→ 意図（LLM または ルール）→ 拾って置く → 返事。
              「片付けて」でスタート台に全部戻す（次の人のためのリセット）。
  自動ループ: 一定時間だれも話さなければ、スタート台の物を右トレイへ運び、少し休んで片付ける（客寄せ）。
              上限回数と休みを設定で持つ（モーター発熱の抑制）。話しかけ・ボタンで即座に対話モードへ戻る。

安全: 実機を持つのはワーカー 1 本だけ。パネルはコマンドを積むだけ。非常停止は e キー相当の `estop`
      （以後の move を拒否）＋本体の電源スイッチ。
"""
from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

import numpy as np

from camera import CameraBase
from panel_web import PanelServer, PanelState


class SharedCamera(CameraBase):
    """開いたカメラを別スレッドで読み続け、read() は最新フレームのコピーを返す（配信と実行で 1 台を共有）。"""

    def __init__(self, inner: CameraBase, hz: float = 30.0):
        self.inner = inner
        self.hz = hz
        self._latest: np.ndarray | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.frames = 0
        self.errors = 0

    def open(self) -> None:
        self._latest = self.inner.read()
        self._thread = threading.Thread(target=self._loop, name="camera-grab", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        period = 1.0 / max(1.0, self.hz)
        while not self._stop.is_set():
            t = time.time()
            try:
                f = self.inner.read()
                with self._lock:
                    self._latest = f
                self.frames += 1
            except Exception:  # noqa: BLE001 — USB 瞬断は数フレーム落として続ける
                self.errors += 1
                time.sleep(0.05)
            time.sleep(max(0.0, period - (time.time() - t)))

    def read(self) -> np.ndarray:
        with self._lock:
            if self._latest is None:
                raise RuntimeError("SharedCamera: no frame yet")
            return self._latest.copy()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self.inner.close()


def attract_due(now: float, last_activity: float, idle_s: float, paused_until: float,
                cycles_last_hour: int, max_per_hour: int, enabled: bool, estop: bool) -> bool:
    """自動ループを今始めてよいか（純関数・テスト対象）。"""
    if not enabled or estop:
        return False
    if now < paused_until or cycles_last_hour >= max_per_hour:
        return False
    return (now - last_activity) >= idle_s


class ThermalGuard:
    """腕を動かした時間の予算: 直近 window_s 秒のうち budget_s 秒まで。超えたら cooldown_s 秒は自動動作を止める。

    ステッピングモーターは動き続けると熱を持つ（Magician は連続稼働の発熱に注意）。数値は設定（DemoConfig）。
    """

    def __init__(self, budget_s: float, window_s: float, cooldown_s: float):
        self.budget_s, self.window_s, self.cooldown_s = budget_s, window_s, cooldown_s
        self.intervals: list[tuple[float, float]] = []
        self.rest_until = 0.0
        self.temp_c: float | None = None
        self.temp_hot = False

    def set_temperature(self, temp_c: float | None, stop_c: float, resume_c: float) -> None:
        """温度センサがあるときの二段しきい値（stop_c 以上で休止・resume_c 未満で再開）。"""
        self.temp_c = temp_c
        if temp_c is None:
            return
        if temp_c >= stop_c:
            self.temp_hot = True
        elif temp_c < resume_c:
            self.temp_hot = False

    def _trim(self, now: float) -> None:
        cutoff = now - self.window_s
        self.intervals = [(a, b) for a, b in self.intervals if b > cutoff]

    def record(self, t0: float, t1: float) -> None:
        if t1 > t0:
            self.intervals.append((t0, t1))
        if self.motion_s(t1) >= self.budget_s:
            self.rest_until = max(self.rest_until, t1 + self.cooldown_s)

    def motion_s(self, now: float) -> float:
        self._trim(now)
        cutoff = now - self.window_s
        return sum(b - max(a, cutoff) for a, b in self.intervals)

    def duty(self, now: float) -> float:
        return self.motion_s(now) / self.window_s if self.window_s > 0 else 0.0

    def resting_s(self, now: float) -> float:
        return max(0.0, self.rest_until - now)

    def can_run(self, now: float) -> bool:
        return (not self.temp_hot) and now >= self.rest_until and self.motion_s(now) < self.budget_s


class DemoRunner:
    def __init__(self, args):
        from main import build
        cfg, camera, robot, executor, agent, logger = build(args)
        self.cfg, self.robot, self.executor, self.agent, self.logger = cfg, robot, executor, agent, logger
        self.camera = SharedCamera(camera)
        self.camera.open()
        executor.camera = self.camera
        self.state = PanelState()
        self.queue: queue.Queue = queue.Queue()
        self.running = threading.Event()
        self.running.set()
        self.busy = threading.Event()
        self.last_activity = time.time()
        self.paused_until = 0.0
        self.cycle_times: list[float] = []
        self.guard = ThermalGuard(cfg.demo.motion_budget_s, cfg.demo.budget_window_s, cfg.demo.cooldown_s)
        executor.loop_handler = self.run_loop     # 「ループして」（ルール／LLM）→ ここ
        self.attract_on = bool(args.attract or cfg.demo.attract)
        self.asr = None
        self.tts = None
        self._panel: PanelServer | None = None
        if hasattr(logger, "listeners"):
            logger.listeners.append(self._on_event)
        cam_info = {"backend": cfg.camera.backend, "index": str(cfg.camera.index)}
        if hasattr(camera, "actual_props"):
            try:
                p = camera.actual_props()
                cam_info.update({"実効": f"{int(p['width'])}x{int(p['height'])}@{p['fps']:.0f}",
                                 "露出固定": "効いている" if getattr(camera, "auto_exposure_applied", None) is not None else "未確認/自動",
                                 "backend": p.get("backend") or cfg.camera.backend})
            except Exception:  # noqa: BLE001
                pass
        self.state.update(camera=cam_info, robot={"robot": cfg.robot.backend, "port": cfg.robot.port or "-"},
                          mode="attract" if self.attract_on else "dialog", state="idle",
                          attract=self._attract_info(), stats=dict(executor.stats, cycles=0))

    # ------------------------------------------------------------- helpers
    def _attract_info(self) -> dict:
        now = time.time()
        return {"on": self.attract_on, "idle_s": self.cfg.demo.idle_s, "pause_s": self.cfg.demo.pause_s,
                "zone": self.cfg.demo.attract_zone, "count": self.cfg.demo.attract_count,
                "cycles_last_hour": len(self._recent_cycles()), "max_per_hour": self.cfg.demo.max_cycles_per_hour,
                "duty_pct": round(self.guard.duty(now) * 100), "rest_s": round(self.guard.resting_s(now)),
                "temp_c": (round(self.guard.temp_c, 1) if self.guard.temp_c is not None else None),
                "temp_hot": self.guard.temp_hot}

    def read_temperature(self) -> float | None:
        """温度センサ（demo.temp_eio）を 1 回読んで熱の予算に渡す。無効・失敗なら None。"""
        d = self.cfg.demo
        if not d.temp_eio or not hasattr(self.robot, "read_adc"):
            return None
        from robot_dobot import thermistor_c
        try:
            if not getattr(self, "_temp_mux_done", False):
                self.robot.set_io_multiplexing(d.temp_eio, "adc")
                self._temp_mux_done = True
            adc = self.robot.read_adc(d.temp_eio)
        except Exception as e:  # noqa: BLE001 — センサ不調で実演を止めない
            self.state.update(error=f"temp: {e!r}")
            return None
        t = thermistor_c(adc, d.temp_r_pullup, d.temp_r25, d.temp_beta, 3.3, d.temp_adc_fullscale_v)
        was_hot = self.guard.temp_hot
        self.guard.set_temperature(t, d.temp_stop_c, d.temp_resume_c)
        robot_info = dict(self.state.snapshot().get("robot") or {})
        robot_info["温度"] = f"{t:.1f} ℃ (adc {adc})" if t is not None else f"範囲外 (adc {adc})"
        self.state.update(robot=robot_info, attract=self._attract_info())
        if self.guard.temp_hot and not was_hot:
            self.state.push_event(f"温度 {t:.1f} ℃ ≥ {d.temp_stop_c} ℃ → 自動動作を休止")
        elif was_hot and not self.guard.temp_hot:
            self.state.push_event(f"温度 {t:.1f} ℃ < {d.temp_resume_c} ℃ → 自動動作を再開")
        return t

    def _record_motion(self, t0: float, picks_before: int) -> None:
        """腕が実際に動いた時間だけを熱の予算に積む（拾った数が増えたときだけ）。"""
        if self.executor.stats["picks_ok"] + self.executor.stats["picks_fail"] > picks_before:
            self.guard.record(t0, time.time())

    def _recent_cycles(self) -> list[float]:
        now = time.time()
        self.cycle_times = [t for t in self.cycle_times if now - t < 3600]
        return self.cycle_times

    def _on_event(self, ev: dict) -> None:
        kind = ev.get("ev")
        if kind == "rule_intent":
            self.state.update(intent={k: ev.get(k) for k in ("action", "object", "zone", "count", "hint")})
            self.state.push_event(f"意図（ルール）: {ev.get('action')} {ev.get('object') or ''} → {ev.get('zone') or ''}")
        elif kind == "tool_call":
            self.state.update(intent={"tool": ev.get("name"), **(ev.get("input") or {})})
            self.state.push_event(f"意図（LLM）: {ev.get('name')} {ev.get('input') or ''}")
        elif kind == "pick":
            t = ev.get("target") or {}
            self.state.update(last_pick={"物": t.get("name"), "から": [t.get("x_mm"), t.get("y_mm")], "へ": ev.get("place"),
                                         "ゾーン": ev.get("zone")})
            self.state.push_event(f"拾う: {t.get('name')} → {ev.get('zone')}")
        elif kind == "error":
            self.state.update(error=f"{ev.get('type')}: {ev.get('msg', '')}")
            self.state.push_event(f"エラー: {ev.get('type')}")
        elif kind == "llm":
            self.state.push_event(f"LLM: {ev.get('stop')} in={ev.get('in')} out={ev.get('out')} {ev.get('ms')}ms")
        elif kind == "llm_unavailable":
            self.state.push_event("LLM が使えないためルール解析で動作")

    def _publish(self, dets, status: str) -> None:
        import cv2

        from detect import annotate
        frame = self.executor.last_frame if self.executor.last_frame is not None else self.camera.read()
        vis = annotate(frame, dets, status)
        ok, buf = cv2.imencode(".jpg", vis, [int(cv2.IMWRITE_JPEG_QUALITY), self.cfg.demo.jpeg_quality])
        if ok:
            self.state.set_frame(buf.tobytes())
        seen: dict[str, int] = {}
        for d in dets:
            seen[d.name] = seen.get(d.name, 0) + 1
        self.state.update(objects_seen=seen, stats=dict(self.executor.stats, cycles=len(self._recent_cycles())),
                          attract=self._attract_info())

    def _speak(self, text: str) -> None:
        if self.tts is None:
            try:
                from tts import TTS
                self.tts = TTS(self.cfg.tts)
            except Exception:  # noqa: BLE001
                self.tts = False
        if self.tts:
            try:
                self.tts.speak(text)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------- actions
    def handle_utterance(self, text: str) -> str:
        self.last_activity = time.time()
        self.state.update(mode="dialog", state="thinking", utterance=text, reply="", error="")
        self.state.push_event(f"発話: {text}")
        self.logger({"ev": "utterance", "text": text})
        t0 = time.time()
        picks0 = self.executor.stats["picks_ok"] + self.executor.stats["picks_fail"]
        self.busy.set()
        try:
            self.state.update(state="moving")
            reply = self.agent.handle(text)
        except Exception as e:  # noqa: BLE001 — 実演を止めない
            reply = "うまく動けませんでした。もう一度お願いします。"
            self.state.update(error=repr(e))
            self.logger({"ev": "error", "type": type(e).__name__, "msg": str(e)})
        finally:
            self.busy.clear()
            self._record_motion(t0, picks0)
        self.logger({"ev": "reply", "text": reply, "ms": int((time.time() - t0) * 1000)})
        self.state.update(state="estop" if self.robot.estop.is_set() else "idle", reply=reply)
        self.state.push_event(f"返事: {reply}")
        self._speak(reply)
        return reply

    def handle_command(self, cmd: dict) -> None:
        t = cmd.get("type")
        self.last_activity = time.time()
        if t == "say":
            self.handle_utterance(str(cmd.get("text", "")).strip() or "何がある？")
        elif t == "listen":
            self.state.update(state="listening")
            if self.asr is None:
                from asr import make_asr
                self.asr = make_asr(self.cfg.asr)
            try:
                utt = self.asr.listen()
            except Exception as e:  # noqa: BLE001
                utt = ""
                self.state.update(error=f"ASR: {e!r}")
            if utt:
                self.handle_utterance(utt)
            else:
                self.state.update(state="idle")
                self.state.push_event("聞き取れませんでした")
        elif t == "home":
            self.busy.set()
            try:
                r = self.executor.go_home()
            finally:
                self.busy.clear()
            self.state.update(reply=r.message, state="idle")
            self.state.push_event(r.message)
        elif t == "tidy":
            t0, picks0 = time.time(), self.executor.stats["picks_ok"] + self.executor.stats["picks_fail"]
            self.busy.set()
            self.state.update(state="moving")
            try:
                r = self.executor.tidy_up()
            finally:
                self.busy.clear()
                self._record_motion(t0, picks0)
            self.state.update(reply=r.message, state="idle")
            self.state.push_event(r.message)
        elif t == "loop":
            r = self.run_loop(int(cmd.get("cycles", self.cfg.demo.loop_cycles)))
            self.state.update(reply=r.message, state="estop" if self.robot.estop.is_set() else "idle")
        elif t == "estop":
            r = self.executor.stop()
            self.attract_on = False
            self.state.update(state="estop", reply=r.message, mode="dialog", attract=self._attract_info())
            self.state.push_event("非常停止")
        elif t == "resume":
            r = self.executor.resume()
            self.state.update(state="idle", reply=r.message, error="")
            self.state.push_event("停止解除")
        elif t == "attract":
            self.attract_on = bool(cmd.get("on", not self.attract_on))
            self.state.update(mode="attract" if self.attract_on else "dialog", attract=self._attract_info())
            self.state.push_event(f"自動ループ {'ON' if self.attract_on else 'OFF'}")
        elif t == "quit":
            self.running.clear()

    def attract_cycle(self, count: int | None = None, label: str = "自動ループ") -> bool:
        """1 サイクル: スタート台の物を count 個 attract_zone へ運ぶ → 一休み → 片付ける。完走したら True。"""
        d = self.cfg.demo
        count = count or d.attract_count
        self.state.update(state="moving", utterance=f"({label})")
        self.state.push_event(f"{label}: 運ぶ（{count} 個）")
        t0, picks0 = time.time(), self.executor.stats["picks_ok"] + self.executor.stats["picks_fail"]
        self.busy.set()
        try:
            r1 = self.executor.pick_and_place(None, d.attract_zone, "any", count=count)
            self.state.update(reply=r1.message)
            if not r1.ok:
                self.attract_on = False
                self.state.push_event(f"{label}停止: {r1.message}")
                return False
            self.busy.clear()
            self._record_motion(t0, picks0)
            self.state.update(state="idle")
            t_end = time.time() + min(d.pause_s, 8.0)   # 運んだ後の小休止（見せ場）。話しかけがあれば譲る
            while time.time() < t_end and self.running.is_set() and self.queue.empty():
                self._tick_frame(f"{label}: 一休み")
                time.sleep(0.1)
            if not self.queue.empty():
                return False
            t1, picks1 = time.time(), self.executor.stats["picks_ok"] + self.executor.stats["picks_fail"]
            self.busy.set()
            self.state.update(state="moving")
            self.state.push_event(f"{label}: 片付け")
            r2 = self.executor.tidy_up()
            self.state.update(reply=r2.message)
            self._record_motion(t1, picks1)
        finally:
            self.busy.clear()
            self.state.update(state="estop" if self.robot.estop.is_set() else "idle", attract=self._attract_info())
        self.cycle_times.append(time.time())
        self.paused_until = time.time() + d.pause_s
        self.last_activity = time.time()
        return True

    def run_loop(self, cycles: int):
        """「ループして」: 運ぶ→戻す を cycles 回。熱の予算・上限回数・話しかけ・非常停止で途中でも止まる。"""
        from planner import Result
        cycles = max(1, min(5, int(cycles)))
        done = 0
        self.state.update(mode="dialog")
        self.state.push_event(f"ループ開始: {cycles} 回")
        for i in range(cycles):
            now = time.time()
            if self.robot.estop.is_set():
                break
            if not self.guard.can_run(now):
                msg = f"{done} 回で休憩に入ります（腕の熱の予算・あと {round(self.guard.resting_s(now))} 秒）。"
                self.state.push_event(msg)
                return Result(done > 0, msg, {"cycles": done})
            if len(self._recent_cycles()) >= self.cfg.demo.max_cycles_per_hour:
                msg = f"{done} 回で止めます（1 時間の上限 {self.cfg.demo.max_cycles_per_hour} 回）。"
                self.state.push_event(msg)
                return Result(done > 0, msg, {"cycles": done})
            if not self.attract_cycle(count=self.cfg.demo.attract_count, label=f"ループ {i + 1}/{cycles}"):
                break
            done += 1
            if not self.queue.empty():   # 話しかけ・ボタンがあれば譲る
                break
        msg = f"{done} 回繰り返しました。" if done else "繰り返せませんでした。"
        self.state.push_event(msg)
        return Result(done > 0, msg, {"cycles": done})

    def _tick_frame(self, status: str) -> None:
        try:
            dets = self.executor.observe()
        except Exception as e:  # noqa: BLE001
            self.state.update(error=f"camera: {e!r}")
            return
        self._publish(dets, status)

    # ------------------------------------------------------------------ run
    def start_panel(self, port: int | None = None) -> int:
        self._panel = PanelServer(self.state, self.queue.put, port=port or self.cfg.demo.panel_port,
                                  stream_fps=self.cfg.demo.stream_fps)
        p = self._panel.start()
        self.state.push_event(f"パネル起動 http://127.0.0.1:{p}")
        return p

    def run(self, seconds: float | None = None) -> None:
        d = self.cfg.demo
        t_start = time.time()
        frame_period = 1.0 / max(1, d.stream_fps)
        last_frame = 0.0
        last_temp = 0.0
        n_frames = 0
        fps_t0 = time.time()
        try:
            self.executor.go_home()
        except Exception as e:  # noqa: BLE001
            self.state.update(error=f"home: {e!r}")
        while self.running.is_set():
            if seconds is not None and time.time() - t_start > seconds:
                break
            try:
                cmd = self.queue.get(timeout=0.05)
            except queue.Empty:
                cmd = None
            if cmd is not None:
                try:
                    self.handle_command(cmd)
                except Exception as e:  # noqa: BLE001
                    self.state.update(error=repr(e), state="idle")
                    self.state.push_event(f"コマンド失敗: {e!r}")
                continue
            now = time.time()
            if d.temp_eio and now - last_temp >= d.temp_period_s:
                self.read_temperature()
                last_temp = now
            if now - last_frame >= frame_period:
                status = {"idle": "ready: talk / tidy / attract", "estop": "E-STOP (resume to continue)"}.get(
                    self.state.snapshot().get("state", "idle"), "")
                self._tick_frame(status)
                last_frame = now
                n_frames += 1
                if now - fps_t0 >= 2.0:
                    self.state.update(fps=round(n_frames / (now - fps_t0), 1))
                    n_frames, fps_t0 = 0, now
            if attract_due(now, self.last_activity, d.idle_s, self.paused_until, len(self._recent_cycles()),
                           d.max_cycles_per_hour, self.attract_on, self.robot.estop.is_set()) and self.guard.can_run(now):
                try:
                    self.state.update(mode="attract")
                    self.attract_cycle()
                except Exception as e:  # noqa: BLE001
                    self.attract_on = False
                    self.state.update(error=repr(e), state="idle")
                    self.state.push_event(f"自動ループ失敗: {e!r}")

    def shutdown(self) -> None:
        self.running.clear()
        try:
            if not self.robot.estop.is_set():
                self.executor.go_home()
        except Exception:  # noqa: BLE001
            pass
        if self._panel is not None:
            self._panel.stop()
        try:
            self.robot.disconnect()
        finally:
            self.camera.close()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Dobot Magician demo runner (browser panel + dialog + attract loop)")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--dry-run", action="store_true", help="カメラ=合成画像・ロボット=記録のみ")
    ap.add_argument("--no-llm", action="store_true", help="ルールベース解析のみ")
    ap.add_argument("--robot", choices=["dry", "pydobot"], default=None)
    ap.add_argument("--port", default=None, help="Magician のシリアルポート（推測しない）")
    ap.add_argument("--camera-index", default=None)
    ap.add_argument("--attract", action="store_true", help="自動ループを最初から ON")
    ap.add_argument("--panel-port", type=int, default=None)
    ap.add_argument("--no-panel", action="store_true")
    ap.add_argument("--once", default=None, help="この 1 発話だけ処理して終了")
    ap.add_argument("--seconds", type=float, default=None, help="この秒数で自動終了（動作確認用）")
    args = ap.parse_args(argv)
    if args.camera_index is not None and str(args.camera_index).isdigit():
        args.camera_index = int(args.camera_index)
    # main.build が見る属性を揃える（demo は常にキーボード/パネル入力・OpenCV パネルは使わない）
    d = dict(vars(args))
    d.update(text=None, keyboard=True, tune=False)
    return SimpleNamespace(**d)


def main(argv=None) -> int:
    args = parse_args(argv)
    runner = DemoRunner(args)
    try:
        if args.once is not None:
            reply = runner.handle_utterance(args.once)
            print("reply:", reply)
            print("\n".join(runner.robot.log))
            return 0
        if not args.no_panel:
            port = runner.start_panel(args.panel_port)
            print(f"panel: http://127.0.0.1:{port}   (Ctrl+C で終了)")
        runner.run(seconds=args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        runner.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

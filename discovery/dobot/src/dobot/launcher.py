"""素人向けの操作口（ランチャー）: ブラウザの 1 ページで、実演の起動・停止・再起動、較正、置き場の調整、
よく触る設定の変更、ログの確認をする。ポート 8789。

- 実演（demo.py）は子プロセスとして起動する。停止はパネルの quit で（腕がホームへ戻ってから切断）。
- 腕が動くのは「実演を起動／再起動」（起動時にホームへ）と「較正」（格子を回る）だけで、どちらも
  ボタンを押したときだけ。較正は端末（Terminal）を開いて走らせる（各点で Enter を押す対話があるため）。
- 置き場の調整（zones_view.py）とカメラ確認は腕を動かさない。実演が動いている間はカメラとポートを
  渡せないので、先に停止してから開く。
起動: ./scripts/start.sh launcher（Mac は scripts/start.command をダブルクリック）。--dry で実機なしの実演を起動する。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dobot.config import AppConfig
from dobot.paths import CONFIG_PATH, PROJECT_ROOT, from_root
from dobot.setup_wizard import save_config_with_backup

LAUNCHER_PORT = 8789
QUIT_WAIT_S = 20.0          # quit を送ってから子プロセスが終わるのを待つ秒数（ホームへ戻る時間を含む）
LOG_TAIL_LINES = 40
CALIBRATE_OBJECT = "blue_cube"

# 画面から触れる設定: (キー, 表示名, 型, 範囲または選択肢, 説明)
SETTINGS: list[tuple[str, str, str, tuple | None, str]] = [
    ("robot.speed_pct", "速さ（%）", "number", (10, 100), "Magician の速度比。脱調アラームや落下なら 70 に"),
    ("robot.z_safe", "退避の高さ z_safe（mm）", "number", (-40, 120), "運ぶときの高さ。箱の縁より上に"),
    ("robot.ee_settle_s", "吸着の待ち（秒）", "number", (0.0, 3.0), "吸えずに落とすなら 0.6"),
    ("robot.travel_mode", "移動の補間", "choice", ("movj", "movl"), "movj=速い（経路は曲がる）/ movl=直線"),
    ("demo.panel_mirror", "映像を左右反転", "bool", None, "来場者がロボットと向かい合う向き"),
    ("demo.stream_fps", "映像の fps", "number", (1, 30), ""),
    ("demo.confirm_auto_run_s", "声の確認待ち（秒）", "number", (0.0, 10.0), "聞き取りから実行までの秒数"),
    ("llm.enabled", "Claude で解釈（API キーが要る）", "bool", None, "OFF ならルール解析のみ"),
]


# ------------------------------------------------------------------ settings
def read_settings(cfg: AppConfig) -> dict:
    out = {}
    for key, *_ in SETTINGS:
        section, name = key.split(".", 1)
        out[key] = getattr(getattr(cfg, section), name)
    return out


def _parse(kind: str, spec, raw, current):
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("on", "true", "1", "yes")
    if kind == "choice":
        v = str(raw).strip()
        if v not in spec:
            raise ValueError(f"{'/'.join(spec)} のどれか")
        return v
    v = float(str(raw).strip())
    lo, hi = spec
    if not (lo <= v <= hi):
        raise ValueError(f"{lo}〜{hi} の範囲")
    return int(v) if isinstance(current, int) and not isinstance(current, bool) else v


def apply_settings(cfg: AppConfig, form: dict) -> tuple[AppConfig, list[str]]:
    """画面の入力を検証して反映した新しい AppConfig を返す（元は変えない）。誤りは errors に集め、1 つでもあれば反映しない。"""
    errors: list[str] = []
    changes: dict[str, dict] = {}
    for key, label, kind, spec, _ in SETTINGS:
        if key not in form:
            continue
        section, name = key.split(".", 1)
        current = getattr(getattr(cfg, section), name)
        try:
            changes.setdefault(section, {})[name] = _parse(kind, spec, form[key], current)
        except ValueError as e:
            errors.append(f"{label}（{key}）: {e}")
    if errors:
        return cfg, errors
    new = cfg
    for section, kw in changes.items():
        new = replace(new, **{section: replace(getattr(new, section), **kw)})
    return new, []


# ------------------------------------------------------------------ process control
def quit_demo_via_panel(port: int) -> bool:
    """実演のパネルに quit を送る（腕はホームへ戻ってから切断される）。届かなければ False。"""
    req = urllib.request.Request(f"http://127.0.0.1:{port}/cmd", data=json.dumps({"type": "quit"}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return bool(json.loads(r.read()).get("ok"))
    except Exception:  # noqa: BLE001 — 届かない＝もう死んでいるか別の理由。呼び出し側が terminate で片付ける
        return False


def _spawn_default(args: list[str], log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    f = log_path.open("ab")
    env = dict(os.environ, PYTHONUNBUFFERED="1")   # 子の出力をすぐファイルへ（ログ欄に出すため）
    return subprocess.Popen(args, cwd=str(PROJECT_ROOT), stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)


class Launcher:
    def __init__(self, config_path: str | Path, spawn: Callable | None = None, quit_demo: Callable[[int], bool] | None = None,
                 wait_s: float = QUIT_WAIT_S, dry: bool = False):
        self.config_path = Path(config_path)
        self._spawn = spawn or _spawn_default
        self._quit = quit_demo or quit_demo_via_panel
        self.wait_s = wait_s
        self.dry = dry
        self.proc = None
        self.log_path: Path | None = None
        self.last_message = ""
        self.started_at: float | None = None
        self._lock = threading.RLock()

    # --- config
    def cfg(self) -> AppConfig:
        return AppConfig.load(self.config_path) if self.config_path.exists() else AppConfig.default()

    def _log_dir(self) -> Path:
        return from_root(self.cfg().log_dir)

    def demo_args(self) -> list[str]:
        args = [sys.executable, "-m", "dobot.demo", "--config", str(self.config_path)]
        if self.dry:
            args += ["--dry-run", "--no-llm"]
        return args

    # --- demo process
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> bool:
        with self._lock:
            if self.running():
                self.last_message = "実演はもう動いています"
                return False
            self.log_path = self._log_dir() / "demo_launcher.log"
            self.proc = self._spawn(self.demo_args(), self.log_path)
            self.started_at = time.time()
            self.last_message = "実演を起動しました（腕がホームへ動きます）"
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self.running():
                self.last_message = "実演は動いていません"
                return True
            answered = self._quit(self.cfg().demo.panel_port)
            end = time.time() + self.wait_s
            while self.proc.poll() is None and time.time() < end:
                time.sleep(0.05)
            if self.proc.poll() is None:
                self.proc.terminate()
                end = time.time() + 3.0
                while self.proc.poll() is None and time.time() < end:
                    time.sleep(0.05)
                self.last_message = "返事が無かったので強制終了しました" + ("" if answered else "（quit が届かなかった）")
            else:
                self.last_message = "実演を止めました（ホームへ戻って切断）"
            return True

    def restart(self) -> bool:
        self.stop()
        return self.start()

    # --- tools (arm does not move unless the tool itself moves it)
    CALIBRATE_MODES = {
        # mode: (フラグ, 端末を開いたときの案内)
        "carry": ("--carry", "端末を開きました。腕が 9 点を回ります: 最初の点でだけ青ブロックを吸盤の真下に置いて Enter、あとは腕が運びます（約 1 分）"),
        "verify": ("--verify", "端末を開きました。見えている青ブロックを拾って中心へ置き、ずれを測ります（約 30 秒）"),
        "drive": ("--drive", "端末を開きました。腕が 9 点を回ります: 各点で青ブロックを吸盤の真下に置いて Enter"),
    }

    def calibrate_command(self, mode: str = "carry") -> str:
        flag = self.CALIBRATE_MODES[mode][0]
        return f"cd {PROJECT_ROOT} && uv run dobot-calibrate --config {self.config_path} --object {CALIBRATE_OBJECT} {flag}"

    def verify_command(self) -> str:
        return self.calibrate_command("verify")

    def open_calibration(self, mode: str = "carry") -> str:
        """実演を止めてから、端末で較正（carry=腕が運ぶ・verify=確認・drive=各点で人が置く。腕が動く）を開く。
        Mac 以外はコマンドを返すだけ。"""
        if mode not in self.CALIBRATE_MODES:
            raise ValueError(f"unknown calibration mode {mode}")
        self.stop()
        command = self.calibrate_command(mode)
        if platform.system() != "Darwin":
            self.last_message = "端末で次を実行してください: " + command
            return command
        script = self._log_dir() / f"calibrate_{mode}.command"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/usr/bin/env bash\n" + command + "\necho; read -r -p '終わりました。Enter で閉じる'\n",
                          encoding="utf-8")
        script.chmod(0o755)
        subprocess.Popen(["open", "-a", "Terminal", str(script)])
        self.last_message = self.CALIBRATE_MODES[mode][1]
        return command

    def calibration_info(self) -> dict | None:
        """assets/homography.json の日時・RMS・点数（無ければ None）。画面の「較正: …」に出す。"""
        hp = from_root(self.cfg().homography_path)
        if not hp.exists():
            return None
        try:
            d = json.loads(hp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"created": "?", "rms_mm": None, "points": 0}
        rms = d.get("rms_error_mm")
        return {"created": d.get("created", "?"), "rms_mm": round(float(rms), 2) if rms is not None else None,
                "points": len(d.get("pairs") or [])}

    def open_zones(self) -> None:
        """実演を止めてから置き場の調整画面（zones_view.py）を開く。腕は動かない。"""
        self.stop()
        self._spawn([sys.executable, "-m", "dobot.zones_view", "--config", str(self.config_path)],
                    self._log_dir() / "zones_view.log")
        self.last_message = "置き場の画面を開きました。数字で選んでクリック、s で保存、q で閉じる"

    def camera_snapshot(self) -> bytes | None:
        """実演が止まっているときだけ、カメラを 1 枚読んで JPEG を返す（動いている間は実演画面で見る）。"""
        if self.running():
            return None
        import cv2

        from dobot.camera import make_camera
        with make_camera(self.cfg().camera) as cam:
            frame = cam.read()
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        return buf.tobytes() if ok else None

    # --- settings / status
    def save_settings(self, form: dict) -> tuple[bool, list[str]]:
        new, errors = apply_settings(self.cfg(), form)
        if errors:
            return False, errors
        save_config_with_backup(new, self.config_path)
        self.last_message = "設定を保存しました。実演を再起動すると反映されます"
        return True, []

    def log_tail(self, n: int = LOG_TAIL_LINES) -> str:
        if not self.log_path or not self.log_path.exists():
            return ""
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(line for line in lines[-n:] if "VIRTUAL_ENV" not in line and "objc[" not in line)

    def status(self) -> dict:
        cfg = self.cfg()
        hp = from_root(cfg.homography_path)
        return {"running": self.running(), "pid": self.proc.pid if self.running() else None,
                "panel_url": f"http://127.0.0.1:{cfg.demo.panel_port}", "message": self.last_message,
                "settings": read_settings(cfg), "log": self.log_tail(), "config_path": str(self.config_path),
                "calibrated": hp.exists(), "calibration": self.calibration_info(),
                "robot": {"backend": cfg.robot.backend, "port": cfg.robot.port},
                "uptime_s": round(time.time() - self.started_at) if self.running() and self.started_at else 0, "dry": self.dry}


# ------------------------------------------------------------------ page
def _settings_form(cfg: AppConfig) -> str:
    rows = []
    for key, label, kind, spec, help_ in SETTINGS:
        section, name = key.split(".", 1)
        cur = getattr(getattr(cfg, section), name)
        if kind == "bool":
            ctl = f'<input type="checkbox" name="{key}" {"checked" if cur else ""}>'
        elif kind == "choice":
            opts = "".join(f'<option value="{o}" {"selected" if o == cur else ""}>{o}</option>' for o in spec)
            ctl = f'<select name="{key}">{opts}</select>'
        else:
            ctl = f'<input type="number" step="any" name="{key}" value="{cur}" min="{spec[0]}" max="{spec[1]}">'
        rows.append(f'<label class="row"><span class="lbl">{label}<small>{key}</small></span>{ctl}<small class="help">{help_}</small></label>')
    return "\n".join(rows)


PAGE_HEAD = """<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dobot 実演 ランチャー</title>
<style>
 body{margin:0;font-family:'Segoe UI',Meiryo,sans-serif;background:#1b1f24;color:#e6e6e6}
 header{background:#2c3e50;padding:12px 18px;font-size:18px;font-weight:bold;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
 #run{padding:4px 14px;border-radius:999px;background:#1b1f24;font-size:16px}.on{color:#7bd88f}.off{color:#9fb3c8}
 main{max-width:900px;margin:0 auto;padding:14px;display:flex;flex-direction:column;gap:12px}
 .card{background:#262b33;border-radius:8px;padding:14px 16px}.card h3{margin:0 0 10px;font-size:14px;color:#9fb3c8;text-transform:uppercase}
 button{background:#3498db;color:#fff;border:0;border-radius:8px;padding:14px 18px;margin:4px 6px 4px 0;font-size:17px;cursor:pointer}
 button.big{font-size:22px;padding:20px 26px}button.danger{background:#c0392b}button.warn{background:#e67e22}button.ghost{background:#4a5568}
 button:disabled{opacity:.4;cursor:default}
 .msg{min-height:22px;color:#f7c948;margin-top:6px}.help{color:#9fb3c8}
 .row{display:grid;grid-template-columns:260px 160px 1fr;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid #333}
 .row small{display:block;color:#9fb3c8;font-size:11px}.row input,.row select{font-size:16px;padding:6px;border-radius:6px;border:1px solid #555;background:#111;color:#eee}
 pre{background:#111;padding:10px;border-radius:6px;max-height:260px;overflow:auto;font-size:12px;white-space:pre-wrap}
 a.panel{display:inline-block;background:#2d6a4f;color:#fff;text-decoration:none;border-radius:8px;padding:14px 18px;font-size:17px;margin:4px 6px 4px 0}
 ol{line-height:1.7}
 img#snap{max-width:100%;border-radius:8px;display:none;margin-top:8px}
</style></head><body>
<header><span>Dobot 実演 ランチャー</span><span id="run" class="off">-</span><span id="dry" class="help"></span></header>
<main>
 <div class="card"><h3>実演</h3>
  <button class="big" onclick="act('start')" id="b-start">▶ 実演を起動（腕がホームへ動きます）</button>
  <a class="panel" id="panel" href="#" target="_blank">実演画面を開く</a>
  <button class="danger" onclick="act('stop')" id="b-stop">■ 停止</button>
  <button class="ghost" onclick="act('restart')" id="b-restart">↻ 再起動（設定を反映）</button>
  <div class="msg" id="msg">-</div>
 </div>
 <div class="card"><h3>調整（実演は自動で止まります）</h3>
  <div id="calinfo" class="help" style="margin-bottom:8px">較正: -</div>
  <button class="warn" onclick="if(confirm('腕が動きます。青ブロックを 1 個、カメラに見える所（トレイの外）に置きましたか？\\n腕がそれを拾って中心へ置き、ずれを測ります（約 30 秒）'))act('verify')">較正を確認（30 秒・腕が動く）</button>
  <button class="warn" onclick="if(confirm('腕が 9 点を回ります（約 1 分）。机からトレイをどけ、青ブロックを 1 個用意しましたか？\\n最初の点でだけ吸盤の真下に置いて Enter。あとは腕が運びます'))act('calibrate')">較正をやり直す（ブロック 1 個・腕が運ぶ・端末が開く）</button>
  <button onclick="act('zones')">置き場を調整（画像をクリック）</button>
  <button class="ghost" onclick="snap()">カメラ確認（1 枚撮る）</button>
  <div class="help">較正が要るのはカメラかロボットを動かしたときだけ。動かしていなければ不要。迷ったら「確認」→ 3 mm を超えたら「やり直す」。置き場: トレイを置き直したとき。</div>
  <img id="snap" alt="camera">
 </div>
 <div class="card"><h3>設定（保存 → 再起動で反映）</h3>
  <form id="settings" onsubmit="return saveSettings(event)">
"""
PAGE_TAIL = """
   <div style="margin-top:10px"><button type="submit">設定を保存</button><span class="msg" id="smsg"></span></div>
  </form>
 </div>
 <div class="card"><h3>手順</h3>
  <ol>
   <li>Magician の電源を入れ、本体のキーを 2 秒長押し（LED が緑になるまで）</li>
   <li>カメラと Magician の USB を Mac に挿す。腕の周りを空ける</li>
   <li>「実演を起動」→ 実演画面が開く。「声の準備ができました」と出たら話しかける</li>
   <li>終わるときは「停止」（腕がホームへ戻る）。電源はそのあと</li>
  </ol>
 </div>
 <div class="card"><h3>ログ（実演の出力）</h3><pre id="log">-</pre></div>
</main>
<script>
async function act(t){const r=await (await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:t})})).json();
 document.getElementById('msg').textContent=r.message||(r.ok?'OK':'失敗');if(r.command){document.getElementById('msg').textContent+='  '+r.command;}poll();}
async function saveSettings(ev){ev.preventDefault();const f=new FormData(document.getElementById('settings'));const o={};
 for(const [k,v] of f.entries())o[k]=v;document.querySelectorAll('#settings input[type=checkbox]').forEach(c=>{o[c.name]=c.checked?'on':'';});
 const r=await (await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o)})).json();
 document.getElementById('smsg').textContent=r.ok?'保存しました。再起動で反映されます':('保存していません: '+r.errors.join(' / '));return false;}
async function snap(){const i=document.getElementById('snap');i.style.display='block';i.src='/snapshot.jpg?t='+Date.now();}
async function poll(){try{const s=await (await fetch('/api/status')).json();
 const r=document.getElementById('run');r.textContent=s.running?('動いています（'+s.uptime_s+' 秒）'):'止まっています';r.className=s.running?'on':'off';
 document.getElementById('dry').textContent=s.dry?'（実機なしの練習モード）':'';
 const p=document.getElementById('panel');p.href=s.panel_url;p.style.opacity=s.running?1:.4;
 const c=s.calibration;document.getElementById('calinfo').textContent=c?('較正: '+c.created+'・RMS '+(c.rms_mm==null?'?':c.rms_mm+' mm')+'・'+c.points+' 点'+(c.rms_mm!=null&&c.rms_mm>3?'（3 mm 超: やり直し推奨）':'')):'較正: まだ（「較正をやり直す」で作る）';
 document.getElementById('b-start').disabled=s.running;document.getElementById('b-stop').disabled=!s.running;
 if(s.message)document.getElementById('msg').textContent=s.message;document.getElementById('log').textContent=s.log||'-';}catch(e){}}
setInterval(poll,2000);poll();
</script></body></html>"""


def page_html(cfg: AppConfig) -> str:
    return PAGE_HEAD + _settings_form(cfg) + PAGE_TAIL


# ------------------------------------------------------------------ server
class LauncherServer:
    def __init__(self, launcher: Launcher, port: int = LAUNCHER_PORT, host: str = "127.0.0.1"):
        self.launcher, self.port, self.host = launcher, port, host
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> int:
        lc = self.launcher

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body: bytes, ctype: str, code: int = 200):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, code: int = 200):
                self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", code)

            def do_GET(self):
                if self.path == "/" or self.path.startswith("/index"):
                    self._send(page_html(lc.cfg()).encode("utf-8"), "text/html; charset=utf-8")
                elif self.path.startswith("/api/status"):
                    self._json(lc.status())
                elif self.path.startswith("/snapshot.jpg"):
                    try:
                        jpeg = lc.camera_snapshot()
                    except Exception as e:  # noqa: BLE001 — カメラが無い・掴まれている
                        return self._json({"ok": False, "error": repr(e)}, 503)
                    if jpeg is None:
                        return self._json({"ok": False, "error": "実演が動いている間は実演画面で見てください"}, 409)
                    self._send(jpeg, "image/jpeg")
                else:
                    self._json({"ok": False, "error": "not found"}, 404)

            def _body(self) -> dict:
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    d = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return {}
                return d if isinstance(d, dict) else {}

            def do_POST(self):
                if self.path.startswith("/api/action"):
                    t = self._body().get("type")
                    try:
                        if t == "start":
                            ok = lc.start()
                        elif t == "stop":
                            ok = lc.stop()
                        elif t == "restart":
                            ok = lc.restart()
                        elif t == "calibrate":
                            return self._json({"ok": True, "message": lc.last_message, "command": lc.open_calibration("carry")})
                        elif t == "verify":
                            return self._json({"ok": True, "message": lc.last_message, "command": lc.open_calibration("verify")})
                        elif t == "zones":
                            lc.open_zones()
                            ok = True
                        else:
                            return self._json({"ok": False, "error": f"unknown action {t}"}, 400)
                    except Exception as e:  # noqa: BLE001 — 失敗は画面に出す
                        return self._json({"ok": False, "message": f"失敗: {e!r}"}, 500)
                    return self._json({"ok": ok, "message": lc.last_message})
                if self.path.startswith("/api/settings"):
                    ok, errors = lc.save_settings(self._body())
                    return self._json({"ok": ok, "errors": errors, "settings": read_settings(lc.cfg()), "message": lc.last_message})
                self._json({"ok": False, "error": "not found"}, 404)

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="launcher-http", daemon=True)
        self.thread.start()
        return self.port

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Dobot 実演のランチャー（起動・停止・調整の画面）")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--port", type=int, default=LAUNCHER_PORT)
    ap.add_argument("--dry", action="store_true", help="実演を実機なし（--dry-run）で起動する練習モード")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    lc = Launcher(args.config, dry=args.dry)
    srv = LauncherServer(lc, port=args.port)
    port = srv.start()
    url = f"http://127.0.0.1:{port}"
    print(f"launcher: {url}   (Ctrl+C で終了。実演が動いていれば止めてから終わります)")
    if not args.no_browser and platform.system() == "Darwin":
        subprocess.Popen(["open", url])
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        lc.stop()
        srv.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

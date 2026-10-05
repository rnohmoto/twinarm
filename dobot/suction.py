"""吸引（真空ポンプ）の ON/OFF だけを行う道具: コマンドと、撮影用の小さなブラウザ画面。腕は動かさない。

使い方（このフォルダで）:
  uv run python suction.py --port /dev/cu.usbserial-XXXX status      # シリアル番号と今の指令状態を読む（読み取りのみ）
  uv run python suction.py --port ... on                             # 吸引 ON（終了後も ON のまま）
  uv run python suction.py --port ... off                            # 吸引 OFF
  uv run python suction.py --port ... pulse --seconds 3              # ON → 3 秒 → OFF
  uv run python suction.py --port ... panel                          # 画面 http://127.0.0.1:8791（ON/OFF の 2 ボタン）
  uv run python suction.py --dry panel                               # 実機なしで画面だけ確かめる

設計
- pydobot を通さず、公式プロトコル（Dobot Communication Protocol V1.1.5）のフレームを直接送る。
  pydobot は接続時にキューの消去と速度設定を書き込むので、吸引だけの用途では触らない方が安全。
- SetEndEffectorSuctionCup（ID 62）を「即時」（isQueued=0）で送る。ホーミング前でもキュー停止中でも効く。
- 画面はシリアルに直接触れない。1 本のワーカースレッドだけが機器を持ち、ボタンはワーカーに依頼する。
- ポンプの連続運転を避けるため、画面では max-on 秒で自動 OFF（既定 300 秒・0 で無効）。終了時は必ず OFF。
- 読めるのは「指令した状態」で、実際に吸えているか（負圧）は Magician からは分からない。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import queue
import signal
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent

ID_DEVICE_SN = 0               # GetDeviceSN → ASCII（機体のシリアル番号）
ID_SUCTION_CUP = 62            # Set/GetEndEffectorSuctionCup: params = ctrlEnabled(uint8), sucked(uint8)
CTRL_READ = 0                  # bit0 = rw（1=書き込み）、bit1 = isQueued（0=即時）
CTRL_WRITE_IMMEDIATE = 1
SYNC = b"\xaa\xaa"
BAUDRATE = 115200
REPLY_TIMEOUT_S = 1.0
DEFAULT_HTTP_PORT = 8791       # demo.py のパネル（8790）と重ならない番号
DEFAULT_MAX_ON_S = 300.0
WORKER_TICK_S = 0.2
COMMAND_WAIT_S = 5.0


class ProtocolError(Exception):
    pass


def build_frame(cmd_id: int, ctrl: int = CTRL_READ, params: bytes = b"") -> bytes:
    """AA AA | len | id | ctrl | params | checksum（payload の和の 2 の補数）。"""
    payload = bytes([cmd_id, ctrl]) + bytes(params)
    return SYNC + bytes([len(payload)]) + payload + bytes([(-sum(payload)) & 0xFF])


def parse_frame(frame: bytes) -> tuple[int, int, bytes]:
    """1 フレームを (id, ctrl, params) にする。形か checksum が合わなければ ProtocolError。"""
    if len(frame) < 6 or frame[:2] != SYNC:
        raise ProtocolError(f"フレームの先頭が AA AA でない: {frame.hex(' ')}")
    length = frame[2]
    payload = frame[3:3 + length]
    if length < 2 or len(frame) != 4 + length:
        raise ProtocolError(f"フレーム長が合わない: {frame.hex(' ')}")
    if (sum(payload) + frame[-1]) & 0xFF:
        raise ProtocolError(f"checksum が合わない: {frame.hex(' ')}")
    return payload[0], payload[1], bytes(payload[2:])


class MagicianSuction:
    """シリアル越しの吸引 ON/OFF。渡された serial オブジェクトを所有する。"""

    def __init__(self, ser, timeout_s: float = REPLY_TIMEOUT_S):
        self.ser = ser
        self.timeout_s = timeout_s

    @classmethod
    def open(cls, port: str) -> MagicianSuction:
        import serial  # pyserial

        return cls(serial.Serial(port, BAUDRATE, timeout=0.05))

    def _read_frame(self) -> bytes:
        deadline = time.monotonic() + self.timeout_s
        buf = b""
        while time.monotonic() < deadline:
            buf += self.ser.read(1)
            start = buf.find(SYNC)
            if start < 0:
                buf = buf[-1:]
                continue
            buf = buf[start:]
            if len(buf) >= 3 and len(buf) >= 4 + buf[2]:
                return buf[:4 + buf[2]]
        raise ProtocolError("応答がない（電源・ケーブル・ポートを確認。Magician 以外に繋いでいないか）")

    def _command(self, cmd_id: int, ctrl: int = CTRL_READ, params: bytes = b"") -> bytes:
        self.ser.reset_input_buffer()
        self.ser.write(build_frame(cmd_id, ctrl, params))
        got_id, _ctrl, got_params = parse_frame(self._read_frame())
        if got_id != cmd_id:
            raise ProtocolError(f"ID {cmd_id} を送ったのに ID {got_id} の応答が来た")
        return got_params

    def device_sn(self) -> str:
        return self._command(ID_DEVICE_SN).rstrip(b"\x00").decode("ascii", errors="replace")

    def state(self) -> bool:
        """指令上の状態（ポンプ有効かつ吸引側）。実際の負圧ではない。"""
        params = self._command(ID_SUCTION_CUP)
        if len(params) < 2:
            raise ProtocolError(f"GetEndEffectorSuctionCup: 応答が短い ({len(params)} bytes)")
        return bool(params[0]) and bool(params[1])

    def set(self, on: bool) -> bool:
        """ON = (ctrlEnabled=1, sucked=1)、OFF = (0, 0)。送ったあと読み返した状態を返す。"""
        self._command(ID_SUCTION_CUP, CTRL_WRITE_IMMEDIATE, bytes([1, 1]) if on else bytes([0, 0]))
        return self.state()

    def close(self) -> None:
        self.ser.close()


class DrySuction:
    """実機なし。状態を覚えるだけ。"""

    def __init__(self):
        self.on = False

    def device_sn(self) -> str:
        return "dry-run"

    def state(self) -> bool:
        return self.on

    def set(self, on: bool) -> bool:
        self.on = bool(on)
        return self.on

    def close(self) -> None:
        pass


class SuctionWorker:
    """機器を持つ唯一のスレッド。画面からの依頼を順に実行し、max_on_s を過ぎたら自分で OFF にする。"""

    def __init__(self, dev, max_on_s: float = DEFAULT_MAX_ON_S, clock=time.monotonic):
        self.dev = dev
        self.max_on_s = max_on_s
        self._clock = clock
        self._lock = threading.Lock()
        self._requests: queue.Queue[tuple[Callable[[], object], Future] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._on = False
        self._on_since: float | None = None
        self._note = ""
        self._error = ""

    def snapshot(self) -> dict:
        with self._lock:
            on_for = self._clock() - self._on_since if self._on_since is not None else 0.0
            remaining = max(0.0, self.max_on_s - on_for) if self._on and self.max_on_s > 0 else None
            return {"on": self._on, "on_for_s": round(on_for, 1),
                    "remaining_s": None if remaining is None else round(remaining),
                    "max_on_s": self.max_on_s, "note": self._note, "error": self._error}

    def apply(self, on: bool, note: str = "") -> bool:
        """機器に送る（ワーカースレッド、または起動前・停止後の呼び出し側スレッドから）。"""
        try:
            actual = self.dev.set(on)
        except Exception as e:
            with self._lock:
                self._error = f"{type(e).__name__}: {e}"
            raise
        with self._lock:
            self._on = actual
            self._on_since = self._clock() if actual else None
            self._note, self._error = note, ""
        return actual

    def tick(self) -> None:
        snap = self.snapshot()
        if snap["on"] and snap["remaining_s"] == 0:
            self.apply(False, note=f"{self.max_on_s:.0f} 秒たったので自動で OFF にしました")

    def request(self, on: bool) -> bool:
        """HTTP スレッドから呼ぶ。ワーカーが実行し終えるまで待って、読み返した状態を返す。"""
        return self.submit(lambda: self.apply(on))

    def submit(self, job: Callable[[], object], wait_s: float = COMMAND_WAIT_S):
        """job をワーカースレッドで実行し、結果（または例外）を返す。"""
        fut: Future = Future()
        self._requests.put((job, fut))
        return fut.result(timeout=wait_s)

    def _run(self) -> None:
        while True:
            try:
                item = self._requests.get(timeout=WORKER_TICK_S)
            except queue.Empty:
                with contextlib.suppress(Exception):  # 自動 OFF の失敗は snapshot の error に残り、次の tick でまた試す
                    self.tick()
                continue
            if item is None:
                return
            job, fut = item
            try:
                fut.set_result(job())
            except Exception as e:  # noqa: BLE001  依頼元へ返す
                fut.set_exception(e)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="suction-worker", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        """ワーカーを止め、必ず OFF を送る。"""
        if self._thread is not None:
            self._requests.put(None)
            self._thread.join(timeout=COMMAND_WAIT_S)
            self._thread = None
        self.apply(False, note="終了時に OFF にしました")


PAGE_HTML = """<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>吸引 ON/OFF</title>
<style>
 html,body{height:100%}
 body{margin:0;font-family:'Segoe UI','Hiragino Sans',Meiryo,sans-serif;background:#1b1f24;color:#e6e6e6;
      display:flex;flex-direction:column;align-items:center;justify-content:center;gap:28px}
 #state{font-size:64px;font-weight:bold;letter-spacing:.04em}
 .on{color:#7bd88f}.off{color:#8a94a3}.ng{color:#ff5c5c}
 .row{display:flex;gap:24px;flex-wrap:wrap;justify-content:center}
 button{width:min(40vw,300px);height:min(34vh,220px);font-size:44px;font-weight:bold;color:#fff;border:0;
        border-radius:20px;cursor:pointer;opacity:.55}
 button.active{opacity:1;outline:6px solid #fff}
 #on{background:#2e9d57}#off{background:#4a5568}
 #info{font-size:18px;color:#9fb3c8;min-height:1.4em;text-align:center;padding:0 16px}
 #keys{font-size:14px;color:#6b7686}
</style></head><body>
<div id="state" class="off">-</div>
<div class="row">
 <button id="on" onclick="send(true)">吸引 ON</button>
 <button id="off" onclick="send(false)">吸引 OFF</button>
</div>
<div id="info"></div>
<div id="keys">スペース: 切り替え ／ O: ON ／ F: OFF</div>
<script>
let isOn=false;
function show(s){
 isOn=!!s.on;const st=document.getElementById('state');
 st.textContent=s.error?'エラー':(isOn?'吸引中':'停止');st.className=s.error?'ng':(isOn?'on':'off');
 document.getElementById('on').classList.toggle('active',isOn);
 document.getElementById('off').classList.toggle('active',!isOn);
 let info=s.error||s.note||'';
 if(!s.error&&isOn){info=Math.round(s.on_for_s)+' 秒'+(s.remaining_s==null?'':'（あと '+s.remaining_s+' 秒で自動 OFF）');}
 document.getElementById('info').textContent=info;
}
async function send(on){
 try{show(await (await fetch('/suction',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({on:on})})).json());}
 catch(e){show({error:'画面のサーバーに届きません（suction.py は動いていますか）'});}
}
async function poll(){try{show(await (await fetch('/status')).json());}catch(e){show({error:'画面のサーバーに届きません（suction.py は動いていますか）'});}}
document.addEventListener('keydown',e=>{
 if(e.repeat)return;
 if(e.code==='Space'){e.preventDefault();send(!isOn);}
 else if(e.key==='o'||e.key==='O')send(true);
 else if(e.key==='f'||e.key==='F')send(false);
});
setInterval(poll,500);poll();
</script></body></html>"""


class SuctionPanel:
    """GET / 画面 ・ GET /status 状態 ・ POST /suction {"on": true|false}。機器にはワーカー経由でしか触れない。

    posts は「パス → body(dict) を受ける関数」。入力が悪ければ ValueError（400）、機器側の失敗はその他の例外（502）。
    """

    def __init__(self, worker: SuctionWorker, port: int = DEFAULT_HTTP_PORT, host: str = "127.0.0.1",
                 page: str = PAGE_HTML, posts: dict[str, Callable[[dict], object]] | None = None):
        self.worker = worker
        self.host, self.port = host, port
        self.page = page
        self.posts = {"/suction": self.post_suction, **(posts or {})}
        self.httpd: ThreadingHTTPServer | None = None

    def post_suction(self, body: dict) -> None:
        if not isinstance(body.get("on"), bool):
            raise ValueError('{"on": true|false} が必要')  # noqa: TRY004  入力の誤りは 400 にまとめる
        self.worker.request(body["on"])

    def start(self) -> int:
        worker, page, posts = self.worker, self.page, self.posts

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 静かに
                pass

            def _send(self, body: bytes, content_type: str, code: int = 200):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, code=200):
                self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", code)

            def do_GET(self):
                if self.path == "/":
                    self._send(page.encode("utf-8"), "text/html; charset=utf-8")
                elif self.path.startswith("/status"):
                    self._json(worker.snapshot())
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self):
                handler = posts.get(self.path)
                if handler is None:
                    return self._json({"error": "not found"}, 404)
                n = int(self.headers.get("Content-Length", "0") or 0)
                try:
                    body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                    if not isinstance(body, dict):
                        raise ValueError("JSON のオブジェクトが必要")  # noqa: TRY004
                    handler(body)
                except ValueError as e:  # JSONDecodeError も ValueError
                    return self._json({"error": str(e)}, 400)
                except Exception as e:  # noqa: BLE001  機器側の失敗。理由を画面に返す
                    return self._json({**worker.snapshot(), "error": str(e) or type(e).__name__}, 502)
                return self._json(worker.snapshot())

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, name="suction-http", daemon=True).start()
        return self.port

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


def resolve_port(port: str | None, config_path: Path) -> str:
    """--port、無ければ config の robot.port。どちらも無ければ止まる（ポートは推測しない）。"""
    if port:
        return port
    if config_path.exists():
        configured = json.loads(config_path.read_text(encoding="utf-8")).get("robot", {}).get("port")
        if configured:
            return configured
    raise SystemExit("ポートが指定されていません。--port で渡すか config.json の robot.port に書いてください"
                     "（候補は uv run python check_robot.py --list）。")


def run_panel(dev, http_port: int, max_on_s: float, open_browser: bool) -> None:
    worker = SuctionWorker(dev, max_on_s=max_on_s)
    serve(worker, SuctionPanel(worker, port=http_port), open_browser)


def serve(worker: SuctionWorker, panel: SuctionPanel, open_browser: bool) -> None:
    """ワーカーと画面を動かし、Ctrl+C か SIGTERM まで待つ。終了時は必ず OFF を送る。"""
    worker.start()
    url = f"http://127.0.0.1:{panel.start()}"
    print(f"パネル: {url}  （止めるときは Ctrl+C。終了時に吸引 OFF を送ります）", flush=True)
    if open_browser:
        webbrowser.open(url)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        panel.stop()
        worker.shutdown()
        print("吸引 OFF を送って終了しました")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Dobot Magician の吸引 ON/OFF（腕は動かさない）")
    ap.add_argument("--port", default=None, help="シリアルポート（Mac: /dev/cu.usbserial-*・Windows: COMn）")
    ap.add_argument("--config", default=str(HERE / "config.json"), help="--port が無いとき robot.port を読む")
    ap.add_argument("--dry", action="store_true", help="実機なし")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="シリアル番号と指令上の状態を読む（読み取りのみ）")
    sub.add_parser("on", help="吸引 ON（このコマンドが終わっても ON のまま）")
    sub.add_parser("off", help="吸引 OFF")
    pulse = sub.add_parser("pulse", help="ON → 指定秒 → OFF")
    pulse.add_argument("--seconds", type=float, default=3.0)
    panel = sub.add_parser("panel", help="ON/OFF だけのブラウザ画面")
    panel.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT)
    panel.add_argument("--max-on-s", type=float, default=DEFAULT_MAX_ON_S, help="連続 ON の上限秒（0 で無効）")
    panel.add_argument("--no-open", action="store_true", help="ブラウザを自動で開かない")
    args = ap.parse_args(argv)

    dev = DrySuction() if args.dry else MagicianSuction.open(resolve_port(args.port, Path(args.config)))
    try:
        print(f"接続: Magician SN {dev.device_sn()}")
        if args.cmd == "status":
            print("吸引（指令上の状態）:", "ON" if dev.state() else "OFF")
        elif args.cmd == "on":
            print("吸引:", "ON" if dev.set(True) else "OFF（ON を送ったが読み返しは OFF）")
        elif args.cmd == "off":
            print("吸引:", "ON（OFF を送ったが読み返しは ON）" if dev.set(False) else "OFF")
        elif args.cmd == "pulse":
            try:
                print("吸引:", "ON" if dev.set(True) else "OFF（ON を送ったが読み返しは OFF）")
                time.sleep(max(0.0, args.seconds))
            finally:
                print("吸引:", "ON（OFF を送ったが読み返しは ON）" if dev.set(False) else "OFF")
        elif args.cmd == "panel":
            run_panel(dev, args.http_port, args.max_on_s, not args.no_open)
    finally:
        dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

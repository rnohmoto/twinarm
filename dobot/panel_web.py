"""ブラウザ UI（stdlib のみ・Mac/Windows 共通）: カメラ映像（MJPEG）＋状態＋操作ボタン。demo.py が使う。

  GET  /          … 画面（HTML 1 枚。JS が /status を 0.5 秒ごとに読む）
  GET  /stream    … multipart/x-mixed-replace の MJPEG（注釈つきの最新フレーム）
  GET  /status    … JSON（モード・状態・発話・意図・返事・検出数・カメラ/ロボットの状態・イベント）
  POST /cmd       … JSON {"type": "say"|"listen"|"home"|"estop"|"resume"|"tidy"|"attract"|"quit", ...}

設計: パネルはロボットに直接触れない。コマンドは demo.py のワーカーに渡すだけ（1 本のスレッドが実機を持つ）。
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE_KEYS = ("mode", "state", "utterance", "reply", "intent", "last_pick", "robot", "camera", "attract", "stats",
              "objects_seen", "error", "fps")


class PanelState:
    """ワーカーが書き、HTTP スレッドが読む共有状態（ロックつき）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._seq = 0
        self._t0 = time.time()
        self._info: dict = {"mode": "dialog", "state": "starting", "utterance": "", "reply": "", "intent": None,
                            "last_pick": None, "robot": {}, "camera": {}, "attract": {}, "stats": {},
                            "objects_seen": {}, "error": "", "fps": 0.0}
        self.events: deque = deque(maxlen=40)

    def set_frame(self, jpeg: bytes) -> None:
        with self._lock:
            self._jpeg = jpeg
            self._seq += 1

    def frame(self) -> tuple[int, bytes | None]:
        with self._lock:
            return self._seq, self._jpeg

    def update(self, **kw) -> None:
        with self._lock:
            self._info.update(kw)

    def push_event(self, text: str) -> None:
        with self._lock:
            self.events.append({"t": time.strftime("%H:%M:%S"), "text": text})

    def snapshot(self) -> dict:
        with self._lock:
            d = dict(self._info)
            d["events"] = list(self.events)[-15:]
            d["uptime_s"] = round(time.time() - self._t0, 1)
            d["frame_seq"] = self._seq
            return d


PAGE_HTML = """<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Magician パネル</title>
<style>
 body{margin:0;font-family:'Segoe UI',Meiryo,sans-serif;background:#1b1f24;color:#e6e6e6}
 header{background:#2c3e50;padding:10px 18px;font-size:18px;font-weight:bold;display:flex;justify-content:space-between}
 main{display:grid;grid-template-columns:2fr 1fr;gap:14px;padding:14px}
 @media(max-width:900px){main{grid-template-columns:1fr}}
 img#cam{width:100%;background:#000;border-radius:8px}
 .card{background:#262b33;border-radius:8px;padding:12px 14px;margin-bottom:12px}
 .card h3{margin:0 0 8px;font-size:14px;color:#9fb3c8;text-transform:uppercase;letter-spacing:.05em}
 .big{font-size:22px;font-weight:bold}
 .state-idle{color:#7bd88f}.state-moving{color:#f7c948}.state-listening{color:#5ac8fa}.state-thinking{color:#c792ea}.state-estop{color:#ff5c5c}
 button{background:#3498db;color:#fff;border:0;border-radius:6px;padding:10px 14px;margin:4px 4px 4px 0;font-size:15px;cursor:pointer}
 button.danger{background:#c0392b}button.warn{background:#e67e22}button.ghost{background:#4a5568}
 input[type=text]{width:70%;padding:10px;font-size:15px;border-radius:6px;border:1px solid #555;background:#111;color:#eee}
 .kv{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:14px}.kv b{color:#9fb3c8;font-weight:normal}
 ul.ev{list-style:none;padding:0;margin:0;font-size:13px;max-height:220px;overflow:auto}ul.ev li{padding:2px 0;border-bottom:1px solid #333}
 .ok{color:#7bd88f}.ng{color:#ff5c5c}
</style></head><body>
<header><span>Dobot Magician — 画像認識ピック＆プレース</span><span id="clock"></span></header>
<main>
 <section>
  <img id="cam" src="/stream" alt="camera">
  <div class="card"><h3>話しかける</h3>
   <input type="text" id="say" placeholder="例: 赤いブロックを右のトレイに置いて / 片付けて" onkeydown="if(event.key==='Enter')say()">
   <button onclick="say()">送る</button> <button onclick="cmd({type:'listen'})">🎤 マイク</button>
   <div style="margin-top:8px">
    <button class="ghost" onclick="cmd({type:'home'})">ホーム</button>
    <button class="warn" onclick="cmd({type:'tidy'})">片付け（元に戻す）</button>
    <button class="ghost" onclick="cmd({type:'loop'})">ループ ×3</button>
    <button class="danger" onclick="cmd({type:'estop'})">非常停止</button>
    <button class="ghost" onclick="cmd({type:'resume'})">停止解除</button>
    <button class="ghost" id="attract" onclick="toggleAttract()">自動ループ: -</button>
   </div>
  </div>
 </section>
 <aside>
  <div class="card"><h3>状態</h3><div class="big" id="state">-</div><div class="kv" id="kv"></div></div>
  <div class="card"><h3>いま聞いた → 判断 → 返事</h3><div class="kv"><b>発話</b><span id="utt">-</span><b>意図</b><span id="intent">-</span><b>返事</b><span id="reply">-</span><b>最後の動作</b><span id="pick">-</span></div></div>
  <div class="card"><h3>カメラ・ロボット</h3><div class="kv" id="hw"></div></div>
  <div class="card"><h3>記録</h3><ul class="ev" id="ev"></ul></div>
 </aside>
</main>
<script>
let attractOn=false;
function cmd(o){return fetch('/cmd',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o)}).then(r=>r.json()).catch(()=>{});}
function say(){const t=document.getElementById('say').value.trim();if(!t)return;cmd({type:'say',text:t});document.getElementById('say').value='';}
function toggleAttract(){cmd({type:'attract',on:!attractOn});}
function esc(s){return String(s==null?'-':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function kv(el,obj){el.innerHTML=Object.entries(obj||{}).map(([k,v])=>'<b>'+esc(k)+'</b><span>'+esc(typeof v==='object'?JSON.stringify(v):v)+'</span>').join('');}
async function poll(){try{const s=await (await fetch('/status')).json();
 const st=document.getElementById('state');st.textContent=(s.mode==='attract'?'自動ループ／':'対話／')+s.state;st.className='big state-'+s.state;
 kv(document.getElementById('kv'),{見えている物:s.objects_seen,拾った:(s.stats||{}).picks_ok,失敗:(s.stats||{}).picks_fail,片付け:(s.stats||{}).tidy,自動サイクル:(s.stats||{}).cycles,稼働:Math.round(s.uptime_s)+' s',映像:(s.fps||0)+' fps',エラー:s.error||'-'});
 document.getElementById('utt').textContent=s.utterance||'-';document.getElementById('intent').textContent=s.intent?JSON.stringify(s.intent):'-';
 document.getElementById('reply').textContent=s.reply||'-';document.getElementById('pick').textContent=s.last_pick?JSON.stringify(s.last_pick):'-';
 kv(document.getElementById('hw'),Object.assign({},s.camera||{},s.robot||{},{自動ループ:JSON.stringify(s.attract||{})}));
 attractOn=!!(s.attract&&s.attract.on);document.getElementById('attract').textContent='自動ループ: '+(attractOn?'ON':'OFF');
 document.getElementById('ev').innerHTML=(s.events||[]).slice().reverse().map(e=>'<li>'+esc(e.t)+' '+esc(e.text)+'</li>').join('');
 document.getElementById('clock').textContent=new Date().toLocaleTimeString();
}catch(e){}}
setInterval(poll,500);poll();
</script></body></html>"""


class PanelServer:
    def __init__(self, state: PanelState, on_command, port: int = 8790, host: str = "127.0.0.1", stream_fps: int = 8):
        self.state = state
        self.on_command = on_command
        self.host, self.port, self.stream_fps = host, port, stream_fps
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> int:
        state, on_command, fps = self.state, self.on_command, self.stream_fps

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 静かに
                pass

            def _json(self, obj, code=200):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/" or self.path.startswith("/index"):
                    body = PAGE_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path.startswith("/status"):
                    self._json(state.snapshot())
                elif self.path.startswith("/stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    last = -1
                    try:
                        while True:
                            seq, jpeg = state.frame()
                            if jpeg is not None and seq != last:
                                last = seq
                                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                                                 str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                                self.wfile.flush()
                            time.sleep(1.0 / max(1, fps))
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        return
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self):
                if not self.path.startswith("/cmd"):
                    return self._json({"error": "not found"}, 404)
                n = int(self.headers.get("Content-Length", "0") or 0)
                try:
                    cmd = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    return self._json({"ok": False, "error": "bad json"}, 400)
                if not isinstance(cmd, dict) or "type" not in cmd:
                    return self._json({"ok": False, "error": "type required"}, 400)
                on_command(cmd)
                return self._json({"ok": True, "queued": cmd.get("type")})

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="panel-http", daemon=True)
        self.thread.start()
        return self.port

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

"""koch4_web_panel.py — 2ペアを1画面で見る操作パネル（標準ライブラリのみ・オフライン動作）.

koch4_teleop.py の UDP テレメトリをペアごとに受けてブラウザに配信(SSE)し、
スライダ/ボタン操作を**全ペアへ同じ値で**UDP 制御チャネルに返す（2ペア原則:
1本ずつゲインを変えない）。グラフは Canvas 直描き。

使い方: 通常は koch4_dual_launch.py から起動される。単体起動:
  python koch4_web_panel.py --telemetry 8765,8767 --ctl-port 8766,8768 --labels A,B
  python koch4_web_panel.py                       # 1ペア(8765/8766)
ポート: HTTP 8780 / テレメトリ受信 UDP(ペアごと) / 制御送信 UDP(ペアごと・同報)
"""

import argparse
import json
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

LATEST: list[dict] = []  # ペアごとの最新テレメトリ
LABELS: list[str] = []
CTL_PORTS: list[int] = []
_lock = threading.Lock()
CAMS_JPEG: dict[int, bytes] = {}  # カメラindex -> 最新JPEGバイト列
PAGE_FINAL = [""]  # %%CAMS%%差し込み後のHTML
STREAM_HZ = 15


def cam_loop(idx):
    """カメラを開いて最新フレームをJPEG保持(パネル専用・制御ループとは独立プロセス)."""
    import cv2

    cap = cv2.VideoCapture(idx)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    if not cap.isOpened():
        print(f"[cam] カメラ{idx}を開けません(録画中で占有/権限/番号違い)")
        return
    print(f"[cam] カメラ{idx} 配信開始")
    while True:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.2)
            continue
        ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if ok:
            CAMS_JPEG[idx] = jpg.tobytes()
        time.sleep(0.03)


PAGE = """<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<title>Koch4 Panel</title><style>
body{font-family:'Segoe UI',Meiryo,sans-serif;background:#1b1e24;color:#e8eaed;margin:0;padding:10px}
h1{font-size:16px;margin:4px 0 8px} h2{font-size:14px;margin:6px 0 4px;color:#7fd1ff}
.row{display:flex;gap:10px;flex-wrap:wrap}
.card{background:#252a33;border-radius:8px;padding:10px;margin-bottom:8px}
.pair{flex:1 1 420px;min-width:320px}
canvas{background:#12151a;border-radius:6px;width:100%;height:110px;display:block}
.lbl{font-size:11px;color:#9aa0a6;margin:6px 0 2px}
.chip{display:inline-block;background:#12151a;border-radius:12px;padding:3px 10px;margin:2px;font-size:12px}
.chip b{color:#7fd1ff}
input[type=range]{width:150px;vertical-align:middle}
button{background:#2d5be3;color:#fff;border:0;border-radius:6px;padding:6px 14px;margin:2px;cursor:pointer;font-size:13px}
button.warn{background:#c0392b} button.mode{background:#3a4150} button.mode.on{background:#1e8e3e}
.val{display:inline-block;min-width:44px;font-size:12px;color:#7fd1ff}
.legend{font-size:11px;color:#9aa0a6}
</style></head><body>
<h1>Koch4 操作パネル（全ペア同じ設定） <span id="conn" class="chip">未接続</span></h1>
<div class="row">%%CAMS%%</div>
<div class="card">
  <button class="mode" id="m_off" onclick="setMode('off')">FF: OFF</button>
  <button class="mode" id="m_gripper" onclick="setMode('gripper')">FF: 握り返し</button>
  <button class="mode" id="m_arm" onclick="setMode('arm')">FF: 腕+握り</button>
  <button class="mode" id="m_vwall" onclick="setMode('vwall')">FF: 仮想壁(VR)</button>
  <button onclick="ctl({resync:1})">合流リセット(全ペア)</button>
  <button class="warn" onclick="if(confirm('全ペアのテレオペを停止しますか?'))ctl({stop:1})">停止(全ペア)</button>
  <div class="row" id="sliders"></div>
</div>
<div class="row" id="pairs"></div>
<script>
const ORDER=["shoulder_pan","shoulder_lift","elbow_flex","wrist_flex","wrist_roll","gripper"];
const COLS=["#4e9cf5","#ff9f43","#2ecc71","#e74c3c","#a55eea","#b8860b"];
const WIN=15;
const SL=[["ff_gain","(spring)ゲイン",0,2.5,0.1],["ff_cap","握り上限mA",60,900,10],["ff_floor","(spring)バネmA",0,120,5],
          ["ff_ke","(error)差分ゲイン",0,30,1],["arm_gain","腕ゲイン",0,1.5,0.05],["arm_cap","腕上限mA",0,400,10],
          ["vw_scale","重さ倍率(VR)",0,0.5,0.01],["vw_cap","重さ上限mA(VR)",0,400,10],["max_rel","追従リミッタ",5,100,5]];
const sdiv=document.getElementById('sliders');
SL.forEach(([k,name,lo,hi,st])=>{
  const d=document.createElement('div');
  d.innerHTML=`<div class="lbl">${name} <span class="val" id="v_${k}">-</span></div>
  <input type="range" id="s_${k}" min="${lo}" max="${hi}" step="${st}"
   oninput="document.getElementById('v_${k}').textContent=this.value"
   onchange="ctl({${k}:parseFloat(this.value)})">`;
  sdiv.appendChild(d);});
function ctl(o){fetch('/ctl?c='+encodeURIComponent(JSON.stringify(o)));}
function setMode(m){ctl({mode:m});}
const pairs=[]; // {label, buf, ffb, lastT, el}
function ensurePair(i,label){
  if(pairs[i]) return pairs[i];
  const buf={L:{},F:{},D:{},C:{}}; ORDER.forEach(m=>{for(const p in buf) buf[p][m]=[];});
  const el=document.createElement('div'); el.className='card pair';
  el.innerHTML=`<h2>ペア ${label} <span class="chip" id="st${i}"></span></h2><div id="chips${i}"></div>
  <div class="lbl">Leader 指令位置 [norm] <span class="legend">${ORDER.map((m,j)=>(j+1)+':'+m).join('  ')}</span></div><canvas id="cL${i}"></canvas>
  <div class="lbl">Follower 実位置 [norm]</div><canvas id="cF${i}"></canvas>
  <div class="lbl">差分 L−F [norm]</div><canvas id="cD${i}"></canvas>
  <div class="lbl">Follower 電流 [mA] (肩2軸=負荷%×10) / 白破線=FF指令</div><canvas id="cC${i}"></canvas>`;
  document.getElementById('pairs').appendChild(el);
  pairs[i]={label,buf,ffb:[],lastT:0,el}; return pairs[i];
}
const es=new EventSource('/stream');
es.onmessage=e=>{
  const msg=JSON.parse(e.data); const list=msg.pairs||[]; const labels=msg.labels||[];
  let anyLive=false;
  list.forEach((d,i)=>{
    const p=ensurePair(i,labels[i]||String.fromCharCode(65+i));
    if(!d||(!d.t&&d.t!==0)) return;
    const live=(Date.now()/1000-d._rx<2); anyLive=anyLive||live;
    document.getElementById('st'+i).innerHTML=live?'<b>接続中</b>':'停止中';
    p.lastT=d.t;
    ORDER.forEach(m=>{
      const l=(d.pos||{})[m]??0, f=(d.fpos||{})[m]??0;
      let c=(d.cur||{})[m]??0; if(m=="shoulder_pan"||m=="shoulder_lift") c*=10;
      push(p.buf.L[m],d.t,l); push(p.buf.F[m],d.t,f); push(p.buf.D[m],d.t,l-f); push(p.buf.C[m],d.t,c);});
    push(p.ffb,d.t,d.ff||0);
    if(i===0){const pr=d.params||{};
      for(const k in pr){const s=document.getElementById('s_'+k);
        if(s&&document.activeElement!==s){s.value=pr[k];document.getElementById('v_'+k).textContent=pr[k];}}
      ["off","gripper","arm","vwall"].forEach(m=>document.getElementById('m_'+m).classList.toggle('on',d.mode===m));}
    const vw=d.vw?` <span class="chip">重さ 肩<b>${d.vw.shoulder_lift??0}</b> 肘<b>${d.vw.elbow_flex??0}</b> mA</span>`:'';
    const wall=d.vwall?` <span class="chip">物体 <b>${d.vwall.name}</b> ${d.vwall.engaged?'握':'−'}</span>`:'';
    document.getElementById('chips'+i).innerHTML=
      `<span class="chip">grip <b>${(d.cur||{}).gripper??0} mA</b></span>`+
      `<span class="chip">FF指令 <b>${d.ff??0} mA</b></span>`+
      `<span class="chip">温度 <b>${d.temp??'-'}°C</b></span>`+
      `<span class="chip">フレーム <b>${d.n??0}</b></span>`+
      `<span class="chip">再接続 <b>${d.rec??0}</b></span>`+
      `<span class="chip">モード <b>${d.mode??'-'}</b></span>`+
      (d.leader_only?'<span class="chip">リーダーのみ</span>':'')+wall+vw;
  });
  document.getElementById('conn').innerHTML=anyLive?'<b>接続中</b>':'停止中';
};
function push(a,t,v){a.push([t,v]); while(a.length&&t-a[0][0]>WIN)a.shift();}
function draw(){
  pairs.forEach((p,i)=>{
    [["cL","L"],["cF","F"],["cD","D"],["cC","C"]].forEach(([cid,key])=>{
      const cv=document.getElementById(cid+i); if(!cv) return; const g=cv.getContext('2d');
      if(cv.width!==cv.clientWidth*2){cv.width=cv.clientWidth*2;cv.height=220;}
      g.clearRect(0,0,cv.width,cv.height);
      let lo=1e9,hi=-1e9;
      ORDER.forEach(m=>p.buf[key][m].forEach(q=>{if(q[1]<lo)lo=q[1];if(q[1]>hi)hi=q[1];}));
      if(key==="C") p.ffb.forEach(q=>{if(q[1]<lo)lo=q[1];if(q[1]>hi)hi=q[1];});
      if(lo>hi){lo=-1;hi=1;} const pad=(hi-lo)*0.1+1e-6; lo-=pad;hi+=pad;
      if(key==="D"){const y0=cv.height*(1-(0-lo)/(hi-lo)); g.strokeStyle="#555";g.beginPath();g.moveTo(0,y0);g.lineTo(cv.width,y0);g.stroke();}
      g.font="20px sans-serif";g.fillStyle="#666";g.fillText(hi.toFixed(0),6,22);g.fillText(lo.toFixed(0),6,cv.height-8);
      const t1=p.lastT,t0=t1-WIN;
      ORDER.forEach((m,j)=>{line(g,p.buf[key][m],t0,t1,lo,hi,cv,COLS[j],false);});
      if(key==="C") line(g,p.ffb,t0,t1,lo,hi,cv,"#eee",true);
    });
  });
  requestAnimationFrame(draw);}
function line(g,a,t0,t1,lo,hi,cv,col,dash){
  if(a.length<2)return; g.strokeStyle=col;g.lineWidth=2;g.setLineDash(dash?[8,6]:[]);g.beginPath();
  a.forEach((q,i)=>{const x=(q[0]-t0)/(t1-t0)*cv.width, y=cv.height*(1-(q[1]-lo)/(hi-lo));
    i?g.lineTo(x,y):g.moveTo(x,y);});
  g.stroke();g.setLineDash([]);}
draw();
</script></body></html>"""


def telemetry_loop(index, port):
    """Receive one pair's telemetry and keep its latest frame."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", port))
    s.settimeout(0.5)
    while True:
        try:
            data, _ = s.recvfrom(8192)
        except TimeoutError:
            continue
        except OSError:
            return
        try:
            msg = json.loads(data.decode())
        except ValueError:
            continue
        with _lock:
            LATEST[index] = dict(msg, _rx=time.time())


def send_ctl(cmd, targets):
    """Send one JSON command string to the selected pairs' control ports."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for i in targets:
        sock.sendto(cmd.encode(), ("127.0.0.1", CTL_PORTS[i]))


class Handler(BaseHTTPRequestHandler):
    """Panel page, SSE stream, control relay and camera tiles."""

    def log_message(self, format, *args):  # アクセスログを黙らせる
        """Silence request logging."""

    def do_GET(self):
        """Route the request."""
        url = urlparse(self.path)
        if url.path in ("/", "/index", "/index.html"):
            body = (PAGE_FINAL[0] or PAGE.replace("%%CAMS%%", "")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path.startswith("/cam/"):
            self.stream_camera(url.path)
        elif url.path == "/ctl":
            self.relay_ctl(url.query)
        elif url.path == "/stream":
            self.stream_events()
        else:
            self.send_response(404)
            self.end_headers()

    def relay_ctl(self, query):
        """Forward a control command to all pairs (or ``to=<index>``)."""
        q = parse_qs(query)
        cmd = unquote(q.get("c", [""])[0])
        try:
            json.loads(cmd)  # 形式検証
            to = q.get("to", ["all"])[0]
            targets = range(len(CTL_PORTS)) if to == "all" else [int(to)]
            send_ctl(cmd, targets)
            self.send_response(204)
        except (ValueError, IndexError):
            self.send_response(400)
        self.end_headers()

    def stream_events(self):
        """Server-sent events with every pair's latest frame."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            while True:
                with _lock:
                    payload = json.dumps({"pairs": LATEST, "labels": LABELS})
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
                time.sleep(1.0 / STREAM_HZ)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def stream_camera(self, path):
        """MJPEG stream for one camera tile."""
        try:
            idx = int(path.rsplit("/", 1)[1])
        except ValueError:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        try:
            while True:
                buf = CAMS_JPEG.get(idx)
                if buf:
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(buf)}\r\n\r\n".encode())
                    self.wfile.write(buf)
                    self.wfile.write(b"\r\n")
                time.sleep(0.066)  # 15fps配信
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return


def parse_int_list(spec):
    """'8765,8767' -> [8765, 8767]."""
    return [int(x) for x in str(spec).split(",") if x.strip()]


def main():
    """Entry point."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--http", type=int, default=8780)
    ap.add_argument(
        "--telemetry",
        default="8765",
        help="テレメトリ受信 UDP ポート(ペアごと・カンマ区切り)",
    )
    ap.add_argument(
        "--ctl-port", default="8766", help="制御送信 UDP ポート(ペアごと・カンマ区切り)"
    )
    ap.add_argument(
        "--labels", default="", help="ペアの表示名(カンマ区切り。既定 A,B,…)"
    )
    ap.add_argument(
        "--cams",
        default="",
        help="パネルに表示するカメラindex(カンマ区切り。例 0 / 0,1)",
    )
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    tele = parse_int_list(args.telemetry)
    ctls = parse_int_list(args.ctl_port)
    if len(tele) != len(ctls):
        raise SystemExit("--telemetry と --ctl-port の個数を揃えてください")
    labels = [x.strip() for x in args.labels.split(",") if x.strip()]
    LABELS[:] = [
        labels[i] if i < len(labels) else chr(65 + i) for i in range(len(tele))
    ]
    CTL_PORTS[:] = ctls
    LATEST[:] = [{} for _ in tele]

    cam_ids = [int(x) for x in args.cams.split(",") if x.strip() != ""]
    cams_html = ""
    if cam_ids:
        try:
            import cv2  # noqa: F401  存在確認のみ(本体はcam_loop内でimport)

            for i in cam_ids:
                threading.Thread(target=cam_loop, args=(i,), daemon=True).start()
            cams_html = "".join(
                f'<div class="card" style="padding:6px"><div class="lbl">camera {i}</div>'
                f'<img src="/cam/{i}" style="width:340px;max-width:45vw;border-radius:6px"></div>'
                for i in cam_ids
            )
        except ImportError:
            print(
                "[cam] opencv(cv2)が見つからないためカメラ表示は無効(pip install opencv-python)"
            )
    PAGE_FINAL[0] = PAGE.replace("%%CAMS%%", cams_html)

    for i, port in enumerate(tele):
        threading.Thread(target=telemetry_loop, args=(i, port), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.http), Handler)
    url = f"http://127.0.0.1:{args.http}"
    print(
        f"[panel] {url} で操作パネル起動(ペア {LABELS} テレメトリ:{tele}/制御:{ctls})"
    )
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

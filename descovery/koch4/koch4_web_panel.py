"""koch4_web_panel.py — ブラウザ操作パネル + リアルタイムグラフ（標準ライブラリのみ・オフライン動作）

Mac/Windows共通。teleop_plusのUDPテレメトリを受けてブラウザに配信(SSE)し、
スライダ/ボタン操作をUDP制御チャネルでteleop_plusへ返す。グラフはCanvas直描き(軽量)。

使い方: 通常は koch4_teleop.py --panel または koch4_dual_launch.py から起動される。単体起動:
  python koch4_web_panel.py            # http://127.0.0.1:8780 が自動で開く
ポート: HTTP 8780 / テレメトリ受信 UDP 8765 / 制御送信 UDP 8766
"""
import argparse, json, socket, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

latest = {}
_lock = threading.Lock()
CTL_ADDR = ["127.0.0.1", 8766]
CAMS_JPEG = {}      # カメラindex -> 最新JPEGバイト列
PAGE_FINAL = [""]   # %%CAMS%%差し込み後のHTML


def cam_loop(idx):
    """カメラを開いて最新フレームをJPEG保持(パネル専用・制御ループとは独立プロセス)"""
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
<title>Koch Panel</title><style>
body{font-family:'Segoe UI',Meiryo,sans-serif;background:#1b1e24;color:#e8eaed;margin:0;padding:10px}
h1{font-size:16px;margin:4px 0 8px}
.row{display:flex;gap:10px;flex-wrap:wrap}
.card{background:#252a33;border-radius:8px;padding:10px;margin-bottom:8px}
canvas{background:#12151a;border-radius:6px;width:100%;height:130px;display:block}
.lbl{font-size:11px;color:#9aa0a6;margin:6px 0 2px}
.chip{display:inline-block;background:#12151a;border-radius:12px;padding:3px 10px;margin:2px;font-size:12px}
.chip b{color:#7fd1ff}
input[type=range]{width:150px;vertical-align:middle}
button{background:#2d5be3;color:#fff;border:0;border-radius:6px;padding:6px 14px;margin:2px;cursor:pointer;font-size:13px}
button.warn{background:#c0392b} button.mode{background:#3a4150} button.mode.on{background:#1e8e3e}
.val{display:inline-block;min-width:44px;font-size:12px;color:#7fd1ff}
.legend{font-size:11px;color:#9aa0a6}
</style></head><body>
<h1>Koch 操作パネル <span id="conn" class="chip">未接続</span></h1>
<div class="row">%%CAMS%%</div>
<div class="card" id="status"></div>
<div class="card">
  <button class="mode" id="m_off" onclick="setMode('off')">FF: OFF</button>
  <button class="mode" id="m_gripper" onclick="setMode('gripper')">FF: 握り返し</button>
  <button class="mode" id="m_arm" onclick="setMode('arm')">FF: 腕+握り</button>
  <button onclick="ctl({resync:1})">合流リセット</button>
  <button class="warn" onclick="if(confirm('テレオペを停止しますか?'))ctl({stop:1})">停止</button>
  <div class="row" id="sliders"></div>
</div>
<div class="card"><div class="lbl">Leader 指令位置 [norm] <span class="legend" id="lg"></span></div><canvas id="cL"></canvas>
<div class="lbl">Follower 実位置 [norm]</div><canvas id="cF"></canvas>
<div class="lbl">差分 L−F [norm]</div><canvas id="cD"></canvas>
<div class="lbl">Follower 電流 [mA] (肩2軸=負荷%×10) / 黒破線=FF指令</div><canvas id="cC"></canvas></div>
<script>
const ORDER=["shoulder_pan","shoulder_lift","elbow_flex","wrist_flex","wrist_roll","gripper"];
const COLS=["#4e9cf5","#ff9f43","#2ecc71","#e74c3c","#a55eea","#b8860b"];
const WIN=15, buf={L:{},F:{},D:{},C:{}}, ffb=[];
ORDER.forEach(m=>{for(const p in buf) buf[p][m]=[];});
document.getElementById('lg').textContent = ORDER.map((m,i)=>(i+1)+':'+m).join('  ');
const SL=[["ff_ke","差分ゲイン",0,30,1],["ff_cap","握り上限mA",60,900,10],
          ["ff_gain","(spring)ゲイン",0,2.5,0.1],["ff_floor","(spring)バネmA",0,120,5],
          ["arm_gain","腕ゲイン",0,1.5,0.05],["arm_cap","腕上限mA",0,400,10],["max_rel","追従リミッタ",5,100,5]];
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
let lastT=0;
const es=new EventSource('/stream');
es.onmessage=e=>{
  const d=JSON.parse(e.data); if(!d.t&&d.t!==0) return;
  document.getElementById('conn').innerHTML=(Date.now()/1000-d._rx<2)?'<b>接続中</b>':'停止中';
  lastT=d.t;
  ORDER.forEach(m=>{
    const l=(d.pos||{})[m]??0, f=(d.fpos||{})[m]??0;
    let c=(d.cur||{})[m]??0; if(m=="shoulder_pan"||m=="shoulder_lift") c*=10;
    push(buf.L[m],d.t,l); push(buf.F[m],d.t,f); push(buf.D[m],d.t,l-f); push(buf.C[m],d.t,c);});
  push(ffb,d.t,d.ff||0);
  const p=d.params||{};
  for(const k in p){const s=document.getElementById('s_'+k);
    if(s&&document.activeElement!==s){s.value=p[k];document.getElementById('v_'+k).textContent=p[k];}}
  ["off","gripper","arm"].forEach(m=>document.getElementById('m_'+m).classList.toggle('on',d.mode===m));
  document.getElementById('status').innerHTML=
    `<span class="chip">grip <b>${(d.cur||{}).gripper??0} mA</b></span>`+
    `<span class="chip">FF指令 <b>${d.ff??0} mA</b></span>`+
    `<span class="chip">温度 <b>${d.temp??'-'}°C</b></span>`+
    `<span class="chip">フレーム <b>${d.n??0}</b></span>`+
    `<span class="chip">再接続 <b>${d.rec??0}</b></span>`+
    `<span class="chip">モード <b>${d.mode??'-'}</b></span>`;
};
function push(a,t,v){a.push([t,v]); while(a.length&&t-a[0][0]>WIN)a.shift();}
function draw(){
  [["cL","L"],["cF","F"],["cD","D"],["cC","C"]].forEach(([cid,key])=>{
    const cv=document.getElementById(cid),g=cv.getContext('2d');
    if(cv.width!==cv.clientWidth*2){cv.width=cv.clientWidth*2;cv.height=260;}
    g.clearRect(0,0,cv.width,cv.height);
    let lo=1e9,hi=-1e9;
    ORDER.forEach(m=>buf[key][m].forEach(p=>{if(p[1]<lo)lo=p[1];if(p[1]>hi)hi=p[1];}));
    if(key==="C") ffb.forEach(p=>{if(p[1]<lo)lo=p[1];if(p[1]>hi)hi=p[1];});
    if(lo>hi){lo=-1;hi=1;} const pad=(hi-lo)*0.1+1e-6; lo-=pad;hi+=pad;
    if(key==="D"){const y0=cv.height*(1-(0-lo)/(hi-lo)); g.strokeStyle="#555";g.beginPath();g.moveTo(0,y0);g.lineTo(cv.width,y0);g.stroke();}
    g.font="20px sans-serif";g.fillStyle="#666";g.fillText(hi.toFixed(0),6,22);g.fillText(lo.toFixed(0),6,cv.height-8);
    const t1=lastT,t0=t1-WIN;
    ORDER.forEach((m,i)=>{line(g,buf[key][m],t0,t1,lo,hi,cv,COLS[i],false);});
    if(key==="C") line(g,ffb,t0,t1,lo,hi,cv,"#eee",true);
  });
  requestAnimationFrame(draw);}
function line(g,a,t0,t1,lo,hi,cv,col,dash){
  if(a.length<2)return; g.strokeStyle=col;g.lineWidth=2;g.setLineDash(dash?[8,6]:[]);g.beginPath();
  a.forEach((p,i)=>{const x=(p[0]-t0)/(t1-t0)*cv.width, y=cv.height*(1-(p[1]-lo)/(hi-lo));
    i?g.lineTo(x,y):g.moveTo(x,y);});
  g.stroke();g.setLineDash([]);}
draw();
</script></body></html>"""


def telemetry_loop(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", port))
    s.settimeout(0.5)
    while True:
        try:
            data, _ = s.recvfrom(8192)
        except socket.timeout:
            continue
        except OSError:
            return
        try:
            msg = json.loads(data.decode())
        except Exception:
            continue
        with _lock:
            latest.clear()
            latest.update(msg)
            latest["_rx"] = time.time()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # アクセスログを黙らせる
        pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            body = (PAGE_FINAL[0] or PAGE.replace("%%CAMS%%", "")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/cam/"):
            try:
                idx = int(self.path.rsplit("/", 1)[1])
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
            except (BrokenPipeError, ConnectionResetError):
                return
        elif self.path.startswith("/ctl"):
            try:
                q = self.path.split("c=", 1)[1]
                from urllib.parse import unquote
                cmd = unquote(q)
                json.loads(cmd)  # 形式検証
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.sendto(cmd.encode(), (CTL_ADDR[0], CTL_ADDR[1]))
                self.send_response(204)
                self.end_headers()
            except Exception:
                self.send_response(400)
                self.end_headers()
        elif self.path.startswith("/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                while True:
                    with _lock:
                        payload = json.dumps(latest)
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.066)  # 15Hz配信(描画には十分・CSVは30Hzフル記録)
            except (BrokenPipeError, ConnectionResetError):
                return
        else:
            self.send_response(404)
            self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", type=int, default=8780)
    ap.add_argument("--telemetry", type=int, default=8765)
    ap.add_argument("--ctl-port", type=int, default=8766)
    ap.add_argument("--cams", default="", help="パネルに表示するカメラindex(カンマ区切り。例 0 / 0,1)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    CTL_ADDR[1] = args.ctl_port

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
                for i in cam_ids)
        except ImportError:
            print("[cam] opencv(cv2)が見つからないためカメラ表示は無効(pip install opencv-python)")
    PAGE_FINAL[0] = PAGE.replace("%%CAMS%%", cams_html)

    threading.Thread(target=telemetry_loop, args=(args.telemetry,), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.http), Handler)
    url = f"http://127.0.0.1:{args.http}"
    print(f"[panel] {url} で操作パネル起動(テレメトリ:{args.telemetry}/制御:{args.ctl_port})")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

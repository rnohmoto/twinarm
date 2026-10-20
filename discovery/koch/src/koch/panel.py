"""koch-panel — 2ペアを1画面で見る操作パネル（標準ライブラリのみ・オフライン動作）.

koch-teleop の UDP テレメトリをペアごとに受けてブラウザに配信(SSE)し、
スライダ/ボタン操作を**全ペアへ同じ値で**UDP 制御チャネルに返す（2ペア原則:
1本ずつゲインを変えない）。グラフは Canvas 直描き。

使い方: 通常は koch-launch から起動される。単体起動:
  python koch-panel --telemetry 8765,8767 --ctl-port 8766,8768 --labels A,B
  python koch-panel                       # 1ペア(8765/8766)
ポート: HTTP 8780 / テレメトリ受信 UDP(ペアごと) / 制御送信 UDP(ペアごと・同報)
"""

import argparse
import json
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, unquote, urlparse

_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):  # Windows cp932 console: never crash on symbols
    _reconfigure(errors="replace")

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


PAGE = resources.files("koch").joinpath("panel.html").read_text(encoding="utf-8")


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
                "[cam] opencv(cv2)が見つからないためカメラ表示は無効(lerobot 同梱の opencv-python-headless が無い。uv sync を実行)"
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

#!/usr/bin/env python3
"""VR bridge for the koch4 virtual-object demo (network only; stdlib only).

Serves ``webxr/`` (the three.js page) to the Quest Pro browser, republishes the
teleop telemetry as ``/state`` for the page, and relays the page's contact
decisions to ``koch4_teleop.py`` as ``{"vwall": ...}`` control messages::

  koch4_teleop.py --ff vwall --viz-port 8765,8769
        │ UDP 8769 (telemetry)                    ▲ UDP 8766 (control)
        ▼                                          │
  koch4_vr_bridge.py --telemetry 8769 --ctl-port 8766 --port 8443
        │ GET /state (20 Hz poll)                  ▲ POST /contact (on change + keep-alive)
        ▼                                          │
  Quest Pro browser  https://<Mac の IP>:8443/  (webxr/index.html)

The page decides *which* object the digital twin's gripper is touching; the teleop
process decides *whether* the trigger is inside that object and writes the servo
registers; the servo's own position loop renders the wall. This bridge never opens
a serial port. Risk class: network only, but it commands a live teleop session
(every field is clamped by the teleop and the wall is released after WALL_TTL_SEC
without a refresh).

使い方（Mac・descovery で `uv run`）:
  uv run python koch4/koch4_vr_bridge.py --sim                # テレオペなし: 分身が正弦波で動き、壁は内部判定
  uv run python koch4/koch4_vr_bridge.py --telemetry 8769     # テレオペ(--viz-port 8765,8769)と接続
  uv run python koch4/koch4_vr_bridge.py --http --port 8080   # adb reverse 方式(証明書警告なし)
"""

import argparse
import json
import math
import socket
import ssl
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
WEBXR_DIR = HERE / "webxr"
DEFAULT_WORK_DIR = HERE / "work"
TELEMETRY_HOST = "127.0.0.1"
STALE_AFTER_SEC = 2.0
KEEPALIVE_SEC = 1.0
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
# 正規化 ±100 → 表示用の関節角(rad)への換算スパン(☆分身表示用の名目値。較正値ではない)
SPAN_DEG = {
    "shoulder_pan": 180.0,
    "shoulder_lift": 100.0,
    "elbow_flex": 100.0,
    "wrist_flex": 100.0,
    "wrist_roll": 180.0,
}
SIM_OBJECT = {"name": "sim", "width": 50.0, "release": 1.0}

STATE: dict[str, Any] = {
    "t": 0.0,
    "q": [0.0] * 5,
    "open": 100.0,
    "vwall": None,
    "mode": "off",
    "src": "none",
}
CONTACT: dict[str, Any] = {"vwall": None, "t": 0.0}
LOCK = threading.Lock()


def norm_to_rad(name, value):
    """Map a normalized joint value (±100) to radians for the twin display."""
    return float(value) / 100.0 * math.radians(SPAN_DEG[name] / 2.0)


def telemetry_loop(port):
    """Receive teleop telemetry frames and keep the latest one."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((TELEMETRY_HOST, port))
    sock.settimeout(1.0)
    print(f"[bridge] テレメトリ待受 UDP {port}")
    while True:
        try:
            data, _ = sock.recvfrom(65535)
        except TimeoutError:
            continue
        try:
            frame = json.loads(data.decode())
        except ValueError:
            continue
        pos = frame.get("pos", {})
        with LOCK:
            STATE.update(
                t=float(frame.get("t", 0.0)),
                q=[round(norm_to_rad(j, pos.get(j, 0.0)), 4) for j in JOINTS],
                open=float(pos.get("gripper", 100.0)),
                vwall=frame.get("vwall"),
                mode=frame.get("mode", "off"),
                src="teleop",
            )
            STATE["_rx"] = time.monotonic()


def sim_loop(hz):
    """Synthesize a moving twin and a gripper sweep; engage the wall locally."""
    t0 = time.perf_counter()
    engaged = False
    while True:
        t = time.perf_counter() - t0
        q = [
            0.6 * math.sin(t * 0.6),
            0.45 * math.sin(t * 0.8) + 0.25,
            0.7 * math.sin(t * 0.7 + 1),
            0.5 * math.sin(t * 0.9 + 2),
            0.8 * math.sin(t * 0.5),
        ]
        opening = 50.0 + 50.0 * math.sin(t * 1.2)
        with LOCK:
            wall = CONTACT["vwall"]
        vwall = None
        if wall:
            limit = wall["width"] + (wall.get("release", 1.0) if engaged else 0.0)
            engaged = opening <= limit
            vwall = {"name": wall["name"], "width": wall["width"], "engaged": engaged}
        else:
            engaged = False
        with LOCK:
            STATE.update(
                t=round(t, 3),
                q=[round(v, 4) for v in q],
                open=round(opening, 1),
                vwall=vwall,
                mode="vwall",
                src="sim",
            )
            STATE["_rx"] = time.monotonic()
        time.sleep(1.0 / hz)


def keepalive_loop(ctl_sock, ctl_port, sim):
    """Re-send the current contact every KEEPALIVE_SEC so the teleop's wall TTL stays alive."""
    while True:
        time.sleep(KEEPALIVE_SEC)
        with LOCK:
            wall = CONTACT["vwall"]
            fresh = time.monotonic() - CONTACT["t"] < STALE_AFTER_SEC * 2
        if wall is not None and fresh and not sim and ctl_sock is not None:
            ctl_sock.sendto(
                json.dumps({"vwall": wall}).encode(), (TELEMETRY_HOST, ctl_port)
            )


def make_handler(ctl_sock, ctl_port, sim):
    """Build the HTTP handler class bound to the control socket."""

    class Handler(SimpleHTTPRequestHandler):
        """Static files + /state + /contact."""

        def do_GET(self):
            """Serve /state as JSON, everything else from webxr/."""
            if self.path.split("?")[0] != "/state":
                super().do_GET()
                return
            with LOCK:
                stale = time.monotonic() - STATE.get("_rx", 0.0) > STALE_AFTER_SEC
                body = {k: v for k, v in STATE.items() if not k.startswith("_")}
            if stale:
                body["src"] = "none"
            payload = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            """Accept {"vwall": {...}|null} from the page and forward it to the teleop."""
            if self.path.split("?")[0] != "/contact":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            try:
                msg = json.loads(self.rfile.read(length).decode() or "{}")
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            wall = msg.get("vwall")
            with LOCK:
                changed = wall != CONTACT["vwall"]
                CONTACT["vwall"], CONTACT["t"] = wall, time.monotonic()
            if changed:
                print(f"[contact] {wall['name'] if wall else '解除'}: {wall}")
            if not sim and ctl_sock is not None:
                ctl_sock.sendto(
                    json.dumps({"vwall": wall}).encode(), (TELEMETRY_HOST, ctl_port)
                )
            self.send_response(204)
            self.end_headers()

        def log_message(self, format, *args):  # http.server's own parameter name
            """Silence the 20 Hz /state and /contact chatter."""
            first = str(args[0]) if args else ""
            if "/state" not in first and "/contact" not in first:
                super().log_message(format, *args)

    return Handler


def ensure_cert(cert_dir):
    """Create a self-signed certificate with openssl once (WebXR needs HTTPS)."""
    cert_dir.mkdir(parents=True, exist_ok=True)
    crt, key = cert_dir / "cert.pem", cert_dir / "key.pem"
    if crt.exists() and key.exists():
        return crt, key
    try:
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(key),
                "-out",
                str(crt),
                "-days",
                "825",
                "-nodes",
                "-subj",
                "/CN=koch4-xr",
            ],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        sys.exit(
            "openssl で証明書を作れませんでした。--http + adb reverse 方式を使ってください。\n"
            + str(e)
        )
    print(f"自己署名証明書を生成: {crt}")
    return crt, key


def lan_ip():
    """Best-effort LAN address for the URL hint."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def build_parser():
    """Define the command line."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--sim", action="store_true", help="テレオペなしのデモ(壁は内部で判定)"
    )
    ap.add_argument(
        "--telemetry",
        type=int,
        default=8769,
        help="テレオペのテレメトリを受ける UDP ポート",
    )
    ap.add_argument(
        "--ctl-port",
        type=int,
        default=8766,
        help="テレオペの制御 UDP ポート(壁を送る先)",
    )
    ap.add_argument(
        "--port", type=int, default=8443, help="Quest が開く HTTP(S) ポート"
    )
    ap.add_argument(
        "--http",
        action="store_true",
        help="TLS なし(adb reverse で localhost に割当てる方式)",
    )
    ap.add_argument("--hz", type=float, default=50, help="--sim の更新レート")
    ap.add_argument(
        "--work-dir",
        type=Path,
        default=DEFAULT_WORK_DIR,
        help="証明書の置き場(work/_certs)",
    )
    return ap


def main():
    """Entry point."""
    args = build_parser().parse_args()
    if not (WEBXR_DIR / "three.module.js").exists():
        sys.exit(
            f"three.module.js がありません: {WEBXR_DIR}\n→ python koch4/webxr/setup_assets.py を一度実行"
        )
    ctl_sock = None if args.sim else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if args.sim:
        threading.Thread(target=sim_loop, args=(args.hz,), daemon=True).start()
    else:
        threading.Thread(
            target=telemetry_loop, args=(args.telemetry,), daemon=True
        ).start()
    threading.Thread(
        target=keepalive_loop, args=(ctl_sock, args.ctl_port, args.sim), daemon=True
    ).start()

    handler = partial(
        make_handler(ctl_sock, args.ctl_port, args.sim), directory=str(WEBXR_DIR)
    )
    httpd = ThreadingHTTPServer(
        ("0.0.0.0", args.port), handler
    )  # LAN 待受(ヘッドセットが開く)
    scheme = "http"
    if not args.http:
        crt, key = ensure_cert(args.work_dir / "_certs")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(crt), str(key))
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    url = f"{scheme}://{lan_ip()}:{args.port}/"
    print(f"\n=== koch4 VR bridge [{'SIM' if args.sim else 'teleop'}] ===")
    print(f"Quest ブラウザでこの URL を開く: {url}")
    if scheme == "https":
        print(
            "（初回は証明書警告 → 詳細設定 → 続行。Quest と Mac は同一 Wi-Fi。会場では自前ルータ）"
        )
    else:
        print(
            f"（adb reverse tcp:{args.port} tcp:{args.port} → Quest で http://localhost:{args.port}/ ）"
        )
    print("Ctrl+C で終了\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if ctl_sock is not None:
            ctl_sock.sendto(
                json.dumps({"vwall": None}).encode(), (TELEMETRY_HOST, args.ctl_port)
            )


if __name__ == "__main__":
    main()

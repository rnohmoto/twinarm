#!/usr/bin/env python3
"""VR bridge for the koch4 virtual-object demo (network only; stdlib only).

Serves ``webxr/`` (the three.js page) to the Quest Pro browser, republishes the
teleop telemetry as ``/state``, keeps the twin/object configuration
(``config/koch4_twin.json``: joint spans/offsets/signs, base placement, objects with
stiffness/cap/mass and their spots) behind ``/config``, and relays the page's contact
decisions to ``koch4_teleop.py`` as ``{"vwall": ...}`` control messages::

  koch4_teleop.py --ff vwall [--vw] --viz-port 8765,8769
        │ UDP 8769 (telemetry)                    ▲ UDP 8766 (control: vwall / twin)
        ▼                                          │
  koch4_vr_bridge.py --telemetry 8769 --ctl-port 8766 --port 8443
        │ GET /state /config                       ▲ POST /contact  POST /config
        ▼                                          │
  Quest Pro browser  https://<Mac の IP>:8443/  (webxr/index.html; ?spectator=1 = 見るだけ)

The page decides *which* object the digital twin's gripper is touching; the teleop
decides *whether* the trigger is inside that object and writes the servo registers;
the servo's own position loop renders the wall, and (with --vw) the leader's lift and
elbow render the object's weight. This bridge never opens a serial port. Risk class:
network only, but it commands a live teleop session (every field is clamped by the
teleop and the wall is released after WALL_TTL_SEC without a refresh).

使い方（Mac・descovery で `uv run`）:
  uv run python koch4/koch4_vr_bridge.py --sim                # テレオペなし: 分身が正弦波で動き、壁は内部判定
  uv run python koch4/koch4_vr_bridge.py --telemetry 8769     # テレオペ(--viz-port 8765,8769)と接続
  uv run python koch4/koch4_vr_bridge.py --http --port 8080   # adb reverse 方式(証明書警告なし)
設定ファイル: koch4/config/koch4_twin.json（ページの編集モードで保存される。手で編集しても可）
"""

import argparse
import copy
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

_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):  # Windows cp932 console: never crash on symbols
    _reconfigure(errors="replace")

HERE = Path(__file__).resolve().parent
WEBXR_DIR = HERE / "webxr"
DEFAULT_CONFIG_DIR = HERE / "config"
DEFAULT_WORK_DIR = HERE / "work"
TWIN_CONFIG_NAME = "koch4_twin.json"
TELEMETRY_HOST = "127.0.0.1"
STALE_AFTER_SEC = 2.0
KEEPALIVE_SEC = 1.0
TWIN_RESEND_SEC = 10.0
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]

# 分身と物体の既定値。ページ側の DEFAULTS と同じ内容(編集モードで上書き・保存される)
DEFAULT_TWIN_CONFIG: dict[str, Any] = {
    "joints": {
        "shoulder_pan": {"span_deg": 180.0, "offset_deg": 0.0, "sign": 1},
        "shoulder_lift": {"span_deg": 100.0, "offset_deg": 0.0, "sign": 1},
        "elbow_flex": {"span_deg": 100.0, "offset_deg": 0.0, "sign": 1},
        "wrist_flex": {"span_deg": 100.0, "offset_deg": 0.0, "sign": 1},
        "wrist_roll": {"span_deg": 180.0, "offset_deg": 0.0, "sign": 1},
    },
    "base": {"x": 0.0, "y": 0.75, "z": -0.6, "yaw_deg": 0.0},
    "objects": {
        "ball": {
            "label": "硬いボール",
            "width": 45,
            "p_gain": 900,
            "cap_ma": 350,
            "release": 1.5,
            "mass_g": 45,
            "color": "#ef4444",
            "kind": "sphere",
            "spot": [0.16, 0.0, 0.10],
        },
        "sponge": {
            "label": "スポンジ",
            "width": 60,
            "p_gain": 250,
            "cap_ma": 120,
            "release": 2.0,
            "mass_g": 10,
            "color": "#facc15",
            "kind": "box",
            "spot": [-0.14, 0.0, 0.12],
        },
        "block": {
            "label": "ブロック",
            "width": 30,
            "p_gain": 1200,
            "cap_ma": 400,
            "release": 1.5,
            "mass_g": 120,
            "color": "#60a5fa",
            "kind": "box",
            "spot": [0.02, 0.0, 0.22],
        },
    },
}
LIMITS = {  # POST /config の clamp 範囲
    "span_deg": (10.0, 360.0),
    "offset_deg": (-180.0, 180.0),
    "width": (0.0, 100.0),
    "p_gain": (0, 16383),
    "cap_ma": (0, 900),
    "release": (0.0, 20.0),
    "mass_g": (0.0, 2000.0),
}

STATE: dict[str, Any] = {
    "t": 0.0,
    "pos": {},
    "q": [0.0] * 5,
    "open": 100.0,
    "vwall": None,
    "vw": None,
    "alerts": [],
    "hw": None,
    "mode": "off",
    "src": "none",
}
CONTACT: dict[str, Any] = {"vwall": None, "t": 0.0}
TWIN: dict[str, Any] = copy.deepcopy(DEFAULT_TWIN_CONFIG)
LOCK = threading.Lock()


def clamp(value, lo, hi):
    """Clamp a number into [lo, hi]."""
    return max(lo, min(hi, value))


def norm_to_rad(name, value):
    """Map a normalized joint value (±100) to radians with the twin config."""
    m = TWIN["joints"][name]
    degrees = float(value) / 100.0 * m["span_deg"] / 2.0 + m["offset_deg"]
    return m["sign"] * math.radians(degrees)


def load_twin_config(path):
    """Read the twin config file if present; unknown keys are ignored."""
    if not path.exists():
        return
    try:
        merge_twin_config(json.loads(path.read_text(encoding="utf-8")))
        print(f"[config] 読み込み {path}")
    except (OSError, ValueError) as e:
        print(f"[config] ⚠ {path} を読めません({e}) — 既定値で続行")


def merge_twin_config(payload):
    """Merge a (partial) config into TWIN with every number clamped."""
    if not isinstance(payload, dict):
        return
    for j, m in (payload.get("joints") or {}).items():
        if j in TWIN["joints"] and isinstance(m, dict):
            cur = TWIN["joints"][j]
            cur["span_deg"] = clamp(
                float(m.get("span_deg", cur["span_deg"])), *LIMITS["span_deg"]
            )
            cur["offset_deg"] = clamp(
                float(m.get("offset_deg", cur["offset_deg"])), *LIMITS["offset_deg"]
            )
            cur["sign"] = 1 if int(m.get("sign", cur["sign"])) >= 0 else -1
    base = payload.get("base")
    if isinstance(base, dict):
        for k in ("x", "y", "z"):
            TWIN["base"][k] = clamp(float(base.get(k, TWIN["base"][k])), -3.0, 3.0)
        TWIN["base"]["yaw_deg"] = clamp(
            float(base.get("yaw_deg", TWIN["base"]["yaw_deg"])), -180.0, 180.0
        )
    for name, obj in (payload.get("objects") or {}).items():
        if not isinstance(obj, dict):
            continue
        cur = TWIN["objects"].setdefault(
            str(name)[:32], copy.deepcopy(DEFAULT_TWIN_CONFIG["objects"]["ball"])
        )
        for key in ("width", "p_gain", "cap_ma", "release", "mass_g"):
            if key in obj:
                lo, hi = LIMITS[key]
                cur[key] = type(lo)(clamp(float(obj[key]), lo, hi))
        for key in ("label", "color", "kind"):
            if key in obj:
                cur[key] = str(obj[key])[:32]
        if isinstance(obj.get("spot"), list) and len(obj["spot"]) == 3:
            cur["spot"] = [clamp(float(v), -1.0, 1.0) for v in obj["spot"]]


def save_twin_config(path):
    """Write TWIN to disk (pretty JSON)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(TWIN, ensure_ascii=False, indent=2), encoding="utf-8")


def send_ctl(ctl_sock, ctl_port, message):
    """Send one JSON control message to the teleop (no-op in --sim)."""
    if ctl_sock is not None:
        ctl_sock.sendto(json.dumps(message).encode(), (TELEMETRY_HOST, ctl_port))


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
                pos={k: round(float(v), 2) for k, v in pos.items()},
                q=[round(norm_to_rad(j, pos.get(j, 0.0)), 4) for j in JOINTS],
                open=float(pos.get("gripper", 100.0)),
                vwall=frame.get("vwall"),
                vw=frame.get("vw"),
                alerts=frame.get("alerts", []),
                hw=frame.get("hw"),
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
        pos = {
            "shoulder_pan": 40.0 * math.sin(t * 0.6),
            "shoulder_lift": 45.0 * math.sin(t * 0.8) + 25.0,
            "elbow_flex": 50.0 * math.sin(t * 0.7 + 1),
            "wrist_flex": 40.0 * math.sin(t * 0.9 + 2),
            "wrist_roll": 60.0 * math.sin(t * 0.5),
            "gripper": 50.0 + 50.0 * math.sin(t * 1.2),
        }
        with LOCK:
            wall = CONTACT["vwall"]
        vwall = None
        if wall:
            limit = wall["width"] + (wall.get("release", 1.0) if engaged else 0.0)
            engaged = pos["gripper"] <= limit
            vwall = {"name": wall["name"], "width": wall["width"], "engaged": engaged}
        else:
            engaged = False
        with LOCK:
            STATE.update(
                t=round(t, 3),
                pos={k: round(v, 2) for k, v in pos.items()},
                q=[round(norm_to_rad(j, pos[j]), 4) for j in JOINTS],
                open=round(pos["gripper"], 1),
                vwall=vwall,
                vw={
                    "shoulder_lift": 40 if engaged else 0,
                    "elbow_flex": 20 if engaged else 0,
                },
                alerts=(
                    ["⚠ 上限負荷（模擬）: 握り反力が上限に張り付いています"]
                    if engaged and pos["gripper"] < 20.0
                    else []
                ),
                hw={"L": 0, "F": 0},
                mode="vwall",
                src="sim",
            )
            STATE["_rx"] = time.monotonic()
        time.sleep(1.0 / hz)


def keepalive_loop(ctl_sock, ctl_port):
    """Re-send the contact (wall TTL) every second and the twin config every 10 s."""
    last_twin = 0.0
    while True:
        time.sleep(KEEPALIVE_SEC)
        with LOCK:
            wall = CONTACT["vwall"]
            fresh = time.monotonic() - CONTACT["t"] < STALE_AFTER_SEC * 2
            joints = copy.deepcopy(TWIN["joints"])
        if wall is not None and fresh:
            send_ctl(ctl_sock, ctl_port, {"vwall": wall})
        if time.monotonic() - last_twin > TWIN_RESEND_SEC:
            send_ctl(ctl_sock, ctl_port, {"twin": {"joints": joints}})
            last_twin = time.monotonic()


def make_handler(ctl_sock, ctl_port, sim, config_path):
    """Build the HTTP handler class bound to the control socket."""

    class Handler(SimpleHTTPRequestHandler):
        """Static files + /state + /config + /contact."""

        def send_json(self, body, status=200):
            """Write a JSON response."""
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def read_json(self):
            """Parse the request body as JSON (None on failure)."""
            length = int(self.headers.get("Content-Length", "0") or 0)
            try:
                return json.loads(self.rfile.read(length).decode() or "{}")
            except ValueError:
                return None

        def do_GET(self):
            """Serve /state and /config as JSON, everything else from webxr/."""
            route = self.path.split("?")[0]
            if route == "/state":
                with LOCK:
                    stale = time.monotonic() - STATE.get("_rx", 0.0) > STALE_AFTER_SEC
                    body = {k: v for k, v in STATE.items() if not k.startswith("_")}
                if stale:
                    body["src"] = "none"
                self.send_json(body)
            elif route == "/config":
                with LOCK:
                    body = copy.deepcopy(TWIN)
                self.send_json(body)
            else:
                super().do_GET()

        def do_POST(self):
            """/contact: {"vwall": {...}|null} → teleop. /config: merge, save, forward."""
            route = self.path.split("?")[0]
            msg = self.read_json()
            if msg is None:
                self.send_response(400)
                self.end_headers()
                return
            if route == "/contact":
                wall = msg.get("vwall")
                with LOCK:
                    changed = wall != CONTACT["vwall"]
                    CONTACT["vwall"], CONTACT["t"] = wall, time.monotonic()
                if changed:
                    print(f"[contact] {wall['name'] if wall else '解除'}: {wall}")
                send_ctl(ctl_sock, ctl_port, {"vwall": wall})
                self.send_response(204)
                self.end_headers()
            elif route == "/config":
                with LOCK:
                    if msg.get("reset"):
                        TWIN.clear()
                        TWIN.update(copy.deepcopy(DEFAULT_TWIN_CONFIG))
                    else:
                        merge_twin_config(msg)
                    save_twin_config(config_path)
                    body = copy.deepcopy(TWIN)
                send_ctl(ctl_sock, ctl_port, {"twin": {"joints": body["joints"]}})
                print(f"[config] 保存 {config_path}")
                self.send_json(body)
            else:
                self.send_response(404)
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
        "--config-dir",
        type=Path,
        default=DEFAULT_CONFIG_DIR,
        help="koch4_twin.json の置き場",
    )
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
    config_path = args.config_dir / TWIN_CONFIG_NAME
    load_twin_config(config_path)
    ctl_sock = None if args.sim else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if args.sim:
        threading.Thread(target=sim_loop, args=(args.hz,), daemon=True).start()
    else:
        threading.Thread(
            target=telemetry_loop, args=(args.telemetry,), daemon=True
        ).start()
    threading.Thread(
        target=keepalive_loop, args=(ctl_sock, args.ctl_port), daemon=True
    ).start()

    handler = partial(
        make_handler(ctl_sock, args.ctl_port, args.sim, config_path),
        directory=str(WEBXR_DIR),
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
    print(f"Quest ブラウザでこの URL を開く: {url}   (観客用: {url}?spectator=1)")
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
        send_ctl(ctl_sock, args.ctl_port, {"vwall": None})


if __name__ == "__main__":
    main()

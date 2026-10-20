#!/usr/bin/env python3
r"""VR bridge for the koch4 virtual-object demo (network only; stdlib only).

Serves ``webxr/`` (the three.js page) to the Quest Pro browser, republishes the
telemetry of one or two teleop processes as ``/state`` (one arm each), keeps the
twin/object configuration (``config/koch4_twin.json``: per-arm joint spans/offsets/
signs and base placement, objects with stiffness/cap/mass/bounce and their spots,
the desk) behind ``/config``, and relays the page's contact decisions to the right
``koch4_teleop.py`` as ``{"vwall": ...}`` control messages::

  koch4_teleop.py --ff vwall --vw --viz-port 8767,8770 --ctl-port 8768 --arm-label B
  koch4_teleop.py --leader-type koch_follower --ff vwall --vw --viz-port 8771,8773 \\
                  --ctl-port 8772 --arm-label F                      (2 人目・任意)
        │ UDP 8770 / 8773 (telemetry)              ▲ UDP 8768 / 8772 (control: vwall / twin)
        ▼                                          │
  koch4_vr_bridge.py --arms B,F --telemetry 8770,8773 --ctl-port 8768,8772 --port 8444
        │ GET /state /config                       ▲ POST /contact {"arm":"B","vwall":…}
        ▼                                          │ POST /config
  Quest Pro browser  https://<Mac の IP>:8444/  (webxr/index.html; ?spectator=1 = 見るだけ)

The page decides *which* object each twin's gripper is touching; each teleop decides
*whether* its trigger is inside that object and writes the servo registers; the
servo's own position loop renders the wall, and (with --vw) the arm's lift/elbow
render the object's weight. This bridge never opens a serial port. Risk class:
network only, but it commands live teleop sessions (every field is clamped by the
teleop and the wall is released after WALL_TTL_SEC without a refresh).

使い方（Mac・descovery で `uv run`）:
  uv run python koch4/koch4_vr_bridge.py --sim                       # 1 本・テレオペなし
  uv run python koch4/koch4_vr_bridge.py --sim --arms B,F            # 2 本の模擬
  uv run python koch4/koch4_vr_bridge.py --arms B --telemetry 8770 --ctl-port 8768
  uv run python koch4/koch4_vr_bridge.py --http --port 8080          # adb reverse 方式(証明書警告なし)
設定ファイル: koch4/config/koch4_twin.json（ページの編集モード・位置合わせで保存。手で編集しても可。
旧形式（arms なし・joints/base が最上位）は読み込み時に最初の腕へ移される）
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
CONFIG_VERSION = 2
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
MODELS = ("koch_leader", "koch_follower")

DEFAULT_JOINTS: dict[str, dict[str, float | int]] = {
    "shoulder_pan": {"span_deg": 180.0, "offset_deg": 0.0, "sign": 1},
    "shoulder_lift": {"span_deg": 100.0, "offset_deg": 0.0, "sign": 1},
    "elbow_flex": {"span_deg": 100.0, "offset_deg": 0.0, "sign": 1},
    "wrist_flex": {"span_deg": 100.0, "offset_deg": 0.0, "sign": 1},
    "wrist_roll": {"span_deg": 180.0, "offset_deg": 0.0, "sign": 1},
}
# 腕の既定の置き場所(local-floor 基準・m)。最初の腕=正面 0.6 m 先の机(高さ 0.75)、
# 2 本目=その右 0.5 m。実機に合わせる作業はページの位置合わせ(C)で行う。
DEFAULT_BASES = [
    {"x": 0.0, "y": 0.75, "z": -0.6, "yaw_deg": 0.0},
    {"x": 0.5, "y": 0.75, "z": -0.6, "yaw_deg": 0.0},
]
ARM_LABELS = {"koch_leader": "リーダー", "koch_follower": "フォロワー機(手)"}
# 物体の並び順がページのキー 1/2/3。軽い・柔らかい → 重い・硬い。
# spot は最初の腕のローカル座標(m)。bounce=反発係数(投げて落ちたときの跳ね方)
DEFAULT_OBJECTS: dict[str, dict[str, Any]] = {
    "sponge": {
        "label": "スポンジ",
        "width": 60,
        "p_gain": 250,
        "cap_ma": 120,
        "release": 2.0,
        "mass_g": 10,
        "bounce": 0.15,
        "color": "#facc15",
        "kind": "box",
        "spot": [-0.155, 0.0, 0.221],
    },
    "ball": {
        "label": "ボール",
        "width": 45,
        "p_gain": 900,
        "cap_ma": 350,
        "release": 1.5,
        "mass_g": 45,
        "bounce": 0.65,
        "color": "#ef4444",
        "kind": "sphere",
        "spot": [0.0, 0.0, 0.27],
    },
    "block": {
        "label": "鉄ブロック",
        "width": 30,
        "p_gain": 2000,
        "cap_ma": 450,
        "release": 1.0,
        "mass_g": 300,
        "bounce": 0.05,
        "color": "#94a3b8",
        "kind": "box",
        "spot": [0.155, 0.0, 0.221],
    },
}
DEFAULT_SCENE: dict[str, Any] = {
    # 机(最初の腕のローカル座標・m)。2 本目の腕が置かれたら幅はページ側で広げる
    "desk": {"x": 0.05, "z": 0.05, "w": 0.5, "d": 0.4},
    "sound": True,
    "ghost": "half",  # AR 中の分身の見せ方: solid / half / tips
}
LIMITS = {
    "span_deg": (10.0, 360.0),
    "offset_deg": (-180.0, 180.0),
    "width": (0.0, 100.0),
    "p_gain": (0, 16383),
    "cap_ma": (0, 900),
    "release": (0.0, 20.0),
    "mass_g": (0.0, 2000.0),
    "bounce": (0.0, 1.0),
}

# label -> latest telemetry frame of that arm
STATE: dict[str, dict[str, Any]] = {}
# label -> {"vwall": …, "t": monotonic}
CONTACT: dict[str, dict[str, Any]] = {}
TWIN: dict[str, Any] = {}
ARMS: list[str] = []  # order = --arms (the first one is the frame of desk/objects)
CTL_PORTS: dict[str, int] = {}
LOCK = threading.Lock()


def clamp(value, lo, hi):
    """Clamp a number into [lo, hi]."""
    return max(lo, min(hi, value))


def norm_to_rad(joint_map, name, value):
    """Map a normalized joint value (±100) to radians with one arm's joint map."""
    m = joint_map[name]
    degrees = float(value) / 100.0 * m["span_deg"] / 2.0 + m["offset_deg"]
    return m["sign"] * math.radians(degrees)


def empty_state():
    """A telemetry frame before anything arrived."""
    return {
        "t": 0.0,
        "pos": {},
        "q": [0.0] * 5,
        "open": 100.0,
        "vwall": None,
        "vw": None,
        "vw_unit": None,
        "alerts": [],
        "hw": None,
        "mode": "off",
        "src": "none",
        "device": "",
    }


def default_arm(index, model):
    """Default twin for the index-th arm of the given model."""
    base = DEFAULT_BASES[min(index, len(DEFAULT_BASES) - 1)]
    return {
        "label": ARM_LABELS.get(model, model),
        "model": model,
        "joints": copy.deepcopy(DEFAULT_JOINTS),
        "base": dict(base),
    }


def default_twin(arms, models):
    """The whole default configuration for the configured arms."""
    return {
        "version": CONFIG_VERSION,
        "arms": {a: default_arm(i, models[i]) for i, a in enumerate(arms)},
        "objects": copy.deepcopy(DEFAULT_OBJECTS),
        "scene": copy.deepcopy(DEFAULT_SCENE),
    }


def load_twin_config(path):
    """Read the twin config file if present; unknown keys are ignored."""
    if not path.exists():
        return
    try:
        merge_twin_config(json.loads(path.read_text(encoding="utf-8")))
        print(f"[config] 読み込み {path}")
    except (OSError, ValueError) as e:
        print(f"[config] ⚠ {path} を読めません({e}) — 既定値で続行")


def _merge_joints(target, payload):
    for j, m in (payload or {}).items():
        if j in target and isinstance(m, dict):
            cur = target[j]
            cur["span_deg"] = clamp(
                float(m.get("span_deg", cur["span_deg"])), *LIMITS["span_deg"]
            )
            cur["offset_deg"] = clamp(
                float(m.get("offset_deg", cur["offset_deg"])), *LIMITS["offset_deg"]
            )
            cur["sign"] = 1 if int(m.get("sign", cur["sign"])) >= 0 else -1


def _merge_base(target, payload):
    if not isinstance(payload, dict):
        return
    for k in ("x", "y", "z"):
        target[k] = clamp(float(payload.get(k, target[k])), -3.0, 3.0)
    target["yaw_deg"] = clamp(
        float(payload.get("yaw_deg", target["yaw_deg"])), -180.0, 180.0
    )


def _merge_arm(label, payload):
    arm = TWIN["arms"].get(label)
    if arm is None or not isinstance(payload, dict):
        return
    if isinstance(payload.get("label"), str):
        arm["label"] = payload["label"][:32]
    if payload.get("model") in MODELS:
        arm["model"] = payload["model"]
    _merge_joints(arm["joints"], payload.get("joints"))
    _merge_base(arm["base"], payload.get("base"))


def _merge_objects(payload):
    if not isinstance(payload, dict):
        return
    names = []
    for name, obj in payload.items():
        if not isinstance(obj, dict):
            continue
        key = str(name)[:32]
        names.append(key)
        cur = TWIN["objects"].setdefault(
            key, copy.deepcopy(DEFAULT_OBJECTS.get(key) or DEFAULT_OBJECTS["ball"])
        )
        for field in ("width", "p_gain", "cap_ma", "release", "mass_g", "bounce"):
            if field in obj:
                lo, hi = LIMITS[field]
                cur[field] = type(lo)(clamp(float(obj[field]), lo, hi))
        for field in ("label", "color", "kind"):
            if field in obj:
                cur[field] = str(obj[field])[:32]
        if isinstance(obj.get("spot"), list) and len(obj["spot"]) == 3:
            cur["spot"] = [clamp(float(v), -1.0, 1.0) for v in obj["spot"]]
    # 並び順＝ページのキー 1/2/3。全物体を含むペイロード(ファイル・保存)のときだけ揃える
    if names and set(names) >= set(TWIN["objects"]):
        TWIN["objects"] = {key: TWIN["objects"][key] for key in names}


def _merge_scene(payload):
    if not isinstance(payload, dict):
        return
    scene = TWIN["scene"]
    desk = payload.get("desk")
    if isinstance(desk, dict):
        for k in ("x", "z"):
            scene["desk"][k] = clamp(float(desk.get(k, scene["desk"][k])), -2.0, 2.0)
        for k in ("w", "d"):
            scene["desk"][k] = clamp(float(desk.get(k, scene["desk"][k])), 0.2, 3.0)
    if "sound" in payload:
        scene["sound"] = bool(payload["sound"])
    if payload.get("ghost") in ("solid", "half", "tips"):
        scene["ghost"] = payload["ghost"]


def merge_twin_config(payload):
    """Merge a (partial) config into TWIN with every number clamped.

    Accepts the v2 shape (``arms``/``objects``/``scene``) and the v1 shape
    (``joints``/``base`` at the top level = the first arm).
    """
    if not isinstance(payload, dict):
        return
    if "arms" in payload and isinstance(payload["arms"], dict):
        for label, arm in payload["arms"].items():
            _merge_arm(str(label), arm)
    elif ARMS and ("joints" in payload or "base" in payload):
        _merge_arm(
            ARMS[0], {"joints": payload.get("joints"), "base": payload.get("base")}
        )
    _merge_objects(payload.get("objects"))
    _merge_scene(payload.get("scene"))


def save_twin_config(path):
    """Write TWIN to disk (pretty JSON, UTF-8)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(TWIN, ensure_ascii=False, indent=2), encoding="utf-8")


def send_ctl(ctl_sock, label, message):
    """Send one JSON control message to the teleop of one arm (no-op in --sim)."""
    port = CTL_PORTS.get(label)
    if ctl_sock is not None and port:
        ctl_sock.sendto(json.dumps(message).encode(), (TELEMETRY_HOST, port))


def telemetry_loop(label, port):
    """Receive one teleop's telemetry frames and keep the latest one."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((TELEMETRY_HOST, port))
    sock.settimeout(1.0)
    print(f"[bridge] 腕 {label}: テレメトリ待受 UDP {port}")
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
            joint_map = TWIN["arms"][label]["joints"]
            STATE[label].update(
                t=float(frame.get("t", 0.0)),
                pos={k: round(float(v), 2) for k, v in pos.items()},
                q=[
                    round(norm_to_rad(joint_map, j, pos.get(j, 0.0)), 4) for j in JOINTS
                ],
                open=float(pos.get("gripper", 100.0)),
                vwall=frame.get("vwall"),
                vw=frame.get("vw"),
                vw_unit=frame.get("vw_unit"),
                alerts=frame.get("alerts", []),
                hw=frame.get("hw"),
                mode=frame.get("mode", "off"),
                device=frame.get("device", ""),
                src="teleop",
            )
            STATE[label]["_rx"] = time.monotonic()


def sim_loop(hz):
    """Synthesize moving twins and gripper sweeps; engage the walls locally."""
    t0 = time.perf_counter()
    engaged = dict.fromkeys(ARMS, False)
    while True:
        t = time.perf_counter() - t0
        for i, label in enumerate(ARMS):
            ph = i * 1.7
            pos = {
                "shoulder_pan": 40.0 * math.sin(t * 0.6 + ph),
                "shoulder_lift": 45.0 * math.sin(t * 0.8 + ph) + 25.0,
                "elbow_flex": 50.0 * math.sin(t * 0.7 + 1 + ph),
                "wrist_flex": 40.0 * math.sin(t * 0.9 + 2 + ph),
                "wrist_roll": 60.0 * math.sin(t * 0.5 + ph),
                "gripper": 50.0 + 50.0 * math.sin(t * 1.2 + ph),
            }
            with LOCK:
                wall = CONTACT.get(label, {}).get("vwall")
                joint_map = TWIN["arms"][label]["joints"]
            vwall = None
            if wall:
                limit = wall["width"] + (
                    wall.get("release", 1.0) if engaged[label] else 0.0
                )
                engaged[label] = pos["gripper"] <= limit
                vwall = {
                    "name": wall["name"],
                    "width": wall["width"],
                    "engaged": engaged[label],
                }
            else:
                engaged[label] = False
            with LOCK:
                STATE[label].update(
                    t=round(t, 3),
                    pos={k: round(v, 2) for k, v in pos.items()},
                    q=[round(norm_to_rad(joint_map, j, pos[j]), 4) for j in JOINTS],
                    open=round(pos["gripper"], 1),
                    vwall=vwall,
                    vw={
                        "shoulder_lift": 40 if engaged[label] else 0,
                        "elbow_flex": 20 if engaged[label] else 0,
                    },
                    alerts=(
                        ["⚠ 上限負荷（模擬）: 握り反力が上限に張り付いています"]
                        if engaged[label] and pos["gripper"] < 20.0
                        else []
                    ),
                    hw={"L": 0, "F": 0},
                    mode="vwall",
                    vw_unit={
                        "shoulder_lift": "PWM"
                        if TWIN["arms"][label]["model"] == "koch_follower"
                        else "mA",
                        "elbow_flex": "mA",
                    },
                    device=TWIN["arms"][label]["model"],
                    src="sim",
                )
                STATE[label]["_rx"] = time.monotonic()
        time.sleep(1.0 / hz)


def keepalive_loop(ctl_sock):
    """Re-send each arm's contact (wall TTL) every second and its twin map every 10 s."""
    last_twin = 0.0
    while True:
        time.sleep(KEEPALIVE_SEC)
        now = time.monotonic()
        with LOCK:
            contacts = {
                label: (c.get("vwall"), now - c.get("t", 0.0) < STALE_AFTER_SEC * 2)
                for label, c in CONTACT.items()
            }
            joints = {a: copy.deepcopy(TWIN["arms"][a]["joints"]) for a in ARMS}
        for label, (wall, fresh) in contacts.items():
            if wall is not None and fresh:
                send_ctl(ctl_sock, label, {"vwall": wall})
        if now - last_twin > TWIN_RESEND_SEC:
            for label in ARMS:
                send_ctl(ctl_sock, label, {"twin": {"joints": joints[label]}})
            last_twin = now


def public_state():
    """The /state body: every arm, plus the first arm mirrored at the top level."""
    now = time.monotonic()
    with LOCK:
        arms = {}
        for label in ARMS:
            body = {k: v for k, v in STATE[label].items() if not k.startswith("_")}
            if now - STATE[label].get("_rx", 0.0) > STALE_AFTER_SEC:
                body["src"] = "none"
            arms[label] = body
    out: dict[str, Any] = {"t": round(now, 3), "arms": arms}
    if ARMS:
        out.update(arms[ARMS[0]])  # 旧ページ互換(1 本目を最上位にも)
    return out


def make_handler(ctl_sock, config_path):
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
                self.send_json(public_state())
            elif route == "/config":
                with LOCK:
                    body = copy.deepcopy(TWIN)
                self.send_json(body)
            else:
                super().do_GET()

        def do_POST(self):
            """/contact: {"arm": label, "vwall": {...}|null} → that arm's teleop. /config: merge, save, forward."""
            route = self.path.split("?")[0]
            msg = self.read_json()
            if msg is None:
                self.send_response(400)
                self.end_headers()
                return
            if route == "/contact":
                label = str(msg.get("arm") or (ARMS[0] if ARMS else ""))
                if label not in CONTACT:
                    self.send_response(404)
                    self.end_headers()
                    return
                wall = msg.get("vwall")
                with LOCK:
                    changed = wall != CONTACT[label]["vwall"]
                    CONTACT[label]["vwall"], CONTACT[label]["t"] = (
                        wall,
                        time.monotonic(),
                    )
                if changed:
                    print(
                        f"[contact] {label}: {wall['name'] if wall else '解除'}: {wall}"
                    )
                send_ctl(ctl_sock, label, {"vwall": wall})
                self.send_response(204)
                self.end_headers()
            elif route == "/config":
                with LOCK:
                    if msg.get("reset"):
                        models = [TWIN["arms"][a]["model"] for a in ARMS]
                        TWIN.clear()
                        TWIN.update(default_twin(ARMS, models))
                    else:
                        merge_twin_config(msg)
                    save_twin_config(config_path)
                    body = copy.deepcopy(TWIN)
                for label in ARMS:
                    send_ctl(
                        ctl_sock,
                        label,
                        {"twin": {"joints": body["arms"][label]["joints"]}},
                    )
                print(f"[config] 保存 {config_path}")
                self.send_json(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):  # http.server's own parameter name
            """Silence the 30 Hz /state and /contact chatter."""
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


def split_list(spec, cast=str):
    """Turn 'B,F' / '8770,8773' into a list; blanks are dropped."""
    return [cast(p.strip()) for p in str(spec).split(",") if p.strip()]


def build_parser():
    """Define the command line."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--sim", action="store_true", help="テレオペなしのデモ(壁は内部で判定)"
    )
    ap.add_argument(
        "--arms",
        default="B",
        help="腕の名前をカンマ区切り(例 B,F)。順番が --telemetry/--ctl-port と対応。1 本目が机と物体の基準",
    )
    ap.add_argument(
        "--models",
        default="",
        help="腕ごとの機種をカンマ区切り(koch_leader/koch_follower)。未指定=1 本目 leader・2 本目以降 follower",
    )
    ap.add_argument(
        "--telemetry",
        default="8769",
        help="テレオペのテレメトリを受ける UDP ポート(腕ごとにカンマ区切り)",
    )
    ap.add_argument(
        "--ctl-port",
        default="8766",
        help="テレオペの制御 UDP ポート(壁を送る先・腕ごとにカンマ区切り)",
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
    arms = split_list(args.arms)
    if not arms:
        sys.exit("--arms に腕の名前を 1 つ以上")
    models = split_list(args.models) or []
    models += [
        "koch_leader" if i == 0 else "koch_follower"
        for i in range(len(models), len(arms))
    ]
    if any(m not in MODELS for m in models):
        sys.exit(f"--models は {MODELS} から")
    tele_ports = split_list(args.telemetry, int)
    ctl_ports = split_list(args.ctl_port, int)
    if not args.sim and len(tele_ports) != len(arms):
        sys.exit("--telemetry は --arms と同じ数のポートをカンマ区切りで")
    if len(ctl_ports) == 1 and len(arms) > 1:
        ctl_ports = ctl_ports * len(arms)  # --sim などで 1 つだけ渡されたとき
    if len(ctl_ports) != len(arms):
        sys.exit("--ctl-port は --arms と同じ数のポートをカンマ区切りで")
    ARMS[:] = arms
    CTL_PORTS.update(dict(zip(arms, ctl_ports, strict=True)))
    for label in arms:
        STATE[label] = empty_state()
        CONTACT[label] = {"vwall": None, "t": 0.0}
    TWIN.update(default_twin(arms, models))

    config_path = args.config_dir / TWIN_CONFIG_NAME
    load_twin_config(config_path)
    ctl_sock = None if args.sim else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if args.sim:
        threading.Thread(target=sim_loop, args=(args.hz,), daemon=True).start()
    else:
        for label, port in zip(arms, tele_ports, strict=True):
            threading.Thread(
                target=telemetry_loop, args=(label, port), daemon=True
            ).start()
    threading.Thread(target=keepalive_loop, args=(ctl_sock,), daemon=True).start()

    handler = partial(make_handler(ctl_sock, config_path), directory=str(WEBXR_DIR))
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
    print(
        f"\n=== koch4 VR bridge [{'SIM' if args.sim else 'teleop'}] 腕={arms} 機種={models} ==="
    )
    print(f"Quest ブラウザでこの URL を開く: {url}   (観客用: {url}?spectator=1)")
    if scheme == "https":
        print(
            "（初回は証明書警告 → 詳細設定 → 続行。Quest と Mac は同一 Wi-Fi。会場では自前ルータ）"
        )
    else:
        print(
            f"（USB 方式: koch4_quest_usb.py --port {args.port} を実行すると Quest 側で "
            f"http://localhost:{args.port}/ が開く）"
        )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n終了")
        for label in arms:
            send_ctl(ctl_sock, label, {"vwall": None})  # 壁を残さない


if __name__ == "__main__":
    main()

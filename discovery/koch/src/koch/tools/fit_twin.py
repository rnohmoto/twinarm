"""Fit the VR digital twin (twin.json) to the real leader from two reference poses.

The page maps a normalized joint value v to an angle  sign * (v/100 * span/2 + offset) [deg],
angle 0 = link pointing straight up. Defaults (span 100) under-rotate the arm, so the twin
floats above the desk while the real gripper touches the table. This tool:

  R1  arm straight up (upper arm, forearm, wrist in one vertical line; base facing front;
      gripper level)            -> offsets so every twin joint reads 0 deg here
  R2  fingertip touching the table straight in front (the usual lowest pose)
                                 -> chooses the joint signs by forward kinematics so the
                                    twin's fingertip lands on the desk in front of the base
  spans come from the leader calibration (full sweep in degrees)
  objects: ball at the R2 fingertip, sponge and block on the same radius at -35 / +35 deg

Reads the leader either from the bridge /state (teleop running) or from the bus (read-only).
Writes via POST /config on the running bridge (which saves config/twin.json and
forwards the joint map to the teleop); falls back to writing the file directly.
"""

import argparse
import itertools
import json
import math
import ssl
import sys
import time
import urllib.request
from pathlib import Path

from koch.paths import CONFIG_DIR, TWIN_CONFIG_NAME

JOINTS = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]
TWIN_JOINTS = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
]
AXIS_Y = 0.055  # desk -> lift axis (base 0.035 + 0.02)
L1, L2, L3 = (
    0.11,
    0.108,
    0.05 + 0.065,
)  # upper arm, forearm, wrist + fingertip (page geometry)
TICK_DEG = 360.0 / 4096.0
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def http_json(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"} if data else {}
    )
    with urllib.request.urlopen(req, timeout=3, context=CTX) as r:
        return json.loads(r.read().decode())


def read_state(bridge, seconds):
    """Average the leader's normalized values from /state; None if the stream is stale."""
    acc, t_seen = {j: [] for j in JOINTS}, set()
    t0 = time.time()
    while time.time() - t0 < seconds:
        st = http_json(f"{bridge}/state")
        if st.get("src") in (None, "none"):
            return None
        t_seen.add(st.get("t"))
        for j in JOINTS:
            if j in (st.get("pos") or {}):
                acc[j].append(float(st["pos"][j]))
        time.sleep(0.1)
    if len(t_seen) < 2:
        return None
    return {j: sum(v) / len(v) for j, v in acc.items() if v}


def read_bus(port_name, cal, seconds):
    """Average raw ticks on the bus and normalize like lerobot (read-only)."""
    from dynamixel_sdk import PacketHandler, PortHandler

    port, packet = PortHandler(port_name), PacketHandler(2.0)
    if not port.openPort():
        sys.exit(f"ポートが開けません: {port_name}")
    port.setBaudRate(1_000_000)
    acc = {i: [] for i in range(1, 7)}
    try:
        t0 = time.time()
        while time.time() - t0 < seconds:
            for i in range(1, 7):
                v, res, _ = packet.read4ByteTxRx(port, i, 132)
                if res == 0:
                    acc[i].append(v - (1 << 32) if v >= (1 << 31) else v)
            time.sleep(0.02)
    finally:
        port.closePort()
    out = {}
    for i, j in enumerate(JOINTS, start=1):
        if not acc[i]:
            continue
        raw = sum(acc[i]) / len(acc[i])
        lo, hi = cal[j]["range_min"], cal[j]["range_max"]
        b = min(hi, max(lo, raw))
        out[j] = (
            (b - lo) / (hi - lo) * 100
            if j == "gripper"
            else (b - lo) / (hi - lo) * 200 - 100
        )
    return out


def angle_deg(cfg, j, v):
    m = cfg[j]
    return m["sign"] * (v / 100.0 * m["span_deg"] / 2.0 + m["offset_deg"])


def fk(cfg, v):
    """Fingertip (x, y, z) in the robot frame, desk top = y 0, forward = +z."""
    a1 = math.radians(angle_deg(cfg, "shoulder_lift", v["shoulder_lift"]))
    a2 = a1 + math.radians(angle_deg(cfg, "elbow_flex", v["elbow_flex"]))
    a3 = a2 + math.radians(angle_deg(cfg, "wrist_flex", v["wrist_flex"]))
    y = AXIS_Y + L1 * math.cos(a1) + L2 * math.cos(a2) + L3 * math.cos(a3)
    z = L1 * math.sin(a1) + L2 * math.sin(a2) + L3 * math.sin(a3)
    p = math.radians(angle_deg(cfg, "shoulder_pan", v["shoulder_pan"]))
    return (z * math.sin(p), y, z * math.cos(p))


def fit(cal, v1, v2):
    spans = {
        "shoulder_pan": 360.0,
        "wrist_roll": 360.0,
        **{
            j: (cal[j]["range_max"] - cal[j]["range_min"]) * TICK_DEG
            for j in ("shoulder_lift", "elbow_flex", "wrist_flex")
        },
    }
    base = {
        j: {
            "span_deg": round(spans[j], 1),
            "offset_deg": round(-v1[j] / 100.0 * spans[j] / 2.0, 1),
            "sign": 1,
        }
        for j in TWIN_JOINTS
    }
    candidates = []
    for s1, s2, s3 in itertools.product((1, -1), repeat=3):
        cfg = json.loads(json.dumps(base))
        (
            cfg["shoulder_lift"]["sign"],
            cfg["elbow_flex"]["sign"],
            cfg["wrist_flex"]["sign"],
        ) = s1, s2, s3
        x, y, z = fk(cfg, v2)
        candidates.append(
            (abs(y) + (0.0 if z > 0.02 else 1.0), (s1, s2, s3), (x, y, z), cfg)
        )
    candidates.sort(key=lambda c: c[0])
    return candidates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leader-id", default="koch_leader_B")
    ap.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    ap.add_argument("--bridge", default="https://127.0.0.1:8444")
    ap.add_argument(
        "--leader-port",
        default=None,
        help="テレオペが止まっているときにバスから直接読む",
    )
    ap.add_argument("--seconds", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cal = json.loads(
        (
            args.config_dir / "calibration" / "koch_leader" / f"{args.leader_id}.json"
        ).read_text(encoding="utf-8")
    )

    def read(label):
        v = None
        try:
            v = read_state(args.bridge, args.seconds)
        except Exception:  # noqa: BLE001 - bridge may be down
            v = None
        src = "bridge /state"
        if v is None:
            if not args.leader_port:
                sys.exit(
                    "テレメトリが来ていません。テレオペを起動するか、--leader-port を付けてバスから読んでください"
                )
            v, src = read_bus(args.leader_port, cal, args.seconds), "bus(読み取り)"
        print(
            f"  [{label}] 読み取り元={src}  "
            + "  ".join(f"{j.split('_')[-1]}={v[j]:.1f}" for j in TWIN_JOINTS)
        )
        return v

    input(
        "\n[R1] 両方の腕の節を一直線に真上へ(上腕・前腕・手首が垂直に一列)。土台は正面、グリッパは水平。\n     その姿勢で保持したまま Enter → "
    )
    v1 = read("R1")
    input(
        "\n[R2] 指先を土台の正面の机に付ける(いつもの下限の姿勢)。土台は正面のまま。\n     その姿勢で保持したまま Enter → "
    )
    v2 = read("R2")

    cands = fit(cal, v1, v2)
    print("\n符号の候補(R2 で指先が机に最も近く、前方にあるものを採用):")
    for score, signs, (x, y, z), _ in cands[:4]:
        print(f"  lift/elbow/wrist = {signs}  指先 x={x:+.3f} y={y:+.3f} z={z:+.3f} m")
    best = cands[0]
    cfg = best[3]
    x, y, z = best[2]
    r = math.hypot(x, z)
    objects = {
        "ball": {"spot": [round(x, 3), 0, round(z, 3)]},
        "sponge": {
            "spot": [
                round(r * math.sin(math.radians(-35)), 3),
                0,
                round(r * math.cos(math.radians(-35)), 3),
            ]
        },
        "block": {
            "spot": [
                round(r * math.sin(math.radians(35)), 3),
                0,
                round(r * math.cos(math.radians(35)), 3),
            ]
        },
    }
    print("\n=== 分身の設定(採用) ===")
    for j in TWIN_JOINTS:
        print(
            f"  {j:<14} span={cfg[j]['span_deg']:>6}  offset={cfg[j]['offset_deg']:>7}  sign={cfg[j]['sign']:+d}"
        )
    print(
        f"  R2 の指先: x={x:+.3f} y={y:+.3f} z={z:+.3f} m(y≈0 なら机の高さ)。ボールをそこに、スポンジ/ブロックは同じ半径 {r:.3f} m の左右 35° に置く"
    )
    if abs(y) > 0.05:
        print(
            "  ⚠ R2 で指先が机から 5 cm 以上離れています。R1/R2 の姿勢を見直すか、編集モードで offset を微調整してください"
        )
    if args.dry_run:
        print("\n--dry-run: 書き込みなし")
        return
    payload = {"joints": cfg, "objects": objects}
    try:
        http_json(f"{args.bridge}/config", payload)
        print(
            f"\n保存: ブリッジ経由で {args.config_dir / TWIN_CONFIG_NAME} に書き、テレオペにも関節写像を送りました"
        )
    except Exception as e:  # noqa: BLE001 - fall back to the file
        path = args.config_dir / TWIN_CONFIG_NAME
        cur = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        cur.setdefault("joints", {}).update(cfg)
        for k, o in objects.items():
            cur.setdefault("objects", {}).setdefault(k, {}).update(o)
        path.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"\nブリッジに届かなかったので({e})ファイルに直接保存: {path}(ブリッジの再起動が必要)"
        )
    print("ページ(Mac の観客ページ・Quest)を再読み込みすると新しい分身になります")


if __name__ == "__main__":
    main()
